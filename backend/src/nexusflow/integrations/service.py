"""Casos de uso de credenciales y resolución en el worker."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from nexusflow.audit.service import RequestContext, record
from nexusflow.auth.models import User
from nexusflow.core.errors import AppError, ConflictError, NotFoundError
from nexusflow.core.logging import get_logger
from nexusflow.db.types import utcnow
from nexusflow.engine.executors import ResolvedCredential, StepError
from nexusflow.engine.http_client import FailureKind
from nexusflow.integrations.adapters import ADAPTERS, CredentialType, parse_secret
from nexusflow.integrations.crypto import CredentialCipher, CredentialDecryptionError
from nexusflow.integrations.models import Credential
from nexusflow.integrations.schemas import (
    CredentialCreate,
    CredentialSecretUpdate,
    CredentialUpdate,
)

logger = get_logger(__name__)


class CredentialAction:
    CREATED = "credential.created"
    UPDATED = "credential.updated"
    SECRET_REPLACED = "credential.secret_replaced"  # noqa: S105  # nombre de acción
    DELETED = "credential.deleted"


class InvalidSecretError(AppError):
    status_code = 422
    code = "invalid-secret"
    title = "El secreto no tiene el formato del tipo de credencial"

    def __init__(self, exc: ValidationError) -> None:
        # Solo la ubicación y el mensaje: nunca el valor recibido.
        errors = [{"loc": list(e["loc"]), "msg": e["msg"]} for e in exc.errors()]
        super().__init__(extra={"errors": errors})


def _validated_secret(credential_type: CredentialType, data: dict[str, Any]) -> dict[str, Any]:
    try:
        return parse_secret(credential_type, data)
    except ValidationError as exc:
        raise InvalidSecretError(exc) from exc


async def get_credential(session: AsyncSession, credential_id: uuid.UUID) -> Credential:
    found = await session.get(Credential, credential_id)
    if found is None:
        raise NotFoundError("La credencial no existe.")
    return found


async def list_credentials(session: AsyncSession) -> list[Credential]:
    return list(await session.scalars(select(Credential).order_by(Credential.name)))


async def existing_names(session: AsyncSession, names: set[str]) -> set[str]:
    if not names:
        return set()
    rows = await session.scalars(select(Credential.name).where(Credential.name.in_(names)))
    return set(rows)


async def create_credential(
    session: AsyncSession,
    cipher: CredentialCipher,
    actor: User,
    data: CredentialCreate,
    context: RequestContext,
) -> Credential:
    secret = _validated_secret(data.type, data.secret)
    credential_id = uuid.uuid4()
    credential = Credential(
        id=credential_id,
        name=data.name,
        type=data.type,
        description=data.description,
        allowed_hosts=sorted(set(data.allowed_hosts)),
        secret_encrypted=cipher.encrypt(credential_id, data.name, secret),
        key_fingerprint=cipher.primary_fingerprint,
        hint=ADAPTERS[data.type].hint(secret),
        created_by=actor.id,
        secret_updated_at=utcnow(),
    )
    session.add(credential)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise ConflictError("Ya existe una credencial con ese nombre.") from exc
    record(
        session,
        action=CredentialAction.CREATED,
        context=context,
        actor_user_id=actor.id,
        resource_type="credential",
        resource_id=str(credential_id),
        details={
            "name": data.name,
            "type": data.type.value,
            "allowed_hosts": credential.allowed_hosts,
        },
    )
    await session.commit()
    return credential


async def update_credential(
    session: AsyncSession,
    actor: User,
    credential_id: uuid.UUID,
    data: CredentialUpdate,
    context: RequestContext,
) -> Credential:
    credential = await session.get(Credential, credential_id, with_for_update=True)
    if credential is None:
        raise NotFoundError("La credencial no existe.")
    changes: dict[str, Any] = {}
    if "description" in data.model_fields_set:
        credential.description = data.description
        changes["description"] = True
    if data.allowed_hosts is not None:
        credential.allowed_hosts = sorted(set(data.allowed_hosts))
        changes["allowed_hosts"] = credential.allowed_hosts
    record(
        session,
        action=CredentialAction.UPDATED,
        context=context,
        actor_user_id=actor.id,
        resource_type="credential",
        resource_id=str(credential_id),
        details=changes,
    )
    await session.commit()
    await session.refresh(credential)
    return credential


async def replace_secret(
    session: AsyncSession,
    cipher: CredentialCipher,
    actor: User,
    credential_id: uuid.UUID,
    data: CredentialSecretUpdate,
    context: RequestContext,
) -> Credential:
    credential = await session.get(Credential, credential_id, with_for_update=True)
    if credential is None:
        raise NotFoundError("La credencial no existe.")
    secret = _validated_secret(credential.type, data.secret)
    credential.secret_encrypted = cipher.encrypt(credential.id, credential.name, secret)
    credential.key_fingerprint = cipher.primary_fingerprint
    credential.hint = ADAPTERS[credential.type].hint(secret)
    credential.secret_updated_at = utcnow()
    record(
        session,
        action=CredentialAction.SECRET_REPLACED,
        context=context,
        actor_user_id=actor.id,
        resource_type="credential",
        resource_id=str(credential_id),
    )
    await session.commit()
    await session.refresh(credential)
    return credential


async def referencing_workflows(session: AsyncSession, name: str) -> list[str]:
    """Flujos activos cuya última versión publicada o borrador usa la credencial."""
    rows = await session.execute(
        text(
            """
            SELECT DISTINCT w.name
            FROM workflows w
            JOIN workflow_versions v ON v.workflow_id = w.id
            WHERE w.status = 'active'
              AND (v.published_at IS NULL OR v.version = (
                    SELECT max(v2.version) FROM workflow_versions v2
                    WHERE v2.workflow_id = w.id AND v2.published_at IS NOT NULL))
              AND jsonb_path_exists(
                    v.definition,
                    '$.steps[*].config.credential ? (@ == $name)',
                    jsonb_build_object('name', CAST(:name AS text)))
            ORDER BY w.name
            """
        ),
        {"name": name},
    )
    return [row[0] for row in rows]


async def delete_credential(
    session: AsyncSession, actor: User, credential_id: uuid.UUID, context: RequestContext
) -> None:
    credential = await session.get(Credential, credential_id, with_for_update=True)
    if credential is None:
        raise NotFoundError("La credencial no existe.")
    users = await referencing_workflows(session, credential.name)
    if users:
        raise ConflictError(
            "La credencial está en uso; quítala de estos flujos o archívalos antes.",
            extra={"workflows": users},
        )
    await session.delete(credential)
    record(
        session,
        action=CredentialAction.DELETED,
        context=context,
        actor_user_id=actor.id,
        resource_type="credential",
        resource_id=str(credential_id),
        details={"name": credential.name},
    )
    await session.commit()


async def rotate_all(session: AsyncSession, cipher: CredentialCipher) -> int:
    """Vuelve a cifrar todos los secretos con la clave principal (rotación de claves)."""
    rows = list(await session.scalars(select(Credential).with_for_update()))
    for credential in rows:
        credential.secret_encrypted = cipher.rotate(credential.secret_encrypted)
        credential.key_fingerprint = cipher.primary_fingerprint
    await session.commit()
    return len(rows)


# ------------------------------------------------------------------ uso en el worker


@dataclass
class DbCredentialResolver:
    sessions: async_sessionmaker[AsyncSession]
    cipher: CredentialCipher

    async def __call__(self, name: str, host: str) -> ResolvedCredential:
        async with self.sessions() as session:
            credential = await session.scalar(select(Credential).where(Credential.name == name))
            if credential is None:
                raise StepError(
                    FailureKind.PERMANENT,
                    "credential_not_found",
                    f"No existe la credencial '{name}'",
                )
            if host.lower() not in credential.allowed_hosts:
                logger.warning("credential_host_rejected", credential=name, host=host)
                raise StepError(
                    FailureKind.PERMANENT,
                    "credential_host_not_allowed",
                    f"La credencial '{name}' no puede enviarse a {host}",
                )
            try:
                secret = self.cipher.decrypt(
                    credential.id, credential.name, credential.secret_encrypted
                )
            except CredentialDecryptionError as exc:
                logger.error("credential_decrypt_failed", credential=name)
                raise StepError(
                    FailureKind.PERMANENT,
                    "credential_unreadable",
                    "No se pudo descifrar la credencial",
                ) from exc
            await session.execute(
                update(Credential)
                .where(Credential.id == credential.id)
                .values(last_used_at=utcnow())
            )
            await session.commit()
        adapter = ADAPTERS[credential.type]
        return ResolvedCredential(
            headers=adapter.headers(secret), secret_values=adapter.secret_values(secret)
        )
