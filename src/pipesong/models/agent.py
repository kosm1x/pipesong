import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from pipesong.services.database import Base


class Agent(Base):
    __tablename__ = "agents"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255))
    system_prompt: Mapped[str] = mapped_column(Text)
    language: Mapped[str] = mapped_column(String(5), default="es")
    voice: Mapped[str] = mapped_column(String(50), default="em_alex")
    phone_number: Mapped[str | None] = mapped_column(String(20), nullable=True)
    disclosure_message: Mapped[str] = mapped_column(Text)
    tools: Mapped[list | None] = mapped_column(JSON, nullable=True, default=list)
    webhook_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    webhook_secret: Mapped[str | None] = mapped_column(String(255), nullable=True)
    variables: Mapped[dict | None] = mapped_column(JSON, nullable=True, default=dict)
    max_call_duration: Mapped[int] = mapped_column(Integer, default=600)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    knowledge_base_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("knowledge_bases.id", ondelete="SET NULL"), nullable=True
    )
    kb_chunk_count: Mapped[int] = mapped_column(Integer, default=3)
    kb_similarity_threshold: Mapped[float] = mapped_column(Float, default=0.5)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
