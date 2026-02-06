from pydantic import BaseModel
from typing import Optional, Literal
from datetime import datetime


class DepartmentInfo(BaseModel):
    """Minimal department info for embedding in user response."""
    id: int
    name: str

    class Config:
        from_attributes = True


class UserBase(BaseModel):
    username: str
    email: Optional[str] = None
    full_name: Optional[str] = None
    extension: Optional[str] = None


class UserCreate(UserBase):
    password: str
    role: Optional[Literal["employee", "admin", "superadmin"]] = "employee"
    department_id: Optional[int] = None


class UserUpdate(BaseModel):
    email: Optional[str] = None
    full_name: Optional[str] = None
    extension: Optional[str] = None
    password: Optional[str] = None
    is_active: Optional[bool] = None
    role: Optional[Literal["employee", "admin", "superadmin"]] = None
    department_id: Optional[int] = None


class UserAdminUpdate(UserUpdate):
    """Extended update schema for admin operations."""
    username: Optional[str] = None


class UserResponse(UserBase):
    id: int
    is_active: bool
    is_admin: bool
    is_superadmin: bool
    role: str
    department_id: Optional[int] = None
    department: Optional[DepartmentInfo] = None
    created_at: datetime

    class Config:
        from_attributes = True


class Token(BaseModel):
    access_token: str
    token_type: str


class TokenData(BaseModel):
    username: Optional[str] = None


class LoginRequest(BaseModel):
    username: str
    password: str
