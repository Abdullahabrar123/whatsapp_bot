"""Add persistent operator-managed message templates."""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "002_message_templates"
down_revision: Union[str, None] = "001_initial_schema"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    if "message_templates" in sa.inspect(bind).get_table_names():
        return

    op.create_table(
        "message_templates",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("business_id", sa.String(64), nullable=False, index=True),
        sa.Column("name", sa.String(512), nullable=False),
        sa.Column("language", sa.String(16), nullable=False),
        sa.Column("category", sa.String(24), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("purpose", sa.Text(), nullable=True),
        sa.Column("requires_opt_in", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("variables", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("business_id", "name", "language", name="uq_template_business_name_language"),
    )


def downgrade() -> None:
    op.drop_table("message_templates")