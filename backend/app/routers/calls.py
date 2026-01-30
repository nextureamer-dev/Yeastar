from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import or_, desc, and_, func
from typing import Optional, List
from datetime import datetime, timedelta

from app.database import get_db
from app.models.call_log import CallLog, CallDirection, CallStatus
from app.models.call_summary import CallSummary
from app.models.contact import Contact
from app.models.user import User
from app.schemas.call_log import CallLogCreate, CallLogResponse, CallLogList, ActiveCall
from app.services.yeastar_client import get_yeastar_client
from app.services.cdr_sync import get_cdr_sync_service
from app.services.auth import get_current_user, get_current_user_required, is_superadmin

router = APIRouter(prefix="/calls", tags=["calls"])


def _parse_yeastar_time(time_str: str) -> str:
    """Parse Yeastar time format (DD/MM/YYYY HH:MM:SS AM/PM) to ISO format."""
    if not time_str:
        return None
    try:
        # Yeastar format: "11/12/2025 05:58:17 PM" (DD/MM/YYYY - European/UAE format)
        from datetime import datetime
        dt = datetime.strptime(time_str, "%d/%m/%Y %I:%M:%S %p")
        return dt.isoformat()
    except ValueError:
        try:
            # Try alternate format without AM/PM
            dt = datetime.strptime(time_str, "%d/%m/%Y %H:%M:%S")
            return dt.isoformat()
        except ValueError:
            # Return as-is if parsing fails
            return time_str


def _transform_cdr(cdr: dict) -> dict:
    """Transform a CDR record from Yeastar format to our API format."""
    # Map call_type to direction
    ct = cdr.get("call_type", "").lower()
    if ct == "outbound":
        direction = "outbound"
    elif ct == "inbound":
        direction = "inbound"
    else:
        direction = "internal"

    # Map disposition to status
    disp = cdr.get("disposition", "").upper()
    if disp == "ANSWERED":
        call_status = "answered"
    elif disp == "NO ANSWER":
        call_status = "no_answer"
    elif disp == "BUSY":
        call_status = "busy"
    elif disp == "VOICEMAIL":
        call_status = "voicemail"
    else:
        call_status = "missed"

    return {
        "id": cdr.get("id"),
        "call_id": cdr.get("uid"),
        "caller_number": cdr.get("call_from_number", ""),
        "callee_number": cdr.get("call_to_number", ""),
        "caller_name": cdr.get("call_from_name", ""),
        "callee_name": cdr.get("call_to_name", ""),
        "direction": direction,
        "status": call_status,
        "extension": cdr.get("call_from_number") if direction == "outbound" else cdr.get("call_to_number"),
        "trunk": cdr.get("dst_trunk") or cdr.get("src_trunk"),
        "start_time": _parse_yeastar_time(cdr.get("time")),
        "duration": cdr.get("duration", 0),
        "talk_duration": cdr.get("talk_duration", 0),
        "ring_duration": cdr.get("ring_duration", 0),
        "recording_file": cdr.get("record_file") or cdr.get("recording"),
    }


