from datetime import timedelta
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session, joinedload

from app.database import get_db
from app.models.user import User
from app.models.department import Department
from app.schemas.user import (
    UserCreate,
    UserResponse,
    UserUpdate,
    UserAdminUpdate,
    Token,
    LoginRequest,
    ChangePassword,
    ResetPassword,
)
from app.services.auth import (
    authenticate_user,
    create_access_token,
    get_password_hash,
    verify_password,
    get_current_user_required,
    get_admin_user,
    get_superadmin_user,
    ACCESS_TOKEN_EXPIRE_MINUTES,
)

router = APIRouter(prefix="/auth", tags=["authentication"])


@router.post("/login", response_model=Token)
def login(login_data: LoginRequest, db: Session = Depends(get_db)):
    """Login and get access token."""
    user = authenticate_user(db, login_data.username, login_data.password)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    access_token = create_access_token(
        data={"sub": user.username},
        expires_delta=timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES),
    )
    return Token(access_token=access_token, token_type="bearer")


@router.post("/register", response_model=UserResponse, status_code=201)
def register(
    user_data: UserCreate,
    db: Session = Depends(get_db),
):
    """Register a new user."""
    # Check if username exists
    existing = db.query(User).filter(User.username == user_data.username).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Username already registered",
        )

    # Check if email exists
    if user_data.email:
        existing_email = db.query(User).filter(User.email == user_data.email).first()
        if existing_email:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Email already registered",
            )

    # Validate department_id if provided
    if user_data.department_id:
        department = db.query(Department).filter(
            Department.id == user_data.department_id
        ).first()
        if not department:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Department not found",
            )

    # Create user
    role = getattr(user_data, 'role', 'employee') or 'employee'
    user = User(
        username=user_data.username,
        email=user_data.email,
        full_name=user_data.full_name,
        extension=user_data.extension,
        department_id=user_data.department_id,
        hashed_password=get_password_hash(user_data.password),
        role=role,
    )

    # Set role-based flags
    if role == "superadmin":
        user.is_superadmin = True
        user.is_admin = True
    elif role == "admin":
        user.is_admin = True
        user.is_superadmin = False
    else:
        user.is_admin = False
        user.is_superadmin = False

    # First user is superadmin
    user_count = db.query(User).count()
    if user_count == 0:
        user.is_admin = True
        user.is_superadmin = True
        user.role = "superadmin"

    db.add(user)
    db.commit()
    db.refresh(user)
    return UserResponse.model_validate(user)


@router.get("/me", response_model=UserResponse)
def get_current_user_info(user: User = Depends(get_current_user_required)):
    """Get current user info."""
    return UserResponse.model_validate(user)


@router.put("/me", response_model=UserResponse)
def update_current_user(
    user_data: UserUpdate,
    user: User = Depends(get_current_user_required),
    db: Session = Depends(get_db),
):
    """Update current user info."""
    if user_data.email and user_data.email != user.email:
        existing = db.query(User).filter(User.email == user_data.email).first()
        if existing:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Email already registered",
            )
        user.email = user_data.email

    if user_data.full_name is not None:
        user.full_name = user_data.full_name

    if user_data.extension is not None:
        user.extension = user_data.extension

    if user_data.password:
        user.hashed_password = get_password_hash(user_data.password)

    db.commit()
    db.refresh(user)
    return UserResponse.model_validate(user)


@router.post("/change-password")
def change_password(
    data: ChangePassword,
    user: User = Depends(get_current_user_required),
    db: Session = Depends(get_db),
):
    """Change own password (requires current password)."""
    if not verify_password(data.current_password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Current password is incorrect",
        )
    if len(data.new_password) < 4:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="New password must be at least 4 characters",
        )
    user.hashed_password = get_password_hash(data.new_password)
    db.commit()
    return {"status": "ok", "message": "Password changed successfully"}


