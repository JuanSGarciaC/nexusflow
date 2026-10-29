"""Endpoints de credenciales. Requieren `credentials:manage` (editor o admin)."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response, status

from nexusflow.auth.dependencies import ContextDep, SessionDep, limit_writes, require_permission
from nexusflow.auth.models import User
from nexusflow.auth.permissions import Permission
from nexusflow.integrations import service
from nexusflow.integrations.crypto import CredentialCipher
from nexusflow.integrations.models import Credential
from nexusflow.integrations.schemas import (
    CredentialCreate,
    CredentialSecretUpdate,
    CredentialUpdate,
    CredentialView,
)

router = APIRouter(
    prefix="/api/v1/credentials", tags=["credentials"], dependencies=[Depends(limit_writes)]
)
_PROBLEM: dict[str, object] = {"content": {"application/problem+json": {}}}
Manager = Annotated[User, Depends(require_permission(Permission.CREDENTIALS_MANAGE))]


def get_cipher(request: Request) -> CredentialCipher:
    cipher: CredentialCipher = request.app.state.cipher
    return cipher


CipherDep = Annotated[CredentialCipher, Depends(get_cipher)]


@router.post(
    "",
    response_model=CredentialView,
    status_code=status.HTTP_201_CREATED,
    summary="Crear una credencial (el secreto nunca se devuelve)",
    responses={409: {"description": "Nombre duplicado", **_PROBLEM}},
)
async def create(
    data: CredentialCreate,
    session: SessionDep,
    cipher: CipherDep,
    context: ContextDep,
    actor: Manager,
) -> Credential:
    return await service.create_credential(session, cipher, actor, data, context)


@router.get("", response_model=list[CredentialView], summary="Listar credenciales (metadatos)")
async def list_all(session: SessionDep, _: Manager) -> list[Credential]:
    return await service.list_credentials(session)


@router.get(
    "/{credential_id}", response_model=CredentialView, summary="Metadatos de una credencial"
)
async def get_one(credential_id: uuid.UUID, session: SessionDep, _: Manager) -> Credential:
    return await service.get_credential(session, credential_id)


@router.patch(
    "/{credential_id}",
    response_model=CredentialView,
    summary="Cambiar descripción u hosts permitidos",
)
async def update(
    credential_id: uuid.UUID,
    data: CredentialUpdate,
    session: SessionDep,
    context: ContextDep,
    actor: Manager,
) -> Credential:
    return await service.update_credential(session, actor, credential_id, data, context)


@router.put(
    "/{credential_id}/secret",
    response_model=CredentialView,
    summary="Reemplazar el secreto (rotación del valor)",
)
async def replace_secret(
    credential_id: uuid.UUID,
    data: CredentialSecretUpdate,
    session: SessionDep,
    cipher: CipherDep,
    context: ContextDep,
    actor: Manager,
) -> Credential:
    return await service.replace_secret(session, cipher, actor, credential_id, data, context)


@router.delete(
    "/{credential_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Eliminar una credencial que no esté en uso",
    responses={409: {"description": "En uso por algún flujo", **_PROBLEM}},
)
async def delete(
    credential_id: uuid.UUID, session: SessionDep, context: ContextDep, actor: Manager
) -> Response:
    await service.delete_credential(session, actor, credential_id, context)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
