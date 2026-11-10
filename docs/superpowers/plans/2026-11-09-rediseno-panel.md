# Rediseño del panel de NexusFlow: plan de implementación

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Dar al panel un sistema visual oscuro con profundidad, un lienzo de flujos animado con datos reales, pantallas para las funciones de la API que hoy no se ven y un botón de reejecutar.

**Architecture:** Todo ocurre en `frontend/`. Un archivo de tokens CSS y un componente `GlowCard` dan el aspecto; el lienzo vive en `src/features/canvas/` con dos funciones puras (disposición y estado de ejecución) y un componente `FlowCanvas` sobre React Flow, cargado con `lazy()`. Las pantallas nuevas siguen el patrón de `features/` con react-query y el cliente tipado `api`.

**Tech Stack:** React 19, TypeScript estricto, Mantine 9, react-query 5, openapi-fetch, `@xyflow/react` (única dependencia nueva), Vitest, Playwright.

**Spec:** `docs/superpowers/specs/2026-11-09-rediseno-panel-design.md`

## Global Constraints

- Todo gratuito y local: solo librerías de código abierto, sin servicios de pago ni claves de API externas.
- El backend no se toca salvo necesidad real; si se toca, se regenera `backend/openapi.json` y `pnpm gen:api`.
- CSP sin cambios: `script-src 'self'`, fuentes e imágenes autohospedadas, sin CDN.
- Solo tema oscuro. Textos y comentarios en español. Sin emojis.
- Solo se animan `transform` y `opacity`. Con `prefers-reduced-motion: reduce` se detienen giro, inclinación y pulsos.
- Toda información del lienzo existe también como texto o tabla. La tabla de pasos actual se conserva.
- Las 8 pruebas E2E actuales siguen en verde; no se cambian sus selectores por rol y texto.
- El suelo de cobertura de `frontend/vite.config.ts` no se baja.
- El chunk de entrada y el de `DashboardPage` no crecen más de un 10 % respecto a la medición inicial.
- Comentarios: solo el porqué que el código no muestra, en una línea.
- Rama de trabajo: `rediseno-panel`. Cada tarea termina con `pnpm lint && pnpm typecheck && pnpm test && pnpm build` en verde y un commit.
- Todos los comandos se ejecutan desde `frontend/`.

## Review Focus

1. **Borrador inválido en el lienzo:** un `next`, `on_true` u `on_false` que apunta a un paso inexistente, un ciclo, `start` inexistente o `steps` vacío. Se espera que el lienzo dibuje lo que pueda y nunca se cuelgue ni lance. Prueba en la tarea 4.
2. **Ejecución de una versión antigua:** el flujo cambió después de la ejecución. Se espera que el lienzo use la definición de `run.version`, no la actual. Prueba en la tarea 8.
3. **Secreto mostrado una sola vez:** al crear o rotar, cerrar el aviso lo pierde. Se espera un aviso que lo diga y que el secreto no quede en la caché de react-query. Prueba en las tareas 10 y 11.
4. **Rol sin permiso que fuerza la URL** de `/webhooks`. Se espera la misma pantalla de acceso denegado que el resto. Prueba en la tarea 11.
5. **Movimiento reducido:** con `prefers-reduced-motion` no debe haber giro, inclinación ni pulsos. Prueba en la tarea 2.

---

## Estructura de archivos

| Archivo | Responsabilidad |
|---|---|
| `src/styles/tokens.css` | Variables CSS: colores, degradados, sombras, duraciones |
| `src/styles/app.css` | Fondo de la aplicación, retícula, regla global de movimiento reducido |
| `src/components/GlowCard.tsx` + `.module.css` | Tarjeta con borde giratorio, foco del cursor e inclinación opcional |
| `src/features/canvas/layout.ts` | Función pura: definición → nodos con posición y conexiones |
| `src/features/canvas/runState.ts` | Función pura: pasos de ejecución → estado por nodo y conexiones recorridas |
| `src/features/canvas/FlowCanvas.tsx` + `.module.css` | Componente React Flow (export por defecto para `lazy`) |
| `src/features/canvas/StepNode.tsx` | Nodo personalizado por tipo de paso |
| `src/features/workflows/TriggersTab.tsx` | Webhooks entrantes de un flujo |
| `src/features/workflows/VersionViewer.tsx` | Versión en solo lectura con lienzo y JSON |
| `src/features/webhooks/WebhooksPage.tsx`, `DeliveriesDrawer.tsx` | Suscripciones salientes y sus entregas |
| `src/components/SecretOnce.tsx` | Modal que muestra un secreto una sola vez |

