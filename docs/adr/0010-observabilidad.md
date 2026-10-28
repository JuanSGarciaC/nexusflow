# ADR-0010: Métricas de negocio desde PostgreSQL y HTTP con Prometheus

- **Estado:** Aceptada e implementada (Fase 7)
- **Fecha:** 2026-10-28

## Contexto

El panel necesita tasa de éxito, tiempos (p50/p95), reintentos y errores por flujo. La
operación necesita latencia y errores HTTP para alertas. Hay varios procesos (`api`,
`worker`, `beat`) y pueden escalarse por separado.

## Decisión

1. **Métricas de negocio calculadas en PostgreSQL al consultarlas.** Las tablas de
   ejecuciones, pasos e intentos ya guardan estados y tiempos. Un endpoint
   (`/api/v1/metrics/summary`) agrega con `COUNT … FILTER`, `percentile_cont` y
   `date_trunc(…, 'UTC')`, y devuelve un resumen de tamaño fijo.
2. **HTTP con `prometheus-client`**, un `CollectorRegistry` por aplicación, etiquetado
   por **plantilla de ruta** (`/api/v1/runs/{run_id}`), nunca por la ruta real.
3. **`/metrics` cerrado por defecto.** Solo existe si se define `NEXUSFLOW_METRICS_TOKEN`
   y exige `Authorization: Bearer`, comparado en tiempo constante. Los indicadores de
   negocio que expone (ejecuciones por estado, entregas en dead-letter) se leen de
   PostgreSQL en cada lectura.
4. **Auditoría con paginación por cursor** (`before_id`) en lugar de `OFFSET`: estable
   aunque lleguen eventos nuevos y sin coste creciente con la profundidad.

## Alternativas descartadas

- **Contadores en memoria en el worker** (o `prometheus_client` en modo multiproceso):
  cada proceso tiene su propia cuenta, y sumarlas exige un directorio compartido o un
  *pushgateway*. La base de datos ya tiene la cifra exacta.
- **Tablas de agregados precalculados o vistas materializadas:** innecesarias con este
  volumen. Con millones de ejecuciones al día sería el siguiente paso, junto con
  particionar `workflow_runs` por mes.
- **Prometheus y Grafana en el `docker-compose` por defecto:** añaden servicios que no
  todo el mundo necesita. El endpoint es compatible con cualquier Prometheus.
- **`/metrics` público:** revela rutas, volumen de uso y tasas de error.

## Consecuencias

- Las consultas usan índices nuevos: `workflow_runs(created_at)`,
  `workflow_runs(finished_at)`, `step_attempts(started_at)` y
  `audit_log(resource_type, resource_id)`.
- La ventana máxima es de 30 días, para acotar el coste de una consulta.
- Las métricas HTTP son por proceso: con varias réplicas, Prometheus lee cada una y las
  suma (comportamiento estándar).
