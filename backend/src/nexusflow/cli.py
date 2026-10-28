"""Comandos de administración.

    nexusflow create-admin --email admin@empresa.com
    nexusflow generate-key           # clave Fernet para NEXUSFLOW_CREDENTIALS_ENCRYPTION_KEYS
    nexusflow rotate-credentials     # vuelve a cifrar todo con la primera clave
    nexusflow export-openapi -o openapi.json   # contrato de la API para el frontend

El registro público solo crea usuarios `viewer`. El primer administrador se crea con este
comando, que requiere acceso al servidor: no existe ningún endpoint que permita a un
usuario anónimo convertirse en administrador.

La contraseña se lee de NEXUSFLOW_ADMIN_PASSWORD (útil en automatización) o se pide por
teclado sin eco. Nunca se acepta como argumento, porque quedaría en el historial de la
shell y en la lista de procesos.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import os
import sys

from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError

from nexusflow.audit.service import AuditAction, RequestContext, record
from nexusflow.auth.models import User
from nexusflow.auth.passwords import hash_password
from nexusflow.auth.permissions import Role
from nexusflow.auth.schemas import RegisterRequest
from nexusflow.core.config import get_settings
from nexusflow.core.logging import configure_logging
from nexusflow.db.session import create_engine, create_session_factory
from nexusflow.integrations.crypto import CredentialCipher, generate_key
from nexusflow.integrations.service import rotate_all


def _read_password() -> str:
    from_env = os.environ.get("NEXUSFLOW_ADMIN_PASSWORD")
    if from_env:
        return from_env
    first = getpass.getpass("Contraseña: ")
    second = getpass.getpass("Repite la contraseña: ")
    if first != second:
        raise SystemExit("Las contraseñas no coinciden.")
    return first


async def create_admin(email: str, password: str) -> int:
    try:
        data = RegisterRequest.model_validate({"email": email, "password": password})
    except ValidationError as exc:
        for error in exc.errors():
            print(f"Error: {str(error['msg']).removeprefix('Value error, ')}", file=sys.stderr)
        return 2

    settings = get_settings()
    engine = create_engine(settings)
    try:
        async with create_session_factory(engine)() as session:
            user = User(
                email=data.email,
                password_hash=hash_password(data.password.get_secret_value()),
                role=Role.ADMIN,
            )
            session.add(user)
            try:
                await session.flush()
            except IntegrityError:
                print(f"Error: ya existe un usuario con el email {data.email}.", file=sys.stderr)
                return 1
            record(
                session,
                action=AuditAction.USER_CREATED_BY_CLI,
                context=RequestContext(),
                actor_user_id=user.id,
                resource_type="user",
                resource_id=str(user.id),
                details={"role": Role.ADMIN.value},
            )
            await session.commit()
            print(f"Administrador creado: {data.email} (id {user.id})")
            return 0
    finally:
        await engine.dispose()


async def rotate_credentials() -> int:
    settings = get_settings()
    cipher = CredentialCipher(settings.credentials_encryption_keys.get_secret_value())
    engine = create_engine(settings)
    try:
        async with create_session_factory(engine)() as session:
            count = await rotate_all(session, cipher)
    finally:
        await engine.dispose()
    print(f"Credenciales recifradas con la clave {cipher.primary_fingerprint}: {count}")
    return 0


def export_openapi(path: str) -> int:
    """Escribe el contrato OpenAPI sin conectarse a nada.

    La configuración usa valores de relleno: generar el esquema no abre conexiones ni
    ejecuta el ciclo de vida de la app. La salida es determinista (claves ordenadas), así el
    CI puede detectar con un simple diff si el contrato versionado quedó desactualizado.
    """
    import json
    from pathlib import Path

    from nexusflow.core.config import Settings
    from nexusflow.main import create_app

    placeholder = Settings(
        _env_file=None,
        env="development",
        database_url="postgresql+asyncpg://openapi:openapi@localhost:5432/openapi",
        redis_url="redis://localhost:6379/0",
        secret_key="openapi-export-" + "x" * 32,
        credentials_encryption_keys=generate_key(),
        docs_enabled=True,
    )
    schema = create_app(placeholder).openapi()
    Path(path).write_text(
        json.dumps(schema, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"OpenAPI escrito en {path} ({len(schema['paths'])} rutas)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="nexusflow", description="Administración de NexusFlow")
    sub = parser.add_subparsers(dest="command", required=True)
    admin = sub.add_parser("create-admin", help="Crea un usuario administrador")
    admin.add_argument("--email", required=True)
    sub.add_parser("generate-key", help="Genera una clave Fernet para cifrar credenciales")
    sub.add_parser("rotate-credentials", help="Recifra las credenciales con la clave principal")
    export = sub.add_parser("export-openapi", help="Escribe el esquema OpenAPI en un archivo")
    export.add_argument("-o", "--output", default="openapi.json")
    args = parser.parse_args(argv)

    if args.command == "generate-key":
        print(generate_key())  # no necesita configuración ni base de datos
        return 0

    configure_logging("WARNING", json_output=False)
    if args.command == "export-openapi":
        return export_openapi(args.output)
    if args.command == "create-admin":
        return asyncio.run(create_admin(args.email, _read_password()))
    if args.command == "rotate-credentials":
        return asyncio.run(rotate_credentials())
    return 1  # pragma: no cover - argparse ya valida el comando


if __name__ == "__main__":
    raise SystemExit(main())
