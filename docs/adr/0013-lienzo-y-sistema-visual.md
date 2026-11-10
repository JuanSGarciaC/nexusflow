# ADR-0013: Lienzo de flujos y sistema visual del panel

- **Estado:** Aceptada e implementada
- **Fecha:** 2026-11-09

## Contexto

El panel era Mantine casi de fábrica: sin estilos propios, con los flujos visibles solo como
JSON y con varias funciones de la API sin pantalla (disparadores, webhooks salientes, visor
de versiones, edición de credenciales). Un motor de flujos que no dibuja sus flujos obliga a
leer JSON para entender qué pasó en una ejecución.

## Decisión

### Lienzo con React Flow, cargado bajo demanda

`@xyflow/react` dibuja cada flujo como nodos conectados, con arrastre y zoom. Es de solo
lectura: editar sigue siendo JSON con el validador del servidor. Vive en un chunk propio
(`FlowCanvas`, 59,5 kB gzip) que solo piden las pantallas de flujos y ejecuciones.

- **Por qué no un lienzo propio en SVG:** habría que reimplementar arrastre, zoom, trazado
  de conexiones y navegación por teclado.
- **Por qué no WebGL:** es lo más pesado, lo menos accesible y lo más frágil en las pruebas
  E2E, y un grafo de pasos no necesita tres dimensiones.
- **Por qué no un editor arrastrable:** es otro producto. El JSON con validación ya funciona
  y es la fuente de verdad de las versiones inmutables.

### Disposición propia, sin librería de layout

`layoutFlow` es una función pura: nivel = camino más largo desde el paso inicial. Acepta
borradores inválidos (destinos inexistentes, ciclos, JSON a medio escribir) porque el lienzo
acompaña al editor mientras se escribe. Si los flujos crecen hasta cruzar muchas conexiones,
se evalúa una librería (dagre, elk).

### El lienzo de una ejecución usa la versión que se ejecutó

Se pide `GET /workflows/{id}/versions/{version}` con la versión de la ejecución, no la
definición actual del flujo: una ejecución antigua debe dibujarse como era.

### Sin SSE

El detalle de ejecución ya consultaba cada 2 s mientras la ejecución estaba activa. El lienzo
se alimenta de esa misma consulta. Un canal de eventos añadiría un tipo de conexión nuevo al
backend para ahorrar, como mucho, dos segundos de retraso.

### Solo tema oscuro

Los brillos y degradados no funcionan sobre fondo claro y mantener dos temas duplica el
trabajo de diseño y de capturas. Se fuerza el esquema oscuro.

### Estilos: tokens y clases estáticas de Mantine

Los colores, sombras y duraciones están en `styles/tokens.css`. Tarjetas, tablas, modales e
inputs se estilizan una vez en `styles/app.css` sobre las clases `mantine-*`, en lugar de
envolver cada pantalla. `GlowCard` (borde de degradado que gira y luz que sigue al cursor) se
reserva para lo que debe destacar: cifras del panel, login y tablas principales.

- El foco del cursor se escribe en variables CSS, no en estado de React: no hay un render
  por cada movimiento.
- Todo lo que se mueve lleva `data-motion`; una sola regla lo detiene con
  `prefers-reduced-motion`, y una prueba E2E lo comprueba emulando esa preferencia (las
  unitarias corren sin CSS y no lo verían).
- Fuente: la pila del sistema. Una fuente autohospedada (la CSP no permite CDN) no
  compensaba su peso.

### Los secretos no pasan por la caché

Al crear o rotar un disparador o una suscripción, la API devuelve el secreto una sola vez.
La mutación lo entrega a estado local y no lo devuelve, para que no quede en la caché de
mutaciones de react-query. Hay una prueba que lo comprueba.

## Consecuencias

- Una dependencia nueva en el frontend (`@xyflow/react`, MIT).
- El chunk de entrada crece un 0,7 % y el del panel de métricas no crece
  ([mediciones](../performance.md)).
- Los estilos globales dependen de los nombres de clase estáticos de Mantine: una versión
  mayor de Mantine obliga a revisarlos.
- Los nodos del lienzo usan `data-run` y no `data-status`: ese atributo identifica la
  insignia de estado y las pruebas E2E lo buscan.
- El lienzo no sabe qué rama eligió una condición: marca como recorrida una conexión cuando
  su origen y su destino tienen registro. Si las dos ramas se reúnen, puede marcar también la
  no tomada. Corregirlo exige que la API informe la rama elegida.
- Toda la información del lienzo sigue disponible como texto: el acordeón de pasos y el JSON
  se conservan, y cada nodo tiene nombre accesible con su estado e intentos.
