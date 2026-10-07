'use client'

import { useState, useRef } from 'react'
import { useSession } from 'next-auth/react'
import { useRouter } from 'next/navigation'
import { apiFetch, ApiError } from '@/lib/api'
import type { ResumeMetadata } from '@/lib/types'
import { Upload, Check, ChevronRight, ChevronLeft, Plus, X, Briefcase, Loader2 } from 'lucide-react'

type Step = 1 | 2 | 3

interface WizardState {
  parsedSkills: string[]
  resumeUploaded: boolean
  roles: string[]
  experienceYears: number
  locations: string[]
  remoteOk: boolean
}

function ProgressDots({ step }: { step: Step }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '28px' }}>
      {([1, 2, 3] as Step[]).map((n) => (
        <div key={n} style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          <div
            style={{
              width: '28px', height: '28px', borderRadius: '50%',
              display: 'flex', alignItems: 'center', justifyContent: 'center',
              fontFamily: 'var(--font-head)', fontSize: '11px', fontWeight: 700,
              background: n < step ? 'var(--green)' : n === step ? 'var(--accent)' : 'var(--bg-3)',
              color: n <= step ? '#fff' : 'var(--fg-3)',
              border: `1px solid ${n < step ? 'var(--green)' : n === step ? 'var(--accent)' : 'var(--border)'}`,
              transition: 'all 250ms ease',
              flexShrink: 0,
            }}
          >
            {n < step ? <Check size={12} strokeWidth={3} /> : n}
          </div>
          {n < 3 && (
            <div style={{
              width: '32px', height: '1px',
              background: n < step ? 'var(--green)' : 'var(--border)',
              transition: 'background 250ms ease',
            }} />
          )}
        </div>
      ))}
    </div>
  )
}

function Step1({
  isDragOver, uploading, uploadError, parsedSkills,
  onDrop, onDragOver, onDragLeave, onFileChange, onNext,
}: {
  isDragOver: boolean; uploading: boolean; uploadError: string | null
  parsedSkills: string[]; onDrop: (f: File) => void
  onDragOver: () => void; onDragLeave: () => void
  onFileChange: (f: File) => void; onNext: () => void
}) {
  const fileRef = useRef<HTMLInputElement>(null)

  return (
    <div>
      <p style={{ fontSize: '11px', fontFamily: 'var(--font-head)', color: 'var(--accent)', letterSpacing: '0.1em', textTransform: 'uppercase', marginBottom: '4px' }}>Step 1 of 3</p>
      <h2 style={{ fontFamily: 'var(--font-head)', fontSize: '18px', fontWeight: 700, marginBottom: '6px' }}>Upload your resume</h2>
      <p style={{ fontSize: '13px', color: 'var(--fg-2)', marginBottom: '20px', lineHeight: 1.6 }}>LaTeX <code style={{ fontSize: '12px' }}>.tex</code> file only — max 2MB</p>

      {/* Drop zone */}
      <div
        onClick={() => fileRef.current?.click()}
        onDragOver={(e) => { e.preventDefault(); onDragOver() }}
        onDragLeave={onDragLeave}
        onDrop={(e) => {
          e.preventDefault(); onDragLeave()
          const f = e.dataTransfer.files[0]
          if (f) onDrop(f)
        }}
        style={{
          border: `2px dashed ${isDragOver ? 'var(--accent)' : uploadError ? 'var(--red)' : 'var(--border)'}`,
          borderRadius: '10px',
          padding: '32px 16px',
          textAlign: 'center',
          cursor: 'pointer',
          background: isDragOver ? 'rgba(59,130,246,0.05)' : 'var(--bg-3)',
          transition: 'all 200ms ease',
          marginBottom: uploadError ? '8px' : '0',
          minHeight: '44px',
        }}
      >
        <input
          ref={fileRef}
          type="file"
          accept=".tex,text/plain,application/x-tex"
          style={{ display: 'none' }}
          onChange={(e) => { const f = e.target.files?.[0]; if (f) onFileChange(f) }}
        />
        {uploading ? (
          <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '10px' }}>
            <Loader2 size={28} color="var(--accent)" style={{ animation: 'spin 0.7s linear infinite' }} />
            <p style={{ fontSize: '13px', color: 'var(--fg-2)' }}>Parsing resume…</p>
          </div>
        ) : parsedSkills.length > 0 ? (
          <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '8px' }}>
            <div style={{ width: '40px', height: '40px', borderRadius: '50%', background: 'rgba(34,197,94,0.15)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
              <Check size={20} color="var(--green)" />
            </div>
            <p style={{ fontSize: '13px', color: 'var(--green)', fontWeight: 500 }}>Resume uploaded — {parsedSkills.length} skills found</p>
            <p style={{ fontSize: '12px', color: 'var(--fg-3)' }}>Click to replace</p>
          </div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '10px' }}>
            <Upload size={28} color="var(--fg-3)" />
            <div>
              <p style={{ fontSize: '14px', color: 'var(--fg)', fontWeight: 500, marginBottom: '2px' }}>Drop your .tex file here</p>
              <p style={{ fontSize: '12px', color: 'var(--fg-3)' }}>or tap to browse</p>
            </div>
          </div>
        )}
      </div>

      {uploadError && (
        <p style={{ fontSize: '12px', color: 'var(--red)', marginTop: '6px', marginBottom: '12px' }}>{uploadError}</p>
      )}

      {/* Parsed skills preview */}
      {parsedSkills.length > 0 && (
        <div style={{ marginTop: '16px', marginBottom: '4px' }}>
          <p style={{ fontSize: '11px', color: 'var(--fg-3)', marginBottom: '8px', textTransform: 'uppercase', letterSpacing: '0.06em' }}>Skills detected</p>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px' }}>
            {parsedSkills.slice(0, 20).map((s) => (
              <span key={s} className="chip chip-match">{s}</span>
            ))}
            {parsedSkills.length > 20 && (
              <span className="chip chip-neutral">+{parsedSkills.length - 20} more</span>
            )}
          </div>
        </div>
      )}

      <div style={{ marginTop: '24px' }}>
        <button
          className="btn btn-accent btn-full"
          disabled={parsedSkills.length === 0}
          onClick={onNext}
          aria-label="Continue to step 2"
        >
          Continue <ChevronRight size={16} />
        </button>
      </div>
    </div>
  )
}