---

### Task 1: Medición inicial, tokens y tema oscuro

**Files:**
- Create: `src/styles/tokens.css`, `src/styles/app.css`
- Modify: `src/providers.tsx`, `src/main.tsx`, `../docs/performance.md`
- Test: `src/test/app.test.tsx`

**Interfaces:**
- Produces: variables CSS `--nf-bg`, `--nf-surface`, `--nf-border`, `--nf-accent-a`, `--nf-accent-b`, `--nf-glow`, `--nf-ok`, `--nf-fail`, `--nf-run`, `--nf-warn`, `--nf-dur-spin` (8s), `--nf-dur-pulse` (1.6s). El `theme` exportado de `providers.tsx` sigue llamándose `theme`.

- [ ] **Step 1:** Crear la rama `rediseno-panel`. Ejecutar `pnpm build` y anotar en `docs/performance.md`, bajo un apartado "Rediseño del panel: medición inicial", el tamaño gzip de cada chunk que imprime Vite (entrada, `DashboardPage`, `WorkflowDetailPage`, `RunDetailPage`).
- [ ] **Step 2: Prueba que falla** en `app.test.tsx`:

```tsx
test("el panel fuerza el tema oscuro", async () => {
  renderApp("/login");
  await screen.findByRole("button", { name: /entrar|iniciar/i });
  expect(document.documentElement).toHaveAttribute("data-mantine-color-scheme", "dark");
});
```

- [ ] **Step 3:** `pnpm test app` falla si el sistema de pruebas resuelve claro.
- [ ] **Step 4:** En `providers.tsx`, `forceColorScheme="dark"` en `MantineProvider`; `theme` con `primaryColor` de acento propio (tupla de 10 tonos derivada de `--nf-accent-a`), `defaultRadius: "md"` y la fuente elegida. En `main.tsx`, importar `tokens.css` y `app.css` después de los estilos de Mantine. `app.css`: fondo fijo con dos degradados radiales tenues y retícula de 32 px al 4 % de opacidad; bloque `@media (prefers-reduced-motion: reduce)` que pone `animation: none` y `transition: none` en `[data-motion]`.
- [ ] **Step 5:** Fuente: una variable autohospedada en `src/assets/fonts/` con `font-display: swap`, solo si su woff2 pesa menos de 60 KB; si no, pila del sistema. Anotar la decisión en el commit.
- [ ] **Step 6:** Verificación completa en verde. Commit `feat(ui): tokens, tema oscuro y medición inicial`.

### Task 2: GlowCard

**Files:**
- Create: `src/components/GlowCard.tsx`, `src/components/GlowCard.module.css`, `src/components/GlowCard.test.tsx`

**Interfaces:**
- Produces: `GlowCard(props: PaperProps & { children: ReactNode; tilt?: boolean; spin?: boolean })`. `spin` por defecto `true`, `tilt` por defecto `false`. El elemento raíz lleva `data-motion` y `data-testid="glow-card"`.

- [ ] **Step 1: Pruebas que fallan:**

```tsx
test("el foco sigue al cursor mediante variables CSS", () => {
  render(<GlowCard>contenido</GlowCard>);
  const card = screen.getByTestId("glow-card");
  fireEvent.pointerMove(card, { clientX: 40, clientY: 20 });
  expect(card.style.getPropertyValue("--mx")).toBe("40px");
  expect(card.style.getPropertyValue("--my")).toBe("20px");
});

test("sin tilt no se escribe la inclinación", () => {
  render(<GlowCard>x</GlowCard>);
  const card = screen.getByTestId("glow-card");
  fireEvent.pointerMove(card, { clientX: 40, clientY: 20 });
  expect(card.style.getPropertyValue("--rx")).toBe("");
});

test("lleva data-motion para que la regla de movimiento reducido lo detenga", () => {
  render(<GlowCard>x</GlowCard>);
  expect(screen.getByTestId("glow-card")).toHaveAttribute("data-motion");
});
```

