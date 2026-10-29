# Decisiones y trade-offs

Para defender el proyecto en una entrevista no basta con saber *qué* se hizo, sino *qué se
descartó, qué costó y cuándo se cambiaría*. Cada fila remite al ADR con el razonamiento
completo; las cifras indicadas son mediciones reales (con su entorno en
[performance.md](performance.md)), no estimaciones.

## Las diez decisiones que más se preguntan

| # | Decisión | Se descartó | Coste que se acepta | Cuándo la revertiría |
|---|---|---|---|---|
| 1 | **Monolito modular** con tres procesos (`api`, `worker`, `beat`) en una sola imagen — [ADR-0001](adr/0001-monolito-modular.md) | Microservicios; Kubernetes | Hay que vigilar que los módulos no se acoplen (solo se importa la capa `services`) | Si un módulo necesita aislamiento de red o escala muy distinta (p. ej. `integrations`) |
| 2 | **PostgreSQL es la fuente de verdad**; Redis/Celery solo transportan «revisa el paso X» — [ADR-0002](adr/0002-postgres-fuente-de-verdad.md) | Guardar estado en la cola o en Redis | Cada paso son varias transacciones cortas; más escrituras que una cola pura | Con volumen enorme y sin llamadas externas, una cola en PostgreSQL (`SKIP LOCKED`) bastaría y sobraría Redis |
| 3 | **Entrega «al menos una vez» + reclamo atómico** (`SELECT … FOR UPDATE`, lease, barrido cada 15 s) — [ADR-0007](adr/0007-motor-de-ejecucion.md) | Intentar «exactamente una vez» (no existe en un sistema distribuido) | Complejidad del motor: reclamar / ejecutar / registrar, intentos `stale` | Nunca hacia «exactamente una»; sí a una cola más rica si el barrido se queda corto |
| 4 | **Pasos no idempotentes no se reintentan a ciegas**: resultado desconocido → `needs_review` (revisión humana) — [ADR-0007](adr/0007-motor-de-ejecucion.md) | Reintentar siempre (riesgo de cobrar dos veces) o fallar siempre | Una persona debe intervenir; hay latencia humana | Si el destino soporta `Idempotency-Key` de forma fiable, ese paso podría declararse `idempotent` |
| 5 | **JWT de 15 min + refresh opaco rotativo** en cookie `HttpOnly`; el rol se lee de la BD en cada petición — [ADR-0005](adr/0005-autenticacion.md) | Sesiones en Redis; JWT como refresh; rol dentro del JWT; token en `localStorage` | Una consulta por petición; rotación estricta sin periodo de gracia (dos canjes simultáneos del mismo refresh: uno cuenta como reutilización y revoca la familia) | Con mucho tráfico, cachear el rol unos segundos |
| 6 | **DSL JSON cerrado, sin código ejecutable**; versiones publicadas inmutables (también con un trigger en PostgreSQL) — [ADR-0006](adr/0006-dsl-de-flujos.md) | Permitir Python/JS/Jinja con expresiones | Menos expresividad; sin editor visual | Un nuevo tipo de paso se añade al DSL cerrado, nunca «un campo de código» |
| 7 | **Credenciales cifradas (Fernet/`MultiFernet`), solo escritura y atadas a hosts** — [ADR-0008](adr/0008-credenciales.md) | Guardarlas en claro; mostrar el valor al editor | Si se pierde la clave, no hay recuperación (por eso se guarda aparte de las copias) | Con más requisitos de cumplimiento, un KMS/Vault en vez de una clave en `.env` |
| 8 | **Webhooks firmados (HMAC + timestamp) y outbox transaccional** — [ADR-0009](adr/0009-webhooks.md) | Notificar tras el commit «sin garantías» | Latencia extra de la entrega por el proceso intermedio | — |
| 9 | **Métricas de negocio calculadas desde PostgreSQL** al leer `/metrics` — [ADR-0010](adr/0010-observabilidad.md) | Contadores en memoria por proceso | Cada lectura de `/metrics` consulta la BD; el estado «nuevas fallidas» se aproxima con `delta()` | Con millones de ejecuciones/día: agregados precalculados |
| 10 | **Programaciones cron en PostgreSQL**, dentro del barrido de `beat` — [ADR-0012](adr/0012-programaciones-cron.md) | APScheduler / entradas dinámicas de `beat` | Resolución de ~15 s; los disparos con más de 5 min de retraso **se omiten** a propósito | Si hiciera falta precisión de segundos |

## Preguntas típicas y cómo responderlas

