"""DevOps Board endpoints — Azure-side process tracking + guarded approvals.

Reads are live aggregations over Azure DevOps (no sync tables). Approval
actions re-verify the pipeline classification server-side at action time:
only KNOWN (mapped-repo) pipelines are approvable; FOREIGN/UNRESOLVED → 403,
stale build view → 409. See devops_board_service for the rules.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from agena_api.api.dependencies import CurrentTenant, require_permission
from agena_core.database import get_db_session
from agena_services.services.devops_board_service import (
    ApprovalGuardError,
    ApprovalMismatchError,
    act_on_approval,
    build_board,
    list_pipelines_for_mapping,
)

router = APIRouter(prefix='/devops', tags=['devops'])


class ApprovalActionRequest(BaseModel):
    project: str
    expected_build_id: int
    comment: str = ''


class ApprovalActionResponse(BaseModel):
    id: str
    status: str
    build_id: int
    classification: str
    reason: str


@router.get('/board')
async def get_board(
    days: int = Query(default=14, ge=1, le=90),
    tenant: CurrentTenant = Depends(require_permission('tasks:read')),
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    return await build_board(
        db,
        organization_id=tenant.organization_id,
        user_id=tenant.user_id,
        days=days,
    )


@router.get('/repo-pipelines')
async def get_repo_pipelines(
    mapping_name: str = Query(default=''),
    project: str = Query(default=''),
    repo_name: str = Query(default=''),
    tenant: CurrentTenant = Depends(require_permission('tasks:read')),
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """Pipeline options for the mapping editor combobox (repo-bound + all)."""
    return await list_pipelines_for_mapping(
        db,
        organization_id=tenant.organization_id,
        user_id=tenant.user_id,
        mapping_name=mapping_name,
        project=project,
        repo_name=repo_name,
    )


async def _approval_action(
    approval_id: str,
    body: ApprovalActionRequest,
    tenant: CurrentTenant,
    db: AsyncSession,
    action: str,
) -> ApprovalActionResponse:
    try:
        result = await act_on_approval(
            db,
            organization_id=tenant.organization_id,
            user_id=tenant.user_id,
            approval_id=approval_id,
            project=body.project,
            action=action,
            expected_build_id=body.expected_build_id,
            comment=body.comment,
        )
    except ApprovalGuardError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ApprovalMismatchError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return ApprovalActionResponse(
        id=str(result.get('id') or approval_id),
        status=str(result.get('status') or ''),
        build_id=int(result.get('build_id') or 0),
        classification=str(result.get('classification') or ''),
        reason=str(result.get('reason') or ''),
    )


@router.post('/approvals/{approval_id}/approve', response_model=ApprovalActionResponse)
async def approve_pipeline_approval(
    approval_id: str,
    body: ApprovalActionRequest,
    tenant: CurrentTenant = Depends(require_permission('tasks:write')),
    db: AsyncSession = Depends(get_db_session),
) -> ApprovalActionResponse:
    return await _approval_action(approval_id, body, tenant, db, 'approved')


@router.post('/approvals/{approval_id}/reject', response_model=ApprovalActionResponse)
async def reject_pipeline_approval(
    approval_id: str,
    body: ApprovalActionRequest,
    tenant: CurrentTenant = Depends(require_permission('tasks:write')),
    db: AsyncSession = Depends(get_db_session),
) -> ApprovalActionResponse:
    return await _approval_action(approval_id, body, tenant, db, 'rejected')