- [ ] **Step 2:** `pnpm test GlowCard` falla: módulo inexistente.
- [ ] **Step 3:** Implementar. Coordenadas relativas al `getBoundingClientRect()` de la tarjeta, escritas con `style.setProperty` (sin estado de React). Con `tilt`, `--rx` y `--ry` entre -4deg y 4deg. CSS: borde con `conic-gradient(from var(--angle), ...)` animado mediante `@property --angle` durante `--nf-dur-spin`; foco con `radial-gradient(240px at var(--mx) var(--my), var(--nf-glow), transparent)` en un pseudo-elemento con `pointer-events: none`; `transform: perspective(900px) rotateX(var(--rx,0)) rotateY(var(--ry,0))`.
- [ ] **Step 4:** Pruebas en verde. Commit `feat(ui): GlowCard con borde giratorio y foco del cursor`.

### Task 3: Armazón y login

**Files:**
- Modify: `src/components/AppLayout.tsx`, `src/auth/LoginPage.tsx`, `src/components/PageHeader.tsx`
- Test: `src/test/app.test.tsx` (existente, sin cambios de selectores)

- [ ] **Step 1:** `AppLayout`: cabecera y barra lateral translúcidas con `backdrop-filter` y borde `--nf-border`; marca "NexusFlow" con texto en degradado; enlace activo con barra de acento y brillo. Se conservan `aria-label`, textos y el `Suspense`.
- [ ] **Step 2:** `LoginPage`: formulario dentro de un `GlowCard` centrado. Mismos `label` y nombre del botón.
- [ ] **Step 3:** `PageHeader`: título con jerarquía mayor y línea de degradado inferior.
- [ ] **Step 4:** Verificación completa en verde. Commit `feat(ui): armazón y login con el sistema visual`.

### Task 4: Disposición del lienzo (función pura)

**Files:**
- Create: `src/features/canvas/layout.ts`, `src/features/canvas/layout.test.ts`

**Interfaces:**
- Produces:

```ts
export type FlowDefinition = components["schemas"]["WorkflowDefinition-Output"];
export type EdgeKind = "next" | "true" | "false";
export interface LaidOutNode { id: string; type: string; label: string; level: number; x: number; y: number; reachable: boolean }
export interface FlowEdge { id: string; source: string; target: string; kind: EdgeKind }
export function layoutFlow(def: FlowDefinition): { nodes: LaidOutNode[]; edges: FlowEdge[] };
/** Ids de nodos y conexiones en cualquier camino que pasa por stepId. */
export function connectedPath(edges: FlowEdge[], stepId: string): { nodes: Set<string>; edges: Set<string> };
```

- Constantes: `COL_W = 280`, `ROW_H = 130`. `x = level * COL_W`, `y = índice dentro del nivel * ROW_H`. `id` de conexión: `` `${source}->${target}:${kind}` ``. `label` es `name ?? id`.

- [ ] **Step 1: Pruebas que fallan** (con un ayudante `def(start, steps)`):

