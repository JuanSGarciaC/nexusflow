"""Endpoints de flujos de trabajo."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, status

from nexusflow.auth.dependencies import ContextDep, SessionDep, require_permission
from nexusflow.auth.models import User
from nexusflow.auth.permissions import Permission
from nexusflow.workflows import service
from nexusflow.workflows.models import Workflow, WorkflowStatus, WorkflowVersion
from nexusflow.workflows.schemas import (
    DraftSave,
    PublishRequest,
    ValidateRequest,
    ValidateResponse,
    ValidationIssue,
    VersionDetail,
    VersionSummary,
    WorkflowCreate,
    WorkflowDetail,
    WorkflowPage,
    WorkflowSummary,
    WorkflowUpdate,
)
from nexusflow.workflows.validation import validate_definition

router = APIRouter(prefix="/api/v1/workflows", tags=["workflows"])

_PROBLEM: dict[str, object] = {"content": {"application/problem+json": {}}}
Reader = Annotated[User, Depends(require_permission(Permission.WORKFLOWS_READ))]
Writer = Annotated[User, Depends(require_permission(Permission.WORKFLOWS_WRITE))]


def _summary(version: WorkflowVersion) -> VersionSummary:
    return VersionSummary(
        id=version.id,
        version=version.version,
        state="published" if version.is_published else "draft",
        revision=version.revision,
        definition_hash=version.definition_hash,
        created_at=version.created_at,
        updated_at=version.updated_at,
        published_at=version.published_at,
        created_by=version.created_by,
        published_by=version.published_by,
    )


def _detail(version: WorkflowVersion) -> VersionDetail:
    return VersionDetail(
        **_summary(version).model_dump(),
        definition=service.parse_stored(version.definition),
        issues=[ValidationIssue(**i.as_dict()) for i in service.issues_for(version)],
    )


def _workflow_summary(
    workflow: Workflow, published: int | None, draft: int | None
) -> WorkflowSummary:
    return WorkflowSummary(
        id=workflow.id,
        name=workflow.name,
        description=workflow.description,
        status=workflow.status,
        created_at=workflow.created_at,
        updated_at=workflow.updated_at,
        published_version=published,
        draft_version=draft,
    )


def _workflow_detail(state: service.WorkflowState) -> WorkflowDetail:
    base = _workflow_summary(
        state.workflow,
        state.published.version if state.published else None,
        state.draft.version if state.draft else None,
    )
    return WorkflowDetail(
        **base.model_dump(),
        created_by=state.workflow.created_by,
        draft=_detail(state.draft) if state.draft else None,
        published=_detail(state.published) if state.published else None,
    )


@router.post(
    "/validate",
    response_model=ValidateResponse,
    summary="Validar una definición sin guardarla",
)
async def validate(data: ValidateRequest, _: Reader) -> ValidateResponse:
    issues = validate_definition(data.definition)
    return ValidateResponse(
        valid=not issues, issues=[ValidationIssue(**i.as_dict()) for i in issues]
    )


@router.post(
    "",
    response_model=WorkflowDetail,
    status_code=status.HTTP_201_CREATED,
    summary="Crear un flujo (con su primer borrador)",
    responses={409: {"description": "Nombre duplicado", **_PROBLEM}},
)
async def create(
    data: WorkflowCreate, session: SessionDep, context: ContextDep, actor: Writer
) -> WorkflowDetail:
    state = await service.create_workflow(session, actor, data, context)
    return _workflow_detail(await service.get_state(session, state.workflow.id))


@router.get("", response_model=WorkflowPage, summary="Listar flujos")
async def list_workflows(
    session: SessionDep,
    _: Reader,
    status_filter: Annotated[WorkflowStatus | None, Query(alias="status")] = None,
    q: Annotated[str | None, Query(max_length=100)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0, le=100_000)] = 0,
) -> WorkflowPage:
    rows, total = await service.list_workflows(
        session, status=status_filter, search=q, limit=limit, offset=offset
    )
    return WorkflowPage(
        items=[_workflow_summary(w, p, d) for w, p, d in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/{workflow_id}",
    response_model=WorkflowDetail,
    summary="Detalle del flujo, borrador y última versión publicada",
    responses={404: {"description": "No existe", **_PROBLEM}},
)
async def get_workflow(workflow_id: uuid.UUID, session: SessionDep, _: Reader) -> WorkflowDetail:
    return _workflow_detail(await service.get_state(session, workflow_id))


@router.patch(
    "/{workflow_id}",
    response_model=WorkflowDetail,
    summary="Renombrar, describir, archivar o restaurar",
)
async def update_workflow(
    workflow_id: uuid.UUID,
    data: WorkflowUpdate,
    session: SessionDep,
    context: ContextDep,
    actor: Writer,
) -> WorkflowDetail:
    await service.update_workflow(session, actor, workflow_id, data, context)
    return _workflow_detail(await service.get_state(session, workflow_id))


@router.put(
    "/{workflow_id}/draft",
    response_model=VersionDetail,
    summary="Guardar el borrador",
    description=(
        "Crea o actualiza el borrador. Se guarda aunque tenga incidencias de validación "
        "(se devuelven en `issues`); solo se exige que no haya ninguna al publicar."
    ),
    responses={409: {"description": "Conflicto de revisión o flujo archivado", **_PROBLEM}},
)
async def save_draft(
    workflow_id: uuid.UUID,
    data: DraftSave,
    session: SessionDep,
    context: ContextDep,
    actor: Writer,
) -> VersionDetail:
    draft = await service.save_draft(
        session, actor, workflow_id, data.definition, data.expected_revision, context
    )
    return _detail(draft)


@router.post(
    "/{workflow_id}/publish",
    response_model=VersionDetail,
    summary="Publicar el borrador como versión inmutable",
    responses={
        409: {"description": "Conflicto de revisión, sin borrador o archivado", **_PROBLEM},
        422: {"description": "La definición tiene incidencias", **_PROBLEM},
    },
)
async def publish(
    workflow_id: uuid.UUID,
    data: PublishRequest,
    session: SessionDep,
    context: ContextDep,
    actor: Writer,
) -> VersionDetail:
    version = await service.publish(session, actor, workflow_id, data.expected_revision, context)
    return _detail(version)


@router.get(
    "/{workflow_id}/versions",
    response_model=list[VersionSummary],
    summary="Historial de versiones",
)
async def list_versions(
    workflow_id: uuid.UUID, session: SessionDep, _: Reader
) -> list[VersionSummary]:
    return [_summary(v) for v in await service.list_versions(session, workflow_id)]


@router.get(
    "/{workflow_id}/versions/{version}",
    response_model=VersionDetail,
    summary="Una versión concreta con su definición",
)
async def get_version(
    workflow_id: uuid.UUID,
    version: Annotated[int, Path(ge=1, le=1_000_000)],
    session: SessionDep,
    _: Reader,
) -> VersionDetail:
    return _detail(await service.get_version(session, workflow_id, version))
