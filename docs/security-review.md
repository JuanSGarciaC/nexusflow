# Revisión de seguridad (OWASP API Security Top 10, 2023)

Revisión hecha sobre el código completo (fases 1–9). Cada control indica **dónde se
comprueba**: una afirmación sin prueba automatizada no cuenta como control.

| # | Riesgo | Controles | Evidencia |
|---|---|---|---|
| API1 | Autorización a nivel de objeto (BOLA) | Modelo de **un único espacio de trabajo**: los permisos son por rol, no por propietario. Todos los endpoints con id exigen el permiso del recurso. Un trigger desactivado o inexistente responde igual (404). | `test_authorization_matrix.py`, `test_webhooks.py` |
| API2 | Autenticación rota | Argon2id; error y tiempo idénticos con un email inexistente; JWT de 15 min con `alg` fijo y claims obligatorios; refresh opaco rotativo con detección de reutilización; límites por IP y por cuenta; logout revoca en el servidor | `test_auth_flows.py`, `test_auth_concurrency.py`, `test_auth_primitives.py`, E2E de sesión |
| API3 | Autorización a nivel de propiedad | Los 17 esquemas de entrada rechazan campos desconocidos (`extra="forbid"`): nadie puede enviar `role` al registrarse. Los secretos son de solo escritura y se redactan de logs, respuestas y auditoría. Las cabeceras de respuesta sensibles de APIs externas (`Set-Cookie`, `X-Api-Key`…) no se guardan. | `test_auth_primitives.py`, `test_credentials.py`, `test_engine_units.py` |
| API4 | Consumo de recursos sin restricción | Cuerpo ≤ 1 MiB (413 incluso en *chunked*); `limit` acotado en todos los listados; ventana de métricas ≤ 30 días; salida de paso ≤ 256 KiB; timeouts por paso y por ejecución. Límites de uso en login, registro, renovación de sesión, webhooks entrantes (por trigger) y **creación de ejecuciones (por usuario)**. | `test_app.py`, `test_runs_api.py`, `test_engine.py`, `test_observability.py` |
| API5 | Autorización a nivel de función | Permisos declarados por endpoint. **Prueba que recorre las 50 operaciones del contrato**: sin token, todas salvo 8 públicas declaradas responden 401, y ninguna escritura es posible como viewer. Comprobada con una mutación: quitar el permiso de un endpoint hace fallar la prueba. | `test_authorization_matrix.py` |
| API6 | Acceso sin restricción a flujos sensibles | Programar ejecuciones recurrentes exige `schedules:manage` (solo editor y admin); El primer administrador solo se crea por CLI; el registro público solo crea viewers y se puede desactivar (desactivado en producción); un admin no puede modificarse a sí mismo | `test_cli.py`, `test_auth_flows.py` |
| API7 | SSRF | El cliente HTTP del motor resuelve el DNS y bloquea IPs privadas, loopback, link-local y metadatos de la nube; vuelve a validar cada redirección; solo https en producción; cada credencial está ligada a sus hosts | `test_engine.py`, `test_credentials.py`, `test_webhooks.py` |
| API8 | Configuración insegura | La API no arranca en producción con secretos de ejemplo, CORS `*`, cookies sin `Secure` o salida HTTP a hosts privados. Cabeceras de seguridad, CSP sin scripts inline (comprobada sin violaciones), contenedores sin root, `/metrics` cerrado salvo con token, documentación desactivada en producción | `test_config.py`, `test_app.py`, CI (`id -u`, CSP), [deployment.md](deployment.md) |
| API9 | Inventario de la API | Contrato OpenAPI versionado. El CI falla si el código y el contrato, o el contrato y los tipos del frontend, no coinciden. Prefijo `/api/v1`. | CI: *Contrato OpenAPI al día*, *Tipos generados* |
| API10 | Consumo inseguro de APIs | Respuestas externas acotadas en tamaño y tiempo; errores clasificados (no enviada, rechazada, resultado desconocido); un resultado incierto en una acción no idempotente pasa a revisión manual en lugar de reintentarse | `test_engine.py`, ADR-0007 |

## Otros controles

- **Secretos.** Gitleaks revisa todo el historial en CI. La única excepción son los
  directorios de pruebas, que contienen secretos ficticios a propósito
  ([`.gitleaks.toml`](../.gitleaks.toml)). Ningún `.env` está en el historial (comprobado).
  - **Valores de ejemplo permitidos.** Además de esos directorios, la configuración admite
    solo tres *valores* (no archivos): `change-me-*` (placeholders de `.env.example`),
    `demo-clave-larga-2026` (contraseña de la demo) y `ci-only-password` (Postgres efímero
    del CI). Cualquier otro secreto, en cualquier ruta fuera de las de pruebas, se detecta.
  - **Guarda contra la erosión de la excepción.** El riesgo no es solo que alguien suba un
    secreto, sino que alguien «arregle» un falso positivo ensanchando la allowlist.
    [`scripts/check_gitleaks_policy.py`](../scripts/check_gitleaks_policy.py) corre en el
    CI y falla si (1) las rutas o valores permitidos difieren de la lista esperada, o
    (2) los *canarios* no se comportan: un token con formato real (generado al vuelo, sin
    literal en el repo) debe detectarse en `backend/src/` y `docs/`, y quedar ignorado
    solo dentro de `backend/tests/` y `frontend/src/test/`. Se ejecuta en modo git, como el
    CI, porque con `--no-git` las rutas llevan prefijo y la regla `^backend/tests/` no
    casaría. Trade-off: ampliar la excepción exige editar el script y este documento en el
    mismo PR, a propósito. Limitación: los canarios prueban el *comportamiento de la
    configuración*, no sustituyen al escaneo del historial real, que sigue haciéndolo
    gitleaks-action.
- **Cadena de suministro.** Lockfiles con hashes (`uv.lock`, `pnpm-lock.yaml`);
  `pip-audit` y `pnpm audit` en CI; GitHub Actions fijadas por SHA de commit.
- **IP del cliente.** Cada salto solo confía en `X-Forwarded-For` si viene del salto
  anterior, con una IP fija (Caddy → nginx → API). Una cabecera falsificada se ignora
  (verificado contra uvicorn).
- **Auditoría.** Solo inserción, en la misma transacción que el cambio, con IP y
  `request_id`.

## Limitaciones conocidas (aceptadas)

| Limitación | Impacto | Cómo se resolvería |
|---|---|---|
| Un solo espacio de trabajo: no hay permisos por flujo ni multi-tenant | Cualquier `viewer` ve todas las ejecuciones, incluidas sus entradas y salidas | Columna `workspace_id` en cada tabla, filtrada en los repositorios, y RLS de PostgreSQL como defensa en profundidad |
| No hay verificación de email ni recuperación de contraseña | El registro revela si un email existe (409), mitigado con el límite por IP | Servicio de correo y respuesta uniforme |
| Un access token sigue siendo válido hasta 15 min tras el logout | Mitigado: el estado y el rol del usuario se consultan en cada petición | Lista de revocación por `jti` en Redis |
| No hay MFA | Una contraseña robada basta para entrar | TOTP / WebAuthn |
| El límite de ventana fija permite ráfagas de hasta 2× en el borde de la ventana | Menor | Ventana deslizante o *token bucket* en Lua |
