# ADR-0007: Motor de ejecución: reclamo atómico, lease y barrido

- **Estado:** Aceptada e implementada (Fase 4)
- **Fecha:** 2026-10-27
- **Complementa:** [ADR-0002](0002-postgres-fuente-de-verdad.md)

## Contexto

Celery con Redis entrega los mensajes *al menos una vez*:
- con `acks_late`, si un worker muere, el mensaje se reentrega;
- las tareas programadas (`countdown`) pueden volver a entregarse si se supera el
  `visibility_timeout`.

Además, un encolado puede fallar después del commit (Redis caído) y el worker puede morir
a mitad de una llamada HTTP. Con todo esto, el motor tiene que garantizar:

1. que un paso no se ejecute dos veces por mensajes duplicados;
2. que un efecto no idempotente (un cobro) no se repita por un reintento automático;
3. que ninguna ejecución quede colgada para siempre.

## Decisión

### Tres transacciones cortas por paso

1. **Reclamar.** Se bloquea la ejecución y después el paso (siempre en ese orden, también
   en la API, para evitar interbloqueos). Si el paso está `pending` o `retry_scheduled` y
   ya vencido, pasa a `running` con un **lease** = timeout del paso + margen, y se crea la
   fila del intento. Un mensaje duplicado encuentra el paso en `running` y no hace nada.
2. **Ejecutar** fuera de cualquier transacción: no se mantienen bloqueos durante una
   llamada HTTP de hasta 300 s.
3. **Registrar.** Se vuelve a bloquear y se comprueba que el reclamo sigue vigente (mismo
   estado y mismo número de intento). Si el barrido lo dio por perdido o la ejecución se
   canceló, el resultado se descarta y el intento queda marcado como `stale`.

### Clasificación de fallos

| Tipo | Ejemplos | ¿Reintento automático? |
|---|---|---|
| `not_sent` | DNS, conexión rechazada | Sí, siempre |
| `rejected` | 408, 429, 503 (respetando `Retry-After`) | Sí, siempre |
| `unknown` | Timeout de lectura, conexión cortada, 5xx, worker muerto | Solo si `side_effect` es `none` o `idempotent` |
| `permanent` | 4xx, destino bloqueado, respuesta demasiado grande | No |

Un `unknown` en un paso `non_idempotent` deja el paso en **`needs_review`**: una persona
comprueba en el sistema externo qué pasó y elige *reintentar*, *marcar como hecho* (con la
salida) o *marcar como fallido*. Todos los intentos envían la misma `Idempotency-Key`
(`run_id:step_id`), así el destino puede deduplicar.

### Barrido periódico (Celery beat, cada 15 s)

- **Mensajes perdidos:** reencola los pasos listos que nadie procesa. `last_dispatched_at`
  evita reencolar en bucle.
- **Workers caídos:** un paso en `running` con el lease vencido se trata como fallo
  `unknown` (`worker_lost`).
- **Plazos:** una ejecución que supera `run_max_duration_hours` pasa a `timed_out`.
- **Esperas largas:** los pasos `delay` de más de 15 minutos no se encolan con `countdown`;
  los recoge el barrido cuando vencen. Así nunca se supera el `visibility_timeout` de Redis.

### Seguridad de salida (anti-SSRF)

Ver `engine/http_client.py`:
- solo https;
- se resuelve el DNS y se bloquea cualquier IP no pública (incluida la de metadatos cloud);
- se conecta a la IP ya validada, con el `Host` y el SNI originales (contra *DNS rebinding*);
- sin redirecciones y con un tamaño máximo de respuesta.

## Alternativas descartadas

- **Exactly-once.** No existe entre sistemas independientes. Lo que sí se consigue es un
  efecto como máximo una vez: reclamo atómico, clave de idempotencia y revisión manual
  cuando el resultado es desconocido.
- **Mantener el bloqueo de fila durante la llamada HTTP.** Ocupa conexiones del pool
  durante minutos y no protege si el worker muere.
- **Reintentar siempre los 5xx.** Es el error clásico que duplica cobros.
- **Una cola en PostgreSQL** (`SKIP LOCKED`). Válida, pero ver ADR-0002: Celery aporta
  `countdown`, límites de tiempo y escalado de workers sin construirlo a mano.

## Consecuencias

- Cada paso cuesta 3 transacciones cortas, un precio aceptable a cambio de trazabilidad
  completa: cada intento queda registrado con su duración, su clase de error y su código
  HTTP.
- La ejecución es secuencial (un paso activo por ejecución): el DSL no tiene
  paralelismo. Si se añadiera, el reclamo ya funciona por paso.
- El barrido debe tener **una sola instancia** de beat.
