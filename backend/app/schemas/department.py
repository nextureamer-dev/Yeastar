from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime


class DepartmentBase(BaseModel):
    name: str
    description: Optional[str] = None


class DepartmentCreate(DepartmentBase):
    pass


class DepartmentUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    is_active: Optional[bool] = None


class DepartmentResponse(DepartmentBase):
    id: int
    is_active: bool
    created_at: datetime
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class DepartmentWithStats(DepartmentResponse):
    """Department with user and extension counts."""
    user_count: int = 0
    extension_count: int = 0


class DepartmentList(BaseModel):
    departments: List[DepartmentResponse]
    total: int
