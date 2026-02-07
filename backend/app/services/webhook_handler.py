import json
import logging
import asyncio
from datetime import datetime
from typing import Dict, Any, List, Callable
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models.call_log import CallLog, CallDirection, CallStatus
from app.models.extension import Extension, ExtensionStatus
from app.models.contact import Contact
from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

# Event subscribers
_subscribers: Dict[str, List[Callable]] = {}


def subscribe(event_type: str, callback: Callable):
    """Subscribe to a specific event type."""
    if event_type not in _subscribers:
        _subscribers[event_type] = []
    _subscribers[event_type].append(callback)


def notify_subscribers(event_type: str, data: Dict[str, Any]):
    """Notify all subscribers of an event."""
    for callback in _subscribers.get(event_type, []):
        try:
            callback(data)
        except Exception as e:
            logger.error(f"Error notifying subscriber: {e}")


def lookup_contact(db: Session, phone_number: str):
    """Lookup contact by phone number."""
    if not phone_number:
        return None

    normalized = "".join(c for c in phone_number if c.isdigit() or c == "+")
    if len(normalized) < 4:
        return None

    from sqlalchemy import or_
    contact = db.query(Contact).filter(
        or_(
            Contact.phone.contains(normalized[-10:]),
            Contact.phone_secondary.contains(normalized[-10:]),
        )
    ).first()
    return contact


def handle_call_event(event_data: Dict[str, Any]):
    """
    Handle call events from Yeastar PBX.

    Event types from Yeastar:
    - NewCdr: Call completed with CDR data
    - Ringing: Incoming call ringing
    - Ring: Outbound call ringing
    - AnswerCall: Call answered
    - ALERT: Extension state change
    """
    event_type = event_data.get("event") or event_data.get("action")

    if not event_type:
        logger.warning(f"Unknown event format: {event_data}")
        return

    db = SessionLocal()
    try:
        if event_type == "NewCdr":
            handle_new_cdr(db, event_data)
        elif event_type in ["Ringing", "Ring"]:
            handle_ringing(db, event_data)
        elif event_type == "AnswerCall":
            handle_answer(db, event_data)
        elif event_type == "Hangup":
            handle_hangup(db, event_data)
        elif event_type == "ALERT":
            handle_extension_alert(db, event_data)

        # Notify WebSocket subscribers
        notify_subscribers(event_type, event_data)

    except Exception as e:
        logger.error(f"Error handling event {event_type}: {e}")
        db.rollback()
    finally:
        db.close()


def handle_new_cdr(db: Session, data: Dict[str, Any]):
    """Handle CDR (Call Detail Record) event - call completed."""
    call_id = data.get("callid")

    # Check if already logged
    existing = db.query(CallLog).filter(CallLog.call_id == call_id).first()
    if existing:
        return

    # Determine direction
    direction = CallDirection.INBOUND
    if data.get("outbound") == "yes":
        direction = CallDirection.OUTBOUND
    elif data.get("internal") == "yes":
        direction = CallDirection.INTERNAL

    # Determine status
    status_map = {
        "ANSWERED": CallStatus.ANSWERED,
        "NO ANSWER": CallStatus.NO_ANSWER,
        "BUSY": CallStatus.BUSY,
        "FAILED": CallStatus.FAILED,
    }
    status = status_map.get(data.get("disposition", "").upper(), CallStatus.MISSED)

    # Parse times
    start_time = datetime.now()
    if data.get("start"):
        try:
            start_time = datetime.fromisoformat(data["start"].replace(" ", "T"))
        except ValueError:
            pass

    answer_time = None
    if data.get("answer"):
        try:
            answer_time = datetime.fromisoformat(data["answer"].replace(" ", "T"))
        except ValueError:
            pass

    end_time = None
    if data.get("end"):
        try:
            end_time = datetime.fromisoformat(data["end"].replace(" ", "T"))
        except ValueError:
            pass

    # Lookup contact
    caller = data.get("src") or data.get("callerid", "")
    callee = data.get("dst") or data.get("destination", "")
    phone_to_lookup = caller if direction == CallDirection.INBOUND else callee

    contact = lookup_contact(db, phone_to_lookup)

    call_log = CallLog(
        call_id=call_id,
        contact_id=contact.id if contact else None,
        caller_number=caller,
        callee_number=callee,
        caller_name=data.get("callername"),
        callee_name=contact.full_name if contact else None,
        direction=direction,
        status=status,
        extension=data.get("ext") or data.get("extid"),
        trunk=data.get("trunk"),
        start_time=start_time,
        answer_time=answer_time,
        end_time=end_time,
        duration=int(data.get("duration", 0)),
        ring_duration=int(data.get("ringtime", 0)),
        recording_file=data.get("recording"),
    )

    db.add(call_log)
    db.commit()
    logger.info(f"Created call log for call {call_id}")

    # === AUTO-PROCESSING: Trigger AI transcription for inbound/outbound calls ===
    if settings.auto_process_calls:
        # Skip internal calls per requirements
        if direction == CallDirection.INTERNAL and not settings.process_internal_calls:
            logger.info(f"Skipping internal call {call_id} for auto-processing")
            return

        # Only process answered calls with recordings
        recording_file = data.get("recording")
        if recording_file and status == CallStatus.ANSWERED:
            # Trigger async processing (non-blocking)
            try:
                asyncio.create_task(
                    _trigger_transcription_processing(
                        call_id=call_id,
                        recording_file=recording_file,
                    )
                )
                logger.info(f"Queued call {call_id} for automatic AI transcription")
            except RuntimeError:
                # No event loop running (sync context) - use alternative
                logger.info(f"Scheduling call {call_id} for AI transcription (deferred)")
                _schedule_transcription_processing(call_id, recording_file)


