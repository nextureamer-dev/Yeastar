"""Call Summary model for storing AI-generated call summaries."""

from sqlalchemy import Column, Integer, String, Text, DateTime, JSON, ForeignKey
from sqlalchemy.orm import relationship
from datetime import datetime

from app.database import Base


class CallSummary(Base):
    """Model for storing AI-generated call summaries."""

    __tablename__ = "call_summaries"

    id = Column(Integer, primary_key=True, index=True)
    call_id = Column(String(100), unique=True, index=True, nullable=False)  # Yeastar call UID
    recording_file = Column(String(255), nullable=True)

    # Transcription info
    language_detected = Column(String(20), nullable=True)
    transcript_preview = Column(Text, nullable=True)  # First 500 chars

    # Summary data (JSON)
    call_type = Column(String(50), nullable=True)  # visa_inquiry, emirates_id, company_setup, etc.
    service_category = Column(String(100), nullable=True)  # Amer Centre Services, Nexture Corporate Services
    summary = Column(Text, nullable=True)
    staff_name = Column(String(100), nullable=True)  # Staff member name if mentioned
    customer_name = Column(String(100), nullable=True)  # Customer name if mentioned
    company_name = Column(String(200), nullable=True)  # Customer's company (for corporate clients)
    topics_discussed = Column(JSON, nullable=True)  # List of topics
    customer_requests = Column(JSON, nullable=True)  # Customer's requests
    staff_responses = Column(JSON, nullable=True)  # How staff responded
    action_items = Column(JSON, nullable=True)  # List of action items
    resolution_status = Column(String(50), nullable=True)  # resolved, pending, escalated, etc.
    sentiment = Column(String(20), nullable=True)  # positive, neutral, negative (legacy)
    key_details = Column(JSON, nullable=True)  # application numbers, phone numbers, amounts, dates, etc.

    # Mood and sentiment analysis
    mood_sentiment_analysis = Column(JSON, nullable=True)  # Detailed mood/sentiment for customer and staff

    # Employee performance metrics
    employee_performance = Column(JSON, nullable=True)  # Performance assessment of staff

    # Legacy fields for backward compatibility
    services_discussed = Column(JSON, nullable=True)
    client_requirements = Column(JSON, nullable=True)
    caller_requests = Column(JSON, nullable=True)

    # Processing info
    processing_time_seconds = Column(Integer, nullable=True)
    model_used = Column(String(50), nullable=True)
    error_message = Column(Text, nullable=True)

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        """Convert to dictionary."""
        # Extract overall sentiment from mood analysis if available
        overall_sentiment = self.sentiment
        if self.mood_sentiment_analysis and isinstance(self.mood_sentiment_analysis, dict):
            overall_sentiment = self.mood_sentiment_analysis.get("overall_sentiment", self.sentiment)

        return {
            "id": self.id,
            "call_id": self.call_id,
            "recording_file": self.recording_file,
            "language_detected": self.language_detected,
            "transcript_preview": self.transcript_preview,
            "call_type": self.call_type,
            "service_category": self.service_category,
            "summary": self.summary,
            "staff_name": self.staff_name,
            "customer_name": self.customer_name,
            "company_name": self.company_name,
            "topics_discussed": self.topics_discussed or self.services_discussed,
            "customer_requests": self.customer_requests or self.caller_requests or self.client_requirements,
            "staff_responses": self.staff_responses,
            "action_items": self.action_items,
            "resolution_status": self.resolution_status,
            "sentiment": overall_sentiment,
            "key_details": self.key_details,
            "mood_sentiment_analysis": self.mood_sentiment_analysis,
            "employee_performance": self.employee_performance,
            "processing_time_seconds": self.processing_time_seconds,
            "error_message": self.error_message,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
