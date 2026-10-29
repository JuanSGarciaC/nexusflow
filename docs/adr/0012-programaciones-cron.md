# ADR-0012: Programaciones (cron) en el barrido existente

- **Estado:** Aceptada e implementada
- **Fecha:** 2026-10-29

## Contexto

Los flujos solo se lanzaban a mano o por webhook. La automatización empresarial necesita
también tareas recurrentes ("cada día laborable a las 09:00") sin depender de un sistema
externo que llame.

## Decisión

Una tabla `workflow_schedules` (expresión cron de 5 campos, zona horaria IANA, entrada fija,
`next_run_at` en UTC) y un `ScheduleFirer` que se ejecuta dentro de la misma tarea de barrido
que `beat` ya lanza cada 15 s. No se añade ningún proceso ni cola nueva.

**Por qué no APScheduler / un `beat` con entradas dinámicas:** las programaciones las crea
y borra el usuario en tiempo de ejecución. Un planificador en memoria perdería su estado al
reiniciarse o necesitaría su propio almacén. Aquí el estado (`next_run_at`) vive en
PostgreSQL, igual que el resto: es la misma decisión que el [ADR-0002](0002-postgres-fuente-de-verdad.md).

### Garantías

- **Un disparo, una ejecución.** La ejecución usa la clave de idempotencia
  `sched:<programación>:<instante previsto>`. Si el proceso muere después de crear la
  ejecución y antes de avanzar `next_run_at`, el siguiente barrido repite el disparo y
  `create_run` devuelve la misma ejecución. El orden es deliberado: primero el efecto
  (idempotente), después el avance.
- **Sin carreras entre barridos.** `next_run_at` solo se actualiza con
  `UPDATE … WHERE next_run_at = <valor leído>`; quien llega segundo no hace nada. Hay una
  prueba con 5 barridos simultáneos.
- **Los disparos perdidos no se recuperan.** Si el sistema estuvo caído, un disparo con más de
  `NEXUSFLOW_SCHEDULE_MISFIRE_GRACE_SECONDS` (300 s) de retraso se omite y queda anotado en
  `last_error`. Ejecutar de golpe 18 informes atrasados casi nunca es lo que se quiere, y es
  peligroso para acciones no idempotentes.
- **Un fallo no detiene la programación.** Si el flujo está archivado, sin versión publicada o
  la entrada ya no es válida, el motivo se guarda en `last_error` y se sigue con el siguiente
  disparo.
- **Cambios de hora.** `cronsim` evalúa la expresión en la zona horaria indicada: la hora que
  no existe (salto de primavera) se mueve a la siguiente válida y la que se repite (otoño) se
  dispara una sola vez. Está cubierto por pruebas con Europe/Madrid.

### Permisos

`schedules:manage`, solo para `editor` y `admin`: programar es ejecutar sin supervisión, así
que no basta con poder ejecutar a mano (`operator`).

## Consecuencias

- La precisión es la del barrido: un disparo puede retrasarse hasta `sweep_interval_seconds`
  (15 s por defecto). Suficiente para tareas de minutos; no para temporización exacta.
- El barrido ya no es solo mantenimiento: si `beat` está parado, las programaciones no
  disparan. `docs/deployment.md` lo indica.
- Una sola instancia de `beat` sigue siendo suficiente; si hubiera más, la idempotencia y el
  UPDATE condicional evitan duplicados.
- Sin límite de frecuencia mínima por programación más allá del minuto del cron, ni máximo de
  programaciones por flujo. Con usuarios no confiables habría que acotarlos.
