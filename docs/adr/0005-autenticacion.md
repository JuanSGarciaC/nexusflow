# ADR-0005: Autenticación con JWT corto y refresh token opaco rotativo

- **Estado:** Aceptada e implementada (Fase 2)
- **Fecha:** 2026-10-27

## Contexto

La API la consumirá un panel web (SPA) y, más adelante, clientes máquina a máquina. Se
necesitan sesiones largas cómodas para el usuario sin que robar un token dé acceso
indefinido, y la autorización debe reflejar al instante los cambios de rol.

## Decisión

| Elemento | Decisión | Motivo |
|---|---|---|
| Hash de contraseñas | Argon2id (argon2-cffi, parámetros por defecto) con `needs_rehash` | Recomendación de OWASP; los parámetros se pueden endurecer sin forzar un cambio de contraseña |
| Access token | JWT HS256 de 15 min, solo con `sub` | Sin estado y rápido de validar; vida corta para limitar el daño de una filtración |
| Rol y estado | Se leen de la base de datos en cada petición | Un cambio de rol o una desactivación tienen efecto inmediato; cuesta una consulta por clave primaria |
| Refresh token | Opaco de 256 bits, guardado como SHA-256 | Revocable (vive en la BD); una filtración de la tabla no permite usar los tokens |
| Transporte del refresh | Cookie `HttpOnly; Secure; SameSite=Strict; Path=/api/v1/auth` | JavaScript no puede leerla (XSS); no viaja en peticiones de otros sitios (CSRF) |
| Rotación | Cada canje emite un token nuevo y marca el anterior como reemplazado | Si un token rotado reaparece, alguien tiene una copia: se revoca toda la familia (RFC 9700) |
| Concurrencia | `SELECT … FOR UPDATE` sobre el token, sin periodo de gracia | Dos canjes simultáneos: uno gana y el otro cuenta como reutilización. Es la opción estricta |
| Primer administrador | Comando `nexusflow create-admin` | Ningún endpoint permite a un anónimo obtener privilegios |

## Alternativas descartadas

- **Sesiones de servidor en Redis.** Válidas, pero se descartan para dejar la API lista
  para clientes no navegador sin dos mecanismos de autenticación distintos.
- **Access token en `localStorage`.** Cualquier XSS lo roba. El access token se guardará
  en memoria del frontend, y la sesión larga vive en la cookie `HttpOnly`.
- **JWT como refresh token.** No se puede revocar sin una lista negra, y entonces se pierde
  la ventaja de no tener estado.
- **Rol dentro del JWT.** Ahorra una consulta, pero un administrador degradado conservaría
  sus permisos hasta que expire el token.
- **Periodo de gracia en la rotación.** Evita cerrar la sesión si el frontend envía dos
  refresh casi a la vez, a costa de una ventana para un atacante. Se prefiere que el
  frontend serialice las renovaciones (Fase 8).

## Consecuencias

- Una consulta adicional por petición autenticada (búsqueda por clave primaria).
- El frontend debe renovar el access token con `/auth/refresh`, y hacerlo de uno en uno.
- Sin verificación de email (requiere un servicio de correo): el registro revela si un
  email existe (409), lo que se mitiga con el límite de uso por IP.
