"""Non-secret owner questions; answers are input, never action approval."""
from datetime import datetime

from sqlalchemy import JSON, CheckConstraint, DateTime, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base
from .models import now, uid


class QuestionRequest(Base):
    __tablename__ = "tt_question_requests"
    __table_args__ = (Index("tt_question_pending_run", "runtime_session_id", "runtime_run_id", "status"),
        CheckConstraint("status IN ('pending','answered','cancelled')", name="tt_question_status"))
    id: Mapped[str] = mapped_column(String(80), primary_key=True, default=uid)
    owner_id: Mapped[str] = mapped_column(ForeignKey("tt_owners.id"), index=True)
    chat_id: Mapped[str] = mapped_column(ForeignKey("tt_chats.id"), index=True)
    origin_chat_id: Mapped[str] = mapped_column(ForeignKey("tt_chats.id"))
    origin_request_id: Mapped[str] = mapped_column(ForeignKey("tt_reply_requests.id"), index=True)
    message_id: Mapped[str] = mapped_column(ForeignKey("tt_messages.id"), unique=True)
    runtime_session_id: Mapped[str] = mapped_column(String(128))
    runtime_run_id: Mapped[str] = mapped_column(String(128))
    runtime_command_id: Mapped[str] = mapped_column(String(256))
    question: Mapped[str] = mapped_column(Text)
    options: Mapped[list] = mapped_column(JSON, default=list)
    source_provenance: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(16), default="pending")
    answer: Mapped[str | None] = mapped_column(Text)
    answer_message_id: Mapped[str | None] = mapped_column(ForeignKey("tt_messages.id"), unique=True)
    continuation_request_id: Mapped[str | None] = mapped_column(ForeignKey("tt_reply_requests.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    answered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