@router.get("")
async def list_call_logs(
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    call_type: Optional[str] = Query(None, description="Filter by call type: Inbound, Outbound, Internal"),
    status: Optional[str] = Query(None, description="Filter by status: ANSWERED, NO ANSWER, BUSY, VOICEMAIL"),
    search: Optional[str] = None,
    direction: Optional[str] = Query(None, description="Filter by direction: inbound, outbound, internal"),
    has_summary: Optional[bool] = Query(None, description="Filter by AI summary availability"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_required),
):
    """List call logs directly from Yeastar PBX. Non-superadmin users only see their own calls."""
    client = get_yeastar_client()

    # Check if user needs extension filtering (non-superadmin users)
    user_extension = None
    if current_user and not is_superadmin(current_user):
        user_extension = current_user.extension

    has_filters = bool(search or call_type or status or direction or has_summary is not None or user_extension)

    # If filtering by AI summary, get list of call IDs with summaries first
    summary_call_ids = set()
    if has_summary is not None:
        summaries = db.query(CallSummary.call_id).filter(CallSummary.error_message.is_(None)).all()
        summary_call_ids = {s.call_id for s in summaries}

    # If no filters, directly fetch the requested page from API
    if not has_filters:
        result = await client.get_cdr_list(
            page=page,
            page_size=per_page,
            sort_by="time",
            order_by="desc",
        )

        if not result or result.get("errcode") != 0:
            return {
                "call_logs": [],
                "total": 0,
                "page": page,
                "per_page": per_page,
                "total_pages": 0,
            }

        cdrs = result.get("data", [])
        total = result.get("total_number", 0)
        total_pages = (total + per_page - 1) // per_page

        call_logs = [_transform_cdr(cdr) for cdr in cdrs]

        return {
            "call_logs": call_logs,
            "total": total,
            "page": page,
            "per_page": per_page,
            "total_pages": total_pages,
        }

    # With filters, we need to fetch and filter client-side
    # For employee users, fetch enough data to cover at least 5 days of calls
    all_filtered_logs = []
    current_page = 1
    max_pages = 50 if user_extension else 200  # 50 pages = 5000 records for employees
    fetch_size = 100  # Fetch max per page for efficiency
    total_api_records = 0
    exhausted_api = False
    # For pagination optimization: stop early once we have enough records
    target_records = (page * per_page) + per_page  # Get a bit more than needed

    # Keep fetching until we've gone through all API pages
    while current_page <= max_pages and not exhausted_api:
        result = await client.get_cdr_list(
            page=current_page,
            page_size=fetch_size,
            sort_by="time",
            order_by="desc",
        )

        if not result or result.get("errcode") != 0:
            exhausted_api = True
            break

        cdrs = result.get("data", [])
        if not cdrs:
            exhausted_api = True
            break

        # Track if we've seen all records from API
        total_api_records = result.get("total_number", 0)
        if current_page * fetch_size >= total_api_records:
            exhausted_api = True

        for cdr in cdrs:
            # Apply filters
            cdr_type = cdr.get("call_type", "").lower()
            cdr_disposition = cdr.get("disposition", "").upper()

            # Direction filter (maps to call_type in Yeastar)
            if direction:
                if direction.lower() != cdr_type:
                    continue

            # Call type filter (legacy)
            if call_type and cdr_type != call_type.lower():
                continue

            # Status filter
            if status:
                status_map = {
                    "answered": "ANSWERED",
                    "missed": "NO ANSWER",
                    "no_answer": "NO ANSWER",
                    "busy": "BUSY",
                    "voicemail": "VOICEMAIL",
                }
                expected_disp = status_map.get(status.lower(), status.upper())
                if cdr_disposition != expected_disp:
                    continue

            # Search filter
            if search:
                search_lower = search.lower()
                searchable = (
                    cdr.get("call_from_number", "").lower() +
                    cdr.get("call_to_number", "").lower() +
                    cdr.get("call_from_name", "").lower() +
                    cdr.get("call_to_name", "").lower()
                )
                if search_lower not in searchable:
                    continue

            # User extension filter (non-superadmin users only see their own calls)
            # Uses EXACT match - extension must match exactly, not be a substring
            if user_extension:
                caller = cdr.get("call_from_number", "")
                callee = cdr.get("call_to_number", "")
                # Check if user's extension is EXACTLY the caller or callee
                caller_match = caller == user_extension or caller.endswith(f"/{user_extension}") or caller.startswith(f"{user_extension}/")
                callee_match = callee == user_extension or callee.endswith(f"/{user_extension}") or callee.startswith(f"{user_extension}/")
                if not caller_match and not callee_match:
                    continue

            # AI Summary filter
            call_uid = cdr.get("uid")
            if has_summary is True and call_uid not in summary_call_ids:
                continue
            if has_summary is False and call_uid in summary_call_ids:
                continue

            all_filtered_logs.append(_transform_cdr(cdr))

            # Early termination for employee users once we have enough records
            if user_extension and len(all_filtered_logs) >= target_records:
                exhausted_api = True
                break

        current_page += 1

    # Calculate pagination for filtered results
    total = len(all_filtered_logs)
    total_pages = (total + per_page - 1) // per_page if total > 0 else 0
    start_idx = (page - 1) * per_page
    end_idx = start_idx + per_page
    call_logs = all_filtered_logs[start_idx:end_idx]

    return {
        "call_logs": call_logs,
        "total": total,
        "page": page,
        "per_page": per_page,
        "total_pages": total_pages,
    }