function Step2({
  roles, roleInput, experienceYears, locations, locationInput, remoteOk,
  onRoleInputChange, onAddRole, onRemoveRole,
  onExpChange, onLocationInputChange, onAddLocation, onRemoveLocation,
  onRemoteChange, onBack, onNext,
}: {
  roles: string[]; roleInput: string; experienceYears: number
  locations: string[]; locationInput: string; remoteOk: boolean
  onRoleInputChange: (v: string) => void; onAddRole: () => void; onRemoveRole: (r: string) => void
  onExpChange: (v: number) => void; onLocationInputChange: (v: string) => void
  onAddLocation: () => void; onRemoveLocation: (l: string) => void
  onRemoteChange: (v: boolean) => void; onBack: () => void; onNext: () => void
}) {
  return (
    <div>
      <p style={{ fontSize: '11px', fontFamily: 'var(--font-head)', color: 'var(--accent)', letterSpacing: '0.1em', textTransform: 'uppercase', marginBottom: '4px' }}>Step 2 of 3</p>
      <h2 style={{ fontFamily: 'var(--font-head)', fontSize: '18px', fontWeight: 700, marginBottom: '6px' }}>Your preferences</h2>
      <p style={{ fontSize: '13px', color: 'var(--fg-2)', marginBottom: '20px', lineHeight: 1.6 }}>Tell us what roles and locations to search</p>

      {/* Roles */}
      <div style={{ marginBottom: '18px' }}>
        <label style={{ fontSize: '12px', fontWeight: 600, color: 'var(--fg-2)', display: 'block', marginBottom: '6px' }}>
          Target roles <span style={{ color: 'var(--red)' }}>*</span>
        </label>
        <div style={{ display: 'flex', gap: '8px', marginBottom: '8px' }}>
          <input
            className="input"
            placeholder="e.g. Data Engineer, Backend SDE"
            value={roleInput}
            onChange={(e) => onRoleInputChange(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); onAddRole() } }}
            style={{ flex: 1 }}
            aria-label="Add a target role"
          />
          <button className="btn btn-ghost btn-sm" onClick={onAddRole} aria-label="Add role" style={{ flexShrink: 0, height: '44px', padding: '0 14px' }}>
            <Plus size={16} />
          </button>
        </div>
        {roles.length > 0 && (
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px' }}>
            {roles.map((r) => (
              <button
                key={r}
                className="chip chip-blue"
                onClick={() => onRemoveRole(r)}
                style={{ display: 'inline-flex', alignItems: 'center', gap: '4px', cursor: 'pointer', background: 'none', border: '1px solid rgba(59,130,246,0.3)', color: 'var(--accent)', borderRadius: '4px', padding: '3px 8px', fontSize: '12px' }}
                aria-label={`Remove ${r}`}
              >
                {r} <X size={12} />
              </button>
            ))}
          </div>
        )}
      </div>

      {/* Experience */}
      <div style={{ marginBottom: '18px' }}>
        <label style={{ fontSize: '12px', fontWeight: 600, color: 'var(--fg-2)', display: 'block', marginBottom: '6px' }}>
          Years of experience
        </label>
        <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
          <button
            className="btn btn-ghost btn-sm"
            onClick={() => onExpChange(Math.max(0, experienceYears - 1))}
            aria-label="Decrease experience years"
            style={{ width: '44px', height: '44px', padding: 0, flexShrink: 0 }}
          >−</button>
          <span style={{ fontFamily: 'var(--font-head)', fontSize: '20px', fontWeight: 700, minWidth: '40px', textAlign: 'center' }}>
            {experienceYears}
          </span>
          <button
            className="btn btn-ghost btn-sm"
            onClick={() => onExpChange(Math.min(10, experienceYears + 1))}
            aria-label="Increase experience years"
            style={{ width: '44px', height: '44px', padding: 0, flexShrink: 0 }}
          >+</button>
          <span style={{ fontSize: '13px', color: 'var(--fg-3)' }}>year{experienceYears !== 1 ? 's' : ''}</span>
        </div>
      </div>

      {/* Locations */}
      <div style={{ marginBottom: '18px' }}>
        <label style={{ fontSize: '12px', fontWeight: 600, color: 'var(--fg-2)', display: 'block', marginBottom: '6px' }}>
          Preferred locations <span style={{ color: 'var(--fg-3)', fontWeight: 400 }}>(optional)</span>
        </label>
        <div style={{ display: 'flex', gap: '8px', marginBottom: '8px' }}>
          <input
            className="input"
            placeholder="e.g. Bangalore, Mumbai"
            value={locationInput}
            onChange={(e) => onLocationInputChange(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); onAddLocation() } }}
            style={{ flex: 1 }}
            aria-label="Add a preferred location"
          />
          <button className="btn btn-ghost btn-sm" onClick={onAddLocation} style={{ flexShrink: 0, height: '44px', padding: '0 14px' }}>
            <Plus size={16} />
          </button>
        </div>
        {locations.length > 0 && (
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px' }}>
            {locations.map((l) => (
              <button
                key={l}
                onClick={() => onRemoveLocation(l)}
                className="chip chip-neutral"
                style={{ display: 'inline-flex', alignItems: 'center', gap: '4px', cursor: 'pointer', background: 'none', border: '1px solid var(--border)', color: 'var(--fg-2)', borderRadius: '4px', padding: '3px 8px', fontSize: '12px' }}
                aria-label={`Remove ${l}`}
              >
                {l} <X size={12} />
              </button>
            ))}
          </div>
        )}
      </div>

      {/* Remote */}
      <div style={{ marginBottom: '24px' }}>
        <label style={{ fontSize: '12px', fontWeight: 600, color: 'var(--fg-2)', display: 'block', marginBottom: '8px' }}>Work type</label>
        <div style={{ display: 'flex', gap: '8px' }}>
          {(['Remote OK', 'On-site only'] as const).map((label) => {
            const val = label === 'Remote OK'
            const active = remoteOk === val
            return (
              <button
                key={label}
                onClick={() => onRemoteChange(val)}
                style={{
                  flex: 1, height: '44px', borderRadius: '8px', border: `1px solid ${active ? 'var(--accent)' : 'var(--border)'}`,
                  background: active ? 'rgba(59,130,246,0.12)' : 'var(--bg-3)',
                  color: active ? 'var(--accent)' : 'var(--fg-2)',
                  fontFamily: 'var(--font-body)', fontSize: '13px', fontWeight: 500,
                  cursor: 'pointer', transition: 'all 150ms ease',
                }}
                aria-pressed={active}
              >
                {label}
              </button>
            )
          })}
        </div>
      </div>

      <div style={{ display: 'flex', gap: '8px' }}>
        <button className="btn btn-ghost" onClick={onBack} aria-label="Back to step 1" style={{ flex: '0 0 auto', paddingLeft: '14px', paddingRight: '14px' }}>
          <ChevronLeft size={16} />
        </button>
        <button
          className="btn btn-accent btn-full"
          disabled={roles.length === 0}
          onClick={onNext}
          aria-label="Continue to step 3"
        >
          Continue <ChevronRight size={16} />
        </button>
      </div>
    </div>
  )
}

