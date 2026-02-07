"""Thread-safe tracker for call IDs currently being processed.

Prevents duplicate processing when multiple sources (API endpoint, CDR poller,
webhook handler, CDR sync) try to process the same call simultaneously.

Uses threading.Lock (not asyncio.Lock) because callers span multiple threads
and event loops (main FastAPI loop, CDR poller thread, webhook sync-fallback thread).
"""

import threading
import logging
from typing import Set

logger = logging.getLogger(__name__)


class ProcessingTracker:

    def __init__(self):
        self._lock = threading.Lock()
        self._processing: Set[str] = set()

    def try_acquire(self, call_id: str) -> bool:
        """Atomically check and mark a call_id as processing.

        Returns True if this caller acquired the slot (proceed with processing).
        Returns False if the call_id is already being processed (skip).
        """
        with self._lock:
            if call_id in self._processing:
                return False
            self._processing.add(call_id)
            return True

    def release(self, call_id: str) -> None:
        """Mark a call_id as no longer processing."""
        with self._lock:
            self._processing.discard(call_id)

    def is_processing(self, call_id: str) -> bool:
        """Check if a call_id is currently being processed."""
        with self._lock:
            return call_id in self._processing

    def active_count(self) -> int:
        """Return count of calls currently being processed."""
        with self._lock:
            return len(self._processing)


_tracker = ProcessingTracker()


def get_processing_tracker() -> ProcessingTracker:
    """Get the global processing tracker singleton."""
    return _tracker
