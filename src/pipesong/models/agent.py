import uuid
from datetime import datetime, timezone

from sqlalchemy import String, Text
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
    created_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(timezone.utc))
