import { redirect } from 'next/navigation'
import { auth } from '@/auth'
import { apiFetch } from '@/lib/api'
import type { ProfileResponse } from '@/lib/types'

export default async function RootPage() {
  const session = await auth()

  // No session → signin (checking !session avoids redirect loop when id_token missing)
  if (!session) {
    redirect('/auth/signin')
  }

  let profile: ProfileResponse | null = null
  try {
    profile = await apiFetch<ProfileResponse>('/api/profile', session.id_token ?? '')
  } catch {
    // API unreachable, 401, or first-time user with no profile
    redirect('/onboarding')
  }

  if (!profile || profile.desired_roles.length === 0) {
    redirect('/onboarding')
  }

  redirect('/dashboard')
}
