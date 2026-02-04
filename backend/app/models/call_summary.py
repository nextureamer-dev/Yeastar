"""Call Summary model for storing AI-generated call summaries."""

from sqlalchemy import Column, Integer, String, Text, DateTime, JSON, ForeignKey, Boolean, Float
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

    # Call classification
    call_type = Column(String(50), nullable=True, index=True)  # visa_inquiry, emirates_id, company_setup, etc.
    service_category = Column(String(100), nullable=True, index=True)  # Amer Centre Services, Nexture Corporate Services
    service_subcategory = Column(String(200), nullable=True)  # Specific service like 'Golden Visa', 'Trade License Renewal'
    summary = Column(Text, nullable=True)

    # Staff information (extension-based)
    staff_name = Column(String(100), nullable=True, index=True)  # Staff member name
    staff_extension = Column(String(20), nullable=True, index=True)  # Extension number (201, 202, etc.)
    staff_department = Column(String(50), nullable=True, index=True)  # Call Centre, Sales, etc.
    staff_role = Column(String(100), nullable=True)  # Call Centre Agent, Sales Agent, etc.

    # Customer information
    customer_name = Column(String(100), nullable=True)  # Customer name if mentioned
    customer_phone = Column(String(50), nullable=True, index=True)  # Customer phone number
    company_name = Column(String(200), nullable=True)  # Customer's company (for corporate clients)

    # Conversation details
    topics_discussed = Column(JSON, nullable=True)  # List of topics
    customer_requests = Column(JSON, nullable=True)  # Customer's requests
    staff_responses = Column(JSON, nullable=True)  # How staff responded
    action_items = Column(JSON, nullable=True)  # List of action items
    commitments_made = Column(JSON, nullable=True)  # Promises made by staff
    resolution_status = Column(String(50), nullable=True, index=True)  # resolved, pending, escalated, etc.
    sentiment = Column(String(20), nullable=True, index=True)  # positive, neutral, negative (legacy)
    key_details = Column(JSON, nullable=True)  # application numbers, phone numbers, amounts, dates, etc.

    # Call classification for sales/business
    call_classification = Column(JSON, nullable=True)  # Sales opportunity, lead quality, etc.
    is_sales_opportunity = Column(Boolean, nullable=True, default=False, index=True)
    lead_quality = Column(String(20), nullable=True, index=True)  # hot, warm, cold
    estimated_deal_value = Column(Float, nullable=True)  # Estimated value in AED
    conversion_likelihood = Column(String(20), nullable=True)  # high, medium, low
    urgency_level = Column(String(50), nullable=True)  # immediate, within_week, within_month, no_urgency
    follow_up_required = Column(Boolean, nullable=True, default=False, index=True)
    follow_up_date = Column(DateTime, nullable=True)

    # Customer profile
    customer_profile = Column(JSON, nullable=True)  # Customer type, nationality, preferences
    customer_type = Column(String(50), nullable=True, index=True)  # individual, corporate, government, vip

    # Mood and sentiment analysis
    mood_sentiment_analysis = Column(JSON, nullable=True)  # Detailed mood/sentiment for customer and staff

    # Employee performance metrics
    employee_performance = Column(JSON, nullable=True)  # Performance assessment of staff
    professionalism_score = Column(Integer, nullable=True)  # 1-10 score
    knowledge_score = Column(Integer, nullable=True)  # 1-10 score
    communication_score = Column(Integer, nullable=True)  # 1-10 score
    empathy_score = Column(Integer, nullable=True)  # 1-10 score
    overall_performance_score = Column(Integer, nullable=True, index=True)  # 1-10 overall score

    # Compliance and quality
    compliance_check = Column(JSON, nullable=True)  # Compliance assessment
    call_quality_metrics = Column(JSON, nullable=True)  # Quality metrics
    first_call_resolution = Column(Boolean, nullable=True)  # Was issue resolved on first call?
    customer_effort_score = Column(String(20), nullable=True)  # low, medium, high

    # Legacy fields for backward compatibility
    services_discussed = Column(JSON, nullable=True)
    client_requirements = Column(JSON, nullable=True)
    caller_requests = Column(JSON, nullable=True)

    # Feedback fields
    feedback_rating = Column(Integer, nullable=True)  # 1=dislike, 2=like
    feedback_by = Column(String(100), nullable=True)  # username who gave feedback
    feedback_at = Column(DateTime(timezone=True), nullable=True)
    feedback_comment = Column(Text, nullable=True)  # optional comment with feedback

    # Processing info
    processing_time_seconds = Column(Integer, nullable=True)
    model_used = Column(String(50), nullable=True)
    error_message = Column(Text, nullable=True)

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
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

            # Call classification
            "call_type": self.call_type,
            "service_category": self.service_category,
            "service_subcategory": self.service_subcategory,
            "summary": self.summary,

            # Staff information
            "staff_name": self.staff_name,
            "staff_extension": self.staff_extension,
            "staff_department": self.staff_department,
            "staff_role": self.staff_role,

            # Customer information
            "customer_name": self.customer_name,
            "customer_phone": self.customer_phone,
            "company_name": self.company_name,

            # Conversation details
            "topics_discussed": self.topics_discussed or self.services_discussed,
            "customer_requests": self.customer_requests or self.caller_requests or self.client_requirements,
            "staff_responses": self.staff_responses,
            "action_items": self.action_items,
            "commitments_made": self.commitments_made,
            "resolution_status": self.resolution_status,
            "sentiment": overall_sentiment,
            "key_details": self.key_details,

            # Sales/Business classification
            "call_classification": self.call_classification,
            "is_sales_opportunity": self.is_sales_opportunity,
            "lead_quality": self.lead_quality,
            "estimated_deal_value": self.estimated_deal_value,
            "conversion_likelihood": self.conversion_likelihood,
            "urgency_level": self.urgency_level,
            "follow_up_required": self.follow_up_required,
            "follow_up_date": self.follow_up_date.isoformat() if self.follow_up_date else None,

            # Customer profile
            "customer_profile": self.customer_profile,
            "customer_type": self.customer_type,

            # Mood and sentiment
            "mood_sentiment_analysis": self.mood_sentiment_analysis,

            # Employee performance
            "employee_performance": self.employee_performance,
            "professionalism_score": self.professionalism_score,
            "knowledge_score": self.knowledge_score,
            "communication_score": self.communication_score,
            "empathy_score": self.empathy_score,
            "overall_performance_score": self.overall_performance_score,

            # Compliance and quality
            "compliance_check": self.compliance_check,
            "call_quality_metrics": self.call_quality_metrics,
            "first_call_resolution": self.first_call_resolution,
            "customer_effort_score": self.customer_effort_score,

            # Feedback
            "feedback_rating": self.feedback_rating,
            "feedback_by": self.feedback_by,
            "feedback_at": self.feedback_at.isoformat() if self.feedback_at else None,
            "feedback_comment": self.feedback_comment,

            # Processing info
            "processing_time_seconds": self.processing_time_seconds,
            "model_used": self.model_used,
            "error_message": self.error_message,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class SummaryNote(Base):
    """Model for user notes on AI summaries."""

    __tablename__ = "summary_notes"

    id = Column(Integer, primary_key=True, index=True)
    call_id = Column(String(100), ForeignKey('call_summaries.call_id'), index=True, nullable=False)
    content = Column(Text, nullable=False)
    created_by = Column(String(100), nullable=False)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at = Column(DateTime(timezone=True), onupdate=datetime.utcnow)

    def to_dict(self):
        """Convert to dictionary."""
        return {
            "id": self.id,
            "call_id": self.call_id,
            "content": self.content,
            "created_by": self.created_by,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
