/**
 * Dashboard — Sprint 5 implementation.
 * 3-column layout: job list (left) + JD panel (center) + tailored resume (right).
 *
 * Sprint 1 placeholder.
 */

import { auth } from "@/auth";
import { redirect } from "next/navigation";

export default async function DashboardPage() {
  const session = await auth();
  if (!session) redirect("/auth/signin");

  return (
    <main style={{ padding: "32px", fontFamily: "var(--font-head)" }}>
      <h1 style={{ fontSize: "20px", marginBottom: "8px" }}>GetJobbed</h1>
      <p style={{ color: "var(--fg-2)", fontSize: "13px" }}>
        Sprint 1 complete — foundation is ready.
        Dashboard UI coming in Sprint 5.
      </p>
      <p style={{ color: "var(--fg-3)", fontSize: "12px", marginTop: "8px" }}>
        Signed in as {session.user?.email}
      </p>
    </main>
  );
}
