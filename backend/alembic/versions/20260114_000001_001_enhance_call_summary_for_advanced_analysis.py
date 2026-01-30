"""Enhance call_summary table with advanced analysis fields

This migration adds comprehensive fields for:
- Staff information (extension, department, role mapping)
- Sales/Lead tracking (opportunity, quality, pipeline value)
- Customer profiling (type, phone, profile data)
- Performance metrics (individual scores for professionalism, knowledge, etc.)
- Compliance and quality checks
- Follow-up management

Revision ID: 001
Revises: None (initial migration)
Create Date: 2026-01-14

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '001'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Use batch mode for SQLite compatibility
    with op.batch_alter_table('call_summaries', schema=None) as batch_op:
        # Service classification
        batch_op.add_column(sa.Column('service_subcategory', sa.String(200), nullable=True))

        # Staff information - Extension mapping
        batch_op.add_column(sa.Column('staff_extension', sa.String(20), nullable=True))
        batch_op.add_column(sa.Column('staff_department', sa.String(50), nullable=True))
        batch_op.add_column(sa.Column('staff_role', sa.String(100), nullable=True))

        # Customer information
        batch_op.add_column(sa.Column('customer_phone', sa.String(50), nullable=True))

        # Conversation details
        batch_op.add_column(sa.Column('commitments_made', sa.JSON(), nullable=True))

        # Sales/Business classification
        batch_op.add_column(sa.Column('call_classification', sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column('is_sales_opportunity', sa.Boolean(), nullable=True, default=False))
        batch_op.add_column(sa.Column('lead_quality', sa.String(20), nullable=True))
        batch_op.add_column(sa.Column('estimated_deal_value', sa.Float(), nullable=True))
        batch_op.add_column(sa.Column('conversion_likelihood', sa.String(20), nullable=True))
        batch_op.add_column(sa.Column('urgency_level', sa.String(50), nullable=True))
        batch_op.add_column(sa.Column('follow_up_required', sa.Boolean(), nullable=True, default=False))
        batch_op.add_column(sa.Column('follow_up_date', sa.DateTime(), nullable=True))

        # Customer profile
        batch_op.add_column(sa.Column('customer_profile', sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column('customer_type', sa.String(50), nullable=True))

        # Employee performance scores (1-10)
        batch_op.add_column(sa.Column('professionalism_score', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('knowledge_score', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('communication_score', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('empathy_score', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('overall_performance_score', sa.Integer(), nullable=True))

        # Compliance and quality
        batch_op.add_column(sa.Column('compliance_check', sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column('call_quality_metrics', sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column('first_call_resolution', sa.Boolean(), nullable=True))
        batch_op.add_column(sa.Column('customer_effort_score', sa.String(20), nullable=True))

        # Create indexes for frequently queried columns
        batch_op.create_index('ix_call_summaries_staff_extension', ['staff_extension'])
        batch_op.create_index('ix_call_summaries_staff_department', ['staff_department'])
        batch_op.create_index('ix_call_summaries_staff_name', ['staff_name'])
        batch_op.create_index('ix_call_summaries_customer_phone', ['customer_phone'])
        batch_op.create_index('ix_call_summaries_is_sales_opportunity', ['is_sales_opportunity'])
        batch_op.create_index('ix_call_summaries_lead_quality', ['lead_quality'])
        batch_op.create_index('ix_call_summaries_follow_up_required', ['follow_up_required'])
        batch_op.create_index('ix_call_summaries_customer_type', ['customer_type'])
        batch_op.create_index('ix_call_summaries_overall_performance_score', ['overall_performance_score'])
        batch_op.create_index('ix_call_summaries_call_type', ['call_type'])
        batch_op.create_index('ix_call_summaries_service_category', ['service_category'])
        batch_op.create_index('ix_call_summaries_resolution_status', ['resolution_status'])
        batch_op.create_index('ix_call_summaries_sentiment', ['sentiment'])
        batch_op.create_index('ix_call_summaries_created_at', ['created_at'])


def downgrade() -> None:
    with op.batch_alter_table('call_summaries', schema=None) as batch_op:
        # Drop indexes first
        batch_op.drop_index('ix_call_summaries_created_at')
        batch_op.drop_index('ix_call_summaries_sentiment')
        batch_op.drop_index('ix_call_summaries_resolution_status')
        batch_op.drop_index('ix_call_summaries_service_category')
        batch_op.drop_index('ix_call_summaries_call_type')
        batch_op.drop_index('ix_call_summaries_overall_performance_score')
        batch_op.drop_index('ix_call_summaries_customer_type')
        batch_op.drop_index('ix_call_summaries_follow_up_required')
        batch_op.drop_index('ix_call_summaries_lead_quality')
        batch_op.drop_index('ix_call_summaries_is_sales_opportunity')
        batch_op.drop_index('ix_call_summaries_customer_phone')
        batch_op.drop_index('ix_call_summaries_staff_name')
        batch_op.drop_index('ix_call_summaries_staff_department')
        batch_op.drop_index('ix_call_summaries_staff_extension')

        # Drop columns
        batch_op.drop_column('customer_effort_score')
        batch_op.drop_column('first_call_resolution')
        batch_op.drop_column('call_quality_metrics')
        batch_op.drop_column('compliance_check')
        batch_op.drop_column('overall_performance_score')
        batch_op.drop_column('empathy_score')
        batch_op.drop_column('communication_score')
        batch_op.drop_column('knowledge_score')
        batch_op.drop_column('professionalism_score')
        batch_op.drop_column('customer_type')
        batch_op.drop_column('customer_profile')
        batch_op.drop_column('follow_up_date')
        batch_op.drop_column('follow_up_required')
        batch_op.drop_column('urgency_level')
        batch_op.drop_column('conversion_likelihood')
        batch_op.drop_column('estimated_deal_value')
        batch_op.drop_column('lead_quality')
        batch_op.drop_column('is_sales_opportunity')
        batch_op.drop_column('call_classification')
        batch_op.drop_column('commitments_made')
        batch_op.drop_column('customer_phone')
        batch_op.drop_column('staff_role')
        batch_op.drop_column('staff_department')
        batch_op.drop_column('staff_extension')
        batch_op.drop_column('service_subcategory')
