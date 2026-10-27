"""Esquemas de la API de ejecuciones."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from nexusflow.engine.models import RunStatus, StepStatus


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RunCreate(_Strict):
    input: dict[str, JsonValue] = Field(default_factory=dict)
    version: int | None = Field(
        default=None, ge=1, description="Versión publicada; por defecto la última"
    )
    idempotency_key: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9._:\-]+$",
        description="Si se repite con la misma entrada, se devuelve la misma ejecución",
    )


class ResolveStep(_Strict):
    action: Literal["retry", "mark_succeeded", "mark_failed"]
    output: JsonValue = None
    note: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def _output_only_on_success(self) -> Self:
        if self.output is not None and self.action != "mark_succeeded":
            raise ValueError("'output' solo se admite con action=mark_succeeded")
        return self


class AttemptView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    attempt_number: int
    started_at: datetime
    finished_at: datetime | None
    duration_ms: int | None
    outcome: str | None
    error_class: str | None
    http_status: int | None
    error_message: str | None


class StepView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    step_id: str
    step_type: str
    sequence: int
    status: StepStatus
    side_effect: str
    idempotency_key: str
    attempt_count: int
    max_attempts: int
    output: Any | None
    error: dict[str, Any] | None
    next_attempt_at: datetime | None
    started_at: datetime | None
    finished_at: datetime | None
    attempts: list[AttemptView] = []


class RunSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    workflow_id: uuid.UUID
    version_id: uuid.UUID
    version: int
    status: RunStatus
    trigger_type: str
    triggered_by: uuid.UUID | None
    idempotency_key: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    duration_ms: int | None
    error: dict[str, Any] | None


class RunDetail(RunSummary):
    input: dict[str, Any]
    output: Any | None
    deadline_at: datetime
    steps: list[StepView]


class RunPage(BaseModel):
    items: list[RunSummary]
    total: int
    limit: int
    offset: int
