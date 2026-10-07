'use client'

import { SessionProvider } from 'next-auth/react'
import { signOut } from 'next-auth/react'
import { QueryClient, QueryClientProvider, QueryCache, MutationCache } from '@tanstack/react-query'
import { useState } from 'react'
import { ApiError } from '@/lib/api'

function handle401(error: unknown) {
  if (error instanceof ApiError && error.status === 401) {
    signOut({ callbackUrl: '/auth/signin' })
  }
}

export function Providers({ children }: { children: React.ReactNode }) {
  const [queryClient] = useState(
    () =>
      new QueryClient({
        queryCache: new QueryCache({ onError: handle401 }),
        mutationCache: new MutationCache({ onError: handle401 }),
        defaultOptions: {
          queries: {
            staleTime: 30_000,
            retry: (failureCount, error: unknown) => {
              const status = (error as { status?: number })?.status
              if (status === 401 || status === 403) return false
              return failureCount < 2
            },
          },
        },
      }),
  )

  return (
    <SessionProvider>
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    </SessionProvider>
  )
}
