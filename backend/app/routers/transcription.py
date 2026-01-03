"""API routes for AI transcription and summarization."""

from fastapi import APIRouter, HTTPException, Query, BackgroundTasks, Depends
from sqlalchemy.orm import Session
from sqlalchemy import func
from typing import Optional
from datetime import datetime, timedelta
import tempfile
import os
import httpx
import logging

from app.database import get_db
from app.models.call_summary import CallSummary
from app.services.ai_transcription import get_ai_service
from app.services.yeastar_client import get_yeastar_client

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/transcription", tags=["transcription"])


@router.get("/status")
async def get_ai_status():
    """Check AI service status (Riva NIM and Ollama/Llama 3 70B)."""
    from app.config import get_settings
    settings = get_settings()

    ai_service = get_ai_service()

    # Check full status (ASR + LLM)
    status = await ai_service.check_status()

    return {
        "riva_nim": {
            "server": status.get("asr_server"),
            "ready": status.get("asr_ready"),
            "engine": status.get("asr_engine"),
        },
        "llm": status.get("llm"),
        "auto_processing": {
            "enabled": settings.auto_process_calls,
            "internal_calls": settings.process_internal_calls,
        },
        "ready": status.get("ready", False),
    }


@router.post("/process/{call_id}")
async def process_call_recording(
    call_id: str,
    background_tasks: BackgroundTasks,
    force: bool = Query(False, description="Force reprocess even if summary exists"),
    recording_file: Optional[str] = Query(None, description="Recording filename from Yeastar"),
    db: Session = Depends(get_db),
):
    """
    Process a call recording: transcribe and summarize.

    This downloads the recording from Yeastar, transcribes it with NVIDIA Riva NIM,
    and generates a detailed analysis using Llama 3 70B.
    """
    # Check if already processed
    existing = db.query(CallSummary).filter(CallSummary.call_id == call_id).first()
    if existing and not force:
        return {
            "status": "already_processed",
            "summary": existing.to_dict()
        }

    # Queue for background processing
    background_tasks.add_task(
        _process_recording_task,
        call_id=call_id,
        recording_file=recording_file,
        force=force,
    )

    return {
        "status": "processing",
        "message": "Recording queued for transcription and summarization",
        "call_id": call_id
    }


@router.get("/summary/{call_id}")
async def get_call_summary(
    call_id: str,
    db: Session = Depends(get_db),
):
    """Get the AI-generated summary for a call."""
    summary = db.query(CallSummary).filter(CallSummary.call_id == call_id).first()

    if not summary:
        raise HTTPException(status_code=404, detail="Summary not found. Process the recording first.")

    return summary.to_dict()


