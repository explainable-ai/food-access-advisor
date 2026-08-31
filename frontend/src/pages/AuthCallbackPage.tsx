import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { completeStaffSignIn } from "../lib/auth";

export function AuthCallbackPage() {
  const navigate = useNavigate();
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    completeStaffSignIn()
      .then(() => navigate("/follow-up", { replace: true }))
      .catch((err) => setError(err instanceof Error ? err.message : String(err)));
  }, [navigate]);

  return (
    <section className="auth-callback" aria-live="polite">
      <h1>Completing staff sign-in</h1>
      {error ? <p className="chat-error">Sign-in failed: {error}</p> : <p>Returning to the review workspace…</p>}
    </section>
  );
}