def handle_ringing(db: Session, data: Dict[str, Any]):
    """Handle incoming/outgoing call ringing."""
    extension_number = data.get("ext") or data.get("extid")
    if not extension_number:
        return

    extension = db.query(Extension).filter(
        Extension.extension_number == extension_number
    ).first()

    if extension:
        extension.status = ExtensionStatus.RINGING
        extension.current_call_id = data.get("callid")
        extension.current_caller = data.get("callerid") or data.get("from")
        db.commit()

    # Notify for call popup
    caller = data.get("callerid") or data.get("from", "")
    contact = lookup_contact(db, caller)

    popup_data = {
        "event": "incoming_call",
        "extension": extension_number,
        "caller": caller,
        "call_id": data.get("callid"),
        "contact": {
            "id": contact.id,
            "name": contact.full_name,
            "company": contact.company,
            "phone": contact.phone,
        } if contact else None,
    }
    notify_subscribers("call_popup", popup_data)


def handle_answer(db: Session, data: Dict[str, Any]):
    """Handle call answered event."""
    extension_number = data.get("ext") or data.get("extid")
    if not extension_number:
        return

    extension = db.query(Extension).filter(
        Extension.extension_number == extension_number
    ).first()

    if extension:
        extension.status = ExtensionStatus.ON_CALL
        db.commit()


def handle_hangup(db: Session, data: Dict[str, Any]):
    """Handle call hangup event."""
    extension_number = data.get("ext") or data.get("extid")
    if not extension_number:
        return

    extension = db.query(Extension).filter(
        Extension.extension_number == extension_number
    ).first()

    if extension:
        extension.status = ExtensionStatus.AVAILABLE
        extension.current_call_id = None
        extension.current_caller = None
        db.commit()


def handle_extension_alert(db: Session, data: Dict[str, Any]):
    """Handle extension status change."""
    extension_number = data.get("ext") or data.get("extid")
    status_str = data.get("status", "").lower()

    if not extension_number:
        return

    status_map = {
        "available": ExtensionStatus.AVAILABLE,
        "idle": ExtensionStatus.AVAILABLE,
        "ringing": ExtensionStatus.RINGING,
        "talking": ExtensionStatus.ON_CALL,
        "busy": ExtensionStatus.BUSY,
        "dnd": ExtensionStatus.DND,
        "unavailable": ExtensionStatus.OFFLINE,
    }

    extension = db.query(Extension).filter(
        Extension.extension_number == extension_number
    ).first()

    if extension:
        extension.status = status_map.get(status_str, ExtensionStatus.AVAILABLE)
        extension.is_registered = status_str != "unavailable"
        extension.last_seen = datetime.utcnow()
        db.commit()


# === Auto-processing helper functions ===

async def _trigger_transcription_processing(call_id: str, recording_file: str):
    """
    Async task to process call recording without blocking webhook response.
    Downloads recording, transcribes with Riva NIM, and analyzes with Llama 3 70B.
    """
    from app.routers.transcription import _process_recording_task
    from app.services.processing_tracker import get_processing_tracker

    if get_processing_tracker().is_processing(call_id):
        logger.info(f"Webhook: skipping {call_id}, already being processed")
        return

    try:
        # Small delay to ensure recording is fully saved on PBX
        await asyncio.sleep(5)

        logger.info(f"Starting auto-processing for call {call_id}")

        # Process the recording
        await _process_recording_task(
            call_id=call_id,
            recording_file=recording_file,
            force=False,
        )

        logger.info(f"Auto-processing completed for call {call_id}")

    except Exception as e:
        logger.error(f"Auto-processing failed for call {call_id}: {e}")


def _schedule_transcription_processing(call_id: str, recording_file: str):
    """
    Schedule transcription processing when no event loop is available.
    This is a fallback for synchronous contexts.
    """
    import threading

    def run_in_thread():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(
                _trigger_transcription_processing(call_id, recording_file)
            )
        finally:
            loop.close()

    thread = threading.Thread(target=run_in_thread, daemon=True)
    thread.start()
