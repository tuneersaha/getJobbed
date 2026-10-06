/**
 * Authenticated fetch wrapper for FastAPI calls.
 *
 * Injects the Google ID token from the next-auth session as
 * Authorization: Bearer <id_token>.
 *
 * Usage (server component or route handler):
 *   const jobs = await apiFetch<Job[]>("/api/jobs")
 *
 * Usage (client component via React Query):
 *   const { data } = useQuery({ queryKey: ['jobs'], queryFn: () => apiFetch('/api/jobs') })
 *   // Provide idToken from useSession() hook
 */

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export async function apiFetch<T>(
  path: string,
  idToken: string,
  options: RequestInit = {},
): Promise<T> {
  const url = `${API_BASE}${path}`;

  const res = await fetch(url, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${idToken}`,
      ...options.headers,
    },
  });

  if (!res.ok) {
    let code = "UNKNOWN_ERROR";
    let message = `HTTP ${res.status}`;
    try {
      const body = await res.json();
      code = body.code ?? code;
      message = body.message ?? message;
    } catch {}
    throw new ApiError(res.status, code, message);
  }

  return res.json() as Promise<T>;
}
