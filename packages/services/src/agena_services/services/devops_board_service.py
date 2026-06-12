"""DevOps Board — Azure work-item-anchored process journey + guarded approvals.

Aggregates, live from Azure DevOps, the continuation of the delivery process
for recent Agena tasks: work item → task → PR → AI review → merge → pipeline
builds → pending deploy approvals.

Design invariants (see plan kind-hugging-falcon):
- Repo mappings come from user_preferences.repo_mappings_json (the RepoMapping
  ORM table is empty on prefs-based installs).
- Build↔PR linkage is COMMIT-SHA based only (lastMergeCommit, then
  lastMergeSourceCommit). Time-window heuristics are forbidden — proximity
  guessing is exactly the class of error behind the rfidapi mis-approval.
- Pipeline approvals are classified KNOWN / FOREIGN / UNRESOLVED; only KNOWN
  ones may be acted on, re-verified server-side at action time.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agena_models.models.task_record import TaskRecord
from agena_models.models.task_review import TaskReview
from agena_models.models.user_preference import UserPreference
from agena_services.integrations.azure_client import AzureDevOpsClient
from agena_services.services.integration_config_service import IntegrationConfigService

logger = logging.getLogger(__name__)

_BUILD_ID_RE = re.compile(r'Build ID:\s*#?(\d+)', re.IGNORECASE)
_EXTERNAL_AZURE_RE = re.compile(r'External Source:\s*Azure\s*#(\d+)', re.IGNORECASE)
_MAPPING_LINE_RE = re.compile(r'^Local Repo Mapping:\s*(.+)$', re.IGNORECASE | re.MULTILINE)

# In-flight request coalescing: two browser tabs polling simultaneously share
# one Azure fan-out instead of doubling the load (mirrors the
# _DORA_SYNC_IN_FLIGHT precedent in routes/analytics.py).
_BOARD_IN_FLIGHT: dict[int, asyncio.Future] = {}


class ApprovalGuardError(Exception):
    """Raised when an approval action targets a non-KNOWN pipeline (→ 403)."""


class ApprovalMismatchError(Exception):
    """Raised when the live build no longer matches what the client saw (→ 409)."""


# ── Prefs-based repo mappings (shared with orchestration) ───────────────────

async def load_prefs_mappings(db: AsyncSession, user_id: int) -> list[dict[str, Any]]:
    """Raw repo-mapping entries from a user's preferences JSON."""
    if not user_id:
        return []
    pref = (await db.execute(
        select(UserPreference).where(UserPreference.user_id == user_id)
    )).scalar_one_or_none()
    if not pref or not pref.repo_mappings_json:
        return []
    try:
        mappings = json.loads(pref.repo_mappings_json)
    except Exception:
        return []
    return [m for m in mappings if isinstance(m, dict)] if isinstance(mappings, list) else []


def find_mapping_by_name(mappings: list[dict[str, Any]], name: str | None) -> dict[str, Any] | None:
    wanted = (name or '').strip()
    if not wanted:
        return None
    return next((m for m in mappings if str(m.get('name') or '').strip() == wanted), None)


def normalize_mapping(entry: dict[str, Any]) -> dict[str, Any] | None:
    """Prefs entry → board-shape mapping {name, project, repo_name, default_branch}."""
    provider = str(entry.get('provider') or '').strip().lower()
    if provider and provider != 'azure':
        return None
    project = str(entry.get('azure_project') or entry.get('owner') or '').strip()
    repo_name = str(entry.get('azure_repo_name') or entry.get('repo_name') or '').strip()
    if not project or not repo_name:
        return None
    return {
        'id': str(entry.get('id') or ''),
        'name': str(entry.get('name') or ''),
        'project': project,
        'repo_name': repo_name,
        'default_branch': str(entry.get('default_branch') or entry.get('base_branch') or '').strip() or 'main',
    }


# ── Approval classification (pure) ──────────────────────────────────────────

