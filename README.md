# NexusFlow

[![CI](https://github.com/JuanSGarciaC/nexusflow/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/JuanSGarciaC/nexusflow/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.13-blue)
![License](https://img.shields.io/badge/license-MIT-green)

Plataforma web de **automatización de procesos empresariales**. Permite definir flujos de
trabajo con pasos, condiciones y acciones, integrarlos con APIs REST externas y ejecutarlos
en segundo plano con reintentos, timeouts e idempotencia.

> **Estado del proyecto:** se está construyendo por fases verificables.
> **Fases 1 a 10 completadas y revisadas:** fundaciones, autenticación y RBAC, definición
> de flujos, motor de ejecución, credenciales cifradas, webhooks firmados, métricas y
> auditoría, panel web, E2E con datos de demostración y guía de despliegue, y programaciones
> cron. La revisión de
> seguridad ([OWASP API Top 10](docs/security-review.md)) y la de rendimiento
> ([mediciones](docs/performance.md)) están documentadas con sus pruebas.
> Esta documentación describe solo lo que ya existe y está probado. Lo pendiente aparece en la
> [hoja de ruta](#hoja-de-ruta).

## Contenido

- [Capturas](#capturas)
- [Qué demuestra este proyecto](#qué-demuestra-este-proyecto)
- [Arquitectura](#arquitectura)
- [Inicio rápido con Docker](#inicio-rápido-con-docker)
- [Desarrollo local en Windows (sin Docker)](#desarrollo-local-en-windows-sin-docker)
- [Pruebas y calidad](#pruebas-y-calidad)
- [API](#api)
- [Configuración](#configuración)
- [Seguridad](#seguridad)
- [Documentación](#documentación)
- [Hoja de ruta](#hoja-de-ruta)
- [Decisiones técnicas](#decisiones-técnicas)

## Capturas

Tomadas del sistema real (API + worker + beat + PostgreSQL + Redis) con los datos de
demostración de `nexusflow seed-demo`, que son ficticios. Se regeneran con
`CAPTURE_SCREENSHOTS=1 pnpm exec playwright test screenshots` (ver
[`frontend/e2e/screenshots.spec.ts`](frontend/e2e/screenshots.spec.ts)).

| | |
|---|---|
| ![Panel de métricas](docs/screenshots/01-panel.png)<br>**Panel:** volumen, tasa de éxito, p50/p95 y últimos fallos, calculados en PostgreSQL | ![Programaciones cron](docs/screenshots/04-programaciones.png)<br>**Programaciones cron** con zona horaria, próximo y último disparo |
| ![Cobro VIP con reintentos](docs/screenshots/06-detalle-ejecucion.png)<br>**Reintentos:** la pasarela simulada falla dos veces con 503 y el motor reintenta con la misma `Idempotency-Key` | ![Ejecución fallida](docs/screenshots/07-ejecucion-fallida.png)<br>**Error clasificado:** un 404 no se reintenta y se muestra el paso que falló |
| ![Editor de flujos](docs/screenshots/03-editor-flujo.png)<br>**Editor** con validación del grafo y versiones inmutables | ![Credenciales](docs/screenshots/08-credenciales.png)<br>**Credenciales** de solo escritura: solo se ven metadatos y una pista |
| ![Auditoría](docs/screenshots/09-auditoria.png)<br>**Auditoría** de solo inserción: la ejecución lanzada por el cron aparece sin actor humano | |

## Qué demuestra este proyecto

| Área | Qué se ve en el código |
|---|---|
| Backend | FastAPI con application factory, Pydantic v2, SQLAlchemy 2 async, errores RFC 9457 |
| Seguridad | Argon2id, JWT de vida corta, refresh tokens con rotación y detección de robo, RBAC por permisos, límites de uso, auditoría |
| Datos | PostgreSQL como fuente de verdad, migraciones Alembic versionadas y comprobadas en CI |
| Integración | Cliente HTTP anti-SSRF, reintentos con backoff y jitter, idempotencia de efectos, revisión manual de resultados inciertos |
| Sistemas distribuidos | Celery con entrega *at-least-once*, reclamo atómico en PostgreSQL, lease y barrido de recuperación, pruebas con un worker real |
| Operación | Logs JSON con `request_id`, health checks *liveness* / *readiness*, Docker no-root |
| Frontend | React 19 + TypeScript estricto + Mantine, **cliente tipado generado desde OpenAPI** (el CI falla si el contrato se desincroniza), sesión en memoria + cookie HttpOnly con renovación serializada, CSP sin scripts inline |
| Calidad | Ruff, mypy `strict`, ESLint; 362 pruebas de backend (88 % de cobertura con ramas, mínimo 85 % en CI) contra PostgreSQL y Redis reales, 27 de frontend y 8 E2E; CI con auditoría de dependencias y escaneo de secretos |

## Arquitectura

Monolito modular: un único código backend que se ejecuta como varios procesos
(`api`, `worker` y `beat`). Ver
[docs/architecture.md](docs/architecture.md) y las [decisiones (ADR)](docs/adr/).

```mermaid
flowchart LR
    browser[Navegador] -->|HTTP| web[web · nginx + SPA React]
    web -->|/api · mismo origen| api[api · FastAPI]
    client[Sistemas externos] -->|webhooks firmados| api
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
│  │  ├─ integrations/    # credenciales cifradas y adaptadores de autenticación
│  │  ├─ webhooks/        # firma HMAC, triggers entrantes, outbox y entregas salientes
│  │  ├─ observability/   # health checks
│  │  ├─ cli.py           # comando `nexusflow create-admin`
│  │  └─ main.py          # application factory
│  ├─ migrations/         # Alembic (async)
│  ├─ tests/{unit,integration}/
│  ├─ openapi.json        # contrato de la API (generado; fuente de los tipos del frontend)
│  ├─ Dockerfile · pyproject.toml · uv.lock
├─ frontend/
│  ├─ src/
│  │  ├─ api/             # cliente tipado (openapi-fetch), sesión y errores RFC 9457
│  │  ├─ auth/            # login, proveedor de sesión, guardas por permiso
│  │  ├─ features/        # panel, flujos, ejecuciones, credenciales, auditoría, usuarios
│  │  └─ components/      # layout y componentes compartidos
│  ├─ nginx.conf · security-headers.conf   # proxy al mismo origen y CSP
│  ├─ Dockerfile · package.json · pnpm-lock.yaml
├─ mock-partner/          # API externa simulada (demos y CI; no es parte del producto)
├─ scripts/               # utilidades de CI
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

# 5. (Opcional) Datos de demostración: 4 usuarios (uno por rol), flujos y ejecuciones
docker compose exec -e NEXUSFLOW_DEMO_PASSWORD="elige-una-clave-larga" api nexusflow seed-demo

# 6. Abrir el panel
start http://127.0.0.1:3000
```

Con el seed puedes entrar como `demo-admin@example.com`, `demo-editor@example.com`,
`demo-operator@example.com` o `demo-viewer@example.com` (todos con la contraseña elegida)
para ver qué permite cada rol. Es idempotente: ejecutarlo de nuevo no duplica nada.

En Linux/macOS el primer paso es `cp .env.example .env`.

`NEXUSFLOW_CREDENTIALS_ENCRYPTION_KEYS` necesita una clave Fernet:
`python -c "import base64, os; print(base64.urlsafe_b64encode(os.urandom(32)).decode())"`.

**Demo con API externa simulada.** El perfil `demo` añade `mock-partner`: un CRM con
token Bearer y una pasarela de pagos idempotente, con fallos inyectables. En `.env`,
rellena `MOCK_PARTNER_*` y permite el acceso por http dentro de la red de compose
(**solo en desarrollo**):

```powershell
# .env: NEXUSFLOW_EGRESS_ALLOW_HTTP=true  y  NEXUSFLOW_EGRESS_ALLOWED_PRIVATE_HOSTS=mock-partner
docker compose --profile demo up -d --build
```

[`scripts/ci_payment_flow.py`](scripts/ci_payment_flow.py) es un ejemplo completo: busca
un cliente con un token Bearer, comprueba si es VIP y cobra. La pasarela falla dos veces
con 503, el motor reintenta con la misma `Idempotency-Key` y la pasarela registra **un
solo** cargo.

- Panel web: http://127.0.0.1:3000
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

### Frontend (Node 22.22+ o 24 LTS)

```powershell
cd frontend
corepack enable          # activa pnpm en la versión fijada en package.json
pnpm install
pnpm dev                 # http://127.0.0.1:5173 — reenvía /api a http://127.0.0.1:8000
```

Si la API está en otra dirección: `$env:NEXUSFLOW_API_URL = "http://127.0.0.1:8001"`.
Tras cambiar la API, regenera el contrato y los tipos:

```powershell
cd backend;  uv run nexusflow export-openapi -o openapi.json
cd ../frontend; pnpm gen:api
```


## Pruebas y calidad

```powershell
cd backend
uv run ruff check .          # lint (incluye reglas de seguridad de Bandit)
uv run ruff format --check . # formato
uv run mypy                  # tipos en modo strict
uv run pytest                # unitarias; las de integración se omiten sin servicios
```

Frontend:

```powershell
cd frontend
pnpm lint; pnpm typecheck; pnpm test; pnpm build
```

**E2E (Playwright)** contra el stack completo, con los datos de demostración:

```powershell
docker compose up -d --build
docker compose exec -e NEXUSFLOW_DEMO_PASSWORD="demo-clave-larga-2026" api nexusflow seed-demo
cd frontend
pnpm exec playwright install chromium     # una vez
$env:E2E_PASSWORD = "demo-clave-larga-2026"; pnpm e2e
```

Las 8 pruebas cubren:
- un operador ejecuta un flujo y ve el resultado del worker;
- la clave de idempotencia evita duplicados;
- un editor ve los errores del validador y publica;
- un viewer solo puede leer, incluso si fuerza la URL;
- un cambio de rol queda auditado;
- la sesión sobrevive a una recarga y el logout la invalida en el servidor;
- un editor crea, pausa y elimina una programación cron (y el operador no la ve).

En CI se ejecutan contra nginx + API + worker en Docker y, si fallan, se guarda el informe
con trazas.

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
| `unit/test_credentials_units.py` | Cifrado con vinculación a la fila, manipulación detectada, rotación de claves, adaptadores, hosts válidos, redacción |
| `integration/test_credentials.py` | Secreto de solo escritura (ni en respuestas, ni en la tabla en claro, ni en la auditoría), permisos, `unknown_credential` al publicar, borrado bloqueado si está en uso, **inyección y redacción en el motor**, **host no permitido**, **cifrado intercambiado entre filas**, rotación |
| `unit/test_webhook_signing.py` | Firma: manipulación de un byte, otro secreto, ventana de tiempo, **timestamp reescrito**, cabeceras mal formadas, rotación con dos firmas |
| `integration/test_schedules.py` | API y permisos (solo editor/admin), validación de cron y zona horaria, **disparo con la clave de idempotencia (la caída entre crear la ejecución y avanzar no duplica)**, **5 barridos simultáneos → una sola ejecución**, disparos perdidos omitidos, errores anotados sin detener la programación; más un disparo real por la tarea de barrido con un worker Celery |
| `unit/test_cron.py` | Expresiones, zonas horarias, **salto de hora de primavera y repetición de otoño** (Europe/Madrid), expresiones inválidas |
| `integration/test_webhooks.py` | Entrada: secreto mostrado una vez, entrada filtrada, **deduplicación con y sin id de evento**, firmas rechazadas (ausente, otra, caducada, cuerpo cambiado), 404 indistinguible, rotación, límite de uso. Salida: **evento firmado verificado por el receptor**, filtros, **el outbox se deshace con el rollback**, reintentos con el mismo `event_id` → dead-letter → reenvío, anti-SSRF, recuperación por barrido |
| `integration/test_observability.py` | Métricas que reflejan ejecuciones reales (éxitos, fallos, reintentos, clases de error, flujos con más fallos); **percentiles exactos** con duraciones conocidas; ventana y filtro por flujo; canceladas fuera de la tasa de éxito; `/metrics` con token, etiquetas por plantilla de ruta (sin ids); auditoría con filtros, **paginación por cursor sin solapes** y prefijo que no actúa como comodín |
| `integration/test_workflows_api.py` | Ciclo de vida borrador → publicación → nueva versión, concurrencia optimista (4 guardados simultáneos: 1 gana), **inmutabilidad en la base de datos**, archivo, búsqueda literal, límite de tamaño (413) |
| `integration/test_authorization_matrix.py` | **Recorre las 50 operaciones del contrato**: sin token, todas salvo 8 públicas declaradas responden 401; ninguna escritura es posible como viewer. Validada con una mutación (quitar un permiso hace fallar la prueba) |
| `integration/test_demo_seed.py` | Seed idempotente con los cuatro roles, credenciales cifradas solo con tokens de `mock-partner`, rechazo de contraseña débil sin dejar datos a medias |
| `frontend/src/api/session.test.ts` | **Cinco 401 simultáneos → una sola renovación** (dos con el mismo refresh token cerrarían la sesión por detección de robo), reintento con el token nuevo, sin reintentos en `/auth/`, sin bucles |
| `frontend/src/test/app.test.tsx` | App completa con rutas reales: login fallido con el error RFC 9457 y su referencia, vuelta a la página pedida, sesión recuperada por cookie, menú y páginas según permisos, publicación bloqueada con incidencias, **conflicto 409 al guardar**, resolución manual solo para quien puede ejecutar |

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
| `workflows:write`, `credentials:manage`, `webhooks:manage`, `schedules:manage` | | | ✓ | ✓ |
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

### Credenciales

| Método | Ruta | Descripción |
|---|---|---|
| POST | `/api/v1/credentials` | Crea una credencial: `bearer_token`, `api_key_header` o `basic_auth`, con `allowed_hosts` |
| GET | `/api/v1/credentials[/{id}]` | Metadatos y pista (`…a1b2`). **Nunca** el secreto. |
| PATCH | `/api/v1/credentials/{id}` | Descripción y hosts permitidos |
| PUT | `/api/v1/credentials/{id}/secret` | Reemplaza el secreto |
| DELETE | `/api/v1/credentials/{id}` | `409` si algún flujo activo la usa |

Todas requieren `credentials:manage` (editor o admin). Un paso la usa con
`"credential": "<nombre>"`. Ver [ADR-0008](docs/adr/0008-credenciales.md).

### Programaciones (cron)

| Método | Ruta | Descripción |
|---|---|---|
| POST/GET | `/api/v1/workflows/{id}/schedules` | Crea una programación (cron de 5 campos + zona horaria IANA + entrada) / lista |
| PATCH/DELETE | `/api/v1/schedules/{id}` | Cambiar horario o entrada, pausar/reanudar / borrar |

Requieren `schedules:manage`. Cada disparo crea una ejecución con la última versión
publicada, con una clave de idempotencia derivada del instante previsto (nunca dos). Los
disparos perdidos por una caída más allá de 5 minutos se omiten, no se recuperan. Ver
[ADR-0012](docs/adr/0012-programaciones-cron.md).

### Webhooks

| Método | Ruta | Descripción |
|---|---|---|
| POST | `/api/v1/hooks/{trigger_id}` | **Público, firmado con HMAC.** Lanza el flujo. `202`; un evento repetido devuelve `200` con la misma ejecución |
| POST/GET | `/api/v1/workflows/{id}/triggers` | Crea un trigger (el secreto `whsec_…` se muestra una vez) / lista |
| PATCH/DELETE | `/api/v1/triggers/{id}` | Activar, renombrar, limitar / borrar |
| POST | `/api/v1/triggers/{id}/rotate-secret` | Secreto nuevo; el anterior deja de valer |
| POST/GET | `/api/v1/webhook-subscriptions` | Suscribe una URL https a `run.succeeded`, `run.failed`, `run.needs_review`, `run.timed_out` o `run.cancelled` |
| PATCH/DELETE | `/api/v1/webhook-subscriptions/{id}` | Editar / borrar |
| POST | `/api/v1/webhook-subscriptions/{id}/ping` | Evento de prueba |
| GET | `/api/v1/webhook-subscriptions/{id}/deliveries` | Historial, incluido el *dead-letter* |
| POST | `/api/v1/webhook-deliveries/{id}/redeliver` | Reenvía con el mismo `event_id` |

La gestión requiere `webhooks:manage`. Firma en ambos sentidos:
`X-NexusFlow-Signature: t=<unix>,v1=<HMAC-SHA256(secreto, "<t>." + cuerpo)>`, con una
ventana de 5 minutos. Ejemplo de envío firmado desde Python:

```python
import hashlib, hmac, json, time, urllib.request
secret, url = "whsec_...", "http://127.0.0.1:8000/api/v1/hooks/<trigger_id>"
body = json.dumps({"order_id": "A-1"}).encode()
t = int(time.time())
sig = hmac.new(secret.encode(), f"{t}.".encode() + body, hashlib.sha256).hexdigest()
req = urllib.request.Request(url, data=body, method="POST", headers={
    "Content-Type": "application/json",
    "X-NexusFlow-Signature": f"t={t},v1={sig}",
    "X-NexusFlow-Event-Id": "pedido-A-1",  # opcional: deduplicación de reenvíos
})
print(urllib.request.urlopen(req).read())
```

Para verificar los webhooks **salientes**, `mock-partner/app.py` (`_valid_signature`)
contiene un receptor de referencia. Ver [ADR-0009](docs/adr/0009-webhooks.md).

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

### Métricas y auditoría

| Método | Ruta | Permiso | Descripción |
|---|---|---|---|
| GET | `/api/v1/metrics/summary` | `metrics:read` | Ventana (`window_hours` 1-720) y `workflow_id` opcional. Ejecuciones por estado, **tasa de éxito**, duración media/p50/p95/máx, reintentos, serie temporal (por hora hasta 48 h, por día después), tiempos y fallos por tipo de paso, clases de error, flujos con más fallos y últimos fallos con su error |
| GET | `/api/v1/audit-logs` | `audit:read` | Filtros `action`, `action_prefix`, `actor_user_id`, `resource_type`, `resource_id`, `since`, `until`. Paginación por cursor (`before_id`, `limit` ≤ 200) |
| GET | `/metrics` | Bearer `NEXUSFLOW_METRICS_TOKEN` | Formato Prometheus: peticiones y latencia HTTP por plantilla de ruta, ejecuciones por estado, pasos activos, entregas pendientes y en dead-letter. **404 si no hay token configurado.** |

Las métricas de negocio se calculan en PostgreSQL al consultarlas (`percentile_cont`,
`COUNT … FILTER`): son las mismas cifras sin importar cuántos procesos API o worker haya.
Las de HTTP son por proceso; Prometheus suma las de cada réplica.

## Configuración

Toda la configuración entra por variables de entorno con el prefijo `NEXUSFLOW_`. La
aplicación **no arranca** si falta un valor obligatorio o si es inseguro para el
entorno: por ejemplo, un secreto de ejemplo con `NEXUSFLOW_ENV=production`.

| Variable | Obligatoria | Descripción |
|---|---|---|
| `NEXUSFLOW_DATABASE_URL` | Sí | `postgresql+asyncpg://...` |
| `NEXUSFLOW_REDIS_URL` | Sí | `redis://` o `rediss://` |
| `NEXUSFLOW_SECRET_KEY` | Sí | ≥ 32 caracteres aleatorios |
| `NEXUSFLOW_CREDENTIALS_ENCRYPTION_KEYS` | Sí | Clave(s) Fernet, separadas por comas; la primera cifra (`nexusflow generate-key`) |
| `NEXUSFLOW_ENV` | No | `development` (por defecto), `test`, `production` |
| `NEXUSFLOW_LOG_LEVEL` / `NEXUSFLOW_LOG_JSON` | No | `INFO` / `true` |
| `NEXUSFLOW_CORS_ORIGINS` | No | Lista separada por comas. `*` está prohibido en producción. |
| `NEXUSFLOW_DOCS_ENABLED` | No | Expone `/docs` y `/openapi.json` |
| `NEXUSFLOW_METRICS_TOKEN` | No | Token (≥ 24 caracteres) para leer `/metrics`. Sin él, `/metrics` responde 404 |
| `NEXUSFLOW_MAX_REQUEST_BODY_BYTES` | No | Tamaño máximo del cuerpo (1 MiB). Responde `413` si se supera. |
| `NEXUSFLOW_CELERY_BROKER_URL` | No | Broker de Celery; por defecto, `NEXUSFLOW_REDIS_URL` |
| `NEXUSFLOW_RUN_MAX_DURATION_HOURS` | No | Plazo máximo de una ejecución (48) |
| `NEXUSFLOW_STEP_OUTPUT_MAX_BYTES` | No | Tamaño máximo de la salida o respuesta de un paso (256 KiB) |
| `NEXUSFLOW_WEBHOOK_SIGNATURE_TOLERANCE_SECONDS` | No | Ventana anti-replay de las firmas (300) |
| `NEXUSFLOW_WEBHOOK_DELIVERY_MAX_ATTEMPTS` | No | Intentos antes del dead-letter (8) |
| `NEXUSFLOW_EGRESS_ALLOW_HTTP` | No | Permitir `http://` en pasos (`false`; prohibido en producción) |
| `NEXUSFLOW_EGRESS_ALLOWED_PRIVATE_HOSTS` | No | Hosts que pueden resolver a IPs privadas (solo desarrollo) |
| `NEXUSFLOW_ACCESS_TOKEN_TTL_MINUTES` | No | Vida del access token (15) |
| `NEXUSFLOW_REFRESH_TOKEN_TTL_DAYS` | No | Vida del refresh token (7) |
| `NEXUSFLOW_REGISTRATION_ENABLED` | No | Registro público (`true`) |
| `NEXUSFLOW_COOKIE_SECURE` | No | Cookie solo por HTTPS (`true`; obligatorio en producción) |
| `NEXUSFLOW_LOGIN_RATE_LIMIT_PER_IP` / `_PER_EMAIL` | No | 20 por minuto / 10 por 15 minutos |
| `NEXUSFLOW_REGISTER_RATE_LIMIT_PER_IP` | No | 5 por hora |
| `NEXUSFLOW_RUN_CREATE_RATE_LIMIT_PER_USER` | No | Ejecuciones manuales por minuto y usuario (120) |

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
- **Límites de uso** en Redis: por IP y por cuenta en el login, por IP en el registro, por
  trigger en los webhooks entrantes y por usuario al crear ejecuciones.
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

Webhooks (Fase 6):

- **HMAC-SHA256 sobre el cuerpo exacto y el timestamp**, ventana de 5 minutos,
  comparación en tiempo constante, secretos cifrados y mostrados una sola vez.
- **Deduplicación** de eventos entrantes; el mismo 404 para triggers inexistentes o
  desactivados; límite de uso por trigger.
- **Outbox transaccional** para los eventos salientes; entrega con anti-SSRF, reintentos y
  dead-letter.

Credenciales (Fase 5):

- **Cifradas** con Fernet (autenticado) y **vinculadas a su fila**. Rotación de claves
  con `MultiFernet` y `nexusflow rotate-credentials`.
- **Solo escritura** en la API. Se descifran únicamente en el worker.
- **Hosts permitidos** por credencial (anti-exfiltración), cabeceras no sobrescribibles y
  **redacción** si la API externa devuelve el secreto.

**Limitaciones conocidas (aceptadas y documentadas):**
- No hay verificación de email ni recuperación de contraseña: requieren un servicio de
  correo. Por eso el registro responde `409` a un email duplicado; con verificación por
  correo se podría responder siempre igual.
- No hay revocación inmediata de access tokens: se mitiga con su vida corta (15 min) y
  con la consulta del estado del usuario en cada petición.
- El límite de ventana fija permite hasta el doble del límite en el borde entre ventanas.

## Documentación

| Documento | Contenido |
|---|---|
| [docs/architecture.md](docs/architecture.md) | Contenedores, módulos, ciclo de una petición, garantías de ejecución |
| [docs/data-model.md](docs/data-model.md) | Tablas, restricciones e índices, con su motivo |
| [docs/workflow-dsl.md](docs/workflow-dsl.md) | Formato de los flujos: pasos, plantillas, condiciones, incidencias |
| [docs/api.md](docs/api.md) | Convenciones de la API: autenticación, errores, paginación, idempotencia, firma de webhooks (ejemplos en Python y Node probados) |
| [docs/deployment.md](docs/deployment.md) | Despliegue en una VM con TLS, copias de seguridad, rotación de secretos, observabilidad y escalado |
| [docs/security-review.md](docs/security-review.md) | Revisión frente al OWASP API Top 10 (2023): control y prueba que lo respalda en cada punto, y limitaciones aceptadas |
| [docs/performance.md](docs/performance.md) | Planes y tiempos de las consultas críticas con 50 000 ejecuciones, la optimización aplicada y cuándo dejará de bastar |
| [docs/adr/](docs/adr/) | 11 decisiones de arquitectura con alternativas descartadas |
| [SECURITY.md](SECURITY.md) | Cómo reportar una vulnerabilidad |
| `/docs` (Swagger) y [backend/openapi.json](backend/openapi.json) | Referencia completa de los endpoints |

## Hoja de ruta

| Fase | Contenido | Estado |
|---|---|---|
| 1 | Fundaciones: configuración, logging, errores, DB, health checks, Docker, CI | ✅ Completada |
| 2 | Autenticación (Argon2, JWT + refresh con rotación), RBAC, auditoría | ✅ Completada |
| 3 | Definición de flujos: DSL, validación de grafo, versiones inmutables | ✅ Completada |
| 4 | Motor de ejecución con Celery, cliente HTTP anti-SSRF, reintentos, timeouts, idempotencia | ✅ Completada |
| 5 | Integraciones: credenciales cifradas, adaptadores, API externa simulada | ✅ Completada |
| 6 | Webhooks entrantes (HMAC) y salientes (outbox) | ✅ Completada |
| 7 | Métricas de ejecución, Prometheus y auditoría consultable | ✅ Completada |
| 8 | Panel web React + TypeScript + Mantine | ✅ Completada |
| 9 | E2E con Playwright, datos de demostración y guía de despliegue | ✅ Completada |
| 10 | Programaciones cron con zona horaria | ✅ Completada |

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
- **Credenciales cifradas, de solo escritura y vinculadas a hosts:** un editor puede usar
  el token del CRM, pero no verlo ni enviarlo a otro servidor.
  ([ADR-0008](docs/adr/0008-credenciales.md))
- **Webhooks firmados + outbox transaccional:** solo quien tiene el secreto lanza flujos,
  una petición capturada no se puede reutilizar, y un evento saliente existe si y solo si
  su transacción se confirmó. ([ADR-0009](docs/adr/0009-webhooks.md))
- **Métricas de negocio desde PostgreSQL, HTTP con Prometheus:** cifras coherentes entre
  procesos sin otro almacén, y `/metrics` cerrado salvo que se configure un token.
  ([ADR-0010](docs/adr/0010-observabilidad.md))
- **SPA en el mismo origen que la API, tipos generados desde OpenAPI:** sin CORS, cookie
  `SameSite=Strict`, CSP sin scripts inline, y un cambio de contrato rompe la compilación
  del frontend en lugar de romper en producción. ([ADR-0011](docs/adr/0011-frontend.md))
- **Programaciones cron dentro del barrido existente:** el estado (`next_run_at`) está en
  PostgreSQL, el disparo es idempotente y los disparos perdidos tras una caída se omiten en
  lugar de ejecutarse tarde. ([ADR-0012](docs/adr/0012-programaciones-cron.md))

El modelo de datos está documentado en [docs/data-model.md](docs/data-model.md).

## Licencia

[MIT](LICENSE)
