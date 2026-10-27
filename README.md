# NexusFlow

[![CI](https://github.com/JuanSGarciaC/nexusflow/actions/workflows/ci.yml/badge.svg)](https://github.com/JuanSGarciaC/nexusflow/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.13-blue)
![License](https://img.shields.io/badge/license-MIT-green)

Plataforma web de **automatización de procesos empresariales**. Permite definir flujos de
trabajo con pasos, condiciones y acciones, integrarlos con APIs REST externas y ejecutarlos
en segundo plano con reintentos, timeouts e idempotencia.

> **Estado del proyecto:** se está construyendo por fases verificables.
> **Fase 1 (Fundaciones) completada.** Esta documentación describe solo lo que ya
> existe y está probado. Lo pendiente aparece en la [hoja de ruta](#hoja-de-ruta).

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
| Datos | PostgreSQL como fuente de verdad, migraciones Alembic versionadas y comprobadas en CI |
| Integración | (Fases 4-6) adaptadores HTTP, reintentos seguros, idempotencia, webhooks firmados |
| Operación | Logs JSON con `request_id`, health checks *liveness* / *readiness*, Docker no-root |
| Calidad | Ruff, mypy `strict`, pruebas contra PostgreSQL y Redis reales, CI con escaneo de secretos |

## Arquitectura

Monolito modular: un único código backend que se ejecuta como varios procesos
(`api`, y a partir de la Fase 4 `worker` y `beat`). Ver
[docs/architecture.md](docs/architecture.md) y las [decisiones (ADR)](docs/adr/).

```mermaid
flowchart LR
    client[Cliente / Panel web] -->|HTTP| api[api · FastAPI]
    api --> pg[(PostgreSQL)]
    api --> redis[(Redis)]
    worker[worker · Celery<br/><i>Fase 4</i>] -.-> pg
    worker -.-> redis
```

Estructura del repositorio:

```
nexusflow/
├─ backend/
│  ├─ src/nexusflow/
│  │  ├─ core/            # configuración, logging, errores, middleware
│  │  ├─ db/              # base declarativa, sesiones, registro de modelos
│  │  ├─ observability/   # health checks
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

uv run uvicorn nexusflow.main:app_factory --factory --reload
```

> A partir de la Fase 4, Celery no admite oficialmente Windows nativo como plataforma
> de producción. El worker se ejecutará con Docker. Para desarrollo se documentará
> `--pool=solo` como alternativa limitada.

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
| `integration/test_readiness.py` | `/health/ready` con servicios reales y con Redis caído; migraciones *upgrade/downgrade*; modelos y migraciones sincronizados (`alembic check`) |

En CI se define `NEXUSFLOW_REQUIRE_INTEGRATION=1`. Si faltan los servicios, las pruebas
de integración **fallan** en lugar de omitirse en silencio.

## API

| Método | Ruta | Descripción |
|---|---|---|
| GET | `/health/live` | El proceso responde. No consulta dependencias. |
| GET | `/health/ready` | PostgreSQL y Redis accesibles. Devuelve `503` si alguno falla. |
| GET | `/docs`, `/openapi.json` | OpenAPI 3.1. Se puede desactivar con `NEXUSFLOW_DOCS_ENABLED=false`. |

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

Plantillas: [`.env.example`](.env.example) (Docker Compose) y
[`backend/.env.example`](backend/.env.example) (ejecución local).

## Seguridad

Implementado en la Fase 1:

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

Autenticación, RBAC, límites de uso, cifrado de credenciales y protección anti-SSRF
llegan en las fases 2 y 5.

## Hoja de ruta

| Fase | Contenido | Estado |
|---|---|---|
| 1 | Fundaciones: configuración, logging, errores, DB, health checks, Docker, CI | ✅ Completada |
| 2 | Autenticación (Argon2, JWT + refresh con rotación), RBAC, auditoría | ⏳ Pendiente |
| 3 | Definición de flujos: DSL, validación de grafo, versiones inmutables | ⏳ |
| 4 | Motor de ejecución con Celery: estados, reintentos, timeouts, idempotencia | ⏳ |
| 5 | Integraciones: credenciales cifradas, cliente HTTP anti-SSRF, adaptadores | ⏳ |
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

## Licencia

[MIT](LICENSE)
