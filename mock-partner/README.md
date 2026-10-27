# mock-partner

API externa **simulada** para demos y para el smoke test de CI. Representa los sistemas
de terceros (un CRM y una pasarela de pagos) a los que llaman los flujos de NexusFlow.
No forma parte del producto.

| Endpoint | Autenticación | Notas |
|---|---|---|
| `GET /crm/customers/{id}` | `Authorization: Bearer $MOCK_PARTNER_CRM_TOKEN` | Clientes `c-100`, `c-200`, `c-300` |
| `POST /payments/charges` | `X-Api-Key: $MOCK_PARTNER_PAYMENTS_KEY` | Respeta `Idempotency-Key`: misma clave → mismo cargo (`replayed: true`) |
| `GET /payments/charges` | — | Lista de cargos, para comprobar que no hay duplicados |

Fallos inyectables con parámetros de consulta: `?delay=3` (timeout),
`?fail=2&key=x&status=503` (las dos primeras llamadas con clave `x` fallan).

Se levanta con el perfil `demo` de Docker Compose:

```bash
docker compose --profile demo up -d --build
```
