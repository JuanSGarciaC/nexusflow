# Rendimiento medido

Consultas críticas medidas con `EXPLAIN ANALYZE` sobre PostgreSQL 16 y un volumen sintético
de **50 000 ejecuciones en 30 días** (el 97 % terminadas), **150 000 pasos**, **150 000
intentos** y **100 000 eventos de auditoría**. El script que genera los datos está más abajo,
así que el resultado se puede reproducir.

Tiempos: **mediana de 5 ejecuciones**. Una sola medición puede variar mucho (la primera, en frío, dio 424 ms en la consulta de 30
días), así que no se usa ninguna aislada.

| Consulta | Frecuencia | Plan | Mediana |
|---|---|---|---|
| Barrido: ejecuciones vencidas | cada 15 s | Índice `ix_workflow_runs_status` | 0,6 ms |
| Barrido: pasos con lease vencido | cada 15 s | Índice parcial `ix_step_runs_dispatch` | 1,3 ms |
| Barrido: pasos listos para encolar | cada 15 s | Índice parcial + índice de estado | 0,8 ms |
| Historial global, página 1 | por visita | `ix_workflow_runs_created_at` (scan inverso) | 0,2 ms |
| Historial de un flujo | por visita | `ix_workflow_runs_workflow_created` | 0,1 ms |
| Auditoría por prefijo de acción | por visita | Scan inverso de la clave primaria + `LIMIT` | 0,1 ms |
| Percentiles de duración, 24 h | cada 15 s en el panel | `ix_workflow_runs_finished_at` | 3,0 ms |
| Métricas por tipo de paso, 24 h | cada 15 s en el panel | `ix_step_attempts_started_at` | **49,9 → 7,0 ms** (×7) |
| Métricas por tipo de paso, 30 días | cada 60 s en el panel | Recorrido secuencial (lee el 100 %) | **233,6 → 117,0 ms** (×2) |
| `COUNT(*)` del historial global | por visita | Recorrido secuencial | 4,8 ms |

## Optimización aplicada

La consulta de métricas por tipo de paso hacía `JOIN` con `step_runs` (para obtener
`step_type`) y con `workflow_runs` (aunque no se filtrara por flujo). Cambios:

1. El `JOIN` con `workflow_runs` solo se hace si se filtra por flujo.
2. `step_type` se **copia en `step_attempts`** al crear cada intento. Es seguro porque el
   tipo de un paso nunca cambia, así que la copia no puede quedar desactualizada. La
   migración añade la columna como nullable, la rellena desde `step_runs` y después exige
   `NOT NULL`, de modo que funciona sobre datos existentes (probada con 150 000 filas).
3. En ventanas de 7 y 30 días, el panel refresca cada 60 s en lugar de cada 15 s.

## Cuándo dejarán de ser suficientes

- **`COUNT(*)` del historial** crece de forma lineal (≈5 ms con 50 000 filas; del orden de
  100 ms con un millón). Alternativas: total aproximado (`pg_class.reltuples`) o paginación por
  cursor sin total.
- **Métricas de 30 días** leen todos los intentos de la ventana. Con millones al día:
  agregados por hora en una tabla (o vista materializada) actualizados por `beat`, y
  particionado por mes de `workflow_runs` y `step_attempts` (ver ADR-0010).
- **El barrido** usa índices parciales que solo contienen pasos no terminados: su coste
  depende de los pasos activos, no del historial, y escala bien.

## Reproducir

```bash
createdb nexusflow_perf
NEXUSFLOW_DATABASE_URL=postgresql+asyncpg://…/nexusflow_perf uv run alembic upgrade head
psql -d nexusflow_perf -f docs/perf/seed.sql      # genera el volumen
psql -d nexusflow_perf -f docs/perf/explain.sql   # planes y tiempos
```
