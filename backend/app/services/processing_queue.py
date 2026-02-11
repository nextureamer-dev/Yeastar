"""
Async processing queue for call transcription/analysis.

Processes items sequentially (one at a time) to avoid GPU contention.
Failed items automatically retry up to 3 times with exponential backoff.
Queue status is trackable and broadcast via WebSocket.
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Dict, Any, List, Callable, Awaitable

logger = logging.getLogger(__name__)


class QueueItemStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    RETRYING = "retrying"


@dataclass
class QueueItem:
    call_id: str
    recording_file: Optional[str] = None
    force: bool = False
    status: QueueItemStatus = QueueItemStatus.PENDING
    attempt: int = 0
    max_retries: int = 3
    error_message: Optional[str] = None
    added_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    next_retry_at: Optional[float] = None
    stage: Optional[str] = None  # downloading, transcribing, analyzing, saving

    def to_dict(self) -> Dict[str, Any]:
        return {
            "call_id": self.call_id,
            "recording_file": self.recording_file,
            "status": self.status.value,
            "attempt": self.attempt,
            "max_retries": self.max_retries,
            "error_message": self.error_message,
            "added_at": self.added_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "stage": self.stage,
        }


class ProcessingQueue:
    """Async queue that processes items one at a time with retry logic."""

    def __init__(self):
        self._queue: asyncio.Queue[QueueItem] = asyncio.Queue()
        self._items: Dict[str, QueueItem] = {}  # call_id -> QueueItem (for lookup)
        self._current: Optional[QueueItem] = None
        self._completed: List[QueueItem] = []  # Recent completed (keep last 50)
        self._failed: List[QueueItem] = []  # Permanently failed (keep last 50)
        self._worker_task: Optional[asyncio.Task] = None
        self._running = False
        self._process_fn: Optional[Callable] = None
        self._broadcast_fn: Optional[Callable] = None
        self._lock = asyncio.Lock()

    def set_process_function(self, fn: Callable[..., Awaitable]):
        """Set the async function that processes each queue item.
        Signature: async fn(call_id, recording_file, force) -> None
        Raises on failure.
        """
        self._process_fn = fn

    def set_broadcast_function(self, fn: Callable[..., Awaitable]):
        """Set the async function that broadcasts queue status via WebSocket.
        Signature: async fn(data: dict) -> None
        """
        self._broadcast_fn = fn

    async def start(self):
        """Start the queue worker."""
        if self._running:
            return
        self._running = True
        self._worker_task = asyncio.create_task(self._worker())
        logger.info("Processing queue worker started")

    async def stop(self):
        """Stop the queue worker gracefully."""
        self._running = False
        if self._worker_task:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
        logger.info("Processing queue worker stopped")

    async def add(self, call_id: str, recording_file: Optional[str] = None,
                  force: bool = False) -> Dict[str, Any]:
        """Add an item to the queue. Returns status dict."""
        async with self._lock:
            # Check if already in queue or processing
            if call_id in self._items:
                existing = self._items[call_id]
                if existing.status in (QueueItemStatus.PENDING, QueueItemStatus.PROCESSING,
                                       QueueItemStatus.RETRYING):
                    return {
                        "status": "already_queued",
                        "position": self._get_position(call_id),
                        "item": existing.to_dict(),
                    }

            item = QueueItem(
                call_id=call_id,
                recording_file=recording_file,
                force=force,
            )
            self._items[call_id] = item
            await self._queue.put(item)

        await self._broadcast_status()
        return {
            "status": "queued",
            "position": self._queue.qsize(),
            "item": item.to_dict(),
        }

    async def add_batch(self, items: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Add multiple items to the queue at once."""
        added = []
        skipped = []
        for item_data in items:
            result = await self.add(
                call_id=item_data["call_id"],
                recording_file=item_data.get("recording_file"),
                force=item_data.get("force", False),
            )
            if result["status"] == "queued":
                added.append(item_data["call_id"])
            else:
                skipped.append(item_data["call_id"])
        return {
            "status": "batch_queued",
            "added_count": len(added),
            "skipped_count": len(skipped),
            "added": added,
            "skipped": skipped,
        }

    async def clear(self) -> Dict[str, Any]:
        """Clear all pending items from the queue. Does not stop the currently processing item."""
        async with self._lock:
            removed = []
            # Drain the asyncio.Queue
            while not self._queue.empty():
                try:
                    self._queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
            # Remove pending/retrying items from tracking
            to_remove = [
                cid for cid, item in self._items.items()
                if item.status in (QueueItemStatus.PENDING, QueueItemStatus.RETRYING)
            ]
            for cid in to_remove:
                removed.append(cid)
                del self._items[cid]

        await self._broadcast_status()
        return {"cleared": len(removed), "call_ids": removed}

    async def update_stage(self, call_id: str, stage: str):
        """Update the processing stage for the currently processing item and broadcast."""
        if self._current and self._current.call_id == call_id:
            self._current.stage = stage
            await self._broadcast_status()

    def get_status(self) -> Dict[str, Any]:
        """Get full queue status."""
        pending_items = []
        for call_id, item in self._items.items():
            if item.status in (QueueItemStatus.PENDING, QueueItemStatus.RETRYING):
                pending_items.append(item.to_dict())

        return {
            "pending": len(pending_items),
            "pending_items": pending_items,
            "processing": self._current.to_dict() if self._current else None,
            "completed_count": len(self._completed),
            "failed_count": len(self._failed),
            "recent_completed": [i.to_dict() for i in self._completed[-10:]],
            "recent_failed": [i.to_dict() for i in self._failed[-10:]],
            "is_running": self._running,
        }

    def _get_position(self, call_id: str) -> int:
        """Get approximate queue position for a call_id."""
        pos = 0
        for cid, item in self._items.items():
            if item.status in (QueueItemStatus.PENDING, QueueItemStatus.RETRYING):
                pos += 1
                if cid == call_id:
                    return pos
        return 0

    async def _worker(self):
        """Main worker loop: process items one at a time."""
        logger.info("Queue worker loop running")
        while self._running:
            try:
                # Wait for next item with timeout so we can check _running flag
                try:
                    item = await asyncio.wait_for(self._queue.get(), timeout=1.0)
                except asyncio.TimeoutError:
                    continue

                # Skip if item was removed
                if item.call_id not in self._items:
                    continue

                # If this is a retry, wait until the retry time
                if item.next_retry_at:
                    wait_time = item.next_retry_at - time.time()
                    if wait_time > 0:
                        logger.info(f"Waiting {wait_time:.1f}s before retry for {item.call_id}")
                        await asyncio.sleep(wait_time)

                # Process item
                item.status = QueueItemStatus.PROCESSING
                item.started_at = time.time()
                item.attempt += 1
                self._current = item
                await self._broadcast_status()

                try:
                    if self._process_fn is None:
                        raise RuntimeError("No process function set")
                    await self._process_fn(
                        call_id=item.call_id,
                        recording_file=item.recording_file,
                        force=item.force,
                    )
                    # Success
                    item.status = QueueItemStatus.COMPLETED
                    item.completed_at = time.time()
                    self._completed.append(item)
                    if len(self._completed) > 50:
                        self._completed = self._completed[-50:]
                    # Remove from active tracking
                    self._items.pop(item.call_id, None)

                except Exception as e:
                    logger.error(f"Queue processing error for {item.call_id} "
                                 f"(attempt {item.attempt}): {e}")
                    item.error_message = str(e)

                    if item.attempt < item.max_retries:
                        # Schedule retry with exponential backoff (10s, 20s, 40s)
                        backoff = 2 ** item.attempt * 5
                        item.status = QueueItemStatus.RETRYING
                        item.next_retry_at = time.time() + backoff
                        logger.info(f"Retrying {item.call_id} in {backoff}s "
                                    f"(attempt {item.attempt + 1}/{item.max_retries})")
                        await self._queue.put(item)
                    else:
                        # Permanently failed
                        item.status = QueueItemStatus.FAILED
                        item.completed_at = time.time()
                        self._failed.append(item)
                        if len(self._failed) > 50:
                            self._failed = self._failed[-50:]
                        self._items.pop(item.call_id, None)

                finally:
                    self._current = None
                    await self._broadcast_status()

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Queue worker error: {e}", exc_info=True)
                await asyncio.sleep(1)

    async def _broadcast_status(self):
        """Broadcast current queue status via WebSocket."""
        if self._broadcast_fn:
            try:
                await self._broadcast_fn({
                    "type": "queue_status",
                    **self.get_status(),
                })
            except Exception as e:
                logger.warning(f"Failed to broadcast queue status: {e}")


# Singleton
_queue: Optional[ProcessingQueue] = None


def get_processing_queue() -> ProcessingQueue:
    """Get the global processing queue singleton."""
    global _queue
    if _queue is None:
        _queue = ProcessingQueue()
    return _queue