async def _get_call_stats_from_api(days: int, current_user: User):
    """Fallback: Get call statistics directly from Yeastar API when database is empty."""
    client = get_yeastar_client()

    # Check if user needs extension filtering (non-superadmin users)
    user_extension = None
    if current_user and not is_superadmin(current_user):
        user_extension = current_user.extension

    # Calculate the cutoff date for filtering
    cutoff_date = datetime.now() - timedelta(days=days)

    # Initialize counters
    inbound_calls = 0
    outbound_calls = 0
    internal_calls = 0
    answered_calls = 0
    missed_calls = 0
    total_duration = 0
    answered_count_for_avg = 0

    # Fetch pages to calculate stats
    max_pages = 20
    found_older_than_cutoff = False

    for page in range(1, max_pages + 1):
        if found_older_than_cutoff:
            break

        result = await client.get_cdr_list(
            page=page,
            page_size=100,
            sort_by="time",
            order_by="desc",
        )

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

            cdr_call_type = cdr.get("call_type", "").lower()

            # User extension filter
            if user_extension:
                caller = cdr.get("call_from_number", "")
                callee = cdr.get("call_to_number", "")
                caller_match = caller == user_extension or caller.endswith(f"/{user_extension}") or caller.startswith(f"{user_extension}/")
                callee_match = callee == user_extension or callee.endswith(f"/{user_extension}") or callee.startswith(f"{user_extension}/")
                if not caller_match and not callee_match:
                    continue

            disposition = cdr.get("disposition", "").upper()

            if cdr_call_type == "inbound":
                inbound_calls += 1
            elif cdr_call_type == "outbound":
                outbound_calls += 1
            elif cdr_call_type == "internal":
                internal_calls += 1

            if disposition == "ANSWERED":
                answered_calls += 1
                total_duration += cdr.get("talk_duration", 0) or cdr.get("duration", 0)
                answered_count_for_avg += 1
            elif disposition in ("NO ANSWER", "VOICEMAIL", "BUSY"):
                missed_calls += 1

    # Calculate totals
    avg_duration = total_duration / answered_count_for_avg if answered_count_for_avg > 0 else 0
    total_calls = inbound_calls + outbound_calls + internal_calls
    sample_total = total_calls if total_calls > 0 else 1

    return {
        "period_days": days,
        "total_calls": total_calls,
        "inbound_calls": inbound_calls,
        "outbound_calls": outbound_calls,
        "internal_calls": internal_calls,
        "missed_calls": missed_calls,
        "answered_calls": answered_calls,
        "answer_rate": round(answered_calls / sample_total * 100, 1) if sample_total > 0 else 0,
        "average_duration": round(float(avg_duration), 1),
        "source": "api",  # Indicate this came from API fallback
    }


