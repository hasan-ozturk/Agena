"""Derived delivery stage — a single high-level phase for a delivery run.

Pure function (no IO) so it can be reused by the Tasks feed (DB-only signals,
batched by the caller) and the per-run endpoint (which also feeds live Azure
signals — PR merge / build / pipeline approval). The feed only passes DB
signals, so the live-only phases (merging/building/deployed) simply never
appear there; everything degrades gracefully.

Phase keys map 1:1 to i18n keys `runStatus.<phase>` in all 7 locales.
"""
from __future__ import annotations

# Ordered set of phases (also the i18n key suffixes under `runStatus.`).
PHASES = (
    'queued', 'analyzing', 'developing', 'prOpen', 'inReview',
    'awaitingApproval', 'merging', 'merged', 'building', 'deployed',
    'completed', 'failed', 'blocked', 'rejected', 'new',
)


def _k(phase: str) -> tuple[str, str]:
    return (phase, f'runStatus.{phase}')


def compute_delivery_stage(
    *,
    task_status: str | None = None,
    task_substatus: str | None = None,
    pr_url: str | None = None,
    review_status: str | None = None,
    review_severity: str | None = None,
    flow_run_status: str | None = None,
    # Live (Azure) signals — only the per-run endpoint passes these; the feed
    # leaves them None so these branches are skipped there.
    pr_merge_status: str | None = None,
    pr_status: str | None = None,
    build_result: str | None = None,
    build_status: str | None = None,
    approval_pending: bool = False,
) -> tuple[str, str]:
    """Return (phase_key, i18n_label_key). First match wins; terminal / failure /
    gate states take precedence over in-flight ones."""
    ts = (task_status or '').strip().lower()
    fr = (flow_run_status or '').strip().lower()
    rev_sev = (review_severity or '').strip().lower()
    rev_st = (review_status or '').strip().lower()
    has_pr = bool(pr_url)
    merged = (pr_merge_status or '').strip().lower() in ('succeeded', 'completed') \
        or (pr_status or '').strip().lower() == 'completed'

    # ── terminal / failure / gate first ──────────────────────────────────
    if ts == 'failed' or fr == 'failed':
        return _k('failed')
    if fr == 'rejected':
        return _k('rejected')
    if fr == 'pending_approval' or ts == 'waiting_approval' or approval_pending:
        return _k('awaitingApproval')
    if rev_sev == 'critical' and ts not in ('completed', 'merged') and not merged:
        return _k('blocked')

    # ── live deploy phases (Azure-only signals; absent on the feed) ───────
    bres = (build_result or '').strip().lower()
    bstat = (build_status or '').strip().lower()
    if bstat in ('inprogress', 'running', 'notstarted', 'postponed'):
        return _k('building')
    if bres in ('succeeded', 'partiallysucceeded'):
        return _k('deployed')

    # ── completed states ──────────────────────────────────────────────────
    if ts in ('completed', 'merged') or merged:
        if has_pr or merged:
            return _k('merged')
        # completed without a PR = a question/answer or no-change task
        return _k('completed')

    # ── in-flight ───────────────────────────────────────────────────────
    if has_pr:
        if rev_st in ('running', 'pending', 'completed'):
            return _k('inReview')
        return _k('prOpen')
    if fr in ('running', 'resuming'):
        return _k('developing')
    if ts == 'running':
        return _k('analyzing')
    if ts == 'queued' or fr in ('queued', 'pending'):
        return _k('queued')
    if ts == 'new':
        return _k('new')

    # Safe fallback: map a known task status straight through, else queued.
    if ts in PHASES:
        return _k(ts)
    return _k('queued')