```ts
test("cadena lineal: un nivel por paso", () => {
  const { nodes, edges } = layoutFlow(def("a", [t("a", "b"), t("b", "c"), t("c")]));
  expect(nodes.map((n) => [n.id, n.level])).toEqual([["a", 0], ["b", 1], ["c", 2]]);
  expect(edges.map((e) => e.id)).toEqual(["a->b:next", "b->c:next"]);
});

test("condición: dos ramas en el mismo nivel, con su tipo", () => {
  const { nodes, edges } = layoutFlow(def("c", [cond("c", "si", "no"), t("si"), t("no")]));
  expect(nodes.find((n) => n.id === "si")).toMatchObject({ level: 1, y: 0 });
  expect(nodes.find((n) => n.id === "no")).toMatchObject({ level: 1, y: 130 });
  expect(edges.map((e) => e.kind)).toEqual(["true", "false"]);
});

test("ramas que se reúnen: el nivel es el camino más largo", () => {
  const d = def("c", [cond("c", "x", "fin"), t("x", "fin"), t("fin")]);
  expect(layoutFlow(d).nodes.find((n) => n.id === "fin")?.level).toBe(2);
});

test("destino inexistente: se omite la conexión y no lanza", () => {
  const { nodes, edges } = layoutFlow(def("a", [t("a", "fantasma")]));
  expect(nodes).toHaveLength(1);
  expect(edges).toHaveLength(0);
});

test("ciclo en un borrador: termina y dibuja cada paso una vez", () => {
  const { nodes } = layoutFlow(def("a", [t("a", "b"), t("b", "a")]));
  expect(nodes.map((n) => n.id).sort()).toEqual(["a", "b"]);
});

test("paso inalcanzable: aparece marcado al final", () => {
  const { nodes } = layoutFlow(def("a", [t("a"), t("huerfano")]));
  expect(nodes.find((n) => n.id === "huerfano")).toMatchObject({ reachable: false, level: 1 });
});

test("sin pasos o con start inexistente: resultado vacío o todo inalcanzable, sin lanzar", () => {
  expect(layoutFlow(def("a", []))).toEqual({ nodes: [], edges: [] });
  expect(layoutFlow(def("nada", [t("a")])).nodes[0]).toMatchObject({ reachable: false });
});

test("connectedPath incluye ancestros y descendientes, no ramas hermanas", () => {
  const { edges } = layoutFlow(def("c", [cond("c", "si", "no"), t("si", "fin"), t("no"), t("fin")]));
  expect([...connectedPath(edges, "si").nodes].sort()).toEqual(["c", "fin", "si"]);
});
```

- [ ] **Step 2:** `pnpm test layout` falla: módulo inexistente.
- [ ] **Step 3:** Implementar. Niveles por relajación del camino más largo desde `start`, ignorando las conexiones de retorno detectadas en un recorrido en profundidad (así un ciclo no cuelga). Los inalcanzables van en el nivel `máximo + 1`.
- [ ] **Step 4:** Pruebas en verde. Commit `feat(canvas): disposición automática por niveles`.

### Task 5: Estado de ejecución sobre el lienzo (función pura)

**Files:**
- Create: `src/features/canvas/runState.ts`, `src/features/canvas/runState.test.ts`

**Interfaces:**
- Consumes: `FlowEdge` de la tarea 4.
- Produces:

```ts
export type StepView = components["schemas"]["StepView"];
export interface NodeRun { status: StepView["status"]; attempts: number; maxAttempts: number; error: string | null; durationMs: number | null }
/** Clave: step_id. Un paso sin registro no aparece: el lienzo lo atenúa. */
export function nodeRuns(steps: StepView[]): Map<string, NodeRun>;
/** Conexiones cuyo origen y destino tienen registro y el destino empezó. */
export function traversedEdges(edges: FlowEdge[], runs: Map<string, NodeRun>): Set<string>;
```

- [ ] **Step 1: Pruebas que fallan:** (a) `nodeRuns` copia `status`, `attempt_count` y `max_attempts`, y calcula `durationMs` de `started_at` y `finished_at` (null si falta alguno); (b) con una condición cuyo `on_true` tiene registro y `on_false` no, `traversedEdges` devuelve solo `c->si:true`; (c) un destino en `pending` no cuenta como recorrido; (d) lista vacía devuelve mapa y conjunto vacíos.
- [ ] **Step 2:** Falla por módulo inexistente. **Step 3:** Implementar. **Step 4:** En verde. Commit `feat(canvas): estado de ejecución por nodo y conexiones recorridas`.

### Task 6: Componente FlowCanvas

**Files:**
- Create: `src/features/canvas/FlowCanvas.tsx`, `StepNode.tsx`, `FlowCanvas.module.css`, `FlowCanvas.test.tsx`
- Modify: `package.json` (`pnpm add @xyflow/react`, versión exacta como el resto), `src/test/setup.ts` (simulación de `ResizeObserver` si jsdom no la trae)

**Interfaces:**
- Consumes: `layoutFlow`, `connectedPath`, `nodeRuns`, `traversedEdges`.
- Produces: export por defecto

```ts
export interface CanvasTrigger { id: string; label: string; kind: "manual" | "cron" | "webhook" }
export default function FlowCanvas(props: {
  definition: FlowDefinition;
  steps?: StepView[];            // presente = modo ejecución
  triggers?: CanvasTrigger[];    // nodos de entrada conectados a `start`
  onSelectStep?: (stepId: string) => void;
  height?: number;               // por defecto 420
}): JSX.Element;
```

