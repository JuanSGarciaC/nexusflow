"""Casos de uso de flujos y versiones."""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from nexusflow.audit.service import RequestContext, record
from nexusflow.auth.models import User
from nexusflow.core.errors import AppError, ConflictError, NotFoundError
from nexusflow.db.types import utcnow
from nexusflow.workflows.dsl import WorkflowDefinition
from nexusflow.workflows.models import Workflow, WorkflowStatus, WorkflowVersion
from nexusflow.workflows.schemas import WorkflowCreate, WorkflowUpdate
from nexusflow.workflows.validation import Issue, validate_definition


class WorkflowAction:
    CREATED = "workflow.created"
    UPDATED = "workflow.updated"
    DRAFT_SAVED = "workflow.draft_saved"
    PUBLISHED = "workflow.published"
    ARCHIVED = "workflow.archived"
    RESTORED = "workflow.restored"


class InvalidDefinitionError(AppError):
    status_code = 422
    code = "invalid-workflow"
    title = "La definición del flujo no es válida"

    def __init__(self, issues: list[Issue]) -> None:
        super().__init__(
            "Corrige las incidencias antes de publicar.",
            extra={"issues": [i.as_dict() for i in issues]},
        )


@dataclass(frozen=True, slots=True)
class WorkflowState:
    workflow: Workflow
    draft: WorkflowVersion | None
    published: WorkflowVersion | None


