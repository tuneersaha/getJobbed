'use client'

import { useState, useEffect } from 'react'
import { useSession } from 'next-auth/react'
import { useRouter } from 'next/navigation'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { apiFetch, ApiError } from '@/lib/api'
import type {
  JobListItem, JobListResponse, JobDetail,
  TailoredResumeResponse, StatsResponse, FetchTriggerResponse,
} from '@/lib/types'
import { AppNav } from '@/app/components/AppNav'
import {
  Briefcase, RefreshCw, Trash2, ExternalLink,
  AlertTriangle, X, CheckCircle, Clock, Loader2, ChevronDown,
} from 'lucide-react'

// ── helpers ────────────────────────────────────────────────────────────────

function scoreClass(s: number) {
  if (s >= 0.7) return 'strong'
  if (s >= 0.5) return 'good'
  return 'weak'
}

function fmt(s: number) { return `${Math.round(s * 100)}%` }

function relTime(iso: string | null) {
  if (!iso) return null
  const d = new Date(iso)
  const diff = (Date.now() - d.getTime()) / 1000
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`
  return `${Math.floor(diff / 86400)}d ago`
}

function safeApplyUrl(url: string) {
  return /^https?:\/\//i.test(url) ? url : null
}

// ── sub-components ──────────────────────────────────────────────────────────


function ScoreBadge({ score }: { score: number }) {
  return (
    <span className={`score-badge ${scoreClass(score)}`} title={`Match score: ${fmt(score)}`}>
      {fmt(score)}
    </span>
  )
}

function StatusChip({ status }: { status: string }) {
  if (status === 'new') return null
  const map: Record<string, string> = {
    applied: 'chip-blue', interviewing: 'chip-amber',
    offer: 'chip-match', accepted: 'chip-match', rejected: 'chip-missing',
  }
  return <span className={`chip ${map[status] ?? 'chip-neutral'}`}>{status}</span>
}

function JobCardSkeleton() {
  return (
    <div style={{ padding: '12px 14px', borderBottom: '1px solid var(--border)' }}>
      <div className="skeleton" style={{ height: '13px', width: '65%', marginBottom: '6px' }} />
      <div className="skeleton" style={{ height: '11px', width: '45%', marginBottom: '8px' }} />
      <div className="skeleton" style={{ height: '18px', width: '36px', borderRadius: '4px' }} />
    </div>
  )
}

function JobCard({
  job, selected, onClick,
}: {
  job: JobListItem; selected: boolean; onClick: () => void
}) {
  return (
    <button
      className={`card${selected ? ' active' : ''}`}
      onClick={onClick}
      style={{
        display: 'block', width: '100%', textAlign: 'left',
        padding: '12px 14px', borderRadius: 0,
        borderBottom: '1px solid var(--border)',
        borderLeft: 'none', borderRight: 'none', borderTop: 'none',
        background: selected ? '#0a1628' : 'transparent',
        cursor: 'pointer',
        minHeight: '44px',
      }}
      aria-pressed={selected}
      aria-label={`${job.title} at ${job.company_name}, score ${fmt(job.match_score)}`}
    >
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: '8px', marginBottom: '3px' }}>
        <span style={{ fontSize: '13px', fontWeight: 600, color: 'var(--fg)', lineHeight: 1.3 }}>{job.title}</span>
        <ScoreBadge score={job.match_score} />
      </div>
      <p style={{ fontSize: '12px', color: 'var(--fg-2)', marginBottom: '5px' }}>{job.company_name}</p>
      <div style={{ display: 'flex', alignItems: 'center', gap: '6px', flexWrap: 'wrap' }}>
        {job.location && <span style={{ fontSize: '11px', color: 'var(--fg-3)' }}>{job.location}</span>}
        <span style={{ fontSize: '11px', color: 'var(--fg-3)', textTransform: 'capitalize' }}>{job.work_type}</span>
        <StatusChip status={job.status} />
        {job.tailoring_failed && (
          <span className="chip chip-missing" title="Tailoring failed after 3 retries">tailoring failed</span>
        )}
        {job.posted_at && (
          <span style={{ fontSize: '11px', color: 'var(--fg-3)', marginLeft: 'auto' }}>{relTime(job.posted_at)}</span>
        )}
      </div>
    </button>
  )
}

function StatsBar({
  stats, onTriggerFetch, fetchLoading,
}: {
  stats: StatsResponse | undefined; onTriggerFetch: () => void; fetchLoading: boolean
}) {
  if (!stats) return null
  const items = [
    { label: 'Matched', value: stats.total_matched },
    { label: 'Applied', value: stats.total_applied },
    { label: 'Queue', value: stats.queue_depth },
    { label: 'Tailoring', value: stats.tailoring_in_progress },
  ]
  return (
    <div style={{
      display: 'flex', alignItems: 'center', gap: '12px',
      padding: '8px 14px', background: 'var(--bg-2)',
      borderBottom: '1px solid var(--border)', flexWrap: 'wrap',
      minHeight: '44px',
    }}>
      {items.map(({ label, value }) => (
        <div key={label} style={{ display: 'flex', gap: '4px', alignItems: 'baseline' }}>
          <span style={{ fontFamily: 'var(--font-head)', fontSize: '13px', fontWeight: 700 }}>{value}</span>
          <span style={{ fontSize: '11px', color: 'var(--fg-3)' }}>{label}</span>
        </div>
      ))}
      {stats.tailoring_failed > 0 && (
        <span className="chip chip-missing" style={{ marginLeft: '4px' }}>
          <AlertTriangle size={10} style={{ marginRight: '3px' }} />
          {stats.tailoring_failed} failed
        </span>
      )}
      <div style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: '8px' }}>
        {stats.last_fetch_at && (
          <span style={{ fontSize: '11px', color: 'var(--fg-3)' }}>
            <Clock size={10} style={{ display: 'inline', marginRight: '3px' }} />
            {relTime(stats.last_fetch_at)}
          </span>
        )}
        <button
          className="btn btn-ghost btn-sm"
          onClick={onTriggerFetch}
          disabled={fetchLoading}
          aria-label="Trigger job fetch"
          style={{ height: '32px', padding: '0 10px', fontSize: '12px' }}
        >
          {fetchLoading
            ? <><Loader2 size={12} style={{ animation: 'spin 0.7s linear infinite' }} /> Fetching…</>
            : <><RefreshCw size={12} /> Fetch</>}
        </button>
      </div>
    </div>
  )
}

function ResumeTab({ matchId, job, idToken }: { matchId: string; job: JobListItem; idToken: string }) {
  const needsPoll = !job.has_tailored_resume && !job.tailoring_failed

  const { data: resume, isLoading, error } = useQuery({
    queryKey: ['resume', matchId],
    queryFn: () => apiFetch<TailoredResumeResponse>(`/api/jobs/${matchId}/resume`, idToken),
    enabled: !job.tailoring_failed,
    refetchInterval: needsPoll ? 10_000 : false,
    staleTime: needsPoll ? 0 : Infinity,
  })

  if (job.tailoring_failed) {
    return (
      <div style={{ padding: '20px 16px', textAlign: 'center' }}>
        <AlertTriangle size={28} color="var(--red)" style={{ marginBottom: '10px' }} />
        <p style={{ fontSize: '13px', color: 'var(--fg-2)', marginBottom: '6px', fontWeight: 500 }}>Tailoring failed</p>
        <p style={{ fontSize: '12px', color: 'var(--fg-3)' }}>3 retries exhausted. Check OpenRouter key or LLM quota.</p>
      </div>
    )
  }

  if (isLoading || needsPoll && !resume) {
    return (
      <div style={{ padding: '16px' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '14px', color: 'var(--fg-3)' }}>
          <Loader2 size={14} style={{ animation: 'spin 0.7s linear infinite', flexShrink: 0 }} />
          <span style={{ fontSize: '12px' }}>Tailoring resume… checking every 10s</span>
        </div>
        {[80, 65, 72, 90, 55].map((w, i) => (
          <div key={i} className="skeleton" style={{ height: '12px', width: `${w}%`, marginBottom: '10px' }} />
        ))}
      </div>
    )
  }

  if (error) {
    const e = error as ApiError
    if (e.status === 404) {
      return (
        <div style={{ padding: '20px 16px', textAlign: 'center' }}>
          <p style={{ fontSize: '13px', color: 'var(--fg-2)' }}>No tailored resume yet.</p>
          <p style={{ fontSize: '12px', color: 'var(--fg-3)', marginTop: '4px' }}>Score may be below your tailor threshold ({job.match_score < 0.4 ? 'this job' : 'check settings'}).</p>
        </div>
      )
    }
    return (
      <div style={{ padding: '16px' }}>
        <p style={{ fontSize: '12px', color: 'var(--red)' }}>Error loading resume: {e.message}</p>
      </div>
    )
  }

  if (!resume) return null

  return (
    <div style={{ padding: '12px 14px' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '10px' }}>
        <div style={{ display: 'flex', gap: '8px' }}>
          <span className="chip chip-neutral" style={{ fontSize: '11px' }}>
            {resume.prompt_tokens + resume.completion_tokens} tok
          </span>
          <span className="chip chip-neutral" style={{ fontSize: '11px' }}>{resume.model_used.split('/').pop()}</span>
        </div>
        <button
          className="btn btn-ghost btn-sm"
          onClick={() => {
            const blob = new Blob([resume.latex_source], { type: 'text/plain' })
            const url = URL.createObjectURL(blob)
            const a = document.createElement('a')
            a.href = url; a.download = `resume-${matchId.slice(0, 8)}.tex`
            a.click(); URL.revokeObjectURL(url)
          }}
          aria-label="Download .tex file"
        >
          Download .tex
        </button>
      </div>
      {/* SECURITY: render as code only — never dangerouslySetInnerHTML */}
      <pre style={{
        fontSize: '11px', lineHeight: 1.7, color: 'var(--fg-2)',
        overflowX: 'auto', maxHeight: '400px', overflowY: 'auto',
        background: 'var(--bg-3)', borderRadius: '6px',
        padding: '12px', border: '1px solid var(--border)',
        tabSize: 2,
      }}>
        <code>{resume.latex_source}</code>
      </pre>
    </div>
  )
}

function GapSection({ job }: { job: JobListItem | JobDetail }) {
  const { matching_skills, missing_skills, skills_coverage } = job.gap_analysis
  if (!matching_skills.length && !missing_skills.length) return null
  return (
    <div style={{ padding: '10px 14px', borderTop: '1px solid var(--border)' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '7px' }}>
        <span style={{ fontSize: '11px', color: 'var(--fg-3)', textTransform: 'uppercase', letterSpacing: '0.06em' }}>Skills gap</span>
        <span style={{ fontSize: '11px', color: 'var(--fg-3)' }}>{Math.round(skills_coverage * 100)}% coverage</span>
      </div>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: '5px' }}>
        {matching_skills.slice(0, 8).map((s) => <span key={s} className="chip chip-match">{s}</span>)}
        {missing_skills.slice(0, 6).map((s) => <span key={s} className="chip chip-missing">{s}</span>)}
      </div>
    </div>
  )
}

function DeleteModal({ title: jobTitle, onConfirm, onCancel, loading }: {
  title: string; onConfirm: () => void; onCancel: () => void; loading: boolean
}) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onCancel() }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [onCancel])

  return (
    <>
      <div
        className="sheet-backdrop"
        onClick={onCancel}
        aria-hidden="true"
        style={{ zIndex: 50 }}
      />
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="delete-dialog-title"
        style={{
          position: 'fixed', top: '50%', left: '50%',
          transform: 'translate(-50%, -50%)',
          background: 'var(--bg-2)', border: '1px solid var(--border)',
          borderRadius: '12px', padding: '20px', width: 'calc(100% - 32px)',
          maxWidth: '380px', zIndex: 51,
          animation: 'fadeIn 150ms ease-out',
        }}
      >
        <p id="delete-dialog-title" style={{ fontWeight: 700, fontSize: '15px', marginBottom: '8px' }}>Remove job?</p>
        <p style={{ fontSize: '13px', color: 'var(--fg-2)', marginBottom: '20px', lineHeight: 1.5 }}>
          Remove <strong>{jobTitle}</strong> from your list? The job stays in the system — only your match is hidden.
        </p>
        <div style={{ display: 'flex', gap: '8px' }}>
          <button className="btn btn-ghost" style={{ flex: 1 }} onClick={onCancel}>Cancel</button>
          <button className="btn btn-danger" style={{ flex: 1 }} onClick={onConfirm} disabled={loading}>
            {loading ? <><div className="spinner" /> Removing…</> : 'Remove'}
          </button>
        </div>
      </div>
    </>
  )
}

type Tab = 'jd' | 'resume'

function JobDetailContent({
  job, onApply, onDelete, idToken,
}: {
  job: JobListItem; onApply: () => void; onDelete: () => void; idToken: string
}) {
  const [tab, setTab] = useState<Tab>('jd')
  const qc = useQueryClient()

  const { data: detail, isLoading: detailLoading } = useQuery({
    queryKey: ['job-detail', job.match_id],
    queryFn: () => apiFetch<JobDetail>(`/api/jobs/${job.match_id}`, idToken),
    staleTime: 2 * 60_000,
  })

  const applyMutation = useMutation({
    mutationFn: () => apiFetch(`/api/matches/${job.match_id}`, idToken, {
      method: 'PATCH',
      body: JSON.stringify({ status: 'applied' }),
    }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['jobs'] })
      qc.invalidateQueries({ queryKey: ['stats'] })
    },
  })

  function handleApply() {
    if (!detail?.apply_url) return
    const safe = safeApplyUrl(detail.apply_url)
    if (!safe) return
    window.open(safe, '_blank', 'noopener,noreferrer')
    applyMutation.mutate()
    onApply()
  }

  const tabStyle = (t: Tab) => ({
    flex: 1, height: '38px', background: 'none', border: 'none', cursor: 'pointer',
    borderBottom: `2px solid ${tab === t ? 'var(--accent)' : 'transparent'}`,
    color: tab === t ? 'var(--fg)' : 'var(--fg-3)',
    fontFamily: 'var(--font-body)', fontSize: '13px', fontWeight: 500,
    transition: 'color 150ms ease, border-color 150ms ease',
  } as React.CSSProperties)

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      {/* Header */}
      <div style={{ padding: '14px', borderBottom: '1px solid var(--border)', flexShrink: 0 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: '10px', marginBottom: '4px' }}>
          <div>
            <h2 style={{ fontFamily: 'var(--font-head)', fontSize: '16px', fontWeight: 700, lineHeight: 1.3, marginBottom: '3px' }}>{job.title}</h2>
            <p style={{ fontSize: '13px', color: 'var(--fg-2)' }}>{job.company_name}{job.location ? ` · ${job.location}` : ''}</p>
          </div>
          <ScoreBadge score={job.match_score} />
        </div>
        <div style={{ display: 'flex', gap: '6px', marginTop: '8px', flexWrap: 'wrap' }}>
          <span className="chip chip-neutral" style={{ textTransform: 'capitalize' }}>{job.work_type}</span>
          <StatusChip status={job.status} />
          {job.posted_at && <span style={{ fontSize: '11px', color: 'var(--fg-3)', alignSelf: 'center' }}>{relTime(job.posted_at)}</span>}
        </div>
      </div>

      {/* Gap analysis */}
      <GapSection job={job} />

      {/* Tab bar */}
      <div style={{ display: 'flex', borderBottom: '1px solid var(--border)', flexShrink: 0 }}>
        <button style={tabStyle('jd')} onClick={() => setTab('jd')}>Description</button>
        <button style={tabStyle('resume')} onClick={() => setTab('resume')}>
          Tailored Resume
          {job.has_tailored_resume && (
            <span style={{ marginLeft: '5px', width: '6px', height: '6px', borderRadius: '50%', background: 'var(--green)', display: 'inline-block', verticalAlign: 'middle' }} />
          )}
        </button>
      </div>

      {/* Tab content */}
      <div style={{ flex: 1, overflowY: 'auto' }}>
        {tab === 'jd' ? (
          <div style={{ padding: '14px' }}>
            {detailLoading ? (
              [90, 75, 82, 68, 95, 70, 60].map((w, i) => (
                <div key={i} className="skeleton" style={{ height: '12px', width: `${w}%`, marginBottom: '10px' }} />
              ))
            ) : detail ? (
              <p style={{ fontSize: '13px', color: 'var(--fg-2)', lineHeight: 1.75, whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
                {detail.description}
              </p>
            ) : (
              <p style={{ fontSize: '13px', color: 'var(--fg-3)' }}>Failed to load description.</p>
            )}
          </div>
        ) : (
          <ResumeTab matchId={job.match_id} job={job} idToken={idToken} />
        )}
      </div>

      {/* Actions */}
      <div style={{
        padding: '12px 14px', borderTop: '1px solid var(--border)',
        display: 'flex', gap: '8px', flexShrink: 0,
        paddingBottom: 'calc(12px + env(safe-area-inset-bottom, 0px))',
      }}>
        <button
          className="btn btn-danger btn-sm"
          onClick={onDelete}
          aria-label="Remove this job"
          style={{ width: '44px', padding: 0, flexShrink: 0 }}
        >
          <Trash2 size={15} />
        </button>
        <button
          className="btn btn-primary"
          style={{ flex: 1 }}
          onClick={handleApply}
          disabled={!detail?.apply_url || applyMutation.isPending}
          aria-label="Apply to this job"
        >
          {applyMutation.isPending ? (
            <><div className="spinner" style={{ borderTopColor: '#000' }} /> Applying…</>
          ) : (
            <><ExternalLink size={15} /> Apply now</>
          )}
        </button>
      </div>
    </div>
  )
}

function EmptyState() {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', height: '100%', padding: '40px 20px', textAlign: 'center' }}>
      <Briefcase size={40} color="var(--fg-3)" style={{ marginBottom: '14px' }} />
      <p style={{ fontSize: '15px', fontWeight: 600, marginBottom: '6px' }}>No matches yet</p>
      <p style={{ fontSize: '13px', color: 'var(--fg-2)', maxWidth: '260px', lineHeight: 1.6 }}>
        Trigger a fetch to find jobs, or wait for the next scheduled run.
      </p>
    </div>
  )
}

// ── main page ───────────────────────────────────────────────────────────────

export default function DashboardPage() {
  const { data: session, status } = useSession()
  const router = useRouter()
  const qc = useQueryClient()

  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [sheetOpen, setSheetOpen] = useState(false)
  const [deleteTarget, setDeleteTarget] = useState<JobListItem | null>(null)
  const [page, setPage] = useState(0)
  const PAGE_SIZE = 25

  const idToken = session?.id_token ?? ''

  const { data: stats } = useQuery({
    queryKey: ['stats'],
    queryFn: () => apiFetch<StatsResponse>('/api/stats', idToken),
    enabled: !!idToken,
    refetchInterval: 60_000,
  })

  const { data: jobsData, isLoading: jobsLoading } = useQuery({
    queryKey: ['jobs', page],
    queryFn: () => apiFetch<JobListResponse>(`/api/jobs?limit=${PAGE_SIZE}&offset=${page * PAGE_SIZE}`, idToken),
    enabled: !!idToken,
    refetchInterval: 5 * 60_000,
  })

  const fetchMutation = useMutation({
    mutationFn: () => apiFetch<FetchTriggerResponse>('/api/fetch/trigger', idToken, { method: 'POST' }),
    onSuccess: () => setTimeout(() => qc.invalidateQueries({ queryKey: ['stats'] }), 3000),
  })

  const deleteMutation = useMutation({
    mutationFn: (matchId: string) =>
      apiFetch(`/api/matches/${matchId}`, idToken, {
        method: 'PATCH',
        body: JSON.stringify({ status: 'deleted' }),
      }),
    onMutate: async (matchId) => {
      await qc.cancelQueries({ queryKey: ['jobs', page] })
      const prev = qc.getQueryData<JobListResponse>(['jobs', page])
      qc.setQueryData<JobListResponse>(['jobs', page], (old) =>
        old ? { ...old, jobs: old.jobs.filter((j) => j.match_id !== matchId), total: old.total - 1 } : old
      )
      return { prev }
    },
    onError: (_err, _matchId, ctx) => {
      if (ctx?.prev) qc.setQueryData(['jobs', page], ctx.prev)
    },
    onSuccess: () => {
      setDeleteTarget(null)
      setSelectedId(null)
      setSheetOpen(false)
      qc.invalidateQueries({ queryKey: ['stats'] })
    },
  })

  const jobs = jobsData?.jobs ?? []
  const selectedJob = jobs.find((j) => j.match_id === selectedId) ?? null

  function selectJob(job: JobListItem) {
    setSelectedId(job.match_id)
    setSheetOpen(true)
  }

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setSheetOpen(false) }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [])

  if (status === 'loading') {
    return (
      <main style={{ minHeight: '100dvh', display: 'flex', alignItems: 'center', justifyContent: 'center', background: 'var(--bg)' }}>
        <div className="spinner" style={{ width: '24px', height: '24px' }} />
      </main>
    )
  }

  if (status === 'unauthenticated') {
    router.replace('/auth/signin')
    return null
  }

  return (
    <div className="app-layout">
      <AppNav />
      <main style={{
        flex: 1, minWidth: 0,
        background: 'var(--bg)',
        display: 'flex',
        flexDirection: 'column',
        paddingBottom: 'calc(56px + env(safe-area-inset-bottom, 0px))',
      }} className="app-main page-bottom-pad">
      {/* Top bar */}
      <header style={{
        display: 'flex', alignItems: 'center',
        padding: '0 14px', height: '48px',
        borderBottom: '1px solid var(--border)',
        background: 'var(--bg-2)',
        flexShrink: 0,
      }}>
        <span style={{ fontFamily: 'var(--font-head)', fontSize: '15px', fontWeight: 700, letterSpacing: '-0.02em' }}>Jobs</span>
      </header>

      {/* Stats bar */}
      <StatsBar
        stats={stats}
        onTriggerFetch={() => fetchMutation.mutate()}
        fetchLoading={fetchMutation.isPending}
      />

      {/* Content area */}
      <div style={{ flex: 1, display: 'flex', overflow: 'hidden' }}>
        {/* Job list */}
        <div style={{
          width: '100%',
          overflowY: 'auto',
          borderRight: '1px solid var(--border)',
          display: 'flex',
          flexDirection: 'column',
        }}
          // Desktop: limit width
          className="job-list-panel"
        >
          {/* List header */}
          <div style={{
            padding: '10px 14px', borderBottom: '1px solid var(--border)',
            display: 'flex', alignItems: 'center', justifyContent: 'space-between',
            position: 'sticky', top: 0, background: 'var(--bg)',
            zIndex: 5, flexShrink: 0,
          }}>
            <span style={{ fontSize: '12px', color: 'var(--fg-3)' }}>
              {jobsLoading ? 'Loading…' : `${jobsData?.total ?? 0} jobs`}
            </span>
            <button
              className="btn btn-ghost btn-sm"
              onClick={() => qc.invalidateQueries({ queryKey: ['jobs'] })}
              style={{ height: '30px', padding: '0 10px', fontSize: '12px' }}
              aria-label="Refresh job list"
            >
              <RefreshCw size={12} />
            </button>
          </div>

          {/* Skeletons */}
          {jobsLoading && Array.from({ length: 6 }).map((_, i) => <JobCardSkeleton key={i} />)}

          {/* Empty */}
          {!jobsLoading && jobs.length === 0 && <EmptyState />}

          {/* Jobs */}
          {jobs.map((job) => (
            <JobCard
              key={job.match_id}
              job={job}
              selected={selectedId === job.match_id}
              onClick={() => selectJob(job)}
            />
          ))}

          {/* Pagination */}
          {jobsData && jobsData.has_more && (
            <button
              className="btn btn-ghost"
              onClick={() => setPage((p) => p + 1)}
              style={{ margin: '12px 14px', width: 'calc(100% - 28px)' }}
            >
              <ChevronDown size={14} /> Load more
            </button>
          )}
        </div>

        {/* Desktop detail panel — hidden on mobile via CSS */}
        <div
          className="detail-panel"
          style={{ flex: 1, overflowY: 'auto', display: 'none' }}
        >
          {selectedJob ? (
            <JobDetailContent
              key={selectedJob.match_id}
              job={selectedJob}
              onApply={() => {}}
              onDelete={() => setDeleteTarget(selectedJob)}
              idToken={idToken}
            />
          ) : (
            <EmptyState />
          )}
        </div>
      </div>

      {/* Mobile bottom sheet */}
      {sheetOpen && selectedJob && (
        <>
          <div className="sheet-backdrop" onClick={() => setSheetOpen(false)} aria-hidden="true" />
          <div className="bottom-sheet" role="dialog" aria-modal="true" aria-label="Job details">
            <div className="sheet-handle" />
            <button
              onClick={() => setSheetOpen(false)}
              aria-label="Close"
              style={{
                position: 'absolute', top: '12px', right: '14px',
                background: 'none', border: 'none', cursor: 'pointer',
                color: 'var(--fg-3)', padding: '6px',
              }}
            >
              <X size={18} />
            </button>
            <div style={{ flex: 1, overflow: 'hidden', display: 'flex', flexDirection: 'column' }}>
              <JobDetailContent
                key={selectedJob.match_id}
                job={selectedJob}
                onApply={() => setSheetOpen(false)}
                onDelete={() => setDeleteTarget(selectedJob)}
                idToken={idToken}
              />
            </div>
          </div>
        </>
      )}

      {/* Delete confirmation */}
      {deleteTarget && (
        <DeleteModal
          title={deleteTarget.title}
          onConfirm={() => deleteMutation.mutate(deleteTarget.match_id)}
          onCancel={() => setDeleteTarget(null)}
          loading={deleteMutation.isPending}
        />
      )}

      </main>
    </div>
  )
}
