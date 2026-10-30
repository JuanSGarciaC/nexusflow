#!/usr/bin/env bash
# Comprobación de integridad tras la prueba de carga: lo que k6 no puede ver desde fuera.
# k6 comprueba que la API responde y que las ejecuciones terminan; aquí se comprueba en
# PostgreSQL (y en el servicio externo simulado) que bajo concurrencia no se perdió ni se
# duplicó trabajo (ADR-0007: el reclamo en PostgreSQL impide ejecutar dos veces un paso
# aunque la tarea de Celery se entregue dos veces).
#
# Dos familias de flujos creados por loadtest/concurrent-runs.js:
# - carga-<n>         (SCENARIO=runs): un paso `transform`; debe tener EXACTAMENTE 1 intento.
# - carga-retry-<n>   (SCENARIO=retries): un cobro que falla 2 veces y acierta a la 3.ª;
#                     debe tener EXACTAMENTE 3 intentos y dejar EXACTAMENTE 1 cobro.
#
# Uso: loadtest/check-integrity.sh   (variables como en scripts/backup.sh: COMPOSE_FILES...)
set -euo pipefail

PG_USER="${POSTGRES_USER:-nexusflow}"
PG_DB="${POSTGRES_DB:-nexusflow}"
# shellcheck disable=SC2206
COMPOSE=(docker compose ${COMPOSE_FILES:-})
q() { "${COMPOSE[@]}" exec -T postgres psql -U "$PG_USER" -d "$PG_DB" -At -c "$1" </dev/null; }

# Espera (hasta 60 s) a que no queden ejecuciones sin terminar.
for _ in $(seq 1 30); do
  open="$(q "SELECT count(*) FROM workflow_runs WHERE status IN ('pending','running')")"
  [ "$open" = "0" ] && break
  sleep 2
done
failed=0
check() { # descripción, valor, esperado
  if [ "$2" = "$3" ]; then echo "  ok   $1 = $2"; else echo "  FALLA $1 = $2 (esperado $3)" >&2; failed=1; fi
}

# Solo los flujos de la carga: los de demostración reintentan a propósito (varios intentos).
PLAIN="w.name LIKE 'carga-%' AND w.name NOT LIKE 'carga-retry-%'"
RETRY="w.name LIKE 'carga-retry-%'"
FROM="FROM step_runs s JOIN workflow_runs wr ON wr.id = s.run_id JOIN workflows w ON w.id = wr.workflow_id"

check "ejecuciones sin terminar (todas)" "$open" 0
check "ejecuciones de la carga que no acabaron en 'succeeded'" \
  "$(q "SELECT count(*) FROM workflow_runs wr JOIN workflows w ON w.id = wr.workflow_id
        WHERE w.name LIKE 'carga-%' AND wr.status <> 'succeeded'")" 0
check "pasos duplicados dentro de una misma ejecución" \
  "$(q "SELECT count(*) FROM (SELECT s.run_id, s.step_id $FROM
        WHERE w.name LIKE 'carga-%' GROUP BY 1, 2 HAVING count(*) > 1) t")" 0

plain_runs="$(q "SELECT count(*) FROM workflow_runs wr JOIN workflows w ON w.id = wr.workflow_id WHERE $PLAIN")"
retry_runs="$(q "SELECT count(*) FROM workflow_runs wr JOIN workflows w ON w.id = wr.workflow_id WHERE $RETRY")"

if [ "$plain_runs" -gt 0 ]; then
  check "pasos sin fallos con un número de intentos distinto de 1 (ejecución doble)" \
    "$(q "SELECT count(*) FROM (SELECT s.id $FROM
          JOIN step_attempts a ON a.step_run_id = s.id
          WHERE $PLAIN GROUP BY s.id HAVING count(*) <> 1) t")" 0
fi
if [ "$retry_runs" -gt 0 ]; then
  check "pasos con reintentos cuyo nº de intentos no es 3 (2 fallos + 1 acierto)" \
    "$(q "SELECT count(*) FROM (SELECT s.id $FROM
          JOIN step_attempts a ON a.step_run_id = s.id
          WHERE $RETRY GROUP BY s.id HAVING count(*) <> 3) t")" 0
  # Efecto externo: un cobro por ejecución. El motor envía Idempotency-Key = "<run_id>:<step_id>"
  # y la pasarela simulada deduplica por esa clave: si hubiera una ejecución doble con otra
  # clave, o una ejecución sin cobro, el recuento no cuadraría.
  run_ids="$(q "SELECT string_agg(wr.id::text, ',') FROM workflow_runs wr JOIN workflows w
                ON w.id = wr.workflow_id WHERE $RETRY")"
  charges="$("${COMPOSE[@]}" exec -T -e RUN_IDS="$run_ids" mock-partner python -c "
import json, os, urllib.request
req = urllib.request.Request('http://127.0.0.1:8080/payments/charges',
                             headers={'X-Api-Key': os.environ['MOCK_PARTNER_PAYMENTS_KEY']})
items = json.load(urllib.request.urlopen(req))['items']
ids = set(os.environ['RUN_IDS'].split(','))
print(sum(1 for c in items if (c.get('idempotency_key') or '').split(':')[0] in ids))
" </dev/null 2>/dev/null || echo "?")"
  check "cobros en la pasarela por cada ejecución con reintentos" "$charges" "$retry_runs"
fi
echo "Ejecuciones de la prueba de carga: $((plain_runs + retry_runs)) ($plain_runs sin red, $retry_runs con reintentos)"
exit "$failed"
