"""Modelo de credenciales de integraciones."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from nexusflow.db.base import Base
from nexusflow.db.types import TimestampMixin, UUIDPrimaryKeyMixin
from nexusflow.integrations.adapters import CredentialType


class Credential(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "credentials"

    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    type: Mapped[CredentialType] = mapped_column(
        Enum(
            CredentialType,
            name="credential_type",
            native_enum=False,
            create_constraint=True,
            length=30,
            values_callable=lambda items: [i.value for i in items],
        ),
        nullable=False,
    )
    description: Mapped[str | None] = mapped_column(Text)
    # Hosts a los que se puede enviar el secreto. Un paso que use la credencial contra otro
    # host falla: así un editor no puede exfiltrar el token cambiando la URL.
    allowed_hosts: Mapped[list[str]] = mapped_column(ARRAY(String(253)), nullable=False)
    secret_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    key_fingerprint: Mapped[str] = mapped_column(String(12), nullable=False)
    # Pista no secreta para reconocerla en la interfaz (p. ej. "…a1b2").
    hint: Mapped[str] = mapped_column(String(120), nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    secret_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
