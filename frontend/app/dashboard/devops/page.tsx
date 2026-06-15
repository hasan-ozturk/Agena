'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { apiFetch } from '@/lib/api';
import { useLocale, type TranslationKey } from '@/lib/i18n';
import NavIcon from '@/components/NavIcon';

// ── Types (mirror devops_board_service response) ─────────────────────────────
type Build = {
  id: number; definition_name: string; source_branch: string; source_version: string;
  status: string; result: string; queue_time: string; finish_time: string;
  web_url: string; repository_name: string; project_name: string;
  matched_via?: string;
};
type Journey = {
  task_id: number; task_title: string; task_status: string; task_substatus: string | null;
  updated_at: string | null; mapping_name: string | null;
  work_item: { id: string; title: string; state: string; type: string; url: string };
  pr: {
    id: string; title: string; status: string; merge_status: string; source_branch: string;
    target_branch: string; closed_date: string; url: string;
  } | null;
  review: { id: number; score: number | null; severity: string | null; status: string; reviewer: string } | null;
  builds: Build[];
};
type Approval = {
  id: string; project: string; created_on: string; instructions_summary: string;
  build: Build | null; expected_build_id: number | null;
  classification: 'known' | 'foreign' | 'unresolved'; reason: string;
  known_via: string | null; mapping_name: string | null;
};
type Board = {
  mappings: { id: string; name: string; project: string; repo_name: string; default_branch: string }[];
  journeys: Journey[];
  pipelines: { mapping_name: string; project: string; builds: Build[] }[];
  approvals: Approval[];
  approvals_total?: number;
  errors: { section: string; error: string }[];
};

const card: React.CSSProperties = { borderRadius: 10, border: '1px solid var(--panel-border)', background: 'var(--panel)' };

const TASK_COLOR: Record<string, string> = {
  completed: '#3f9d6a', running: '#5b9bd5', queued: '#c98a2b',
  waiting_approval: '#c98a2b', failed: '#cf5b57', cancelled: '#94a3b8', new: '#94a3b8',
};
const PR_COLOR: Record<string, string> = { active: '#3f9d6a', completed: 'var(--acc)', abandoned: '#94a3b8' };

function buildColor(b: Build): string {
  if (b.status === 'inProgress' || b.status === 'notStarted') return '#5b9bd5';
  if (b.result === 'succeeded') return '#3f9d6a';
  if (b.result === 'partiallySucceeded') return '#c98a2b';
  if (b.result === 'failed') return '#cf5b57';
  return '#94a3b8';
}
function sevColor(s: string | null): string {
  return ({ critical: '#cf5b57', high: '#c98a2b', medium: '#c98a2b', low: '#3f9d6a', clean: '#3f9d6a' } as Record<string, string>)[(s || '').toLowerCase()] || 'var(--ink-50)';
}
const CLS_COLOR: Record<string, string> = { known: '#3f9d6a', foreign: '#cf5b57', unresolved: '#94a3b8' };

function Chip({ color, children, hollow, href, title }: {
  color: string; children: React.ReactNode; hollow?: boolean; href?: string; title?: string;
}) {
  const style: React.CSSProperties = {
    display: 'inline-flex', alignItems: 'center', gap: 4, fontSize: 11, fontWeight: 700,
    padding: '3px 9px', borderRadius: 999, whiteSpace: 'nowrap',
    background: hollow ? 'transparent' : `${color}18`,
    color: hollow ? 'var(--ink-30)' : color,
    border: `1px solid ${hollow ? 'var(--panel-border-2)' : color + '40'}`,
    textDecoration: 'none',
  };
  if (href) {
    return <a href={href} target={href.startsWith('/') ? undefined : '_blank'} rel='noreferrer' style={style} title={title}>{children}</a>;
  }
  return <span style={style} title={title}>{children}</span>;
}

function Arrow() {
  return <span style={{ color: 'var(--ink-25)', fontSize: 11 }}>→</span>;
}

