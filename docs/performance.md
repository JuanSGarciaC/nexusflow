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

## Carga concurrente (k6)

Prueba del sistema completo (API → Redis → worker de Celery → PostgreSQL) creando
ejecuciones a ritmo constante. Guion y razonamiento en [loadtest/README.md](../loadtest/README.md).

**Entorno de la medición (local, no producción):** Docker Desktop en Windows 11, una sola
réplica de API (un proceso uvicorn), worker de Celery con `--concurrency=2`, PostgreSQL 16 y
Redis 7 en contenedores, flujo de un único paso `transform`. Cada fila: 30 s de carga.

| Ritmo objetivo | Ejecuciones | Creación p95 | Finalización p95 | Correctas |
|---|---|---|---|---|
| 20 / s | 600 | 23 ms | 61 ms | 100 % |
| 40 / s | 1 201 | 32 ms | 63 ms | 100 % |
| 80 / s | 1 717 (53 / s reales, 684 iteraciones descartadas) | 5,6 s | 3,2 s | 100 % |

Lectura honesta de los resultados:

- **Capacidad sostenida en esa máquina: entre 40 y ~53 ejecuciones/s.** A 80/s el sistema
  se satura y lo hace por la **API** (la creación pasa de 32 ms a 5,6 s), no por el worker.
  Un solo proceso de API es el cuello de botella más probable; escalarlo (más réplicas o
  `--workers`) es el siguiente paso. **No lo he probado**, es una hipótesis razonable.
- **Saturado no es roto:** incluso a 80/s no se perdió ninguna ejecución ni hubo errores.
  Tras las 4 pruebas hay 3 718 ejecuciones en la base, exactamente las que k6 creó, todas
  `succeeded`, sin ningún paso con más de un intento
  ([`check-integrity.sh`](../loadtest/check-integrity.sh)). Es la evidencia empírica del
  reclamo en PostgreSQL de [ADR-0007](adr/0007-motor-de-ejecucion.md). Matiz: la unicidad
  `(run_id, step_id)` ya la impone una restricción de la base, así que esa comprobación
  concreta es débil; la valiosa es la de intentos y la de ejecuciones terminadas.
- **Lo que NO mide:** pasos HTTP reales (la latencia de un tercero domina), ejecuciones
  largas, reintentos bajo carga, varios workers ni una base con mucho histórico. Tampoco son
  números de producción: otra máquina dará otras cifras. Úsalas para comparar versiones
  entre sí, no como promesa.
- **Reintentos y límites:** ver más abajo, en «Pasos HTTP con reintentos» y «Límites de uso
  medidos».
- **Rate limiting:** la prueba sube el límite de creación (120/min por usuario) porque, con
  el valor por defecto, lo correcto es que cortara la carga con 429. Ese comportamiento lo
  cubren las pruebas de integración, no k6.

### Pasos HTTP con reintentos

`SCENARIO=retries`: cada ejecución hace un cobro HTTP al servicio simulado que falla 2 veces
con 503 y acierta a la tercera (backoff de 1 s y 2 s). Mismo entorno que arriba.

| Ritmo | Ejecuciones | Finalización p95 | Correctas | Intentos por paso | Cobros |
|---|---|---|---|---|---|
| 5 / s, 20 s | 98 | 3,5 s | 100 % | **exactamente 3** en las 98 | **98** (uno por ejecución) |
| 5 / s, 20 s (repetición) | 101 | 3,9 s | 100 % | **exactamente 3** | 101 |

- La integridad bajo reintentos concurrentes se cumple: ni un intento de más (ejecución
  doble) ni un cobro duplicado o perdido. El suelo teórico de finalización es ~3 s por los
  dos backoffs; se mide 2,8 s de media.
