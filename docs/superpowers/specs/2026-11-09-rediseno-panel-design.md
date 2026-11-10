# Rediseño del panel: lienzo de flujos, sistema visual y pantallas faltantes

Fecha: 2026-11-09 · Estado: aprobado por el dueño el 2026-11-09

## Intención

NexusFlow tiene un backend sólido y un panel que no lo muestra: Mantine casi de fábrica, sin
CSS propio, flujos visibles solo como JSON y varias funciones de la API sin pantalla. El
proyecto es parte de un portafolio y quien lo abre decide en segundos si sigue mirando. El
panel debe verse como un producto profesional y llamativo, y cada efecto visual debe decir
algo del sistema.

## Objetivos

1. Un sistema visual oscuro con profundidad: degradados, capas, bordes iluminados en
   movimiento y luz que sigue al cursor.
2. Un lienzo arrastrable y con zoom que dibuja cada flujo como nodos conectados, y que en una
   ejecución anima lo que pasó de verdad.
3. Pantallas para lo que la API ya ofrece y el panel no usa.
4. Reejecutar una ejecución terminada.
5. Que el panel no cargue más lento por esto, con la medición escrita.

## Fuera de alcance

- Editor de flujos arrastrable. Editar sigue siendo JSON con el validador actual.
- Tipos de paso nuevos, landing pública, WebGL o three.js, comparación de versiones.
- Refactor del backend. Ya pasa mypy `strict` y ruff; solo se toca si aparece un fallo.

## Lo que haría fracasar el resultado aunque funcione

- Adorno que estorbe leer tablas, estados o errores.
- Animaciones que ignoren `prefers-reduced-motion` o sigan corriendo con la pestaña oculta.
- Romper la CSP (`script-src 'self'`, fuentes e imágenes solo propias).
- Que el panel de métricas pague el peso del lienzo.
- Información que solo exista en el lienzo: todo debe seguir disponible como texto o tabla.

## Restricciones

- Todo gratuito y local: solo librerías de código abierto, sin servicios de pago ni claves
  de API externas.
- El backend no necesita cambios: la API ya ofrece todo. El dueño autoriza tocarlo si
  aparece una necesidad real; en ese caso se regenera `openapi.json` y el cliente tipado.
- React 19, TypeScript estricto, Mantine 9, react-query, cliente tipado desde OpenAPI.
- CSP actual: sin CDN. Fuentes y recursos van autohospedados.
- Las 8 pruebas E2E actuales siguen pasando; los selectores por rol y texto se conservan.
- El suelo de cobertura de `vite.config.ts` no se baja.
- Textos de la interfaz y comentarios en español, como el resto del repo. Sin emojis.
- Permisos: cada pantalla nueva se protege con el permiso que exige su endpoint.

## Enfoques considerados

| Enfoque | A favor | En contra |
|---|---|---|
| Lienzo propio en SVG y CSS | Sin dependencias | Reinventa arrastre, zoom, trazado de conexiones y accesibilidad |
| **React Flow (`@xyflow/react`) y efectos en CSS** | Estándar, accesible por teclado, se carga solo donde se usa | Una dependencia nueva, unos 50 a 60 KB comprimidos |
| WebGL con three.js | 3D real | Lo más pesado, peor accesibilidad, riesgo para E2E; las referencias no lo usan |

Elegido por el dueño: **React Flow y CSS**. Las referencias (el lienzo de Railway y los
paneles de Resend) son 2D con profundidad, no WebGL.

## Diseño

### 1. Sistema visual

- **Solo tema oscuro.** Decisión mía: los brillos no funcionan en claro y mantener dos temas
  duplica el trabajo. Se cambia `defaultColorScheme="auto"` por oscuro forzado.
- Tokens en un archivo CSS propio (colores, degradados, sombras, radios, duraciones) y tema
  de Mantine alineado con ellos. Un color de acento con degradado de dos tonos; los colores de
  estado (éxito, fallo, en curso, incierto) se mantienen distinguibles y con contraste AA.
- Fondo de la aplicación: degradados radiales tenues y una retícula sutil, fijos.
- `GlowCard`: tarjeta con borde de degradado cónico que gira sin parar (`@property` para
  animar el ángulo) y un foco de luz que sigue al cursor mediante variables CSS. Un único
  manejador de `pointermove` por tarjeta, sin estado de React.
- Profundidad: sombras por capas y una inclinación 3D leve al pasar el cursor por las
  tarjetas de métricas. No se inclinan tablas ni formularios.
- Solo se animan `transform` y `opacity`. Con `prefers-reduced-motion` se detienen el giro,
  la inclinación y los pulsos; el foco del cursor se queda.
