'use client'

import { useState, useRef } from 'react'
import { useSession, signOut } from 'next-auth/react'
import { useRouter } from 'next/navigation'
import { motion, AnimatePresence } from 'framer-motion'
import { apiFetch, ApiError } from '@/lib/api'
import type { ResumeMetadata } from '@/lib/types'
import { Upload, Check, ChevronRight, ChevronLeft, Plus, X, Briefcase, Loader2, AlertCircle } from 'lucide-react'

type Step = 1 | 2 | 3

interface WizardState {
  parsedSkills: string[]
  resumeUploaded: boolean
  roles: string[]
  experienceYears: number
  locations: string[]
  remoteOk: boolean
}

const STEP_LABELS = ['Resume', 'Preferences', 'Launch']

function ProgressBar({ step }: { step: Step }) {
  const pct = ((step - 1) / 2) * 100
  return (
    <div style={{ marginBottom: '32px' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '12px' }}>
        {STEP_LABELS.map((label, i) => {
          const n = (i + 1) as Step
          const done = n < step
          const active = n === step
          return (
            <div key={label} style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '4px' }}>
              <div style={{
                width: '24px', height: '24px', borderRadius: '50%',
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                background: done ? 'var(--green)' : active ? 'var(--accent)' : 'var(--bg-3)',
                border: `1px solid ${done ? 'var(--green)' : active ? 'var(--accent)' : 'var(--border)'}`,
                transition: 'all 300ms ease',
                fontSize: '10px', fontWeight: 700, color: done || active ? '#fff' : 'var(--fg-3)',
                fontFamily: 'var(--font-head)',
              }}>
                {done ? <Check size={12} strokeWidth={3} /> : n}
              </div>
              <span style={{
                fontSize: '10px', fontWeight: 500,
                color: active ? 'var(--fg)' : 'var(--fg-3)',
                transition: 'color 300ms ease',
              }}>
                {label}
              </span>
            </div>
          )
        })}
      </div>
      <div style={{ height: '2px', background: 'var(--bg-3)', borderRadius: '1px', overflow: 'hidden' }}>
        <motion.div
          style={{ height: '100%', background: 'var(--accent)', borderRadius: '1px' }}
          animate={{ width: `${pct}%` }}
          transition={{ duration: 0.4, ease: 'easeOut' }}
        />
      </div>
    </div>
  )
}

const stepVariants = {
  enter: (dir: number) => ({ x: dir * 36, opacity: 0 }),
  center: { x: 0, opacity: 1 },
  exit: (dir: number) => ({ x: dir * -36, opacity: 0 }),
}

const stepTransition = { duration: 0.22, ease: 'easeInOut' as const }

