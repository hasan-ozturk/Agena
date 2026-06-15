"""flow_runs: durable async execution — organization_id, snapshot, updated_at, resume_attempts

Moving flow execution off the synchronous request thread onto the worker with
crash recovery. The run needs to be resumable standalone (snapshot), scoped to
an org (recovery query + isolation), and have a staleness signal (updated_at)
plus an auto-resume cap (resume_attempts).

Revision ID: 0069_flow_run_durable
Revises: 0068_alert_rule_noise
Create Date: 2026-06-15
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect


revision = '0069_flow_run_durable'
down_revision = '0068_alert_rule_noise'
branch_labels = None
depends_on = None


def _has_col(bind, table, col):
    insp = inspect(bind)
    return insp.has_table(table) and any(c['name'] == col for c in insp.get_columns(table))


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_col(bind, 'flow_runs', 'organization_id'):
        op.add_column('flow_runs', sa.Column('organization_id', sa.Integer(), nullable=True))
        op.create_index('ix_flow_runs_organization_id', 'flow_runs', ['organization_id'])
    if not _has_col(bind, 'flow_runs', 'flow_snapshot_json'):
        op.add_column('flow_runs', sa.Column('flow_snapshot_json', sa.Text(), nullable=True))
    if not _has_col(bind, 'flow_runs', 'updated_at'):
        op.add_column('flow_runs', sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now(), nullable=True))
    if not _has_col(bind, 'flow_runs', 'resume_attempts'):
        op.add_column('flow_runs', sa.Column('resume_attempts', sa.Integer(), nullable=False, server_default='0'))

    # Backfill organization_id for existing rows from the user's org membership.
    # (A multi-org user resolves to one membership row — acceptable for legacy
    # rows; all new rows set org explicitly.)
    op.execute(
        """
        UPDATE flow_runs fr
        JOIN organization_members om ON om.user_id = fr.user_id
        SET fr.organization_id = om.organization_id
        WHERE fr.organization_id IS NULL
        """
    )


def downgrade() -> None:
    bind = op.get_bind()
    if _has_col(bind, 'flow_runs', 'organization_id'):
        op.drop_index('ix_flow_runs_organization_id', table_name='flow_runs')
    for col in ('resume_attempts', 'updated_at', 'flow_snapshot_json', 'organization_id'):
        if _has_col(bind, 'flow_runs', col):
            op.drop_column('flow_runs', col)