def canonical_json(definition: dict[str, Any]) -> str:
    return json.dumps(definition, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def definition_hash(definition: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(definition).encode()).hexdigest()


def _serialize(definition: WorkflowDefinition) -> dict[str, Any]:
    # by_alias: la condición "not" se guarda con su nombre público, no "not_".
    data: dict[str, Any] = definition.model_dump(mode="json", by_alias=True, exclude_none=True)
    return data


def parse_stored(definition: dict[str, Any]) -> WorkflowDefinition:
    return WorkflowDefinition.model_validate(definition)


def issues_for(version: WorkflowVersion) -> list[Issue]:
    return validate_definition(parse_stored(version.definition))


def referenced_credentials(definition: WorkflowDefinition) -> dict[str, int]:
    """Nombre de credencial → índice del primer paso que la usa."""
    found: dict[str, int] = {}
    for index, step in enumerate(definition.steps):
        credential = getattr(getattr(step, "config", None), "credential", None)
        if credential and credential not in found:
            found[credential] = index
    return found


async def credential_issues(session: AsyncSession, definition: WorkflowDefinition) -> list[Issue]:
    """Credenciales referenciadas que no existen (requiere base de datos)."""
    from nexusflow.integrations.models import Credential

    wanted = referenced_credentials(definition)
    if not wanted:
        return []
    rows = await session.scalars(select(Credential.name).where(Credential.name.in_(wanted)))
    existing = set(rows)
    return [
        Issue(
            "unknown_credential",
            f"La credencial '{name}' no existe",
            f"steps[{index}].config.credential",
        )
        for name, index in wanted.items()
        if name not in existing
    ]


async def all_issues(session: AsyncSession, version: WorkflowVersion) -> list[Issue]:
    definition = parse_stored(version.definition)
    return validate_definition(definition) + await credential_issues(session, definition)


# ------------------------------------------------------------------ lectura


async def _get_workflow(
    session: AsyncSession, workflow_id: uuid.UUID, *, lock: bool = False
) -> Workflow:
    workflow = await session.get(Workflow, workflow_id, with_for_update=lock)
    if workflow is None:
        raise NotFoundError("El flujo no existe.")
    return workflow


async def _draft(
    session: AsyncSession, workflow_id: uuid.UUID, *, lock: bool = False
) -> WorkflowVersion | None:
    query = select(WorkflowVersion).where(
        WorkflowVersion.workflow_id == workflow_id, WorkflowVersion.published_at.is_(None)
    )
    if lock:
        query = query.with_for_update()
    return await session.scalar(query)


async def latest_published(session: AsyncSession, workflow_id: uuid.UUID) -> WorkflowVersion | None:
    return await session.scalar(
        select(WorkflowVersion)
        .where(
            WorkflowVersion.workflow_id == workflow_id, WorkflowVersion.published_at.is_not(None)
        )
        .order_by(WorkflowVersion.version.desc())
        .limit(1)
    )


async def get_state(session: AsyncSession, workflow_id: uuid.UUID) -> WorkflowState:
    workflow = await _get_workflow(session, workflow_id)
    return WorkflowState(
        workflow, await _draft(session, workflow_id), await latest_published(session, workflow_id)
    )


async def list_workflows(
    session: AsyncSession,
    *,
    status: WorkflowStatus | None,
    search: str | None,
    limit: int,
    offset: int,
) -> tuple[list[tuple[Workflow, int | None, int | None]], int]:
    published = (
        select(func.max(WorkflowVersion.version))
        .where(
            WorkflowVersion.workflow_id == Workflow.id, WorkflowVersion.published_at.is_not(None)
        )
        .scalar_subquery()
    )
    draft = (
        select(WorkflowVersion.version)
        .where(WorkflowVersion.workflow_id == Workflow.id, WorkflowVersion.published_at.is_(None))
        .scalar_subquery()
    )
    filters = []
    if status is not None:
        filters.append(Workflow.status == status)
    if search:
        # Se escapan los comodines de LIKE: el texto del usuario se busca literalmente.
        escaped = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        pattern = f"%{escaped}%"
        filters.append(
            or_(
                Workflow.name.ilike(pattern, escape="\\"),
                Workflow.description.ilike(pattern, escape="\\"),
            )
        )

    total = await session.scalar(select(func.count()).select_from(Workflow).where(*filters)) or 0
    rows = await session.execute(
        select(Workflow, published, draft)
        .where(*filters)
        .order_by(Workflow.updated_at.desc(), Workflow.id)
        .limit(limit)
        .offset(offset)
    )
    return [(w, p, d) for w, p, d in rows.all()], total


async def list_versions(session: AsyncSession, workflow_id: uuid.UUID) -> list[WorkflowVersion]:
    await _get_workflow(session, workflow_id)
    rows = await session.scalars(
        select(WorkflowVersion)
        .where(WorkflowVersion.workflow_id == workflow_id)
        .order_by(WorkflowVersion.version.desc())
    )
    return list(rows)


async def get_version(
    session: AsyncSession, workflow_id: uuid.UUID, version: int
) -> WorkflowVersion:
    found = await session.scalar(
        select(WorkflowVersion).where(
            WorkflowVersion.workflow_id == workflow_id, WorkflowVersion.version == version
        )
    )
    if found is None:
        raise NotFoundError("La versión no existe.")
    return found


# ------------------------------------------------------------------ escritura


def _ensure_active(workflow: Workflow) -> None:
    if workflow.status == WorkflowStatus.ARCHIVED:
        raise ConflictError("El flujo está archivado. Restáuralo antes de modificarlo.")


async def create_workflow(
    session: AsyncSession, actor: User, data: WorkflowCreate, context: RequestContext
) -> WorkflowState:
    stored = _serialize(data.definition)
    workflow = Workflow(
        id=uuid.uuid4(), name=data.name.strip(), description=data.description, created_by=actor.id
    )
    draft = WorkflowVersion(
        workflow_id=workflow.id,
        version=1,
        revision=1,
        definition=stored,
        definition_hash=definition_hash(stored),
        created_by=actor.id,
        updated_by=actor.id,
    )
    session.add(workflow)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise ConflictError("Ya existe un flujo con ese nombre.") from exc
    session.add(draft)
    record(
        session,
        action=WorkflowAction.CREATED,
        context=context,
        actor_user_id=actor.id,
        resource_type="workflow",
        resource_id=str(workflow.id),
        details={"name": workflow.name},
    )
    await session.commit()
    return WorkflowState(workflow, draft, None)


async def update_workflow(
    session: AsyncSession,
    actor: User,
    workflow_id: uuid.UUID,
    data: WorkflowUpdate,
    context: RequestContext,
) -> Workflow:
    workflow = await _get_workflow(session, workflow_id, lock=True)
    changes: dict[str, Any] = {}
    if data.name is not None and data.name.strip() != workflow.name:
        changes["name"] = data.name.strip()
        workflow.name = data.name.strip()
    if "description" in data.model_fields_set and data.description != workflow.description:
        changes["description"] = True
        workflow.description = data.description
    action = WorkflowAction.UPDATED
    if data.status is not None and data.status != workflow.status:
        workflow.status = data.status
        action = (
            WorkflowAction.ARCHIVED
            if data.status == WorkflowStatus.ARCHIVED
            else WorkflowAction.RESTORED
        )
        changes["status"] = data.status.value
    if not changes:
        await session.rollback()
        return workflow
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise ConflictError("Ya existe un flujo con ese nombre.") from exc
    record(
        session,
        action=action,
        context=context,
        actor_user_id=actor.id,
        resource_type="workflow",
        resource_id=str(workflow.id),
        details=changes,
    )
    await session.commit()
    await session.refresh(workflow)
    return workflow


async def save_draft(
    session: AsyncSession,
    actor: User,
    workflow_id: uuid.UUID,
    definition: WorkflowDefinition,
    expected_revision: int | None,
    context: RequestContext,
) -> WorkflowVersion:
    """Guarda el borrador con control de concurrencia optimista.

    Si existe borrador, se actualiza solo si su `revision` coincide con la esperada
    (UPDATE ... WHERE revision = :expected): si otro editor guardó antes, 0 filas → 409.
    Si no existe, se crea uno nuevo a partir de la última versión.
    """
    workflow = await _get_workflow(session, workflow_id, lock=True)
    _ensure_active(workflow)
    stored = _serialize(definition)
    digest = definition_hash(stored)
    current = await _draft(session, workflow_id)

    if current is not None:
        if expected_revision is None:
            raise ConflictError(
                "Ya existe un borrador: indica expected_revision para evitar sobrescribir "
                "cambios de otro usuario."
            )
        result = await session.execute(
            update(WorkflowVersion)
            .where(WorkflowVersion.id == current.id, WorkflowVersion.revision == expected_revision)
            .values(
                definition=stored,
                definition_hash=digest,
                revision=WorkflowVersion.revision + 1,
                updated_by=actor.id,
                updated_at=utcnow(),
            )
            .returning(WorkflowVersion.id)
        )
        if result.scalar_one_or_none() is None:
            await session.rollback()
            raise ConflictError(
                "El borrador cambió desde que lo abriste. Recárgalo y vuelve a aplicar tus cambios."
            )
        draft_id = current.id
    else:
        if expected_revision is not None:
            await session.rollback()
            raise ConflictError("No hay borrador abierto; omite expected_revision.")
        next_version = (
            await session.scalar(
                select(func.max(WorkflowVersion.version)).where(
                    WorkflowVersion.workflow_id == workflow_id
                )
            )
            or 0
        ) + 1
        new_draft = WorkflowVersion(
            workflow_id=workflow_id,
            version=next_version,
            revision=1,
            definition=stored,
            definition_hash=digest,
            created_by=actor.id,
            updated_by=actor.id,
        )
        session.add(new_draft)
        await session.flush()
        draft_id = new_draft.id

    workflow.updated_at = utcnow()
    record(
        session,
        action=WorkflowAction.DRAFT_SAVED,
        context=context,
        actor_user_id=actor.id,
        resource_type="workflow",
        resource_id=str(workflow_id),
        details={"definition_hash": digest},
    )
    await session.commit()
    saved = await session.get(WorkflowVersion, draft_id, populate_existing=True)
    if saved is None:  # solo posible si se borró en paralelo, cosa que el trigger impide
        raise NotFoundError("El borrador no existe.")
    return saved


async def publish(
    session: AsyncSession,
    actor: User,
    workflow_id: uuid.UUID,
    expected_revision: int,
    context: RequestContext,
) -> WorkflowVersion:
    workflow = await _get_workflow(session, workflow_id, lock=True)
    _ensure_active(workflow)
    draft = await _draft(session, workflow_id, lock=True)
    if draft is None:
        raise ConflictError("No hay borrador que publicar.")
    if draft.revision != expected_revision:
        raise ConflictError(
            "El borrador cambió desde que lo revisaste. Revisa la última revisión "
            "antes de publicar."
        )
    issues = await all_issues(session, draft)
    if issues:
        await session.rollback()
        raise InvalidDefinitionError(issues)

    draft.published_at = utcnow()
    draft.published_by = actor.id
    workflow.updated_at = utcnow()
    record(
        session,
        action=WorkflowAction.PUBLISHED,
        context=context,
        actor_user_id=actor.id,
        resource_type="workflow",
        resource_id=str(workflow_id),
        details={"version": draft.version, "definition_hash": draft.definition_hash},
    )
    await session.commit()
    await session.refresh(draft)
    return draft
