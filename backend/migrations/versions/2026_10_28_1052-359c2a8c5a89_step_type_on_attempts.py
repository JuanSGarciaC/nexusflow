"""step type on attempts

Revision ID: 359c2a8c5a89
Revises: 335e391e0559
Create Date: 2026-10-28 10:52:54.547145

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "359c2a8c5a89"
down_revision: str | Sequence[str] | None = "335e391e0559"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Copia step_runs.step_type en step_attempts (campo inmutable, para métricas sin JOIN).

    En tres pasos para que funcione con datos existentes: añadir nullable, rellenar desde
    step_runs y después exigir NOT NULL.
    """
    op.add_column("step_attempts", sa.Column("step_type", sa.String(length=20), nullable=True))
    op.execute(
        "UPDATE step_attempts AS a SET step_type = s.step_type "
        "FROM step_runs AS s WHERE s.id = a.step_run_id"
    )
    op.alter_column("step_attempts", "step_type", nullable=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("step_attempts", "step_type")
