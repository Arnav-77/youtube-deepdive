"""videos: add raw_paste

Revision ID: a41f0c9e2b7d
Revises: 061d5bbd7ef7
Create Date: 2026-11-10 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a41f0c9e2b7d'
down_revision: Union[str, Sequence[str], None] = '061d5bbd7ef7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Keep the exact text a person pasted from YouTube's transcript panel."""
    # Nullable and expand-only: every existing video (all captions path) keeps
    # NULL, and nothing is backfilled. Applied before the code that reads it,
    # because session.get(Video) selects every mapped column.
    op.add_column("videos", sa.Column("raw_paste", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("videos", "raw_paste")
