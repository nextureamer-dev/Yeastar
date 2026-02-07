#!/usr/bin/env python3
"""
Seed script to add initial users to the Yeastar CRM system.
Run with: python seed_users.py
"""

import sys
import os

# Add the backend directory to the path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bcrypt
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

def get_password_hash(password: str) -> str:
    """Generate password hash using bcrypt directly."""
    return bcrypt.hashpw(
        password.encode('utf-8'),
        bcrypt.gensalt()
    ).decode('utf-8')

# Database connection - uses MySQL
DB_HOST = os.getenv('DB_HOST', 'localhost')
DB_PORT = os.getenv('DB_PORT', '3306')
DB_NAME = os.getenv('DB_NAME', 'yeastar_crm')
DB_USER = os.getenv('DB_USER', 'yeastar')
DB_PASSWORD = os.getenv('DB_PASSWORD', 'yeastar_pass_2024')

DATABASE_URL = f"mysql+pymysql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"

# Users to create
USERS = [
    {
        "username": "superadmin",
        "email": "superadmin@yeastar.local",
        "full_name": "Super Admin",
        "extension": None,
        "department": None,
        "password": "SuperAdmin@123",
        "is_admin": True,
        "is_superadmin": True,
        "role": "superadmin"
    },
    # Sales Team
    {
        "username": "swaroop",
        "email": "swaroop@yeastar.local",
        "full_name": "Swaroop",
        "extension": "211",
        "department": "Sales",
        "password": "Swaroop@123",
        "is_admin": False,
        "is_superadmin": False,
        "role": "employee"
    },
    {
        "username": "amith",
        "email": "amith@yeastar.local",
        "full_name": "Amith",
        "extension": "111",
        "department": "Sales",
        "password": "Amith@123",
        "is_admin": False,
        "is_superadmin": False,
        "role": "employee"
    },
    {
        "username": "saumil",
        "email": "saumil@yeastar.local",
        "full_name": "Saumil",
        "extension": "207",
        "department": "Sales",
        "password": "Saumil@123",
        "is_admin": False,
        "is_superadmin": False,
        "role": "employee"
    },
    {
        "username": "pranay",
        "email": "pranay@yeastar.local",
        "full_name": "Pranay",
        "extension": "208",
        "department": "Sales",
        "password": "Pranay@123",
        "is_admin": False,
        "is_superadmin": False,
        "role": "employee"
    },
    {
        "username": "sai",
        "email": "sai@yeastar.local",
        "full_name": "Sai",
        "extension": "209",
        "department": "Sales",
        "password": "Sai@123",
        "is_admin": False,
        "is_superadmin": False,
        "role": "employee"
    },
    # Call Center Team
    {
        "username": "jijina",
        "email": "jijina@yeastar.local",
        "full_name": "Jijina",
        "extension": "201",
        "department": "Call Centre",
        "password": "Jijina@123",
        "is_admin": False,
        "is_superadmin": False,
        "role": "employee"
    },
    {
        "username": "joanna",
        "email": "joanna@yeastar.local",
        "full_name": "Joanna",
        "extension": "202",
        "department": "Call Centre",
        "password": "Joanna@123",
        "is_admin": False,
        "is_superadmin": False,
        "role": "employee"
    },
    {
        "username": "ramshad",
        "email": "ramshad@yeastar.local",
        "full_name": "Ramshad",
        "extension": "203",
        "department": "Call Centre",
        "password": "Ramshad@123",
        "is_admin": False,
        "is_superadmin": False,
        "role": "employee"
    },
    # Qualifier Team
    {
        "username": "vismaya",
        "email": "vismaya@yeastar.local",
        "full_name": "Vismaya",
        "extension": "221",
        "department": "Qualifier",
        "password": "Vismaya@123",
        "is_admin": False,
        "is_superadmin": False,
        "role": "employee"
    },
]

def main():
    print(f"Connecting to database: {DATABASE_URL.replace(DB_PASSWORD, '***')}")

    try:
        engine = create_engine(DATABASE_URL)
        Session = sessionmaker(bind=engine)
        session = Session()

        # First, ensure the new columns exist (for existing databases)
        # MySQL doesn't support IF NOT EXISTS in ALTER TABLE, so we handle errors
        for col_sql in [
            "ALTER TABLE users ADD COLUMN is_superadmin BOOLEAN DEFAULT FALSE",
            "ALTER TABLE users ADD COLUMN role VARCHAR(20) DEFAULT 'employee'",
            "ALTER TABLE users ADD COLUMN department VARCHAR(50) DEFAULT NULL"
        ]:
            try:
                session.execute(text(col_sql))
                session.commit()
            except Exception as e:
                if "Duplicate column" in str(e):
                    pass  # Column already exists, ignore
                else:
                    print(f"Note: {e}")
                session.rollback()
        print("Database schema verified.")

        # Add each user
        for user_data in USERS:
            # Check if user already exists
            result = session.execute(
                text("SELECT id FROM users WHERE username = :username"),
                {"username": user_data["username"]}
            )
            existing = result.fetchone()

            if existing:
                print(f"User '{user_data['username']}' already exists (ID: {existing[0]}). Updating...")
                # Update existing user
                session.execute(
                    text("""
                        UPDATE users SET
                            email = :email,
                            full_name = :full_name,
                            extension = :extension,
                            department = :department,
                            hashed_password = :hashed_password,
                            is_admin = :is_admin,
                            is_superadmin = :is_superadmin,
                            role = :role,
                            is_active = TRUE
                        WHERE username = :username
                    """),
                    {
                        "username": user_data["username"],
                        "email": user_data["email"],
                        "full_name": user_data["full_name"],
                        "extension": user_data["extension"],
                        "department": user_data.get("department"),
                        "hashed_password": get_password_hash(user_data["password"]),
                        "is_admin": user_data["is_admin"],
                        "is_superadmin": user_data["is_superadmin"],
                        "role": user_data["role"]
                    }
                )
            else:
                print(f"Creating user '{user_data['username']}'...")
                # Insert new user
                session.execute(
                    text("""
                        INSERT INTO users (username, email, full_name, extension, department, hashed_password, is_active, is_admin, is_superadmin, role)
                        VALUES (:username, :email, :full_name, :extension, :department, :hashed_password, TRUE, :is_admin, :is_superadmin, :role)
                    """),
                    {
                        "username": user_data["username"],
                        "email": user_data["email"],
                        "full_name": user_data["full_name"],
                        "extension": user_data["extension"],
                        "department": user_data.get("department"),
                        "hashed_password": get_password_hash(user_data["password"]),
                        "is_admin": user_data["is_admin"],
                        "is_superadmin": user_data["is_superadmin"],
                        "role": user_data["role"]
                    }
                )

        session.commit()
        print("\nUsers created/updated successfully!")
        print("\n" + "="*80)
        print("USER CREDENTIALS:")
        print("="*80)
        for user in USERS:
            dept = user.get("department", "N/A") or "N/A"
            ext_label = user['extension'] or "N/A"
            print(f"  {user['username']:18} | Pass: {user['password']:15} | Ext: {ext_label:4} | Dept: {dept}")
        print("="*80)

        session.close()

    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    main()
