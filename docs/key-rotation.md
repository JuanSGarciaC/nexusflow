# Política de rotación de claves

Qué se rota, cada cuánto, cómo y qué pasa si una clave se filtra. Los procedimientos de
comandos están en [deployment.md §6](deployment.md); aquí está la política y su porqué.

## Resumen

| Clave | Para qué sirve | Rotación programada | Rotación de emergencia | Interrupción |
|---|---|---|---|---|
| `NEXUSFLOW_CREDENTIALS_ENCRYPTION_KEYS` (Fernet) | Cifra los secretos de las credenciales en la BD | Cada 90 días | Al sospechar filtración o cuando salga alguien con acceso | Ninguna (MultiFernet) |
| `NEXUSFLOW_SECRET_KEY` (JWT HS256) | Firma los access tokens | Cada 180 días | Al sospechar filtración | Sin cortes visibles; los access tokens en vuelo caducan (≤ 15 min) |

Las cifras son una decisión del proyecto, no un requisito legal: el ciclo corto de Fernet
refleja que protege datos en reposo de terceros; el de JWT es más largo porque el daño
queda acotado por la vida del token (15 min) y el refresh vive en la BD.

## Claves Fernet (cifrado de credenciales)

**Cómo funciona.** La variable admite una lista `nueva,antigua`. Se cifra siempre con la
primera y se descifra con cualquiera (`MultiFernet`), así que se puede rotar sin parar el
servicio. `nexusflow rotate-credentials` recifra todo con la primera clave.

**Procedimiento programado**
1. Generar: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`.
2. Poner la nueva **delante** (`nueva,antigua`) y reiniciar API, worker y beat.
3. Ejecutar `nexusflow rotate-credentials` y comprobar que termina sin errores.
4. Quitar la antigua de la lista y reiniciar de nuevo.
5. Guardar la clave nueva en el gestor de secretos, **separada de las copias de la BD**.

**Si se filtra la clave.** Rotar la clave no basta: quien tenga la clave *y* una copia de la
BD ya pudo leer los secretos en claro. Además de los pasos anteriores hay que **cambiar los
secretos de origen** (tokens y API keys de los terceros) y volver a introducirlos. Es el
paso que más se olvida y el que de verdad cierra el incidente.

**Cuidado.** No quitar la clave antigua antes de `rotate-credentials`: lo cifrado con ella
quedaría ilegible. Las copias de seguridad antiguas solo se pueden descifrar con las claves
de su época, por eso las claves retiradas se conservan según la retención de las copias.

## Clave JWT (`NEXUSFLOW_SECRET_KEY`)

**Cómo funciona.** HS256 con una sola clave, sin identificador de clave (`kid`). El access
token vale 15 min; el refresh token es opaco, está en la BD y rota en cada uso (con
detección de reutilización). Por eso la clave JWT no protege las sesiones largas.

**Procedimiento programado**
1. Generar un valor aleatorio de ≥ 32 caracteres (`openssl rand -hex 32`).
2. Cambiar la variable y reiniciar la API.
3. Efecto: los access tokens en vuelo dejan de valer; el frontend los renueva solo con el
   refresh y el usuario no nota nada. No hay que invalidar nada más.

**Si se filtra la clave.** Con ella se pueden **forjar access tokens** de cualquier usuario
hasta que se cambie. Cambiarla de inmediato corta el acceso (el efecto es instantáneo,
no hay que esperar a que caduquen). Después revisar `audit_logs` en busca de actividad
anómala durante la ventana de exposición. Los refresh tokens no se ven afectados por esta
filtración, porque son aleatorios y no se derivan de la clave.

**Limitación conocida.** Sin `kid` no hay solapamiento de dos claves: el cambio es abrupto.
Es aceptable por el TTL de 15 min. Si el producto creciera, se añadiría `kid` y una lista
de claves de verificación, igual que ya se hace con Fernet.

## Qué no cubre esta política

- Gestor de secretos (Vault, SOPS, el del proveedor cloud): hoy las claves viven en `.env`
  del servidor. Es el siguiente paso natural.
- Recordatorio automático: la fecha de la última rotación no se registra en el sistema;
  hoy es una tarea operativa (calendario del responsable).
- Webhooks y credenciales de PostgreSQL/Redis: ver [deployment.md §6](deployment.md).
