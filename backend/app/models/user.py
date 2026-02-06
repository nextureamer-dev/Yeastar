from sqlalchemy import Column, Integer, String, Boolean, DateTime, Enum, ForeignKey
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
import enum
from app.database import Base


class UserRole(str, enum.Enum):
    EMPLOYEE = "employee"
    ADMIN = "admin"
    SUPERADMIN = "superadmin"


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(100), unique=True, nullable=False, index=True)
    email = Column(String(255), unique=True, nullable=True)
    hashed_password = Column(String(255), nullable=False)
    full_name = Column(String(200), nullable=True)
    extension = Column(String(20), nullable=True)  # Associated PBX extension
    department_id = Column(Integer, ForeignKey("departments.id"), nullable=True, index=True)
    is_active = Column(Boolean, default=True)
    is_admin = Column(Boolean, default=False)
    is_superadmin = Column(Boolean, default=False)  # Can see all data and detailed dashboard
    role = Column(String(20), default=UserRole.EMPLOYEE.value)  # employee, admin, superadmin
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    # Relationships
    department = relationship("Department", back_populates="users")
