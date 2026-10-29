"""Esquemas de la API de programaciones."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from nexusflow.schedules.cron import InvalidScheduleError, next_fire


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


def check_cron(expression: str, timezone: str) -> None:
    from datetime import UTC

    try:
        next_fire(expression, timezone, datetime.now(UTC))
    except InvalidScheduleError as exc:
        raise ValueError(str(exc)) from exc


class ScheduleCreate(_Strict):
    name: str = Field(min_length=1, max_length=100)
    cron_expression: str = Field(
        min_length=9,
        max_length=100,
        examples=["*/15 * * * *", "0 9 * * 1-5"],
        description="Cron de 5 campos: minuto hora día-del-mes mes día-de-la-semana",
    )
    timezone: str = Field(default="UTC", max_length=64, examples=["Europe/Madrid"])
    input: dict[str, JsonValue] = Field(default_factory=dict)
    is_active: bool = True

    @model_validator(mode="after")
    def _valid_cron(self) -> ScheduleCreate:
        check_cron(self.cron_expression, self.timezone)
        return self


class ScheduleUpdate(_Strict):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    cron_expression: str | None = Field(default=None, min_length=9, max_length=100)
    timezone: str | None = Field(default=None, max_length=64)
    input: dict[str, JsonValue] | None = None
    is_active: bool | None = None


class ScheduleView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    workflow_id: uuid.UUID
    name: str
    cron_expression: str
    timezone: str
    input: dict[str, JsonValue]
    is_active: bool
    next_run_at: datetime | None
    last_run_at: datetime | None
    last_run_id: uuid.UUID | None
    last_error: str | None
    created_at: datetime