export default function OnboardingPage() {
  const { data: session, status } = useSession()
  const router = useRouter()

  const [step, setStep] = useState<Step>(1)
  const [dir, setDir] = useState(1)
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
  const fileRef = useRef<HTMLInputElement>(null)

  function goNext() { setDir(1); setStep((s) => (s + 1) as Step) }
  function goBack() { setDir(-1); setStep((s) => (s - 1) as Step) }

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
    if (file.size > 2 * 1024 * 1024) {
      setUploadError('File too large — max 2MB')
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
      if (e instanceof ApiError && e.status === 401) {
        signOut({ callbackUrl: '/auth/signin' })
        return
      }
      setUploadError(
        e instanceof ApiError
          ? e.code === 'RESUME_INVALID_LATEX'
            ? 'Invalid LaTeX — needs \\begin{document} and \\end{document}'
            : e.code === 'RESUME_TOO_LARGE'
              ? 'File too large (max 2MB)'
              : e.message
          : 'Upload failed. Try again.'
      )
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
      await apiFetch('/api/fetch/trigger', idToken, { method: 'POST' }).catch(() => {})
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) {
        signOut({ callbackUrl: '/auth/signin' })
        return
      }
    }
    router.push('/dashboard')
  }

  return (
    <main style={{
      minHeight: '100dvh',
      background: 'var(--bg)',
      display: 'flex',
      flexDirection: 'column',
      alignItems: 'center',
      justifyContent: 'center',
      padding: '24px 16px',
      position: 'relative',
      overflow: 'hidden',
    }}>
      {/* Ambient background */}
      <div style={{
        position: 'absolute', inset: 0,
        background: 'radial-gradient(ellipse 80% 50% at 50% -10%, rgba(59,130,246,0.09) 0%, transparent 60%)',
        pointerEvents: 'none',
      }} />
      <div className="orb orb-1" />
      <div className="orb orb-2" />

      {/* Brand */}
      <motion.div
        initial={{ opacity: 0, y: -8 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.4 }}
        style={{ textAlign: 'center', marginBottom: '28px', position: 'relative', zIndex: 1 }}
      >
        <h1 style={{ fontFamily: 'var(--font-head)', fontSize: '21px', fontWeight: 700, letterSpacing: '-0.03em', marginBottom: '4px' }}>
          GetJobbed
        </h1>
        <p style={{ fontSize: '13px', color: 'var(--fg-3)' }}>Set up in under 2 minutes</p>
      </motion.div>

      {/* Card */}
      <motion.div
        initial={{ opacity: 0, y: 16 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.4, delay: 0.08 }}
        className="card-glass"
        style={{ width: '100%', maxWidth: '480px', padding: '28px 28px 24px', position: 'relative', zIndex: 1, overflow: 'hidden' }}
      >
        <ProgressBar step={step} />

        <AnimatePresence mode="wait" custom={dir}>
          <motion.div
            key={step}
            custom={dir}
            variants={stepVariants}
            initial="enter"
            animate="center"
            exit="exit"
            transition={stepTransition}
          >
            {step === 1 && (
              <Step1Content
                isDragOver={isDragOver}
                uploading={uploading}
                uploadError={uploadError}
                parsedSkills={state.parsedSkills}
                onDragOver={() => setIsDragOver(true)}
                onDragLeave={() => setIsDragOver(false)}
                onDrop={uploadResume}
                onFileChange={uploadResume}
                fileRef={fileRef}
                onNext={goNext}
              />
            )}
            {step === 2 && (
              <Step2Content
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
                onBack={goBack}
                onNext={goNext}
              />
            )}
            {step === 3 && (
              <Step3Content
                parsedSkills={state.parsedSkills}
                roles={state.roles}
                experienceYears={state.experienceYears}
                remoteOk={state.remoteOk}
                saving={saving}
                onBack={goBack}
                onStart={handleStart}
              />
            )}
          </motion.div>
        </AnimatePresence>
      </motion.div>
    </main>
  )
}

// ── Step 1 ────────────────────────────────────────────────────────────────────

function Step1Content({
  isDragOver, uploading, uploadError, parsedSkills,
  onDrop, onDragOver, onDragLeave, onFileChange, fileRef, onNext,
}: {
  isDragOver: boolean; uploading: boolean; uploadError: string | null
  parsedSkills: string[]; onDrop: (f: File) => void
  onDragOver: () => void; onDragLeave: () => void
  onFileChange: (f: File) => void
  fileRef: React.RefObject<HTMLInputElement | null>
  onNext: () => void
}) {
  return (
    <div>
      <p style={{ fontSize: '11px', fontFamily: 'var(--font-head)', color: 'var(--accent)', letterSpacing: '0.1em', textTransform: 'uppercase', marginBottom: '4px' }}>Step 1</p>
      <h2 style={{ fontFamily: 'var(--font-head)', fontSize: '17px', fontWeight: 700, marginBottom: '4px' }}>Upload your resume</h2>
      <p style={{ fontSize: '13px', color: 'var(--fg-3)', marginBottom: '20px' }}>LaTeX <code>.tex</code> file only — max 2MB</p>

      <div
        onClick={() => fileRef.current?.click()}
        onDragOver={(e) => { e.preventDefault(); onDragOver() }}
        onDragLeave={onDragLeave}
        onDrop={(e) => { e.preventDefault(); onDragLeave(); const f = e.dataTransfer.files[0]; if (f) onDrop(f) }}
        style={{
          border: `2px dashed ${isDragOver ? 'var(--accent)' : uploadError ? 'var(--red)' : 'var(--border)'}`,
          borderRadius: '12px',
          padding: '28px 16px',
          textAlign: 'center',
          cursor: 'pointer',
          background: isDragOver ? 'rgba(59,130,246,0.04)' : 'rgba(255,255,255,0.02)',
          transition: 'all 200ms ease',
          marginBottom: '4px',
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
            <Loader2 size={26} color="var(--accent)" style={{ animation: 'spin 0.7s linear infinite' }} />
            <p style={{ fontSize: '13px', color: 'var(--fg-2)' }}>Parsing resume…</p>
          </div>
        ) : parsedSkills.length > 0 ? (
          <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '8px' }}>
            <div style={{ width: '36px', height: '36px', borderRadius: '50%', background: 'rgba(34,197,94,0.15)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
              <Check size={18} color="var(--green)" />
            </div>
            <p style={{ fontSize: '13px', color: 'var(--green)', fontWeight: 500 }}>{parsedSkills.length} skills detected</p>
            <p style={{ fontSize: '12px', color: 'var(--fg-3)' }}>Click to replace</p>
          </div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '10px' }}>
            <Upload size={26} color="var(--fg-3)" />
            <div>
              <p style={{ fontSize: '14px', color: 'var(--fg)', fontWeight: 500, marginBottom: '2px' }}>Drop your .tex file here</p>
              <p style={{ fontSize: '12px', color: 'var(--fg-3)' }}>or click to browse</p>
            </div>
          </div>
        )}
      </div>

      {uploadError && (
        <p style={{ fontSize: '12px', color: 'var(--red)', marginBottom: '12px', display: 'flex', alignItems: 'center', gap: '5px', paddingTop: '6px' }}>
          <AlertCircle size={12} /> {uploadError}
        </p>
      )}

      {parsedSkills.length > 0 && (
        <div style={{ marginTop: '16px', marginBottom: '4px' }}>
          <p style={{ fontSize: '11px', color: 'var(--fg-3)', marginBottom: '8px', textTransform: 'uppercase', letterSpacing: '0.06em' }}>Detected skills</p>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: '5px' }}>
            {parsedSkills.slice(0, 18).map((s) => <span key={s} className="chip chip-match">{s}</span>)}
            {parsedSkills.length > 18 && <span className="chip chip-neutral">+{parsedSkills.length - 18}</span>}
          </div>
        </div>
      )}

      <div style={{ marginTop: '24px' }}>
        <button className="btn btn-accent btn-full" disabled={parsedSkills.length === 0} onClick={onNext}>
          Continue <ChevronRight size={16} />
        </button>
      </div>
    </div>
  )
}

