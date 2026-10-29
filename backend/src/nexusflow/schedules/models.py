"""Programaciones: lanzan un flujo según una expresión cron."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from nexusflow.db.base import Base
from nexusflow.db.types import TimestampMixin, UUIDPrimaryKeyMixin


class WorkflowSchedule(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "workflow_schedules"
    __table_args__ = (
        # El barrido solo busca programaciones activas: el índice no crece con las pausadas.
        Index(
            "ix_workflow_schedules_due",
            "next_run_at",
            postgresql_where=text("is_active"),
        ),
    )

    workflow_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workflows.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    cron_expression: Mapped[str] = mapped_column(String(100), nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="UTC")
    input: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # Próximo disparo (UTC). NULL si la programación está pausada.
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("workflow_runs.id", ondelete="SET NULL")
    )
    last_error: Mapped[str | None] = mapped_column(String(300))
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
