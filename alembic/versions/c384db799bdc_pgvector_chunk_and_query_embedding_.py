"""pgvector: chunk and query embedding tables

Revision ID: c384db799bdc
Revises: 7e49eeb7f12a
Create Date: 2026-10-14 10:01:41.732182

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c384db799bdc'
down_revision: Union[str, Sequence[str], None] = '7e49eeb7f12a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    # pgvector ships with Neon but has to be enabled once per database.
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # One embedding per (chunk, model). Never a column on chunks: chunk rows
    # are append-only, and two embedding models must be able to coexist.
    op.execute(
        """
        CREATE TABLE chunk_embeddings (
            chunk_id   integer      NOT NULL REFERENCES chunks (id),
            model      varchar(100) NOT NULL,
            embedding  vector(768)  NOT NULL,
            created_at timestamptz  NOT NULL DEFAULT now(),
            PRIMARY KEY (chunk_id, model)
        )
        """
    )

    # Question-embedding cache: the same text and the same model always give
    # the same vector, so evaluation reruns cost no quota after the first.
    op.execute(
        """
        CREATE TABLE query_embeddings (
            text_sha256 varchar(64)  NOT NULL,
            model       varchar(100) NOT NULL,
            embedding   vector(768)  NOT NULL,
            created_at  timestamptz  NOT NULL DEFAULT now(),
            PRIMARY KEY (text_sha256, model)
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE query_embeddings")
    op.execute("DROP TABLE chunk_embeddings")
    # The extension is deliberately left installed: an unused extension costs
    # nothing, and dropping it would fail if anything else came to depend on it.