- [ ] **Step 1: Pruebas que fallan:** (a) dibuja un nodo por paso, localizable por `getByRole("button", { name: /Paso fetch_order/ })`; (b) en modo ejecución el nombre accesible del nodo incluye el estado en español y los intentos, por ejemplo `"Paso cobro: fallido, 3 de 3 intentos"`; (c) pulsar Enter sobre un nodo llama a `onSelectStep` con su id; (d) una definición sin pasos muestra el texto "Este flujo aún no tiene pasos" y no lanza.
- [ ] **Step 2:** Fallan por módulo inexistente.
- [ ] **Step 3:** Implementar con `ReactFlow`, `Background`, `Controls` y `MiniMap`; `nodesDraggable={false}`, `nodesConnectable={false}`, `fitView`, `proOptions={{ hideAttribution: false }}`. `StepNode`: icono de Tabler y color por tipo (`http_request`, `transform`, `delay`, `condition`), `tabIndex={0}`, `role="button"`. Las conexiones `true` y `false` llevan etiqueta "sí" y "no". Al pasar el cursor o enfocar un nodo, los nodos y conexiones fuera de `connectedPath` bajan a opacidad 0,25. En modo ejecución: color de borde por estado con los tokens `--nf-ok`, `--nf-fail`, `--nf-run`, `--nf-warn`; conexiones recorridas con un trazo discontinuo que avanza (`stroke-dashoffset` animado durante `--nf-dur-pulse`); `running` y `retry_scheduled` con un halo que late; nodos sin registro al 35 % de opacidad; `Tooltip` de Mantine con estado, duración, intentos y error. Todo elemento animado lleva `data-motion`.
- [ ] **Step 4:** Pruebas en verde y `pnpm build`: confirmar en la salida que `@xyflow/react` cae en un chunk propio y no en el de entrada. Commit `feat(canvas): lienzo de flujos con modo ejecución`.

### Task 7: Lienzo en flujos, vista previa y visor de versiones

**Files:**
- Create: `src/features/workflows/VersionViewer.tsx`, `src/test/workflows-canvas.test.tsx`
- Modify: `src/features/workflows/WorkflowDetailPage.tsx`, `WorkflowCreatePage.tsx`

**Interfaces:**
- Consumes: `FlowCanvas` mediante `const FlowCanvas = lazy(() => import("../canvas/FlowCanvas"))`, dentro de `Suspense` con un `Skeleton` de la misma altura.
- Produces: `VersionViewer({ workflowId, version }: { workflowId: string; version: number })`, que consulta `GET /api/v1/workflows/{workflow_id}/versions/{version}` con clave `["workflow", workflowId, "version", version]`.

- [ ] **Step 1: Pruebas que fallan** con `installFakeApi`: (a) la pestaña "Definición" muestra el lienzo con los pasos del borrador y, debajo, el editor JSON de siempre; (b) en "Versiones", pulsar "Ver" en la versión 1 pide `GET .../versions/1` y muestra su lienzo y su JSON en solo lectura; (c) en la creación, con un JSON válido aparece la vista previa y con uno inválido se mantiene la última válida sin lanzar.
- [ ] **Step 2:** Fallan. **Step 3:** Implementar. Los disparadores del lienzo salen de las consultas ya existentes de programaciones (`kind: "cron"`) más un nodo "Manual"; los webhooks entrantes se añaden en la tarea 10. Si la vista previa complica `WorkflowCreatePage` más de unas 30 líneas, se omite y se anota en el commit, como permite el spec.
- [ ] **Step 4:** Verificación completa. Commit `feat(workflows): lienzo en definición, vista previa y visor de versiones`.

### Task 8: Lienzo en el detalle de ejecución y botón Reejecutar

**Files:**
- Modify: `src/features/runs/RunDetailPage.tsx`
- Create: `src/test/run-detail.test.tsx`

**Interfaces:**
- Consumes: `FlowCanvas`, y la clave de consulta `["workflow", workflowId, "version", version]` de la tarea 7.

