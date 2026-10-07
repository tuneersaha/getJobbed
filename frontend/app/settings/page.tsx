'use client'

import { useState, useEffect, useRef, useCallback } from 'react'
import { useSession, signOut } from 'next-auth/react'
import { useRouter } from 'next/navigation'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { motion } from 'framer-motion'
import { apiFetch, ApiError } from '@/lib/api'
import type { ProfileResponse, ResumeMetadata, StatsResponse, FetchTriggerResponse } from '@/lib/types'
import { AppNav } from '@/app/components/AppNav'
import {
  Plus, X, RefreshCw, Upload, Loader2, Check, AlertTriangle,
} from 'lucide-react'

function SectionHeader({ title }: { title: string }) {
  return (
    <p style={{
      fontSize: '10px', fontWeight: 700, color: 'var(--fg-3)',
      textTransform: 'uppercase', letterSpacing: '0.1em',
      padding: '20px 0 8px',
    }}>{title}</p>
  )
}

function Card({ children }: { children: React.ReactNode }) {
  return (
    <div style={{
      background: 'var(--bg-2)', border: '1px solid var(--border)',
      borderRadius: '10px', padding: '16px', marginBottom: '8px',
    }}>
      {children}
    </div>
  )
}

function SaveIndicator({ state }: { state: 'idle' | 'saving' | 'saved' | 'error' }) {
  if (state === 'idle') return null
  return (
    <span style={{
      fontSize: '11px',
      color: state === 'saved' ? 'var(--green)' : state === 'error' ? 'var(--red)' : 'var(--fg-3)',
      display: 'inline-flex', alignItems: 'center', gap: '4px',
    }}>
      {state === 'saving' && <Loader2 size={10} style={{ animation: 'spin 0.7s linear infinite' }} />}
      {state === 'saved' && <Check size={10} />}
      {state === 'saving' ? 'Saving…' : state === 'saved' ? 'Saved' : 'Error saving'}
    </span>
  )
}

interface Draft {
  desired_roles: string[]
  experience_years: number
  locations: string[]
  remote_ok: boolean
  excluded_keywords: string[]
  tailor_threshold: number
}

