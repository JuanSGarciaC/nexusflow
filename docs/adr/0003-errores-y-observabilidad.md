# ADR-0003: Errores RFC 9457 y logs estructurados con request_id

- **Estado:** Aceptada e implementada (Fase 1)
- **Fecha:** 2026-10-27

## Decisión

- **Errores.** Todas las respuestas de error usan `application/problem+json` (RFC 9457),
  con `type`, `title`, `status`, `instance`, `detail` y `request_id`. Los errores de dominio
  heredan de `AppError` y declaran su status y su código.
- **Sin filtración de detalles.** Los 500 devuelven un mensaje genérico. Los 422 no
  devuelven el valor recibido, que podría ser una contraseña.
- **Logs.** structlog en JSON, en un único formato para los logs propios y los de
  librerías. Cada línea lleva un `request_id` (vía `contextvars`) y las claves sensibles
  se redactan automáticamente.
- **Middleware ASGI puro** en lugar de `BaseHTTPMiddleware`: no interfiere con respuestas
  en streaming y propaga bien los `contextvars`.

## Alternativas descartadas

- Formato de error propio: obliga a cada cliente a aprender otro contrato.
- `logging` estándar con texto libre: no se puede consultar ni agregar con fiabilidad.

## Consecuencias

Un incidente se puede investigar con un solo dato: el `request_id` que ve el usuario en
la respuesta aparece en todas las líneas de log de esa petición.
