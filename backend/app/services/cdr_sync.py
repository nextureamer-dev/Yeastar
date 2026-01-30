import asyncio
import logging
from datetime import datetime, timedelta
from typing import Optional, Tuple
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models.call_log import CallLog, CallDirection, CallStatus
from app.models.contact import Contact
from app.services.yeastar_client import get_yeastar_client
from app.config import get_settings

logger = logging.getLogger(__name__)


class CDRSyncService:
    """Service for synchronizing CDR (Call Detail Records) from Yeastar PBX."""

    def __init__(self):
        self.client = get_yeastar_client()
        self._running = False
        self._task: Optional[asyncio.Task] = None

    async def start(self, interval_minutes: int = 5):
        """Start periodic CDR sync."""
        if self._running:
            logger.warning("CDR sync already running")
            return

        self._running = True
        self._task = asyncio.create_task(self._sync_loop(interval_minutes))
        logger.info(f"CDR sync started with {interval_minutes} minute interval")

    async def stop(self):
        """Stop periodic CDR sync."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("CDR sync stopped")

    async def _sync_loop(self, interval_minutes: int):
        """Main sync loop."""
        while self._running:
            try:
                # For live sync, only fetch last 5 pages (500 records max)
                synced = await self._sync_cloud_cdrs(max_pages=5)
                if synced > 0:
                    logger.info(f"Live sync: {synced} new CDR records")
            except Exception as e:
                logger.error(f"Error in CDR sync: {e}")

            await asyncio.sleep(interval_minutes * 60)

    async def sync_recent_cdrs(self, hours: int = 24, max_records: int = 1000):
        """Sync CDRs from the PBX."""
        from app.config import get_settings
        settings = get_settings()

        logger.info(f"Syncing CDRs (max {max_records} records)")

        # For Cloud PBX, use the CDR list endpoint
        if settings.is_cloud_pbx:
            return await self._sync_cloud_cdrs(max_records)

        # For on-premise, use the original download method
        end_time = datetime.now()
        start_time = end_time - timedelta(hours=hours)

        result = await self.client.download_cdr(
            start_time=start_time.strftime("%Y-%m-%d %H:%M:%S"),
            end_time=end_time.strftime("%Y-%m-%d %H:%M:%S"),
        )

        if not result:
            logger.warning("Failed to download CDR")
            return 0

        if result.get("status") != "Success":
            logger.warning(f"CDR download failed: {result.get('errmsg')}")
            return 0

        cdrs = result.get("cdr", [])
        logger.info(f"Downloaded {len(cdrs)} CDR records")

        synced = 0
        db = SessionLocal()
        try:
            for cdr in cdrs:
                if self._process_cdr(db, cdr):
                    synced += 1
            db.commit()
        except Exception as e:
            logger.error(f"Error processing CDRs: {e}")
            db.rollback()
        finally:
            db.close()

        logger.info(f"Synced {synced} new CDR records")
        return synced

    async def _sync_cloud_cdrs(self, max_records: int = 100000, max_pages: int = None):
        """Sync CDRs from Cloud PBX."""
        synced = 0
        page = 1
        page_size = 100
        calls_to_process = []  # Collect new calls for AI processing
        settings = get_settings()

        db = SessionLocal()
        try:
            while True:
                result = await self.client.get_cdr_list(
                    page=page,
                    page_size=page_size,
                    sort_by="time",
                    order_by="desc",
                )

                if not result or result.get("errcode") != 0:
                    logger.warning(f"Failed to get CDR list: {result}")
                    break

                cdrs = result.get("data", [])
                if not cdrs:
                    break

                logger.info(f"Processing page {page} with {len(cdrs)} records")

                for cdr in cdrs:
                    new_call_info = self._process_cloud_cdr(db, cdr)
                    if new_call_info:
                        synced += 1
                        # Collect for AI processing if it has recording
                        if new_call_info.get("recording") and new_call_info.get("should_process"):
                            calls_to_process.append(new_call_info)

                db.commit()

                # If we got fewer records than page_size, we're done
                if len(cdrs) < page_size:
                    break

                page += 1

                # Safety limit - allow up to 600 pages (60,000 records) or max_pages
                page_limit = max_pages if max_pages else 600
                if page > page_limit:
                    break

        except Exception as e:
            logger.error(f"Error processing Cloud CDRs: {e}")
            db.rollback()
        finally:
            db.close()

        logger.info(f"Synced {synced} new CDR records from Cloud PBX")

        # Trigger AI processing for new calls with recordings
        if settings.auto_process_calls and calls_to_process:
            logger.info(f"Queuing {len(calls_to_process)} calls for AI processing")
            for call_info in calls_to_process:
                asyncio.create_task(
                    self._trigger_ai_processing(call_info["call_id"], call_info["recording"])
                )

        return synced

    async def _trigger_ai_processing(self, call_id: str, recording_file: str):
        """Trigger AI processing for a call."""
        from app.routers.transcription import _process_recording_task

        try:
            # Small delay to avoid overwhelming the AI service
            await asyncio.sleep(2)
            logger.info(f"Starting AI processing for call {call_id}")
            await _process_recording_task(call_id=call_id, recording_file=recording_file, force=False)
            logger.info(f"AI processing completed for call {call_id}")
        except Exception as e:
            logger.error(f"AI processing failed for call {call_id}: {e}")

    def _process_cloud_cdr(self, db: Session, cdr: dict) -> Optional[dict]:
        """Process a single CDR record from Cloud PBX.

        Returns dict with call info if new call was created, None otherwise.
        """
        settings = get_settings()
        call_id = cdr.get("uid")
        if not call_id:
            return None

        # Check if already exists
        existing = db.query(CallLog).filter(CallLog.call_id == call_id).first()
        if existing:
            return None

        # Determine direction from call_type
        call_type = cdr.get("call_type", "").lower()
        if call_type == "outbound":
            direction = CallDirection.OUTBOUND
        elif call_type == "inbound":
            direction = CallDirection.INBOUND
        else:
            direction = CallDirection.INTERNAL

        # Determine status from disposition
        disposition = cdr.get("disposition", "").upper()
        if disposition == "ANSWERED":
            status = CallStatus.ANSWERED
        elif disposition == "NO ANSWER":
            status = CallStatus.NO_ANSWER
        elif disposition == "BUSY":
            status = CallStatus.BUSY
        elif disposition == "VOICEMAIL":
            status = CallStatus.VOICEMAIL
        elif disposition in ("FAILED", "CONGESTION"):
            status = CallStatus.FAILED
        else:
            status = CallStatus.MISSED

        # Parse time from Cloud PBX format (e.g., "18/10/2025 03:10:26 PM")
        start_time = self._parse_cloud_time(cdr.get("time"))

        # Get caller/callee info
        caller_number = cdr.get("call_from_number", "")
        caller_name = cdr.get("call_from_name", "")
        callee_number = cdr.get("call_to_number", "")
        callee_name = cdr.get("call_to_name", "")
        recording_file = cdr.get("record_file") or cdr.get("recording")

        # Lookup contact
        phone_to_lookup = caller_number if direction == CallDirection.INBOUND else callee_number
        contact = self._lookup_contact(db, phone_to_lookup)

        try:
            # Create call log
            call_log = CallLog(
                call_id=call_id,
                contact_id=contact.id if contact else None,
                caller_number=caller_number,
                callee_number=callee_number,
                caller_name=caller_name,
                callee_name=callee_name or (contact.full_name if contact else None),
                direction=direction,
                status=status,
                extension=caller_number if direction == CallDirection.OUTBOUND else callee_number,
                trunk=cdr.get("dst_trunk") or cdr.get("src_trunk"),
                start_time=start_time or datetime.now(),
                answer_time=None,
                end_time=None,
                duration=int(cdr.get("duration", 0)),
                ring_duration=int(cdr.get("ring_duration", 0)),
                recording_file=recording_file,
            )

            db.add(call_log)
            db.flush()  # Flush to catch integrity errors early

            # Determine if this call should be processed for AI
            # Only process answered inbound/outbound calls with recordings
            should_process = (
                status == CallStatus.ANSWERED
                and recording_file
                and (
                    direction != CallDirection.INTERNAL
                    or settings.process_internal_calls
                )
            )

            return {
                "call_id": call_id,
                "recording": recording_file,
                "direction": direction.value,
                "status": status.value,
                "should_process": should_process,
            }
        except Exception as e:
            logger.warning(f"Failed to insert CDR {call_id}: {e}")
            db.rollback()
            return None

    def _parse_cloud_time(self, time_str: Optional[str]) -> Optional[datetime]:
        """Parse Cloud PBX time format."""
        if not time_str:
            return None

        # Cloud PBX format: "18/10/2025 03:10:26 PM"
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

    def _process_cdr(self, db: Session, cdr: dict) -> bool:
        """Process a single CDR record."""
        call_id = cdr.get("callid") or cdr.get("uniqueid")
        if not call_id:
            return False

        # Check if already exists
        existing = db.query(CallLog).filter(CallLog.call_id == call_id).first()
        if existing:
            return False

        # Determine direction
        direction = CallDirection.INBOUND
        if cdr.get("type") == "Outbound" or cdr.get("outbound") == "yes":
            direction = CallDirection.OUTBOUND
        elif cdr.get("type") == "Internal" or cdr.get("internal") == "yes":
            direction = CallDirection.INTERNAL

        # Determine status
        disposition = cdr.get("disposition", "").upper()
        status_map = {
            "ANSWERED": CallStatus.ANSWERED,
            "NO ANSWER": CallStatus.NO_ANSWER,
            "BUSY": CallStatus.BUSY,
            "FAILED": CallStatus.FAILED,
            "CONGESTION": CallStatus.FAILED,
        }
        status = status_map.get(disposition, CallStatus.MISSED)

        # Parse times
        start_time = self._parse_time(cdr.get("start") or cdr.get("calldate"))
        answer_time = self._parse_time(cdr.get("answer"))
        end_time = self._parse_time(cdr.get("end"))

        # Get caller/callee info
        caller = cdr.get("src") or cdr.get("callerid", "")
        callee = cdr.get("dst") or cdr.get("destination", "")

        # Lookup contact
        phone_to_lookup = caller if direction == CallDirection.INBOUND else callee
        contact = self._lookup_contact(db, phone_to_lookup)

        # Create call log
        call_log = CallLog(
            call_id=call_id,
            contact_id=contact.id if contact else None,
            caller_number=caller,
            callee_number=callee,
            caller_name=cdr.get("callername") or cdr.get("src_name"),
            callee_name=contact.full_name if contact else cdr.get("dst_name"),
            direction=direction,
            status=status,
            extension=cdr.get("ext") or cdr.get("dstchannel", "").split("/")[0] if "/" in cdr.get("dstchannel", "") else None,
            trunk=cdr.get("trunk") or cdr.get("dstchannel"),
            start_time=start_time or datetime.now(),
            answer_time=answer_time,
            end_time=end_time,
            duration=int(cdr.get("duration", 0) or cdr.get("billsec", 0)),
            ring_duration=int(cdr.get("ringtime", 0)),
            recording_file=cdr.get("recording") or cdr.get("recordfile"),
        )

        db.add(call_log)
        return True

    def _parse_time(self, time_str: Optional[str]) -> Optional[datetime]:
        """Parse time string to datetime."""
        if not time_str:
            return None

        formats = [
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%dT%H:%M:%S",
            "%Y/%m/%d %H:%M:%S",
        ]

        for fmt in formats:
            try:
                return datetime.strptime(time_str, fmt)
            except ValueError:
                continue

        return None

    def _lookup_contact(self, db: Session, phone: str) -> Optional[Contact]:
        """Lookup contact by phone number."""
        if not phone or len(phone) < 4:
            return None

        normalized = "".join(c for c in phone if c.isdigit() or c == "+")
        if len(normalized) < 4:
            return None

        from sqlalchemy import or_
        return db.query(Contact).filter(
            or_(
                Contact.phone.contains(normalized[-10:]),
                Contact.phone_secondary.contains(normalized[-10:]),
            )
        ).first()


# Global instance
_cdr_sync_service: Optional[CDRSyncService] = None


def get_cdr_sync_service() -> CDRSyncService:
    """Get or create the CDR sync service instance."""
    global _cdr_sync_service
    if _cdr_sync_service is None:
        _cdr_sync_service = CDRSyncService()
    return _cdr_sync_service
