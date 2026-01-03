from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List

from app.database import get_db
from app.models.extension import Extension, ExtensionStatus
from app.schemas.extension import ExtensionResponse, ExtensionList
from app.services.yeastar_client import get_yeastar_client

router = APIRouter(prefix="/extensions", tags=["extensions"])


@router.get("", response_model=ExtensionList)
def list_extensions(db: Session = Depends(get_db)):
    """List all extensions from database."""
    extensions = db.query(Extension).order_by(Extension.extension_number).all()
    return ExtensionList(
        extensions=[ExtensionResponse.model_validate(ext) for ext in extensions],
        total=len(extensions),
    )


@router.get("/sync")
async def sync_extensions(db: Session = Depends(get_db)):
    """Sync extensions from Yeastar PBX to local database."""
    client = get_yeastar_client()
    ext_list = await client.get_extension_list()

    if ext_list is None:
        raise HTTPException(status_code=503, detail="Failed to connect to PBX")

    synced = 0
    for ext_data in ext_list:
        # Cloud PBX uses 'number', on-premise uses 'extid' or 'extnumber'
        ext_number = ext_data.get("number") or ext_data.get("extid") or ext_data.get("extnumber")
        if not ext_number:
            continue

        existing = db.query(Extension).filter(Extension.extension_number == ext_number).first()

        # Cloud PBX: check online_status.sip_phone.status or presence_status
        # On-premise: check status == "Registered"
        online_status = ext_data.get("online_status", {})
        sip_status = online_status.get("sip_phone", {}).get("status", 0)
        is_registered = sip_status == 1 or ext_data.get("status") == "Registered"

        presence = ext_data.get("presence_status", "offline")
        if presence == "available":
            status = ExtensionStatus.AVAILABLE
        elif presence in ("away", "busy", "do_not_disturb"):
            status = ExtensionStatus.BUSY
        elif is_registered:
            status = ExtensionStatus.AVAILABLE
        else:
            status = ExtensionStatus.OFFLINE

        # Cloud PBX uses 'caller_id_name', on-premise uses 'username'
        name = ext_data.get("caller_id_name") or ext_data.get("username")

        if existing:
            existing.name = name or existing.name
            existing.is_registered = is_registered
            existing.status = status
        else:
            new_ext = Extension(
                extension_number=ext_number,
                name=name,
                is_registered=is_registered,
                status=status,
            )
            db.add(new_ext)

        synced += 1

    db.commit()
    return {"status": "success", "synced": synced}


@router.get("/{extension_number}", response_model=ExtensionResponse)
async def get_extension(extension_number: str, db: Session = Depends(get_db)):
    """Get extension details."""
    # First check local DB
    extension = db.query(Extension).filter(Extension.extension_number == extension_number).first()

    # Also get live status from PBX
    client = get_yeastar_client()
    pbx_data = await client.get_extension(extension_number)

    if extension:
        if pbx_data:
            extension.is_registered = pbx_data.get("status") == "Registered"
        return ExtensionResponse.model_validate(extension)
    elif pbx_data:
        # Create new extension record
        extension = Extension(
            extension_number=extension_number,
            name=pbx_data.get("username"),
            is_registered=pbx_data.get("status") == "Registered",
            status=ExtensionStatus.AVAILABLE if pbx_data.get("status") == "Registered" else ExtensionStatus.OFFLINE,
        )
        db.add(extension)
        db.commit()
        db.refresh(extension)
        return ExtensionResponse.model_validate(extension)
    else:
        raise HTTPException(status_code=404, detail="Extension not found")


@router.get("/{extension_number}/voicemails")
async def get_extension_voicemails(extension_number: str):
    """Get voicemails for an extension."""
    client = get_yeastar_client()
    voicemails = await client.get_voicemails(extension_number)

    if voicemails is None:
        return []

    return voicemails
