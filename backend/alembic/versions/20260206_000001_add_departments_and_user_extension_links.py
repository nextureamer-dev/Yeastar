"""Add departments table and link users/extensions to departments

Revision ID: 20260206_000001
Revises: 20260204_000001
Create Date: 2026-02-06 00:00:01.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '20260206_000001'
down_revision = '20260204_000001'
branch_labels = None
depends_on = None


def upgrade():
    # ==================== DEPARTMENTS TABLE ====================

    # Create departments table
    op.create_table(
        'departments',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('name', sa.String(100), unique=True, nullable=False, index=True),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('is_active', sa.Boolean(), default=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), onupdate=sa.func.now()),
    )

    # ==================== USERS TABLE CHANGES ====================

    # Add department_id to users table
    op.add_column('users', sa.Column('department_id', sa.Integer(), nullable=True))
    op.create_foreign_key(
        'fk_users_department_id',
        'users',
        'departments',
        ['department_id'],
        ['id']
    )
    op.create_index('ix_users_department_id', 'users', ['department_id'])

    # ==================== EXTENSIONS TABLE CHANGES ====================

    # Add user_id and department_id to extensions table
    op.add_column('extensions', sa.Column('user_id', sa.Integer(), nullable=True))
    op.add_column('extensions', sa.Column('department_id', sa.Integer(), nullable=True))

    op.create_foreign_key(
        'fk_extensions_user_id',
        'extensions',
        'users',
        ['user_id'],
        ['id']
    )
    op.create_foreign_key(
        'fk_extensions_department_id',
        'extensions',
        'departments',
        ['department_id'],
        ['id']
    )
    op.create_index('ix_extensions_user_id', 'extensions', ['user_id'])
    op.create_index('ix_extensions_department_id', 'extensions', ['department_id'])


def downgrade():
    # Drop indexes and foreign keys from extensions
    op.drop_index('ix_extensions_department_id', table_name='extensions')
    op.drop_index('ix_extensions_user_id', table_name='extensions')
    op.drop_constraint('fk_extensions_department_id', 'extensions', type_='foreignkey')
    op.drop_constraint('fk_extensions_user_id', 'extensions', type_='foreignkey')
    op.drop_column('extensions', 'department_id')
    op.drop_column('extensions', 'user_id')

    # Drop index and foreign key from users
    op.drop_index('ix_users_department_id', table_name='users')
    op.drop_constraint('fk_users_department_id', 'users', type_='foreignkey')
    op.drop_column('users', 'department_id')

    # Drop departments table
    op.drop_table('departments')
