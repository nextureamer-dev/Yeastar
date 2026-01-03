from fastapi import APIRouter, HTTPException
from typing import Optional

from app.services.yeastar_client import get_yeastar_client

router = APIRouter(prefix="/pbx", tags=["pbx"])


@router.get("/info")
async def get_pbx_info():
    """Get PBX device information."""
    client = get_yeastar_client()
    result = await client.get_device_info()

    if result:
        # Handle Cloud PBX response (errcode: 0)
        if result.get("errcode") == 0 or result.get("status") == "Success":
            # Cloud PBX may have different field names
            return {
                "status": "connected",
                "device_name": result.get("devicename") or result.get("device_name") or "Yeastar Cloud PBX",
                "serial_number": result.get("sn") or result.get("serial_number") or "N/A",
                "firmware_version": result.get("firmwarever") or result.get("firmware_version") or "Cloud",
                "system_time": result.get("systemtime") or result.get("system_time") or "N/A",
                "uptime": result.get("uptime") or "N/A",
                "extensions": result.get("extensionstatus") or result.get("extensions") or "N/A",
            }

    raise HTTPException(
        status_code=503,
        detail="Failed to connect to PBX"
    )


@router.get("/status")
async def get_pbx_status():
    """Check PBX connection status."""
    client = get_yeastar_client()

    try:
        if await client.ensure_authenticated():
            return {"status": "connected", "message": "PBX connection active"}
        else:
            return {"status": "disconnected", "message": "Failed to authenticate"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@router.post("/login")
async def pbx_login():
    """Force a new login to the PBX."""
    client = get_yeastar_client()
    client.token = None  # Clear existing token

    if await client.login():
        return {"status": "success", "message": "Logged in successfully"}
    else:
        raise HTTPException(status_code=401, detail="Login failed")


@router.post("/logout")
async def pbx_logout():
    """Logout from the PBX."""
    client = get_yeastar_client()

    if await client.logout():
        return {"status": "success", "message": "Logged out successfully"}
    else:
        raise HTTPException(status_code=500, detail="Logout failed")


@router.get("/queues")
async def get_queue_status():
    """Get all queue statuses."""
    client = get_yeastar_client()
    queues = await client.get_queue_status()

    if queues is None:
        return []

    return queues
