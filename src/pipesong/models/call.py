import uuid
from datetime import datetime, timezone

from sqlalchemy import BigInteger, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from pipesong.services.database import Base


class Call(Base):
    __tablename__ = "calls"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    agent_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agents.id"))
    from_number: Mapped[str | None] = mapped_column(String(20), nullable=True)
    to_number: Mapped[str | None] = mapped_column(String(20), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    recording_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    direction: Mapped[str] = mapped_column(String(10), default="inbound")
    call_control_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="in_progress")


class CallLatency(Base):
    """Per-turn latency measurements collected from Pipecat MetricsFrame data."""
    __tablename__ = "call_latency"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    call_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("calls.id"), index=True)
    turn_index: Mapped[int] = mapped_column(Integer)
    stt_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    llm_ttft_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    tts_ttfb_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    e2e_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class Transcript(Base):
    __tablename__ = "transcripts"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    call_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("calls.id"))
    role: Mapped[str] = mapped_column(String(20))  # 'user' or 'assistant'
    content: Mapped[str] = mapped_column(Text)
    timestamp_ms: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
