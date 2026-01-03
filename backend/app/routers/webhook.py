from fastapi import APIRouter, Request, Query, Header
from typing import Optional
import logging

from app.services.webhook_handler import handle_call_event
from app.config import get_settings

router = APIRouter(prefix="/webhook", tags=["webhook"])
logger = logging.getLogger(__name__)
settings = get_settings()


def verify_webhook_token(token: Optional[str]) -> bool:
    """Verify the webhook token if configured."""
    if not settings.yeastar_webhook_token:
        return True  # No token configured, accept all
    return token == settings.yeastar_webhook_token


@router.post("")
async def receive_webhook(
    request: Request,
    token: Optional[str] = Query(None),
    x_webhook_token: Optional[str] = Header(None, alias="X-Webhook-Token"),
):
    """
    Receive webhook events from Yeastar PBX.

    The PBX sends events to this endpoint when calls occur.
    Token can be passed as query param or X-Webhook-Token header.
    """
    # Check token from query param or header
    provided_token = token or x_webhook_token
    if not verify_webhook_token(provided_token):
        logger.warning(f"Invalid webhook token received")
        return {"status": "error", "message": "Invalid token"}

    try:
        data = await request.json()
        logger.info(f"Received webhook: {data}")
        handle_call_event(data)
        return {"status": "received"}
    except Exception as e:
        logger.error(f"Error processing webhook: {e}")
        return {"status": "error", "message": str(e)}


@router.post("/cdr")
async def receive_cdr(
    request: Request,
    token: Optional[str] = Query(None),
    x_webhook_token: Optional[str] = Header(None, alias="X-Webhook-Token"),
):
    """Receive CDR events specifically."""
    provided_token = token or x_webhook_token
    if not verify_webhook_token(provided_token):
        logger.warning(f"Invalid webhook token received on /cdr")
        return {"status": "error", "message": "Invalid token"}

    try:
        data = await request.json()
        data["event"] = "NewCdr"  # Ensure event type is set
        logger.info(f"Received CDR: {data}")
        handle_call_event(data)
        return {"status": "received"}
    except Exception as e:
        logger.error(f"Error processing CDR: {e}")
        return {"status": "error", "message": str(e)}
