# ADR-0011: Panel web en el mismo origen, con tipos generados desde OpenAPI

- **Estado:** Aceptada e implementada (Fase 8)
- **Fecha:** 2026-10-28

## Contexto

El panel debe crear, editar, publicar y ejecutar flujos, y mostrar historial, errores y
métricas. Los roles de la API deben respetarse sin duplicar la lógica, y la sesión debe
resistir XSS y CSRF.

## Decisiones

| Tema | Decisión | Motivo |
|---|---|---|
| Stack | Vite + React 19 + TypeScript `strict` + Mantine 9 + TanStack Query + React Router | Mantine: biblioteca mantenida, accesible y con formularios, tablas y gráficos incluidos. TanStack Query separa el estado del servidor (caché, reintentos, sondeo) del de la interfaz. |
| Contrato | `backend/openapi.json` se genera con `nexusflow export-openapi`; los tipos con `openapi-typescript`; las llamadas con `openapi-fetch` | Rutas, parámetros y cuerpos se comprueban al compilar. El CI regenera ambos y falla si difieren. |
| Origen | nginx sirve la SPA y reenvía `/api` a la API; en desarrollo lo hace el proxy de Vite | Sin CORS. La cookie de refresh puede ser `SameSite=Strict` y restringida a `/api/v1/auth`. |
| Sesión | Access token solo en memoria; sesión larga en la cookie `HttpOnly`; renovaciones **serializadas** | Un XSS no puede leer tokens persistentes. Dos renovaciones simultáneas con el mismo refresh token se tratarían como robo (ADR-0005), así que se comparte una sola petición en vuelo. |
| Autorización en la interfaz | El menú y las acciones se ocultan según `permissions` de `/users/me` | Solo es experiencia de usuario: la API aplica los permisos en cada endpoint. |
| CSP | `script-src 'self'`; `style-src 'self' 'unsafe-inline'` | Sin scripts inline. Los estilos inline son necesarios para las variables CSS de Mantine; se comprobó que una CSP sin ellos rompe el layout. Swagger UI tiene una CSP propia, limitada a `/docs`. |
| Editor de flujos | JSON con validación en el servidor (`/workflows/validate`) e incidencias junto al editor | Un editor visual de arrastrar y soltar añade mucha complejidad y poco valor para demostrar el sistema; el DSL ya es legible. |
| Conflictos de edición | Se envía `expected_revision`; un 409 muestra un aviso y la opción de recargar | Nunca se sobrescribe en silencio el trabajo de otra persona. |
| Rendimiento | Rutas cargadas bajo demanda (`React.lazy`) | El bundle inicial baja de 1,1 MB a 466 kB; la librería de gráficos (417 kB) solo se descarga en el panel. |

## Versiones

Se fijan versiones exactas (`save-exact`) con `pnpm-lock.yaml`. **TypeScript se mantiene
en 5.9.3** aunque ya existe la 7.0: `typescript-eslint` exige `< 6.1` y `openapi-typescript`
exige `5.x`. Subir cuando el resto de herramientas lo admita.

## Consecuencias

- Al cambiar la API hay que regenerar el contrato y los tipos (dos comandos; el CI lo recuerda).
- Las páginas se actualizan por sondeo (ejecuciones en curso cada 2 s, listados cada 5 s).
  WebSockets o SSE solo merecerían la pena con muchos usuarios mirando ejecuciones a la vez.
- `IP del cliente`: la API solo acepta `X-Forwarded-For` de la IP fija del contenedor web
  (`FORWARDED_ALLOW_IPS`). Una cabecera falsificada se ignora (verificado).