**«¿Cómo evitas ejecutar dos veces un paso?»**
No lo evito en la cola (Celery puede entregar dos veces): lo evito en la base. La tarea
reclama el paso con un bloqueo de fila y una transición de estado válida; un mensaje duplicado
lo encuentra ya `running` y no hace nada. Lo comprobé bajo carga: tras 3 718 ejecuciones
concurrentes, ningún paso tuvo más de un intento
([performance.md](performance.md#carga-concurrente-k6)). Matiz honesto: esa prueba usa pasos
`transform` sin fallos, así que **no ejercita reintentos ni caídas de worker** (eso lo cubren
las pruebas de integración del motor).

**«¿Y si el worker muere a mitad de una llamada HTTP?»**
El lease del paso vence, el barrido lo trata como fallo `unknown` (`worker_lost`) y decide
según el `side_effect`: reintenta si es `none`/`idempotent`; si es `non_idempotent`, lo deja en
`needs_review` para que una persona compruebe qué pasó. Todos los intentos envían la misma
`Idempotency-Key`.

**«¿Por qué no microservicios?»**
Un dominio, un equipo y cargas distintas que ya se separan con procesos. Los microservicios
añadirían red, contratos versionados y trazas distribuidas sin beneficio. Los límites entre
módulos permiten extraer uno si aparece una razón real.

**«¿Dónde se rompería primero con más carga?»**
En la **API**, no en el worker. En mi máquina (una réplica, un proceso uvicorn, worker con
concurrencia 2) sostuvo entre 40 y ~53 ejecuciones/s; a 80/s la latencia de creación pasó de
32 ms a 5,6 s, y aun así no se perdió ninguna ejecución. Es una hipótesis razonable que
escalar la API sería el primer paso, **pero no la he probado**, y no son cifras de producción.

**«¿Qué pasa si se filtra una clave?»**
Depende de cuál: [key-rotation.md](key-rotation.md). Con la clave Fernet, rotar no basta si
también se filtró la base: hay que cambiar los secretos de los terceros. Con la clave JWT se
pueden forjar access tokens hasta cambiarla, pero los refresh tokens no se ven afectados.

**«¿Cómo sabes que las copias de seguridad sirven?»**
El CI hace una copia y la restaura en una base temporal en cada cambio, y compara tablas,
versión de migración y filas ([deployment.md](deployment.md)). Eso prueba el mecanismo, no tu
disco ni tu proveedor, y el tiempo de recuperación con datos reales no está medido.

**«¿Cómo proteges la API del abuso?»**
Límites por IP en registro/login/refresh, por usuario en escrituras (120/min) y más estricto
(10/min) en acciones que provocan una petición saliente, y por webhook en las entradas. Todos
fallan cerrado si Redis cae. Es ventana fija: permite hasta 2× el límite en el borde de dos
ventanas. Los valores 120 y 10 son una estimación, no un resultado de carga
([security-review.md](security-review.md)).

**«¿Qué cambiarías con más tiempo?»** → sección siguiente.

## Deuda conocida y qué haría después

Lo que sigue lo dejo por escrito porque conocerlo es parte de defender el diseño.

| Limitación | Impacto | Siguiente paso |
|---|---|---|
| Las alertas **no avisan a nadie**: hay reglas probadas, pero sin Alertmanager | Un incidente solo se ve si alguien mira Grafana | Alertmanager con un canal real |
| Worker y beat no exportan métricas; su caída se detecta de forma indirecta | Falsos negativos si hay un patrón de cola atípico | Exporter de Celery |
| Clave JWT única, sin `kid` | La rotación invalida los access tokens en vuelo (≤ 15 min) | `kid` y lista de claves de verificación |
| Claves en `.env` del servidor | Sin auditoría de acceso a la clave | Vault/KMS |
| La API se sirve con un solo proceso | Es el cuello de botella medido | Más réplicas o `--workers`, y repetir la medición |
| RPO = intervalo entre copias (24 h con copia diaria); sin PITR | Se puede perder un día | WAL archiving (pgBackRest/wal-g) o base gestionada |
| La prueba de carga solo usa pasos locales | No mide terceros, reintentos ni histórico grande | Escenario con `mock-partner` y fallos inyectados |
| El frontend no tiene umbral de cobertura | La cobertura de la interfaz puede bajar sin que el CI avise | Umbral en Vitest |
| Los 429 de las escrituras no están en el contrato OpenAPI | Los clientes generados no los tipan | Declararlos y regenerar los tipos |
| Sin captura/GIF de la demo | Menos inmediato para quien solo lea el README | Grabarlo (ver [README](../README.md#capturas)) |

## Lo que NO afirmo

- **No está en producción.** Todo se ha verificado en local y en CI, sobre datos de
  demostración.
- Las cifras de rendimiento son de **una máquina de desarrollo**; sirven para comparar
  versiones entre sí, no como promesa.
- «Probado» significa que hay una prueba que falla si se rompe; en cada documento se indica
  qué cubre y qué no.
