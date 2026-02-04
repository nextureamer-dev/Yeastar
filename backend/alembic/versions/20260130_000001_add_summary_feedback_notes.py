"""Add feedback fields to call_summaries and create summary_notes table

Revision ID: 20260130_000001
Revises: 20260114_000001_001
Create Date: 2026-01-30 00:00:01.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '20260130_000001'
down_revision = '001'
branch_labels = None
depends_on = None


def upgrade():
    # Add feedback columns to call_summaries table
    op.add_column('call_summaries', sa.Column('feedback_rating', sa.Integer(), nullable=True))
    op.add_column('call_summaries', sa.Column('feedback_by', sa.String(100), nullable=True))
    op.add_column('call_summaries', sa.Column('feedback_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('call_summaries', sa.Column('feedback_comment', sa.Text(), nullable=True))

    # Create summary_notes table
    op.create_table(
        'summary_notes',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('call_id', sa.String(100), sa.ForeignKey('call_summaries.call_id'), nullable=False, index=True),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('created_by', sa.String(100), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), onupdate=sa.func.now()),
    )

    # Create index on call_id for faster lookups
    op.create_index('ix_summary_notes_call_id', 'summary_notes', ['call_id'])


def downgrade():
    # Drop summary_notes table
    op.drop_index('ix_summary_notes_call_id', table_name='summary_notes')
    op.drop_table('summary_notes')

    # Remove feedback columns from call_summaries
    op.drop_column('call_summaries', 'feedback_comment')
    op.drop_column('call_summaries', 'feedback_at')
    op.drop_column('call_summaries', 'feedback_by')
    op.drop_column('call_summaries', 'feedback_rating')
