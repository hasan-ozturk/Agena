"""Flow run endpoints."""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from agena_api.api.dependencies import CurrentTenant, get_current_tenant
from agena_core.database import get_db_session
from agena_models.models.flow_assets import AgentAnalyticsSnapshot, FlowTemplate, FlowVersion
from agena_models.models.flow_run import FlowRun, FlowRunStep
from agena_models.models.user_preference import UserPreference
from agena_services.services.flow_executor import run_flow

logger = logging.getLogger(__name__)

router = APIRouter(prefix='/flows', tags=['flows'])


class RunFlowRequest(BaseModel):
    flow_id: str
    task: dict[str, Any]  # {id, title, state, description, ...}


class StepOut(BaseModel):
    id: int
    node_id: str
    node_type: str
    node_label: str | None
    status: str
    output: Any
    error_msg: str | None
    started_at: str | None
    finished_at: str | None


class RunOut(BaseModel):
    id: int
    flow_id: str
    flow_name: str
    task_id: str | None
    task_title: str | None
    status: str
    started_at: str
    finished_at: str | None
    steps: list[StepOut]


class FlowTemplateIn(BaseModel):
    name: str
    description: str | None = None
    flow: dict[str, Any]


class FlowTemplateOut(BaseModel):
    id: int
    name: str
    description: str | None
    flow: dict[str, Any]
    created_at: str
    updated_at: str


class FlowVersionIn(BaseModel):
    flow_name: str
    label: str
    flow: dict[str, Any]


class FlowVersionOut(BaseModel):
    id: int
    flow_id: str
    flow_name: str
    label: str
    flow: dict[str, Any]
    created_at: str


class AgentAnalyticsOut(BaseModel):
    snapshot_id: int | None
    created_at: str | None
    data: dict[str, Any]


def _step_out(s: FlowRunStep) -> StepOut:
    output = None
    if s.output_json:
        try:
            output = json.loads(s.output_json)
        except Exception:
            output = s.output_json
    return StepOut(
        id=s.id,
        node_id=s.node_id,
        node_type=s.node_type,
        node_label=s.node_label,
        status=s.status,
        output=output,
        error_msg=s.error_msg,
        started_at=s.started_at.isoformat() if s.started_at else None,
        finished_at=s.finished_at.isoformat() if s.finished_at else None,
    )


def _run_out(r: FlowRun) -> RunOut:
    return RunOut(
        id=r.id,
        flow_id=r.flow_id,
        flow_name=r.flow_name,
        task_id=r.task_id,
        task_title=r.task_title,
        status=r.status,
        started_at=r.started_at.isoformat(),
        finished_at=r.finished_at.isoformat() if r.finished_at else None,
        steps=[_step_out(s) for s in (r.steps or [])],
    )


def _template_out(tpl: FlowTemplate) -> FlowTemplateOut:
    flow = json.loads(tpl.flow_json)
    return FlowTemplateOut(
        id=tpl.id,
        name=tpl.name,
        description=tpl.description,
        flow=flow,
        created_at=tpl.created_at.isoformat(),
        updated_at=tpl.updated_at.isoformat(),
    )


def _version_out(ver: FlowVersion) -> FlowVersionOut:
    flow = json.loads(ver.flow_json)
    return FlowVersionOut(
        id=ver.id,
        flow_id=ver.flow_id,
        flow_name=ver.flow_name,
        label=ver.label,
        flow=flow,
        created_at=ver.created_at.isoformat(),
    )


