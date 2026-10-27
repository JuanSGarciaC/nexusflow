# ADR-0002: PostgreSQL como fuente de verdad; Redis y Celery como transporte

- **Estado:** Aceptada (implementación en las fases 4 a 6)
- **Fecha:** 2026-10-27

## Contexto

Las ejecuciones deben sobrevivir a la caída de un worker, no duplicar acciones con efectos
externos y dejar un historial auditable. Celery con `acks_late` garantiza la entrega
*at-least-once*: un mensaje puede procesarse más de una vez.

## Decisión

1. El estado de cada ejecución, paso e intento vive en **PostgreSQL**. Un mensaje de Celery
   solo dice "revisa el paso X"; nunca transporta el estado.
2. Cada tarea bloquea su fila (`SELECT … FOR UPDATE`) y valida la transición en una máquina
   de estados explícita. Si el paso ya terminó, la tarea no hace nada.
3. Los eventos salientes se escriben en una tabla **outbox** dentro de la misma transacción
   que el cambio de estado, y otro proceso los entrega.
4. Las acciones declaran su `side_effect` (`none`, `idempotent`, `non_idempotent`). Un
   resultado desconocido en una acción `non_idempotent` no se reintenta: pasa a revisión
   humana.

## Por qué Redis + Celery y no una cola en PostgreSQL

La carga son llamadas de red lentas y fallables, con reintentos diferidos, y el worker
debe escalar por separado. Celery resuelve los reintentos programados, los límites de
tiempo y la concurrencia del worker. Redis, además, ya hace falta para los límites de uso.
Con un volumen bajo y sin llamadas externas, una cola en PostgreSQL (`SKIP LOCKED`)
habría sido suficiente.

## Consecuencias

- Ejecutar dos veces el mismo mensaje no tiene efecto doble (se probará explícitamente).
- Si Redis pierde mensajes, `beat` detecta los pasos atascados en PostgreSQL y los reencola.
- Cada transición de estado cuesta escrituras en la base de datos: es un coste aceptable
  a cambio de trazabilidad.
