# ADR-0006: DSL declarativo sin código ejecutable y versiones inmutables

- **Estado:** Aceptada e implementada (Fase 3)
- **Fecha:** 2026-10-27

## Contexto

Los usuarios con rol `editor` definen flujos que el servidor ejecuta. Si el formato
permitiera código (Python, JavaScript, plantillas Jinja con expresiones), cualquier editor
podría ejecutar código en el worker. Además, una ejecución debe poder reproducirse y
auditarse: hay que saber exactamente qué definición se ejecutó.

## Decisión

1. **DSL JSON con tipos de paso cerrados** (`http_request`, `transform`, `condition`,
   `delay`), validado con Pydantic y `extra="forbid"`.
2. **Sin evaluación de código.** Las condiciones son estructuras con operadores fijos; las
   plantillas solo admiten rutas (`{{ steps.a.output.id }}`). Un valor que contenga `{{ }}`
   no se vuelve a evaluar.
3. **Validación semántica separada de la estructural.** Los borradores pueden tener
   incidencias para poder editarse por partes; publicar exige que no haya ninguna.
4. **Referencias por dominadores.** Una plantilla solo puede usar la salida de un paso que
   *domina* al actual (que está en todos los caminos desde el inicio). Comprobar solo que
   sea un ancestro no basta: si está en otra rama, en ejecución faltaría el valor.
5. **Versiones inmutables, garantizadas en dos capas.** La aplicación nunca modifica una
   versión publicada, y un trigger de PostgreSQL rechaza `UPDATE` y `DELETE` sobre ellas.
6. **Concurrencia optimista en borradores.** El guardado hace
   `UPDATE … WHERE revision = :esperada`; si no se actualiza ninguna fila, la API responde
   `409`. No se mantienen bloqueos mientras alguien edita.
7. **Un borrador por flujo**, garantizado con un índice único parcial
   (`WHERE published_at IS NULL`).

## Alternativas descartadas

- **Expresiones Jinja o JSONLogic completas.** Más potentes, pero con una superficie de
  ataque mucho mayor (SSTI) y difíciles de validar de forma estática.
- **Grafo libre con bucles.** Los bucles complican la terminación, los límites de tiempo
  y la auditoría. Si hicieran falta, se añadirían como un paso `for_each` acotado.
- **Bloqueo pesimista del borrador** (marcarlo como "en edición"). Exige liberar el
  bloqueo y gestionar su caducidad; la concurrencia optimista basta para un editor web.
- **Solo validar en la aplicación.** Un error futuro o un acceso directo a la base de datos
  podría alterar una versión ya ejecutada; el trigger lo impide.

## Consecuencias

- El DSL es menos expresivo que un lenguaje de programación. Las transformaciones
  complejas deben hacerse en la API externa o en un paso futuro bien acotado.
- `alembic check` no ve los triggers: el trigger se crea a mano en la migración y tiene
  pruebas de integración propias.