@router.post('/run', response_model=RunOut)
async def run_flow_endpoint(
    body: RunFlowRequest,
    tenant: CurrentTenant = Depends(get_current_tenant),
    db: AsyncSession = Depends(get_db_session),
) -> RunOut:
    """Flow'u çalıştır."""
    # Kullanıcının kayıtlı flow'larından bul
    result = await db.execute(
        select(UserPreference).where(UserPreference.user_id == tenant.user_id)
    )
    pref = result.scalar_one_or_none()
    if not pref or not pref.flows_json:
        raise HTTPException(status_code=404, detail='Flow bulunamadı')

    flows: list[dict[str, Any]] = json.loads(pref.flows_json)
    flow = next((f for f in flows if f['id'] == body.flow_id), None)
    if not flow:
        raise HTTPException(status_code=404, detail=f'Flow {body.flow_id} bulunamadı')

    flow_run = await run_flow(
        flow=flow,
        task=body.task,
        user_id=tenant.user_id,
        organization_id=tenant.organization_id,
        db=db,
    )
    # Reload with eager-loaded steps to avoid async lazy-load during response serialization.
    run_result = await db.execute(
        select(FlowRun)
        .where(FlowRun.id == flow_run.id, FlowRun.user_id == tenant.user_id)
        .options(selectinload(FlowRun.steps))
    )
    run_row = run_result.scalar_one_or_none()
    if not run_row:
        raise HTTPException(status_code=404, detail='Run bulunamadı')
    return _run_out(run_row)


@router.get('/runs', response_model=list[RunOut])
async def list_runs(
    limit: int = 20,
    tenant: CurrentTenant = Depends(get_current_tenant),
    db: AsyncSession = Depends(get_db_session),
) -> list[RunOut]:
    """Son flow run'larını listele."""
    result = await db.execute(
        select(FlowRun)
        .where(FlowRun.user_id == tenant.user_id)
        .options(selectinload(FlowRun.steps))
        .order_by(FlowRun.started_at.desc())
        .limit(limit)
    )
    runs = result.scalars().all()
    return [_run_out(r) for r in runs]


@router.get('/runs/{run_id}', response_model=RunOut)
async def get_run(
    run_id: int,
    tenant: CurrentTenant = Depends(get_current_tenant),
    db: AsyncSession = Depends(get_db_session),
) -> RunOut:
    result = await db.execute(
        select(FlowRun)
        .where(FlowRun.id == run_id, FlowRun.user_id == tenant.user_id)
        .options(selectinload(FlowRun.steps))
    )
    run = result.scalar_one_or_none()
    if not run:
        raise HTTPException(status_code=404, detail='Run bulunamadı')
    return _run_out(run)


async def _resume_flow_run_bg(run_id: int, organization_id: int, snapshot_raw: str | None) -> None:
    """Resume a paused flow run on a fresh session (fire-and-forget).

    The {flow, task, user_id} snapshot was persisted into the gate step's
    input_json at pause time, so this survives Redis flushes and works no
    matter which entry path (sync API / worker) started the run. Any
    failure flips the run back to 'pending_approval' so the user can simply
    click Approve again."""
    from agena_core.database import SessionLocal

    try:
        snapshot = json.loads(snapshot_raw or '{}')
    except Exception:
        snapshot = {}
    flow = snapshot.get('flow')
    task = snapshot.get('task') or {}
    user_id = int(snapshot.get('user_id') or 0)

    async with SessionLocal() as session:
        try:
            run = await session.get(FlowRun, run_id)
            if run is None:
                return
            if not flow:
                # Legacy/last-resort fallback: rebuild from the user's saved
                # flows. Validated implicitly — unknown node ids simply have
                # no prior steps and re-execute.
                pref = (await session.execute(
                    select(UserPreference).where(UserPreference.user_id == run.user_id)
                )).scalar_one_or_none()
                flows = json.loads(pref.flows_json) if pref and pref.flows_json else []
                flow = next((f for f in flows if f.get('id') == run.flow_id), None)
                user_id = user_id or run.user_id
                task = task or {'id': run.task_id, 'title': run.task_title or ''}
                if not flow:
                    raise ValueError('Flow definition not found for resume (snapshot and saved flows both missing)')
            await run_flow(
                flow=flow,
                task=task,
                user_id=user_id or run.user_id,
                organization_id=organization_id,
                db=session,
                resume_run_id=run_id,
            )
        except Exception:
            logger.exception('Flow resume failed for run %s', run_id)
            try:
                run = await session.get(FlowRun, run_id)
                if run is not None and run.status in ('resuming', 'running'):
                    run.status = 'pending_approval'
                    await session.commit()
            except Exception:
                pass