- [ ] **Step 1: Pruebas que fallan:** (a) con una ejecución de `version: 1` y un flujo cuya versión actual es la 2, se pide `GET .../versions/1` y el lienzo muestra los pasos de la versión 1; (b) el botón "Reejecutar" solo aparece con estado `succeeded`, `failed`, `cancelled` o `timed_out` y permiso `workflows:execute`; (c) al pulsarlo se envía `POST /api/v1/workflows/{id}/runs` con cuerpo `{ input: <la de la ejecución>, version: <la de la ejecución> }`, sin `idempotency_key`, y se navega a `/runs/<id nuevo>`; (d) un 409 o 422 de la API se muestra con `ErrorAlert` y no navega.
- [ ] **Step 2:** Fallan. **Step 3:** Implementar. El lienzo va encima de la tabla de pasos, que no cambia. `onSelectStep` desplaza y resalta la fila del paso en la tabla. El lienzo recibe `steps` de la consulta existente, que ya refresca cada 2 s con la ejecución activa.
- [ ] **Step 4:** Verificación completa. Commit `feat(runs): lienzo animado de la ejecución y reejecutar`.

### Task 9: Sistema visual en panel, listas y formularios

**Files:**
- Modify: `src/features/dashboard/DashboardPage.tsx`, `src/features/runs/RunTable.tsx`, `src/features/workflows/WorkflowListPage.tsx`, `src/features/audit/AuditPage.tsx`, `src/features/users/UsersPage.tsx`, `src/components/StatusBadge.tsx`, `src/components/JsonView.tsx`, `src/components/ErrorAlert.tsx`

- [ ] **Step 1:** Panel de métricas: cada cifra en un `GlowCard tilt`; gráficos con relleno en degradado del acento; sin cambiar textos ni roles.
- [ ] **Step 2:** Tablas dentro de `GlowCard spin={false}` (sin giro ni inclinación), cabecera fija translúcida, fila resaltada al pasar el cursor.
- [ ] **Step 3:** `StatusBadge`: punto de color con brillo y, para `running`, un latido con `data-motion`. `JsonView`: fondo más profundo y fuente monoespaciada.
- [ ] **Step 4:** Verificación completa y `pnpm build`: el chunk de `DashboardPage` no supera en más de un 10 % la medición inicial. Commit `feat(ui): panel, listas y formularios con el sistema visual`.

### Task 10: Disparadores (webhooks entrantes)

**Files:**
- Create: `src/components/SecretOnce.tsx`, `src/features/workflows/TriggersTab.tsx`, `src/test/triggers.test.tsx`
- Modify: `src/features/workflows/WorkflowDetailPage.tsx`

**Interfaces:**
- Produces: `SecretOnce({ title, secret, onClose }: { title: string; secret: string | null; onClose: () => void })`, modal abierto cuando `secret` no es null, con el texto exacto "Copia este secreto ahora. No se volverá a mostrar." y un `CopyButton`. `TriggersTab({ workflowId }: { workflowId: string })`.
- Endpoints: `GET|POST /api/v1/workflows/{workflow_id}/triggers`, `PATCH|DELETE /api/v1/triggers/{trigger_id}`, `POST /api/v1/triggers/{trigger_id}/rotate-secret`.

- [ ] **Step 1: Pruebas que fallan:** (a) la pestaña "Disparadores" solo aparece con `webhooks:manage`; (b) lista nombre, URL completa (`window.location.origin + url_path`), límite por minuto y última recepción; (c) crear envía `{ name, rate_limit_per_minute }` y muestra el secreto de la respuesta en `SecretOnce`; (d) tras cerrar el modal, el secreto no está en ninguna consulta de `queryClient.getQueryCache()`; (e) rotar pide confirmación y muestra el secreto nuevo; (f) pausar envía `PATCH` con `{ is_active: false }`.
- [ ] **Step 2:** Fallan. **Step 3:** Implementar siguiendo `SchedulesTab.tsx`. El secreto vive solo en estado local del componente; las mutaciones invalidan la lista y no guardan su respuesta. Los disparadores activos se pasan al lienzo como `CanvasTrigger` de tipo `webhook`.
- [ ] **Step 4:** Verificación completa. Commit `feat(workflows): pestaña de disparadores con secreto de un solo uso`.

### Task 11: Webhooks salientes

