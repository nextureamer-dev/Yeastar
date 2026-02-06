from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime
from app.models.extension import ExtensionStatus


class UserInfo(BaseModel):
    """Minimal user info for embedding in extension response."""
    id: int
    username: str
    full_name: Optional[str] = None

    class Config:
        from_attributes = True


class DepartmentInfo(BaseModel):
    """Minimal department info for embedding in extension response."""
    id: int
    name: str

    class Config:
        from_attributes = True


class ExtensionBase(BaseModel):
    extension_number: str
    name: Optional[str] = None
    email: Optional[str] = None


class ExtensionCreate(ExtensionBase):
    """Schema for creating a new extension."""
    user_id: Optional[int] = None
    department_id: Optional[int] = None


class ExtensionUpdate(BaseModel):
    """Schema for updating an extension."""
    name: Optional[str] = None
    email: Optional[str] = None
    user_id: Optional[int] = None
    department_id: Optional[int] = None


class ExtensionResponse(ExtensionBase):
    id: int
    status: ExtensionStatus
    is_registered: bool
    user_id: Optional[int] = None
    department_id: Optional[int] = None
    user: Optional[UserInfo] = None
    department: Optional[DepartmentInfo] = None
    current_call_id: Optional[str] = None
    current_caller: Optional[str] = None
    last_seen: Optional[datetime] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class ExtensionList(BaseModel):
    extensions: List[ExtensionResponse]
    total: int