@router.post('/runs/{run_id}/approve', response_model=RunOut)
async def approve_run(
    run_id: int,
    tenant: CurrentTenant = Depends(get_current_tenant),
    db: AsyncSession = Depends(get_db_session),
) -> RunOut:
    """Onay kapısında bekleyen koşuyu onaylar ve arka planda devam ettirir."""
    # Conditional UPDATE → exactly one concurrent approve wins; the loser
    # (double click) sees rowcount 0 and gets a 409 instead of a second
    # resume task.
    upd = await db.execute(
        update(FlowRun)
        .where(
            FlowRun.id == run_id,
            FlowRun.user_id == tenant.user_id,
            FlowRun.status == 'pending_approval',
        )
        .values(status='resuming')
    )
    if (upd.rowcount or 0) == 0:
        existing = (await db.execute(
            select(FlowRun).where(FlowRun.id == run_id, FlowRun.user_id == tenant.user_id)
        )).scalar_one_or_none()
        if existing is None:
            raise HTTPException(status_code=404, detail='Run bulunamadı')
        raise HTTPException(status_code=409, detail=f'Run is not pending approval (status={existing.status})')

    gate = (await db.execute(
        select(FlowRunStep)
        .where(FlowRunStep.run_id == run_id, FlowRunStep.status == 'awaiting_approval')
        .order_by(FlowRunStep.id.desc())
    )).scalars().first()
    snapshot_raw = gate.input_json if gate is not None else None
    if gate is not None:
        gate.status = 'approved'
        gate.finished_at = datetime.now(timezone.utc)
    await db.commit()

    asyncio.create_task(_resume_flow_run_bg(run_id, tenant.organization_id, snapshot_raw))

    run_row = (await db.execute(
        select(FlowRun)
        .where(FlowRun.id == run_id, FlowRun.user_id == tenant.user_id)
        .options(selectinload(FlowRun.steps))
    )).scalar_one_or_none()
    if not run_row:
        raise HTTPException(status_code=404, detail='Run bulunamadı')
    return _run_out(run_row)


@router.post('/runs/{run_id}/reject', response_model=RunOut)
async def reject_run(
    run_id: int,
    tenant: CurrentTenant = Depends(get_current_tenant),
    db: AsyncSession = Depends(get_db_session),
) -> RunOut:
    """Onay kapısında bekleyen koşuyu reddeder (terminal)."""
    upd = await db.execute(
        update(FlowRun)
        .where(
            FlowRun.id == run_id,
            FlowRun.user_id == tenant.user_id,
            FlowRun.status == 'pending_approval',
        )
        .values(status='rejected', finished_at=datetime.now(timezone.utc))
    )
    if (upd.rowcount or 0) == 0:
        existing = (await db.execute(
            select(FlowRun).where(FlowRun.id == run_id, FlowRun.user_id == tenant.user_id)
        )).scalar_one_or_none()
        if existing is None:
            raise HTTPException(status_code=404, detail='Run bulunamadı')
        raise HTTPException(status_code=409, detail=f'Run is not pending approval (status={existing.status})')

    gate = (await db.execute(
        select(FlowRunStep)
        .where(FlowRunStep.run_id == run_id, FlowRunStep.status == 'awaiting_approval')
        .order_by(FlowRunStep.id.desc())
    )).scalars().first()
    if gate is not None:
        gate.status = 'rejected'
        gate.finished_at = datetime.now(timezone.utc)

    # Best-effort: release the linked task out of waiting_approval.
    run_for_task = (await db.execute(
        select(FlowRun).where(FlowRun.id == run_id)
    )).scalar_one_or_none()
    if run_for_task and (run_for_task.task_id or '').strip().isdigit():
        try:
            from agena_models.models.task_record import TaskRecord
            task_row = await db.get(TaskRecord, int(run_for_task.task_id))
            if task_row is not None and task_row.status == 'waiting_approval':
                task_row.status = 'cancelled'
        except Exception:
            pass
    await db.commit()

    run_row = (await db.execute(
        select(FlowRun)
        .where(FlowRun.id == run_id, FlowRun.user_id == tenant.user_id)
        .options(selectinload(FlowRun.steps))
    )).scalar_one_or_none()
    if not run_row:
        raise HTTPException(status_code=404, detail='Run bulunamadı')
    return _run_out(run_row)


