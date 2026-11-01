# Plan de pruebas por riesgos de fiabilidad

Qué puede salir mal en el motor (ejecutar dos veces, perder trabajo, reintentar lo que no
se debe), qué lo impide y qué prueba falla si deja de impedirlo. Los riesgos de
**seguridad** (autenticación, RBAC, credenciales, SSRF, límites de uso) ya tienen su matriz
en [security-review.md](security-review.md), organizada por OWASP API Top 10; aquí no se
repiten.

Leyenda de impacto: **Alto** = efecto externo duplicado o perdido (un cobro doble, un pedido
sin procesar). **Medio** = estado incorrecto visible, pero recuperable sin tocar a terceros.

## Matriz

| # | Riesgo | Impacto | Control | Prueba que falla si se rompe |
|---|---|---|---|---|
| R1 | Un webhook reenviado crea una segunda ejecución | Alto | Clave de idempotencia derivada del id de evento (o del hash de timestamp + cuerpo firmados) | `test_webhooks.py::test_duplicate_events_do_not_create_new_runs`; de punta a punta en el [caso de estudio](caso-de-estudio-webhook.md) (rama `mejoras-portafolio`) |
| R2 | Dos peticiones simultáneas con la misma clave crean dos ejecuciones | Alto | Restricción única en PostgreSQL; el perdedor recibe la existente | `test_engine.py::test_concurrent_run_creation_with_same_key`, `test_runs_api.py::test_idempotent_creation_returns_same_run` |
| R3 | La misma clave con otra entrada se acepta en silencio | Medio | `409` si la entrada difiere | `test_engine.py::test_same_key_with_different_input_conflicts` |
| R4 | Dos workers reciben el mismo mensaje (entrega *at-least-once*) y llaman dos veces al tercero | Alto | Reclamo atómico del paso en PostgreSQL | `test_engine.py::test_duplicate_message_executes_once`; bajo carga, `loadtest/check-integrity.sh` (un cobro por ejecución) |
| R5 | Un 503 o un 429 hace fallar la ejecución | Medio | Clasificación «rechazado sin procesar» → reintento con backoff y jitter, respetando `Retry-After` | `test_engine.py::test_transient_errors_are_retried_with_history`, `::test_retry_after_header_is_respected`, `test_engine_units.py::test_classify_status`, `::test_backoff_grows_exponentially_with_bounded_jitter` |
| R6 | Un POST sin respuesta (timeout) se reintenta y cobra dos veces | Alto | Resultado incierto + paso no idempotente → `needs_review`, sin reintento automático | `test_engine.py::test_unknown_outcome_on_non_idempotent_stops_for_review`, `test_engine_units.py::test_failure_decision_table` |
| R7 | Un reintento de un PUT incierto llega como una petición nueva | Alto | La misma `Idempotency-Key` en todos los intentos y en el reintento manual | `test_engine.py::test_unknown_outcome_on_idempotent_retries_with_same_key`, `::test_review_retry_reuses_idempotency_key` |
| R8 | Un worker muere a mitad de un paso y la ejecución queda colgada | Medio | Lease por paso; el barrido lo recupera (GET → reintento, POST → revisión) | `test_engine.py::test_sweep_recovers_steps_of_dead_workers` |
| R9 | **El worker que se dio por muerto despierta y su resultado pisa el del reintento** | Alto | Al registrar, el paso debe seguir `running` **y** en el mismo número de intento; si no, el resultado se descarta (`stale`) | `test_engine.py::test_late_result_of_worker_whose_lease_expired_is_discarded` (**nueva**, éxito y fallo tardíos) |
| R10 | Un mensaje se pierde al encolar (Redis caído) y la ejecución no avanza | Medio | PostgreSQL es la fuente de verdad; el barrido reencola una sola vez | `test_engine.py::test_sweep_redispatches_lost_messages_once` |
| R11 | El resultado de una ejecución cancelada se aplica igualmente | Medio | Misma comprobación `stale` que R9 | `test_engine.py::test_result_of_cancelled_run_is_discarded` |
| R12 | Una notificación saliente se envía aunque la transacción se deshaga | Medio | Outbox en la misma transacción | `test_webhooks.py::test_outbox_rolls_back_with_the_transaction` |
| R13 | Una entrega saliente fallida se pierde o cambia de `event_id` | Medio | Reintentos con el mismo `event_id` → dead-letter → reenvío manual; barrido de entregas perdidas | `test_webhooks.py::test_retries_keep_event_id_then_dead_letter_and_redeliver`, `::test_sweep_recovers_lost_deliveries` |
| R14 | Una caída entre crear la ejecución programada y avanzar la programación la duplica | Alto | Clave de idempotencia por disparo | `test_schedules.py::test_crash_between_creating_the_run_and_advancing_does_not_duplicate` |
| R15 | Varios barridos simultáneos disparan la misma programación | Alto | Bloqueo de fila en el barrido | `test_schedules.py::test_concurrent_sweeps_fire_once` |
| R16 | Cambio de hora: un disparo se salta o se repite | Medio | Cálculo en la zona horaria de la programación | `test_cron.py::test_daylight_saving_gap_moves_the_fire_to_the_next_valid_time`, `::test_daylight_saving_overlap_fires_once` |
| R17 | Tras una parada larga se reproducen todos los disparos perdidos | Medio | Fuera del periodo de gracia se omiten | `test_schedules.py::test_missed_fire_beyond_the_grace_period_is_skipped_not_replayed` |
| R18 | Dos editores guardan el mismo borrador y uno pierde cambios sin saberlo | Medio | Concurrencia optimista con `expected_revision` → `409` | `test_workflows_api.py::test_concurrent_draft_saves_with_same_revision` |
| R19 | Se modifica una versión ya publicada mientras se ejecuta | Alto | Inmutabilidad comprobada en la base de datos | `test_workflows_api.py::test_published_version_is_immutable_in_database` |

