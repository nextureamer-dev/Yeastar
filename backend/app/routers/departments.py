from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy import func
from typing import List

from app.database import get_db
from app.models.department import Department
from app.models.user import User
from app.models.extension import Extension
from app.schemas.department import (
    DepartmentCreate,
    DepartmentUpdate,
    DepartmentResponse,
    DepartmentWithStats,
    DepartmentList,
)
from app.services.auth import get_superadmin_user, get_admin_user

router = APIRouter(prefix="/departments", tags=["departments"])


@router.get("", response_model=List[DepartmentWithStats])
def list_departments(
    include_inactive: bool = False,
    db: Session = Depends(get_db),
    admin: User = Depends(get_admin_user),
):
    """List all departments with user and extension counts. Admin access required."""
    query = db.query(Department)
    if not include_inactive:
        query = query.filter(Department.is_active == True)

    departments = query.order_by(Department.name).all()

    result = []
    for dept in departments:
        # Count users and extensions in this department
        user_count = db.query(func.count(User.id)).filter(
            User.department_id == dept.id
        ).scalar()
        extension_count = db.query(func.count(Extension.id)).filter(
            Extension.department_id == dept.id
        ).scalar()

        dept_data = DepartmentWithStats(
            id=dept.id,
            name=dept.name,
            description=dept.description,
            is_active=dept.is_active,
            created_at=dept.created_at,
            updated_at=dept.updated_at,
            user_count=user_count or 0,
            extension_count=extension_count or 0,
        )
        result.append(dept_data)

    return result


@router.get("/{department_id}", response_model=DepartmentWithStats)
def get_department(
    department_id: int,
    db: Session = Depends(get_db),
    admin: User = Depends(get_admin_user),
):
    """Get department details. Admin access required."""
    dept = db.query(Department).filter(Department.id == department_id).first()
    if not dept:
        raise HTTPException(status_code=404, detail="Department not found")

    user_count = db.query(func.count(User.id)).filter(
        User.department_id == dept.id
    ).scalar()
    extension_count = db.query(func.count(Extension.id)).filter(
        Extension.department_id == dept.id
    ).scalar()

    return DepartmentWithStats(
        id=dept.id,
        name=dept.name,
        description=dept.description,
        is_active=dept.is_active,
        created_at=dept.created_at,
        updated_at=dept.updated_at,
        user_count=user_count or 0,
        extension_count=extension_count or 0,
    )


@router.post("", response_model=DepartmentResponse, status_code=201)
def create_department(
    department_data: DepartmentCreate,
    db: Session = Depends(get_db),
    superadmin: User = Depends(get_superadmin_user),
):
    """Create a new department. Superadmin access required."""
    # Check if department name already exists
    existing = db.query(Department).filter(
        Department.name == department_data.name
    ).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Department with this name already exists",
        )

    department = Department(
        name=department_data.name,
        description=department_data.description,
    )
    db.add(department)
    db.commit()
    db.refresh(department)

    return DepartmentResponse.model_validate(department)


@router.put("/{department_id}", response_model=DepartmentResponse)
def update_department(
    department_id: int,
    department_data: DepartmentUpdate,
    db: Session = Depends(get_db),
    superadmin: User = Depends(get_superadmin_user),
):
    """Update a department. Superadmin access required."""
    department = db.query(Department).filter(Department.id == department_id).first()
    if not department:
        raise HTTPException(status_code=404, detail="Department not found")

    # Check name uniqueness if changing name
    if department_data.name and department_data.name != department.name:
        existing = db.query(Department).filter(
            Department.name == department_data.name
        ).first()
        if existing:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Department with this name already exists",
            )
        department.name = department_data.name

    if department_data.description is not None:
        department.description = department_data.description

    if department_data.is_active is not None:
        department.is_active = department_data.is_active

    db.commit()
    db.refresh(department)

    return DepartmentResponse.model_validate(department)


@router.delete("/{department_id}", status_code=204)
def delete_department(
    department_id: int,
    force: bool = False,
    db: Session = Depends(get_db),
    superadmin: User = Depends(get_superadmin_user),
):
    """
    Delete a department. Superadmin access required.

    If force=False (default), will fail if department has users or extensions.
    If force=True, will unassign all users and extensions before deleting.
    """
    department = db.query(Department).filter(Department.id == department_id).first()
    if not department:
        raise HTTPException(status_code=404, detail="Department not found")

    # Check for associated users and extensions
    user_count = db.query(func.count(User.id)).filter(
        User.department_id == department_id
    ).scalar()
    extension_count = db.query(func.count(Extension.id)).filter(
        Extension.department_id == department_id
    ).scalar()

    if (user_count > 0 or extension_count > 0) and not force:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Department has {user_count} users and {extension_count} extensions. Use force=true to delete anyway.",
        )

    if force:
        # Unassign users and extensions
        db.query(User).filter(User.department_id == department_id).update(
            {"department_id": None}
        )
        db.query(Extension).filter(Extension.department_id == department_id).update(
            {"department_id": None}
        )

    db.delete(department)
    db.commit()


@router.get("/{department_id}/users", response_model=List[dict])
def list_department_users(
    department_id: int,
    db: Session = Depends(get_db),
    admin: User = Depends(get_admin_user),
):
    """List all users in a department. Admin access required."""
    department = db.query(Department).filter(Department.id == department_id).first()
    if not department:
        raise HTTPException(status_code=404, detail="Department not found")

    users = db.query(User).filter(User.department_id == department_id).all()

    return [
        {
            "id": u.id,
            "username": u.username,
            "full_name": u.full_name,
            "email": u.email,
            "extension": u.extension,
            "role": u.role,
            "is_active": u.is_active,
        }
        for u in users
    ]


@router.get("/{department_id}/extensions", response_model=List[dict])
def list_department_extensions(
    department_id: int,
    db: Session = Depends(get_db),
    admin: User = Depends(get_admin_user),
):
    """List all extensions in a department. Admin access required."""
    department = db.query(Department).filter(Department.id == department_id).first()
    if not department:
        raise HTTPException(status_code=404, detail="Department not found")

    extensions = db.query(Extension).filter(Extension.department_id == department_id).all()

    return [
        {
            "id": ext.id,
            "extension_number": ext.extension_number,
            "name": ext.name,
            "email": ext.email,
            "status": ext.status.value if ext.status else None,
            "is_registered": ext.is_registered,
            "user_id": ext.user_id,
        }
        for ext in extensions
    ]
