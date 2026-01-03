from pydantic import BaseModel, EmailStr
from typing import Optional, List
from datetime import datetime


class ContactBase(BaseModel):
    first_name: str
    last_name: Optional[str] = None
    company: Optional[str] = None
    email: Optional[str] = None
    phone: str
    phone_secondary: Optional[str] = None
    address: Optional[str] = None
    notes: Optional[str] = None
    tags: Optional[str] = None
    is_favorite: bool = False


class ContactCreate(ContactBase):
    pass


class ContactUpdate(BaseModel):
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    company: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    phone_secondary: Optional[str] = None
    address: Optional[str] = None
    notes: Optional[str] = None
    tags: Optional[str] = None
    is_favorite: Optional[bool] = None


class ContactResponse(ContactBase):
    id: int
    full_name: str
    created_at: datetime
    updated_at: Optional[datetime] = None
    call_count: int = 0

    class Config:
        from_attributes = True


class ContactList(BaseModel):
    contacts: List[ContactResponse]
    total: int
    page: int
    per_page: int