@router.get("/stats")
async def get_call_stats(
    days: int = Query(7, ge=1, le=365),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_required),
):
    """Get call statistics from the local database (fast). Non-superadmin users get stats for their calls only."""
    # Check if database has any records - if not, fall back to API
    total_db_records = db.query(func.count(CallLog.id)).scalar()

    if total_db_records == 0:
        # Database is empty, use API-based approach
        return await _get_call_stats_from_api(days, current_user)

    # Calculate the cutoff date for filtering
    cutoff_date = datetime.now() - timedelta(days=days)

    # Check if user needs extension filtering (non-superadmin users)
    user_extension = None
    if current_user and not is_superadmin(current_user):
        user_extension = current_user.extension

    # Build base query with date filter
    base_filter = CallLog.start_time >= cutoff_date

    # Add extension filter for non-superadmin users
    if user_extension:
        # User's extension can be caller or callee
        extension_filter = or_(
            CallLog.caller_number == user_extension,
            CallLog.callee_number == user_extension,
            CallLog.caller_number.like(f"%/{user_extension}"),
            CallLog.callee_number.like(f"%/{user_extension}"),
            CallLog.caller_number.like(f"{user_extension}/%"),
            CallLog.callee_number.like(f"{user_extension}/%"),
        )
        base_filter = and_(base_filter, extension_filter)

    # Query stats using efficient SQL aggregation
    # Count by direction
    direction_counts = db.query(
        CallLog.direction,
        func.count(CallLog.id).label('count')
    ).filter(base_filter).group_by(CallLog.direction).all()

    # Count by status
    status_counts = db.query(
        CallLog.status,
        func.count(CallLog.id).label('count')
    ).filter(base_filter).group_by(CallLog.status).all()

    # Get total duration for answered calls
    duration_result = db.query(
        func.sum(CallLog.duration).label('total_duration'),
        func.count(CallLog.id).label('answered_count')
    ).filter(
        and_(base_filter, CallLog.status == CallStatus.ANSWERED)
    ).first()

    # Process direction counts
    inbound_calls = 0
    outbound_calls = 0
    internal_calls = 0
    for direction, count in direction_counts:
        if direction == CallDirection.INBOUND:
            inbound_calls = count
        elif direction == CallDirection.OUTBOUND:
            outbound_calls = count
        elif direction == CallDirection.INTERNAL:
            internal_calls = count

    # Process status counts
    answered_calls = 0
    missed_calls = 0
    for status, count in status_counts:
        if status == CallStatus.ANSWERED:
            answered_calls = count
        elif status in (CallStatus.NO_ANSWER, CallStatus.MISSED, CallStatus.VOICEMAIL, CallStatus.BUSY):
            missed_calls += count

    # Calculate totals
    total_duration = duration_result.total_duration or 0 if duration_result else 0
    answered_count_for_avg = duration_result.answered_count or 0 if duration_result else 0
    avg_duration = total_duration / answered_count_for_avg if answered_count_for_avg > 0 else 0
    total_calls = inbound_calls + outbound_calls + internal_calls
    sample_total = total_calls if total_calls > 0 else 1

    return {
        "period_days": days,
        "total_calls": total_calls,
        "inbound_calls": inbound_calls,
        "outbound_calls": outbound_calls,
        "internal_calls": internal_calls,
        "missed_calls": missed_calls,
        "answered_calls": answered_calls,
        "answer_rate": round(answered_calls / sample_total * 100, 1) if sample_total > 0 else 0,
        "average_duration": round(float(avg_duration), 1),
        "source": "database",  # Indicate this came from local database
    }