function Step3({
  parsedSkills, roles, experienceYears, remoteOk, saving,
  onBack, onStart,
}: {
  parsedSkills: string[]; roles: string[]; experienceYears: number
  remoteOk: boolean; saving: boolean; onBack: () => void; onStart: () => void
}) {
  return (
    <div>
      <p style={{ fontSize: '11px', fontFamily: 'var(--font-head)', color: 'var(--accent)', letterSpacing: '0.1em', textTransform: 'uppercase', marginBottom: '4px' }}>Step 3 of 3</p>
      <h2 style={{ fontFamily: 'var(--font-head)', fontSize: '18px', fontWeight: 700, marginBottom: '6px' }}>Looks good — ready to go</h2>
      <p style={{ fontSize: '13px', color: 'var(--fg-2)', marginBottom: '20px', lineHeight: 1.6 }}>We'll start fetching jobs right after you launch.</p>

      <div style={{ background: 'var(--bg-3)', border: '1px solid var(--border)', borderRadius: '10px', padding: '16px', marginBottom: '20px', display: 'flex', flexDirection: 'column', gap: '10px' }}>
        <Row label="Resume skills" value={`${parsedSkills.length} detected`} good />
        <Row label="Target roles" value={roles.join(', ')} />
        <Row label="Experience" value={`${experienceYears} year${experienceYears !== 1 ? 's' : ''}`} />
        <Row label="Work type" value={remoteOk ? 'Remote + on-site' : 'On-site only'} />
        <Row label="Tailor threshold" value="0.40 (default)" />
      </div>

      <div style={{ display: 'flex', gap: '8px' }}>
        <button className="btn btn-ghost" onClick={onBack} disabled={saving} style={{ flex: '0 0 auto', paddingLeft: '14px', paddingRight: '14px' }}>
          <ChevronLeft size={16} />
        </button>
        <button
          className="btn btn-primary btn-full"
          onClick={onStart}
          disabled={saving}
          aria-label="Save settings and start fetching jobs"
        >
          {saving ? (
            <><div className="spinner" style={{ borderTopColor: '#000' }} /> Starting…</>
          ) : (
            <><Briefcase size={16} /> Start fetching jobs</>
          )}
        </button>
      </div>
    </div>
  )
}

