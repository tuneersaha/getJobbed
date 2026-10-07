'use client'

import { useState } from 'react'
import { useSession } from 'next-auth/react'
import { useRouter } from 'next/navigation'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { apiFetch } from '@/lib/api'
import type { AppliedListItem, AppliedListResponse } from '@/lib/types'
import { AppNav } from '@/app/components/AppNav'
import { CheckCircle, ChevronDown, Trash2, TrendingUp } from 'lucide-react'

const STATUSES = ['applied', 'interviewing', 'offer', 'accepted', 'rejected'] as const
type AppStatus = (typeof STATUSES)[number]

const STATUS_LABELS: Record<AppStatus, string> = {
  applied: 'Applied',
  interviewing: 'Interviewing',
  offer: 'Offer',
  accepted: 'Accepted',
  rejected: 'Rejected',
}

const STATUS_CHIP: Record<AppStatus, string> = {
  applied: 'chip-blue',
  interviewing: 'chip-amber',
  offer: 'chip-match',
  accepted: 'chip-match',
  rejected: 'chip-missing',
}

function fmt(s: number) { return `${Math.round(s * 100)}%` }

function relTime(iso: string | null) {
  if (!iso) return '—'
  const d = new Date(iso)
  const diff = (Date.now() - d.getTime()) / 1000
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`
  if (diff < 86400 * 7) return `${Math.floor(diff / 86400)}d ago`
  return d.toLocaleDateString('en-IN', { day: 'numeric', month: 'short' })
}


function DeleteModal({ title: jobTitle, onConfirm, onCancel, loading }: {
  title: string; onConfirm: () => void; onCancel: () => void; loading: boolean
}) {
  return (
    <>
      <div className="sheet-backdrop" onClick={onCancel} aria-hidden="true" style={{ zIndex: 50 }} />
      <div
        role="dialog" aria-modal="true" aria-labelledby="del-title"
        style={{
          position: 'fixed', top: '50%', left: '50%',
          transform: 'translate(-50%, -50%)',
          background: 'var(--bg-2)', border: '1px solid var(--border)',
          borderRadius: '12px', padding: '20px',
          width: 'calc(100% - 32px)', maxWidth: '360px', zIndex: 51,
          animation: 'fadeIn 150ms ease-out',
        }}
      >
        <p id="del-title" style={{ fontWeight: 700, fontSize: '15px', marginBottom: '8px' }}>Remove application?</p>
        <p style={{ fontSize: '13px', color: 'var(--fg-2)', marginBottom: '20px', lineHeight: 1.5 }}>
          Remove <strong>{jobTitle}</strong> from your applied list?
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

type SortKey = 'applied_at' | 'match_score' | 'status'

function sortJobs(jobs: AppliedListItem[], key: SortKey, dir: 1 | -1) {
  return [...jobs].sort((a, b) => {
    let va: string | number, vb: string | number
    if (key === 'applied_at') { va = a.applied_at ?? ''; vb = b.applied_at ?? '' }
    else if (key === 'match_score') { va = a.match_score; vb = b.match_score }
    else { va = a.status; vb = b.status }
    if (va < vb) return -1 * dir
    if (va > vb) return 1 * dir
    return 0
  })
}

export default function AppliedPage() {
  const { data: session, status } = useSession()
  const router = useRouter()
  const qc = useQueryClient()

  const [deleteTarget, setDeleteTarget] = useState<AppliedListItem | null>(null)
  const [sortKey, setSortKey] = useState<SortKey>('applied_at')
  const [sortDir, setSortDir] = useState<1 | -1>(-1)
  const [page, setPage] = useState(0)
  const PAGE_SIZE = 50

  const idToken = session?.id_token ?? ''

  const { data: jobsData, isLoading } = useQuery({
    queryKey: ['applied', page],
    queryFn: () =>
      apiFetch<AppliedListResponse>(`/api/applied?limit=${PAGE_SIZE}&offset=${page * PAGE_SIZE}`, idToken),
    enabled: !!idToken,
    staleTime: 60_000,
  })

  const statusMutation = useMutation({
    mutationFn: ({ matchId, status: s }: { matchId: string; status: AppStatus }) =>
      apiFetch(`/api/applied/${matchId}`, idToken, {
        method: 'PATCH',
        body: JSON.stringify({ status: s }),
      }),
    onMutate: async ({ matchId, status: s }) => {
      await qc.cancelQueries({ queryKey: ['applied', page] })
      const prev = qc.getQueryData<AppliedListResponse>(['applied', page])
      qc.setQueryData<AppliedListResponse>(['applied', page], (old) =>
        old ? { ...old, jobs: old.jobs.map((j) => j.match_id === matchId ? { ...j, status: s } : j) } : old
      )
      return { prev }
    },
    onError: (_err, _vars, ctx) => {
      if (ctx?.prev) qc.setQueryData(['applied', page], ctx.prev)
    },
  })

  const deleteMutation = useMutation({
    mutationFn: (matchId: string) =>
      apiFetch(`/api/matches/${matchId}`, idToken, {
        method: 'PATCH',
        body: JSON.stringify({ status: 'deleted' }),
      }),
    onMutate: async (matchId) => {
      await qc.cancelQueries({ queryKey: ['applied', page] })
      const prev = qc.getQueryData<AppliedListResponse>(['applied', page])
      qc.setQueryData<AppliedListResponse>(['applied', page], (old) =>
        old ? { ...old, jobs: old.jobs.filter((j) => j.match_id !== matchId), total: old.total - 1 } : old
      )
      return { prev }
    },
    onError: (_err, _matchId, ctx) => {
      if (ctx?.prev) qc.setQueryData(['applied', page], ctx.prev)
    },
    onSuccess: () => setDeleteTarget(null),
  })

  function toggleSort(key: SortKey) {
    if (sortKey === key) setSortDir((d) => (d === -1 ? 1 : -1))
    else { setSortKey(key); setSortDir(-1) }
  }

  const rawJobs = jobsData?.jobs ?? []
  const jobs = sortJobs(rawJobs, sortKey, sortDir)

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

  const SortBtn = ({ col, label }: { col: SortKey; label: string }) => (
    <button
      onClick={() => toggleSort(col)}
      style={{
        background: 'none', border: 'none', cursor: 'pointer',
        color: sortKey === col ? 'var(--fg)' : 'var(--fg-3)',
        fontFamily: 'var(--font-body)', fontSize: '11px', fontWeight: 600,
        display: 'inline-flex', alignItems: 'center', gap: '2px',
        textTransform: 'uppercase', letterSpacing: '0.06em', padding: '4px 0',
      }}
      aria-sort={sortKey === col ? (sortDir === -1 ? 'descending' : 'ascending') : 'none'}
    >
      {label}
      {sortKey === col && <ChevronDown size={10} style={{ transform: sortDir === 1 ? 'rotate(180deg)' : 'none' }} />}
    </button>
  )

  return (
    <div className="app-layout">
      <AppNav />
      <main style={{
        flex: 1, minWidth: 0,
        background: 'var(--bg)',
        display: 'flex', flexDirection: 'column',
        paddingBottom: 'calc(56px + env(safe-area-inset-bottom, 0px))',
      }} className="app-main page-bottom-pad">
      {/* Header */}
      <header style={{
        display: 'flex', alignItems: 'center', gap: '10px',
        padding: '0 14px', height: '48px',
        borderBottom: '1px solid var(--border)',
        background: 'var(--bg-2)', flexShrink: 0,
      }}>
        <TrendingUp size={16} color="var(--accent)" />
        <span style={{ fontFamily: 'var(--font-head)', fontSize: '15px', fontWeight: 700 }}>Applied</span>
        {!isLoading && jobsData && (
          <span style={{ fontSize: '12px', color: 'var(--fg-3)', marginLeft: '4px' }}>{jobsData.total}</span>
        )}
      </header>

      {/* Table */}
      <div style={{ flex: 1, overflowY: 'auto' }}>
        {isLoading ? (
          <div style={{ display: 'flex', flexDirection: 'column' }}>
            {Array.from({ length: 5 }).map((_, i) => (
              <div key={i} style={{ padding: '14px', borderBottom: '1px solid var(--border)' }}>
                <div className="skeleton" style={{ height: '13px', width: '60%', marginBottom: '6px' }} />
                <div className="skeleton" style={{ height: '11px', width: '40%' }} />
              </div>
            ))}
          </div>
        ) : jobs.length === 0 ? (
          <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', height: '300px', gap: '10px', textAlign: 'center', padding: '20px' }}>
            <CheckCircle size={36} color="var(--fg-3)" />
            <p style={{ fontSize: '14px', fontWeight: 600 }}>No applications yet</p>
            <p style={{ fontSize: '13px', color: 'var(--fg-2)' }}>Head to the jobs tab and hit Apply on a match.</p>
          </div>
        ) : (
          <>
            {/* Sort bar */}
            <div style={{
              display: 'flex', gap: '14px', padding: '8px 14px',
              borderBottom: '1px solid var(--border)',
              position: 'sticky', top: 0, background: 'var(--bg)', zIndex: 5,
            }}>
              <SortBtn col="applied_at" label="Date" />
              <SortBtn col="match_score" label="Score" />
              <SortBtn col="status" label="Status" />
            </div>

            {jobs.map((job) => (
              <div
                key={job.match_id}
                style={{
                  padding: '12px 14px', borderBottom: '1px solid var(--border)',
                  display: 'flex', alignItems: 'flex-start', gap: '10px',
                }}
              >
                {/* Main info */}
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ display: 'flex', alignItems: 'flex-start', gap: '6px', flexWrap: 'wrap', marginBottom: '2px' }}>
                    <span style={{ fontSize: '13px', fontWeight: 600, lineHeight: 1.3 }}>{job.title}</span>
                    <span style={{
                      fontFamily: 'var(--font-head)', fontSize: '11px', fontWeight: 700,
                      color: job.match_score >= 0.7 ? 'var(--green)' : job.match_score >= 0.5 ? 'var(--amber)' : 'var(--fg-3)',
                    }}>{fmt(job.match_score)}</span>
                  </div>
                  <p style={{ fontSize: '12px', color: 'var(--fg-2)', marginBottom: '6px' }}>
                    {job.company_name}{job.location ? ` · ${job.location}` : ''}
                  </p>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap' }}>
                    {/* Status dropdown */}
                    <div style={{ position: 'relative' }}>
                      <select
                        value={job.status}
                        onChange={(e) =>
                          statusMutation.mutate({ matchId: job.match_id, status: e.target.value as AppStatus })
                        }
                        className="input"
                        style={{
                          height: '28px', padding: '0 24px 0 8px', fontSize: '11px',
                          appearance: 'none', width: 'auto', paddingRight: '20px',
                          cursor: 'pointer',
                        }}
                        aria-label={`Change status for ${job.title}`}
                      >
                        {STATUSES.map((s) => (
                          <option key={s} value={s}>{STATUS_LABELS[s]}</option>
                        ))}
                      </select>
                      <ChevronDown size={10} style={{ position: 'absolute', right: '6px', top: '50%', transform: 'translateY(-50%)', pointerEvents: 'none', color: 'var(--fg-3)' }} />
                    </div>
                    <span style={{ fontSize: '11px', color: 'var(--fg-3)' }}>{relTime(job.applied_at)}</span>
                  </div>
                </div>
                {/* Delete */}
                <button
                  className="btn btn-ghost btn-sm"
                  onClick={() => setDeleteTarget(job)}
                  aria-label={`Remove ${job.title}`}
                  style={{ width: '36px', height: '36px', padding: 0, flexShrink: 0 }}
                >
                  <Trash2 size={14} />
                </button>
              </div>
            ))}

            {jobsData?.has_more && (
              <button
                className="btn btn-ghost"
                onClick={() => setPage((p) => p + 1)}
                style={{ margin: '12px 14px', width: 'calc(100% - 28px)' }}
              >
                <ChevronDown size={14} /> Load more
              </button>
            )}
          </>
        )}
      </div>

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
