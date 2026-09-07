"""Initial schema migration for all WhatsApp Bot Platform tables.

Revision ID: 001_initial_schema
Revises: None
Create Date: 2026-09-08 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "001_initial_schema"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Contacts
    op.create_table(
        "contacts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("business_id", sa.String(64), nullable=False, index=True),
        sa.Column("phone_hash", sa.String(64), nullable=False),
        sa.Column("phone_encrypted", sa.Text(), nullable=False),
        sa.Column("phone_masked", sa.String(32), nullable=False),
        sa.Column("external_id", sa.String(128), nullable=True),
        sa.Column("first_name", sa.String(128), nullable=True),
        sa.Column("last_name", sa.String(128), nullable=True),
        sa.Column("locale", sa.String(16), nullable=False, server_default="en"),
        sa.Column("timezone_name", sa.String(64), nullable=True),
        sa.Column("tags", sa.JSON(), nullable=False),
        sa.Column("consent_status", sa.String(24), nullable=False, index=True),
        sa.Column("bot_paused", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("pause_reason", sa.String(255), nullable=True),
        sa.Column("last_inbound_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_outbound_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("business_id", "phone_hash", name="uq_contact_business_phone"),
    )
    op.create_index("ix_contact_business_consent", "contacts", ["business_id", "consent_status"])

    # 2. Conversations
    op.create_table(
        "conversations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("business_id", sa.String(64), nullable=False, index=True),
        sa.Column("contact_id", sa.Integer(), sa.ForeignKey("contacts.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("state", sa.String(32), nullable=False, index=True),
        sa.Column("last_intent", sa.String(48), nullable=True),
        sa.Column("last_confidence", sa.Float(), nullable=True),
        sa.Column("escalated", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("human_agent", sa.String(128), nullable=True),
        sa.Column("service_window_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("correlation_id", sa.String(64), nullable=True),
        sa.Column("context", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_conv_business_state", "conversations", ["business_id", "state"])

    # 3. Campaigns
    op.create_table(
        "campaigns",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("business_id", sa.String(64), nullable=False, index=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("template_name", sa.String(512), nullable=False),
        sa.Column("template_language", sa.String(16), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, index=True),
        sa.Column("pause_reason", sa.String(255), nullable=True),
        sa.Column("total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("eligible", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("rejected", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("sent", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("delivered", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("read", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("correlation_id", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("business_id", "name", name="uq_campaign_business_name"),
    )

    # 4. Messages
    op.create_table(
        "messages",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("business_id", sa.String(64), nullable=False, index=True),
        sa.Column("conversation_id", sa.Integer(), sa.ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("contact_id", sa.Integer(), sa.ForeignKey("contacts.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("meta_message_id", sa.String(128), nullable=True),
        sa.Column("direction", sa.String(16), nullable=False),
        sa.Column("message_type", sa.String(40), nullable=False),
        sa.Column("content_encrypted", sa.Text(), nullable=True),
        sa.Column("content_preview", sa.String(80), nullable=True),
        sa.Column("media_reference", sa.JSON(), nullable=True),
        sa.Column("intent", sa.String(48), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("delivery_status", sa.String(24), nullable=True, index=True),
        sa.Column("error_code", sa.Integer(), nullable=True),
        sa.Column("error_detail", sa.Text(), nullable=True),
        sa.Column("template_name", sa.String(512), nullable=True),
        sa.Column("template_language", sa.String(16), nullable=True),
        sa.Column("campaign_id", sa.Integer(), sa.ForeignKey("campaigns.id", ondelete="SET NULL"), nullable=True, index=True),
        sa.Column("correlation_id", sa.String(64), nullable=True, index=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("business_id", "meta_message_id", name="uq_message_business_meta_id"),
    )
    op.create_index("ix_message_business_created", "messages", ["business_id", "created_at"])

    # 5. Consent Records
    op.create_table(
        "consent_records",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("business_id", sa.String(64), nullable=False, index=True),
        sa.Column("contact_id", sa.Integer(), sa.ForeignKey("contacts.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("purpose", sa.String(24), nullable=False),
        sa.Column("source", sa.String(48), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("evidence", sa.Text(), nullable=True),
        sa.Column("correlation_id", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_consent_contact_purpose", "consent_records", ["contact_id", "purpose"])

    # 6. Suppression List
    op.create_table(
        "suppression_list",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("business_id", sa.String(64), nullable=False, index=True),
        sa.Column("phone_hash", sa.String(64), nullable=False),
        sa.Column("phone_masked", sa.String(32), nullable=False),
        sa.Column("reason", sa.String(64), nullable=False),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("suppressed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("business_id", "phone_hash", name="uq_suppression_business_phone"),
    )

    # 7. Campaign Recipients
    op.create_table(
        "campaign_recipients",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("campaign_id", sa.Integer(), sa.ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("contact_id", sa.Integer(), sa.ForeignKey("contacts.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("status", sa.String(24), nullable=False, index=True),
        sa.Column("rejection_reason", sa.String(255), nullable=True),
        sa.Column("template_variables", sa.JSON(), nullable=False),
        sa.Column("meta_message_id", sa.String(128), nullable=True),
        sa.Column("error_code", sa.Integer(), nullable=True),
        sa.Column("error_detail", sa.Text(), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("campaign_id", "contact_id", name="uq_campaign_recipient"),
    )

    # 8. Escalations
    op.create_table(
        "escalations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("business_id", sa.String(64), nullable=False, index=True),
        sa.Column("conversation_id", sa.Integer(), sa.ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("contact_id", sa.Integer(), sa.ForeignKey("contacts.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("reason", sa.String(48), nullable=False, index=True),
        sa.Column("status", sa.String(24), nullable=False, index=True),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("detected_intent", sa.String(48), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("assigned_to", sa.String(128), nullable=True),
        sa.Column("resolution_note", sa.Text(), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("correlation_id", sa.String(64), nullable=True),
        sa.Column("crm_reference", sa.String(128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    # 9. Webhook Events (Idempotency ledger)
    op.create_table(
        "webhook_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("business_id", sa.String(64), nullable=False, index=True),
        sa.Column("event_key", sa.String(160), nullable=False),
        sa.Column("event_type", sa.String(40), nullable=False),
        sa.Column("processed", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("correlation_id", sa.String(64), nullable=True),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("business_id", "event_key", name="uq_webhook_business_event"),
    )

    # 10. Dead Letters
    op.create_table(
        "dead_letters",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("business_id", sa.String(64), nullable=False, index=True),
        sa.Column("source", sa.String(48), nullable=False),
        sa.Column("event_key", sa.String(160), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("error", sa.Text(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("resolved", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("correlation_id", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    # 11. Bot State
    op.create_table(
        "bot_state",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("business_id", sa.String(64), nullable=False, unique=True, index=True),
        sa.Column("paused", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("pause_reason", sa.String(255), nullable=True),
        sa.Column("paused_by", sa.String(128), nullable=True),
        sa.Column("paused_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("bot_state")
    op.drop_table("dead_letters")
    op.drop_table("webhook_events")
    op.drop_table("escalations")
    op.drop_table("campaign_recipients")
    op.drop_table("suppression_list")
    op.drop_table("consent_records")
    op.drop_table("messages")
    op.drop_table("campaigns")
    op.drop_table("conversations")
    op.drop_table("contacts")
