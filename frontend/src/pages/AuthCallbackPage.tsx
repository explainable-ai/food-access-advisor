import { useEffect, useRef, useState } from "react";
import { completeStaffSignIn } from "../lib/auth";

export function AuthCallbackPage() {
  const started = useRef(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (started.current) return;
    started.current = true;
    completeStaffSignIn()
      // Reload after the callback so the header reads the newly stored user.
      .then(() => window.location.replace("/follow-up"))
      .catch((err) => setError(err instanceof Error ? err.message : String(err)));
  }, []);

  return (
    <section className="auth-callback" aria-live="polite">
      <h1>Completing staff sign-in</h1>
      {error ? <p className="chat-error">Sign-in failed: {error}</p> : <p>Returning to the review workspace…</p>}
    </section>
  );
}