@router.get('/templates', response_model=list[FlowTemplateOut])
async def list_templates(
    tenant: CurrentTenant = Depends(get_current_tenant),
    db: AsyncSession = Depends(get_db_session),
) -> list[FlowTemplateOut]:
    result = await db.execute(
        select(FlowTemplate)
        .where(FlowTemplate.organization_id == tenant.organization_id)
        .order_by(FlowTemplate.updated_at.desc())
    )
    rows = result.scalars().all()
    return [_template_out(r) for r in rows]


@router.post('/templates', response_model=FlowTemplateOut)
async def create_template(
    body: FlowTemplateIn,
    tenant: CurrentTenant = Depends(get_current_tenant),
    db: AsyncSession = Depends(get_db_session),
) -> FlowTemplateOut:
    row = FlowTemplate(
        organization_id=tenant.organization_id,
        name=body.name,
        description=body.description,
        flow_json=json.dumps(body.flow, ensure_ascii=False),
        created_by_user_id=tenant.user_id,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return _template_out(row)


@router.put('/templates/{template_id}', response_model=FlowTemplateOut)
async def update_template(
    template_id: int,
    body: FlowTemplateIn,
    tenant: CurrentTenant = Depends(get_current_tenant),
    db: AsyncSession = Depends(get_db_session),
) -> FlowTemplateOut:
    result = await db.execute(
        select(FlowTemplate).where(
            FlowTemplate.id == template_id,
            FlowTemplate.organization_id == tenant.organization_id,
        )
    )
    row = result.scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail='Template bulunamadı')
    row.name = body.name
    row.description = body.description
    row.flow_json = json.dumps(body.flow, ensure_ascii=False)
    await db.commit()
    await db.refresh(row)
    return _template_out(row)


@router.delete('/templates/{template_id}')
async def delete_template(
    template_id: int,
    tenant: CurrentTenant = Depends(get_current_tenant),
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, bool]:
    result = await db.execute(
        select(FlowTemplate).where(
            FlowTemplate.id == template_id,
            FlowTemplate.organization_id == tenant.organization_id,
        )
    )
    row = result.scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail='Template bulunamadı')
    await db.delete(row)
    await db.commit()
    return {'ok': True}


@router.get('/{flow_id}/versions', response_model=list[FlowVersionOut])
async def list_versions(
    flow_id: str,
    limit: int = 30,
    tenant: CurrentTenant = Depends(get_current_tenant),
    db: AsyncSession = Depends(get_db_session),
) -> list[FlowVersionOut]:
    result = await db.execute(
        select(FlowVersion)
        .where(
            FlowVersion.organization_id == tenant.organization_id,
            FlowVersion.user_id == tenant.user_id,
            FlowVersion.flow_id == flow_id,
        )
        .order_by(FlowVersion.created_at.desc())
        .limit(limit)
    )
    rows = result.scalars().all()
    return [_version_out(r) for r in rows]