def parse_build_id_from_instructions(text: str | None) -> int | None:
    m = _BUILD_ID_RE.search(text or '')
    if not m:
        return None
    try:
        return int(m.group(1))
    except (TypeError, ValueError):
        return None


def classify_build(
    build_meta: dict[str, Any] | None,
    mappings: list[dict[str, Any]],
) -> dict[str, Any]:
    """Classify an approval's underlying build against the known repo set.

    Returns {'classification': 'known'|'foreign'|'unresolved',
             'reason': str, 'known_via': str|None, 'mapping_name': str|None}.
    Repository identity is the primary, deterministic rule; definition-name
    prefix is a fallback when the build payload lacks repository info.
    """
    if not build_meta:
        return {'classification': 'unresolved', 'reason': 'build could not be resolved from the approval',
                'known_via': None, 'mapping_name': None}

    repo_name = str(build_meta.get('repository_name') or '').strip().lower()
    project_name = str(build_meta.get('project_name') or '').strip().lower()
    definition = str(build_meta.get('definition_name') or '').strip()

    for m in mappings:
        m_repo = str(m.get('repo_name') or '').strip().lower()
        m_proj = str(m.get('project') or '').strip().lower()
        if not m_repo:
            continue
        if repo_name and repo_name == m_repo and (not project_name or not m_proj or project_name == m_proj):
            return {'classification': 'known',
                    'reason': f'matched repo: {m.get("repo_name")}',
                    'known_via': 'repository', 'mapping_name': m.get('name')}
    if not repo_name:
        for m in mappings:
            m_repo = str(m.get('repo_name') or '').strip()
            if m_repo and definition.lower().startswith(m_repo.lower()):
                return {'classification': 'known',
                        'reason': f'matched pipeline definition prefix: {definition} ~ {m_repo}',
                        'known_via': 'definition_prefix', 'mapping_name': m.get('name')}

    label = definition or repo_name or 'unknown pipeline'
    return {'classification': 'foreign',
            'reason': f'pipeline "{label}" does not belong to any mapped repo',
            'known_via': None, 'mapping_name': None}


# ── Board assembly ───────────────────────────────────────────────────────────

async def _azure_cfg(db: AsyncSession, organization_id: int) -> dict[str, str] | None:
    config = await IntegrationConfigService(db).get_config(organization_id, 'azure')
    if not config or not config.secret or not config.base_url:
        return None
    return {'org_url': config.base_url.rstrip('/'), 'pat': config.secret}


def _resolve_work_item_id(task: TaskRecord) -> str | None:
    if (task.source or '') == 'azure' and (task.external_id or '').strip().isdigit():
        return task.external_id.strip()
    wid = (getattr(task, 'external_work_item_id', None) or '').strip()
    if wid.isdigit():
        return wid
    m = _EXTERNAL_AZURE_RE.search(task.description or '')
    return m.group(1) if m else None


def _resolve_mapping_name(task: TaskRecord) -> str | None:
    m = _MAPPING_LINE_RE.search(task.description or '')
    return m.group(1).strip() if m else None