// ── Step 2 ────────────────────────────────────────────────────────────────────

function Step2Content({
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
      <p style={{ fontSize: '11px', fontFamily: 'var(--font-head)', color: 'var(--accent)', letterSpacing: '0.1em', textTransform: 'uppercase', marginBottom: '4px' }}>Step 2</p>
      <h2 style={{ fontFamily: 'var(--font-head)', fontSize: '17px', fontWeight: 700, marginBottom: '4px' }}>Your preferences</h2>
      <p style={{ fontSize: '13px', color: 'var(--fg-3)', marginBottom: '20px' }}>What roles should we search for?</p>

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
          />
          <button className="btn btn-ghost btn-sm" onClick={onAddRole} style={{ height: '44px', padding: '0 14px', flexShrink: 0 }}>
            <Plus size={15} />
          </button>
        </div>
        {roles.length > 0 && (
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px' }}>
            {roles.map((r) => (
              <button key={r} onClick={() => onRemoveRole(r)} className="chip chip-blue" style={{ display: 'inline-flex', alignItems: 'center', gap: '4px', cursor: 'pointer', background: 'none', border: '1px solid rgba(59,130,246,0.3)', color: 'var(--accent)', borderRadius: '4px', padding: '3px 8px', fontSize: '12px' }}>
                {r} <X size={11} />
              </button>
            ))}
          </div>
        )}
      </div>

      {/* Experience */}
      <div style={{ marginBottom: '18px' }}>
        <label style={{ fontSize: '12px', fontWeight: 600, color: 'var(--fg-2)', display: 'block', marginBottom: '8px' }}>
          Experience — <span style={{ fontFamily: 'var(--font-head)', color: 'var(--fg)' }}>{experienceYears} yr{experienceYears !== 1 ? 's' : ''}</span>
        </label>
        <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
          <button className="btn btn-ghost btn-sm" onClick={() => onExpChange(Math.max(0, experienceYears - 1))} style={{ width: '40px', height: '40px', padding: 0, flexShrink: 0 }}>−</button>
          <input
            type="range" min={0} max={10} step={1} value={experienceYears}
            onChange={(e) => onExpChange(Number(e.target.value))}
            style={{ flex: 1, accentColor: 'var(--accent)', cursor: 'pointer' }}
          />
          <button className="btn btn-ghost btn-sm" onClick={() => onExpChange(Math.min(10, experienceYears + 1))} style={{ width: '40px', height: '40px', padding: 0, flexShrink: 0 }}>+</button>
        </div>
      </div>

      {/* Locations */}
      <div style={{ marginBottom: '18px' }}>
        <label style={{ fontSize: '12px', fontWeight: 600, color: 'var(--fg-2)', display: 'block', marginBottom: '6px' }}>
          Preferred locations <span style={{ fontWeight: 400, color: 'var(--fg-3)' }}>(optional)</span>
        </label>
        <div style={{ display: 'flex', gap: '8px', marginBottom: '8px' }}>
          <input className="input" placeholder="e.g. Bangalore, Remote" value={locationInput} onChange={(e) => onLocationInputChange(e.target.value)} onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); onAddLocation() } }} style={{ flex: 1 }} />
          <button className="btn btn-ghost btn-sm" onClick={onAddLocation} style={{ height: '44px', padding: '0 14px', flexShrink: 0 }}><Plus size={15} /></button>
        </div>
        {locations.length > 0 && (
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px' }}>
            {locations.map((l) => (
              <button key={l} onClick={() => onRemoveLocation(l)} className="chip chip-neutral" style={{ display: 'inline-flex', alignItems: 'center', gap: '4px', cursor: 'pointer', background: 'none', border: '1px solid var(--border)', color: 'var(--fg-2)', borderRadius: '4px', padding: '3px 8px', fontSize: '12px' }}>
                {l} <X size={11} />
              </button>
            ))}
          </div>
        )}
      </div>

      {/* Remote */}
      <div style={{ marginBottom: '24px' }}>
        <label style={{ fontSize: '12px', fontWeight: 600, color: 'var(--fg-2)', display: 'block', marginBottom: '8px' }}>Work type</label>
        <div className="toggle-pill">
          {(['Remote OK', 'On-site only'] as const).map((label) => {
            const val = label === 'Remote OK'
            return (
              <button key={label} onClick={() => onRemoteChange(val)} className={remoteOk === val ? 'active' : ''} aria-pressed={remoteOk === val}>
                {label}
              </button>
            )
          })}
        </div>
      </div>

      <div style={{ display: 'flex', gap: '8px' }}>
        <button className="btn btn-ghost" onClick={onBack} style={{ flexShrink: 0, padding: '0 14px' }}><ChevronLeft size={16} /></button>
        <button className="btn btn-accent btn-full" disabled={roles.length === 0} onClick={onNext}>
          Continue <ChevronRight size={16} />
        </button>
      </div>
    </div>
  )
}

