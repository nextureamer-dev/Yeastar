"""Add department-wise analysis fields and tracking tables

Revision ID: 20260204_000001
Revises: 20260130_000001
Create Date: 2026-02-04 00:00:01.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '20260204_000001'
down_revision = '20260130_000001'
branch_labels = None
depends_on = None


def upgrade():
    # ==================== CALL_SUMMARIES TABLE ADDITIONS ====================

    # Star Rating (Universal)
    op.add_column('call_summaries', sa.Column('star_rating', sa.Integer(), nullable=True))
    op.add_column('call_summaries', sa.Column('star_rating_justification', sa.Text(), nullable=True))
    op.create_index('ix_call_summaries_star_rating', 'call_summaries', ['star_rating'])

    # Qualifier Department Fields
    op.add_column('call_summaries', sa.Column('qualifier_requirement_type', sa.String(50), nullable=True))
    op.add_column('call_summaries', sa.Column('qualifier_timeline', sa.String(50), nullable=True))
    op.add_column('call_summaries', sa.Column('qualifier_decision_maker_status', sa.String(50), nullable=True))
    op.add_column('call_summaries', sa.Column('qualifier_appointment_offered', sa.Boolean(), nullable=True))
    op.add_column('call_summaries', sa.Column('qualifier_fail_reason', sa.String(100), nullable=True))
    op.add_column('call_summaries', sa.Column('qualifier_service_name', sa.String(200), nullable=True))
    op.add_column('call_summaries', sa.Column('qualifier_short_description', sa.Text(), nullable=True))
    op.add_column('call_summaries', sa.Column('qualifier_expected_month', sa.String(20), nullable=True))
    op.add_column('call_summaries', sa.Column('qualifier_decision_role', sa.String(50), nullable=True))
    op.add_column('call_summaries', sa.Column('qualifier_availability', sa.Boolean(), nullable=True))
    op.add_column('call_summaries', sa.Column('qualifier_missing_fields', sa.JSON(), nullable=True))
    op.create_index('ix_call_summaries_qualifier_timeline', 'call_summaries', ['qualifier_timeline'])

    # Sales Department Fields
    op.add_column('call_summaries', sa.Column('sales_sql_eligible', sa.Boolean(), nullable=True))
    op.add_column('call_summaries', sa.Column('sales_notes_quality', sa.String(20), nullable=True))
    op.add_column('call_summaries', sa.Column('sales_exit_status', sa.String(50), nullable=True))
    op.add_column('call_summaries', sa.Column('sales_parking_status', sa.String(50), nullable=True))
    op.add_column('call_summaries', sa.Column('sales_last_contact_days', sa.Integer(), nullable=True))
    op.add_column('call_summaries', sa.Column('sales_next_action', sa.Text(), nullable=True))
    op.add_column('call_summaries', sa.Column('sales_qualification_reason', sa.Text(), nullable=True))
    op.add_column('call_summaries', sa.Column('sales_cadence_compliant', sa.Boolean(), nullable=True))
    op.create_index('ix_call_summaries_sales_sql_eligible', 'call_summaries', ['sales_sql_eligible'])
    op.create_index('ix_call_summaries_sales_exit_status', 'call_summaries', ['sales_exit_status'])

    # Call Center Department Fields
    op.add_column('call_summaries', sa.Column('cc_opening_compliant', sa.Boolean(), nullable=True))
    op.add_column('call_summaries', sa.Column('cc_opening_time_seconds', sa.Integer(), nullable=True))
    op.add_column('call_summaries', sa.Column('cc_satisfaction_question_asked', sa.Boolean(), nullable=True))
    op.add_column('call_summaries', sa.Column('cc_customer_response', sa.String(20), nullable=True))
    op.add_column('call_summaries', sa.Column('cc_call_category', sa.String(50), nullable=True))
    op.add_column('call_summaries', sa.Column('cc_whatsapp_handoff_valid', sa.Boolean(), nullable=True))
    op.add_column('call_summaries', sa.Column('cc_premium_pitch_quality', sa.String(20), nullable=True))
    op.create_index('ix_call_summaries_cc_call_category', 'call_summaries', ['cc_call_category'])

    # Cross-Department Fields
    op.add_column('call_summaries', sa.Column('future_opportunities', sa.JSON(), nullable=True))
    op.add_column('call_summaries', sa.Column('industry_interests', sa.JSON(), nullable=True))
    op.add_column('call_summaries', sa.Column('repeat_caller', sa.Boolean(), nullable=True))
    op.add_column('call_summaries', sa.Column('compliance_alerts', sa.JSON(), nullable=True))
    op.add_column('call_summaries', sa.Column('sla_breach', sa.Boolean(), nullable=True))
    op.add_column('call_summaries', sa.Column('talk_time_ratio', sa.Float(), nullable=True))
    op.add_column('call_summaries', sa.Column('greeting_compliant', sa.Boolean(), nullable=True))
    op.add_column('call_summaries', sa.Column('duration_anomaly', sa.Boolean(), nullable=True))
    op.add_column('call_summaries', sa.Column('handoff_quality', sa.String(20), nullable=True))
    op.add_column('call_summaries', sa.Column('department_analysis', sa.JSON(), nullable=True))
    op.create_index('ix_call_summaries_repeat_caller', 'call_summaries', ['repeat_caller'])
    op.create_index('ix_call_summaries_sla_breach', 'call_summaries', ['sla_breach'])

    # ==================== FOLLOW_UP_TRACKING TABLE ====================
    op.create_table(
        'follow_up_tracking',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('call_id', sa.String(100), sa.ForeignKey('call_summaries.call_id'), nullable=False),
        sa.Column('customer_phone', sa.String(50), nullable=False),
        sa.Column('staff_extension', sa.String(20), nullable=True),
        sa.Column('staff_name', sa.String(100), nullable=True),
        sa.Column('star_rating', sa.Integer(), nullable=True),
        sa.Column('last_contact_date', sa.DateTime(), nullable=True),
        sa.Column('next_follow_up_date', sa.DateTime(), nullable=True),
        sa.Column('days_since_contact', sa.Integer(), nullable=True),
        sa.Column('follow_up_status', sa.String(50), nullable=True),
        sa.Column('cadence_threshold', sa.Integer(), nullable=True),
        sa.Column('is_overdue', sa.Boolean(), default=False),
        sa.Column('status', sa.String(50), default='active'),
        sa.Column('parking_reason', sa.Text(), nullable=True),
        sa.Column('exit_reason', sa.String(100), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now(), onupdate=sa.func.now()),
    )
    op.create_index('ix_follow_up_tracking_call_id', 'follow_up_tracking', ['call_id'])
    op.create_index('ix_follow_up_tracking_customer_phone', 'follow_up_tracking', ['customer_phone'])
    op.create_index('ix_follow_up_tracking_staff_extension', 'follow_up_tracking', ['staff_extension'])
    op.create_index('ix_follow_up_tracking_last_contact_date', 'follow_up_tracking', ['last_contact_date'])
    op.create_index('ix_follow_up_tracking_next_follow_up_date', 'follow_up_tracking', ['next_follow_up_date'])
    op.create_index('ix_follow_up_tracking_is_overdue', 'follow_up_tracking', ['is_overdue'])

    # ==================== SLA_TRACKING TABLE ====================
    op.create_table(
        'sla_tracking',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('call_id', sa.String(100), sa.ForeignKey('call_summaries.call_id'), nullable=False),
        sa.Column('customer_phone', sa.String(50), nullable=False),
        sa.Column('staff_extension', sa.String(20), nullable=True),
        sa.Column('staff_name', sa.String(100), nullable=True),
        sa.Column('call_date', sa.DateTime(), nullable=True),
        sa.Column('response_time_seconds', sa.Integer(), nullable=True),
        sa.Column('opening_time_seconds', sa.Integer(), nullable=True),
        sa.Column('resolution_time_seconds', sa.Integer(), nullable=True),
        sa.Column('answer_sla_met', sa.Boolean(), nullable=True),
        sa.Column('opening_sla_met', sa.Boolean(), nullable=True),
        sa.Column('resolution_sla_met', sa.Boolean(), nullable=True),
        sa.Column('satisfaction_asked', sa.Boolean(), nullable=True),
        sa.Column('customer_satisfied', sa.Boolean(), nullable=True),
        sa.Column('first_call_resolution', sa.Boolean(), nullable=True),
        sa.Column('sla_breached', sa.Boolean(), default=False),
        sa.Column('breach_type', sa.String(50), nullable=True),
        sa.Column('breach_duration_seconds', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now(), onupdate=sa.func.now()),
    )
    op.create_index('ix_sla_tracking_call_id', 'sla_tracking', ['call_id'])
    op.create_index('ix_sla_tracking_customer_phone', 'sla_tracking', ['customer_phone'])
    op.create_index('ix_sla_tracking_staff_extension', 'sla_tracking', ['staff_extension'])
    op.create_index('ix_sla_tracking_call_date', 'sla_tracking', ['call_date'])
    op.create_index('ix_sla_tracking_sla_breached', 'sla_tracking', ['sla_breached'])


def downgrade():
    # Drop SLA tracking table
    op.drop_index('ix_sla_tracking_sla_breached', table_name='sla_tracking')
    op.drop_index('ix_sla_tracking_call_date', table_name='sla_tracking')
    op.drop_index('ix_sla_tracking_staff_extension', table_name='sla_tracking')
    op.drop_index('ix_sla_tracking_customer_phone', table_name='sla_tracking')
    op.drop_index('ix_sla_tracking_call_id', table_name='sla_tracking')
    op.drop_table('sla_tracking')

    # Drop follow-up tracking table
    op.drop_index('ix_follow_up_tracking_is_overdue', table_name='follow_up_tracking')
    op.drop_index('ix_follow_up_tracking_next_follow_up_date', table_name='follow_up_tracking')
    op.drop_index('ix_follow_up_tracking_last_contact_date', table_name='follow_up_tracking')
    op.drop_index('ix_follow_up_tracking_staff_extension', table_name='follow_up_tracking')
    op.drop_index('ix_follow_up_tracking_customer_phone', table_name='follow_up_tracking')
    op.drop_index('ix_follow_up_tracking_call_id', table_name='follow_up_tracking')
    op.drop_table('follow_up_tracking')

    # Drop cross-department indexes and columns
    op.drop_index('ix_call_summaries_sla_breach', table_name='call_summaries')
    op.drop_index('ix_call_summaries_repeat_caller', table_name='call_summaries')
    op.drop_column('call_summaries', 'department_analysis')
    op.drop_column('call_summaries', 'handoff_quality')
    op.drop_column('call_summaries', 'duration_anomaly')
    op.drop_column('call_summaries', 'greeting_compliant')
    op.drop_column('call_summaries', 'talk_time_ratio')
    op.drop_column('call_summaries', 'sla_breach')
    op.drop_column('call_summaries', 'compliance_alerts')
    op.drop_column('call_summaries', 'repeat_caller')
    op.drop_column('call_summaries', 'industry_interests')
    op.drop_column('call_summaries', 'future_opportunities')

    # Drop call center indexes and columns
    op.drop_index('ix_call_summaries_cc_call_category', table_name='call_summaries')
    op.drop_column('call_summaries', 'cc_premium_pitch_quality')
    op.drop_column('call_summaries', 'cc_whatsapp_handoff_valid')
    op.drop_column('call_summaries', 'cc_call_category')
    op.drop_column('call_summaries', 'cc_customer_response')
    op.drop_column('call_summaries', 'cc_satisfaction_question_asked')
    op.drop_column('call_summaries', 'cc_opening_time_seconds')
    op.drop_column('call_summaries', 'cc_opening_compliant')

    # Drop sales indexes and columns
    op.drop_index('ix_call_summaries_sales_exit_status', table_name='call_summaries')
    op.drop_index('ix_call_summaries_sales_sql_eligible', table_name='call_summaries')
    op.drop_column('call_summaries', 'sales_cadence_compliant')
    op.drop_column('call_summaries', 'sales_qualification_reason')
    op.drop_column('call_summaries', 'sales_next_action')
    op.drop_column('call_summaries', 'sales_last_contact_days')
    op.drop_column('call_summaries', 'sales_parking_status')
    op.drop_column('call_summaries', 'sales_exit_status')
    op.drop_column('call_summaries', 'sales_notes_quality')
    op.drop_column('call_summaries', 'sales_sql_eligible')

    # Drop qualifier indexes and columns
    op.drop_index('ix_call_summaries_qualifier_timeline', table_name='call_summaries')
    op.drop_column('call_summaries', 'qualifier_missing_fields')
    op.drop_column('call_summaries', 'qualifier_availability')
    op.drop_column('call_summaries', 'qualifier_decision_role')
    op.drop_column('call_summaries', 'qualifier_expected_month')
    op.drop_column('call_summaries', 'qualifier_short_description')
    op.drop_column('call_summaries', 'qualifier_service_name')
    op.drop_column('call_summaries', 'qualifier_fail_reason')
    op.drop_column('call_summaries', 'qualifier_appointment_offered')
    op.drop_column('call_summaries', 'qualifier_decision_maker_status')
    op.drop_column('call_summaries', 'qualifier_timeline')
    op.drop_column('call_summaries', 'qualifier_requirement_type')

    # Drop star rating index and columns
    op.drop_index('ix_call_summaries_star_rating', table_name='call_summaries')
    op.drop_column('call_summaries', 'star_rating_justification')
    op.drop_column('call_summaries', 'star_rating')
