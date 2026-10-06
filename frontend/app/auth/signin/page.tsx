/**
 * Sign-in page — Google OAuth2 only.
 */

import { signIn } from "@/auth";

export default function SignInPage() {
  return (
    <main style={{
      display: "flex",
      flexDirection: "column",
      alignItems: "center",
      justifyContent: "center",
      minHeight: "100vh",
      gap: "24px",
    }}>
      <div style={{ textAlign: "center" }}>
        <h1 style={{
          fontFamily: "var(--font-head)",
          fontSize: "24px",
          fontWeight: 700,
          letterSpacing: "-0.03em",
          marginBottom: "8px",
        }}>
          GetJobbed
        </h1>
        <p style={{ color: "var(--fg-2)", fontSize: "13px" }}>
          Automated job discovery and resume tailoring
        </p>
      </div>

      <form action={async () => {
        "use server";
        await signIn("google", { redirectTo: "/" });
      }}>
        <button
          type="submit"
          style={{
            background: "var(--accent)",
            color: "#fff",
            border: "none",
            borderRadius: "6px",
            padding: "10px 20px",
            fontSize: "14px",
            fontWeight: 500,
            cursor: "pointer",
          }}
        >
          Sign in with Google
        </button>
      </form>
    </main>
  );
}
