# Guion de demostración (3–4 minutos)

Para grabar un vídeo corto o enseñarlo en directo en una entrevista. Todos los datos son
**ficticios** (`nexusflow seed-demo`: dominios `example.com` y clientes inventados) y todo
corre en local; no es un sistema en producción, y conviene decirlo así.

Cada paso de este guion se ejecutó contra el stack real el 1 nov 2026.

## Preparación (antes de grabar, ~5 min)

```powershell
Copy-Item .env.example .env
# Edita .env: secretos reales (ver README), y para la API simulada:
#   NEXUSFLOW_EGRESS_ALLOW_HTTP=true
#   NEXUSFLOW_EGRESS_ALLOWED_PRIVATE_HOSTS=mock-partner
#   MOCK_PARTNER_CRM_TOKEN, MOCK_PARTNER_PAYMENTS_KEY y MOCK_PARTNER_WEBHOOK_SECRET con valores aleatorios
docker compose --profile demo up -d --build

# El seed necesita también los tokens del socio simulado; sin ellos omite el flujo de cobro.
$envs = Get-Content .env | Where-Object { $_ -match '^MOCK_PARTNER_(CRM_TOKEN|PAYMENTS_KEY)=' }
$flags = $envs | ForEach-Object { "-e"; $_ }
docker compose exec -e NEXUSFLOW_DEMO_PASSWORD="demo-clave-larga-2026" @flags api nexusflow seed-demo
```

Si los puertos 8000, 3000 u 8080 están ocupados, cambia `API_PORT`, `WEB_PORT` y
`MOCK_PARTNER_PORT` en `.env`.

Ten abiertas dos cosas: el panel (`http://127.0.0.1:3000`) y una terminal en la raíz del
repositorio.

## Guion

| Tiempo | Qué se ve | Qué se dice |
|---|---|---|
| 0:00–0:20 | Panel como `demo-admin@example.com` | «NexusFlow ejecuta flujos que llaman a APIs externas que fallan. Lo que quiero enseñar no es el panel, sino qué pasa cuando el tercero falla o el mismo evento llega dos veces.» |
| 0:20–1:20 | Entrar como `demo-operator@example.com` → Flujos → **Demo · Cobro a cliente VIP** → pestaña **Ejecutar**, entrada `{"customer_id": "c-100", "amount": 49.9}` y clave de idempotencia `demo-1` → abrir la ejecución | «La pasarela simulada responde 503 dos veces. El motor reintenta con backoff y manda **la misma `Idempotency-Key`** en los tres intentos: la pasarela registra un solo cobro.» Señalar el historial: `503`, `503`, éxito. |
| 1:20–1:40 | Ejecutar de nuevo con la **misma** clave `demo-1` | «Si el operador pulsa dos veces, o su red reintenta, la misma clave devuelve la misma ejecución en vez de crear otra.» |
| 1:40–2:40 | Terminal: el fragmento de [abajo](#webhook-desde-la-terminal) | «Ahora un ERP avisa por webhook. Una firma incorrecta da 401 y no crea nada. La firmada da 202 y se ejecuta en segundo plano. Si el ERP reenvía **el mismo evento**, recibe 200 con `duplicate: true` y la misma ejecución.» |
| 2:40–3:10 | Entrar como `demo-viewer@example.com`: ve flujos y ejecuciones, pero no la pestaña **Ejecutar** ni los menús Credenciales y Auditoría | «Los permisos se aplican en el backend en cada endpoint; la interfaz solo oculta lo que no puedes hacer. Hay una prueba que recorre las 50 operaciones de la API comprobando eso.» |
| 3:10–3:40 | Como admin: **Credenciales** (solo pista, nunca el secreto) y **Auditoría** (`webhook.trigger_created`, `run.created`) | «Los secretos son de solo escritura y están cifrados. La auditoría es de solo inserción y se escribe en la misma transacción que el cambio.» |
| 3:40–4:00 | README: tabla de pruebas y CI en verde | «Cada cosa que he enseñado tiene una prueba que falla si se rompe, contra PostgreSQL y Redis reales; el caso del webhook está en `docs/caso-de-estudio-webhook.md`.» |

## Webhook desde la terminal

Los disparadores webhook se gestionan por API (no tienen pantalla en el panel). Guarda esto
como `webhook_demo.py` fuera del repositorio y ejecútalo con `python webhook_demo.py`.
Solo usa la biblioteca estándar:

```python
import hashlib, hmac, json, os, time, urllib.error, urllib.request

BASE = os.environ.get("NEXUSFLOW_URL", "http://127.0.0.1:8000")
PASSWORD = os.environ.get("NEXUSFLOW_DEMO_PASSWORD", "demo-clave-larga-2026")

def call(method, path, token=None, body=None, raw=None, headers=None):
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    req = urllib.request.Request(BASE + path, data=data, method=method,
                                 headers={"Content-Type": "application/json", **(headers or {})})
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"null")

_, auth = call("POST", "/api/v1/auth/login",
               body={"email": "demo-editor@example.com", "password": PASSWORD})
token = auth["access_token"]
_, flows = call("GET", "/api/v1/workflows?limit=50", token)
flow = next(w for w in flows["items"] if w["name"] == "Demo · Clasificar pedido")
_, trigger = call("POST", f"/api/v1/workflows/{flow['id']}/triggers", token, {"name": "erp-demo"})

body = json.dumps({"order_id": "PED-1001", "amount": 2500, "country": "ES"}).encode()
event_id = f"evt-{int(time.time())}"

def send(secret):
    t = str(int(time.time()))
    v1 = hmac.new(secret.encode(), f"{t}.".encode() + body, hashlib.sha256).hexdigest()
    return call("POST", trigger["url_path"], raw=body,
                headers={"X-NexusFlow-Signature": f"t={t},v1={v1}", "X-NexusFlow-Event-Id": event_id})

print("1. Firma incorrecta :", send("whsec_" + "x" * 40))
print("2. Firmado          :", send(trigger["secret"]))
print("3. Mismo evento     :", send(trigger["secret"]))
```

Salida obtenida (los `run_id` cambian en cada ejecución):

```
1. Firma incorrecta : (401, {...})
2. Firmado          : (202, {'run_id': '…', 'duplicate': False})
3. Mismo evento     : (200, {'run_id': '…', 'duplicate': True})
```

Cada ejecución del script crea un disparador nuevo (`erp-demo`) y usa un id de evento nuevo,
así que se puede repetir. La ejecución lanzada aparece en **Ejecuciones** con origen
`webhook`; como el pedido es de 2500, el flujo lo manda a revisión (`manual_review`).

## Preguntas que suelen salir

Respuestas preparadas en [decisiones-y-tradeoffs.md](decisiones-y-tradeoffs.md#preguntas-típicas-y-cómo-responderlas).
