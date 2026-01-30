from pydantic import BaseModel
from typing import Optional, Literal
from datetime import datetime


class UserBase(BaseModel):
    username: str
    email: Optional[str] = None
    full_name: Optional[str] = None
    extension: Optional[str] = None


class UserCreate(UserBase):
    password: str
    role: Optional[Literal["employee", "admin", "superadmin"]] = "employee"


class UserUpdate(BaseModel):
    email: Optional[str] = None
    full_name: Optional[str] = None
    extension: Optional[str] = None
    password: Optional[str] = None
    is_active: Optional[bool] = None
    role: Optional[Literal["employee", "admin", "superadmin"]] = None


class UserResponse(UserBase):
    id: int
    is_active: bool
    is_admin: bool
    is_superadmin: bool
    role: str
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
