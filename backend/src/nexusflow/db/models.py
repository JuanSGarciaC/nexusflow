"""Registro central de modelos ORM.

Cada módulo de dominio importa aquí sus modelos para que Alembic los detecte.
En la Fase 1 todavía no hay tablas; la Fase 2 añade usuarios y tokens.
"""

from nexusflow.db.base import Base

__all__ = ["Base"]