function Row({ label, value, good }: { label: string; value: string; good?: boolean }) {
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '12px' }}>
      <span style={{ fontSize: '12px', color: 'var(--fg-3)' }}>{label}</span>
      <span style={{ fontSize: '13px', fontWeight: 500, color: good ? 'var(--green)' : 'var(--fg)', textAlign: 'right' }}>{value}</span>
    </div>
  )
}

export default function OnboardingPage() {
  const { data: session, status } = useSession()
  const router = useRouter()

  const [step, setStep] = useState<Step>(1)
  const [state, setState] = useState<WizardState>({
    parsedSkills: [], resumeUploaded: false,
    roles: [], experienceYears: 1,
    locations: [], remoteOk: true,
  })
  const [roleInput, setRoleInput] = useState('')
  const [locationInput, setLocationInput] = useState('')
  const [uploading, setUploading] = useState(false)
  const [uploadError, setUploadError] = useState<string | null>(null)
  const [isDragOver, setIsDragOver] = useState(false)
  const [saving, setSaving] = useState(false)

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

  const idToken = session?.id_token ?? ''

  async function uploadResume(file: File) {
    if (!file.name.endsWith('.tex') && file.type !== 'text/plain' && file.type !== 'application/x-tex') {
      setUploadError('Please upload a .tex file')
      return
    }
    setUploading(true)
    setUploadError(null)
    try {
      const text = await file.text()
      const res = await apiFetch<ResumeMetadata>('/api/resume', idToken, {
        method: 'POST',
        headers: { 'Content-Type': 'text/plain' },
        body: text,
      } as RequestInit)
      setState((s) => ({ ...s, parsedSkills: res.parsed_skills, resumeUploaded: true }))
    } catch (e) {
      const msg =
        e instanceof ApiError
          ? e.code === 'RESUME_INVALID_LATEX'
            ? 'Invalid LaTeX — needs \\begin{document} and \\end{document}'
            : e.code === 'RESUME_TOO_LARGE'
              ? 'File too large (max 2MB)'
              : e.message
          : 'Upload failed. Please try again.'
      setUploadError(msg)
    } finally {
      setUploading(false)
    }
  }

  function addRole() {
    const r = roleInput.trim()
    if (r && !state.roles.includes(r) && state.roles.length < 10) {
      setState((s) => ({ ...s, roles: [...s.roles, r] }))
    }
    setRoleInput('')
  }

  function addLocation() {
    const l = locationInput.trim()
    if (l && !state.locations.includes(l)) {
      setState((s) => ({ ...s, locations: [...s.locations, l] }))
    }
    setLocationInput('')
  }

  async function handleStart() {
    setSaving(true)
    try {
      await apiFetch('/api/profile', idToken, {
        method: 'PUT',
        body: JSON.stringify({
          desired_roles: state.roles,
          experience_years: state.experienceYears,
          locations: state.locations,
          remote_ok: state.remoteOk,
        }),
      })
      // Kick off the first fetch (ignore 429 — means already running)
      await apiFetch('/api/fetch/trigger', idToken, { method: 'POST' }).catch(() => {})
    } catch {}
    router.push('/dashboard')
  }

  return (
    <main
      style={{
        minHeight: '100dvh',
        background: 'var(--bg)',
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        justifyContent: 'center',
        padding: '24px 16px',
        paddingBottom: 'calc(24px + env(safe-area-inset-bottom, 0px))',
      }}
    >
      {/* Brand */}
      <div style={{ textAlign: 'center', marginBottom: '32px' }}>
        <h1 style={{ fontFamily: 'var(--font-head)', fontSize: '22px', fontWeight: 700, letterSpacing: '-0.03em', marginBottom: '4px' }}>
          GetJobbed
        </h1>
        <p style={{ fontSize: '13px', color: 'var(--fg-2)' }}>Set up in under 2 minutes</p>
      </div>

      {/* Card */}
      <div
        style={{
          width: '100%', maxWidth: '460px',
          background: 'var(--bg-2)',
          border: '1px solid var(--border)',
          borderRadius: '14px',
          padding: '24px',
        }}
      >
        <ProgressDots step={step} />

        {step === 1 && (
          <Step1
            isDragOver={isDragOver}
            uploading={uploading}
            uploadError={uploadError}
            parsedSkills={state.parsedSkills}
            onDragOver={() => setIsDragOver(true)}
            onDragLeave={() => setIsDragOver(false)}
            onDrop={uploadResume}
            onFileChange={uploadResume}
            onNext={() => setStep(2)}
          />
        )}

        {step === 2 && (
          <Step2
            roles={state.roles}
            roleInput={roleInput}
            experienceYears={state.experienceYears}
            locations={state.locations}
            locationInput={locationInput}
            remoteOk={state.remoteOk}
            onRoleInputChange={setRoleInput}
            onAddRole={addRole}
            onRemoveRole={(r) => setState((s) => ({ ...s, roles: s.roles.filter((x) => x !== r) }))}
            onExpChange={(v) => setState((s) => ({ ...s, experienceYears: v }))}
            onLocationInputChange={setLocationInput}
            onAddLocation={addLocation}
            onRemoveLocation={(l) => setState((s) => ({ ...s, locations: s.locations.filter((x) => x !== l) }))}
            onRemoteChange={(v) => setState((s) => ({ ...s, remoteOk: v }))}
            onBack={() => setStep(1)}
            onNext={() => setStep(3)}
          />
        )}

        {step === 3 && (
          <Step3
            parsedSkills={state.parsedSkills}
            roles={state.roles}
            experienceYears={state.experienceYears}
            remoteOk={state.remoteOk}
            saving={saving}
            onBack={() => setStep(2)}
            onStart={handleStart}
          />
        )}
      </div>
    </main>
  )
}
