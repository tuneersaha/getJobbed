/**
 * Root page — redirect logic:
 *   No session → /auth/signin
 *   No profile → /onboarding
 *   Has profile → /dashboard
 *
 * Sprint 1: placeholder until Sprint 5 frontend implementation.
 */

import { redirect } from "next/navigation";
import { auth } from "@/auth";

export default async function RootPage() {
  const session = await auth();

  if (!session) {
    redirect("/auth/signin");
  }

  // TODO Sprint 5: check if profile is complete, redirect to /onboarding if not
  redirect("/dashboard");
}
