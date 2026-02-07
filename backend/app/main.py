import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.database import engine, Base
from app.routers import contacts, calls, extensions, pbx, webhook, notes, auth, transcription, departments
from app.models.user import User  # Import to ensure table is created
from app.models.department import Department  # Import to ensure table is created
from app.services.yeastar_client import get_yeastar_client
from app.services.websocket_manager import get_websocket_manager
from app.services.webhook_handler import subscribe

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

settings = get_settings()


# Background task for CDR polling
_cdr_poller_task = None
_shutdown_event = None


async def cdr_polling_task():
    """Background task that polls for new CDRs and auto-processes them."""
    import asyncio
    import sys
    import os
    import hashlib

    # Ensure the backend directory is in path
    backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if backend_dir not in sys.path:
        sys.path.insert(0, backend_dir)

    from app.database import SessionLocal
    from app.models.call_summary import CallSummary

    logger.info("=== CDR poller function entered ===")

    # Wait for main app to fully start and login to Yeastar
    await asyncio.sleep(10)
    logger.info("=== CDR auto-processing poller running ===")

    while not _shutdown_event.is_set():
        try:
            logger.info("CDR poller: checking for new calls...")

            import httpx
            from app.config import get_settings
            settings = get_settings()

            cdrs = []

            if settings.is_cloud_pbx:
                # Cloud PBX: Use OpenAPI
                main_client = get_yeastar_client()
                if not main_client.token:
                    logger.warning("CDR poller: main client not logged in, skipping")
                    await asyncio.sleep(30)
                    continue

                token = main_client.token
                base_url = f"https://{settings.yeastar_host}/openapi/v1.0"

                async with httpx.AsyncClient() as http:
                    resp = await http.get(
                        f"{base_url}/cdr/list",
                        params={
                            "access_token": token,
                            "page": 1,
                            "page_size": 50,
                            "sort_by": "time",
                            "order_by": "desc"
                        }
                    )
                    data = resp.json()
                    cdrs = data.get("data", []) if data.get("errcode") == 0 else []
            else:
                # On-Premise PBX: Use API v1.1.0
                main_client = get_yeastar_client()
                if not main_client.token:
                    # Try to login for on-premise
                    logger.info("CDR poller: attempting login for on-premise PBX...")
                    if not await main_client.login():
                        logger.warning("CDR poller: failed to login to on-premise PBX")
                        await asyncio.sleep(30)
                        continue

                token = main_client.token
                base_url = settings.yeastar_base_url

                async with httpx.AsyncClient(verify=False) as http:
                    # On-premise uses /api/v1.1.0/cdr/get_random
                    resp = await http.post(
                        f"{base_url}/api/v1.1.0/cdr/get_random",
                        params={"token": token},
                        json={},
                        timeout=30.0
                    )
                    data = resp.json()
                    logger.info(f"CDR poller on-premise response: {data.get('status', 'unknown')}")
                    if data.get("status") == "Success":
                        cdrs = data.get("cdr", [])
                    else:
                        logger.warning(f"CDR fetch failed: {data.get('errmsg', 'unknown error')}")
                        # Token might be expired, clear it
                        if "token" in data.get("errmsg", "").lower():
                            main_client.token = None

            logger.info(f"CDR poller: fetched {len(cdrs) if cdrs else 0} recent CDRs")

            new_calls_found = 0
            if cdrs:
                db = SessionLocal()
                try:
                    for cdr in cdrs:
                        # Cloud PBX uses "uid", on-premise uses "callid"
                        call_id = cdr.get("uid") or cdr.get("callid")
                        if not call_id:
                            continue

                        # Skip if already processed
                        existing = db.query(CallSummary).filter(
                            CallSummary.call_id == call_id
                        ).first()
                        if existing:
                            continue

                        # Check call type - skip internal
                        # Cloud uses "call_type", on-premise uses "calltype" or "type"
                        call_type = (
                            cdr.get("call_type") or
                            cdr.get("calltype") or
                            cdr.get("type", "")
                        ).lower()
                        if call_type not in ("inbound", "outbound"):
                            continue

                        # Only process answered calls with recordings
                        # Cloud uses "disposition", on-premise uses "status"
                        disposition = (
                            cdr.get("disposition") or
                            cdr.get("status", "")
                        ).upper()
                        if disposition != "ANSWERED":
                            continue

                        # Get recording filename
                        # Both use "recording" but on-premise might also use "recordingfile"
                        recording = cdr.get("recording") or cdr.get("recordingfile")
                        if not recording:
                            continue

                        # Skip if already being processed by another source
                        from app.services.processing_tracker import get_processing_tracker
                        if get_processing_tracker().is_processing(call_id):
                            logger.info(f"CDR poller: skipping {call_id}, already being processed")
                            continue

                        # Process this call
                        new_calls_found += 1
                        logger.info(f"Auto-processing new call: {call_id} ({call_type})")
                        try:
                            from app.routers.transcription import _process_recording_task
                            await _process_recording_task(
                                call_id=call_id,
                                recording_file=recording,
                                force=False,
                            )
                            logger.info(f"Auto-processed call {call_id} successfully")
                        except Exception as e:
                            logger.error(f"Failed to auto-process {call_id}: {e}")
                finally:
                    db.close()

            if new_calls_found == 0:
                logger.info("CDR poller: no new calls to process")

            # Wait before next poll
            await asyncio.sleep(30)

        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"CDR poller error: {e}", exc_info=True)
            await asyncio.sleep(60)

    logger.info("=== CDR auto-processing poller stopped ===")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager."""
    global _cdr_poller_task, _shutdown_event
    import asyncio

    # Startup
    logger.info("Starting Yeastar CRM API...")

    # Create database tables
    Base.metadata.create_all(bind=engine)
    logger.info("Database tables created")

    # Initialize Yeastar client
    client = get_yeastar_client()
    if await client.login():
        logger.info("Connected to Yeastar PBX")

        # Sync recent CDRs to local database for fast stats
        from app.services.cdr_sync import get_cdr_sync_service
        cdr_service = get_cdr_sync_service()
        try:
            # Sync last 7 days of CDRs (up to 2000 records) for stats
            synced = await cdr_service._sync_cloud_cdrs(max_pages=20)
            logger.info(f"Initial CDR sync complete: {synced} records synced")
        except Exception as e:
            logger.warning(f"Initial CDR sync failed: {e}")
    else:
        logger.warning("Failed to connect to Yeastar PBX - check configuration")

    # Setup webhook event forwarding to WebSocket
    ws_manager = get_websocket_manager()

    async def forward_to_websocket(data):
        import asyncio
        asyncio.create_task(ws_manager.broadcast(data))

    subscribe("call_popup", lambda data: forward_to_websocket(data))
    subscribe("NewCdr", lambda data: forward_to_websocket({"type": "new_cdr", **data}))
    subscribe("Hangup", lambda data: forward_to_websocket({"type": "call_ended", **data}))
    subscribe("AnswerCall", lambda data: forward_to_websocket({"type": "call_answered", **data}))

    # Start CDR polling background task for auto-processing
    if settings.auto_process_calls:
        import threading
        _shutdown_event = asyncio.Event()

        def run_poller_in_thread():
            """Run the CDR poller in a new event loop in a separate thread."""
            import asyncio as aio
            import sys
            import os

            # Ensure backend is in path (use the actual backend directory)
            backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            if backend_dir not in sys.path:
                sys.path.insert(0, backend_dir)
            os.chdir(backend_dir)

            loop = aio.new_event_loop()
            aio.set_event_loop(loop)
            try:
                loop.run_until_complete(cdr_polling_task())
            except Exception as e:
                logger.error(f"Poller thread error: {e}", exc_info=True)
            finally:
                loop.close()

        poller_thread = threading.Thread(target=run_poller_in_thread, daemon=True)
        poller_thread.start()
        logger.info("CDR auto-processing thread started")

    yield

    # Shutdown
    logger.info("Shutting down Yeastar CRM API...")

    # Stop CDR poller
    if _cdr_poller_task and _shutdown_event:
        logger.info("Stopping CDR poller...")
        _shutdown_event.set()
        _cdr_poller_task.cancel()
        try:
            await _cdr_poller_task
        except asyncio.CancelledError:
            pass

    await client.logout()
    await client.close()


app = FastAPI(
    title="Yeastar CRM",
    description="CRM integration with Yeastar PBX",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS middleware for React frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(contacts.router, prefix="/api")
app.include_router(calls.router, prefix="/api")
app.include_router(extensions.router, prefix="/api")
app.include_router(pbx.router, prefix="/api")
app.include_router(webhook.router, prefix="/api")
app.include_router(notes.router, prefix="/api")
app.include_router(auth.router, prefix="/api")
app.include_router(transcription.router, prefix="/api")
app.include_router(departments.router, prefix="/api")


@app.get("/")
async def root():
    """Root endpoint."""
    return {
        "name": "Yeastar CRM API",
        "version": "1.0.0",
        "status": "running",
    }


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    client = get_yeastar_client()
    pbx_connected = client.token is not None

    return {
        "status": "healthy",
        "pbx_connected": pbx_connected,
    }


@app.websocket("/ws")
async def websocket_endpoint(
    websocket: WebSocket,
    extension: str = Query(None, description="Extension number for targeted events"),
):
    """
    WebSocket endpoint for real-time updates.

    Connect with optional extension parameter to receive extension-specific events.
    Example: ws://localhost:8000/ws?extension=1001
    """
    ws_manager = get_websocket_manager()
    await ws_manager.connect(websocket, extension)

    try:
        while True:
            # Keep connection alive and handle incoming messages
            data = await websocket.receive_text()

            # Handle client messages (e.g., ping/pong, commands)
            if data == "ping":
                await websocket.send_text("pong")

    except WebSocketDisconnect:
        ws_manager.disconnect(websocket, extension)
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
        ws_manager.disconnect(websocket, extension)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=settings.api_port,
        reload=True,
    )
