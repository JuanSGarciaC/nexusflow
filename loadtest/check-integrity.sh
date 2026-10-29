#!/usr/bin/env bash
# Comprobación de integridad tras la prueba de carga: lo que k6 no puede ver desde fuera.
# k6 comprueba que la API responde y que las ejecuciones terminan; aquí se comprueba en
# PostgreSQL que bajo concurrencia no se perdió ni se duplicó trabajo (ADR-0007: el reclamo
# en PostgreSQL impide ejecutar dos veces un paso aunque la tarea de Celery se entregue dos).
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
check "ejecuciones sin terminar" "$open" 0
check "ejecuciones que no acabaron en 'succeeded'" \
  "$(q "SELECT count(*) FROM workflow_runs wr JOIN workflows w ON w.id = wr.workflow_id
        WHERE w.name LIKE 'carga-%' AND wr.status <> 'succeeded'")" 0
check "pasos con más de un intento (ejecución doble)" \
  "$(q "SELECT count(*) FROM (SELECT step_run_id FROM step_attempts
        GROUP BY 1 HAVING count(*) > 1) t")" 0
check "pasos duplicados dentro de una misma ejecución" \
  "$(q "SELECT count(*) FROM (SELECT run_id, step_id FROM step_runs
        GROUP BY 1, 2 HAVING count(*) > 1) t")" 0
echo "Ejecuciones de la prueba de carga: $(q "SELECT count(*) FROM workflow_runs wr JOIN workflows w
  ON w.id = wr.workflow_id WHERE w.name LIKE 'carga-%'")"
exit "$failed"
