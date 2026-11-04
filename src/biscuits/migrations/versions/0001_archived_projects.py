"""Projects archived in biscuits

Revision ID: 0001
Revises:
"""

from alembic import op
import sqlalchemy as sa

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "archived_projects",
        sa.Column("project_id", sa.Integer(), autoincrement=False, primary_key=True),
        sa.Column("archived_at", sa.DateTime(), nullable=False),
    )


def downgrade():
    op.drop_table("archived_projects")