@router.post("/users/{user_id}/reset-password")
def reset_user_password(
    user_id: int,
    data: ResetPassword,
    admin: User = Depends(get_superadmin_user),
    db: Session = Depends(get_db),
):
    """Reset a user's password (superadmin only)."""
    target_user = db.query(User).filter(User.id == user_id).first()
    if not target_user:
        raise HTTPException(status_code=404, detail="User not found")
    if len(data.new_password) < 4:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="New password must be at least 4 characters",
        )
    target_user.hashed_password = get_password_hash(data.new_password)
    db.commit()
    return {"status": "ok", "message": f"Password reset for {target_user.username}"}


@router.get("/users", response_model=list[UserResponse])
def list_users(
    department_id: Optional[int] = None,
    role: Optional[str] = None,
    is_active: Optional[bool] = None,
    admin: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    """List all users with optional filters (admin only)."""
    query = db.query(User).options(joinedload(User.department))

    if department_id is not None:
        query = query.filter(User.department_id == department_id)
    if role is not None:
        query = query.filter(User.role == role)
    if is_active is not None:
        query = query.filter(User.is_active == is_active)

    users = query.order_by(User.username).all()
    return [UserResponse.model_validate(u) for u in users]


@router.get("/users/{user_id}", response_model=UserResponse)
def get_user(
    user_id: int,
    admin: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    """Get a specific user (admin only)."""
    user = db.query(User).options(
        joinedload(User.department)
    ).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return UserResponse.model_validate(user)


@router.put("/users/{user_id}", response_model=UserResponse)
def update_user(
    user_id: int,
    user_data: UserAdminUpdate,
    admin: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    """Update a user (admin only). Role changes require superadmin."""
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Check if trying to change role - requires superadmin
    if user_data.role is not None and user_data.role != user.role:
        if not admin.is_superadmin:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only superadmin can change user roles",
            )

        user.role = user_data.role
        if user_data.role == "superadmin":
            user.is_superadmin = True
            user.is_admin = True
        elif user_data.role == "admin":
            user.is_admin = True
            user.is_superadmin = False
        else:
            user.is_admin = False
            user.is_superadmin = False

    # Update other fields
    if user_data.username is not None and user_data.username != user.username:
        existing = db.query(User).filter(User.username == user_data.username).first()
        if existing:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Username already taken",
            )
        user.username = user_data.username

    if user_data.email is not None and user_data.email != user.email:
        if user_data.email:  # Only check if not empty
            existing = db.query(User).filter(User.email == user_data.email).first()
            if existing:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Email already registered",
                )
        user.email = user_data.email

    if user_data.full_name is not None:
        user.full_name = user_data.full_name

    if user_data.extension is not None:
        user.extension = user_data.extension

    if user_data.department_id is not None:
        if user_data.department_id:
            department = db.query(Department).filter(
                Department.id == user_data.department_id
            ).first()
            if not department:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Department not found",
                )
        user.department_id = user_data.department_id

    if user_data.is_active is not None:
        if user.id == admin.id and not user_data.is_active:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot deactivate yourself",
            )
        user.is_active = user_data.is_active

    if user_data.password:
        user.hashed_password = get_password_hash(user_data.password)

    db.commit()

    # Reload with department
    user = db.query(User).options(
        joinedload(User.department)
    ).filter(User.id == user_id).first()

    return UserResponse.model_validate(user)


@router.post("/users/{user_id}/assign-department", response_model=UserResponse)
def assign_user_department(
    user_id: int,
    department_id: int,
    admin: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    """Assign a department to a user (admin only)."""
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    department = db.query(Department).filter(Department.id == department_id).first()
    if not department:
        raise HTTPException(status_code=404, detail="Department not found")

    user.department_id = department_id
    db.commit()

    # Reload with department
    user = db.query(User).options(
        joinedload(User.department)
    ).filter(User.id == user_id).first()

    return UserResponse.model_validate(user)


@router.delete("/users/{user_id}", status_code=204)
def delete_user(
    user_id: int,
    admin: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    """Delete a user (admin only)."""
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    if user.id == admin.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot delete yourself",
        )

    db.delete(user)
    db.commit()