export default function SettingsPage() {
  const { data: session, status } = useSession()
  const router = useRouter()
  const qc = useQueryClient()

  const idToken = session?.id_token ?? ''
  const saveTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const [saveState, setSaveState] = useState<'idle' | 'saving' | 'saved' | 'error'>('idle')
  const [draft, setDraft] = useState<Draft | null>(null)

  const [roleInput, setRoleInput] = useState('')
  const [locationInput, setLocationInput] = useState('')
  const [keywordInput, setKeywordInput] = useState('')

  const fileRef = useRef<HTMLInputElement>(null)
  const [uploading, setUploading] = useState(false)
  const [uploadError, setUploadError] = useState<string | null>(null)
  const [uploadOk, setUploadOk] = useState(false)

  const { data: profile, isLoading: profileLoading } = useQuery({
    queryKey: ['profile'],
    queryFn: () => apiFetch<ProfileResponse>('/api/profile', idToken),
    enabled: !!idToken,
    staleTime: 5 * 60_000,
  })

  const { data: resume } = useQuery({
    queryKey: ['resume-meta'],
    queryFn: () => apiFetch<ResumeMetadata>('/api/resume', idToken),
    enabled: !!idToken,
    staleTime: 5 * 60_000,
  })

  const { data: stats } = useQuery({
    queryKey: ['stats'],
    queryFn: () => apiFetch<StatsResponse>('/api/stats', idToken),
    enabled: !!idToken,
    staleTime: 60_000,
  })

  const fetchMutation = useMutation({
    mutationFn: () => apiFetch<FetchTriggerResponse>('/api/fetch/trigger', idToken, { method: 'POST' }),
    onSuccess: () => setTimeout(() => qc.invalidateQueries({ queryKey: ['stats'] }), 3000),
  })

  useEffect(() => {
    if (profile && !draft) {
      setDraft({
        desired_roles: profile.desired_roles,
        experience_years: profile.experience_years,
        locations: profile.locations,
        remote_ok: profile.remote_ok,
        excluded_keywords: profile.excluded_keywords,
        tailor_threshold: profile.tailor_threshold,
      })
    }
  }, [profile, draft])

  const scheduleSave = useCallback((d: Draft) => {
    if (saveTimer.current) clearTimeout(saveTimer.current)
    setSaveState('saving')
    saveTimer.current = setTimeout(async () => {
      try {
        await apiFetch('/api/profile', idToken, {
          method: 'PUT',
          body: JSON.stringify({
            desired_roles: d.desired_roles,
            experience_years: d.experience_years,
            locations: d.locations,
            remote_ok: d.remote_ok,
            excluded_keywords: d.excluded_keywords,
            tailor_threshold: d.tailor_threshold,
          }),
        })
        setSaveState('saved')
        qc.invalidateQueries({ queryKey: ['profile'] })
        setTimeout(() => setSaveState('idle'), 2500)
      } catch {
        setSaveState('error')
      }
    }, 800)
  }, [idToken, qc])

  function update(patch: Partial<Draft>) {
    if (!draft) return
    const next = { ...draft, ...patch }
    setDraft(next)
    scheduleSave(next)
  }

  async function uploadResume(file: File) {
    if (!file.name.endsWith('.tex') && file.type !== 'text/plain' && file.type !== 'application/x-tex') {
      setUploadError('Please upload a .tex file')
      return
    }
    setUploading(true)
    setUploadError(null)
    setUploadOk(false)
    try {
      const text = await file.text()
      await apiFetch<ResumeMetadata>('/api/resume', idToken, {
        method: 'POST',
        headers: { 'Content-Type': 'text/plain' },
        body: text,
      } as RequestInit)
      setUploadOk(true)
      qc.invalidateQueries({ queryKey: ['resume-meta'] })
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) {
        signOut({ callbackUrl: '/auth/signin' })
        return
      }
      setUploadError(
        e instanceof ApiError
          ? e.code === 'RESUME_INVALID_LATEX' ? 'Invalid LaTeX file' : e.message
          : 'Upload failed'
      )
    } finally {
      setUploading(false)
    }
  }

  if (status === 'loading' || profileLoading || !draft) {
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

      <main className="app-main" style={{ background: 'var(--bg)' }}>
        {/* Header */}
        <header style={{
          display: 'flex', alignItems: 'center', justifyContent: 'space-between',
          padding: '0 20px', height: '48px',
          borderBottom: '1px solid var(--border)', background: 'var(--bg-2)', flexShrink: 0,
        }}>
          <span style={{ fontFamily: 'var(--font-head)', fontSize: '15px', fontWeight: 700 }}>Settings</span>
          <SaveIndicator state={saveState} />
        </header>

        {/* Scrollable content */}
        <div style={{ flex: 1, overflowY: 'auto', paddingBottom: 'calc(56px + env(safe-area-inset-bottom, 0px))' }} className="page-bottom-pad">
          <motion.div
            initial={{ opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.3, ease: 'easeOut' }}
            className="content-wrap"
            style={{ padding: '0 16px' }}
          >

            {/* ── Roles & Experience ── */}
            <SectionHeader title="Roles & Experience" />

            <Card>
              <label style={{ fontSize: '12px', fontWeight: 600, color: 'var(--fg-2)', display: 'block', marginBottom: '8px' }}>
                Target roles
              </label>
              <div style={{ display: 'flex', gap: '8px', marginBottom: '8px' }}>
                <input
                  className="input"
                  placeholder="Add role…"
                  value={roleInput}
                  onChange={(e) => setRoleInput(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key !== 'Enter') return
                    e.preventDefault()
                    const r = roleInput.trim()
                    if (r && !draft.desired_roles.includes(r) && draft.desired_roles.length < 10) {
                      update({ desired_roles: [...draft.desired_roles, r] })
                    }
                    setRoleInput('')
                  }}
                  style={{ flex: 1 }}
                  aria-label="Add target role"
                />
                <button
                  className="btn btn-ghost btn-sm"
                  onClick={() => {
                    const r = roleInput.trim()
                    if (r && !draft.desired_roles.includes(r)) update({ desired_roles: [...draft.desired_roles, r] })
                    setRoleInput('')
                  }}
                  style={{ height: '44px', padding: '0 14px', flexShrink: 0 }}
                >
                  <Plus size={16} />
                </button>
              </div>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px' }}>
                {draft.desired_roles.map((r) => (
                  <button
                    key={r}
                    onClick={() => update({ desired_roles: draft.desired_roles.filter((x) => x !== r) })}
                    style={{ display: 'inline-flex', alignItems: 'center', gap: '4px', cursor: 'pointer', background: 'none', border: '1px solid rgba(59,130,246,0.3)', color: 'var(--accent)', borderRadius: '4px', padding: '3px 8px', fontSize: '12px' }}
                    aria-label={`Remove ${r}`}
                  >
                    {r} <X size={12} />
                  </button>
                ))}
              </div>
            </Card>

            <Card>
              <label style={{ fontSize: '12px', fontWeight: 600, color: 'var(--fg-2)', display: 'block', marginBottom: '8px' }}>
                Experience — <span style={{ fontFamily: 'var(--font-head)', color: 'var(--fg)' }}>{draft.experience_years} yr{draft.experience_years !== 1 ? 's' : ''}</span>
              </label>
              <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
                <button className="btn btn-ghost btn-sm" onClick={() => update({ experience_years: Math.max(0, draft.experience_years - 1) })} style={{ width: '44px', height: '44px', padding: 0 }} aria-label="Decrease">−</button>
                <input type="range" min={0} max={10} step={1} value={draft.experience_years} onChange={(e) => update({ experience_years: Number(e.target.value) })} style={{ flex: 1, accentColor: 'var(--accent)', height: '4px', cursor: 'pointer' }} aria-label="Experience years slider" />
                <button className="btn btn-ghost btn-sm" onClick={() => update({ experience_years: Math.min(10, draft.experience_years + 1) })} style={{ width: '44px', height: '44px', padding: 0 }} aria-label="Increase">+</button>
              </div>
            </Card>

            {/* ── Job Preferences ── */}
            <SectionHeader title="Job Preferences" />

            <Card>
              <label style={{ fontSize: '12px', fontWeight: 600, color: 'var(--fg-2)', display: 'block', marginBottom: '8px' }}>
                Preferred locations
              </label>
              <div style={{ display: 'flex', gap: '8px', marginBottom: '8px' }}>
                <input
                  className="input"
                  placeholder="Add location…"
                  value={locationInput}
                  onChange={(e) => setLocationInput(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key !== 'Enter') return
                    e.preventDefault()
                    const l = locationInput.trim()
                    if (l && !draft.locations.includes(l)) update({ locations: [...draft.locations, l] })
                    setLocationInput('')
                  }}
                  style={{ flex: 1 }}
                />
                <button
                  className="btn btn-ghost btn-sm"
                  onClick={() => {
                    const l = locationInput.trim()
                    if (l && !draft.locations.includes(l)) update({ locations: [...draft.locations, l] })
                    setLocationInput('')
                  }}
                  style={{ height: '44px', padding: '0 14px', flexShrink: 0 }}
                >
                  <Plus size={16} />
                </button>
              </div>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px' }}>
                {draft.locations.map((l) => (
                  <button key={l} onClick={() => update({ locations: draft.locations.filter((x) => x !== l) })} style={{ display: 'inline-flex', alignItems: 'center', gap: '4px', cursor: 'pointer', background: 'none', border: '1px solid var(--border)', color: 'var(--fg-2)', borderRadius: '4px', padding: '3px 8px', fontSize: '12px' }} aria-label={`Remove ${l}`}>
                    {l} <X size={12} />
                  </button>
                ))}
              </div>
            </Card>

            <Card>
              <label style={{ fontSize: '12px', fontWeight: 600, color: 'var(--fg-2)', display: 'block', marginBottom: '10px' }}>Work type</label>
              <div className="toggle-pill">
                {(['Remote OK', 'On-site only'] as const).map((label) => {
                  const val = label === 'Remote OK'
                  return (
                    <button key={label} onClick={() => update({ remote_ok: val })} className={draft.remote_ok === val ? 'active' : ''} aria-pressed={draft.remote_ok === val}>
                      {label}
                    </button>
                  )
                })}
              </div>
            </Card>

            <Card>
              <label style={{ fontSize: '12px', fontWeight: 600, color: 'var(--fg-2)', display: 'block', marginBottom: '8px' }}>
                Excluded keywords
              </label>
              <div style={{ display: 'flex', gap: '8px', marginBottom: '8px' }}>
                <input
                  className="input"
                  placeholder="e.g. .NET, PHP"
                  value={keywordInput}
                  onChange={(e) => setKeywordInput(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key !== 'Enter') return
                    e.preventDefault()
                    const k = keywordInput.trim()
                    if (k && !draft.excluded_keywords.includes(k)) update({ excluded_keywords: [...draft.excluded_keywords, k] })
                    setKeywordInput('')
                  }}
                  style={{ flex: 1 }}
                />
                <button
                  className="btn btn-ghost btn-sm"
                  onClick={() => {
                    const k = keywordInput.trim()
                    if (k && !draft.excluded_keywords.includes(k)) update({ excluded_keywords: [...draft.excluded_keywords, k] })
                    setKeywordInput('')
                  }}
                  style={{ height: '44px', padding: '0 14px', flexShrink: 0 }}
                >
                  <Plus size={16} />
                </button>
              </div>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px' }}>
                {draft.excluded_keywords.map((k) => (
                  <button key={k} onClick={() => update({ excluded_keywords: draft.excluded_keywords.filter((x) => x !== k) })} style={{ display: 'inline-flex', alignItems: 'center', gap: '4px', cursor: 'pointer', background: 'none', border: '1px solid rgba(239,68,68,0.3)', color: 'var(--red)', borderRadius: '4px', padding: '3px 8px', fontSize: '12px' }} aria-label={`Remove ${k}`}>
                    {k} <X size={12} />
                  </button>
                ))}
              </div>
            </Card>

            <Card>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '8px' }}>
                <label style={{ fontSize: '12px', fontWeight: 600, color: 'var(--fg-2)' }}>Tailor threshold</label>
                <span style={{ fontFamily: 'var(--font-head)', fontSize: '13px', fontWeight: 700, color: 'var(--accent)' }}>
                  {Math.round(draft.tailor_threshold * 100)}%
                </span>
              </div>
              <input
                type="range" min={0.1} max={0.9} step={0.05}
                value={draft.tailor_threshold}
                onChange={(e) => update({ tailor_threshold: Number(e.target.value) })}
                style={{ width: '100%', accentColor: 'var(--accent)', height: '4px', cursor: 'pointer' }}
                aria-label="Tailor threshold"
              />
              <p style={{ fontSize: '11px', color: 'var(--fg-3)', marginTop: '6px' }}>
                Jobs scoring above this get a tailored resume. Lower = more resumes generated.
              </p>
            </Card>

            {/* ── System ── */}
            <SectionHeader title="System" />

            <Card>
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '12px', flexWrap: 'wrap' }}>
                <div>
                  <p style={{ fontSize: '13px', fontWeight: 600, marginBottom: '2px' }}>Fetch jobs now</p>
                  <p style={{ fontSize: '12px', color: 'var(--fg-3)' }}>
                    {stats?.last_fetch_at
                      ? `Last run: ${new Date(stats.last_fetch_at).toLocaleString('en-IN')}`
                      : 'Never run'}
                  </p>
                </div>
                <button
                  className="btn btn-ghost btn-sm"
                  onClick={() => fetchMutation.mutate()}
                  disabled={fetchMutation.isPending}
                  style={{ flexShrink: 0 }}
                >
                  {fetchMutation.isPending
                    ? <><Loader2 size={13} style={{ animation: 'spin 0.7s linear infinite' }} /> Fetching…</>
                    : <><RefreshCw size={13} /> Trigger fetch</>}
                </button>
              </div>
              {stats && (
                <div style={{ marginTop: '12px', display: 'flex', flexWrap: 'wrap', gap: '8px' }}>
                  <span className="chip chip-neutral">Queue: {stats.queue_depth}</span>
                  <span className="chip chip-neutral">Tailoring: {stats.tailoring_in_progress}</span>
                  {stats.tailoring_failed > 0 && (
                    <span className="chip chip-missing">
                      <AlertTriangle size={10} style={{ marginRight: '3px' }} />
                      {stats.tailoring_failed} failed
                    </span>
                  )}
                </div>
              )}
            </Card>

            {stats && (
              <Card>
                <p style={{ fontSize: '12px', fontWeight: 600, color: 'var(--fg-2)', marginBottom: '12px' }}>LLM cost</p>
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: '12px' }}>
                  {[
                    { label: 'Total tokens', value: (stats.cumulative_prompt_tokens + stats.cumulative_completion_tokens).toLocaleString() },
                    { label: 'Prompt', value: stats.cumulative_prompt_tokens.toLocaleString() },
                    { label: 'Completion', value: stats.cumulative_completion_tokens.toLocaleString() },
                  ].map(({ label, value }) => (
                    <div key={label} style={{ padding: '10px', background: 'var(--bg-3)', borderRadius: '8px' }}>
                      <p style={{ fontFamily: 'var(--font-head)', fontSize: '15px', fontWeight: 700, marginBottom: '2px' }}>{value}</p>
                      <p style={{ fontSize: '11px', color: 'var(--fg-3)' }}>{label}</p>
                    </div>
                  ))}
                </div>
              </Card>
            )}

            <Card>
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '12px', marginBottom: '8px', flexWrap: 'wrap' }}>
                <div>
                  <p style={{ fontSize: '13px', fontWeight: 600, marginBottom: '2px' }}>Resume</p>
                  {resume ? (
                    <p style={{ fontSize: '12px', color: 'var(--fg-3)' }}>
                      {resume.parsed_skills.length} skills · uploaded {new Date(resume.uploaded_at).toLocaleDateString('en-IN')}
                    </p>
                  ) : (
                    <p style={{ fontSize: '12px', color: 'var(--fg-3)' }}>No resume uploaded</p>
                  )}
                </div>
                <button className="btn btn-ghost btn-sm" onClick={() => fileRef.current?.click()} disabled={uploading} style={{ flexShrink: 0 }}>
                  {uploading
                    ? <><Loader2 size={13} style={{ animation: 'spin 0.7s linear infinite' }} /> Uploading…</>
                    : <><Upload size={13} /> Replace .tex</>}
                </button>
                <input ref={fileRef} type="file" accept=".tex,text/plain,application/x-tex" style={{ display: 'none' }} onChange={(e) => { const f = e.target.files?.[0]; if (f) uploadResume(f) }} />
              </div>
              {uploadOk && <p style={{ fontSize: '12px', color: 'var(--green)', display: 'flex', alignItems: 'center', gap: '4px' }}><Check size={12} /> Resume updated</p>}
              {uploadError && <p style={{ fontSize: '12px', color: 'var(--red)' }}>{uploadError}</p>}
              {resume && resume.parsed_skills.length > 0 && (
                <div style={{ marginTop: '8px', display: 'flex', flexWrap: 'wrap', gap: '5px' }}>
                  {resume.parsed_skills.slice(0, 12).map((s) => <span key={s} className="chip chip-match">{s}</span>)}
                  {resume.parsed_skills.length > 12 && <span className="chip chip-neutral">+{resume.parsed_skills.length - 12} more</span>}
                </div>
              )}
            </Card>

            <Card>
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                <div>
                  <p style={{ fontSize: '13px', fontWeight: 600, marginBottom: '2px' }}>Account</p>
                  <p style={{ fontSize: '12px', color: 'var(--fg-3)' }}>{session?.user?.email}</p>
                </div>
                <button
                  className="btn btn-ghost btn-sm"
                  onClick={() => signOut({ callbackUrl: '/auth/signin' })}
                  style={{ color: 'var(--red)', borderColor: 'rgba(239,68,68,0.3)', flexShrink: 0 }}
                >
                  Sign out
                </button>
              </div>
            </Card>

            <div style={{ height: '24px' }} />
          </motion.div>
        </div>
      </main>
    </div>
  )
}
