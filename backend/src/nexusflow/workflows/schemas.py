"""Esquemas de la API de flujos."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from nexusflow.workflows.dsl import WorkflowDefinition
from nexusflow.workflows.models import WorkflowStatus


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ValidationIssue(BaseModel):
    code: str
    message: str
    location: str


class WorkflowCreate(_Strict):
    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    definition: WorkflowDefinition


class WorkflowUpdate(_Strict):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    status: WorkflowStatus | None = None


class DraftSave(_Strict):
    definition: WorkflowDefinition
    expected_revision: int | None = Field(
        default=None,
        ge=1,
        description=(
            "Revisión del borrador que se está editando. Obligatoria si ya existe un borrador; "
            "si otro usuario lo guardó antes, la API responde 409."
        ),
    )


class PublishRequest(_Strict):
    expected_revision: int = Field(ge=1)


class ValidateRequest(_Strict):
    definition: WorkflowDefinition


class ValidateResponse(BaseModel):
    valid: bool
    issues: list[ValidationIssue]


class VersionSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    version: int
    state: Literal["draft", "published"]
    revision: int
    definition_hash: str
    created_at: datetime
    updated_at: datetime
    published_at: datetime | None
    created_by: uuid.UUID | None
    published_by: uuid.UUID | None


class VersionDetail(VersionSummary):
    definition: WorkflowDefinition
    issues: list[ValidationIssue]


class WorkflowSummary(BaseModel):
    id: uuid.UUID
    name: str
    description: str | None
    status: WorkflowStatus
    created_at: datetime
    updated_at: datetime
    published_version: int | None
    draft_version: int | None


class WorkflowDetail(WorkflowSummary):
    created_by: uuid.UUID | None
    draft: VersionDetail | None
    published: VersionDetail | None


class WorkflowPage(BaseModel):
    items: list[WorkflowSummary]
    total: int
    limit: int
    offset: int
