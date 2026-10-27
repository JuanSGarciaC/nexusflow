# NexusFlow

[![CI](https://github.com/JuanSGarciaC/nexusflow/actions/workflows/ci.yml/badge.svg)](https://github.com/JuanSGarciaC/nexusflow/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.13-blue)
![License](https://img.shields.io/badge/license-MIT-green)

Plataforma web de **automatización de procesos empresariales**. Permite definir flujos de
trabajo con pasos, condiciones y acciones, integrarlos con APIs REST externas y ejecutarlos
en segundo plano con reintentos, timeouts e idempotencia.

> **Estado del proyecto:** se está construyendo por fases verificables.
> **Fases 1 a 4 completadas** (fundaciones, autenticación y RBAC, definición de flujos,
> motor de ejecución).
> Esta documentación describe solo lo que ya existe y está probado. Lo pendiente aparece en la
> [hoja de ruta](#hoja-de-ruta).

## Contenido

- [Qué demuestra este proyecto](#qué-demuestra-este-proyecto)
- [Arquitectura](#arquitectura)
- [Inicio rápido con Docker](#inicio-rápido-con-docker)
- [Desarrollo local en Windows (sin Docker)](#desarrollo-local-en-windows-sin-docker)
- [Pruebas y calidad](#pruebas-y-calidad)
- [API](#api)
- [Configuración](#configuración)
- [Seguridad](#seguridad)
- [Hoja de ruta](#hoja-de-ruta)
- [Decisiones técnicas](#decisiones-técnicas)

## Qué demuestra este proyecto

| Área | Qué se ve en el código |
|---|---|
| Backend | FastAPI con application factory, Pydantic v2, SQLAlchemy 2 async, errores RFC 9457 |
| Seguridad | Argon2id, JWT de vida corta, refresh tokens con rotación y detección de robo, RBAC por permisos, límites de uso, auditoría |
| Datos | PostgreSQL como fuente de verdad, migraciones Alembic versionadas y comprobadas en CI |
| Integración | Cliente HTTP anti-SSRF, reintentos con backoff y jitter, idempotencia de efectos, revisión manual de resultados inciertos |
| Sistemas distribuidos | Celery con entrega *at-least-once*, reclamo atómico en PostgreSQL, lease y barrido de recuperación, pruebas con un worker real |
| Operación | Logs JSON con `request_id`, health checks *liveness* / *readiness*, Docker no-root |
| Calidad | Ruff, mypy `strict`, pruebas contra PostgreSQL y Redis reales, CI con escaneo de secretos |

## Arquitectura

Monolito modular: un único código backend que se ejecuta como varios procesos
(`api`, `worker` y `beat`). Ver
[docs/architecture.md](docs/architecture.md) y las [decisiones (ADR)](docs/adr/).

```mermaid
flowchart LR
    client[Cliente / Panel web] -->|HTTP| api[api · FastAPI]
    api --> pg[(PostgreSQL)]
    api -->|encola| redis[(Redis)]
    redis --> worker[worker · Celery]
    worker --> pg
    worker -->|HTTPS anti-SSRF| ext[APIs externas]
    beat[beat · barrido] --> redis
```

Estructura del repositorio:

```
nexusflow/
├─ backend/
│  ├─ src/nexusflow/
│  │  ├─ core/            # configuración, logging, errores, middleware, límites de uso
│  │  ├─ db/              # base declarativa, sesiones, registro de modelos
│  │  ├─ auth/            # usuarios, contraseñas, tokens, permisos, endpoints
│  │  ├─ audit/           # registro de auditoría (solo inserción)
│  │  ├─ workflows/       # DSL, validación del grafo, versiones, endpoints
│  │  ├─ engine/          # ejecuciones, runner, cliente HTTP seguro, Celery
│  │  ├─ observability/   # health checks
│  │  ├─ cli.py           # comando `nexusflow create-admin`
│  │  └─ main.py          # application factory
│  ├─ migrations/         # Alembic (async)
│  ├─ tests/{unit,integration}/
│  ├─ Dockerfile · pyproject.toml · uv.lock
├─ docs/                  # arquitectura y ADR
├─ .github/workflows/ci.yml
├─ docker-compose.yml · .env.example
└─ README.md
```

## Inicio rápido con Docker

Requisitos: Docker Desktop (Windows/macOS) o Docker Engine con Compose v2.

```powershell
# 1. Configuración: copia el ejemplo y genera secretos reales
Copy-Item .env.example .env
python -c "import secrets; print(secrets.token_hex(32))"   # repite para cada secreto
# Edita .env y reemplaza POSTGRES_PASSWORD, REDIS_PASSWORD y NEXUSFLOW_SECRET_KEY

# 2. Construir y levantar (migraciones incluidas)
docker compose up -d --build

# 3. Comprobar
curl http://127.0.0.1:8000/health/ready

# 4. Crear el primer administrador (la contraseña se pide por teclado, sin eco)
docker compose exec api nexusflow create-admin --email admin@example.com
```

En Linux/macOS el primer paso es `cp .env.example .env`.

- Documentación interactiva: http://127.0.0.1:8000/docs
- Logs: `docker compose logs -f api`
- Apagar: `docker compose down` (añade `-v` para borrar también los datos)

El servicio `migrate` aplica `alembic upgrade head` y termina. La API solo arranca si
las migraciones terminan bien. PostgreSQL y Redis no publican puertos hacia fuera de la
red de compose.

## Desarrollo local en Windows (sin Docker)

Requisitos:

- Python 3.13
- [uv](https://docs.astral.sh/uv/getting-started/installation/)
- PostgreSQL 16
- Redis 7, o compatible en Windows (por ejemplo [Memurai](https://www.memurai.com/)),
  o solo los servicios de datos en Docker:
  `docker compose up -d postgres redis` con puertos publicados mediante un
  `docker-compose.override.yml`.

```powershell
# Instalar uv (una vez)
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"

cd backend
uv sync                                   # crea .venv con dependencias exactas de uv.lock
Copy-Item .env.example .env               # edita las URLs y el secreto

# Crear las bases (ajusta usuario y contraseña)
psql -U postgres -c "CREATE ROLE nexusflow LOGIN PASSWORD 'tu-password';"
psql -U postgres -c "CREATE DATABASE nexusflow OWNER nexusflow;"
psql -U postgres -c "CREATE DATABASE nexusflow_test OWNER nexusflow;"

# Alembic necesita la variable en el entorno del proceso
$env:NEXUSFLOW_DATABASE_URL = "postgresql+asyncpg://nexusflow:tu-password@localhost:5432/nexusflow"
uv run alembic upgrade head
uv run nexusflow create-admin --email admin@example.com   # pide la contraseña

uv run uvicorn nexusflow.main:app_factory --factory --reload

# En otras dos terminales: el worker y el barrido periódico
uv run celery -A nexusflow.engine.worker:celery_app worker --loglevel=INFO --pool=solo
uv run celery -A nexusflow.engine.worker:celery_app beat --loglevel=INFO
```

> **Windows:** Celery no admite oficialmente el pool `prefork` en Windows. Para desarrollo
> usa `--pool=solo` (un proceso, una tarea a la vez), como arriba. En Docker o Linux se
> usa `prefork` con varios procesos.


## Pruebas y calidad

```powershell
cd backend
uv run ruff check .          # lint (incluye reglas de seguridad de Bandit)
uv run ruff format --check . # formato
uv run mypy                  # tipos en modo strict
uv run pytest                # unitarias; las de integración se omiten sin servicios
```

Para ejecutar también las pruebas de integración (PostgreSQL y Redis reales, sin mocks):

```powershell
$env:NEXUSFLOW_TEST_DATABASE_URL = "postgresql+asyncpg://nexusflow:tu-password@localhost:5432/nexusflow_test"
$env:NEXUSFLOW_TEST_REDIS_URL   = "redis://localhost:6379/15"
uv run pytest
```

| Suite | Qué verifica |
|---|---|
| `unit/test_config.py` | Fallo al arrancar sin secretos, esquemas de URL, reglas de producción, secretos ocultos en `repr` |
| `unit/test_logging.py` | JSON estructurado, redacción de claves sensibles (también anidadas), logs de librerías en el mismo formato |
| `unit/test_app.py` | `request_id` (generado, propagado y saneado), cabeceras de seguridad, RFC 9457, 500 sin filtrar detalles, CORS, docs desactivables |
| `unit/test_auth_primitives.py` | Argon2id, JWT (expirado, otra clave, otra audiencia/emisor, `alg: none`, tipo incorrecto), matriz de permisos, política de contraseñas, *mass assignment* |
| `unit/test_auth_api_without_services.py` | 401 sin token o con token malformado, login **falla cerrado** (503) si Redis cae, validación de IP del cliente |
| `integration/test_readiness.py` | `/health/ready` con servicios reales y con Redis caído; migraciones *upgrade/downgrade*; modelos y migraciones sincronizados (`alembic check`) |
| `integration/test_auth_flows.py` | Registro, login y `me`; atributos de la cookie; mismo error para email inexistente y contraseña errónea; rotación; **reutilización de refresh token revoca la familia**; logout; RBAC; cambio de rol efectivo al instante; desactivación corta access y refresh; límites de uso con `Retry-After`; auditoría sin contraseñas |
| `integration/test_auth_concurrency.py` | Dos canjes simultáneos del mismo refresh token: exactamente uno gana. Cinco registros simultáneos del mismo email: exactamente uno se crea. |
| `integration/test_cli.py` | `create-admin`: creación auditada, duplicados y contraseñas débiles |
| `unit/test_workflow_dsl.py` | Estructura del DSL, cabeceras peligrosas, efectos secundarios por método, ciclos, pasos inalcanzables, referencias no garantizadas (dominadores), plantillas y condiciones |
| `unit/test_engine_units.py` | Anti-SSRF (IPs privadas, metadatos cloud, IPv4 en IPv6, `localhost`), clasificación de códigos, `Retry-After`, backoff con jitter acotado, máquina de estados, **tabla de decisión ante fallos**, validación de la entrada |
| `integration/test_engine.py` | Contra una API externa real local: camino feliz, reintentos con historial, `Retry-After`, **timeout de un POST → revisión sin reintento**, PUT reintentado con la misma `Idempotency-Key`, **mensaje duplicado → una sola llamada**, SSRF, límite de respuesta, recuperación de workers caídos, mensajes perdidos, plazos, cancelación, esperas, resolución manual, creación idempotente concurrente |
| `integration/test_runs_api.py` | Permisos, `202`/`200` por idempotencia, `409` con otra entrada, validación de la entrada, cancelación |
| `integration/test_celery_worker.py` | **API → Redis → worker de Celery real → PostgreSQL → API externa**, con paso `delay` |
| `integration/test_workflows_api.py` | Ciclo de vida borrador → publicación → nueva versión, concurrencia optimista (4 guardados simultáneos: 1 gana), **inmutabilidad en la base de datos**, archivo, búsqueda literal, límite de tamaño (413) |

En CI se define `NEXUSFLOW_REQUIRE_INTEGRATION=1`. Si faltan los servicios, las pruebas
de integración **fallan** en lugar de omitirse en silencio.

## API

| Método | Ruta | Descripción |
|---|---|---|
| GET | `/health/live` | El proceso responde. No consulta dependencias. |
| GET | `/health/ready` | PostgreSQL y Redis accesibles. Devuelve `503` si alguno falla. |
| GET | `/docs`, `/openapi.json` | OpenAPI 3.1. Se puede desactivar con `NEXUSFLOW_DOCS_ENABLED=false`. |
| POST | `/api/v1/auth/register` | Crea una cuenta `viewer`. Se puede desactivar con `NEXUSFLOW_REGISTRATION_ENABLED=false`. |
| POST | `/api/v1/auth/login` | Devuelve el access token (JWT, 15 min) y fija el refresh token en una cookie `httpOnly` |
| POST | `/api/v1/auth/refresh` | Canjea la cookie por un access token nuevo y rota la cookie |
| POST | `/api/v1/auth/logout` | Revoca la sesión (la familia de refresh tokens). Idempotente. |
| GET | `/api/v1/users/me` | Usuario autenticado y sus permisos efectivos |
| GET | `/api/v1/users` | Lista paginada (`limit` ≤ 100). Requiere `users:read`. |
| PATCH | `/api/v1/users/{id}` | Cambia rol o estado. Requiere `users:manage`; no se permite sobre uno mismo. |

### Roles y permisos

Los endpoints exigen **permisos**, no roles. La matriz completa está en
[`auth/permissions.py`](backend/src/nexusflow/auth/permissions.py):

| Permiso | viewer | operator | editor | admin |
|---|:-:|:-:|:-:|:-:|
| `workflows:read`, `runs:read`, `metrics:read` | ✓ | ✓ | ✓ | ✓ |
| `workflows:execute` | | ✓ | ✓ | ✓ |
| `workflows:write`, `credentials:manage`, `webhooks:manage` | | | ✓ | ✓ |
| `users:read`, `users:manage`, `audit:read` | | | | ✓ |

El primer administrador solo se crea con el comando `nexusflow create-admin`: ningún
endpoint permite a un usuario anónimo obtener privilegios.

### Flujos de trabajo

| Método | Ruta | Permiso | Descripción |
|---|---|---|---|
| POST | `/api/v1/workflows` | `workflows:write` | Crea el flujo con su borrador v1 |
| GET | `/api/v1/workflows` | `workflows:read` | Lista con `status`, `q` (búsqueda literal), `limit`, `offset` |
| GET | `/api/v1/workflows/{id}` | `workflows:read` | Detalle, borrador y última versión publicada, con incidencias |
| PATCH | `/api/v1/workflows/{id}` | `workflows:write` | Renombrar, describir, archivar o restaurar |
| PUT | `/api/v1/workflows/{id}/draft` | `workflows:write` | Guarda el borrador (`expected_revision` para la concurrencia optimista) |
| POST | `/api/v1/workflows/{id}/publish` | `workflows:write` | Publica si no hay incidencias. La versión queda inmutable. |
| GET | `/api/v1/workflows/{id}/versions[/{n}]` | `workflows:read` | Historial y definición de una versión |
| POST | `/api/v1/workflows/validate` | `workflows:read` | Valida una definición sin guardarla |

El formato de los flujos está documentado en [docs/workflow-dsl.md](docs/workflow-dsl.md).

### Ejecuciones

| Método | Ruta | Permiso | Descripción |
|---|---|---|---|
| POST | `/api/v1/workflows/{id}/runs` | `workflows:execute` | Ejecuta la última versión publicada (o `version`). `202`; con `idempotency_key` repetida, `200` y la misma ejecución |
| GET | `/api/v1/runs` | `runs:read` | Historial con filtros `workflow_id` y `status` |
| GET | `/api/v1/runs/{id}` | `runs:read` | Pasos, intentos (duración, clase de error, código HTTP), entrada y salida |
| POST | `/api/v1/runs/{id}/cancel` | `workflows:execute` | Cancela; el resultado de un paso en curso se descarta |
| POST | `/api/v1/runs/{id}/steps/{step}/resolve` | `workflows:execute` | Resuelve un paso en revisión: `retry`, `mark_succeeded` (con `output`) o `mark_failed` |

Estados de una ejecución: `pending → running → succeeded | failed | cancelled | timed_out`,
con `needs_review` cuando un paso no idempotente tiene un resultado desconocido. Ver
[ADR-0007](docs/adr/0007-motor-de-ejecucion.md).

Todos los errores siguen [RFC 9457](https://www.rfc-editor.org/rfc/rfc9457):

```json
{
  "type": "https://nexusflow.dev/problems/not-found",
  "title": "Not Found",
  "status": 404,
  "instance": "/no-existe",
  "request_id": "4de326fa33654caaa69a212a76c1e95b"
}
```

## Configuración

Toda la configuración entra por variables de entorno con el prefijo `NEXUSFLOW_`. La
aplicación **no arranca** si falta un valor obligatorio o si es inseguro para el
entorno: por ejemplo, un secreto de ejemplo con `NEXUSFLOW_ENV=production`.

| Variable | Obligatoria | Descripción |
|---|---|---|
| `NEXUSFLOW_DATABASE_URL` | Sí | `postgresql+asyncpg://...` |
| `NEXUSFLOW_REDIS_URL` | Sí | `redis://` o `rediss://` |
| `NEXUSFLOW_SECRET_KEY` | Sí | ≥ 32 caracteres aleatorios |
| `NEXUSFLOW_ENV` | No | `development` (por defecto), `test`, `production` |
| `NEXUSFLOW_LOG_LEVEL` / `NEXUSFLOW_LOG_JSON` | No | `INFO` / `true` |
| `NEXUSFLOW_CORS_ORIGINS` | No | Lista separada por comas. `*` está prohibido en producción. |
| `NEXUSFLOW_DOCS_ENABLED` | No | Expone `/docs` y `/openapi.json` |
| `NEXUSFLOW_MAX_REQUEST_BODY_BYTES` | No | Tamaño máximo del cuerpo (1 MiB). Responde `413` si se supera. |
| `NEXUSFLOW_CELERY_BROKER_URL` | No | Broker de Celery; por defecto, `NEXUSFLOW_REDIS_URL` |
| `NEXUSFLOW_RUN_MAX_DURATION_HOURS` | No | Plazo máximo de una ejecución (48) |
| `NEXUSFLOW_STEP_OUTPUT_MAX_BYTES` | No | Tamaño máximo de la salida o respuesta de un paso (256 KiB) |
| `NEXUSFLOW_EGRESS_ALLOW_HTTP` | No | Permitir `http://` en pasos (`false`; prohibido en producción) |
| `NEXUSFLOW_EGRESS_ALLOWED_PRIVATE_HOSTS` | No | Hosts que pueden resolver a IPs privadas (solo desarrollo) |
| `NEXUSFLOW_ACCESS_TOKEN_TTL_MINUTES` | No | Vida del access token (15) |
| `NEXUSFLOW_REFRESH_TOKEN_TTL_DAYS` | No | Vida del refresh token (7) |
| `NEXUSFLOW_REGISTRATION_ENABLED` | No | Registro público (`true`) |
| `NEXUSFLOW_COOKIE_SECURE` | No | Cookie solo por HTTPS (`true`; obligatorio en producción) |
| `NEXUSFLOW_LOGIN_RATE_LIMIT_PER_IP` / `_PER_EMAIL` | No | 20 por minuto / 10 por 15 minutos |
| `NEXUSFLOW_REGISTER_RATE_LIMIT_PER_IP` | No | 5 por hora |

Plantillas: [`.env.example`](.env.example) (Docker Compose) y
[`backend/.env.example`](backend/.env.example) (ejecución local).

## Seguridad

Autenticación y autorización (Fase 2):

- **Contraseñas** con Argon2id (12-128 caracteres, sin el usuario del email, no
  repetitivas). El hash se ejecuta en un hilo para no bloquear el event loop. Si un email
  no existe, se verifica igualmente contra un hash ficticio, de modo que la respuesta tarda
  lo mismo y el error es idéntico: no se pueden enumerar cuentas.
- **Access token** JWT HS256 de 15 minutos con `iss`, `aud`, `exp`, `nbf` y `type`
  obligatorios y una lista explícita de algoritmos (bloquea `alg: none` y la confusión de
  algoritmos). El rol **no** va en el token: se lee de la base de datos en cada petición,
  así que un cambio de rol o una desactivación tienen efecto inmediato.
- **Refresh token** opaco de 256 bits, guardado solo como SHA-256, en una cookie
  `HttpOnly; Secure; SameSite=Strict` restringida a `/api/v1/auth`. Se rota en cada uso; si
  reaparece un token ya rotado, se revoca toda la familia (detección de robo, RFC 9700).
- **Autorización por permisos** en el backend. El registro público ignora cualquier
  campo extra (`extra="forbid"`), así que nadie puede auto-asignarse un rol. Un
  administrador no puede modificarse a sí mismo.
- **Límites de uso** en Redis: por IP y por cuenta en el login, y por IP en el registro.
  Responden `429` con `Retry-After`. Si Redis no responde, los endpoints de autenticación
  **fallan cerrados** (503) en lugar de permitir fuerza bruta sin límite.
- **Auditoría** en la misma transacción que el cambio: registro, login correcto o
  fallido (con motivo, nunca la contraseña), logout, reutilización de tokens y cambios de
  rol o estado. Incluye IP y `request_id`.

Fundaciones (Fase 1):

- **Secretos.** Solo por entorno, con tipo `SecretStr` (no aparecen en `repr` ni en
  volcados). Los `.env` están en `.gitignore` y gitleaks los vigila en CI.
- **Logs.** Se redactan automáticamente las claves con `password`, `token`, `secret`,
  `authorization`, etc., también dentro de estructuras anidadas. No se registra SQL con
  parámetros.
- **Errores.** Los 500 devuelven un mensaje genérico y el `request_id`; la traza solo va
  a los logs. Los errores de validación no devuelven el valor recibido.
- **Cabeceras de respuesta.** `X-Content-Type-Options`, `X-Frame-Options`,
  `Referrer-Policy` y `Cross-Origin-Opener-Policy`. Un `X-Request-ID` entrante solo se
  acepta si cumple `[A-Za-z0-9._-]{1,64}`, para evitar inyección en logs.
- **Contenedor.** Usuario sin privilegios (UID 10001), imagen *slim* multi-etapa sin
  herramientas de compilación ni dependencias de desarrollo.
- **Infraestructura.** Redis con contraseña. Base de datos y Redis sin puertos expuestos.
  La API solo escucha en `127.0.0.1`.
- **Cadena de suministro.** `uv.lock` con hashes, `pip-audit` en CI y GitHub Actions
  fijadas por SHA de commit.

Motor de ejecución (Fase 4):

- **Anti-SSRF** en todas las llamadas salientes: solo https, IPs privadas y de metadatos
  bloqueadas, conexión a la IP ya validada (contra *DNS rebinding*), sin redirecciones,
  respuesta acotada.
- **Sin efectos duplicados:** reclamo atómico frente a mensajes repetidos;
  `Idempotency-Key` estable en todos los reintentos; revisión manual cuando el resultado
  de un paso no idempotente es desconocido.
- **Sin ejecuciones eternas:** timeout por paso, plazo máximo por ejecución, lease frente
  a workers caídos.

El cifrado de credenciales de integraciones llega en la Fase 5.

**Limitaciones conocidas (aceptadas y documentadas):**
- No hay verificación de email ni recuperación de contraseña: requieren un servicio de
  correo. Por eso el registro responde `409` a un email duplicado; con verificación por
  correo se podría responder siempre igual.
- No hay revocación inmediata de access tokens: se mitiga con su vida corta (15 min) y
  con la consulta del estado del usuario en cada petición.
- El límite de ventana fija permite hasta el doble del límite en el borde entre ventanas.

## Hoja de ruta

| Fase | Contenido | Estado |
|---|---|---|
| 1 | Fundaciones: configuración, logging, errores, DB, health checks, Docker, CI | ✅ Completada |
| 2 | Autenticación (Argon2, JWT + refresh con rotación), RBAC, auditoría | ✅ Completada |
| 3 | Definición de flujos: DSL, validación de grafo, versiones inmutables | ✅ Completada |
| 4 | Motor de ejecución con Celery, cliente HTTP anti-SSRF, reintentos, timeouts, idempotencia | ✅ Completada |
| 5 | Integraciones: credenciales cifradas, adaptadores, API externa simulada | ⏳ Pendiente |
| 6 | Webhooks entrantes (HMAC) y salientes (outbox) | ⏳ |
| 7 | Métricas y auditoría consultable | ⏳ |
| 8 | Frontend React + TypeScript + Mantine | ⏳ |
| 9 | E2E con Playwright, datos de demostración y guía de despliegue | ⏳ |

## Decisiones técnicas

Resumen. El razonamiento completo está en [docs/adr](docs/adr/).

- **Monolito modular** en lugar de microservicios: un equipo y un dominio. Los procesos
  `api`/`worker` escalan por separado sin separar el código.
  ([ADR-0001](docs/adr/0001-monolito-modular.md))
- **PostgreSQL es la fuente de verdad; Celery solo transporta.** Las entregas de Celery
  son *at-least-once*, así que la idempotencia se garantiza en la base de datos.
  ([ADR-0002](docs/adr/0002-postgres-fuente-de-verdad.md))
- **Errores RFC 9457 y logs JSON con `request_id`:** un contrato de errores estable para
  clientes y trazabilidad de cada petición.
  ([ADR-0003](docs/adr/0003-errores-y-observabilidad.md))
- **uv + Ruff + mypy strict:** builds reproducibles con lockfile y calidad verificable
  en CI. ([ADR-0004](docs/adr/0004-herramientas.md))
- **JWT corto + refresh opaco rotativo en cookie:** un XSS no puede robar la sesión
  larga, y un refresh token robado se detecta en cuanto se reutiliza.
  ([ADR-0005](docs/adr/0005-autenticacion.md))
- **DSL sin código ejecutable y versiones inmutables:** un editor no puede ejecutar código
  en el servidor, y lo que se ejecutó no puede cambiar después (trigger en PostgreSQL).
  ([ADR-0006](docs/adr/0006-dsl-de-flujos.md))
- **Reclamo atómico + lease + barrido:** los mensajes duplicados no ejecutan dos veces,
  los workers caídos se recuperan y un resultado desconocido en un paso no idempotente
  se para para revisión en lugar de arriesgar un cobro doble.
  ([ADR-0007](docs/adr/0007-motor-de-ejecucion.md))

El modelo de datos está documentado en [docs/data-model.md](docs/data-model.md).

## Licencia

[MIT](LICENSE)
