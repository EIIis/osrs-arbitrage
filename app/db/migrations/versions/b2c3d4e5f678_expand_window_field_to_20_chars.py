"""Expand window field from 2 chars to 20 chars to support 'latest_price'

Revision ID: b2c3d4e5f678
Revises: 8efdfd11a49d
Create Date: 2025-12-11

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b2c3d4e5f678'
down_revision: Union[str, None] = '8efdfd11a49d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Expand window column from VARCHAR(2) to VARCHAR(20)
    # This allows for longer window names like 'latest_price' instead of just '1m' or '5m'
    op.alter_column(
        'price_snapshots',
        'window',
        type_=sa.String(length=20),
        existing_type=sa.String(length=2),
        existing_nullable=False
    )
    op.alter_column(
        'arbitrage_snapshots',
        'window',
        type_=sa.String(length=20),
        existing_type=sa.String(length=2),
        existing_nullable=False
    )


def downgrade() -> None:
    # Shrink window column back to VARCHAR(2)
    # WARNING: This will truncate any values longer than 2 characters!
    op.alter_column(
        'price_snapshots',
        'window',
        type_=sa.String(length=2),
        existing_type=sa.String(length=20),
        existing_nullable=False
    )
    op.alter_column(
        'arbitrage_snapshots',
        'window',
        type_=sa.String(length=2),
        existing_type=sa.String(length=20),
        existing_nullable=False
    )
