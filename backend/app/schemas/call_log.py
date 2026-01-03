from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime
from app.models.call_log import CallDirection, CallStatus


class CallLogBase(BaseModel):
    call_id: str
    caller_number: str
    callee_number: str
    caller_name: Optional[str] = None
    callee_name: Optional[str] = None
    direction: CallDirection
    status: CallStatus
    extension: Optional[str] = None
    trunk: Optional[str] = None
    start_time: datetime
    answer_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    duration: int = 0
    ring_duration: int = 0
    recording_file: Optional[str] = None
    notes: Optional[str] = None


class CallLogCreate(CallLogBase):
    contact_id: Optional[int] = None


class CallLogResponse(CallLogBase):
    id: int
    contact_id: Optional[int] = None
    contact_name: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True


class CallLogList(BaseModel):
    call_logs: List[CallLogResponse]
    total: int
    page: int
    per_page: int


class ActiveCall(BaseModel):
    call_id: str
    caller: str
    callee: str
    extension: str
    direction: str
    status: str
    duration: int = 0
    contact_name: Optional[str] = None
