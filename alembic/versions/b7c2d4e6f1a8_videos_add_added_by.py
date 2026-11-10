"""videos: add added_by

Revision ID: b7c2d4e6f1a8
Revises: a41f0c9e2b7d
Create Date: 2026-11-10 14:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b7c2d4e6f1a8'
down_revision: Union[str, Sequence[str], None] = 'a41f0c9e2b7d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Record who added a pasted video: 'visitor' (public) or 'admin' (token)."""
    # Nullable and expand-only. Captions videos keep NULL; pasted videos created
    # before this column existed (none in production) read as 'visitor' in code.
    op.add_column("videos", sa.Column("added_by", sa.String(length=16), nullable=True))


def downgrade() -> None:
    op.drop_column("videos", "added_by")
