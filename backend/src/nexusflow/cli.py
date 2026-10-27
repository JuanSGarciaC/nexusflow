"""Comandos de administración.

    nexusflow create-admin --email admin@empresa.com

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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="nexusflow", description="Administración de NexusFlow")
    sub = parser.add_subparsers(dest="command", required=True)
    admin = sub.add_parser("create-admin", help="Crea un usuario administrador")
    admin.add_argument("--email", required=True)
    args = parser.parse_args(argv)

    configure_logging("WARNING", json_output=False)
    if args.command == "create-admin":
        return asyncio.run(create_admin(args.email, _read_password()))
    return 1  # pragma: no cover - argparse ya valida el comando


if __name__ == "__main__":
    raise SystemExit(main())