def _match_builds_to_pr(pr: dict[str, Any] | None, builds: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """SHA-join: build.source_version vs PR merge commits. No heuristics."""
    if not pr:
        return []
    merge_sha = (pr.get('last_merge_commit') or '').strip().lower()
    source_sha = (pr.get('last_merge_source_commit') or '').strip().lower()
    matched: list[dict[str, Any]] = []
    for b in builds:
        sv = str(b.get('source_version') or '').strip().lower()
        if not sv:
            continue
        if merge_sha and sv == merge_sha:
            matched.append({**b, 'matched_via': 'merge_commit'})
        elif source_sha and sv == source_sha:
            matched.append({**b, 'matched_via': 'pr_validation'})
    return matched


async def build_board(
    db: AsyncSession,
    *,
    organization_id: int,
    user_id: int,
    days: int = 14,
) -> dict[str, Any]:
    """Coalesced entrypoint — concurrent callers share one assembly."""
    fut = _BOARD_IN_FLIGHT.get(organization_id)
    if fut is not None and not fut.done():
        return await asyncio.shield(fut)
    loop = asyncio.get_running_loop()
    fut = loop.create_future()
    _BOARD_IN_FLIGHT[organization_id] = fut
    try:
        result = await _build_board_inner(db, organization_id=organization_id, user_id=user_id, days=days)
        if not fut.done():
            fut.set_result(result)
        return result
    except Exception as exc:
        if not fut.done():
            fut.set_exception(exc)
        raise
    finally:
        if _BOARD_IN_FLIGHT.get(organization_id) is fut:
            _BOARD_IN_FLIGHT.pop(organization_id, None)


async def _build_board_inner(
    db: AsyncSession,
    *,
    organization_id: int,
    user_id: int,
    days: int,
) -> dict[str, Any]:
    errors: list[dict[str, str]] = []
    cfg = await _azure_cfg(db, organization_id)
    if cfg is None:
        return {'mappings': [], 'journeys': [], 'pipelines': [], 'approvals': [],
                'errors': [{'section': 'config', 'error': 'Azure integration not configured'}]}

    cutoff = datetime.now(timezone.utc) - timedelta(days=max(1, min(days, 90)))
    tasks = list((await db.execute(
        select(TaskRecord)
        .where(
            TaskRecord.organization_id == organization_id,
            TaskRecord.updated_at >= cutoff.replace(tzinfo=None),
        )
        .order_by(TaskRecord.updated_at.desc())
        .limit(60)
    )).scalars().all())
    # Keep only tasks that resolve to an Azure work item; cap at 20.
    journable: list[tuple[TaskRecord, str]] = []
    for t in tasks:
        wid = _resolve_work_item_id(t)
        if wid:
            journable.append((t, wid))
        if len(journable) >= 20:
            break

    # ── Known repo set: requesting user's prefs + every task creator's prefs.
    mapping_index: dict[tuple[int, str], dict[str, Any]] = {}
    known_mappings: dict[str, dict[str, Any]] = {}

    async def _load_user(uid: int) -> None:
        for entry in await load_prefs_mappings(db, uid):
            norm = normalize_mapping(entry)
            if norm is None:
                continue
            mapping_index[(uid, norm['name'])] = norm
            known_mappings.setdefault(norm['name'], norm)

    creator_ids = {user_id} | {int(t.created_by_user_id or 0) for t, _ in journable}
    for uid in sorted(i for i in creator_ids if i):
        await _load_user(uid)
    mappings = list(known_mappings.values())[:10]

    # ── Latest review per task (single IN query).
    reviews_by_task: dict[int, dict[str, Any]] = {}
    task_ids = [t.id for t, _ in journable]
    if task_ids:
        rows = (await db.execute(
            select(TaskReview)
            .where(TaskReview.task_id.in_(task_ids), TaskReview.organization_id == organization_id)
            .order_by(TaskReview.id.desc())
        )).scalars().all()
        for r in rows:
            reviews_by_task.setdefault(int(r.task_id), {
                'id': r.id, 'score': r.score, 'severity': r.severity,
                'status': r.status, 'reviewer': r.reviewer_agent_role,
            })

    client = AzureDevOpsClient()
    from agena_services.services.azure_pr_service import AzurePRService
    pr_svc = AzurePRService(db)

    # ── Live Azure fan-out.
    projects = sorted({m['project'] for m in mappings})

    async def _builds_for(mapping: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
        return mapping['name'], await client.list_builds(cfg=cfg, project=mapping['project'], top=25)

    async def _approvals_for(project: str) -> tuple[str, list[dict[str, Any]]]:
        return project, await client.list_pending_approvals(cfg=cfg, project=project)

    sem = asyncio.Semaphore(8)

    async def _pr_for(task: TaskRecord) -> tuple[int, dict[str, Any] | None]:
        ref = pr_svc._parse_pr_ref(task.pr_url or '')
        if ref is None:
            return task.id, None
        project, repo, pr_id = ref
        async with sem:
            return task.id, await client.get_pull_request(cfg=cfg, project=project, repo=repo, pr_id=pr_id)

    wi_project = mappings[0]['project'] if mappings else (projects[0] if projects else '')
    wi_ids = sorted({wid for _, wid in journable})

    build_results, approval_results, pr_results, wi_map = await asyncio.gather(
        asyncio.gather(*(_builds_for(m) for m in mappings), return_exceptions=True),
        asyncio.gather(*(_approvals_for(p) for p in projects), return_exceptions=True),
        asyncio.gather(*(_pr_for(t) for t, _ in journable if (t.pr_url or '').strip()), return_exceptions=True),
        client.get_work_items_batch(cfg=cfg, project=wi_project, ids=wi_ids) if wi_ids else asyncio.sleep(0, result={}),
        return_exceptions=False,
    )

    pipelines: list[dict[str, Any]] = []
    builds_by_mapping: dict[str, list[dict[str, Any]]] = {}
    for item in build_results:
        if isinstance(item, Exception):
            errors.append({'section': 'builds', 'error': str(item)[:200]})
            continue
        name, builds = item
        builds_by_mapping[name] = builds
        mapping = known_mappings.get(name) or {}
        pipelines.append({'mapping_name': name, 'project': mapping.get('project', ''), 'builds': builds})

    raw_approvals: list[tuple[str, dict[str, Any]]] = []
    for item in approval_results:
        if isinstance(item, Exception):
            errors.append({'section': 'approvals', 'error': str(item)[:200]})
            continue
        project, rows = item
        raw_approvals.extend((project, a) for a in rows)

    prs_by_task: dict[int, dict[str, Any] | None] = {}
    for item in pr_results:
        if isinstance(item, Exception):
            errors.append({'section': 'prs', 'error': str(item)[:200]})
            continue
        tid, pr = item
        prs_by_task[tid] = pr

    if isinstance(wi_map, Exception):
        errors.append({'section': 'work_items', 'error': str(wi_map)[:200]})
        wi_map = {}

    # ── Approval enrichment + classification.
    async def _enrich(project: str, approval: dict[str, Any]) -> dict[str, Any]:
        build_id = parse_build_id_from_instructions(approval.get('instructions'))
        build = None
        if build_id:
            async with sem:
                build = await client.get_build(cfg=cfg, project=project, build_id=build_id)
        cls = classify_build(build, mappings)
        instructions = str(approval.get('instructions') or '')
        return {
            'id': approval.get('id'),
            'project': project,
            'created_on': approval.get('created_on'),
            'instructions_summary': instructions[:280],
            'build': build,
            'expected_build_id': build_id,
            **cls,
        }

    # Busy orgs accumulate hundreds of stale pending approvals (250 observed
    # live). Enriching them all means hundreds of get_build calls per poll —
    # slow and an Azure-throttling hazard. Enrich only the newest 30; report
    # the total so the UI can say "+N older".
    approvals_total = len(raw_approvals)
    raw_approvals.sort(key=lambda pa: str(pa[1].get('created_on') or ''), reverse=True)
    to_enrich = raw_approvals[:30]

    enriched_approvals: list[dict[str, Any]] = []
    if to_enrich:
        enr = await asyncio.gather(*(_enrich(p, a) for p, a in to_enrich), return_exceptions=True)
        for item in enr:
            if isinstance(item, Exception):
                errors.append({'section': 'approvals', 'error': str(item)[:200]})
            else:
                enriched_approvals.append(item)
    # Actionable (known) first; newest first within each class.
    _cls_rank = {'known': 0, 'foreign': 1, 'unresolved': 2}
    enriched_approvals.sort(
        key=lambda a: (_cls_rank.get(a.get('classification'), 9), str(a.get('created_on') or '')),
    )
    enriched_approvals = (
        sorted([a for a in enriched_approvals if a['classification'] == 'known'],
               key=lambda a: str(a.get('created_on') or ''), reverse=True)
        + sorted([a for a in enriched_approvals if a['classification'] != 'known'],
                 key=lambda a: str(a.get('created_on') or ''), reverse=True)
    )

    # ── Journey rows.
    journeys: list[dict[str, Any]] = []
    for task, wid in journable:
        mapping_name = _resolve_mapping_name(task)
        mapping = None
        if mapping_name:
            uid = int(task.created_by_user_id or 0)
            mapping = mapping_index.get((uid, mapping_name)) or known_mappings.get(mapping_name)
        pr = prs_by_task.get(task.id)
        candidate_builds = builds_by_mapping.get(mapping['name'], []) if mapping else [
            b for blist in builds_by_mapping.values() for b in blist
        ]
        journeys.append({
            'task_id': task.id,
            'task_title': task.title,
            'task_status': task.status,
            'task_substatus': getattr(task, 'substatus', None),
            'updated_at': task.updated_at.isoformat() if task.updated_at else None,
            'mapping_name': mapping['name'] if mapping else mapping_name,
            'work_item': (wi_map or {}).get(wid) or {'id': wid, 'title': '', 'state': '', 'type': '', 'url': ''},
            'pr': pr,
            'review': reviews_by_task.get(task.id),
            'builds': _match_builds_to_pr(pr, candidate_builds),
        })

    return {
        'mappings': mappings,
        'journeys': journeys,
        'pipelines': pipelines,
        'approvals': enriched_approvals,
        'approvals_total': approvals_total,
        'errors': errors,
    }


# ── Guarded approval action ──────────────────────────────────────────────────

async def act_on_approval(
    db: AsyncSession,
    *,
    organization_id: int,
    user_id: int,
    approval_id: str,
    project: str,
    action: str,  # 'approved' | 'rejected'
    expected_build_id: int,
    comment: str = '',
) -> dict[str, Any]:
    """Approve/reject a pipeline approval with server-side guardrails.

    The enrichment + classification is RE-RUN here at action time — the
    client's view is never trusted. Raises ApprovalGuardError (→403) for
    FOREIGN/UNRESOLVED, ApprovalMismatchError (→409) when the freshly
    resolved build differs from what the client rendered, ValueError (→404)
    when the approval is no longer pending.
    """
    cfg = await _azure_cfg(db, organization_id)
    if cfg is None:
        raise ApprovalGuardError('Azure integration not configured')

    mappings = [n for n in (normalize_mapping(e) for e in await load_prefs_mappings(db, user_id)) if n]
    if not mappings:
        raise ApprovalGuardError('No repo mappings configured — nothing is approvable from the board')

    client = AzureDevOpsClient()
    pending = await client.list_pending_approvals(cfg=cfg, project=project)
    approval = next((a for a in pending if str(a.get('id')) == str(approval_id)), None)
    if approval is None:
        raise ValueError('Approval is no longer pending (already acted on or expired)')

    build_id = parse_build_id_from_instructions(approval.get('instructions'))
    build = await client.get_build(cfg=cfg, project=project, build_id=build_id) if build_id else None
    cls = classify_build(build, mappings)
    if cls['classification'] != 'known':
        logger.warning('Approval %s blocked: %s (%s)', approval_id, cls['classification'], cls['reason'])
        raise ApprovalGuardError(f"{cls['classification']}: {cls['reason']}")
    if not build_id or int(expected_build_id or 0) != int(build_id):
        raise ApprovalMismatchError(
            f'Approval now resolves to build {build_id}, client expected {expected_build_id} — refresh the board'
        )

    result = await client.patch_approval(
        cfg=cfg, project=project, approval_id=str(approval_id),
        status=action, comment=comment or f'{action} via Agena DevOps Board',
    )
    logger.info('Approval %s %s by user %s (build %s, %s)', approval_id, action, user_id, build_id, cls['reason'])
    return {**result, 'build_id': build_id, 'classification': cls['classification'], 'reason': cls['reason']}