// ── Step 3 ────────────────────────────────────────────────────────────────────

function Step3Content({
  parsedSkills, roles, experienceYears, remoteOk, saving, onBack, onStart,
}: {
  parsedSkills: string[]; roles: string[]; experienceYears: number
  remoteOk: boolean; saving: boolean; onBack: () => void; onStart: () => void
}) {
  return (
    <div>
      <p style={{ fontSize: '11px', fontFamily: 'var(--font-head)', color: 'var(--accent)', letterSpacing: '0.1em', textTransform: 'uppercase', marginBottom: '4px' }}>Step 3</p>
      <h2 style={{ fontFamily: 'var(--font-head)', fontSize: '17px', fontWeight: 700, marginBottom: '4px' }}>Ready to launch</h2>
      <p style={{ fontSize: '13px', color: 'var(--fg-3)', marginBottom: '20px' }}>We'll start scanning jobs right after you hit launch.</p>

      <div style={{ background: 'rgba(255,255,255,0.02)', border: '1px solid var(--border)', borderRadius: '10px', padding: '16px', marginBottom: '20px', display: 'flex', flexDirection: 'column', gap: '10px' }}>
        {[
          { label: 'Resume skills', value: `${parsedSkills.length} detected`, accent: true },
          { label: 'Target roles', value: roles.join(', ') || '—' },
          { label: 'Experience', value: `${experienceYears} yr${experienceYears !== 1 ? 's' : ''}` },
          { label: 'Work type', value: remoteOk ? 'Remote + on-site' : 'On-site only' },
          { label: 'Tailor threshold', value: '40% (default)' },
        ].map(({ label, value, accent }) => (
          <div key={label} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '12px' }}>
            <span style={{ fontSize: '12px', color: 'var(--fg-3)' }}>{label}</span>
            <span style={{ fontSize: '13px', fontWeight: 500, color: accent ? 'var(--green)' : 'var(--fg)', textAlign: 'right' }}>{value}</span>
          </div>
        ))}
      </div>

      <div style={{ display: 'flex', gap: '8px' }}>
        <button className="btn btn-ghost" onClick={onBack} disabled={saving} style={{ flexShrink: 0, padding: '0 14px' }}><ChevronLeft size={16} /></button>
        <button className="btn btn-primary btn-full" onClick={onStart} disabled={saving}>
          {saving
            ? <><div className="spinner" style={{ borderTopColor: '#000' }} /> Launching…</>
            : <><Briefcase size={16} /> Launch GetJobbed</>}
        </button>
      </div>
    </div>
  )
}
