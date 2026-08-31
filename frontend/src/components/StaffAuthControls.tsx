import { useEffect, useState } from "react";
import {
  beginStaffSignIn,
  getStaffUser,
  isStaffAuthConfigured,
  signOutStaff,
  STAFF_AUTH_NOT_CONFIGURED,
} from "../lib/auth";

export function StaffAuthControls() {
  const [signedIn, setSignedIn] = useState(false);
  const [email, setEmail] = useState("");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getStaffUser()
      .then((user) => {
        setSignedIn(Boolean(user));
        setEmail(typeof user?.profile.email === "string" ? user.profile.email : "Staff");
      })
      .catch((err) => setError(err instanceof Error ? err.message : String(err)));
  }, []);

  if (!isStaffAuthConfigured) {
    return <span className="staff-auth-note" title={STAFF_AUTH_NOT_CONFIGURED}>Staff sign-in unavailable</span>;
  }

  return (
    <div className="staff-auth">
      {error && <span className="staff-auth-error">{error}</span>}
      {signedIn ? (
        <>
          <span className="staff-auth-name">{email}</span>
          <button type="button" onClick={() => void signOutStaff().catch((err) => setError(String(err)))}>
            Sign out
          </button>
        </>
      ) : (
        <button type="button" onClick={() => void beginStaffSignIn().catch((err) => setError(String(err)))}>
          Staff sign in
        </button>
      )}
    </div>
  );
}
