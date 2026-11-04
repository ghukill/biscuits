"""biscuits accounts, and who archived a project

Revision ID: 0002
Revises: 0001
"""

from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("username", sa.String(64), nullable=False, unique=True),
        sa.Column("display_name", sa.String(255), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("is_admin", sa.Boolean(), nullable=False),
        sa.Column("must_change_password", sa.Boolean(), nullable=False),
        sa.Column("session_version", sa.Integer(), nullable=False),
        sa.Column("op_user_id", sa.Integer(), nullable=True, unique=True),
        sa.Column("op_user_name", sa.String(255), nullable=True),
        sa.Column("op_key_enc", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("last_login_at", sa.DateTime(), nullable=True),
        sa.Column("disabled_at", sa.DateTime(), nullable=True),
    )
    with op.batch_alter_table("archived_projects") as batch:
        batch.add_column(sa.Column("archived_by", sa.Integer(), nullable=True))


def downgrade():
    with op.batch_alter_table("archived_projects") as batch:
        batch.drop_column("archived_by")
    op.drop_table("users")
