from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime


class NoteBase(BaseModel):
    content: str
    created_by: Optional[str] = None


class NoteCreate(NoteBase):
    contact_id: int
    call_log_id: Optional[int] = None


class NoteResponse(NoteBase):
    id: int
    contact_id: int
    call_log_id: Optional[int] = None
    created_at: datetime
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True
