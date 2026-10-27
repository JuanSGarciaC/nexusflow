# ADR-0009: Webhooks firmados y outbox transaccional

- **Estado:** Aceptada e implementada (Fase 6)
- **Fecha:** 2026-10-27

## Contexto

- **Entrada:** sistemas externos (ERP, tienda online) deben poder lanzar flujos sin un
  usuario humano y sin que cualquiera que conozca la URL pueda hacerlo.
- **Salida:** otros sistemas quieren enterarse de cómo terminó una ejecución sin consultar
  la API una y otra vez. No se puede perder un evento ni notificar algo que no ocurrió.

## Decisión

### Firma (ambos sentidos)

`X-NexusFlow-Signature: t=<unix>,v1=HMAC-SHA256(secreto, "<t>." + cuerpo)`. Es el mismo
esquema que Stripe, ampliamente conocido.

- Se firma el **cuerpo exacto en bytes**; la API lee `request.body()` antes de parsear.
- El timestamp está **dentro** de lo firmado, con una ventana de ±5 minutos (anti-replay).
- Comparación en tiempo constante; se admiten varias firmas `v1` para rotar el secreto.
- El secreto (`whsec_…`) se muestra **una sola vez** y se guarda cifrado con la misma
  clave Fernet que las credenciales, vinculado a su fila.

### Entrada: `POST /api/v1/hooks/{trigger_id}`

- Sin JWT: la firma es la autenticación. El id del trigger es un UUID no adivinable.
- **Deduplicación** reutilizando la idempotencia de ejecuciones:
  `idempotency_key = wh:<trigger>:<X-NexusFlow-Event-Id>`. Si no se envía id de evento,
  se usa el hash de `t` + cuerpo, de modo que una repetición exacta también se detecta.
  Un reenvío devuelve `200` con la misma ejecución.
- Un trigger inexistente y uno desactivado devuelven el **mismo 404** (sin enumeración).
- Límite de uso por trigger (Redis, falla cerrado) y tamaño máximo del cuerpo (1 MiB).
- Los campos que el flujo no declara se ignoran (los sistemas externos envían de más),
  pero los declarados se validan en tipo y obligatoriedad.

### Salida: outbox transaccional

1. Un hook `before_flush` de SQLAlchemy detecta las ejecuciones que pasan a `succeeded`,
   `failed`, `needs_review`, `timed_out` o `cancelled`, e inserta las filas de
   `webhook_deliveries` **en la misma transacción**. No depende de recordar llamar a nada en
   cada sitio donde cambia el estado.
2. Tras el commit, quien hizo el cambio encola las entregas. Si eso falla, el barrido de
   beat las recoge (patrón *polling publisher*).
3. El worker las entrega firmadas con el mismo cliente anti-SSRF:
   - **at-least-once**, con un `event_id` estable para que el receptor deduplique;
   - backoff de 30 s a 1 h con jitter;
   - agotados los intentos → `dead`, con reenvío manual desde la API.

## Alternativas descartadas

- **Notificar después del commit sin outbox.** Si el proceso muere entre el commit y el
  envío, el evento se pierde.
- **Notificar dentro de la transacción** (llamada HTTP antes del commit). Bloquea la
  transacción durante la llamada y avisa de cambios que podrían deshacerse.
- **CDC con Debezium o Kafka.** Es la solución a gran escala, pero excesiva aquí
  (ADR-0001).
- **Autenticar la entrada con una API key en la URL o en una cabecera.** No protege la
  integridad del cuerpo ni impide reutilizar una petición capturada.

## Consecuencias

- Los receptores deben ser idempotentes por `event_id`. Está documentado, y `mock-partner`
  incluye un receptor de referencia que verifica firma, ventana y duplicados.
- La latencia de un evento es la del commit más el encolado. En el peor caso (encolado
  fallido) es un intervalo de barrido, unos 15 s.
