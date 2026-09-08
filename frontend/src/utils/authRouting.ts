export type PasswordResetRoute = {
  uid: string;
  token: string;
};

export function getPasswordResetRoute(pathname: string): PasswordResetRoute | null {
  const segments = pathname.split('/').filter(Boolean);
  if (segments.length !== 3 || segments[0] !== 'reset-password') return null;

  const [, uid, token] = segments;
  return uid && token
    ? { uid: decodeURIComponent(uid), token: decodeURIComponent(token) }
    : null;
}

export function isPublicAuthPath(pathname: string) {
  return pathname === '/login' || getPasswordResetRoute(pathname) !== null;
}

export function shouldRedirectToLogin(profileLoaded: boolean, status: number | null) {
  return !profileLoaded && (status === 401 || status === 403);
}

export function requiresSignInRecovery(status: number | null, profileStatus: number | null) {
  if (status === 401) return true;
  return status === 403 && (profileStatus === 401 || profileStatus === 403);
}
