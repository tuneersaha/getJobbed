import { signIn } from '@/auth'

export default function SignInPage() {
  return (
    <main style={{
      minHeight: '100dvh',
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'center',
      background: 'var(--bg)',
      position: 'relative',
      overflow: 'hidden',
      padding: '24px 16px',
    }}>
      {/* Ambient background */}
      <div style={{
        position: 'absolute',
        inset: 0,
        background: 'radial-gradient(ellipse 80% 50% at 50% -10%, rgba(59,130,246,0.1) 0%, transparent 60%)',
        pointerEvents: 'none',
      }} />
      <div className="orb orb-1" />
      <div className="orb orb-2" />

      {/* Card */}
      <div className="card-glass" style={{
        width: '100%',
        maxWidth: '400px',
        padding: '40px 36px',
        position: 'relative',
        zIndex: 1,
      }}>
        {/* Brand */}
        <div style={{ marginBottom: '32px' }}>
          <div style={{
            display: 'inline-flex',
            alignItems: 'center',
            justifyContent: 'center',
            width: '40px', height: '40px',
            borderRadius: '10px',
            background: 'rgba(59,130,246,0.15)',
            border: '1px solid rgba(59,130,246,0.25)',
            marginBottom: '16px',
          }}>
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{ color: 'var(--accent)' }}>
              <rect x="2" y="7" width="20" height="14" rx="2" ry="2" />
              <path d="M16 21V5a2 2 0 0 0-2-2h-4a2 2 0 0 0-2 2v16" />
            </svg>
          </div>
          <h1 style={{
            fontFamily: 'var(--font-head)',
            fontSize: '22px',
            fontWeight: 700,
            letterSpacing: '-0.03em',
            marginBottom: '6px',
          }}>
            GetJobbed
          </h1>
          <p style={{ fontSize: '14px', color: 'var(--fg-3)', lineHeight: 1.5 }}>
            Your AI-powered job search engine. Finds, scores, and tailors resumes automatically.
          </p>
        </div>

        {/* Sign-in */}
        <form action={async () => {
          'use server'
          await signIn('google', { redirectTo: '/' })
        }}>
          <button type="submit" className="btn-google">
            <svg width="18" height="18" viewBox="0 0 48 48">
              <path fill="#FFC107" d="M43.6 20.1H42V20H24v8h11.3C33.6 32.7 29.2 36 24 36c-6.6 0-12-5.4-12-12s5.4-12 12-12c3.1 0 5.8 1.1 7.9 3l5.7-5.7C34 6.1 29.3 4 24 4 12.9 4 4 12.9 4 24s8.9 20 20 20 20-8.9 20-20c0-1.3-.1-2.6-.4-3.9z"/>
              <path fill="#FF3D00" d="m6.3 14.7 6.6 4.8C14.6 15.9 19 13 24 13c3.1 0 5.8 1.1 7.9 3l5.7-5.7C34 6.1 29.3 4 24 4 16.3 4 9.7 8.3 6.3 14.7z"/>
              <path fill="#4CAF50" d="M24 44c5.2 0 9.9-2 13.4-5.2l-6.2-5.2C29.2 35.3 26.7 36 24 36c-5.2 0-9.6-3.3-11.3-7.9l-6.5 5C9.6 39.6 16.3 44 24 44z"/>
              <path fill="#1976D2" d="M43.6 20.1H42V20H24v8h11.3c-.8 2.2-2.2 4.1-4 5.5l6.2 5.2C39.5 36.2 44 30.7 44 24c0-1.3-.1-2.6-.4-3.9z"/>
            </svg>
            Continue with Google
          </button>
        </form>

        {/* Features */}
        <ul style={{ listStyle: 'none', marginTop: '28px', display: 'flex', flexDirection: 'column', gap: '10px' }}>
          {[
            'Scans 10+ job boards automatically every 6 hours',
            'AI scores each job against your resume',
            'Generates tailored LaTeX resumes per application',
          ].map((f) => (
            <li key={f} style={{ display: 'flex', alignItems: 'flex-start', gap: '10px', fontSize: '13px', color: 'var(--fg-3)', lineHeight: 1.5 }}>
              <span style={{
                width: '5px', height: '5px', borderRadius: '50%',
                background: 'var(--accent)', flexShrink: 0, marginTop: '6px',
              }} />
              {f}
            </li>
          ))}
        </ul>

        {/* Footer */}
        <p style={{ fontSize: '11px', color: 'var(--fg-3)', textAlign: 'center', marginTop: '24px', lineHeight: 1.5 }}>
          By continuing you agree to our terms. No credit card required.
        </p>
      </div>
    </main>
  )
}
