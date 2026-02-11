"""API routes for AI transcription and summarization."""

from fastapi import APIRouter, HTTPException, Query, BackgroundTasks, Depends
from sqlalchemy.orm import Session
from sqlalchemy import func, and_, or_
from typing import Optional
from datetime import datetime, timedelta
import tempfile
import os
import httpx
import logging

from sqlalchemy.exc import IntegrityError

from app.database import get_db
from app.models.call_summary import CallSummary, SummaryNote
from app.models.user import User
from app.services.ai_transcription import get_ai_service
from app.services.yeastar_client import get_yeastar_client
from app.services.auth import get_current_user, is_superadmin
from app.services.processing_tracker import get_processing_tracker
from app.services.processing_queue import get_processing_queue

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/transcription", tags=["transcription"])

# Staff extension directory - maps extension to staff info
STAFF_DIRECTORY = {
    "111": {"name": "Amith", "department": "Sales", "role": "Sales Agent"},
    "201": {"name": "Jijina", "department": "Call Centre", "role": "Call Centre Agent"},
    "202": {"name": "Joanna", "department": "Call Centre", "role": "Call Centre Agent"},
    "203": {"name": "Ramshad", "department": "Call Centre", "role": "Call Centre Agent"},
    "207": {"name": "Saumil", "department": "Sales", "role": "Sales Agent"},
    "208": {"name": "Pranay", "department": "Sales", "role": "Sales Agent"},
    "209": {"name": "Sai", "department": "Sales", "role": "Sales Agent"},
    "211": {"name": "Swaroop", "department": "Sales", "role": "Sales Agent"},
    "221": {"name": "Vismaya", "department": "Qualifier", "role": "Qualifier Agent"},
}


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
    force: bool = Query(False, description="Force reprocess even if summary exists"),
    recording_file: Optional[str] = Query(None, description="Recording filename from Yeastar"),
    db: Session = Depends(get_db),
):
    """
    Process a call recording: add to processing queue.

    The recording is queued for sequential processing (transcription + AI analysis).
    Failed items automatically retry up to 3 times with exponential backoff.
    When force=true, the existing summary is deleted first for a clean regeneration.
    """
    tracker = get_processing_tracker()
    queue = get_processing_queue()

    # Check if this call is already being processed
    if tracker.is_processing(call_id):
        if force:
            raise HTTPException(
                status_code=409,
                detail="Call is currently being processed. Please retry after processing completes."
            )
        return {
            "status": "processing",
            "message": "This call is already being processed.",
            "call_id": call_id,
        }

    # Check if already processed
    existing = db.query(CallSummary).filter(CallSummary.call_id == call_id).first()
    if existing and not force:
        return {
            "status": "already_processed",
            "summary": existing.to_dict()
        }

    # If force regeneration, delete the existing summary for a clean start
    if existing and force:
        logger.info(f"Force regeneration requested for call {call_id}, deleting existing summary")
        db.query(SummaryNote).filter(SummaryNote.call_id == call_id).delete()
        db.query(CallSummary).filter(CallSummary.call_id == call_id).delete()
        db.commit()

    # Add to processing queue
    result = await queue.add(call_id=call_id, recording_file=recording_file, force=force)

    return {
        "status": "queued",
        "message": "Recording added to processing queue.",
        "call_id": call_id,
        "queue_position": result.get("position", 0),
    }


