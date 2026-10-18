"""Owner-only credential requests. Values live exclusively in the host SecretStore."""
from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base
from .models import now, uid


class CredentialRequest(Base):
    __tablename__ = "tt_credential_requests"
    id: Mapped[str] = mapped_column(String(80), primary_key=True, default=uid)
    owner_id: Mapped[str] = mapped_column(ForeignKey("tt_owners.id"), index=True)
    chat_id: Mapped[str] = mapped_column(ForeignKey("tt_chats.id"), index=True)
    origin_request_id: Mapped[str] = mapped_column(ForeignKey("tt_reply_requests.id"))
    runtime_session_id: Mapped[str | None] = mapped_column(String(128))
    runtime_run_id: Mapped[str | None] = mapped_column(String(128))
    runtime_command_id: Mapped[str | None] = mapped_column(String(256))
    ref: Mapped[str] = mapped_column(String(128))
    description: Mapped[str] = mapped_column(Text, default="")
    keynames: Mapped[list] = mapped_column(JSON)
    reason: Mapped[str] = mapped_column(Text, default="")
    source_provenance: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(16), default="pending")
    continuation_request_id: Mapped[str | None] = mapped_column(ForeignKey("tt_reply_requests.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
