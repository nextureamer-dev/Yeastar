from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime
from app.models.extension import ExtensionStatus


class ExtensionBase(BaseModel):
    extension_number: str
    name: Optional[str] = None
    email: Optional[str] = None


class ExtensionResponse(ExtensionBase):
    id: int
    status: ExtensionStatus
    is_registered: bool
    current_call_id: Optional[str] = None
    current_caller: Optional[str] = None
    last_seen: Optional[datetime] = None

    class Config:
        from_attributes = True


class ExtensionList(BaseModel):
    extensions: List[ExtensionResponse]
    total: int