@router.get("/summary/{call_id}")
async def get_call_summary(
    call_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get the AI-generated summary for a call.
    Non-superadmin users can only access summaries for calls linked to their extension.
    Includes call metadata (duration, direction, start_time) from CallLog.
    """
    from app.models.call_log import CallLog

    summary = db.query(CallSummary).filter(CallSummary.call_id == call_id).first()

    if not summary:
        raise HTTPException(status_code=404, detail="Summary not found. Process the recording first.")

    # Get call metadata from CallLog (local DB, fast)
    call_log = db.query(CallLog).filter(CallLog.call_id == call_id).first()

    # Check access for non-superadmin users
    if not is_superadmin(current_user) and current_user.extension:
        user_ext = current_user.extension

        # Check multiple ways the user could be associated with this call:
        # 1. Staff extension matches (from summary)
        # 2. Staff name matches user's full name or username
        # 3. User's extension is in the CallLog as caller or callee (for outbound/inbound)
        # 4. User's extension appears in the recording filename
        has_access = (
            summary.staff_extension == user_ext or
            summary.staff_name == current_user.full_name or
            summary.staff_name == current_user.username
        )

        # Check CallLog for caller/callee matching user's extension
        # This handles outbound (user is caller) and inbound (user is callee) calls
        if not has_access and call_log:
            caller = call_log.caller_number or ""
            callee = call_log.callee_number or ""
            # Check if user's extension is the caller or callee
            # Extension might appear as "201" or "ext/201" or "201@..." etc.
            has_access = (
                caller == user_ext or
                callee == user_ext or
                caller.startswith(f"{user_ext}/") or
                callee.startswith(f"{user_ext}/") or
                caller.endswith(f"/{user_ext}") or
                callee.endswith(f"/{user_ext}") or
                f"/{user_ext}/" in caller or
                f"/{user_ext}/" in callee or
                # Also check if extension is contained in caller/callee (handles various formats)
                user_ext in caller or
                user_ext in callee
            )

        # If still no access and no CallLog, check the recording filename
        # Recording filename format often includes extension: e.g., "20251211-201-Outbound.wav"
        if not has_access and summary.recording_file:
            import re
            # Look for the extension pattern in filename: -XXX- where XXX is 2-4 digits
            ext_match = re.search(r'-(\d{2,4})-', summary.recording_file)
            if ext_match and ext_match.group(1) == user_ext:
                has_access = True

        if not has_access:
            raise HTTPException(status_code=403, detail="You don't have access to this summary")

    result = summary.to_dict()

    # Add call metadata if available
    if call_log:
        result["call_duration"] = call_log.duration
        result["call_direction"] = call_log.direction.value if call_log.direction else None
        result["call_start_time"] = call_log.start_time.isoformat() if call_log.start_time else None
    else:
        result["call_duration"] = None
        result["call_direction"] = None
        result["call_start_time"] = None

    # Always provide call_time - use CallLog start_time, or parse from call_id
    if call_log and call_log.start_time:
        result["call_time"] = call_log.start_time.isoformat()
    else:
        parsed_time = CallSummary.parse_call_time_from_id(summary.call_id)
        result["call_time"] = parsed_time.isoformat() if parsed_time else None

    return result


@router.get("/queue/status")
async def get_queue_status():
    """Get current processing queue status."""
    queue = get_processing_queue()
    return queue.get_status()


@router.post("/queue/clear")
async def clear_queue():
    """Clear all pending items from the processing queue."""
    queue = get_processing_queue()
    result = await queue.clear()
    return result


@router.get("/summaries")
async def list_summaries(
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    call_type: Optional[str] = Query(None),
    sentiment: Optional[str] = Query(None),
    search: Optional[str] = Query(None, description="Search by customer name, staff name, summary text, or call ID"),
    staff: Optional[str] = Query(None, description="Filter by staff name"),
    has_feedback: Optional[str] = Query(None, description="Filter by feedback: 'yes' or 'no'"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List all call summaries with filtering. Only shows successful summaries.
    Non-superadmin users only see summaries for calls linked to their extension.
    """
    from app.models.call_log import CallLog

    # Join with CallLog to get call_time (start_time)
    query = db.query(CallSummary, CallLog.start_time).outerjoin(
        CallLog, CallSummary.call_id == CallLog.call_id
    )

    # Only show successfully generated summaries (no errors, has summary text)
    query = query.filter(
        CallSummary.error_message.is_(None),
        CallSummary.summary.isnot(None),
        CallSummary.summary != ""
    )

    # Filter by user's extension if not superadmin
    if not is_superadmin(current_user) and current_user.extension:
        user_ext = current_user.extension
        # Check staff_extension, staff_name, OR caller/callee in CallLog
        # Also check recording_file for extension pattern
        query = query.filter(
            or_(
                CallSummary.staff_extension == user_ext,
                CallSummary.staff_name == current_user.full_name,
                CallSummary.staff_name == current_user.username,
                # Check if user's extension is caller/callee in CallLog
                CallLog.caller_number == user_ext,
                CallLog.callee_number == user_ext,
                CallLog.caller_number.like(f"{user_ext}/%"),
                CallLog.callee_number.like(f"{user_ext}/%"),
                CallLog.caller_number.like(f"%/{user_ext}"),
                CallLog.callee_number.like(f"%/{user_ext}"),
                # Check if extension is contained in caller/callee
                CallLog.caller_number.contains(user_ext),
                CallLog.callee_number.contains(user_ext),
                # Check recording filename for extension pattern (e.g., -201-)
                CallSummary.recording_file.like(f"%-{user_ext}-%"),
            )
        )

    if call_type:
        # Support partial matching for call type (e.g., "inquiry" matches "visa_inquiry", "general_inquiry")
        query = query.filter(CallSummary.call_type.contains(call_type))
    if sentiment:
        query = query.filter(CallSummary.sentiment == sentiment)

    # Server-side search across multiple fields
    if search:
        search_term = f"%{search}%"
        query = query.filter(
            or_(
                CallSummary.customer_name.ilike(search_term),
                CallSummary.staff_name.ilike(search_term),
                CallSummary.summary.ilike(search_term),
                CallSummary.call_id.ilike(search_term),
                CallSummary.customer_phone.ilike(search_term),
                CallSummary.call_type.ilike(search_term),
                CallSummary.company_name.ilike(search_term),
            )
        )

    # Server-side staff filter
    if staff:
        query = query.filter(CallSummary.staff_name == staff)

    # Server-side feedback filter
    if has_feedback == 'yes':
        query = query.filter(CallSummary.feedback_rating.isnot(None))
    elif has_feedback == 'no':
        query = query.filter(CallSummary.feedback_rating.is_(None))

    # Count total - need a separate query that includes the CallLog join for extension matching
    count_query = db.query(func.count(CallSummary.id)).outerjoin(
        CallLog, CallSummary.call_id == CallLog.call_id
    ).filter(
        CallSummary.error_message.is_(None),
        CallSummary.summary.isnot(None),
        CallSummary.summary != ""
    )
    if not is_superadmin(current_user) and current_user.extension:
        user_ext = current_user.extension
        count_query = count_query.filter(
            or_(
                CallSummary.staff_extension == user_ext,
                CallSummary.staff_name == current_user.full_name,
                CallSummary.staff_name == current_user.username,
                # Check if user's extension is caller/callee in CallLog
                CallLog.caller_number == user_ext,
                CallLog.callee_number == user_ext,
                CallLog.caller_number.like(f"{user_ext}/%"),
                CallLog.callee_number.like(f"{user_ext}/%"),
                CallLog.caller_number.like(f"%/{user_ext}"),
                CallLog.callee_number.like(f"%/{user_ext}"),
                # Check if extension is contained in caller/callee
                CallLog.caller_number.contains(user_ext),
                CallLog.callee_number.contains(user_ext),
                # Check recording filename for extension pattern (e.g., -201-)
                CallSummary.recording_file.like(f"%-{user_ext}-%"),
            )
        )
    if call_type:
        count_query = count_query.filter(CallSummary.call_type.contains(call_type))
    if sentiment:
        count_query = count_query.filter(CallSummary.sentiment == sentiment)
    if search:
        search_term = f"%{search}%"
        count_query = count_query.filter(
            or_(
                CallSummary.customer_name.ilike(search_term),
                CallSummary.staff_name.ilike(search_term),
                CallSummary.summary.ilike(search_term),
                CallSummary.call_id.ilike(search_term),
                CallSummary.customer_phone.ilike(search_term),
                CallSummary.call_type.ilike(search_term),
                CallSummary.company_name.ilike(search_term),
            )
        )
    if staff:
        count_query = count_query.filter(CallSummary.staff_name == staff)
    if has_feedback == 'yes':
        count_query = count_query.filter(CallSummary.feedback_rating.isnot(None))
    elif has_feedback == 'no':
        count_query = count_query.filter(CallSummary.feedback_rating.is_(None))
    total_count = count_query.scalar()

    # Order by call time (from CallLog) if available, otherwise by summary created_at
    results = query.order_by(
        func.coalesce(CallLog.start_time, CallSummary.created_at).desc()
    ).offset((page - 1) * per_page).limit(per_page).all()

    # Build response with call_time included
    summaries_data = []
    for summary, call_time in results:
        summary_dict = summary.to_dict()
        # Use CallLog start_time if available, otherwise parse from call_id
        if call_time:
            summary_dict["call_time"] = call_time.isoformat()
        else:
            parsed_time = CallSummary.parse_call_time_from_id(summary.call_id)
            summary_dict["call_time"] = parsed_time.isoformat() if parsed_time else None
        summaries_data.append(summary_dict)

    # Get unique staff names for filter dropdown
    staff_names = db.query(CallSummary.staff_name).filter(
        CallSummary.error_message.is_(None),
        CallSummary.summary.isnot(None),
        CallSummary.staff_name.isnot(None),
    ).distinct().all()
    staff_list = sorted([s[0] for s in staff_names if s[0]])

    return {
        "summaries": summaries_data,
        "total": total_count,
        "page": page,
        "per_page": per_page,
        "staff_list": staff_list,
    }


@router.get("/pending-stats")
async def get_pending_stats(
    days: int = Query(7, ge=1, le=365),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get count of calls pending AI summary generation. Falls back to API if database is empty."""
    from app.models.call_log import CallLog, CallDirection, CallStatus

    # Check if database has any call records
    total_db_records = db.query(func.count(CallLog.id)).scalar()

    if total_db_records == 0:
        # Database is empty, use API-based approach
        return await _get_pending_stats_from_api(days, db, current_user)

    # Calculate cutoff date
    cutoff_date = datetime.now() - timedelta(days=days)

    # Check if user needs extension filtering
    user_extension = None
    if current_user and not is_superadmin(current_user):
        user_extension = current_user.extension

    # Get set of call IDs that already have summaries (successful ones only)
    existing_summaries = db.query(CallSummary.call_id).filter(
        CallSummary.error_message.is_(None)
    ).all()
    processed_call_ids = {s.call_id for s in existing_summaries}

    # Build base query for answered calls with recordings in date range
    base_filter = and_(
        CallLog.start_time >= cutoff_date,
        CallLog.status == CallStatus.ANSWERED,
        CallLog.recording_file.isnot(None),
        CallLog.recording_file != "",
    )

    # Add extension filter for non-superadmin users
    if user_extension:
        extension_filter = or_(
            CallLog.caller_number == user_extension,
            CallLog.callee_number == user_extension,
            CallLog.caller_number.like(f"%/{user_extension}"),
            CallLog.callee_number.like(f"%/{user_extension}"),
            CallLog.caller_number.like(f"{user_extension}/%"),
            CallLog.callee_number.like(f"{user_extension}/%"),
        )
        base_filter = and_(base_filter, extension_filter)

    # Query calls grouped by direction
    calls = db.query(CallLog.call_id, CallLog.direction).filter(base_filter).all()

    # Count pending calls (those without summaries)
    inbound_pending = 0
    outbound_pending = 0
    internal_pending = 0

    for call_id, direction in calls:
        if call_id not in processed_call_ids:
            if direction == CallDirection.INBOUND:
                inbound_pending += 1
            elif direction == CallDirection.OUTBOUND:
                outbound_pending += 1
            elif direction == CallDirection.INTERNAL:
                internal_pending += 1

    return {
        "inbound_pending": inbound_pending,
        "outbound_pending": outbound_pending,
        "internal_pending": internal_pending,
        "total_pending": inbound_pending + outbound_pending + internal_pending,
        "source": "database",
    }


async def _get_pending_stats_from_api(days: int, db, current_user):
    """Fallback: Get pending stats from Yeastar API when database is empty."""
    # Check if user needs extension filtering
    user_extension = None
    if current_user and not is_superadmin(current_user):
        user_extension = current_user.extension

    # Calculate cutoff date
    cutoff_date = datetime.now() - timedelta(days=days)

    # Get set of call IDs that already have summaries
    existing_summaries = db.query(CallSummary.call_id).all()
    processed_call_ids = {s.call_id for s in existing_summaries}

    # Fetch recent CDRs from Yeastar to count pending
    client = get_yeastar_client()
    inbound_pending = 0
    outbound_pending = 0
    internal_pending = 0

    # Fetch calls within the date range
    max_pages = 20
    found_older_than_cutoff = False

    for page in range(1, max_pages + 1):
        if found_older_than_cutoff:
            break

        result = await client.get_cdr_list(page=page, page_size=100, sort_by="time", order_by="desc")
        if not result or result.get("errcode") != 0:
            break
        cdrs = result.get("data", [])
        if not cdrs:
            break

        for cdr in cdrs:
            # Parse call time and check if within date range
            time_str = cdr.get("time", "")
            call_time = None
            if time_str:
                try:
                    call_time = datetime.strptime(time_str, "%d/%m/%Y %I:%M:%S %p")
                except ValueError:
                    try:
                        call_time = datetime.strptime(time_str, "%d/%m/%Y %H:%M:%S")
                    except ValueError:
                        pass

            # Skip if call is older than cutoff date
            if call_time and call_time < cutoff_date:
                found_older_than_cutoff = True
                continue

            call_id = cdr.get("uid")
            disposition = cdr.get("disposition", "").upper()
            call_type = cdr.get("call_type", "").lower()
            recording = cdr.get("record_file") or cdr.get("recording")

            # Filter by user extension if not superadmin
            if user_extension:
                caller = cdr.get("call_from_number", "")
                callee = cdr.get("call_to_number", "")
                caller_match = caller == user_extension or caller.endswith(f"/{user_extension}") or caller.startswith(f"{user_extension}/")
                callee_match = callee == user_extension or callee.endswith(f"/{user_extension}") or callee.startswith(f"{user_extension}/")
                if not caller_match and not callee_match:
                    continue

            # Only count answered calls with recordings that haven't been processed
            if disposition == "ANSWERED" and recording and call_id not in processed_call_ids:
                if call_type == "inbound":
                    inbound_pending += 1
                elif call_type == "outbound":
                    outbound_pending += 1
                elif call_type == "internal":
                    internal_pending += 1

    return {
        "inbound_pending": inbound_pending,
        "outbound_pending": outbound_pending,
        "internal_pending": internal_pending,
        "total_pending": inbound_pending + outbound_pending + internal_pending,
        "source": "api",
    }


@router.get("/staff-call-metrics")
async def get_staff_call_metrics(
    days: int = Query(7, ge=1, le=7, description="Number of days (max 7)"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Get per-staff call counts (inbound + outbound) and pending AI summary counts.
    Max 7 day range.
    """
    from app.models.call_log import CallLog, CallDirection, CallStatus

    if not is_superadmin(current_user):
        raise HTTPException(status_code=403, detail="Superadmin access required")

    cutoff_date = datetime.now() - timedelta(days=days)

    # Get all answered calls with recordings in date range
    calls = db.query(
        CallLog.call_id,
        CallLog.direction,
        CallLog.caller_number,
        CallLog.callee_number,
        CallLog.extension,
    ).filter(
        CallLog.start_time >= cutoff_date,
        CallLog.status == CallStatus.ANSWERED,
    ).all()

    # Get set of call IDs that already have AI summaries
    existing_summaries = db.query(CallSummary.call_id).filter(
        CallSummary.error_message.is_(None),
        CallSummary.summary.isnot(None),
    ).all()
    processed_call_ids = {s.call_id for s in existing_summaries}

    # Build per-staff metrics using STAFF_DIRECTORY extensions
    staff_metrics = {}
    for ext, info in STAFF_DIRECTORY.items():
        staff_metrics[ext] = {
            "extension": ext,
            "name": info["name"],
            "department": info["department"],
            "inbound": 0,
            "outbound": 0,
            "internal": 0,
            "total_calls": 0,
            "pending_summary": 0,
        }

    for call_id, direction, caller, callee, ext_field in calls:
        # Determine which staff extension this call belongs to
        matched_ext = None
        if ext_field and ext_field in STAFF_DIRECTORY:
            matched_ext = ext_field
        else:
            # Check caller/callee against known extensions
            for known_ext in STAFF_DIRECTORY:
                if caller == known_ext or callee == known_ext:
                    matched_ext = known_ext
                    break

        if not matched_ext:
            continue

        metrics = staff_metrics[matched_ext]
        dir_str = direction.value if hasattr(direction, 'value') else str(direction)
        if dir_str == 'inbound':
            metrics["inbound"] += 1
        elif dir_str == 'outbound':
            metrics["outbound"] += 1
        elif dir_str == 'internal':
            metrics["internal"] += 1
        metrics["total_calls"] += 1

        # Check if this call has a pending AI summary (has recording but no summary)
        if call_id not in processed_call_ids:
            metrics["pending_summary"] += 1

    # Convert to list, sorted by total calls desc
    result = sorted(staff_metrics.values(), key=lambda x: x["total_calls"], reverse=True)

    return {
        "period_days": days,
        "staff_metrics": result,
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


@router.get("/language-analytics")
async def get_language_analytics(
    days: int = Query(30, ge=1, le=365, description="Number of days to analyze"),
    db: Session = Depends(get_db),
):
    """Get language distribution analytics from AI-processed call summaries."""
    from datetime import datetime, timedelta

    # Filter by date range
    cutoff_date = datetime.utcnow() - timedelta(days=days)

    # Count by language
    language_counts = db.query(
        CallSummary.language_detected,
        func.count(CallSummary.id).label('count')
    ).filter(
        CallSummary.created_at >= cutoff_date,
        CallSummary.error_message.is_(None),
        CallSummary.language_detected.isnot(None)
    ).group_by(CallSummary.language_detected).all()

    # Total calls with language detected
    total_with_language = sum(count for _, count in language_counts)

    # Build distribution with percentages
    # Invalid/ambiguous language codes to exclude
    invalid_langs = {'auto', 'unknown', '', 'it', 'it/cy', 'cy'}

    language_distribution = {}
    for lang, count in language_counts:
        if lang and lang.lower() not in invalid_langs and '/' not in lang and ',' not in lang:
            percentage = round((count / total_with_language * 100), 1) if total_with_language > 0 else 0
            language_distribution[lang] = {
                "count": count,
                "percentage": percentage
            }

    # Get language breakdown by sentiment
    language_sentiment = db.query(
        CallSummary.language_detected,
        CallSummary.sentiment,
        func.count(CallSummary.id).label('count')
    ).filter(
        CallSummary.created_at >= cutoff_date,
        CallSummary.error_message.is_(None),
        CallSummary.language_detected.isnot(None),
        CallSummary.sentiment.isnot(None)
    ).group_by(CallSummary.language_detected, CallSummary.sentiment).all()

    # Build language-sentiment matrix
    lang_sentiment_matrix = {}
    for lang, sentiment, count in language_sentiment:
        if lang and lang.lower() not in invalid_langs and '/' not in lang and ',' not in lang:
            if lang not in lang_sentiment_matrix:
                lang_sentiment_matrix[lang] = {"positive": 0, "neutral": 0, "negative": 0}
            if sentiment in lang_sentiment_matrix[lang]:
                lang_sentiment_matrix[lang][sentiment] = count

    # Get language breakdown by call type
    language_call_type = db.query(
        CallSummary.language_detected,
        CallSummary.call_type,
        func.count(CallSummary.id).label('count')
    ).filter(
        CallSummary.created_at >= cutoff_date,
        CallSummary.error_message.is_(None),
        CallSummary.language_detected.isnot(None),
        CallSummary.call_type.isnot(None)
    ).group_by(CallSummary.language_detected, CallSummary.call_type).all()

    # Build language-call_type matrix
    lang_call_type_matrix = {}
    for lang, call_type, count in language_call_type:
        if lang and lang.lower() not in invalid_langs and '/' not in lang and ',' not in lang:
            if lang not in lang_call_type_matrix:
                lang_call_type_matrix[lang] = {}
            lang_call_type_matrix[lang][call_type] = count

    return {
        "period_days": days,
        "total_analyzed": total_with_language,
        "language_distribution": language_distribution,
        "language_sentiment": lang_sentiment_matrix,
        "language_call_types": lang_call_type_matrix,
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


@router.get("/staff-analytics")
async def get_staff_analytics(
    days: int = Query(30, ge=1, le=365, description="Number of days to analyze"),
    db: Session = Depends(get_db),
):
    """
    Get comprehensive staff analytics focused on meaningful metrics.

    Returns detailed metrics for each staff member including:
    - Call volume and activity patterns
    - Call type distribution (what types of calls they handle)
    - Service categories handled
    - Resolution and follow-up tracking
    - Sentiment distribution of their calls
    - Customer type breakdown
    - Sales opportunities (for sales team)
    - Top topics and customer requests handled
    """
    from collections import Counter

    cutoff_date = datetime.utcnow() - timedelta(days=days)

    # Get all successful summaries with staff info
    summaries = db.query(CallSummary).filter(
        CallSummary.created_at >= cutoff_date,
        CallSummary.error_message.is_(None),
        CallSummary.summary.isnot(None),
    ).all()

    # Use module-level staff directory
    staff_directory = STAFF_DIRECTORY

    # Initialize staff stats
    staff_stats = {}

    for s in summaries:
        # Determine staff key (extension or name)
        staff_key = s.staff_extension or s.staff_name or "Unknown"

        # Get staff info from directory or summary
        if s.staff_extension and s.staff_extension in staff_directory:
            staff_info = staff_directory[s.staff_extension]
            staff_name = staff_info["name"]
            department = staff_info["department"]
            role = staff_info["role"]
        else:
            staff_name = s.staff_name or "Unknown"
            department = s.staff_department or "Unknown"
            role = s.staff_role or "Unknown"

        # Initialize staff entry if needed
        if staff_key not in staff_stats:
            staff_stats[staff_key] = {
                "extension": s.staff_extension,
                "name": staff_name,
                "department": department,
                "role": role,
                "total_calls": 0,
                "calls_by_date": {},
                "sentiments": {"positive": 0, "neutral": 0, "negative": 0},
                "resolutions": {"resolved": 0, "pending": 0, "escalated": 0, "other": 0},
                "call_types": Counter(),
                "service_categories": Counter(),
                "customer_types": Counter(),
                "topics": Counter(),
                "customer_requests": Counter(),
                "sales_opportunities": 0,
                "leads": {"hot": 0, "warm": 0, "cold": 0},
                "pipeline_value": 0,
                "first_call_resolutions": 0,
                "follow_ups_required": 0,
                "follow_ups_pending": 0,
                "urgency_levels": Counter(),
                "recent_calls": [],
            }

        stats = staff_stats[staff_key]
        stats["total_calls"] += 1

        # Track calls by date for activity pattern
        if s.created_at:
            date_key = s.created_at.strftime("%Y-%m-%d")
            stats["calls_by_date"][date_key] = stats["calls_by_date"].get(date_key, 0) + 1

        # Sentiment distribution
        if s.sentiment:
            sent_key = s.sentiment.lower()
            if sent_key in stats["sentiments"]:
                stats["sentiments"][sent_key] += 1

        # Resolution status
        if s.resolution_status:
            res_key = s.resolution_status.lower()
            if res_key in ["resolved", "completed"]:
                stats["resolutions"]["resolved"] += 1
            elif res_key in ["pending", "requires_followup"]:
                stats["resolutions"]["pending"] += 1
            elif res_key == "escalated":
                stats["resolutions"]["escalated"] += 1
            else:
                stats["resolutions"]["other"] += 1

        # Call types
        if s.call_type:
            stats["call_types"][s.call_type] += 1

        # Service categories
        if s.service_category:
            stats["service_categories"][s.service_category] += 1

        # Customer types
        if s.customer_type:
            stats["customer_types"][s.customer_type] += 1

        # Topics discussed
        if s.topics_discussed:
            for topic in s.topics_discussed:
                stats["topics"][topic.lower()] += 1

        # Customer requests
        if s.customer_requests:
            for req in s.customer_requests:
                stats["customer_requests"][req.lower()[:50]] += 1  # Truncate for grouping

        # Sales metrics
        if s.is_sales_opportunity:
            stats["sales_opportunities"] += 1
        if s.lead_quality:
            lq = s.lead_quality.lower()
            if lq in stats["leads"]:
                stats["leads"][lq] += 1
        if s.estimated_deal_value:
            stats["pipeline_value"] += s.estimated_deal_value

        # Follow-up tracking
        if s.first_call_resolution:
            stats["first_call_resolutions"] += 1
        if s.follow_up_required:
            stats["follow_ups_required"] += 1
            if s.follow_up_date and s.follow_up_date > datetime.utcnow():
                stats["follow_ups_pending"] += 1

        # Urgency levels
        if s.urgency_level:
            stats["urgency_levels"][s.urgency_level] += 1

        # Keep recent calls for context (last 5)
        if len(stats["recent_calls"]) < 5:
            stats["recent_calls"].append({
                "call_id": s.call_id,
                "date": s.created_at.isoformat() if s.created_at else None,
                "type": s.call_type,
                "sentiment": s.sentiment,
                "summary": (s.summary[:100] + "...") if s.summary and len(s.summary) > 100 else s.summary,
            })

    # Process and build final staff performance list (matching frontend interface)
    staff_performance = []
    for key, stats in staff_stats.items():
        if stats["name"] == "Unknown" and stats["total_calls"] < 3:
            continue  # Skip unknown with very few calls

        total = stats["total_calls"]

        # Calculate percentages
        resolution_rate = round((stats["resolutions"]["resolved"] / total * 100), 1) if total > 0 else 0
        positive_rate = round((stats["sentiments"]["positive"] / total * 100), 1) if total > 0 else 0
        fcr_rate = round((stats["first_call_resolutions"] / total * 100), 1) if total > 0 else 0

        staff_performance.append({
            # Basic info
            "extension": stats["extension"],
            "name": stats["name"],
            "department": stats["department"],

            # Core metrics (flat, matching frontend interface)
            "total_calls": total,
            "resolution_rate": resolution_rate,
            "positive_sentiment_rate": positive_rate,
            "first_call_resolution_rate": fcr_rate,

            # Sentiment breakdown
            "sentiment_breakdown": {
                "positive": stats["sentiments"]["positive"],
                "neutral": stats["sentiments"]["neutral"],
                "negative": stats["sentiments"]["negative"],
                "mixed": 0,
            },

            # Resolution breakdown
            "resolution_breakdown": {
                "resolved": stats["resolutions"]["resolved"],
                "pending": stats["resolutions"]["pending"],
                "escalated": stats["resolutions"]["escalated"],
                "other": stats["resolutions"]["other"],
            },

            # Performance scores (null - we removed arbitrary scoring)
            "avg_performance_score": None,
            "avg_professionalism_score": None,
            "avg_knowledge_score": None,
            "avg_communication_score": None,
            "avg_empathy_score": None,

            # Sales metrics (flat)
            "sales_opportunities": stats["sales_opportunities"],
            "hot_leads": stats["leads"]["hot"],
            "warm_leads": stats["leads"]["warm"],
            "cold_leads": stats["leads"]["cold"],
            "estimated_pipeline_value": round(stats["pipeline_value"], 2),
            "follow_ups_required": stats["follow_ups_required"],

            # Call type distribution
            "top_call_types": list(stats["call_types"].most_common(10)),
            "service_category_breakdown": dict(stats["service_categories"].most_common(10)),
            "customer_type_breakdown": dict(stats["customer_types"].most_common(5)),
        })

    # Sort by total calls
    staff_performance = sorted(staff_performance, key=lambda x: x["total_calls"], reverse=True)

    # Calculate department aggregates
    dept_aggregates = {}
    for staff in staff_performance:
        dept = staff["department"]
        if dept not in dept_aggregates:
            dept_aggregates[dept] = {
                "department": dept,
                "total_staff": 0,
                "total_calls": 0,
                "total_resolved": 0,
                "total_positive": 0,
                "sales_opportunities": 0,
                "pipeline_value": 0,
            }

        agg = dept_aggregates[dept]
        agg["total_staff"] += 1
        agg["total_calls"] += staff["total_calls"]
        agg["total_resolved"] += staff["resolution_breakdown"]["resolved"]
        agg["total_positive"] += staff["sentiment_breakdown"]["positive"]
        agg["sales_opportunities"] += staff["sales_opportunities"]
        agg["pipeline_value"] += staff["estimated_pipeline_value"]

    # Finalize department stats
    department_summary = []
    for dept, agg in dept_aggregates.items():
        department_summary.append({
            "department": dept,
            "total_staff": agg["total_staff"],
            "total_calls": agg["total_calls"],
            "resolution_rate": round((agg["total_resolved"] / agg["total_calls"] * 100) if agg["total_calls"] > 0 else 0, 1),
            "positive_sentiment_rate": round((agg["total_positive"] / agg["total_calls"] * 100) if agg["total_calls"] > 0 else 0, 1),
            "avg_performance_score": None,  # Removed arbitrary scores
            "sales_opportunities": agg["sales_opportunities"],
            "pipeline_value": round(agg["pipeline_value"], 2),
        })

    # Build leaderboards (matching frontend interface)
    top_by_calls = sorted(staff_performance, key=lambda x: x["total_calls"], reverse=True)[:5]
    top_by_resolution = sorted([s for s in staff_performance if s["total_calls"] >= 3], key=lambda x: x["resolution_rate"], reverse=True)[:5]
    top_by_score = []  # Empty - we removed performance scores
    top_by_sales = sorted(staff_performance, key=lambda x: x["sales_opportunities"], reverse=True)[:5]

    return {
        "period_days": days,
        "total_calls_analyzed": len(summaries),
        "staff_directory": staff_directory,
        "staff_performance": staff_performance,
        "department_summary": department_summary,
        "leaderboards": {
            "by_call_volume": [{"name": s["name"], "extension": s["extension"], "calls": s["total_calls"]} for s in top_by_calls],
            "by_performance_score": [{"name": s["name"], "extension": s["extension"], "score": 0} for s in top_by_calls[:3]],  # Placeholder
            "by_resolution_rate": [{"name": s["name"], "extension": s["extension"], "rate": s["resolution_rate"]} for s in top_by_resolution],
            "by_sales_opportunities": [{"name": s["name"], "extension": s["extension"], "opportunities": s["sales_opportunities"]} for s in top_by_sales],
        },
    }


@router.get("/sales-pipeline")
async def get_sales_pipeline(
    days: int = Query(30, ge=1, le=365, description="Number of days to analyze"),
    db: Session = Depends(get_db),
):
    """
    Get sales pipeline analytics from call summaries.

    Returns leads, opportunities, and potential revenue from calls.
    """
    cutoff_date = datetime.utcnow() - timedelta(days=days)

    # Get calls marked as sales opportunities
    opportunities = db.query(CallSummary).filter(
        CallSummary.created_at >= cutoff_date,
        CallSummary.error_message.is_(None),
        CallSummary.is_sales_opportunity == True,
    ).order_by(CallSummary.created_at.desc()).all()

    # Aggregate by lead quality
    pipeline = {
        "hot": {"count": 0, "value": 0, "calls": []},
        "warm": {"count": 0, "value": 0, "calls": []},
        "cold": {"count": 0, "value": 0, "calls": []},
        "unqualified": {"count": 0, "value": 0, "calls": []},
    }

    for opp in opportunities:
        quality = opp.lead_quality or "unqualified"
        if quality not in pipeline:
            quality = "unqualified"

        pipeline[quality]["count"] += 1
        pipeline[quality]["value"] += opp.estimated_deal_value or 0
        pipeline[quality]["calls"].append({
            "call_id": opp.call_id,
            "customer_name": opp.customer_name,
            "company_name": opp.company_name,
            "service_category": opp.service_category,
            "estimated_value": opp.estimated_deal_value,
            "urgency": opp.urgency_level,
            "follow_up_required": opp.follow_up_required,
            "follow_up_date": opp.follow_up_date.isoformat() if opp.follow_up_date else None,
            "staff_name": opp.staff_name,
            "created_at": opp.created_at.isoformat() if opp.created_at else None,
        })

    # Get follow-ups due
    follow_ups = db.query(CallSummary).filter(
        CallSummary.follow_up_required == True,
        CallSummary.follow_up_date.isnot(None),
        CallSummary.follow_up_date >= datetime.utcnow(),
    ).order_by(CallSummary.follow_up_date).limit(20).all()

    return {
        "period_days": days,
        "total_opportunities": len(opportunities),
        "total_pipeline_value": sum(p["value"] for p in pipeline.values()),
        "by_lead_quality": {
            k: {"count": v["count"], "value": v["value"]}
            for k, v in pipeline.items()
        },
        "hot_leads": pipeline["hot"]["calls"][:10],
        "warm_leads": pipeline["warm"]["calls"][:10],
        "upcoming_follow_ups": [
            {
                "call_id": f.call_id,
                "customer_name": f.customer_name,
                "company_name": f.company_name,
                "follow_up_date": f.follow_up_date.isoformat() if f.follow_up_date else None,
                "staff_name": f.staff_name,
            }
            for f in follow_ups
        ],
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
    limit: int = Query(10, ge=1, le=50, description="Number of recordings to process"),
    db: Session = Depends(get_db),
):
    """
    Batch queue unprocessed recordings for processing.

    Fetches recent calls with recordings and queues any that don't have summaries.
    Items are processed sequentially with automatic retry on failure.
    """
    # Get recent calls with recordings from Yeastar
    client = get_yeastar_client()
    result = await client.get_cdr_list(page=1, page_size=limit * 2)

    if not result or result.get("errcode") != 0:
        raise HTTPException(status_code=500, detail="Failed to fetch call list")

    cdrs = result.get("data", [])

    # Filter calls with recordings that haven't been processed
    to_process = []
    seen_call_ids = set()

    for cdr in cdrs:
        recording = cdr.get("recording") or cdr.get("record_file")
        call_id = cdr.get("uid")

        if not recording or not call_id or call_id in seen_call_ids:
            continue

        seen_call_ids.add(call_id)

        existing = db.query(CallSummary).filter(CallSummary.call_id == call_id).first()
        if not existing:
            to_process.append({
                "call_id": call_id,
                "recording_file": recording
            })

        if len(to_process) >= limit:
            break

    # Add all to processing queue
    queue = get_processing_queue()
    batch_result = await queue.add_batch(to_process)

    return {
        "status": "queued",
        "count": batch_result["added_count"],
        "skipped": batch_result["skipped_count"],
        "call_ids": batch_result["added"],
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


def _parse_score(value) -> Optional[int]:
    """Parse a score value (1-10) from various formats."""
    if value is None:
        return None
    if isinstance(value, int):
        return max(1, min(10, value))
    if isinstance(value, float):
        return max(1, min(10, int(value)))
    if isinstance(value, str):
        # Handle "8/10", "8 out of 10", "8", etc.
        import re
        match = re.search(r'(\d+)', str(value))
        if match:
            return max(1, min(10, int(match.group(1))))
    return None


def _parse_amount(value) -> Optional[float]:
    """Parse an amount value from various formats."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        import re
        # Remove currency symbols and commas
        cleaned = re.sub(r'[^\d.]', '', str(value))
        if cleaned:
            try:
                return float(cleaned)
            except ValueError:
                pass
    return None


def _parse_date(value) -> Optional[datetime]:
    """Parse a date value from various formats."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        formats = [
            "%Y-%m-%d",
            "%d/%m/%Y",
            "%d-%m-%Y",
            "%Y-%m-%d %H:%M:%S",
            "%d/%m/%Y %H:%M:%S",
        ]
        for fmt in formats:
            try:
                return datetime.strptime(value, fmt)
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

    tracker = get_processing_tracker()

    # Atomically check if this call is already being processed
    if not tracker.try_acquire(call_id):
        logger.info(f"Call {call_id} is already being processed, skipping duplicate")
        return

    db = SessionLocal()
    try:
        logger.info(f"Processing call {call_id} with recording_file: {recording_file}")

        # Stage tracking via processing queue
        from app.services.processing_queue import get_processing_queue
        queue = get_processing_queue()

        async def _stage_callback(stage: str):
            await queue.update_stage(call_id, stage)

        await queue.update_stage(call_id, "downloading")

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
            result = await ai_service.process_recording(temp_path, recording_file=recording_file, stage_callback=_stage_callback)

            # Save to database
            await queue.update_stage(call_id, "saving")
            if result.get("success"):
                summary_data = result.get("summary", {}) or {}

                # Check if exists (for force update)
                existing = db.query(CallSummary).filter(CallSummary.call_id == call_id).first()

                # Extract sentiment from mood analysis if available
                sentiment = summary_data.get("sentiment")
                mood_analysis = summary_data.get("mood_sentiment_analysis")
                if mood_analysis and isinstance(mood_analysis, dict):
                    sentiment = mood_analysis.get("overall_sentiment", sentiment)

                # Extract performance scores from employee_performance
                emp_perf = summary_data.get("employee_performance", {}) or {}
                professionalism_score = _parse_score(emp_perf.get("professionalism_score"))
                knowledge_score = _parse_score(emp_perf.get("knowledge_score"))
                communication_score = _parse_score(emp_perf.get("communication_score"))
                empathy_score = _parse_score(emp_perf.get("empathy_score"))
                overall_performance_score = _parse_score(emp_perf.get("overall_performance_score"))

                # Extract call classification data
                call_class = summary_data.get("call_classification", {}) or {}
                is_sales_opportunity = call_class.get("is_sales_opportunity", False)
                lead_quality = call_class.get("lead_quality")
                estimated_deal_value = _parse_amount(call_class.get("estimated_deal_value"))
                conversion_likelihood = call_class.get("conversion_likelihood")
                urgency_level = call_class.get("urgency_level")
                follow_up_required = call_class.get("follow_up_required", False)
                follow_up_date = _parse_date(call_class.get("follow_up_date"))

                # Extract customer profile data
                cust_profile = summary_data.get("customer_profile", {}) or {}
                customer_type = cust_profile.get("customer_type")

                # Extract call quality metrics
                quality_metrics = summary_data.get("call_quality_metrics", {}) or {}
                first_call_resolution = quality_metrics.get("first_call_resolution")
                customer_effort_score = quality_metrics.get("customer_effort_score")

                # Get staff info from result (extracted from filename) or summary
                staff_extension = result.get("staff_extension") or summary_data.get("staff_extension")
                staff_name = result.get("staff_name") or summary_data.get("staff_name")
                staff_department = result.get("staff_department") or summary_data.get("staff_department")

                # Override with STAFF_DIRECTORY if extension is known (AI may extract role as name)
                if staff_extension and staff_extension in STAFF_DIRECTORY:
                    staff_info = STAFF_DIRECTORY[staff_extension]
                    staff_name = staff_info["name"]
                    staff_department = staff_info["department"]

                # ==================== EXTRACT DEPARTMENT-SPECIFIC ANALYSIS ====================
                dept_analysis = summary_data.get("department_analysis", {}) or {}
                cross_dept = summary_data.get("cross_department", {}) or {}

                # Star Rating (Universal)
                star_rating = _parse_score(dept_analysis.get("star_rating"))
                star_rating_justification = dept_analysis.get("star_rating_justification")

                # Qualifier Department Fields
                qualifier_analysis = dept_analysis.get("qualifier_analysis", {}) or {}
                qualifier_requirement_type = qualifier_analysis.get("requirement_type")
                qualifier_timeline = qualifier_analysis.get("timeline")
                qualifier_decision_maker_status = qualifier_analysis.get("decision_maker_status")
                qualifier_appointment_offered = qualifier_analysis.get("appointment_offered")
                qualifier_fail_reason = qualifier_analysis.get("fail_reason")
                qualifier_service_name = qualifier_analysis.get("service_name")
                qualifier_short_description = qualifier_analysis.get("short_description")
                qualifier_expected_month = qualifier_analysis.get("expected_month")
                qualifier_decision_role = qualifier_analysis.get("decision_maker_status")  # Same as decision_maker_status
                qualifier_availability = qualifier_analysis.get("availability")
                qualifier_missing_fields = qualifier_analysis.get("missing_fields")

                # Sales Department Fields
                sales_analysis = dept_analysis.get("sales_analysis", {}) or {}
                sales_sql_eligible = sales_analysis.get("sql_eligible")
                sales_notes_quality = sales_analysis.get("notes_quality")
                sales_exit_status = sales_analysis.get("exit_status")
                sales_parking_status = sales_analysis.get("parking_status")
                sales_last_contact_days = None  # Calculated from last_contact_mentioned
                sales_next_action = sales_analysis.get("next_action")
                sales_qualification_reason = sales_analysis.get("qualification_reason")
                sales_cadence_compliant = sales_analysis.get("cadence_compliant")

                # Call Center Department Fields
                cc_analysis = dept_analysis.get("call_centre_analysis", {}) or {}
                cc_opening_compliant = cc_analysis.get("opening_compliant")
                cc_opening_time_seconds = cc_analysis.get("opening_time_seconds")
                cc_satisfaction_question_asked = cc_analysis.get("satisfaction_question_asked")
                cc_customer_response = cc_analysis.get("customer_response")
                cc_call_category = cc_analysis.get("call_category")
                cc_whatsapp_handoff = cc_analysis.get("whatsapp_handoff", {}) or {}
                cc_whatsapp_handoff_valid = cc_whatsapp_handoff.get("valid")
                cc_premium_pitch_quality = cc_analysis.get("premium_pitch_quality")

                # Cross-Department Fields
                future_opportunities = cross_dept.get("future_opportunities")
                industry_interests = cross_dept.get("industry_interests")
                repeat_caller = cross_dept.get("repeat_caller")
                if repeat_caller == "suspected":
                    repeat_caller = True  # Treat suspected as True
                compliance_alerts = dept_analysis.get("compliance_alerts")
                sla_breach = False  # Will be calculated based on metrics
                talk_time_data = cross_dept.get("talk_time_ratio", {}) or {}
                talk_time_ratio = talk_time_data.get("staff_percent")
                greeting_compliant = cross_dept.get("greeting_compliant")
                duration_anomaly = cross_dept.get("duration_anomaly")
                handoff_quality = cross_dept.get("handoff_quality")

                # Full department analysis JSON
                department_analysis_json = dept_analysis

                if existing:
                    # Update existing
                    existing.recording_file = recording_file
                    existing.language_detected = result.get("language_detected")
                    existing.transcript_preview = result.get("transcript_preview")
                    existing.call_type = summary_data.get("call_type")
                    existing.service_category = summary_data.get("service_category")
                    existing.service_subcategory = summary_data.get("service_subcategory")
                    existing.summary = summary_data.get("summary")
                    existing.staff_name = staff_name
                    existing.staff_extension = staff_extension
                    existing.staff_department = staff_department
                    existing.staff_role = summary_data.get("staff_role")
                    existing.customer_name = summary_data.get("customer_name")
                    existing.customer_phone = summary_data.get("customer_phone")
                    existing.company_name = summary_data.get("company_name")
                    existing.topics_discussed = summary_data.get("topics_discussed")
                    existing.customer_requests = summary_data.get("customer_requests")
                    existing.staff_responses = summary_data.get("staff_responses")
                    existing.action_items = summary_data.get("action_items")
                    existing.commitments_made = summary_data.get("commitments_made")
                    existing.resolution_status = summary_data.get("resolution_status")
                    existing.sentiment = sentiment
                    existing.key_details = summary_data.get("key_details")
                    existing.call_classification = call_class
                    existing.is_sales_opportunity = is_sales_opportunity
                    existing.lead_quality = lead_quality
                    existing.estimated_deal_value = estimated_deal_value
                    existing.conversion_likelihood = conversion_likelihood
                    existing.urgency_level = urgency_level
                    existing.follow_up_required = follow_up_required
                    existing.follow_up_date = follow_up_date
                    existing.customer_profile = cust_profile
                    existing.customer_type = customer_type
                    existing.mood_sentiment_analysis = mood_analysis
                    existing.employee_performance = emp_perf
                    existing.professionalism_score = professionalism_score
                    existing.knowledge_score = knowledge_score
                    existing.communication_score = communication_score
                    existing.empathy_score = empathy_score
                    existing.overall_performance_score = overall_performance_score
                    existing.compliance_check = summary_data.get("compliance_check")
                    existing.call_quality_metrics = quality_metrics
                    existing.first_call_resolution = first_call_resolution
                    existing.customer_effort_score = customer_effort_score
                    existing.processing_time_seconds = result.get("processing_time_seconds")
                    existing.model_used = result.get("model_used")
                    existing.error_message = None

                    # Department-wise analysis fields
                    existing.star_rating = star_rating
                    existing.star_rating_justification = star_rating_justification
                    existing.qualifier_requirement_type = qualifier_requirement_type
                    existing.qualifier_timeline = qualifier_timeline
                    existing.qualifier_decision_maker_status = qualifier_decision_maker_status
                    existing.qualifier_appointment_offered = qualifier_appointment_offered
                    existing.qualifier_fail_reason = qualifier_fail_reason
                    existing.qualifier_service_name = qualifier_service_name
                    existing.qualifier_short_description = qualifier_short_description
                    existing.qualifier_expected_month = qualifier_expected_month
                    existing.qualifier_decision_role = qualifier_decision_role
                    existing.qualifier_availability = qualifier_availability
                    existing.qualifier_missing_fields = qualifier_missing_fields
                    existing.sales_sql_eligible = sales_sql_eligible
                    existing.sales_notes_quality = sales_notes_quality
                    existing.sales_exit_status = sales_exit_status
                    existing.sales_parking_status = sales_parking_status
                    existing.sales_last_contact_days = sales_last_contact_days
                    existing.sales_next_action = sales_next_action
                    existing.sales_qualification_reason = sales_qualification_reason
                    existing.sales_cadence_compliant = sales_cadence_compliant
                    existing.cc_opening_compliant = cc_opening_compliant
                    existing.cc_opening_time_seconds = cc_opening_time_seconds
                    existing.cc_satisfaction_question_asked = cc_satisfaction_question_asked
                    existing.cc_customer_response = cc_customer_response
                    existing.cc_call_category = cc_call_category
                    existing.cc_whatsapp_handoff_valid = cc_whatsapp_handoff_valid
                    existing.cc_premium_pitch_quality = cc_premium_pitch_quality
                    existing.future_opportunities = future_opportunities
                    existing.industry_interests = industry_interests
                    existing.repeat_caller = repeat_caller
                    existing.compliance_alerts = compliance_alerts
                    existing.sla_breach = sla_breach
                    existing.talk_time_ratio = talk_time_ratio
                    existing.greeting_compliant = greeting_compliant
                    existing.duration_anomaly = duration_anomaly
                    existing.handoff_quality = handoff_quality
                    existing.department_analysis = department_analysis_json
                else:
                    # Create new
                    summary = CallSummary(
                        call_id=call_id,
                        recording_file=recording_file,
                        language_detected=result.get("language_detected"),
                        transcript_preview=result.get("transcript_preview"),
                        call_type=summary_data.get("call_type"),
                        service_category=summary_data.get("service_category"),
                        service_subcategory=summary_data.get("service_subcategory"),
                        summary=summary_data.get("summary"),
                        staff_name=staff_name,
                        staff_extension=staff_extension,
                        staff_department=staff_department,
                        staff_role=summary_data.get("staff_role"),
                        customer_name=summary_data.get("customer_name"),
                        customer_phone=summary_data.get("customer_phone"),
                        company_name=summary_data.get("company_name"),
                        topics_discussed=summary_data.get("topics_discussed"),
                        customer_requests=summary_data.get("customer_requests"),
                        staff_responses=summary_data.get("staff_responses"),
                        action_items=summary_data.get("action_items"),
                        commitments_made=summary_data.get("commitments_made"),
                        resolution_status=summary_data.get("resolution_status"),
                        sentiment=sentiment,
                        key_details=summary_data.get("key_details"),
                        call_classification=call_class,
                        is_sales_opportunity=is_sales_opportunity,
                        lead_quality=lead_quality,
                        estimated_deal_value=estimated_deal_value,
                        conversion_likelihood=conversion_likelihood,
                        urgency_level=urgency_level,
                        follow_up_required=follow_up_required,
                        follow_up_date=follow_up_date,
                        customer_profile=cust_profile,
                        customer_type=customer_type,
                        mood_sentiment_analysis=mood_analysis,
                        employee_performance=emp_perf,
                        professionalism_score=professionalism_score,
                        knowledge_score=knowledge_score,
                        communication_score=communication_score,
                        empathy_score=empathy_score,
                        overall_performance_score=overall_performance_score,
                        compliance_check=summary_data.get("compliance_check"),
                        call_quality_metrics=quality_metrics,
                        first_call_resolution=first_call_resolution,
                        customer_effort_score=customer_effort_score,
                        processing_time_seconds=result.get("processing_time_seconds"),
                        model_used=result.get("model_used"),
                        # Department-wise analysis fields
                        star_rating=star_rating,
                        star_rating_justification=star_rating_justification,
                        qualifier_requirement_type=qualifier_requirement_type,
                        qualifier_timeline=qualifier_timeline,
                        qualifier_decision_maker_status=qualifier_decision_maker_status,
                        qualifier_appointment_offered=qualifier_appointment_offered,
                        qualifier_fail_reason=qualifier_fail_reason,
                        qualifier_service_name=qualifier_service_name,
                        qualifier_short_description=qualifier_short_description,
                        qualifier_expected_month=qualifier_expected_month,
                        qualifier_decision_role=qualifier_decision_role,
                        qualifier_availability=qualifier_availability,
                        qualifier_missing_fields=qualifier_missing_fields,
                        sales_sql_eligible=sales_sql_eligible,
                        sales_notes_quality=sales_notes_quality,
                        sales_exit_status=sales_exit_status,
                        sales_parking_status=sales_parking_status,
                        sales_last_contact_days=sales_last_contact_days,
                        sales_next_action=sales_next_action,
                        sales_qualification_reason=sales_qualification_reason,
                        sales_cadence_compliant=sales_cadence_compliant,
                        cc_opening_compliant=cc_opening_compliant,
                        cc_opening_time_seconds=cc_opening_time_seconds,
                        cc_satisfaction_question_asked=cc_satisfaction_question_asked,
                        cc_customer_response=cc_customer_response,
                        cc_call_category=cc_call_category,
                        cc_whatsapp_handoff_valid=cc_whatsapp_handoff_valid,
                        cc_premium_pitch_quality=cc_premium_pitch_quality,
                        future_opportunities=future_opportunities,
                        industry_interests=industry_interests,
                        repeat_caller=repeat_caller,
                        compliance_alerts=compliance_alerts,
                        sla_breach=sla_breach,
                        talk_time_ratio=talk_time_ratio,
                        greeting_compliant=greeting_compliant,
                        duration_anomaly=duration_anomaly,
                        handoff_quality=handoff_quality,
                        department_analysis=department_analysis_json,
                    )
                    db.add(summary)

                try:
                    db.commit()
                    logger.info(f"Successfully processed call {call_id}")
                except IntegrityError:
                    db.rollback()
                    logger.warning(f"IntegrityError for call {call_id} - concurrent insert detected, skipping")

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
        tracker.release(call_id)
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
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        logger.warning(f"IntegrityError saving error for call {call_id}, skipping")


# ============================================================================
# FEEDBACK ENDPOINTS
# ============================================================================

from pydantic import BaseModel

class FeedbackRequest(BaseModel):
    rating: int  # 1=dislike, 2=like
    comment: Optional[str] = None


@router.post("/summary/{call_id}/feedback")
async def submit_feedback(
    call_id: str,
    feedback: FeedbackRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Submit feedback (like/dislike) for an AI summary."""
    # Validate rating
    if feedback.rating not in [1, 2]:
        raise HTTPException(status_code=400, detail="Rating must be 1 (dislike) or 2 (like)")

    # Find the summary
    summary = db.query(CallSummary).filter(CallSummary.call_id == call_id).first()
    if not summary:
        raise HTTPException(status_code=404, detail="Summary not found")

    # Update feedback fields
    summary.feedback_rating = feedback.rating
    summary.feedback_by = current_user.username
    summary.feedback_at = datetime.utcnow()
    summary.feedback_comment = feedback.comment

    db.commit()
    db.refresh(summary)

    return {
        "status": "success",
        "message": "Feedback submitted successfully",
        "feedback": {
            "rating": summary.feedback_rating,
            "by": summary.feedback_by,
            "at": summary.feedback_at.isoformat() if summary.feedback_at else None,
            "comment": summary.feedback_comment,
        }
    }


@router.delete("/summary/{call_id}/feedback")
async def remove_feedback(
    call_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Remove feedback from an AI summary."""
    # Find the summary
    summary = db.query(CallSummary).filter(CallSummary.call_id == call_id).first()
    if not summary:
        raise HTTPException(status_code=404, detail="Summary not found")

    # Clear feedback fields
    summary.feedback_rating = None
    summary.feedback_by = None
    summary.feedback_at = None
    summary.feedback_comment = None

    db.commit()

    return {
        "status": "success",
        "message": "Feedback removed successfully"
    }


# ============================================================================
# NOTES ENDPOINTS
# ============================================================================

from app.models.call_summary import SummaryNote

class NoteRequest(BaseModel):
    content: str


@router.get("/summary/{call_id}/notes")
async def get_notes(
    call_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get all notes for a summary."""
    # Verify summary exists
    summary = db.query(CallSummary).filter(CallSummary.call_id == call_id).first()
    if not summary:
        raise HTTPException(status_code=404, detail="Summary not found")

    # Get notes ordered by created_at desc
    notes = db.query(SummaryNote).filter(
        SummaryNote.call_id == call_id
    ).order_by(SummaryNote.created_at.desc()).all()

    return {
        "notes": [n.to_dict() for n in notes],
        "total": len(notes)
    }


@router.post("/summary/{call_id}/notes")
async def create_note(
    call_id: str,
    note: NoteRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Create a new note for a summary."""
    # Verify summary exists
    summary = db.query(CallSummary).filter(CallSummary.call_id == call_id).first()
    if not summary:
        raise HTTPException(status_code=404, detail="Summary not found")

    # Validate content
    if not note.content or not note.content.strip():
        raise HTTPException(status_code=400, detail="Note content cannot be empty")

    # Create note
    new_note = SummaryNote(
        call_id=call_id,
        content=note.content.strip(),
        created_by=current_user.username,
    )
    db.add(new_note)
    db.commit()
    db.refresh(new_note)

    return {
        "status": "success",
        "note": new_note.to_dict()
    }


@router.put("/summary/{call_id}/notes/{note_id}")
async def update_note(
    call_id: str,
    note_id: int,
    note: NoteRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Update a note. Only the creator can update their note."""
    # Find the note
    existing_note = db.query(SummaryNote).filter(
        SummaryNote.id == note_id,
        SummaryNote.call_id == call_id
    ).first()

    if not existing_note:
        raise HTTPException(status_code=404, detail="Note not found")

    # Check ownership
    if existing_note.created_by != current_user.username:
        raise HTTPException(status_code=403, detail="You can only edit your own notes")

    # Validate content
    if not note.content or not note.content.strip():
        raise HTTPException(status_code=400, detail="Note content cannot be empty")

    # Update note
    existing_note.content = note.content.strip()
    existing_note.updated_at = datetime.utcnow()

    db.commit()
    db.refresh(existing_note)

    return {
        "status": "success",
        "note": existing_note.to_dict()
    }


@router.delete("/summary/{call_id}/notes/{note_id}")
async def delete_note(
    call_id: str,
    note_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Delete a note. Only the creator can delete their note."""
    # Find the note
    existing_note = db.query(SummaryNote).filter(
        SummaryNote.id == note_id,
        SummaryNote.call_id == call_id
    ).first()

    if not existing_note:
        raise HTTPException(status_code=404, detail="Note not found")

    # Check ownership
    if existing_note.created_by != current_user.username:
        raise HTTPException(status_code=403, detail="You can only delete your own notes")

    db.delete(existing_note)
    db.commit()

    return {
        "status": "success",
        "message": "Note deleted successfully"
    }


@router.post("/fix-staff-records")
async def fix_staff_records(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Update all call summaries with correct staff info based on extension.
    Maps Unknown staff names/departments to correct values from STAFF_DIRECTORY.
    Requires superadmin access.
    """
    if not is_superadmin(current_user):
        raise HTTPException(status_code=403, detail="Superadmin access required")

    updated_count = 0
    records_checked = 0

    # Get all records with staff_extension
    summaries = db.query(CallSummary).filter(
        CallSummary.staff_extension.isnot(None)
    ).all()

    for summary in summaries:
        records_checked += 1
        ext = summary.staff_extension

        if ext in STAFF_DIRECTORY:
            staff_info = STAFF_DIRECTORY[ext]
            needs_update = False

            # Check if any field needs updating
            if summary.staff_name != staff_info["name"]:
                summary.staff_name = staff_info["name"]
                needs_update = True
            if summary.staff_department != staff_info["department"]:
                summary.staff_department = staff_info["department"]
                needs_update = True
            if summary.staff_role != staff_info["role"]:
                summary.staff_role = staff_info["role"]
                needs_update = True

            if needs_update:
                updated_count += 1

    db.commit()

    return {
        "status": "success",
        "records_checked": records_checked,
        "records_updated": updated_count,
        "staff_directory": STAFF_DIRECTORY,
    }


# ============================================================================
# DEPARTMENT-WISE ANALYTICS ENDPOINTS
# ============================================================================

from sqlalchemy import func, case, and_


@router.get("/department-analytics/qualifier")
async def get_qualifier_analytics(
    days: int = 30,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Get Qualifier department analytics including:
    - Star rating distribution
    - Fail reason breakdown
    - Timeline distribution
    - Missing fields compliance
    - Appointment offer rate
    """
    from datetime import datetime, timedelta

    start_date = datetime.utcnow() - timedelta(days=days)

    # Get qualifier calls
    qualifier_calls = db.query(CallSummary).filter(
        CallSummary.staff_department == "Qualifier",
        CallSummary.created_at >= start_date,
    ).all()

    total_calls = len(qualifier_calls)
    if total_calls == 0:
        return {
            "department": "Qualifier",
            "period_days": days,
            "total_calls": 0,
            "star_distribution": {},
            "fail_reasons": {},
            "timeline_distribution": {},
            "compliance": {},
        }

    # Star rating distribution
    star_dist = {}
    for rating in range(1, 6):
        count = sum(1 for c in qualifier_calls if c.star_rating == rating)
        star_dist[str(rating)] = {
            "count": count,
            "percentage": round(count / total_calls * 100, 1) if total_calls > 0 else 0,
        }

    # Fail reason breakdown (for 1-star calls)
    fail_reasons = {}
    one_star_calls = [c for c in qualifier_calls if c.star_rating == 1]
    for call in one_star_calls:
        reason = call.qualifier_fail_reason or "unknown"
        fail_reasons[reason] = fail_reasons.get(reason, 0) + 1

    # Timeline distribution
    timeline_dist = {}
    for call in qualifier_calls:
        timeline = call.qualifier_timeline or "unknown"
        timeline_dist[timeline] = timeline_dist.get(timeline, 0) + 1

    # Compliance metrics
    calls_with_missing = sum(1 for c in qualifier_calls if c.qualifier_missing_fields and len(c.qualifier_missing_fields) > 0)
    appointment_offered = sum(1 for c in qualifier_calls if c.qualifier_appointment_offered is True)
    high_value_calls = [c for c in qualifier_calls if c.star_rating and c.star_rating >= 4]

    compliance = {
        "calls_with_missing_fields": calls_with_missing,
        "missing_fields_rate": round(calls_with_missing / total_calls * 100, 1) if total_calls > 0 else 0,
        "appointment_offered_count": appointment_offered,
        "appointment_offer_rate": round(appointment_offered / len(high_value_calls) * 100, 1) if high_value_calls else 0,
        "high_value_calls": len(high_value_calls),
    }

    # Requirement type distribution
    requirement_types = {}
    for call in qualifier_calls:
        req_type = call.qualifier_requirement_type or "unknown"
        requirement_types[req_type] = requirement_types.get(req_type, 0) + 1

    return {
        "department": "Qualifier",
        "period_days": days,
        "total_calls": total_calls,
        "star_distribution": star_dist,
        "fail_reasons": fail_reasons,
        "timeline_distribution": timeline_dist,
        "requirement_types": requirement_types,
        "compliance": compliance,
    }


@router.get("/department-analytics/sales")
async def get_sales_analytics(
    days: int = 30,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Get Sales department analytics including:
    - SQL eligible count and rate
    - Exit status breakdown
    - Parking status analysis
    - Follow-up cadence compliance
    - Notes quality distribution
    """
    from datetime import datetime, timedelta

    start_date = datetime.utcnow() - timedelta(days=days)

    # Get sales calls
    sales_calls = db.query(CallSummary).filter(
        CallSummary.staff_department == "Sales",
        CallSummary.created_at >= start_date,
    ).all()

    total_calls = len(sales_calls)
    if total_calls == 0:
        return {
            "department": "Sales",
            "period_days": days,
            "total_calls": 0,
            "sql_metrics": {},
            "exit_status": {},
            "parking_analysis": {},
            "notes_quality": {},
            "cadence_compliance": {},
        }

    # SQL metrics
    sql_eligible = sum(1 for c in sales_calls if c.sales_sql_eligible is True)
    sql_metrics = {
        "sql_eligible_count": sql_eligible,
        "sql_eligible_rate": round(sql_eligible / total_calls * 100, 1) if total_calls > 0 else 0,
    }

    # Star rating distribution
    star_dist = {}
    for rating in range(1, 6):
        count = sum(1 for c in sales_calls if c.star_rating == rating)
        star_dist[str(rating)] = {
            "count": count,
            "percentage": round(count / total_calls * 100, 1) if total_calls > 0 else 0,
        }

    # Exit status breakdown
    exit_status = {}
    for call in sales_calls:
        status = call.sales_exit_status or "active"
        exit_status[status] = exit_status.get(status, 0) + 1

    # Parking analysis
    parking_analysis = {
        "parked_with_plan": sum(1 for c in sales_calls if c.sales_parking_status == "parked_with_plan"),
        "parked_no_plan": sum(1 for c in sales_calls if c.sales_parking_status == "parked_no_plan"),
        "active": sum(1 for c in sales_calls if c.sales_parking_status == "active"),
    }

    # High-value parked without plan (compliance issue)
    parked_no_plan_high_value = sum(
        1 for c in sales_calls
        if c.sales_parking_status == "parked_no_plan" and c.star_rating and c.star_rating >= 4
    )
    parking_analysis["high_value_parked_no_plan"] = parked_no_plan_high_value

    # Notes quality distribution
    notes_quality = {}
    for call in sales_calls:
        quality = call.sales_notes_quality or "unknown"
        notes_quality[quality] = notes_quality.get(quality, 0) + 1

    # Cadence compliance
    cadence_compliant = sum(1 for c in sales_calls if c.sales_cadence_compliant is True)
    cadence_compliance = {
        "compliant_count": cadence_compliant,
        "compliance_rate": round(cadence_compliant / total_calls * 100, 1) if total_calls > 0 else 0,
    }

    return {
        "department": "Sales",
        "period_days": days,
        "total_calls": total_calls,
        "star_distribution": star_dist,
        "sql_metrics": sql_metrics,
        "exit_status": exit_status,
        "parking_analysis": parking_analysis,
        "notes_quality": notes_quality,
        "cadence_compliance": cadence_compliance,
    }


@router.get("/department-analytics/call-centre")
async def get_call_centre_analytics(
    days: int = 30,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Get Call Centre department analytics including:
    - Opening compliance rate
    - Call category distribution
    - Satisfaction question rate
    - Customer response breakdown
    - Premium pitch quality
    - WhatsApp handoff stats
    """
    from datetime import datetime, timedelta

    start_date = datetime.utcnow() - timedelta(days=days)

    # Get call centre calls
    cc_calls = db.query(CallSummary).filter(
        CallSummary.staff_department == "Call Centre",
        CallSummary.created_at >= start_date,
    ).all()

    total_calls = len(cc_calls)
    if total_calls == 0:
        return {
            "department": "Call Centre",
            "period_days": days,
            "total_calls": 0,
            "opening_compliance": {},
            "call_categories": {},
            "satisfaction_metrics": {},
            "premium_pitch": {},
            "whatsapp_handoff": {},
        }

    # Opening compliance
    opening_compliant = sum(1 for c in cc_calls if c.cc_opening_compliant is True)
    avg_opening_time = 0
    calls_with_time = [c for c in cc_calls if c.cc_opening_time_seconds is not None]
    if calls_with_time:
        avg_opening_time = sum(c.cc_opening_time_seconds for c in calls_with_time) / len(calls_with_time)

    opening_compliance = {
        "compliant_count": opening_compliant,
        "compliance_rate": round(opening_compliant / total_calls * 100, 1) if total_calls > 0 else 0,
        "avg_opening_time_seconds": round(avg_opening_time, 1),
    }

    # Call category distribution
    call_categories = {}
    for call in cc_calls:
        category = call.cc_call_category or "unknown"
        call_categories[category] = call_categories.get(category, 0) + 1

    # Satisfaction question metrics
    satisfaction_asked = sum(1 for c in cc_calls if c.cc_satisfaction_question_asked is True)
    customer_responses = {}
    for call in cc_calls:
        response = call.cc_customer_response or "not_asked"
        customer_responses[response] = customer_responses.get(response, 0) + 1

    satisfaction_metrics = {
        "question_asked_count": satisfaction_asked,
        "question_asked_rate": round(satisfaction_asked / total_calls * 100, 1) if total_calls > 0 else 0,
        "customer_responses": customer_responses,
    }

    # Premium pitch quality
    premium_pitch = {}
    for call in cc_calls:
        quality = call.cc_premium_pitch_quality or "none"
        premium_pitch[quality] = premium_pitch.get(quality, 0) + 1

    # WhatsApp handoff
    whatsapp_offered = sum(1 for c in cc_calls if c.cc_whatsapp_handoff_valid is not None)
    whatsapp_valid = sum(1 for c in cc_calls if c.cc_whatsapp_handoff_valid is True)
    whatsapp_handoff = {
        "offered_count": whatsapp_offered,
        "valid_count": whatsapp_valid,
        "validity_rate": round(whatsapp_valid / whatsapp_offered * 100, 1) if whatsapp_offered > 0 else 0,
    }

    # Star rating distribution
    star_dist = {}
    for rating in range(1, 6):
        count = sum(1 for c in cc_calls if c.star_rating == rating)
        star_dist[str(rating)] = {
            "count": count,
            "percentage": round(count / total_calls * 100, 1) if total_calls > 0 else 0,
        }

    return {
        "department": "Call Centre",
        "period_days": days,
        "total_calls": total_calls,
        "star_distribution": star_dist,
        "opening_compliance": opening_compliance,
        "call_categories": call_categories,
        "satisfaction_metrics": satisfaction_metrics,
        "premium_pitch": premium_pitch,
        "whatsapp_handoff": whatsapp_handoff,
    }


@router.get("/compliance-alerts")
async def get_compliance_alerts(
    days: int = 30,
    department: str = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Get compliance alerts across all departments.
    Returns calls with compliance issues that need attention.
    """
    from datetime import datetime, timedelta

    start_date = datetime.utcnow() - timedelta(days=days)

    # Build query
    query = db.query(CallSummary).filter(
        CallSummary.created_at >= start_date,
        CallSummary.compliance_alerts.isnot(None),
    )

    if department:
        query = query.filter(CallSummary.staff_department == department)

    calls_with_alerts = query.all()

    alerts = []
    for call in calls_with_alerts:
        if call.compliance_alerts and len(call.compliance_alerts) > 0:
            alerts.append({
                "call_id": call.call_id,
                "staff_name": call.staff_name,
                "staff_department": call.staff_department,
                "star_rating": call.star_rating,
                "alerts": call.compliance_alerts,
                "created_at": call.created_at.isoformat() if call.created_at else None,
            })

    # Group by alert type
    alert_summary = {}
    for alert_item in alerts:
        for alert_text in alert_item.get("alerts", []):
            alert_summary[alert_text] = alert_summary.get(alert_text, 0) + 1

    return {
        "period_days": days,
        "department_filter": department,
        "total_calls_with_alerts": len(alerts),
        "alert_summary": alert_summary,
        "recent_alerts": alerts[:50],  # Return last 50
    }


@router.get("/star-rating-distribution")
async def get_star_rating_distribution(
    days: int = 30,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Get star rating distribution across all departments.
    """
    from datetime import datetime, timedelta

    start_date = datetime.utcnow() - timedelta(days=days)

    # Get all calls with star ratings
    calls = db.query(CallSummary).filter(
        CallSummary.created_at >= start_date,
        CallSummary.star_rating.isnot(None),
    ).all()

    # Overall distribution
    overall = {}
    for rating in range(1, 6):
        count = sum(1 for c in calls if c.star_rating == rating)
        overall[str(rating)] = count

    # By department
    departments = ["Qualifier", "Sales", "Call Centre"]
    by_department = {}

    for dept in departments:
        dept_calls = [c for c in calls if c.staff_department == dept]
        dept_dist = {}
        for rating in range(1, 6):
            count = sum(1 for c in dept_calls if c.star_rating == rating)
            dept_dist[str(rating)] = count
        by_department[dept] = {
            "total": len(dept_calls),
            "distribution": dept_dist,
            "average": round(sum(c.star_rating for c in dept_calls) / len(dept_calls), 2) if dept_calls else 0,
        }

    # By staff member
    by_staff = {}
    for call in calls:
        staff_name = call.staff_name or "Unknown"
        if staff_name not in by_staff:
            by_staff[staff_name] = {
                "department": call.staff_department,
                "total": 0,
                "ratings": [],
            }
        by_staff[staff_name]["total"] += 1
        by_staff[staff_name]["ratings"].append(call.star_rating)

    # Calculate averages for each staff
    for staff_name in by_staff:
        ratings = by_staff[staff_name]["ratings"]
        by_staff[staff_name]["average"] = round(sum(ratings) / len(ratings), 2) if ratings else 0
        by_staff[staff_name]["distribution"] = {
            str(r): ratings.count(r) for r in range(1, 6)
        }
        del by_staff[staff_name]["ratings"]  # Remove raw ratings

    return {
        "period_days": days,
        "total_rated_calls": len(calls),
        "overall_distribution": overall,
        "overall_average": round(sum(c.star_rating for c in calls) / len(calls), 2) if calls else 0,
        "by_department": by_department,
        "by_staff": by_staff,
    }


@router.get("/repeat-caller-analysis")
async def get_repeat_caller_analysis(
    days: int = 30,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Analyze repeat caller patterns.
    """
    from datetime import datetime, timedelta

    start_date = datetime.utcnow() - timedelta(days=days)

    # Get calls marked as repeat caller
    repeat_calls = db.query(CallSummary).filter(
        CallSummary.created_at >= start_date,
        CallSummary.repeat_caller == True,
    ).all()

    # Group by customer phone
    by_phone = {}
    for call in repeat_calls:
        phone = call.customer_phone or "unknown"
        if phone not in by_phone:
            by_phone[phone] = {
                "calls": [],
                "departments_contacted": set(),
            }
        by_phone[phone]["calls"].append({
            "call_id": call.call_id,
            "staff_name": call.staff_name,
            "department": call.staff_department,
            "call_type": call.call_type,
            "created_at": call.created_at.isoformat() if call.created_at else None,
        })
        by_phone[phone]["departments_contacted"].add(call.staff_department)

    # Convert sets to lists for JSON serialization
    for phone in by_phone:
        by_phone[phone]["departments_contacted"] = list(by_phone[phone]["departments_contacted"])
        by_phone[phone]["call_count"] = len(by_phone[phone]["calls"])

    # Sort by call count (most calls first)
    sorted_callers = sorted(by_phone.items(), key=lambda x: x[1]["call_count"], reverse=True)

    # Summary stats
    total_repeat_calls = len(repeat_calls)
    unique_repeat_callers = len(by_phone)

    return {
        "period_days": days,
        "total_repeat_calls": total_repeat_calls,
        "unique_repeat_callers": unique_repeat_callers,
        "top_repeat_callers": dict(sorted_callers[:20]),  # Top 20
    }


@router.get("/future-opportunities")
async def get_future_opportunities(
    days: int = 30,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Get detected future opportunities from calls.
    """
    from datetime import datetime, timedelta

    start_date = datetime.utcnow() - timedelta(days=days)

    # Get calls with future opportunities
    calls = db.query(CallSummary).filter(
        CallSummary.created_at >= start_date,
        CallSummary.future_opportunities.isnot(None),
    ).all()

    # Aggregate opportunities
    opportunity_counts = {}
    opportunity_details = []

    for call in calls:
        if call.future_opportunities and len(call.future_opportunities) > 0:
            for opp in call.future_opportunities:
                opportunity_counts[opp] = opportunity_counts.get(opp, 0) + 1

            opportunity_details.append({
                "call_id": call.call_id,
                "customer_name": call.customer_name,
                "customer_phone": call.customer_phone,
                "opportunities": call.future_opportunities,
                "staff_department": call.staff_department,
                "created_at": call.created_at.isoformat() if call.created_at else None,
            })

    # Sort opportunities by count
    sorted_opportunities = sorted(opportunity_counts.items(), key=lambda x: x[1], reverse=True)

    return {
        "period_days": days,
        "total_calls_with_opportunities": len(calls),
        "opportunity_counts": dict(sorted_opportunities),
        "recent_opportunities": opportunity_details[:50],
    }


@router.get("/industry-interests")
async def get_industry_interests(
    days: int = 30,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Get detected industry interests from calls.
    """
    from datetime import datetime, timedelta

    start_date = datetime.utcnow() - timedelta(days=days)

    # Get calls with industry interests
    calls = db.query(CallSummary).filter(
        CallSummary.created_at >= start_date,
        CallSummary.industry_interests.isnot(None),
    ).all()

    # Aggregate industries
    industry_counts = {}

    for call in calls:
        if call.industry_interests and len(call.industry_interests) > 0:
            for industry in call.industry_interests:
                industry_counts[industry] = industry_counts.get(industry, 0) + 1

    # Sort industries by count
    sorted_industries = sorted(industry_counts.items(), key=lambda x: x[1], reverse=True)

    return {
        "period_days": days,
        "total_calls_with_industry": len(calls),
        "industry_distribution": dict(sorted_industries),
    }
