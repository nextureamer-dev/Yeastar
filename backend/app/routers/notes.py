from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from typing import List

from app.database import get_db
from app.models.note import Note
from app.models.contact import Contact
from app.schemas.note import NoteCreate, NoteResponse

router = APIRouter(prefix="/notes", tags=["notes"])


@router.get("", response_model=List[NoteResponse])
def list_notes(
    contact_id: int = Query(None, description="Filter by contact ID"),
    call_log_id: int = Query(None, description="Filter by call log ID"),
    limit: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """List notes with optional filtering."""
    query = db.query(Note)

    if contact_id:
        query = query.filter(Note.contact_id == contact_id)

    if call_log_id:
        query = query.filter(Note.call_log_id == call_log_id)

    notes = query.order_by(Note.created_at.desc()).limit(limit).all()
    return [NoteResponse.model_validate(note) for note in notes]


@router.get("/{note_id}", response_model=NoteResponse)
def get_note(note_id: int, db: Session = Depends(get_db)):
    """Get a specific note by ID."""
    note = db.query(Note).filter(Note.id == note_id).first()
    if not note:
        raise HTTPException(status_code=404, detail="Note not found")
    return NoteResponse.model_validate(note)


@router.post("", response_model=NoteResponse, status_code=201)
def create_note(note_data: NoteCreate, db: Session = Depends(get_db)):
    """Create a new note."""
    # Verify contact exists
    contact = db.query(Contact).filter(Contact.id == note_data.contact_id).first()
    if not contact:
        raise HTTPException(status_code=404, detail="Contact not found")

    note = Note(**note_data.model_dump())
    db.add(note)
    db.commit()
    db.refresh(note)
    return NoteResponse.model_validate(note)


@router.put("/{note_id}", response_model=NoteResponse)
def update_note(
    note_id: int,
    content: str,
    db: Session = Depends(get_db),
):
    """Update a note's content."""
    note = db.query(Note).filter(Note.id == note_id).first()
    if not note:
        raise HTTPException(status_code=404, detail="Note not found")

    note.content = content
    db.commit()
    db.refresh(note)
    return NoteResponse.model_validate(note)


@router.delete("/{note_id}", status_code=204)
def delete_note(note_id: int, db: Session = Depends(get_db)):
    """Delete a note."""
    note = db.query(Note).filter(Note.id == note_id).first()
    if not note:
        raise HTTPException(status_code=404, detail="Note not found")

    db.delete(note)
    db.commit()
