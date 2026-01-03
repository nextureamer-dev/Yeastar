from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, Enum
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.database import Base
import enum


class CallDirection(str, enum.Enum):
    INBOUND = "inbound"
    OUTBOUND = "outbound"
    INTERNAL = "internal"


class CallStatus(str, enum.Enum):
    ANSWERED = "answered"
    MISSED = "missed"
    BUSY = "busy"
    FAILED = "failed"
    NO_ANSWER = "no_answer"
    VOICEMAIL = "voicemail"


class CallLog(Base):
    __tablename__ = "call_logs"

    id = Column(Integer, primary_key=True, index=True)
    call_id = Column(String(100), unique=True, index=True)  # Yeastar call ID
    contact_id = Column(Integer, ForeignKey("contacts.id"), nullable=True)

    # Call details
    caller_number = Column(String(50), nullable=False, index=True)
    callee_number = Column(String(50), nullable=False, index=True)
    caller_name = Column(String(200), nullable=True)
    callee_name = Column(String(200), nullable=True)

    direction = Column(Enum(CallDirection), nullable=False)
    status = Column(Enum(CallStatus), nullable=False)

    # Extension info
    extension = Column(String(20), nullable=True)
    trunk = Column(String(100), nullable=True)

    # Timing
    start_time = Column(DateTime(timezone=True), nullable=False)
    answer_time = Column(DateTime(timezone=True), nullable=True)
    end_time = Column(DateTime(timezone=True), nullable=True)
    duration = Column(Integer, default=0)  # Duration in seconds
    ring_duration = Column(Integer, default=0)  # Ring duration in seconds

    # Recording
    recording_file = Column(String(500), nullable=True)

    # Notes
    notes = Column(Text, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Relationships
    contact = relationship("Contact", back_populates="call_logs")
