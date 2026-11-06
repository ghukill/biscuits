"""Where each section sits in its project

Revision ID: 0003
Revises: 0002
"""

from alembic import op
import sqlalchemy as sa

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "section_positions",
        sa.Column("section_id", sa.Integer(), autoincrement=False, primary_key=True),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("updated_by", sa.Integer(), nullable=True),
    )


def downgrade():
    op.drop_table("section_positions")
