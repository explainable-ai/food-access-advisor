import { UserManager, WebStorageStateStore, type User } from "oidc-client-ts";

const authority = (import.meta.env.VITE_COGNITO_AUTHORITY as string | undefined)?.trim() ?? "";
const clientId = (import.meta.env.VITE_COGNITO_CLIENT_ID as string | undefined)?.trim() ?? "";
const redirectUri = (import.meta.env.VITE_COGNITO_REDIRECT_URI as string | undefined)?.trim() ?? "";
const logoutUri = (import.meta.env.VITE_COGNITO_LOGOUT_URI as string | undefined)?.trim() ?? "";

export const STAFF_AUTH_NOT_CONFIGURED = "Staff sign-in is not configured yet.";
export const STAFF_AUTH_REQUIRED = "Staff sign-in required.";

export const isStaffAuthConfigured = Boolean(authority && clientId && redirectUri);

let manager: UserManager | null = null;

function getManager(): UserManager {
  if (!isStaffAuthConfigured) throw new Error(STAFF_AUTH_NOT_CONFIGURED);
  if (!manager) {
    manager = new UserManager({
      authority,
      client_id: clientId,
      redirect_uri: redirectUri,
      post_logout_redirect_uri: logoutUri || window.location.origin,
      response_type: "code",
      scope: "openid email profile",
      userStore: new WebStorageStateStore({ store: window.sessionStorage }),
    });
  }
  return manager;
}

export async function getStaffUser(): Promise<User | null> {
  if (!isStaffAuthConfigured) return null;
  const user = await getManager().getUser();
  return user && !user.expired ? user : null;
}

export async function getStaffAccessToken(): Promise<string> {
  const user = await getStaffUser();
  if (!user?.access_token) throw new Error(STAFF_AUTH_REQUIRED);
  return user.access_token;
}

export async function beginStaffSignIn(): Promise<void> {
  await getManager().signinRedirect();
}

export async function completeStaffSignIn(): Promise<User> {
  return getManager().signinRedirectCallback();
}

export async function signOutStaff(): Promise<void> {
  const auth = getManager();
  const user = await auth.getUser();
  if (user?.id_token) {
    await auth.signoutRedirect({ id_token_hint: user.id_token });
    return;
  }
  await auth.removeUser();
  window.location.assign(logoutUri || window.location.origin);
}
