"""query_logs: add retrieval_method

Revision ID: 061d5bbd7ef7
Revises: c384db799bdc
Create Date: 2026-10-18 16:07:58.273079

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '061d5bbd7ef7'
down_revision: Union[str, Sequence[str], None] = 'c384db799bdc'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Record which search answered each request: lexical, dense, or a fallback."""
    # Nullable on purpose: every row written before this column existed stays
    # NULL. All of those were lexical; created_at and commit_sha date them.
    op.add_column(
        "query_logs",
        sa.Column("retrieval_method", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("query_logs", "retrieval_method")
