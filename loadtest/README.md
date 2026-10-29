# Prueba de carga (k6)

Mide el sistema completo (API → cola Redis → worker de Celery → PostgreSQL) con
**ejecuciones concurrentes**, que es donde viven los riesgos del motor: reclamo de pasos,
idempotencia y la cola. Resultados y límites conocidos en
[docs/performance.md](../docs/performance.md#carga-concurrente-k6).

## Qué hace

- [`concurrent-runs.js`](concurrent-runs.js): a un ritmo constante (`RATE` ejecuciones
  nuevas por segundo durante `DURATION`), cada iteración crea una ejecución (espera 202) y
  la consulta hasta que termina. Mide la latencia de creación y el tiempo de punta a punta.
  El flujo es solo `transform` (sin red externa): se mide el motor, no a un tercero.
- [`check-integrity.sh`](check-integrity.sh): después, en PostgreSQL, comprueba que no
  quedan ejecuciones sin terminar, que todas acabaron en `succeeded` y que ningún paso se
  ejecutó dos veces. Es lo que k6 no puede ver desde fuera.

**Por qué un ritmo constante** (`constant-arrival-rate`) y no un número fijo de usuarios:
con usuarios fijos, si el sistema se ralentiza el ritmo de peticiones baja y la prueba se
«adapta» al fallo; con ritmo constante, la cola crece y se ve.

## Ejecutarla

```bash
docker compose --profile demo up -d --build
# Datos de demostración (usuarios demo-editor y demo-operator)
docker compose exec -T -e NEXUSFLOW_DEMO_PASSWORD="$PW" api nexusflow seed-demo --runs 0
# Sube el límite de creación de ejecuciones: con el valor por defecto (120/min por usuario)
# el propio límite cortaría la carga con 429, que es lo correcto pero no lo que se mide aquí.
NEXUSFLOW_RUN_CREATE_RATE_LIMIT_PER_USER=100000 docker compose --profile demo up -d api

docker run --rm --network container:"$(docker compose ps -q api)" \
  -v "$PWD/loadtest:/loadtest:ro" -e NEXUSFLOW_DEMO_PASSWORD="$PW" \
  -e RATE=20 -e DURATION=30s grafana/k6:2.3.0 run /loadtest/concurrent-runs.js
loadtest/check-integrity.sh
```

`--network container:…` comparte la red de la API, así que `127.0.0.1:8000` funciona sin
publicar puertos. Variables: `RATE`, `DURATION`, `MAX_CREATE_P95_MS` (500) y
`MAX_COMPLETION_P95_MS` (3000).

## Umbrales (y por qué son holgados)

El CI usa `RATE=20` durante 20 s (≈400 ejecuciones). Los umbrales (creación p95 < 500 ms,
finalización p95 < 3 s, 100 % de ejecuciones correctas) están ~15–50× por encima de lo
medido en local. Es deliberado: un runner compartido es ruidoso y un umbral ajustado a
la décima daría fallos falsos. Detectan una regresión grave, **no** una degradación del 20 %.
Para eso haría falta una máquina dedicada y comparar contra una línea base.
