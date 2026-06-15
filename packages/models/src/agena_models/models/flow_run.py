from datetime import datetime
from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from agena_core.db.base import Base
import enum


class RunStatus(str, enum.Enum):
    pending = 'pending'
    queued = 'queued'  # pre-created, enqueued, not yet picked up by the worker
    running = 'running'
    completed = 'completed'
    failed = 'failed'
    cancelled = 'cancelled'
    # Approval-gate lifecycle (columns are plain String(20); these enums are
    # informational): paused at a waitForApproval node → user approves →
    # 'resuming' until the worker picks the resume job up → 'running'.
    pending_approval = 'pending_approval'
    resuming = 'resuming'
    rejected = 'rejected'


class StepStatus(str, enum.Enum):
    pending = 'pending'
    running = 'running'
    completed = 'completed'
    failed = 'failed'
    skipped = 'skipped'
    # Approval-gate states for the gated node's step row.
    awaiting_approval = 'awaiting_approval'
    approved = 'approved'
    rejected = 'rejected'


class FlowRun(Base):
    __tablename__ = 'flow_runs'

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    flow_id: Mapped[str] = mapped_column(String(255), nullable=False)
    flow_name: Mapped[str] = mapped_column(String(255), nullable=False)
    task_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    task_title: Mapped[str | None] = mapped_column(Text, nullable=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    # Multi-tenant scope + recovery: nullable so the migration can backfill
    # existing rows (user→org); always set going forward.
    organization_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(20), default='pending')
    # Durable per-run snapshot {flow, task, user_id, organization_id} so the
    # worker can execute/resume a run standalone (no dependency on Redis TTL or
    # the gate step). Written at run creation.
    flow_snapshot_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Touched on every per-step commit → staleness signal for the recovery
    # poller (a genuinely-running run keeps this fresh).
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=True)
    # Auto-resume cap: incremented each time the recovery poller requeues a
    # stale run; past the cap the run is marked failed.
    resume_attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default='0', default=0)
    started_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    steps: Mapped[list['FlowRunStep']] = relationship('FlowRunStep', back_populates='run', cascade='all, delete-orphan', order_by='FlowRunStep.id')


class FlowRunStep(Base):
    __tablename__ = 'flow_run_steps'

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(ForeignKey('flow_runs.id', ondelete='CASCADE'), nullable=False)
    node_id: Mapped[str] = mapped_column(String(255), nullable=False)
    node_type: Mapped[str] = mapped_column(String(50), nullable=False)
    node_label: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default='pending')
    input_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    output_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_msg: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    run: Mapped['FlowRun'] = relationship('FlowRun', back_populates='steps')
