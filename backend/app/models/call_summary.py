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

    # ==================== DEPARTMENT-WISE ANALYSIS FIELDS ====================

    # Star Rating (Universal - 1-5 scale)
    star_rating = Column(Integer, nullable=True, index=True)  # 1-5
    star_rating_justification = Column(Text, nullable=True)

    # Qualifier Department Fields
    qualifier_requirement_type = Column(String(50), nullable=True)  # specific, vague, none
    qualifier_timeline = Column(String(50), nullable=True, index=True)  # immediate, short_term, mid_term, long_term, no_timeline
    qualifier_decision_maker_status = Column(String(50), nullable=True)  # decision_maker, influencer, consultant, third_party
    qualifier_appointment_offered = Column(Boolean, nullable=True)
    qualifier_fail_reason = Column(String(100), nullable=True)  # just_checking, need_details_only, vague_inquiry
    qualifier_service_name = Column(String(200), nullable=True)  # Exact service mentioned
    qualifier_short_description = Column(Text, nullable=True)  # 1-line requirement description
    qualifier_expected_month = Column(String(20), nullable=True)  # For long-term timeline
    qualifier_decision_role = Column(String(50), nullable=True)  # decision_maker, influencer, consultant, third_party
    qualifier_availability = Column(Boolean, nullable=True)  # Yes/No availability
    qualifier_missing_fields = Column(JSON, nullable=True)  # List of missing mandatory fields

    # Sales Department Fields
    sales_sql_eligible = Column(Boolean, nullable=True, index=True)  # Only 2-5 star leads
    sales_notes_quality = Column(String(20), nullable=True)  # complete, partial, missing
    sales_exit_status = Column(String(50), nullable=True, index=True)  # converted, lost_competitor, lost_pricing, closed, unqualified
    sales_parking_status = Column(String(50), nullable=True)  # active, parked_with_plan, parked_no_plan
    sales_last_contact_days = Column(Integer, nullable=True)
    sales_next_action = Column(Text, nullable=True)  # Next action to take
    sales_qualification_reason = Column(Text, nullable=True)  # Why this rating
    sales_cadence_compliant = Column(Boolean, nullable=True)  # Follow-up cadence compliance

    # Call Center Department Fields
    cc_opening_compliant = Column(Boolean, nullable=True)  # Proper greeting compliance
    cc_opening_time_seconds = Column(Integer, nullable=True)  # Time to proper opening (<=20s target)
    cc_satisfaction_question_asked = Column(Boolean, nullable=True)  # "Is there anything else I can help with?"
    cc_customer_response = Column(String(20), nullable=True)  # positive, negative, unclear
    cc_call_category = Column(String(50), nullable=True, index=True)  # status, new_inquiry, document, office_info, complaint
    cc_whatsapp_handoff_valid = Column(Boolean, nullable=True)  # Valid WhatsApp handoff
    cc_premium_pitch_quality = Column(String(20), nullable=True)  # benefit_first, pushy, appropriate

    # Cross-Department Fields
    future_opportunities = Column(JSON, nullable=True)  # Auto-detected: Residency, Property, Business Expansion, etc.
    industry_interests = Column(JSON, nullable=True)  # Detected industries (20+ types)
    repeat_caller = Column(Boolean, nullable=True, index=True)  # Is this a repeat caller
    compliance_alerts = Column(JSON, nullable=True)  # List of compliance alerts
    sla_breach = Column(Boolean, nullable=True, index=True)  # SLA breach indicator
    talk_time_ratio = Column(Float, nullable=True)  # Staff vs customer talk time ratio
    greeting_compliant = Column(Boolean, nullable=True)  # Greeting compliance
    duration_anomaly = Column(Boolean, nullable=True)  # Call duration anomaly flag
    handoff_quality = Column(String(20), nullable=True)  # good, poor, none

    # Full department analysis JSON (flexible storage)
    department_analysis = Column(JSON, nullable=True)

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

            # Department-wise analysis
            "star_rating": self.star_rating,
            "star_rating_justification": self.star_rating_justification,

            # Qualifier fields
            "qualifier_requirement_type": self.qualifier_requirement_type,
            "qualifier_timeline": self.qualifier_timeline,
            "qualifier_decision_maker_status": self.qualifier_decision_maker_status,
            "qualifier_appointment_offered": self.qualifier_appointment_offered,
            "qualifier_fail_reason": self.qualifier_fail_reason,
            "qualifier_service_name": self.qualifier_service_name,
            "qualifier_short_description": self.qualifier_short_description,
            "qualifier_expected_month": self.qualifier_expected_month,
            "qualifier_decision_role": self.qualifier_decision_role,
            "qualifier_availability": self.qualifier_availability,
            "qualifier_missing_fields": self.qualifier_missing_fields,

            # Sales fields
            "sales_sql_eligible": self.sales_sql_eligible,
            "sales_notes_quality": self.sales_notes_quality,
            "sales_exit_status": self.sales_exit_status,
            "sales_parking_status": self.sales_parking_status,
            "sales_last_contact_days": self.sales_last_contact_days,
            "sales_next_action": self.sales_next_action,
            "sales_qualification_reason": self.sales_qualification_reason,
            "sales_cadence_compliant": self.sales_cadence_compliant,

            # Call Center fields
            "cc_opening_compliant": self.cc_opening_compliant,
            "cc_opening_time_seconds": self.cc_opening_time_seconds,
            "cc_satisfaction_question_asked": self.cc_satisfaction_question_asked,
            "cc_customer_response": self.cc_customer_response,
            "cc_call_category": self.cc_call_category,
            "cc_whatsapp_handoff_valid": self.cc_whatsapp_handoff_valid,
            "cc_premium_pitch_quality": self.cc_premium_pitch_quality,

            # Cross-department fields
            "future_opportunities": self.future_opportunities,
            "industry_interests": self.industry_interests,
            "repeat_caller": self.repeat_caller,
            "compliance_alerts": self.compliance_alerts,
            "sla_breach": self.sla_breach,
            "talk_time_ratio": self.talk_time_ratio,
            "greeting_compliant": self.greeting_compliant,
            "duration_anomaly": self.duration_anomaly,
            "handoff_quality": self.handoff_quality,
            "department_analysis": self.department_analysis,

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


