# Modelo de datos

> Refleja las migraciones aplicadas hasta la **Fase 6**. Cada fase añade sus tablas
> mediante una migración de Alembic; `alembic check` en CI garantiza que modelos y
> migraciones coinciden.

```mermaid
erDiagram
    users ||--o{ refresh_tokens : "tiene"
    users |o--o{ audit_log : "actor de"
    refresh_tokens |o--o| refresh_tokens : "reemplazado por"
    workflows ||--|{ workflow_versions : "versiones"
    users |o--o{ workflows : "crea"
    workflows ||--o{ workflow_runs : "ejecuciones"
    workflow_versions ||--o{ workflow_runs : "versión ejecutada"
    workflow_runs ||--|{ step_runs : "pasos"
    step_runs ||--|{ step_attempts : "intentos"
    users |o--o{ credentials : "crea"
    workflows ||--o{ webhook_triggers : "entrada"
    webhook_subscriptions ||--o{ webhook_deliveries : "outbox"

    users {
        uuid id PK
        varchar email UK "minúsculas (CHECK)"
        varchar password_hash "Argon2id"
        varchar role "admin|editor|operator|viewer (CHECK)"
        boolean is_active
        timestamptz last_login_at
        timestamptz created_at
        timestamptz updated_at
    }
    refresh_tokens {
        uuid id PK
        uuid user_id FK "ON DELETE CASCADE"
        uuid family_id "índice"
        varchar token_hash UK "SHA-256"
        timestamptz created_at
        timestamptz expires_at
        timestamptz revoked_at
        uuid replaced_by_id FK "ON DELETE SET NULL"
    }
    audit_log {
        bigint id PK
        timestamptz occurred_at "índice"
        uuid actor_user_id FK "ON DELETE SET NULL"
        varchar action "índice"
        varchar resource_type
        varchar resource_id
        inet ip_address
        varchar request_id
        jsonb details
    }
    workflows {
        uuid id PK
        varchar name UK
        text description
        varchar status "active|archived (CHECK)"
        uuid created_by FK
        timestamptz created_at
        timestamptz updated_at
    }
    workflow_versions {
        uuid id PK
        uuid workflow_id FK "ON DELETE RESTRICT"
        int version "UNIQUE(workflow_id, version)"
        int revision "concurrencia optimista"
        jsonb definition
        varchar definition_hash "SHA-256 del JSON canónico"
        uuid created_by FK
        uuid updated_by FK
        timestamptz published_at "NULL = borrador"
        uuid published_by FK
    }
    workflow_runs {
        uuid id PK
        uuid workflow_id FK
        uuid version_id FK "RESTRICT"
        varchar status "CHECK"
        varchar idempotency_key "UNIQUE parcial con workflow_id"
        varchar input_hash
        jsonb input
        jsonb output
        jsonb error
        timestamptz deadline_at
    }
    step_runs {
        uuid id PK
        uuid run_id FK
        varchar step_id "UNIQUE(run_id, step_id)"
        varchar status "CHECK"
        varchar side_effect
        varchar idempotency_key "run_id:step_id"
        int attempt_count
        int max_attempts
        timestamptz next_attempt_at
        timestamptz lease_expires_at
        timestamptz last_dispatched_at
        jsonb output
        jsonb error
    }
    step_attempts {
        bigint id PK
        uuid step_run_id FK
        int attempt_number "UNIQUE con step_run_id"
        varchar outcome "succeeded|failed|abandoned|stale"
        varchar error_class
        int http_status
        int duration_ms
    }
    credentials {
        uuid id PK
        varchar name UK
        varchar type "bearer_token|api_key_header|basic_auth"
        text_array allowed_hosts
        text secret_encrypted "Fernet, vinculado a id+name"
        varchar key_fingerprint "clave con la que se cifró"
        varchar hint "…a1b2"
        timestamptz last_used_at
    }
    webhook_triggers {
        uuid id PK "parte de la URL pública"
        uuid workflow_id FK
        boolean is_active
        text secret_encrypted
        int rate_limit_per_minute
    }
    webhook_subscriptions {
        uuid id PK
        varchar url
        varchar_array events
        uuid workflow_id FK "NULL = todos"
        text secret_encrypted
    }
    webhook_deliveries {
        uuid id PK
        uuid subscription_id FK
        uuid event_id "estable entre reintentos"
        varchar status "pending|delivering|retry_scheduled|delivered|dead"
        jsonb payload
        int attempt_count
        timestamptz next_attempt_at
    }
```

## Decisiones

| Decisión | Motivo |
|---|---|
| Claves UUID generadas en la aplicación | El id se conoce antes del INSERT y no revela el volumen de registros. `audit_log` usa `bigint`: es solo de inserción, se ordena por id y nunca se expone para enumerar. |
| `role` como `VARCHAR` + `CHECK` en lugar de `ENUM` nativo | Añadir un rol es una migración trivial; con `ENUM` habría que usar `ALTER TYPE`, que tiene restricciones dentro de transacciones. |
| `email` en minúsculas garantizado por `CHECK` | La aplicación normaliza, pero la base de datos es la última línea de defensa contra duplicados como `Ana@` y `ana@`. |
| Unicidad por restricción, no por consulta previa | Comprobar y luego insertar tiene una condición de carrera. Hay una prueba con 5 registros simultáneos. |
| Solo el hash del refresh token | Una copia de la base de datos no permite secuestrar sesiones. |
| `family_id` en refresh tokens | Permite revocar de una vez todos los tokens derivados de un mismo login. |
| `audit_log` con `ON DELETE SET NULL` | El historial sobrevive a la eliminación de usuarios. |
| `timestamptz` en todas las fechas | Nunca fechas sin zona horaria. |
| Índice único parcial `(workflow_id) WHERE published_at IS NULL` | Como máximo un borrador por flujo, garantizado por la base de datos. |
| Trigger `trg_workflow_versions_immutable` | Rechaza `UPDATE`/`DELETE` de una versión publicada: lo ejecutado no puede cambiar. |
| `definition_hash` | Identifica la definición exacta (JSON canónico) en auditoría y ejecuciones. |
| `workflows` sin borrado físico | Se archivan; `RESTRICT` en la FK impide borrar un flujo con versiones. |
| `UNIQUE (workflow_id, idempotency_key) WHERE idempotency_key IS NOT NULL` | Creación idempotente de ejecuciones, también con peticiones simultáneas. |
| `version_id` con `RESTRICT` | Cada ejecución apunta a la versión exacta (inmutable) que ejecutó. |
| Índice parcial `ix_step_runs_dispatch` | El barrido solo recorre pasos activos, no el historial completo. |
| `step_attempts` separado de `step_runs` | Historial completo de cada intento sin sobrescribir el anterior. |
| `credentials.secret_encrypted` sin columna en claro | El secreto solo existe cifrado; `key_fingerprint` permite saber qué filas faltan por rotar. |
| `webhook_deliveries` como outbox | Se inserta en la misma transacción que el cambio de estado (hook `before_flush`). |
| Deduplicación de entrada sin tabla propia | Reutiliza el índice único de idempotencia de `workflow_runs`. |
| Convención de nombres de restricciones | Nombres deterministas (`uq_users_email`, `fk_refresh_tokens_user_id_users`), así las migraciones son reproducibles. |

## Tablas planificadas

Ver [architecture.md §6](architecture.md#6-modelo-de-datos-planificado).
