from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session, joinedload
from typing import List, Optional

from app.database import get_db
from app.models.extension import Extension, ExtensionStatus
from app.models.user import User
from app.models.department import Department
from app.schemas.extension import (
    ExtensionResponse,
    ExtensionList,
    ExtensionCreate,
    ExtensionUpdate,
)
from app.services.yeastar_client import get_yeastar_client
from app.services.auth import get_admin_user, get_current_user_required

router = APIRouter(prefix="/extensions", tags=["extensions"])


@router.get("", response_model=ExtensionList)
def list_extensions(
    department_id: Optional[int] = None,
    user_id: Optional[int] = None,
    db: Session = Depends(get_db),
):
    """List all extensions from database with optional filters."""
    query = db.query(Extension).options(
        joinedload(Extension.user),
        joinedload(Extension.department),
    )

    if department_id:
        query = query.filter(Extension.department_id == department_id)
    if user_id:
        query = query.filter(Extension.user_id == user_id)

    extensions = query.order_by(Extension.extension_number).all()
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


@router.post("", response_model=ExtensionResponse, status_code=201)
def create_extension(
    extension_data: ExtensionCreate,
    db: Session = Depends(get_db),
    admin: User = Depends(get_admin_user),
):
    """Create a new extension. Admin access required."""
    # Check if extension number already exists
    existing = db.query(Extension).filter(
        Extension.extension_number == extension_data.extension_number
    ).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Extension number already exists",
        )

    # Validate user_id if provided
    if extension_data.user_id:
        user = db.query(User).filter(User.id == extension_data.user_id).first()
        if not user:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="User not found",
            )

    # Validate department_id if provided
    if extension_data.department_id:
        department = db.query(Department).filter(
            Department.id == extension_data.department_id
        ).first()
        if not department:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Department not found",
            )

    extension = Extension(
        extension_number=extension_data.extension_number,
        name=extension_data.name,
        email=extension_data.email,
        user_id=extension_data.user_id,
        department_id=extension_data.department_id,
        status=ExtensionStatus.OFFLINE,
        is_registered=False,
    )
    db.add(extension)
    db.commit()

    # Reload with relationships
    extension = db.query(Extension).options(
        joinedload(Extension.user),
        joinedload(Extension.department),
    ).filter(Extension.id == extension.id).first()

    return ExtensionResponse.model_validate(extension)


@router.put("/{extension_id}", response_model=ExtensionResponse)
def update_extension(
    extension_id: int,
    extension_data: ExtensionUpdate,
    db: Session = Depends(get_db),
    admin: User = Depends(get_admin_user),
):
    """Update an extension. Admin access required."""
    extension = db.query(Extension).filter(Extension.id == extension_id).first()
    if not extension:
        raise HTTPException(status_code=404, detail="Extension not found")

    # Validate user_id if provided
    if extension_data.user_id is not None:
        if extension_data.user_id:
            user = db.query(User).filter(User.id == extension_data.user_id).first()
            if not user:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="User not found",
                )
        extension.user_id = extension_data.user_id

    # Validate department_id if provided
    if extension_data.department_id is not None:
        if extension_data.department_id:
            department = db.query(Department).filter(
                Department.id == extension_data.department_id
            ).first()
            if not department:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Department not found",
                )
        extension.department_id = extension_data.department_id

    if extension_data.name is not None:
        extension.name = extension_data.name

    if extension_data.email is not None:
        extension.email = extension_data.email

    db.commit()

    # Reload with relationships
    extension = db.query(Extension).options(
        joinedload(Extension.user),
        joinedload(Extension.department),
    ).filter(Extension.id == extension_id).first()

    return ExtensionResponse.model_validate(extension)


@router.delete("/{extension_id}", status_code=204)
def delete_extension(
    extension_id: int,
    db: Session = Depends(get_db),
    admin: User = Depends(get_admin_user),
):
    """Delete an extension. Admin access required."""
    extension = db.query(Extension).filter(Extension.id == extension_id).first()
    if not extension:
        raise HTTPException(status_code=404, detail="Extension not found")

    db.delete(extension)
    db.commit()


@router.post("/{extension_id}/assign-user", response_model=ExtensionResponse)
def assign_user_to_extension(
    extension_id: int,
    user_id: int,
    db: Session = Depends(get_db),
    admin: User = Depends(get_admin_user),
):
    """Assign a user to an extension. Admin access required."""
    extension = db.query(Extension).filter(Extension.id == extension_id).first()
    if not extension:
        raise HTTPException(status_code=404, detail="Extension not found")

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    extension.user_id = user_id
    # Also update the extension name to match the user's full name
    if user.full_name:
        extension.name = user.full_name

    db.commit()

    # Reload with relationships
    extension = db.query(Extension).options(
        joinedload(Extension.user),
        joinedload(Extension.department),
    ).filter(Extension.id == extension_id).first()

    return ExtensionResponse.model_validate(extension)


@router.post("/{extension_id}/assign-department", response_model=ExtensionResponse)
def assign_department_to_extension(
    extension_id: int,
    department_id: int,
    db: Session = Depends(get_db),
    admin: User = Depends(get_admin_user),
):
    """Assign a department to an extension. Admin access required."""
    extension = db.query(Extension).filter(Extension.id == extension_id).first()
    if not extension:
        raise HTTPException(status_code=404, detail="Extension not found")

    department = db.query(Department).filter(Department.id == department_id).first()
    if not department:
        raise HTTPException(status_code=404, detail="Department not found")

    extension.department_id = department_id
    db.commit()

    # Reload with relationships
    extension = db.query(Extension).options(
        joinedload(Extension.user),
        joinedload(Extension.department),
    ).filter(Extension.id == extension_id).first()

    return ExtensionResponse.model_validate(extension)
