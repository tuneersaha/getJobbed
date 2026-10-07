'use client'

import Link from 'next/link'
import { usePathname } from 'next/navigation'
import { signOut, useSession } from 'next-auth/react'
import { Briefcase, CheckCircle, Settings, LogOut } from 'lucide-react'

const NAV_ITEMS = [
  { href: '/dashboard', label: 'Jobs',     Icon: Briefcase,   match: '/dashboard' },
  { href: '/applied',   label: 'Applied',  Icon: CheckCircle, match: '/applied' },
  { href: '/settings',  label: 'Settings', Icon: Settings,    match: '/settings' },
]

export function AppNav() {
  const pathname = usePathname()
  const { data: session } = useSession()

  return (
    <>
      {/* ── Desktop sidebar ── */}
      <aside className="app-sidebar">
        <div className="sidebar-brand">
          <span style={{
            fontFamily: 'var(--font-head)',
            fontSize: '15px',
            fontWeight: 700,
            letterSpacing: '-0.02em',
            color: 'var(--fg)',
          }}>
            GetJobbed
          </span>
        </div>

        <nav style={{ display: 'flex', flexDirection: 'column', gap: '2px', flex: 1 }}>
          {NAV_ITEMS.map(({ href, label, Icon, match }) => (
            <Link
              key={href}
              href={href}
              className={`sidebar-item${pathname.startsWith(match) ? ' active' : ''}`}
            >
              <Icon size={16} />
              {label}
            </Link>
          ))}
        </nav>

        <div className="sidebar-footer">
          {session?.user?.email && (
            <p style={{
              fontSize: '11px',
              color: 'var(--fg-3)',
              padding: '0 12px 10px',
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              whiteSpace: 'nowrap',
            }}>
              {session.user.email}
            </p>
          )}
          <button
            className="sidebar-item"
            onClick={() => signOut({ callbackUrl: '/auth/signin' })}
            style={{ color: 'var(--red)' }}
          >
            <LogOut size={16} />
            Sign out
          </button>
        </div>
      </aside>

      {/* ── Mobile bottom nav ── */}
      <nav className="bottom-nav" aria-label="Main navigation">
        {NAV_ITEMS.map(({ href, label, Icon, match }) => (
          <Link
            key={href}
            href={href}
            className={`bottom-nav-item${pathname.startsWith(match) ? ' active' : ''}`}
          >
            <Icon size={20} />
            <span>{label}</span>
          </Link>
        ))}
      </nav>
    </>
  )
}
