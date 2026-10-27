"""Modelos ORM de flujos y versiones."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from nexusflow.db.base import Base
from nexusflow.db.types import TimestampMixin, UUIDPrimaryKeyMixin


class WorkflowStatus(StrEnum):
    ACTIVE = "active"
    ARCHIVED = "archived"


class Workflow(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "workflows"

    name: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[WorkflowStatus] = mapped_column(
        Enum(
            WorkflowStatus,
            name="workflow_status",
            native_enum=False,
            create_constraint=True,
            length=20,
            values_callable=lambda items: [i.value for i in items],
        ),
        nullable=False,
        default=WorkflowStatus.ACTIVE,
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )


class WorkflowVersion(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Una versión de la definición.

    - Borrador (`published_at` NULL): editable, como máximo uno por flujo; `revision` se
      incrementa en cada guardado (control de concurrencia optimista).
    - Publicada: inmutable. Un trigger de PostgreSQL rechaza cualquier UPDATE posterior,
      de modo que ni un error de la aplicación puede alterar lo que ya se ejecutó.
    """

    __tablename__ = "workflow_versions"
    __table_args__ = (
        UniqueConstraint("workflow_id", "version", name="uq_workflow_versions_workflow_version"),
        Index(
            "uq_workflow_versions_one_draft",
            "workflow_id",
            unique=True,
            postgresql_where=text("published_at IS NULL"),
        ),
        CheckConstraint("version >= 1", name="version_positive"),
        CheckConstraint("revision >= 1", name="revision_positive"),
    )

    workflow_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workflows.id", ondelete="RESTRICT"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    definition: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    definition_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    updated_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    published_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )

    @property
    def is_published(self) -> bool:
        return self.published_at is not None