@router.post('/{flow_id}/versions', response_model=FlowVersionOut)
async def create_version(
    flow_id: str,
    body: FlowVersionIn,
    tenant: CurrentTenant = Depends(get_current_tenant),
    db: AsyncSession = Depends(get_db_session),
) -> FlowVersionOut:
    row = FlowVersion(
        organization_id=tenant.organization_id,
        user_id=tenant.user_id,
        flow_id=flow_id,
        flow_name=body.flow_name,
        label=body.label,
        flow_json=json.dumps(body.flow, ensure_ascii=False),
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return _version_out(row)


@router.get('/{flow_id}/versions/{version_id}', response_model=FlowVersionOut)
async def get_version(
    flow_id: str,
    version_id: int,
    tenant: CurrentTenant = Depends(get_current_tenant),
    db: AsyncSession = Depends(get_db_session),
) -> FlowVersionOut:
    result = await db.execute(
        select(FlowVersion).where(
            FlowVersion.id == version_id,
            FlowVersion.flow_id == flow_id,
            FlowVersion.organization_id == tenant.organization_id,
            FlowVersion.user_id == tenant.user_id,
        )
    )
    row = result.scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail='Versiyon bulunamadı')
    return _version_out(row)


@router.get('/analytics/agents', response_model=AgentAnalyticsOut)
async def get_agent_analytics(
    persist: bool = True,
    tenant: CurrentTenant = Depends(get_current_tenant),
    db: AsyncSession = Depends(get_db_session),
) -> AgentAnalyticsOut:
    pref_result = await db.execute(select(UserPreference).where(UserPreference.user_id == tenant.user_id))
    pref = pref_result.scalar_one_or_none()
    flows = json.loads(pref.flows_json) if pref and pref.flows_json else []
    agents = json.loads(pref.agents_json) if pref and pref.agents_json else []

    run_result = await db.execute(
        select(FlowRun)
        .where(FlowRun.user_id == tenant.user_id)
        .order_by(FlowRun.started_at.desc())
        .limit(50)
    )
    runs = run_result.scalars().all()
    completed_runs = [r for r in runs if r.status == 'completed']
    success_base = round((len(completed_runs) / len(runs)) * 100) if runs else 0
    total_duration = 0
    duration_count = 0
    for r in runs:
        if r.finished_at and r.started_at:
            total_duration += max(0.0, (r.finished_at - r.started_at).total_seconds())
            duration_count += 1
    avg_run_sec = round(total_duration / duration_count) if duration_count else 45

    all_agent_nodes: list[dict[str, Any]] = []
    for f in flows:
        all_agent_nodes.extend([n for n in f.get('nodes', []) if n.get('type') == 'agent'])
    total_flows = max(1, len(flows))
    total_agent_nodes = max(1, len(all_agent_nodes))

    data: dict[str, Any] = {}
    for ag in agents:
        role = ag.get('role')
        if not role:
            continue
        flow_hit = sum(1 for f in flows if any(n.get('type') == 'agent' and n.get('role') == role for n in f.get('nodes', [])))
        node_hit = sum(1 for n in all_agent_nodes if n.get('role') == role)
        coverage = round((flow_hit / total_flows) * 100)
        activity = round((node_hit / total_agent_nodes) * 100)
        latency = max(1, round(avg_run_sec * (0.8 + max(1, node_hit) / 10)))
        success = max(0, min(100, success_base + round((coverage - 50) / 10)))
        data[str(role)] = {
            'coveragePct': coverage,
            'activityPct': activity,
            'latencySec': latency,
            'successPct': success,
        }

    snapshot_id: int | None = None
    created_at: str | None = None
    if persist:
        snap = AgentAnalyticsSnapshot(
            organization_id=tenant.organization_id,
            user_id=tenant.user_id,
            snapshot_json=json.dumps(data, ensure_ascii=False),
        )
        db.add(snap)
        await db.commit()
        await db.refresh(snap)
        snapshot_id = snap.id
        created_at = snap.created_at.isoformat()
    else:
        last_result = await db.execute(
            select(AgentAnalyticsSnapshot)
            .where(
                AgentAnalyticsSnapshot.organization_id == tenant.organization_id,
                AgentAnalyticsSnapshot.user_id == tenant.user_id,
            )
            .order_by(AgentAnalyticsSnapshot.created_at.desc())
            .limit(1)
        )
        last = last_result.scalar_one_or_none()
        if last:
            snapshot_id = last.id
            created_at = last.created_at.isoformat()

    return AgentAnalyticsOut(snapshot_id=snapshot_id, created_at=created_at, data=data)
