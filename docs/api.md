# Guía de la API

La referencia completa (rutas, esquemas y ejemplos) es el contrato OpenAPI 3.1:

- Interactiva: `http://127.0.0.1:8000/docs` (Swagger UI) o `/redoc`
- Archivo versionado: [`backend/openapi.json`](../backend/openapi.json). El CI comprueba que
  coincide con el código.

Esta guía explica las **convenciones** que aplican a todos los endpoints.

## Autenticación

```http
POST /api/v1/auth/login
Content-Type: application/json

{"email": "ana@example.com", "password": "…"}
```

```json
{"access_token": "eyJ…", "token_type": "bearer", "expires_in": 900}
```

- Envía `Authorization: Bearer <access_token>` en cada petición. Caduca a los 15 minutos.
- La respuesta del login fija la cookie `nexusflow_refresh` (`HttpOnly; Secure;
  SameSite=Strict; Path=/api/v1/auth`). `POST /api/v1/auth/refresh` la canjea por un access
  token nuevo **y la rota**.
- Si un refresh token ya usado vuelve a aparecer, se revoca toda la sesión (detección de
  robo). Los clientes deben **serializar** las renovaciones, como hace el panel web.
- `POST /api/v1/auth/logout` revoca la sesión en el servidor.

Los permisos de cada rol están en el [README](../README.md#roles-y-permisos). Sin token se
responde `401`; sin permiso, `403`.

## Errores (RFC 9457)

Todas las respuestas de error usan `Content-Type: application/problem+json`:

```json
{
  "type": "https://nexusflow.dev/problems/validation-error",
  "title": "Los datos enviados no son válidos",
  "status": 422,
  "instance": "/api/v1/workflows",
  "request_id": "c6cec47a51de4e448c5fe8c6ab76482d",
  "errors": [
    {"loc": ["body", "name"], "msg": "Field required", "type": "missing"}
  ]
}
```

- `request_id` también llega en la cabecera `X-Request-ID` y aparece en todas las líneas de
  log de la petición. Inclúyelo al reportar un problema.
- Un `X-Request-ID` propio (`[A-Za-z0-9._-]{1,64}`) se respeta, para correlacionar con tus
  propios sistemas.
- Los errores de validación nunca devuelven el valor recibido, que podría ser un secreto.

| Código | Significado habitual |
|---|---|
| 401 | Sin token, token caducado o firma de webhook inválida |
| 403 | El rol no tiene el permiso, o la acción no está permitida (p. ej., modificarse a sí mismo) |
| 404 | No existe, o no se revela su existencia (triggers de webhook) |
| 409 | Conflicto de estado: revisión desactualizada, nombre duplicado, idempotencia con otra entrada |
| 413 | Cuerpo demasiado grande (`NEXUSFLOW_MAX_REQUEST_BODY_BYTES`, 1 MiB por defecto) |
| 422 | Datos no válidos, definición de flujo con incidencias al publicar, o webhook cuyo cuerpo no es un objeto JSON |
| 429 | Límite de uso superado: respeta `Retry-After` (segundos) |
| 503 | Dependencia no disponible (p. ej., Redis en endpoints con límite de uso) |

## Paginación

- **Listados** (`/workflows`, `/runs`, `/users`): `limit` (≤ 100) y `offset`. La respuesta
  incluye `total`.
- **Auditoría** (`/audit-logs`): por cursor. Pasa `next_before_id` como `before_id`; si es
  `null`, no hay más páginas. Es estable aunque lleguen eventos nuevos mientras se pagina.

## Idempotencia de las ejecuciones

```http
POST /api/v1/workflows/{id}/runs
{"input": {"order_id": "PED-1"}, "idempotency_key": "pedido-PED-1"}
```

| Situación | Respuesta |
|---|---|
| Clave nueva | `202` con la ejecución creada |
| Misma clave y misma entrada | `200` con **la misma** ejecución (no se crea otra) |
| Misma clave, otra entrada | `409` |

Reintentar una petición que falló por red con la misma clave es siempre seguro.

En las llamadas que el motor hace a APIs externas, cada paso envía
`Idempotency-Key: <run_id>:<step_id>`, estable entre reintentos (ver
[workflow-dsl.md](workflow-dsl.md#efectos-secundarios-side_effect)).

## Concurrencia optimista (borradores)

`PUT /workflows/{id}/draft` y `POST /workflows/{id}/publish` exigen `expected_revision`. Si
otra persona guardó antes, la respuesta es `409` y no se sobrescribe nada: recarga el flujo
y vuelve a aplicar tus cambios.

## Programaciones (cron)

```bash
curl -X POST "$API/api/v1/workflows/$WORKFLOW_ID/schedules" \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"name": "Informe diario", "cron_expression": "0 9 * * 1-5",
       "timezone": "Europe/Madrid", "input": {"region": "eu"}}'
```

- Cron de **5 campos** (minuto, hora, día del mes, mes, día de la semana) evaluado en la
  zona horaria IANA indicada. Una expresión o zona inválida responde `422`.
- `next_run_at` (UTC) es el próximo disparo; `last_run_id` y `last_error` explican el último.
- Pausar con `PATCH {"is_active": false}`; reanudar recalcula el próximo disparo desde ahora.
- Los disparos perdidos por una caída (más de 5 min de retraso) **se omiten**.
- Las ejecuciones creadas llevan `trigger_type: "schedule"`.

## Webhooks entrantes (lanzar un flujo)

Cada trigger tiene una URL pública y un secreto `whsec_…`, que se muestra una sola vez.
Firma el **cuerpo exacto** que envías:

```
X-NexusFlow-Signature: t=<unix>,v1=<hex(HMAC-SHA256(secreto, "<t>." + cuerpo))>
X-NexusFlow-Event-Id: <id único del evento>   (opcional, para deduplicar reenvíos)
```

Python:

```python
import hashlib, hmac, json, time, urllib.request

secret = "whsec_..."
url = "https://nexusflow.example.com/api/v1/hooks/<trigger_id>"
body = json.dumps({"order_id": "PED-1"}).encode()
t = str(int(time.time()))
sig = hmac.new(secret.encode(), f"{t}.".encode() + body, hashlib.sha256).hexdigest()
req = urllib.request.Request(url, data=body, method="POST", headers={
    "Content-Type": "application/json",
    "X-NexusFlow-Signature": f"t={t},v1={sig}",
    "X-NexusFlow-Event-Id": "evt-0001",
})
print(urllib.request.urlopen(req).status)   # 202, o 200 si el evento ya se recibió
```

Node.js:

```js
import { createHmac } from "node:crypto";
const body = JSON.stringify({ order_id: "PED-1" });
const t = Math.floor(Date.now() / 1000);
const v1 = createHmac("sha256", process.env.WHSEC).update(`${t}.${body}`).digest("hex");
await fetch(url, { method: "POST", body, headers: {
  "Content-Type": "application/json", "X-NexusFlow-Signature": `t=${t},v1=${v1}` } });
```

Se rechaza (`401`) una firma ausente o incorrecta, un cuerpo modificado o un `t` con más de
5 minutos de diferencia (protección contra *replay*). Un trigger inexistente y uno
desactivado responden igual (`404`).

## Webhooks salientes (recibir eventos)

`POST /api/v1/webhook-subscriptions` suscribe una URL `https` a `run.succeeded`,
`run.failed`, `run.needs_review`, `run.timed_out` o `run.cancelled`. NexusFlow firma igual
que arriba y envía además `X-NexusFlow-Event-Id` y `X-NexusFlow-Event-Type`.

Para verificar en el receptor:
1. Recalcula el HMAC sobre `"<t>." + cuerpo_crudo` y compáralo en tiempo constante
   (`hmac.compare_digest`).
2. Rechaza `t` con más de 5 minutos de antigüedad.
3. Deduplica por `X-NexusFlow-Event-Id`: los reintentos conservan el mismo id.

Las entregas se reintentan con backoff exponencial. Al agotar los intentos pasan a
*dead-letter* y se pueden reenviar a mano con
`POST /api/v1/webhook-deliveries/{id}/redeliver`.