@router.get("/duration-analytics")
async def get_duration_analytics(
    start_date: str = Query(..., description="Start date in YYYY-MM-DD format"),
    end_date: str = Query(..., description="End date in YYYY-MM-DD format"),
):
    """
    Get call duration analytics for inbound and outbound calls within a date range.
    Returns detailed duration statistics broken down by direction.
    """
    client = get_yeastar_client()

    # Parse dates
    try:
        start_dt = datetime.strptime(start_date, "%Y-%m-%d")
        end_dt = datetime.strptime(end_date, "%Y-%m-%d")
        # Set end_dt to end of day
        end_dt = end_dt.replace(hour=23, minute=59, second=59)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD")

    # Initialize counters
    inbound_stats = {
        "total_calls": 0,
        "answered_calls": 0,
        "missed_calls": 0,
        "total_duration_seconds": 0,
        "total_talk_duration_seconds": 0,
        "total_ring_duration_seconds": 0,
        "durations": [],  # For calculating percentiles
    }
    outbound_stats = {
        "total_calls": 0,
        "answered_calls": 0,
        "missed_calls": 0,
        "total_duration_seconds": 0,
        "total_talk_duration_seconds": 0,
        "total_ring_duration_seconds": 0,
        "durations": [],
    }

    # Duration buckets for distribution
    duration_buckets = {
        "0-30s": {"inbound": 0, "outbound": 0},
        "30s-1m": {"inbound": 0, "outbound": 0},
        "1-2m": {"inbound": 0, "outbound": 0},
        "2-5m": {"inbound": 0, "outbound": 0},
        "5-10m": {"inbound": 0, "outbound": 0},
        "10m+": {"inbound": 0, "outbound": 0},
    }

    # Hourly distribution
    hourly_distribution = {str(h).zfill(2): {"inbound": 0, "outbound": 0, "inbound_duration": 0, "outbound_duration": 0} for h in range(24)}

    # Fetch CDRs page by page
    page = 1
    max_pages = 100  # Safety limit

    while page <= max_pages:
        result = await client.get_cdr_list(
            page=page,
            page_size=100,
            sort_by="time",
            order_by="desc",
        )

        if not result or result.get("errcode") != 0:
            break

        cdrs = result.get("data", [])
        if not cdrs:
            break

        found_older = False
        for cdr in cdrs:
            # Parse call time
            time_str = cdr.get("time", "")
            call_time = None
            if time_str:
                try:
                    call_time = datetime.strptime(time_str, "%d/%m/%Y %I:%M:%S %p")
                except ValueError:
                    try:
                        call_time = datetime.strptime(time_str, "%d/%m/%Y %H:%M:%S")
                    except ValueError:
                        continue

            if not call_time:
                continue

            # Check if within date range
            if call_time < start_dt:
                found_older = True
                continue
            if call_time > end_dt:
                continue

            call_type = cdr.get("call_type", "").lower()
            disposition = cdr.get("disposition", "").upper()
            duration = cdr.get("duration", 0) or 0
            talk_duration = cdr.get("talk_duration", 0) or 0
            ring_duration = cdr.get("ring_duration", 0) or 0

            # Determine which stats to update
            if call_type == "inbound":
                stats = inbound_stats
                direction = "inbound"
            elif call_type == "outbound":
                stats = outbound_stats
                direction = "outbound"
            else:
                continue  # Skip internal calls

            stats["total_calls"] += 1

            if disposition == "ANSWERED":
                stats["answered_calls"] += 1
                stats["total_duration_seconds"] += duration
                stats["total_talk_duration_seconds"] += talk_duration
                stats["total_ring_duration_seconds"] += ring_duration
                stats["durations"].append(talk_duration if talk_duration > 0 else duration)

                # Duration bucket
                if duration <= 30:
                    duration_buckets["0-30s"][direction] += 1
                elif duration <= 60:
                    duration_buckets["30s-1m"][direction] += 1
                elif duration <= 120:
                    duration_buckets["1-2m"][direction] += 1
                elif duration <= 300:
                    duration_buckets["2-5m"][direction] += 1
                elif duration <= 600:
                    duration_buckets["5-10m"][direction] += 1
                else:
                    duration_buckets["10m+"][direction] += 1

                # Hourly distribution
                hour = str(call_time.hour).zfill(2)
                hourly_distribution[hour][direction] += 1
                hourly_distribution[hour][f"{direction}_duration"] += duration
            else:
                stats["missed_calls"] += 1

        # If we found calls older than start_date, we can stop
        if found_older:
            break

        page += 1

    # Calculate averages and percentiles
    def calc_stats(stats_dict):
        durations = sorted(stats_dict["durations"])
        count = len(durations)

        if count > 0:
            avg = sum(durations) / count
            median = durations[count // 2] if count % 2 == 1 else (durations[count // 2 - 1] + durations[count // 2]) / 2
            p90 = durations[int(count * 0.9)] if count >= 10 else durations[-1]
            min_dur = durations[0]
            max_dur = durations[-1]
        else:
            avg = median = p90 = min_dur = max_dur = 0

        return {
            "total_calls": stats_dict["total_calls"],
            "answered_calls": stats_dict["answered_calls"],
            "missed_calls": stats_dict["missed_calls"],
            "answer_rate": round(stats_dict["answered_calls"] / stats_dict["total_calls"] * 100, 1) if stats_dict["total_calls"] > 0 else 0,
            "total_duration_seconds": stats_dict["total_duration_seconds"],
            "total_talk_duration_seconds": stats_dict["total_talk_duration_seconds"],
            "avg_duration_seconds": round(avg, 1),
            "median_duration_seconds": round(median, 1),
            "p90_duration_seconds": round(p90, 1),
            "min_duration_seconds": min_dur,
            "max_duration_seconds": max_dur,
            "total_duration_formatted": _format_duration(stats_dict["total_duration_seconds"]),
            "avg_duration_formatted": _format_duration(avg),
        }

    def _format_duration(seconds):
        """Format seconds to human readable duration."""
        if seconds < 60:
            return f"{int(seconds)}s"
        elif seconds < 3600:
            mins = int(seconds // 60)
            secs = int(seconds % 60)
            return f"{mins}m {secs}s"
        else:
            hours = int(seconds // 3600)
            mins = int((seconds % 3600) // 60)
            return f"{hours}h {mins}m"

    return {
        "start_date": start_date,
        "end_date": end_date,
        "inbound": calc_stats(inbound_stats),
        "outbound": calc_stats(outbound_stats),
        "duration_distribution": duration_buckets,
        "hourly_distribution": hourly_distribution,
        "combined": {
            "total_calls": inbound_stats["total_calls"] + outbound_stats["total_calls"],
            "answered_calls": inbound_stats["answered_calls"] + outbound_stats["answered_calls"],
            "total_duration_seconds": inbound_stats["total_duration_seconds"] + outbound_stats["total_duration_seconds"],
            "total_duration_formatted": _format_duration(inbound_stats["total_duration_seconds"] + outbound_stats["total_duration_seconds"]),
        }
    }


@router.get("/active", response_model=List[ActiveCall])
async def get_active_calls():
    """Get currently active calls from PBX."""
    client = get_yeastar_client()

    inbound = await client.get_inbound_calls() or []
    outbound = await client.get_outbound_calls() or []

    active_calls = []

    for call in inbound:
        active_calls.append(ActiveCall(
            call_id=call.get("callid", ""),
            caller=call.get("from", ""),
            callee=call.get("to", ""),
            extension=call.get("ext", ""),
            direction="inbound",
            status=call.get("status", ""),
            duration=0,
        ))

    for call in outbound:
        active_calls.append(ActiveCall(
            call_id=call.get("callid", ""),
            caller=call.get("from", ""),
            callee=call.get("to", ""),
            extension=call.get("ext", ""),
            direction="outbound",
            status=call.get("status", ""),
            duration=0,
        ))

    return active_calls


@router.get("/detail/{call_id}", response_model=CallLogResponse)
def get_call_log(call_id: int, db: Session = Depends(get_db)):
    """Get a specific call log by ID."""
    call = db.query(CallLog).filter(CallLog.id == call_id).first()
    if not call:
        raise HTTPException(status_code=404, detail="Call log not found")

    contact_name = None
    if call.contact_id:
        contact = db.query(Contact).filter(Contact.id == call.contact_id).first()
        if contact:
            contact_name = contact.full_name

    return CallLogResponse(
        id=call.id,
        call_id=call.call_id,
        contact_id=call.contact_id,
        contact_name=contact_name,
        caller_number=call.caller_number,
        callee_number=call.callee_number,
        caller_name=call.caller_name,
        callee_name=call.callee_name,
        direction=call.direction,
        status=call.status,
        extension=call.extension,
        trunk=call.trunk,
        start_time=call.start_time,
        answer_time=call.answer_time,
        end_time=call.end_time,
        duration=call.duration,
        ring_duration=call.ring_duration,
        recording_file=call.recording_file,
        notes=call.notes,
        created_at=call.created_at,
    )


@router.post("/{call_id}/notes")
def add_call_notes(
    call_id: int,
    notes: str,
    db: Session = Depends(get_db),
):
    """Add notes to a call log."""
    call = db.query(CallLog).filter(CallLog.id == call_id).first()
    if not call:
        raise HTTPException(status_code=404, detail="Call log not found")

    call.notes = notes
    db.commit()
    return {"status": "success"}


@router.get("/db-status")
async def get_database_status(
    db: Session = Depends(get_db),
):
    """Check the status of call logs in the local database (for debugging)."""
    from sqlalchemy import text

    total_records = db.query(func.count(CallLog.id)).scalar()

    # Get date range of records
    oldest_record = db.query(func.min(CallLog.start_time)).scalar()
    newest_record = db.query(func.max(CallLog.start_time)).scalar()

    # Count by direction using raw SQL to avoid enum conversion issues
    try:
        result = db.execute(text("SELECT direction, COUNT(*) as cnt FROM call_logs GROUP BY direction"))
        direction_counts = {str(row[0]): row[1] for row in result.fetchall()}
    except Exception:
        direction_counts = {}

    # Count records in last 7 days
    cutoff_7_days = datetime.now() - timedelta(days=7)
    records_last_7_days = db.query(func.count(CallLog.id)).filter(
        CallLog.start_time >= cutoff_7_days
    ).scalar()

    return {
        "total_records": total_records,
        "oldest_record": oldest_record.isoformat() if oldest_record else None,
        "newest_record": newest_record.isoformat() if newest_record else None,
        "records_last_7_days": records_last_7_days,
        "by_direction": direction_counts,
        "database_ready": total_records > 0,
    }


def _is_success(result: dict) -> bool:
    """Check if PBX API response indicates success (handles both Cloud and on-premise)."""
    if not result:
        return False
    # Cloud PBX returns errcode=0 for success
    if result.get("errcode") == 0:
        return True
    # On-premise returns status="Success"
    if result.get("status") == "Success":
        return True
    return False


def _get_error_message(result: dict, default: str = "Operation failed") -> str:
    """Extract error message from PBX API response."""
    if not result:
        return "PBX connection failed"
    return result.get("errmsg") or result.get("msg") or default


@router.post("/dial")
async def make_call(
    extension: str = Query(..., description="Extension making the call"),
    number: str = Query(..., description="Number to dial"),
):
    """Initiate an outbound call from an extension."""
    client = get_yeastar_client()
    result = await client.make_call(extension, number)

    if _is_success(result):
        return {"status": "success", "message": "Call initiated"}
    else:
        raise HTTPException(
            status_code=400,
            detail=_get_error_message(result, "Failed to initiate call")
        )


@router.post("/hangup")
async def hangup_call(
    extension: str = Query(..., description="Extension to hang up"),
):
    """Hang up a call on an extension."""
    client = get_yeastar_client()
    result = await client.hangup_call(extension)

    if _is_success(result):
        return {"status": "success", "message": "Call ended"}
    else:
        raise HTTPException(
            status_code=400,
            detail=_get_error_message(result, "Failed to hang up")
        )


@router.post("/hold")
async def hold_call(
    extension: str = Query(..., description="Extension to hold"),
):
    """Put a call on hold."""
    client = get_yeastar_client()
    result = await client.hold_call(extension)

    if _is_success(result):
        return {"status": "success", "message": "Call on hold"}
    else:
        raise HTTPException(
            status_code=400,
            detail=_get_error_message(result, "Failed to hold call")
        )


@router.post("/unhold")
async def unhold_call(
    extension: str = Query(..., description="Extension to resume"),
):
    """Resume a held call."""
    client = get_yeastar_client()
    result = await client.unhold_call(extension)

    if _is_success(result):
        return {"status": "success", "message": "Call resumed"}
    else:
        raise HTTPException(
            status_code=400,
            detail=_get_error_message(result, "Failed to resume call")
        )


@router.post("/transfer")
async def transfer_call(
    extension: str = Query(..., description="Extension transferring the call"),
    transfer_to: str = Query(..., description="Number to transfer to"),
):
    """Transfer a call to another number."""
    client = get_yeastar_client()
    result = await client.transfer_call(extension, transfer_to)

    if _is_success(result):
        return {"status": "success", "message": "Call transferred"}
    else:
        raise HTTPException(
            status_code=400,
            detail=_get_error_message(result, "Failed to transfer call")
        )


@router.post("/sync")
async def sync_cdr(
    hours: int = Query(None, ge=1, le=168, description="Number of hours to sync"),
    days: int = Query(None, ge=1, le=30, description="Number of days to sync (alternative to hours)"),
    max_pages: int = Query(20, ge=1, le=100, description="Maximum pages to fetch (100 records per page)"),
):
    """Manually trigger CDR sync from PBX. Syncs call records to local database for fast stats."""
    cdr_service = get_cdr_sync_service()

    # Determine sync period
    if days:
        sync_hours = days * 24
    elif hours:
        sync_hours = hours
    else:
        sync_hours = 24  # Default to 24 hours

    synced = await cdr_service._sync_cloud_cdrs(max_pages=max_pages)
    return {
        "status": "success",
        "message": f"Synced {synced} CDR records to local database",
        "synced_count": synced,
        "max_pages": max_pages,
    }


@router.get("/recording/{recording_id}")
async def get_recording_url(recording_id: str):
    """Get download URL for a call recording."""
    client = get_yeastar_client()
    result = await client.download_recording(recording_id)

    if result and result.get("status") == "Success":
        return {
            "status": "success",
            "download_url": result.get("download_url"),
        }
    else:
        raise HTTPException(
            status_code=404,
            detail=_get_error_message(result, "Recording not found or unavailable")
        )