- **Latencia de creación: no la tengo bien explicada.** En las dos primeras tandas (justo
  tras reiniciar la API y con el worker aún acabando trabajo) el p95 fue de 1,2 s y 0,86 s;
  con todo en caliente, 48 ms a 2/s y 103 ms a 5/s. No he aislado la causa (arranque en frío,
  cola atrasada o contención de CPU en Docker Desktop). Por eso este escenario tiene su
  propio umbral, más holgado (1,5 s), y no debe leerse como «la API tarda 1 s».
- Un sondeo agresivo (cada 50 ms) con ejecuciones de varios segundos genera cientos de
  lecturas/s y falsea la latencia; este escenario sondea cada 250 ms.
- No cubre: ejecuciones largas, `needs_review`, caídas de worker ni varios workers.

### Límites de uso medidos

[`rate-limits.js`](../loadtest/rate-limits.js), con los límites **por defecto**, dos
usuarios en paralelo a 3 peticiones/s durante 75 s (225 intentos cada uno):

| Límite | Aceptadas | Rechazadas (429) | `Retry-After` válido | Rechazo p95 | Otros estados |
|---|---|---|---|---|---|
| Crear ejecuciones (120/min) | 164–165 | 61 | 100 % | 33–34 ms | 0 |
| Escrituras `PATCH` (120/min) | 164 (+2 de preparación) | 62 | 100 % | 33–34 ms | 0 |

- **Coincide con la teoría de la ventana fija:** 120 en el primer minuto + ~45 en los últimos
  15 s, ya en la ventana siguiente. Demuestra a la vez que **el límite se aplica**, que
  **la ventana se reabre** y que el máximo (2× en el borde) no se supera.
- **Rechazar es barato:** p95 de ~34 ms, comparable a una petición normal; un cliente que
  ignora el 429 no degrada a la API ni provoca ningún 5xx.
- **Esto valida el MECANISMO, no los valores.** 120/min por usuario no sale de datos de uso
  reales: es un tope razonable para una persona y para integraciones modestas. Si un cliente
  máquina a máquina legítimo necesita más, se sube por entorno
  (`NEXUSFLOW_WRITE_RATE_LIMIT_PER_USER`). El límite de `ping`/`redeliver` (10/min) lo cubren
  las pruebas de integración, no k6, para no provocar peticiones salientes reales.
- Lección del propio ejercicio: una primera ejecución dio **cero 429 en las escrituras**
  porque la imagen local era anterior a la fusión del límite. La prueba lo detectó (en CI no
  puede ocurrir: el runner construye las imágenes desde cero).

**En CI** corre una versión corta (20 ejecuciones/s durante 20 s) con umbrales muy holgados
(creación p95 < 500 ms, finalización p95 < 3 s, 100 % correctas) más la comprobación de
integridad. Detecta regresiones graves, no una degradación del 20 %: un runner compartido es
demasiado ruidoso para umbrales ajustados.

## Cobertura de pruebas

El umbral de **85 %** del backend (`fail_under = 85` en `pyproject.toml`) lo aplica
`pytest --cov` en el CI: si baja, el job falla. Última medición completa en local, en la rama del PR de rate limiting (366
pruebas, unitarias e integración contra PostgreSQL y Redis reales): **87,98 %**. El frontend
tiene un **suelo** en `vite.config.ts` (63 % sentencias, 54 % ramas, 50 % funciones, 64 %
líneas) que el CI aplica con `pnpm test:coverage`. Lo medido hoy es 65,7 % / 56,7 % / 52 % /
66,7 %: **es una cobertura modesta**, y las pantallas de auditoría y credenciales están al 0 %
en Vitest (las ejercita Playwright E2E, que no cuenta en esta cifra). El suelo solo impide
retroceder; el objetivo es subirlo añadiendo pruebas de esas pantallas.

## Reproducir

```bash
createdb nexusflow_perf
NEXUSFLOW_DATABASE_URL=postgresql+asyncpg://…/nexusflow_perf uv run alembic upgrade head
psql -d nexusflow_perf -f docs/perf/seed.sql      # genera el volumen
psql -d nexusflow_perf -f docs/perf/explain.sql   # planes y tiempos
```
