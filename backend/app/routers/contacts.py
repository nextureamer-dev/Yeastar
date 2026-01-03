from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import func, or_
from typing import Optional, List

from app.database import get_db
from app.models.contact import Contact
from app.models.call_log import CallLog
from app.schemas.contact import ContactCreate, ContactUpdate, ContactResponse, ContactList

router = APIRouter(prefix="/contacts", tags=["contacts"])


@router.get("", response_model=ContactList)
def list_contacts(
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    search: Optional[str] = None,
    favorites_only: bool = False,
    db: Session = Depends(get_db),
):
    """List all contacts with pagination and search."""
    query = db.query(Contact)

    if search:
        search_term = f"%{search}%"
        query = query.filter(
            or_(
                Contact.first_name.ilike(search_term),
                Contact.last_name.ilike(search_term),
                Contact.company.ilike(search_term),
                Contact.phone.ilike(search_term),
                Contact.email.ilike(search_term),
            )
        )

    if favorites_only:
        query = query.filter(Contact.is_favorite == True)

    total = query.count()
    contacts = query.order_by(Contact.first_name).offset((page - 1) * per_page).limit(per_page).all()

    # Get call counts for each contact
    contact_responses = []
    for contact in contacts:
        call_count = db.query(CallLog).filter(CallLog.contact_id == contact.id).count()
        response = ContactResponse(
            id=contact.id,
            first_name=contact.first_name,
            last_name=contact.last_name,
            company=contact.company,
            email=contact.email,
            phone=contact.phone,
            phone_secondary=contact.phone_secondary,
            address=contact.address,
            notes=contact.notes,
            tags=contact.tags,
            is_favorite=contact.is_favorite,
            full_name=contact.full_name,
            created_at=contact.created_at,
            updated_at=contact.updated_at,
            call_count=call_count,
        )
        contact_responses.append(response)

    return ContactList(
        contacts=contact_responses,
        total=total,
        page=page,
        per_page=per_page,
    )


@router.get("/lookup", response_model=Optional[ContactResponse])
def lookup_contact_by_phone(
    phone: str = Query(..., description="Phone number to lookup"),
    db: Session = Depends(get_db),
):
    """Lookup a contact by phone number."""
    # Normalize phone number - remove common formatting
    normalized = "".join(c for c in phone if c.isdigit() or c == "+")

    contact = db.query(Contact).filter(
        or_(
            Contact.phone.contains(normalized[-10:]),  # Last 10 digits
            Contact.phone_secondary.contains(normalized[-10:]),
        )
    ).first()

    if not contact:
        return None

    call_count = db.query(CallLog).filter(CallLog.contact_id == contact.id).count()
    return ContactResponse(
        id=contact.id,
        first_name=contact.first_name,
        last_name=contact.last_name,
        company=contact.company,
        email=contact.email,
        phone=contact.phone,
        phone_secondary=contact.phone_secondary,
        address=contact.address,
        notes=contact.notes,
        tags=contact.tags,
        is_favorite=contact.is_favorite,
        full_name=contact.full_name,
        created_at=contact.created_at,
        updated_at=contact.updated_at,
        call_count=call_count,
    )


@router.get("/{contact_id}", response_model=ContactResponse)
def get_contact(contact_id: int, db: Session = Depends(get_db)):
    """Get a specific contact by ID."""
    contact = db.query(Contact).filter(Contact.id == contact_id).first()
    if not contact:
        raise HTTPException(status_code=404, detail="Contact not found")

    call_count = db.query(CallLog).filter(CallLog.contact_id == contact.id).count()
    return ContactResponse(
        id=contact.id,
        first_name=contact.first_name,
        last_name=contact.last_name,
        company=contact.company,
        email=contact.email,
        phone=contact.phone,
        phone_secondary=contact.phone_secondary,
        address=contact.address,
        notes=contact.notes,
        tags=contact.tags,
        is_favorite=contact.is_favorite,
        full_name=contact.full_name,
        created_at=contact.created_at,
        updated_at=contact.updated_at,
        call_count=call_count,
    )


@router.post("", response_model=ContactResponse, status_code=201)
def create_contact(contact_data: ContactCreate, db: Session = Depends(get_db)):
    """Create a new contact."""
    contact = Contact(**contact_data.model_dump())
    db.add(contact)
    db.commit()
    db.refresh(contact)

    return ContactResponse(
        id=contact.id,
        first_name=contact.first_name,
        last_name=contact.last_name,
        company=contact.company,
        email=contact.email,
        phone=contact.phone,
        phone_secondary=contact.phone_secondary,
        address=contact.address,
        notes=contact.notes,
        tags=contact.tags,
        is_favorite=contact.is_favorite,
        full_name=contact.full_name,
        created_at=contact.created_at,
        updated_at=contact.updated_at,
        call_count=0,
    )


@router.put("/{contact_id}", response_model=ContactResponse)
def update_contact(
    contact_id: int,
    contact_data: ContactUpdate,
    db: Session = Depends(get_db),
):
    """Update an existing contact."""
    contact = db.query(Contact).filter(Contact.id == contact_id).first()
    if not contact:
        raise HTTPException(status_code=404, detail="Contact not found")

    update_data = contact_data.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(contact, field, value)

    db.commit()
    db.refresh(contact)

    call_count = db.query(CallLog).filter(CallLog.contact_id == contact.id).count()
    return ContactResponse(
        id=contact.id,
        first_name=contact.first_name,
        last_name=contact.last_name,
        company=contact.company,
        email=contact.email,
        phone=contact.phone,
        phone_secondary=contact.phone_secondary,
        address=contact.address,
        notes=contact.notes,
        tags=contact.tags,
        is_favorite=contact.is_favorite,
        full_name=contact.full_name,
        created_at=contact.created_at,
        updated_at=contact.updated_at,
        call_count=call_count,
    )


@router.delete("/{contact_id}", status_code=204)
def delete_contact(contact_id: int, db: Session = Depends(get_db)):
    """Delete a contact."""
    contact = db.query(Contact).filter(Contact.id == contact_id).first()
    if not contact:
        raise HTTPException(status_code=404, detail="Contact not found")

    db.delete(contact)
    db.commit()