## Cómo se sabe que las pruebas sirven

Una prueba que no falla cuando el código se rompe no protege nada. Las pruebas clave se
validaron con **mutaciones**: se rompe a propósito el control, se comprueba que la prueba
falla y se revierte el cambio.

| Prueba | Mutación | Resultado |
|---|---|---|
| R9 (nueva) | `_is_stale` devuelve siempre `False` | Falla: el resultado tardío se acepta |
| R9 (nueva) | `_is_stale` solo compara el estado, no el número de intento | Falla: con el reintento en marcha, el resultado del intento 1 se acepta |
| `test_authorization_matrix.py` | Quitar el permiso de un endpoint | Falla (ver [security-review.md](security-review.md)) |

La segunda mutación de R9 es la razón de que la prueba exista: en la versión anterior del
código, la única prueba de la rama `stale` era la de cancelación (R11), y esa pasa aunque se
quite la comparación del número de intento.

## Huecos conocidos

| Hueco | Por qué no está cubierto | Cómo se cubriría |
|---|---|---|
| Varios procesos de worker de Celery a la vez | La carga en CI usa un solo worker con `--concurrency=2`; R4 se prueba con dos llamadas concurrentes al mismo motor | Repetir `concurrent-runs.js` con 2–3 workers y `check-integrity.sh` |
| Matar un worker durante una prueba de carga | R8 simula la muerte caducando el lease, no matando el proceso | Escenario de k6 con `docker kill` del worker a mitad de la carga |
| PostgreSQL caído durante una ejecución | No hay prueba que corte la base de datos en mitad de un paso; la recuperación depende del lease (R8) | Prueba con un proxy TCP que corte la conexión |
| Relojes desfasados entre workers | El lease lo calcula el worker con su propio reloj (`utcnow()` en Python) y lo compara el barrido con el suyo. Un desfase mayor que el margen de gracia del lease haría que un paso vivo se diera por muerto; R9 impide que eso duplique el resultado, pero no la llamada externa de un GET reintentado | Calcular el lease con `now()` de PostgreSQL, o exigir NTP en el despliegue y documentarlo |

## Cómo ejecutarlo

```powershell
cd backend
uv run pytest tests/integration/test_engine.py tests/integration/test_schedules.py tests/integration/test_webhooks.py -v
```

Requiere PostgreSQL y Redis de prueba (ver «Pruebas y calidad» en el [README](../README.md)).
