"""email (H1): email_newsletters, the newsletters admins write and send

Revision ID: 0033
Revises: 0032
"""
from alembic import op
import sqlalchemy as sa

revision = "0033"
down_revision = "0032"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "email_newsletters",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("subject", sa.String(150), nullable=False),
        sa.Column("body", sa.Text, nullable=False),
        sa.Column("created_by", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True)),
        sa.Column("sent_by", sa.String(255)),
        sa.Column("recipients", sa.Integer),
    )


def downgrade() -> None:
    op.drop_table("email_newsletters")