**Files:**
- Create: `src/features/webhooks/WebhooksPage.tsx`, `DeliveriesDrawer.tsx`, `src/test/webhooks.test.tsx`
- Modify: `src/App.tsx` (ruta `/webhooks` con `RequirePermission permission="webhooks:manage"`), `src/components/AppLayout.tsx` (entrada "Webhooks" con `IconWebhook`, después de "Ejecuciones")

**Interfaces:**
- Consumes: `SecretOnce`, `GlowCard`.
- Endpoints: `GET|POST /api/v1/webhook-subscriptions`, `PATCH|DELETE .../{subscription_id}`, `GET .../{subscription_id}/deliveries`, `POST .../{subscription_id}/ping`, `POST .../{subscription_id}/rotate-secret`, `POST /api/v1/webhook-deliveries/{delivery_id}/redeliver`.

- [ ] **Step 1: Pruebas que fallan:** (a) un viewer que abre `/webhooks` ve la misma pantalla de acceso denegado que en `/users` y no hay entrada en la navegación; (b) crear una suscripción envía `{ name, url, events, workflow_id }` y muestra el secreto en `SecretOnce`; (c) "Entregas" abre el cajón con estado, intentos (`attempt_count` de `max_attempts`), último código y último error; (d) "Reenviar" solo está habilitado para entregas `dead` y llama a `redeliver`; (e) "Ping" muestra una notificación de éxito o el error de la API; (f) el cajón pagina con `limit` y `offset`.
- [ ] **Step 2:** Fallan. **Step 3:** Implementar. Estados de entrega con `StatusBadge` ampliado: `pending`, `delivering`, `retry_scheduled`, `delivered`, `dead`.
- [ ] **Step 4:** Verificación completa. Commit `feat(webhooks): suscripciones salientes, entregas, ping y reenvío`.

### Task 12: Credenciales: editar, reemplazar secreto y borrar

**Files:**
- Modify: `src/features/credentials/CredentialsPage.tsx`, `src/test/credentials.test.tsx`

- Endpoints: `PATCH /api/v1/credentials/{credential_id}` con `{ description, allowed_hosts }`, `PUT .../secret` con `{ secret }`, `DELETE .../{credential_id}`.

- [ ] **Step 1: Pruebas que fallan:** (a) "Editar" envía solo `description` y `allowed_hosts`; (b) "Reemplazar secreto" envía `PUT` con el secreto y el campo queda vacío al cerrar; (c) "Eliminar" pide confirmación escribiendo el nombre de la credencial y envía `DELETE`; (d) un 409 de la API se muestra con `ErrorAlert`.
- [ ] **Step 2:** Fallan. **Step 3:** Implementar con un menú de acciones por fila. **Step 4:** Verificación completa. Commit `feat(credentials): editar, reemplazar secreto y borrar`.

### Task 13: E2E, capturas, medición final y documentación

**Files:**
- Modify: `e2e/nexusflow.spec.ts`, `e2e/screenshots.spec.ts`, `e2e/demo.spec.ts`, `../README.md`, `../docs/performance.md`
- Create: `../docs/adr/0013-lienzo-y-sistema-visual.md`

- [ ] **Step 1:** Añadir dos pruebas E2E: `"operador reejecuta una ejecución fallida y llega a la ejecución nueva"` y `"editor crea una suscripción de webhook, hace ping y ve la entrega"`.
- [ ] **Step 2:** Levantar el stack (`docker compose up -d --build`, `seed-demo`) y ejecutar `pnpm e2e`: 10 de 10 en verde. Si Docker no está disponible, se dice en el informe y no se da por verificado.
- [ ] **Step 3:** Regenerar capturas con `CAPTURE_SCREENSHOTS=1 pnpm exec playwright test screenshots`, añadiendo capturas del lienzo en ejecución y de webhooks; regenerar el GIF con el guion de `docs/demo.md`.
- [ ] **Step 4:** `pnpm build` y anotar en `docs/performance.md` la medición final junto a la inicial, con el porcentaje de cambio por chunk.
- [ ] **Step 5:** ADR-0013: React Flow frente a SVG propio y WebGL, solo tema oscuro, disposición propia, sin SSE. README: tabla de capturas, lista de pruebas E2E y apartado del frontend al día.
- [ ] **Step 6:** Verificación completa. Commit `docs: ADR-0013, capturas y mediciones del rediseño`.
