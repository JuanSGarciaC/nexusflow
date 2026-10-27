# ADR-0008: Credenciales de integraciones cifradas y vinculadas a hosts

- **Estado:** Aceptada e implementada (Fase 5)
- **Fecha:** 2026-10-27

## Contexto

Los flujos llaman a APIs de terceros con tokens, API keys o usuario y contraseña.
Requisitos:
- los editores usan las credenciales, pero no deben poder verlas;
- una copia de la base de datos no debe exponerlas;
- un editor no debe poder exfiltrarlas cambiando la URL de un paso;
- las claves de cifrado tienen que poder rotarse.

## Decisión

| Aspecto | Decisión |
|---|---|
| Cifrado | Fernet (AES-128-CBC + HMAC-SHA256, autenticado) con `MultiFernet` para rotar claves |
| Clave | `NEXUSFLOW_CREDENTIALS_ENCRYPTION_KEYS`, fuera de la base de datos; la app no arranca si falta o no es válida |
| Vinculación | El id y el nombre de la credencial van dentro del texto cifrado; mover un cifrado a otra fila hace fallar el descifrado |
| API | Solo escritura: ninguna respuesta contiene el secreto. Se muestra una pista (`…a1b2`) |
| Uso | El secreto solo se descifra en el worker, justo antes de la llamada HTTP |
| Hosts permitidos | Cada credencial declara a qué hosts puede enviarse; si el paso apunta a otro, falla sin enviar nada |
| Cabeceras | Las de la credencial se aplican al final, sin distinguir mayúsculas: el flujo no puede sobrescribirlas |
| Redacción | Si la API externa devuelve el secreto (un eco), se sustituye por `[REDACTED]` antes de guardar la salida |
| Borrado | Se rechaza si algún flujo activo la usa (consulta `jsonb_path_exists` sobre las definiciones) |
| Adaptadores | Patrón Strategy por tipo (`bearer_token`, `api_key_header`, `basic_auth`): cada uno valida su secreto y genera sus cabeceras |
| Publicación | Un flujo que referencia una credencial inexistente no se publica (`unknown_credential`) |

Rotación de la clave de cifrado:
1. Añadir la clave nueva al **principio** de `NEXUSFLOW_CREDENTIALS_ENCRYPTION_KEYS`.
2. Ejecutar `nexusflow rotate-credentials`. Todas las filas se recifran con la clave
   nueva, y `key_fingerprint` lo refleja.
3. Retirar la clave antigua.

## Alternativas descartadas

- **Gestor de secretos externo** (Vault, AWS Secrets Manager). Es la opción correcta a
  escala, pero añade infraestructura que este proyecto no necesita. La interfaz
  `CredentialResolver` permite sustituir la implementación sin tocar el motor.
- **pgcrypto en la base de datos.** La clave viajaría en las consultas y quedaría en los
  logs de PostgreSQL.
- **Devolver el secreto al editor.** Ningún flujo lo necesita, y amplía la superficie de
  fuga.

## Consecuencias

- Perder la clave Fernet hace irrecuperables las credenciales; hay que guardarla como
  cualquier secreto de infraestructura.
- La redacción de salidas es una defensa en profundidad: solo cubre coincidencias exactas
  del secreto.