export default function DevOpsBoardPage() {
  const { t } = useLocale();
  const tt = useCallback((k: string) => t(k as TranslationKey), [t]);
  const [board, setBoard] = useState<Board | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<string>('all');
  const [confirmTarget, setConfirmTarget] = useState<{ approval: Approval; action: 'approve' | 'reject' } | null>(null);
  const [actionBusy, setActionBusy] = useState(false);
  const [actionError, setActionError] = useState('');
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const load = useCallback(async () => {
    try {
      const data = await apiFetch<Board>('/devops/board?days=14');
      setBoard(data);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  // Adaptive polling: only while something is actually moving or waiting.
  useEffect(() => {
    const active = Boolean(board && (
      board.approvals.length > 0
      || board.pipelines.some((p) => p.builds.some((b) => b.status === 'inProgress' || b.status === 'notStarted'))
      || board.journeys.some((j) => ['running', 'queued', 'waiting_approval'].includes(j.task_status))
    ));
    if (active && !pollRef.current) {
      pollRef.current = setInterval(() => void load(), 10000);
    } else if (!active && pollRef.current) {
      clearInterval(pollRef.current); pollRef.current = null;
    }
    return () => { if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; } };
  }, [board, load]);

  async function runApprovalAction() {
    if (!confirmTarget) return;
    const { approval, action } = confirmTarget;
    setActionBusy(true); setActionError('');
    try {
      await apiFetch(`/devops/approvals/${encodeURIComponent(approval.id)}/${action}`, {
        method: 'POST',
        body: JSON.stringify({
          project: approval.project,
          expected_build_id: approval.expected_build_id ?? 0,
          comment: '',
        }),
      });
      setConfirmTarget(null);
      void load();
    } catch (e) {
      setActionError(e instanceof Error ? e.message : String(e));
    } finally {
      setActionBusy(false);
    }
  }

  const journeys = (board?.journeys || []).filter((j) => filter === 'all' || j.mapping_name === filter);
  const pipelines = (board?.pipelines || []).filter((p) => filter === 'all' || p.mapping_name === filter);

  return (
    <div style={{ display: 'grid', gap: 16, maxWidth: 1180 }}>
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap' }}>
        <div>
          <div style={{ fontSize: 10, fontWeight: 800, color: 'var(--muted)', letterSpacing: 1.5, textTransform: 'uppercase', marginBottom: 6 }}>
            {tt('devops.eyebrow')}
          </div>
          <h1 style={{ fontSize: 22, fontWeight: 700, color: 'var(--ink-90)', display: 'flex', alignItems: 'center', gap: 8 }}>
            <NavIcon name='activity' size={20} /> {tt('devops.title')}
          </h1>
          <p style={{ fontSize: 13, color: 'var(--ink-58)', marginTop: 4 }}>{tt('devops.subtitle')}</p>
        </div>
        <button onClick={() => { setLoading(true); void load(); }}
          style={{ padding: '8px 14px', borderRadius: 8, border: '1px solid var(--panel-border-3)', background: 'var(--glass)', color: 'var(--ink-58)', cursor: 'pointer', fontSize: 12, fontWeight: 600, display: 'flex', alignItems: 'center', gap: 6 }}>
          <NavIcon name='activity' size={13} /> {tt('devops.refresh')}
        </button>
      </div>

      {/* Section errors (degraded, not fatal) */}
      {board && board.errors.length > 0 && (
        <div style={{ ...card, borderColor: '#c98a2b55', background: '#c98a2b10', padding: '10px 14px', fontSize: 12, color: '#c98a2b' }}>
          {board.errors.map((e, i) => <div key={i}>{e.section}: {e.error}</div>)}
        </div>
      )}
      {error && (
        <div style={{ ...card, borderColor: '#cf5b5755', background: '#cf5b5710', padding: '10px 14px', fontSize: 12, color: '#cf5b57' }}>{error}</div>
      )}
      {loading && !board && <div style={{ padding: 24, color: 'var(--ink-58)', fontSize: 14 }}>{tt('devops.loading')}</div>}

      {board && (
        <>
          {/* Mapping filter chips */}
          {board.mappings.length > 0 && (
            <div style={{ display: 'flex', gap: 4, padding: 4, background: 'var(--panel)', border: '1px solid var(--panel-border)', borderRadius: 10, width: 'fit-content', flexWrap: 'wrap' }}>
              {['all', ...board.mappings.map((m) => m.name)].map((name) => (
                <button key={name} onClick={() => setFilter(name)}
                  style={{ padding: '6px 12px', borderRadius: 8, border: 'none', fontSize: 12, fontWeight: 700, cursor: 'pointer', background: filter === name ? 'var(--acc-soft)' : 'transparent', color: filter === name ? 'var(--acc)' : 'var(--ink-50)' }}>
                  {name === 'all' ? tt('devops.allRepos') : name}
                </button>
              ))}
            </div>
          )}

          {/* ── JOURNEY ──────────────────────────────────────────────────── */}
          <section style={{ display: 'grid', gap: 8 }}>
            <div className='section-label'>{tt('devops.journeyTitle')}</div>
            {journeys.length === 0 && (
              <div style={{ ...card, padding: 20, textAlign: 'center', color: 'var(--ink-30)', fontSize: 13 }}>{tt('devops.noJourneys')}</div>
            )}
            {journeys.map((j) => {
              const taskColor = TASK_COLOR[j.task_status] || 'var(--ink-50)';
              const prColor = j.pr ? (PR_COLOR[j.pr.status] || '#94a3b8') : '';
              const merged = j.pr?.status === 'completed';
              return (
                <article key={j.task_id} style={{ ...card, padding: 14, display: 'grid', gap: 10, borderLeft: `4px solid ${taskColor}` }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap', justifyContent: 'space-between' }}>
                    <div style={{ fontSize: 13, fontWeight: 700, color: 'var(--ink-90)', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                      {j.work_item.title || j.task_title}
                    </div>
                    {j.mapping_name && <span style={{ fontSize: 10, color: 'var(--ink-35)', fontFamily: 'monospace' }}>{j.mapping_name}</span>}
                  </div>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
                    <Chip color='#5b9bd5' href={j.work_item.url || undefined} title={j.work_item.type}>
                      WI #{j.work_item.id}{j.work_item.state ? ` · ${j.work_item.state}` : ''} {j.work_item.url ? '↗' : ''}
                    </Chip>
                    <Arrow />
                    <Chip color={taskColor} href={`/dashboard/tasks/${j.task_id}?tab=pipeline`} title={j.task_title}>
                      {tt('devops.chipTask')} #{j.task_id} · {j.task_status}
                    </Chip>
                    <Arrow />
                    {j.pr ? (
                      <Chip color={prColor} href={j.pr.url} title={j.pr.title}>
                        PR #{j.pr.id} · {j.pr.status} ↗
                      </Chip>
                    ) : <Chip color='' hollow>{tt('devops.chipNoPr')}</Chip>}
                    <Arrow />
                    {j.review ? (
                      <Chip color={sevColor(j.review.severity)} title={j.review.reviewer}>
                        {tt('devops.chipReview')} {j.review.score ?? '–'}{j.review.severity ? ` · ${j.review.severity}` : ''}
                      </Chip>
                    ) : <Chip color='' hollow>{tt('devops.chipNoReview')}</Chip>}
                    <Arrow />
                    {merged ? (
                      <Chip color='var(--acc)' title={j.pr?.closed_date}>{tt('devops.chipMerged')}</Chip>
                    ) : <Chip color='' hollow>{tt('devops.chipNotMerged')}</Chip>}
                    <Arrow />
                    {j.builds.length > 0 ? j.builds.slice(0, 4).map((b) => (
                      <Chip key={b.id} color={buildColor(b)} href={b.web_url || undefined}
                        title={`${b.matched_via === 'pr_validation' ? 'PR validation' : 'merge commit'} · ${b.source_branch}`}>
                        {b.definition_name} · {b.status === 'inProgress' ? tt('devops.buildRunning') : (b.result || b.status)} ↗
                      </Chip>
                    )) : <Chip color='' hollow>{tt('devops.noLinkedBuild')}</Chip>}
                  </div>
                </article>
              );
            })}
          </section>

          {/* ── LIVE PIPELINES ───────────────────────────────────────────── */}
          <section style={{ display: 'grid', gap: 8 }}>
            <div className='section-label'>{tt('devops.pipelinesTitle')}</div>
            {pipelines.length === 0 && (
              <div style={{ ...card, padding: 20, textAlign: 'center', color: 'var(--ink-30)', fontSize: 13 }}>{tt('devops.noBuilds')}</div>
            )}
            {pipelines.map((p) => (
              <div key={p.mapping_name} style={{ ...card, padding: 0, overflow: 'hidden' }}>
                <div style={{ padding: '10px 14px', borderBottom: '1px solid var(--panel-border)', fontSize: 12, fontWeight: 700, color: 'var(--ink-78)' }}>
                  {p.mapping_name} <span style={{ color: 'var(--ink-35)', fontWeight: 400 }}>· {p.project}</span>
                </div>
                {p.builds.slice(0, 8).map((b) => {
                  const c = buildColor(b);
                  return (
                    <div key={b.id} style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '9px 14px', borderBottom: '1px solid var(--panel-alt)', fontSize: 12 }}>
                      <span style={{ width: 8, height: 8, borderRadius: '50%', background: c, flexShrink: 0 }} />
                      <span style={{ fontWeight: 600, color: 'var(--ink-78)', minWidth: 140 }}>{b.definition_name}</span>
                      <span style={{ color: 'var(--ink-42)', fontFamily: 'monospace', fontSize: 11 }}>{b.source_branch}</span>
                      <span style={{ marginLeft: 'auto', color: c, fontWeight: 700, fontSize: 11 }}>
                        {b.status === 'inProgress' ? tt('devops.buildRunning') : (b.result || b.status)}
                      </span>
                      {b.web_url && (
                        <a href={b.web_url} target='_blank' rel='noreferrer' style={{ color: 'var(--acc)', fontSize: 11, textDecoration: 'none' }}>#{b.id} ↗</a>
                      )}
                    </div>
                  );
                })}
              </div>
            ))}
          </section>

          {/* ── PENDING APPROVALS ────────────────────────────────────────── */}
          <section style={{ display: 'grid', gap: 8 }}>
            <div className='section-label' style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              {tt('devops.approvalsTitle')}
              {(board.approvals_total ?? 0) > board.approvals.length && (
                <span style={{ fontSize: 10, fontWeight: 600, color: 'var(--ink-35)' }}>
                  ({board.approvals.length} / {board.approvals_total})
                </span>
              )}
            </div>
            {board.approvals.length === 0 && (
              <div style={{ ...card, padding: 20, textAlign: 'center', color: 'var(--ink-30)', fontSize: 13 }}>{tt('devops.noApprovals')}</div>
            )}
            {/* Actionable (known) approvals — always expanded */}
            {board.approvals.filter((a) => a.classification === 'known').map((a) => {
              const c = CLS_COLOR[a.classification] || 'var(--ink-50)';
              const known = a.classification === 'known';
              return (
                <article key={a.id} style={{ ...card, padding: 14, display: 'grid', gap: 8, borderLeft: `4px solid ${c}` }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                    <span style={{ fontSize: 11, padding: '2px 8px', borderRadius: 999, background: `${c}22`, color: c, fontWeight: 700, textTransform: 'uppercase' }}>
                      {known ? '' : '🔒 '}{tt(`devops.cls.${a.classification}`)}
                    </span>
                    <span style={{ fontSize: 11, color: 'var(--ink-42)' }}>{a.reason}</span>
                    {a.build && (
                      <a href={a.build.web_url || '#'} target='_blank' rel='noreferrer' style={{ marginLeft: 'auto', fontSize: 11, color: 'var(--acc)', textDecoration: 'none' }}>
                        {a.build.definition_name} · build #{a.build.id} ↗
                      </a>
                    )}
                  </div>
                  <div style={{ fontSize: 12, color: 'var(--ink-58)', whiteSpace: 'pre-wrap', maxHeight: 72, overflow: 'hidden' }}>
                    {a.instructions_summary}
                  </div>
                  {known && (
                    <div style={{ display: 'flex', gap: 8 }}>
                      <button onClick={() => { setActionError(''); setConfirmTarget({ approval: a, action: 'approve' }); }}
                        style={{ padding: '7px 16px', borderRadius: 8, border: 'none', background: '#3f9d6a', color: '#fff', fontWeight: 700, fontSize: 12, cursor: 'pointer' }}>
                        ✓ {tt('devops.approve')}
                      </button>
                      <button onClick={() => { setActionError(''); setConfirmTarget({ approval: a, action: 'reject' }); }}
                        style={{ padding: '7px 16px', borderRadius: 8, border: '1px solid #cf5b5755', background: 'transparent', color: '#cf5b57', fontWeight: 700, fontSize: 12, cursor: 'pointer' }}>
                        ✕ {tt('devops.reject')}
                      </button>
                    </div>
                  )}
                  {!known && (
                    <div style={{ fontSize: 11, color: 'var(--ink-35)' }}>{tt('devops.lockedHint')}</div>
                  )}
                </article>
              );
            })}
            {/* Foreign / unresolved — visible but collapsed and locked.
                Visibility is the anti-incident feature; hiding them recreates
                "where is my approval?" confusion, but a busy org has hundreds. */}
            {(() => {
              const locked = board.approvals.filter((a) => a.classification !== 'known');
              if (locked.length === 0) return null;
              return (
                <details style={{ ...card, padding: '10px 14px' }}>
                  <summary style={{ cursor: 'pointer', fontSize: 12, fontWeight: 700, color: 'var(--ink-50)' }}>
                    🔒 {tt('devops.cls.foreign')} / {tt('devops.cls.unresolved')} ({locked.length})
                  </summary>
                  <div style={{ display: 'grid', gap: 6, marginTop: 10 }}>
                    {locked.map((a) => {
                      const c = CLS_COLOR[a.classification] || 'var(--ink-50)';
                      return (
                        <div key={a.id} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '7px 10px', borderRadius: 8, border: `1px solid ${c}25`, background: `${c}06`, fontSize: 11 }}>
                          <span style={{ padding: '1px 7px', borderRadius: 999, background: `${c}22`, color: c, fontWeight: 700, textTransform: 'uppercase', flexShrink: 0 }}>
                            {tt(`devops.cls.${a.classification}`)}
                          </span>
                          <span style={{ color: 'var(--ink-58)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                            {a.build?.definition_name || a.reason}
                          </span>
                          <span style={{ marginLeft: 'auto', color: 'var(--ink-30)', flexShrink: 0 }}>{(a.created_on || '').slice(0, 10)}</span>
                        </div>
                      );
                    })}
                  </div>
                </details>
              );
            })()}
          </section>
        </>
      )}

      {/* Confirm modal — the human reads exactly what they are approving. */}
      {confirmTarget && (
        <div onClick={() => !actionBusy && setConfirmTarget(null)}
          style={{ position: 'fixed', inset: 0, zIndex: 9999, background: 'rgba(0,0,0,0.55)', backdropFilter: 'blur(4px)', display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 24 }}>
          <div onClick={(e) => e.stopPropagation()}
            style={{ width: 'min(440px, 94vw)', borderRadius: 14, border: '1px solid var(--panel-border-3)', background: 'var(--surface)', padding: 20, display: 'grid', gap: 12 }}>
            <div style={{ fontSize: 15, fontWeight: 800, color: 'var(--ink-90)' }}>
              {confirmTarget.action === 'approve' ? tt('devops.confirmApproveTitle') : tt('devops.confirmRejectTitle')}
            </div>
            <div style={{ ...card, padding: 12, fontSize: 12, color: 'var(--ink-78)', display: 'grid', gap: 4 }}>
              <div><b>{tt('devops.confirmPipeline')}:</b> {confirmTarget.approval.build?.definition_name || '—'}</div>
              <div><b>Build:</b> #{confirmTarget.approval.expected_build_id ?? '—'}</div>
              <div><b>{tt('devops.confirmProject')}:</b> {confirmTarget.approval.project}</div>
              <div><b>{tt('devops.confirmBranch')}:</b> {confirmTarget.approval.build?.source_branch || '—'}</div>
              <div style={{ color: CLS_COLOR[confirmTarget.approval.classification] }}>{confirmTarget.approval.reason}</div>
            </div>
            {actionError && <div style={{ fontSize: 12, color: '#cf5b57' }}>{actionError}</div>}
            <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
              <button onClick={() => setConfirmTarget(null)} disabled={actionBusy}
                style={{ padding: '8px 16px', borderRadius: 8, border: '1px solid var(--panel-border-3)', background: 'transparent', color: 'var(--ink-58)', fontWeight: 600, fontSize: 12, cursor: 'pointer' }}>
                {tt('devops.cancel')}
              </button>
              <button onClick={() => void runApprovalAction()} disabled={actionBusy}
                style={{ padding: '8px 16px', borderRadius: 8, border: 'none', background: confirmTarget.action === 'approve' ? '#3f9d6a' : '#cf5b57', color: '#fff', fontWeight: 700, fontSize: 12, cursor: actionBusy ? 'not-allowed' : 'pointer' }}>
                {actionBusy ? '…' : (confirmTarget.action === 'approve' ? tt('devops.approve') : tt('devops.reject'))}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
