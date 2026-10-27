"""Tipos y columnas reutilizables."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, func
from sqlalchemy.orm import Mapped, mapped_column


def utcnow() -> datetime:
    """Hora actual con zona horaria UTC (nunca datetimes \"naive\")."""
    return datetime.now(UTC)


class UUIDPrimaryKeyMixin:
    # Se genera en la aplicación: el id se conoce antes del INSERT (útil para auditoría
    # y referencias dentro de la misma transacción) y no revela cuántos registros hay.
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