@router.get("/summaries")
async def list_summaries(
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    call_type: Optional[str] = Query(None),
    sentiment: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    """List all call summaries with filtering."""
    query = db.query(CallSummary)

    if call_type:
        query = query.filter(CallSummary.call_type == call_type)
    if sentiment:
        query = query.filter(CallSummary.sentiment == sentiment)

    total = query.count()
    summaries = query.order_by(CallSummary.created_at.desc())\
        .offset((page - 1) * per_page)\
        .limit(per_page)\
        .all()

    return {
        "summaries": [s.to_dict() for s in summaries],
        "total": total,
        "page": page,
        "per_page": per_page,
    }


@router.get("/analytics")
async def get_call_analytics(
    days: int = Query(30, ge=1, le=365, description="Number of days to analyze"),
    db: Session = Depends(get_db),
):
    """Get analytics from AI-processed call summaries."""
    from datetime import datetime, timedelta

    # Filter by date range
    cutoff_date = datetime.utcnow() - timedelta(days=days)
    base_query = db.query(CallSummary).filter(
        CallSummary.created_at >= cutoff_date,
        CallSummary.error_message.is_(None)  # Only successful summaries
    )

    total_analyzed = base_query.count()

    # Count by call type
    call_type_counts = db.query(
        CallSummary.call_type,
        func.count(CallSummary.id).label('count')
    ).filter(
        CallSummary.created_at >= cutoff_date,
        CallSummary.error_message.is_(None),
        CallSummary.call_type.isnot(None)
    ).group_by(CallSummary.call_type).all()

    # Count by sentiment
    sentiment_counts = db.query(
        CallSummary.sentiment,
        func.count(CallSummary.id).label('count')
    ).filter(
        CallSummary.created_at >= cutoff_date,
        CallSummary.error_message.is_(None),
        CallSummary.sentiment.isnot(None)
    ).group_by(CallSummary.sentiment).all()

    # Map call types to business categories
    type_mapping = {
        'inquiry': 'Enquiry',
        'consultation': 'Enquiry',
        'sales': 'Business Opportunity',
        'follow_up': 'Follow Up',
        'complaint': 'Complaint',
        'support': 'Support',
        'internal': 'Internal',
        'other': 'Other',
    }

    # Aggregate into business categories
    categories = {}
    for call_type, count in call_type_counts:
        if call_type:
            # Handle pipe-separated types like "application|support"
            for t in call_type.split('|'):
                t = t.strip().lower()
                category = type_mapping.get(t, 'Other')
                categories[category] = categories.get(category, 0) + count

    # Get recent summaries for display
    recent_summaries = base_query.order_by(CallSummary.created_at.desc()).limit(5).all()

    return {
        "period_days": days,
        "total_analyzed": total_analyzed,
        "by_category": categories,
        "by_call_type": {ct: count for ct, count in call_type_counts if ct},
        "by_sentiment": {s: count for s, count in sentiment_counts if s},
        "recent_summaries": [
            {
                "call_id": s.call_id,
                "call_type": s.call_type,
                "summary": s.summary,
                "sentiment": s.sentiment,
                "created_at": s.created_at.isoformat() if s.created_at else None,
            }
            for s in recent_summaries
        ]
    }


@router.get("/deep-analytics")
async def get_deep_analytics(
    days: int = Query(30, ge=1, le=365, description="Number of days to analyze"),
    db: Session = Depends(get_db),
):
    """
    Get comprehensive analytics from AI-processed call summaries.

    Includes keywords, requirements, complaints, issues, staff performance,
    and sentiment trends for maximum business insights.
    """
    import re
    from collections import Counter
    from sqlalchemy import or_, and_, cast, String

    cutoff_date = datetime.utcnow() - timedelta(days=days)

    # Get all successful summaries in date range
    summaries = db.query(CallSummary).filter(
        CallSummary.created_at >= cutoff_date,
        CallSummary.error_message.is_(None)
    ).order_by(CallSummary.created_at.desc()).all()

    total_analyzed = len(summaries)

    if total_analyzed == 0:
        return {
            "period": {"days": days, "start_date": cutoff_date.isoformat(), "end_date": datetime.utcnow().isoformat()},
            "overview": {"total_calls_analyzed": 0},
            "keywords": {"top_keywords": [], "by_category": {}},
            "requirements": {"top_requests": [], "unresolved": []},
            "complaints": {"total": 0, "top_issues": [], "by_service": {}},
            "business_issues": {"delays": [], "escalations": 0, "pending_actions": []},
            "staff_performance": [],
            "sentiment_trends": {"daily": [], "distribution": {}},
            "resolution_stats": {}
        }

    # --- KEYWORD EXTRACTION ---
    stopwords = {'the', 'a', 'an', 'is', 'are', 'was', 'were', 'be', 'been', 'being',
                 'have', 'has', 'had', 'do', 'does', 'did', 'will', 'would', 'could',
                 'should', 'may', 'might', 'must', 'shall', 'can', 'need', 'dare',
                 'to', 'of', 'in', 'for', 'on', 'with', 'at', 'by', 'from', 'as',
                 'into', 'through', 'during', 'before', 'after', 'above', 'below',
                 'between', 'under', 'again', 'further', 'then', 'once', 'here',
                 'there', 'when', 'where', 'why', 'how', 'all', 'each', 'few',
                 'more', 'most', 'other', 'some', 'such', 'no', 'nor', 'not',
                 'only', 'own', 'same', 'so', 'than', 'too', 'very', 'just',
                 'and', 'but', 'if', 'or', 'because', 'until', 'while', 'about',
                 'this', 'that', 'these', 'those', 'am', 'i', 'you', 'he', 'she',
                 'it', 'we', 'they', 'what', 'which', 'who', 'whom', 'call',
                 'caller', 'customer', 'staff', 'regarding', 'also', 'like'}

    all_keywords = []
    keyword_sentiments = {}
    topics_counter = Counter()
    requests_counter = Counter()

    for s in summaries:
        sentiment_val = 1 if s.sentiment == 'positive' else (-1 if s.sentiment == 'negative' else 0)

        # Extract from topics_discussed
        if s.topics_discussed:
            for topic in s.topics_discussed:
                topic_lower = topic.lower().strip()
                topics_counter[topic_lower] += 1
                words = re.findall(r'\b[a-zA-Z]{3,}\b', topic_lower)
                for word in words:
                    if word not in stopwords:
                        all_keywords.append(word)
                        keyword_sentiments.setdefault(word, []).append(sentiment_val)

        # Extract from customer_requests
        if s.customer_requests:
            for req in s.customer_requests:
                req_lower = req.lower().strip()
                requests_counter[req_lower] += 1
                words = re.findall(r'\b[a-zA-Z]{3,}\b', req_lower)
                for word in words:
                    if word not in stopwords:
                        all_keywords.append(word)
                        keyword_sentiments.setdefault(word, []).append(sentiment_val)

        # Extract from summary text
        if s.summary:
            words = re.findall(r'\b[a-zA-Z]{3,}\b', s.summary.lower())
            for word in words:
                if word not in stopwords:
                    all_keywords.append(word)
                    keyword_sentiments.setdefault(word, []).append(sentiment_val)

    # Count and rank keywords
    keyword_counts = Counter(all_keywords)
    top_keywords = []
    for word, count in keyword_counts.most_common(50):
        sentiments = keyword_sentiments.get(word, [0])
        avg_sentiment = sum(sentiments) / len(sentiments) if sentiments else 0
        top_keywords.append({
            "word": word,
            "count": count,
            "sentiment_avg": round(avg_sentiment, 2)
        })

    # --- REQUIREMENTS ANALYSIS ---
    top_requests = []
    for req, count in requests_counter.most_common(20):
        # Calculate resolution rate for this request type
        resolved = sum(1 for s in summaries
                      if s.customer_requests and req in [r.lower().strip() for r in s.customer_requests]
                      and s.resolution_status in ['resolved', 'completed'])
        total_with_req = sum(1 for s in summaries
                            if s.customer_requests and req in [r.lower().strip() for r in s.customer_requests])
        resolution_rate = round((resolved / total_with_req * 100) if total_with_req > 0 else 0, 1)
        top_requests.append({
            "request": req.title(),
            "count": count,
            "resolution_rate": resolution_rate
        })

    # Unresolved requests
    unresolved = []
    for s in summaries:
        if s.resolution_status in ['pending', 'unresolved', 'escalated'] and s.customer_requests:
            for req in s.customer_requests[:2]:  # First 2 requests
                unresolved.append({
                    "request": req,
                    "call_id": s.call_id,
                    "date": s.created_at.strftime("%Y-%m-%d") if s.created_at else None,
                    "status": s.resolution_status
                })

    # --- COMPLAINTS ANALYSIS ---
    complaints = [s for s in summaries if s.call_type == 'complaint' or s.sentiment == 'negative']
    complaint_issues = Counter()
    complaints_by_service = Counter()

    for s in complaints:
        # Extract issue patterns
        if s.summary:
            summary_lower = s.summary.lower()
            if 'delay' in summary_lower or 'late' in summary_lower or 'slow' in summary_lower:
                complaint_issues['Processing Delays'] += 1
            if 'communication' in summary_lower or 'response' in summary_lower or 'callback' in summary_lower:
                complaint_issues['Communication Issues'] += 1
            if 'document' in summary_lower or 'paperwork' in summary_lower:
                complaint_issues['Document Issues'] += 1
            if 'payment' in summary_lower or 'fee' in summary_lower or 'charge' in summary_lower:
                complaint_issues['Payment/Billing Issues'] += 1
            if 'error' in summary_lower or 'mistake' in summary_lower or 'wrong' in summary_lower:
                complaint_issues['Errors/Mistakes'] += 1
            if 'waiting' in summary_lower or 'long' in summary_lower:
                complaint_issues['Long Wait Times'] += 1

        # By service category
        if s.service_category:
            complaints_by_service[s.service_category] += 1
        elif s.topics_discussed:
            complaints_by_service[s.topics_discussed[0] if s.topics_discussed else 'Other'] += 1

    top_issues = [{"issue": issue, "count": count, "resolved": 0}
                  for issue, count in complaint_issues.most_common(10)]

    # --- BUSINESS ISSUES (Delays, Escalations, Pending Actions) ---
    delay_patterns = ['delay', 'delayed', 'waiting', 'pending', 'slow', 'late', 'overdue', 'stuck']
    delays = Counter()

    for s in summaries:
        if s.summary:
            summary_lower = s.summary.lower()
            for pattern in delay_patterns:
                if pattern in summary_lower:
                    if 'visa' in summary_lower:
                        delays['Visa Processing Delay'] += 1
                    elif 'document' in summary_lower:
                        delays['Document Processing Delay'] += 1
                    elif 'payment' in summary_lower:
                        delays['Payment Processing Delay'] += 1
                    elif 'application' in summary_lower:
                        delays['Application Processing Delay'] += 1
                    else:
                        delays['General Processing Delay'] += 1
                    break

    delay_list = [{"type": dtype, "mentions": count} for dtype, count in delays.most_common(10)]

    # Escalations
    escalations = sum(1 for s in summaries if s.resolution_status == 'escalated')

    # Pending actions
    pending_actions = []
    for s in summaries:
        if s.action_items and s.resolution_status in ['pending', 'unresolved']:
            for action in s.action_items[:1]:  # First action item
                days_pending = (datetime.utcnow() - s.created_at).days if s.created_at else 0
                pending_actions.append({
                    "action": action,
                    "call_id": s.call_id,
                    "days_pending": days_pending,
                    "customer": s.customer_name or "Unknown"
                })
    pending_actions = sorted(pending_actions, key=lambda x: x['days_pending'], reverse=True)[:20]

    # --- STAFF PERFORMANCE ---
    staff_stats = {}
    for s in summaries:
        staff = s.staff_name or "Unknown"
        if staff not in staff_stats:
            staff_stats[staff] = {
                "name": staff,
                "calls": 0,
                "sentiments": [],
                "resolved": 0,
                "total_for_resolution": 0
            }
        staff_stats[staff]["calls"] += 1
        if s.sentiment:
            sent_val = 1 if s.sentiment == 'positive' else (-1 if s.sentiment == 'negative' else 0)
            staff_stats[staff]["sentiments"].append(sent_val)
        if s.resolution_status:
            staff_stats[staff]["total_for_resolution"] += 1
            if s.resolution_status in ['resolved', 'completed']:
                staff_stats[staff]["resolved"] += 1

    staff_performance = []
    for staff, stats in staff_stats.items():
        if staff == "Unknown":
            continue
        avg_sentiment = sum(stats["sentiments"]) / len(stats["sentiments"]) if stats["sentiments"] else 0
        resolution_rate = (stats["resolved"] / stats["total_for_resolution"] * 100) if stats["total_for_resolution"] > 0 else 0
        staff_performance.append({
            "name": staff,
            "calls": stats["calls"],
            "avg_sentiment": round(avg_sentiment, 2),
            "resolution_rate": round(resolution_rate, 1)
        })
    staff_performance = sorted(staff_performance, key=lambda x: x['calls'], reverse=True)[:15]

    # --- SENTIMENT TRENDS ---
    daily_sentiment = {}
    for s in summaries:
        if s.created_at and s.sentiment:
            date_str = s.created_at.strftime("%Y-%m-%d")
            if date_str not in daily_sentiment:
                daily_sentiment[date_str] = {"positive": 0, "neutral": 0, "negative": 0}
            daily_sentiment[date_str][s.sentiment] = daily_sentiment[date_str].get(s.sentiment, 0) + 1

    sentiment_daily = [{"date": d, **counts} for d, counts in sorted(daily_sentiment.items())]

    # Sentiment distribution
    sentiment_dist = Counter(s.sentiment for s in summaries if s.sentiment)

    # --- RESOLUTION STATS ---
    resolution_counts = Counter(s.resolution_status for s in summaries if s.resolution_status)
    total_with_status = sum(resolution_counts.values())
    resolution_rate = round((resolution_counts.get('resolved', 0) + resolution_counts.get('completed', 0)) / total_with_status * 100, 1) if total_with_status > 0 else 0

    # --- TOP TOPICS ---
    top_topics = [{"topic": t.title(), "count": c} for t, c in topics_counter.most_common(15)]

    return {
        "period": {
            "days": days,
            "start_date": cutoff_date.isoformat(),
            "end_date": datetime.utcnow().isoformat()
        },
        "overview": {
            "total_calls_analyzed": total_analyzed,
            "avg_sentiment_score": round(sum(1 if s.sentiment == 'positive' else (-1 if s.sentiment == 'negative' else 0) for s in summaries) / total_analyzed, 2) if total_analyzed > 0 else 0,
            "resolution_rate": resolution_rate,
            "complaints_count": len(complaints),
            "escalations_count": escalations
        },
        "keywords": {
            "top_keywords": top_keywords,
            "top_topics": top_topics
        },
        "requirements": {
            "top_requests": top_requests,
            "unresolved": unresolved[:15]
        },
        "complaints": {
            "total": len(complaints),
            "top_issues": top_issues,
            "by_service": dict(complaints_by_service.most_common(10))
        },
        "business_issues": {
            "delays": delay_list,
            "escalations": escalations,
            "pending_actions": pending_actions
        },
        "staff_performance": staff_performance,
        "sentiment_trends": {
            "daily": sentiment_daily[-30:],  # Last 30 days
            "distribution": dict(sentiment_dist)
        },
        "resolution_stats": {
            "by_status": dict(resolution_counts),
            "resolution_rate": resolution_rate
        }
    }


@router.get("/by-category/{category}")
async def get_summaries_by_category(
    category: str,
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    days: int = Query(30, ge=1, le=365),
    db: Session = Depends(get_db),
):
    """Get all summaries for a specific business category."""
    from datetime import datetime, timedelta

    # Map category to call types
    category_to_types = {
        'Enquiry': ['inquiry', 'consultation'],
        'Business Opportunity': ['sales'],
        'Follow Up': ['follow_up'],
        'Complaint': ['complaint'],
        'Support': ['support'],
        'Internal': ['internal'],
        'Other': ['other'],
    }

    call_types = category_to_types.get(category, [category.lower()])
    cutoff_date = datetime.utcnow() - timedelta(days=days)

    # Query summaries matching any of the call types
    query = db.query(CallSummary).filter(
        CallSummary.created_at >= cutoff_date,
        CallSummary.error_message.is_(None),
    )

    # Filter by call types (including pipe-separated ones)
    from sqlalchemy import or_
    type_filters = []
    for ct in call_types:
        type_filters.append(CallSummary.call_type == ct)
        type_filters.append(CallSummary.call_type.like(f'%{ct}%'))

    query = query.filter(or_(*type_filters))

    total = query.count()
    summaries = query.order_by(CallSummary.created_at.desc())\
        .offset((page - 1) * per_page)\
        .limit(per_page)\
        .all()

    return {
        "category": category,
        "summaries": [s.to_dict() for s in summaries],
        "total": total,
        "page": page,
        "per_page": per_page,
        "total_pages": (total + per_page - 1) // per_page,
    }


@router.get("/by-sentiment/{sentiment}")
async def get_summaries_by_sentiment(
    sentiment: str,
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    days: int = Query(30, ge=1, le=365),
    db: Session = Depends(get_db),
):
    """Get all summaries for a specific sentiment."""
    from datetime import datetime, timedelta

    cutoff_date = datetime.utcnow() - timedelta(days=days)

    query = db.query(CallSummary).filter(
        CallSummary.created_at >= cutoff_date,
        CallSummary.error_message.is_(None),
        CallSummary.sentiment == sentiment.lower(),
    )

    total = query.count()
    summaries = query.order_by(CallSummary.created_at.desc())\
        .offset((page - 1) * per_page)\
        .limit(per_page)\
        .all()

    return {
        "sentiment": sentiment,
        "summaries": [s.to_dict() for s in summaries],
        "total": total,
        "page": page,
        "per_page": per_page,
        "total_pages": (total + per_page - 1) // per_page,
    }


@router.post("/analyze-contact")
async def analyze_contact_history(
    phone_number: str = Query(..., description="Phone number to analyze"),
    db: Session = Depends(get_db),
):
    """
    Analyze all AI summaries for a contact/phone number and generate
    a comprehensive issue summary using the LLM.
    """
    from app.models.call_log import CallLog

    # Find all call IDs for this phone number
    call_logs = db.query(CallLog).filter(
        (CallLog.caller_number.like(f'%{phone_number[-9:]}%')) |
        (CallLog.callee_number.like(f'%{phone_number[-9:]}%'))
    ).all()

    call_ids = [c.call_id for c in call_logs]

    # Get all summaries for these calls
    summaries = db.query(CallSummary).filter(
        CallSummary.call_id.in_(call_ids),
        CallSummary.error_message.is_(None),
    ).order_by(CallSummary.created_at.desc()).all()

    if not summaries:
        raise HTTPException(status_code=404, detail="No AI summaries found for this contact")

    # Prepare context for LLM analysis
    summaries_text = []
    for s in summaries:
        date_str = s.created_at.strftime("%Y-%m-%d %H:%M") if s.created_at else "Unknown date"
        summaries_text.append(f"""
Call Date: {date_str}
Type: {s.call_type or 'Unknown'}
Sentiment: {s.sentiment or 'Unknown'}
Summary: {s.summary or 'No summary'}
Customer Requests: {', '.join(s.customer_requests) if s.customer_requests else 'None'}
Staff Responses: {', '.join(s.staff_responses) if s.staff_responses else 'None'}
Action Items: {', '.join(s.action_items) if s.action_items else 'None'}
---""")

    combined_summaries = "\n".join(summaries_text)

    # Use LLM to analyze
    ai_service = get_ai_service()

    analysis_prompt = f"""Analyze the following call history for a customer and provide a comprehensive summary.

CALL HISTORY:
{combined_summaries}

Based on ALL the calls above, provide a JSON response with:
{{
    "total_interactions": <number of calls>,
    "customer_profile": "Brief description of who this customer is based on calls",
    "main_issues": ["List of main issues/concerns raised across all calls"],
    "resolution_status": "resolved|partially_resolved|unresolved|ongoing",
    "overall_sentiment_trend": "improving|stable|declining",
    "key_requests": ["Important requests made by customer"],
    "actions_taken": ["Actions staff have taken"],
    "pending_actions": ["Any unresolved action items"],
    "recommendation": "What should be done next for this customer",
    "priority_level": "high|medium|low",
    "comprehensive_summary": "2-3 paragraph summary of complete customer interaction history"
}}

Return ONLY valid JSON, no other text."""

    try:
        analysis_result = await ai_service.call_ollama(analysis_prompt)

        # Parse JSON from response
        import json
        import re

        # Try to extract JSON from response
        json_match = re.search(r'\{[\s\S]*\}', analysis_result)
        if json_match:
            analysis = json.loads(json_match.group())
        else:
            analysis = {"error": "Failed to parse analysis", "raw_response": analysis_result}

    except Exception as e:
        logger.error(f"Failed to analyze contact history: {e}")
        analysis = {"error": str(e)}

    return {
        "phone_number": phone_number,
        "total_calls_analyzed": len(summaries),
        "call_summaries": [s.to_dict() for s in summaries],
        "analysis": analysis,
    }


@router.post("/batch-process")
async def batch_process_recordings(
    background_tasks: BackgroundTasks,
    limit: int = Query(10, ge=1, le=50, description="Number of recordings to process"),
    db: Session = Depends(get_db),
):
    """
    Process multiple unprocessed recordings in batch.

    Fetches recent calls with recordings and processes any that don't have summaries.
    """
    # Get recent calls with recordings from Yeastar
    client = get_yeastar_client()
    result = await client.get_cdr_list(page=1, page_size=limit * 2)

    if not result or result.get("errcode") != 0:
        raise HTTPException(status_code=500, detail="Failed to fetch call list")

    cdrs = result.get("data", [])

    # Filter calls with recordings that haven't been processed
    to_process = []
    seen_call_ids = set()  # Prevent duplicates

    for cdr in cdrs:
        # CDR uses "recording" field, not "record_file"
        recording = cdr.get("recording") or cdr.get("record_file")
        call_id = cdr.get("uid")

        # Skip if no recording, no call_id, or already seen
        if not recording or not call_id or call_id in seen_call_ids:
            continue

        seen_call_ids.add(call_id)

        # Skip if already processed
        existing = db.query(CallSummary).filter(CallSummary.call_id == call_id).first()
        if not existing:
            to_process.append({
                "call_id": call_id,
                "recording_file": recording
            })

        if len(to_process) >= limit:
            break

    # Queue all for processing
    for item in to_process:
        background_tasks.add_task(
            _process_recording_task,
            call_id=item["call_id"],
            recording_file=item["recording_file"],
            force=False,
        )

    return {
        "status": "queued",
        "count": len(to_process),
        "call_ids": [item["call_id"] for item in to_process]
    }


@router.post("/process-historical")
async def process_historical_calls(
    background_tasks: BackgroundTasks,
    days: int = Query(None, ge=1, le=365, description="Number of days to look back"),
    hours: int = Query(None, ge=1, le=720, description="Number of hours to look back (alternative to days)"),
    start_time: str = Query(None, description="Specific start time (format: YYYY-MM-DD HH:MM)"),
    end_time: str = Query(None, description="Specific end time (format: YYYY-MM-DD HH:MM)"),
    force: bool = Query(False, description="Force reprocess even if summary exists"),
    db: Session = Depends(get_db),
):
    """
    Process all INBOUND and OUTBOUND calls from the specified period.

    This fetches ALL CDRs from the specified time range, filters for:
    - Only INBOUND and OUTBOUND calls (skips INTERNAL)
    - Only ANSWERED calls with recordings
    - Skips already processed calls (unless force=True)

    Use this to backfill transcription/analysis for historical calls.
    """
    client = get_yeastar_client()

    # Calculate date range - specific times take precedence
    if start_time and end_time:
        try:
            start_date = datetime.strptime(start_time, "%Y-%m-%d %H:%M")
            end_date = datetime.strptime(end_time, "%Y-%m-%d %H:%M")
            period_desc = f"{start_time} to {end_time}"
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid time format. Use: YYYY-MM-DD HH:MM")
    elif hours:
        end_date = datetime.now()
        start_date = end_date - timedelta(hours=hours)
        period_desc = f"{hours} hours"
    elif days:
        end_date = datetime.now()
        start_date = end_date - timedelta(days=days)
        period_desc = f"{days} days"
    else:
        end_date = datetime.now()
        start_date = end_date - timedelta(hours=24)
        period_desc = "24 hours"

    logger.info(f"Processing historical calls from {start_date} to {end_date}")

    # Fetch all CDRs page by page
    all_cdrs = []
    page = 1
    page_size = 100
    max_pages = 600  # Up to 60,000 records

    while page <= max_pages:
        result = await client.get_cdr_list(
            page=page,
            page_size=page_size,
            sort_by="time",
            order_by="desc",
        )

        if not result or result.get("errcode") != 0:
            logger.warning(f"Failed to fetch page {page}: {result}")
            break

        cdrs = result.get("data", [])
        if not cdrs:
            break

        all_cdrs.extend(cdrs)

        # Check if we've gone past our date range
        last_cdr = cdrs[-1]
        last_time_str = last_cdr.get("time", "")
        if last_time_str:
            # Parse Cloud PBX time format
            last_time = _parse_cloud_time(last_time_str)
            if last_time and last_time < start_date:
                logger.info(f"Reached calls before start date at page {page}")
                break

        if len(cdrs) < page_size:
            break

        page += 1

    logger.info(f"Fetched {len(all_cdrs)} total CDR records")

    # Filter for inbound/outbound answered calls with recordings
    to_process = []
    seen_call_ids = set()
    skipped_internal = 0
    skipped_no_recording = 0
    skipped_not_answered = 0
    skipped_already_processed = 0

    for cdr in all_cdrs:
        call_id = cdr.get("uid")
        if not call_id or call_id in seen_call_ids:
            continue
        seen_call_ids.add(call_id)

        # Check call type - ONLY process inbound and outbound
        call_type = cdr.get("call_type", "").lower()
        if call_type not in ("inbound", "outbound"):
            skipped_internal += 1
            continue

        # Check if answered
        disposition = cdr.get("disposition", "").upper()
        if disposition != "ANSWERED":
            skipped_not_answered += 1
            continue

        # Check for recording
        recording = cdr.get("recording") or cdr.get("record_file")
        if not recording:
            skipped_no_recording += 1
            continue

        # Check if already processed (unless force)
        if not force:
            existing = db.query(CallSummary).filter(CallSummary.call_id == call_id).first()
            if existing and not existing.error_message:
                skipped_already_processed += 1
                continue

        # Check if within date range
        time_str = cdr.get("time", "")
        if time_str:
            call_time = _parse_cloud_time(time_str)
            if call_time and (call_time < start_date or call_time > end_date):
                continue

        to_process.append({
            "call_id": call_id,
            "recording_file": recording,
            "call_type": call_type,
            "time": time_str,
        })

    logger.info(f"Filtered to {len(to_process)} calls to process")
    logger.info(f"Skipped: {skipped_internal} internal, {skipped_not_answered} not answered, "
                f"{skipped_no_recording} no recording, {skipped_already_processed} already processed")

    # Queue all for background processing
    for item in to_process:
        background_tasks.add_task(
            _process_recording_task,
            call_id=item["call_id"],
            recording_file=item["recording_file"],
            force=force,
        )

    return {
        "status": "queued",
        "period": {
            "description": period_desc,
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
        },
        "stats": {
            "total_cdrs_fetched": len(all_cdrs),
            "calls_to_process": len(to_process),
            "skipped_internal": skipped_internal,
            "skipped_not_answered": skipped_not_answered,
            "skipped_no_recording": skipped_no_recording,
            "skipped_already_processed": skipped_already_processed,
        },
        "call_ids": [item["call_id"] for item in to_process],
    }


def _parse_cloud_time(time_str: str) -> Optional[datetime]:
    """Parse Cloud PBX time format."""
    if not time_str:
        return None

    formats = [
        "%d/%m/%Y %I:%M:%S %p",
        "%d/%m/%Y %H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S",
    ]

    for fmt in formats:
        try:
            return datetime.strptime(time_str, fmt)
        except ValueError:
            continue

    return None


async def _process_recording_task(
    call_id: str,
    recording_file: Optional[str] = None,
    force: bool = False,
):
    """Background task to process a recording."""
    from app.database import SessionLocal

    db = SessionLocal()
    try:
        logger.info(f"Processing call {call_id} with recording_file: {recording_file}")

        # Get recording file from Yeastar's recording list API
        if not recording_file:
            client = get_yeastar_client()
            # Search through recordings to find matching UID
            # The API doesn't filter by UID, so we need to search manually
            for page in range(1, 20):  # Search up to 20 pages (2000 recordings)
                result = await client.get_recording_list(page=page, page_size=100)
                if result and result.get("errcode") == 0:
                    for rec in result.get("data", []):
                        if rec.get("uid") == call_id:
                            recording_file = rec.get("file")
                            logger.info(f"Found recording file for call {call_id} on page {page}: {recording_file}")
                            break
                    if recording_file:
                        break
                    # If we've searched past the available recordings, stop
                    if len(result.get("data", [])) < 100:
                        break
                else:
                    break

        if not recording_file:
            logger.error(f"No recording file found for call {call_id}")
            _save_error(db, call_id, "No recording available for this call")
            return

        # Download recording
        client = get_yeastar_client()
        download_result = await client.download_recording(recording_file)

        if not download_result or download_result.get("status") != "Success":
            error_msg = download_result.get("errmsg", "Failed to get download URL") if download_result else "Download failed"
            logger.error(f"Failed to download recording: {error_msg}")
            _save_error(db, call_id, error_msg, recording_file)
            return

        download_url = download_result.get("download_url")

        # Download to temp file
        async with httpx.AsyncClient(timeout=60.0) as http_client:
            response = await http_client.get(download_url)
            response.raise_for_status()

            with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as temp_file:
                temp_file.write(response.content)
                temp_path = temp_file.name

        try:
            # Process with AI
            ai_service = get_ai_service()
            result = await ai_service.process_recording(temp_path)

            # Save to database
            if result.get("success"):
                summary_data = result.get("summary", {})

                # Check if exists (for force update)
                existing = db.query(CallSummary).filter(CallSummary.call_id == call_id).first()

                # Extract sentiment from mood analysis if available
                sentiment = summary_data.get("sentiment")
                mood_analysis = summary_data.get("mood_sentiment_analysis")
                if mood_analysis and isinstance(mood_analysis, dict):
                    sentiment = mood_analysis.get("overall_sentiment", sentiment)

                if existing:
                    # Update existing
                    existing.recording_file = recording_file
                    existing.language_detected = result.get("language_detected")
                    existing.transcript_preview = result.get("transcript_preview")
                    existing.call_type = summary_data.get("call_type")
                    existing.service_category = summary_data.get("service_category")
                    existing.summary = summary_data.get("summary")
                    existing.staff_name = summary_data.get("staff_name")
                    existing.customer_name = summary_data.get("customer_name")
                    existing.company_name = summary_data.get("company_name")
                    existing.topics_discussed = summary_data.get("topics_discussed")
                    existing.customer_requests = summary_data.get("customer_requests")
                    existing.staff_responses = summary_data.get("staff_responses")
                    existing.action_items = summary_data.get("action_items")
                    existing.resolution_status = summary_data.get("resolution_status")
                    existing.sentiment = sentiment
                    existing.key_details = summary_data.get("key_details")
                    existing.mood_sentiment_analysis = mood_analysis
                    existing.employee_performance = summary_data.get("employee_performance")
                    existing.processing_time_seconds = result.get("processing_time_seconds")
                    existing.error_message = None
                else:
                    # Create new
                    summary = CallSummary(
                        call_id=call_id,
                        recording_file=recording_file,
                        language_detected=result.get("language_detected"),
                        transcript_preview=result.get("transcript_preview"),
                        call_type=summary_data.get("call_type"),
                        service_category=summary_data.get("service_category"),
                        summary=summary_data.get("summary"),
                        staff_name=summary_data.get("staff_name"),
                        customer_name=summary_data.get("customer_name"),
                        company_name=summary_data.get("company_name"),
                        topics_discussed=summary_data.get("topics_discussed"),
                        customer_requests=summary_data.get("customer_requests"),
                        staff_responses=summary_data.get("staff_responses"),
                        action_items=summary_data.get("action_items"),
                        resolution_status=summary_data.get("resolution_status"),
                        sentiment=sentiment,
                        key_details=summary_data.get("key_details"),
                        mood_sentiment_analysis=mood_analysis,
                        employee_performance=summary_data.get("employee_performance"),
                        processing_time_seconds=result.get("processing_time_seconds"),
                    )
                    db.add(summary)

                db.commit()
                logger.info(f"Successfully processed call {call_id}")

                # Broadcast analytics update via WebSocket
                try:
                    from app.services.websocket_manager import get_websocket_manager
                    import asyncio
                    ws_manager = get_websocket_manager()
                    asyncio.create_task(ws_manager.send_analytics_update())
                    logger.info(f"Broadcasted analytics update for call {call_id}")
                except Exception as ws_err:
                    logger.warning(f"Failed to broadcast WebSocket update: {ws_err}")
            else:
                error_msg = result.get("error", "Processing failed")
                _save_error(db, call_id, error_msg, recording_file)

        finally:
            # Clean up temp file
            if os.path.exists(temp_path):
                os.unlink(temp_path)

    except Exception as e:
        logger.error(f"Error processing call {call_id}: {e}")
        _save_error(db, call_id, str(e), recording_file)
    finally:
        db.close()


def _save_error(db: Session, call_id: str, error_msg: str, recording_file: str = None):
    """Save error to database."""
    existing = db.query(CallSummary).filter(CallSummary.call_id == call_id).first()
    if existing:
        existing.error_message = error_msg
    else:
        summary = CallSummary(
            call_id=call_id,
            recording_file=recording_file,
            error_message=error_msg,
        )
        db.add(summary)
    db.commit()
