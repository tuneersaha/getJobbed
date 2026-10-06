export default function AuthErrorPage({
  searchParams,
}: {
  searchParams: { error?: string };
}) {
  return (
    <main style={{
      display: "flex",
      flexDirection: "column",
      alignItems: "center",
      justifyContent: "center",
      minHeight: "100vh",
      gap: "16px",
    }}>
      <h1 style={{ fontFamily: "var(--font-head)", fontSize: "18px" }}>
        Sign-in error
      </h1>
      <p style={{ color: "var(--fg-2)", fontSize: "13px" }}>
        {searchParams.error ?? "An unknown error occurred during sign-in."}
      </p>
      <a href="/auth/signin" style={{ color: "var(--accent)", fontSize: "13px" }}>
        Try again
      </a>
    </main>
  );
}