- Tipografía: una fuente variable autohospedada, o la pila del sistema si el peso no compensa.

Se aplica a: armazón (`AppLayout`), login, panel de métricas, listas, detalles y formularios.

### 2. Lienzo de flujos

Componente `FlowCanvas`, cargado con `lazy()` en su propio chunk.

- **Entrada:** la definición del flujo (pasos `http_request`, `transform`, `delay`,
  `condition`, con sus `next` y ramas) y, opcionalmente, los pasos de una ejecución.
- **Disposición:** automática por niveles desde el paso inicial, en una función pura y
  probada. Sin librería de layout; si los flujos crecen y se cruzan demasiado, se evalúa una.
- **Nodos:** uno por paso, con icono y color por tipo. Los disparadores del flujo (manual,
  cron, webhook entrante) aparecen como nodos de entrada.
- **Modo definición** (detalle de flujo y visor de versiones): al pasar el cursor por un nodo
  se ilumina el camino que llega a él y el que sale; clic abre su configuración.
- **Modo ejecución** (detalle de ejecución): cada nodo toma el color de su estado; las
  conexiones recorridas muestran un pulso que viaja; el paso en curso late; los reintentos
  se ven como contador y parpadeo; el paso fallido queda en rojo con su error al pasar el
  cursor; las ramas no tomadas se atenúan. Se alimenta de la consulta que ya refresca cada
  2 s mientras la ejecución está activa. No se añade SSE.
- **Controles:** arrastre, zoom, ajustar a la vista, minimapa. Nodos enfocables por teclado.
- La tabla de pasos actual se conserva debajo del lienzo: es la alternativa accesible y la
  que usan las pruebas E2E.
- En la creación de flujos, vista previa del lienzo cuando el JSON es válido.

### 3. Pantallas para funciones que ya existen

- **Disparadores** (pestaña en el detalle de flujo): listar, crear, pausar, borrar y rotar el
  secreto de los webhooks entrantes; se muestra la URL del disparador para copiar.
- **Webhooks salientes** (sección nueva en la navegación): suscripciones (crear, editar,
  borrar, rotar secreto), historial de entregas, botón de ping y reenvío de una entrega.
- **Versiones:** abrir cualquier versión de un flujo en solo lectura, con lienzo y JSON.
- **Credenciales:** editar metadatos, reemplazar el secreto y borrar, además de crear.

Los secretos se muestran una sola vez, al crearlos o rotarlos, como hace hoy la API.

### 4. Reejecutar

Botón en el detalle de una ejecución terminada. Llama a
`POST /api/v1/workflows/{id}/runs` con la misma `input` y la misma `version`, sin clave de
idempotencia, y navega a la ejecución nueva. Visible solo con permiso de ejecutar.

### 5. Rendimiento

- Antes de empezar se mide el peso de cada chunk y se anota. Al terminar se repite.
- El chunk de entrada y el del panel de métricas no crecen más de un 10 %. El lienzo y sus
  estilos viven en un chunk que solo piden las pantallas de flujos y ejecuciones.
- Resultados en `docs/performance.md`.

### 6. Código y documentación

- Comentarios solo donde el código no explica el porqué, en una línea.
- ADR-0013 con las decisiones del lienzo y del sistema visual.
- README: capturas y GIF regenerados con los scripts de Playwright existentes.

## Pruebas

- Unitarias (Vitest): la función de disposición, el mapeo de estados de ejecución a nodos y
  cada pantalla nueva con sus permisos.
- E2E (Playwright): las 8 actuales en verde, más una para reejecutar y otra para crear una
  suscripción de webhook y reenviar una entrega.
- `pnpm lint`, `pnpm typecheck`, `pnpm test`, `pnpm build` y el chequeo de contrato OpenAPI.

## Orden de construcción

1. Medición inicial, tokens, tema oscuro, `GlowCard`, armazón y login.
2. Lienzo en el detalle de flujo y en el detalle de ejecución.
3. Panel de métricas, listas y formularios con el sistema visual.
4. Pantallas faltantes: disparadores, webhooks salientes, versiones, credenciales.
5. Reejecutar.
6. Medición final, ADR, capturas, GIF y README.

Cada fase termina con lint, tipos, pruebas y build en verde.

## Lo que queda a criterio de quien construye

- Paleta exacta, fuente, forma de los nodos y curvas de animación, dentro de lo descrito.
- Estructura de archivos de los componentes nuevos, siguiendo `features/` y `components/`.
- Si la vista previa del lienzo en la creación de flujos complica el formulario, se omite y
  se anota.
