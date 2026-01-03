from sqlalchemy import Column, Integer, String, DateTime, Boolean, Enum
from sqlalchemy.sql import func
from app.database import Base
import enum


class ExtensionStatus(str, enum.Enum):
    AVAILABLE = "available"
    ON_CALL = "on_call"
    RINGING = "ringing"
    BUSY = "busy"
    DND = "dnd"
    OFFLINE = "offline"


class Extension(Base):
    __tablename__ = "extensions"

    id = Column(Integer, primary_key=True, index=True)
    extension_number = Column(String(20), unique=True, nullable=False, index=True)
    name = Column(String(200), nullable=True)
    email = Column(String(255), nullable=True)
    status = Column(Enum(ExtensionStatus), default=ExtensionStatus.OFFLINE)
    is_registered = Column(Boolean, default=False)

    # Current call info (updated in real-time)
    current_call_id = Column(String(100), nullable=True)
    current_caller = Column(String(100), nullable=True)

    last_seen = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