class FollowUpTracking(Base):
    """Model for tracking sales follow-up cadence."""

    __tablename__ = "follow_up_tracking"

    id = Column(Integer, primary_key=True, index=True)
    call_id = Column(String(100), ForeignKey('call_summaries.call_id'), index=True, nullable=False)
    customer_phone = Column(String(50), index=True, nullable=False)
    staff_extension = Column(String(20), index=True, nullable=True)
    staff_name = Column(String(100), nullable=True)
    star_rating = Column(Integer, nullable=True)  # 1-5

    # Follow-up tracking
    last_contact_date = Column(DateTime, nullable=True, index=True)
    next_follow_up_date = Column(DateTime, nullable=True, index=True)
    days_since_contact = Column(Integer, nullable=True)
    follow_up_status = Column(String(50), nullable=True)  # on_track, overdue_25, overdue_30, overdue_50, overdue_60

    # Cadence thresholds (in days)
    # 5-star: every 10 days, 4-star: every 15 days, 3-star: every 20 days, 2-star: every 30 days
    cadence_threshold = Column(Integer, nullable=True)
    is_overdue = Column(Boolean, default=False, index=True)

    # Status
    status = Column(String(50), default='active')  # active, parked, converted, lost, closed
    parking_reason = Column(Text, nullable=True)
    exit_reason = Column(String(100), nullable=True)  # lost_competitor, lost_pricing, closed, unqualified

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        """Convert to dictionary."""
        return {
            "id": self.id,
            "call_id": self.call_id,
            "customer_phone": self.customer_phone,
            "staff_extension": self.staff_extension,
            "staff_name": self.staff_name,
            "star_rating": self.star_rating,
            "last_contact_date": self.last_contact_date.isoformat() if self.last_contact_date else None,
            "next_follow_up_date": self.next_follow_up_date.isoformat() if self.next_follow_up_date else None,
            "days_since_contact": self.days_since_contact,
            "follow_up_status": self.follow_up_status,
            "cadence_threshold": self.cadence_threshold,
            "is_overdue": self.is_overdue,
            "status": self.status,
            "parking_reason": self.parking_reason,
            "exit_reason": self.exit_reason,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class SLATracking(Base):
    """Model for tracking call center SLA metrics."""

    __tablename__ = "sla_tracking"

    id = Column(Integer, primary_key=True, index=True)
    call_id = Column(String(100), ForeignKey('call_summaries.call_id'), index=True, nullable=False)
    customer_phone = Column(String(50), index=True, nullable=False)
    staff_extension = Column(String(20), index=True, nullable=True)
    staff_name = Column(String(100), nullable=True)

    # SLA metrics
    call_date = Column(DateTime, nullable=True, index=True)
    response_time_seconds = Column(Integer, nullable=True)  # Time to answer
    opening_time_seconds = Column(Integer, nullable=True)  # Time to proper greeting
    resolution_time_seconds = Column(Integer, nullable=True)  # Total call handling time

    # SLA compliance
    answer_sla_met = Column(Boolean, nullable=True)  # Answered within target (e.g., 20 seconds)
    opening_sla_met = Column(Boolean, nullable=True)  # Proper greeting within target
    resolution_sla_met = Column(Boolean, nullable=True)  # Resolved within target time

    # Call quality indicators
    satisfaction_asked = Column(Boolean, nullable=True)
    customer_satisfied = Column(Boolean, nullable=True)
    first_call_resolution = Column(Boolean, nullable=True)

    # Breach tracking
    sla_breached = Column(Boolean, default=False, index=True)
    breach_type = Column(String(50), nullable=True)  # answer, opening, resolution
    breach_duration_seconds = Column(Integer, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        """Convert to dictionary."""
        return {
            "id": self.id,
            "call_id": self.call_id,
            "customer_phone": self.customer_phone,
            "staff_extension": self.staff_extension,
            "staff_name": self.staff_name,
            "call_date": self.call_date.isoformat() if self.call_date else None,
            "response_time_seconds": self.response_time_seconds,
            "opening_time_seconds": self.opening_time_seconds,
            "resolution_time_seconds": self.resolution_time_seconds,
            "answer_sla_met": self.answer_sla_met,
            "opening_sla_met": self.opening_sla_met,
            "resolution_sla_met": self.resolution_sla_met,
            "satisfaction_asked": self.satisfaction_asked,
            "customer_satisfied": self.customer_satisfied,
            "first_call_resolution": self.first_call_resolution,
            "sla_breached": self.sla_breached,
            "breach_type": self.breach_type,
            "breach_duration_seconds": self.breach_duration_seconds,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
