import { apiGet, ApiError } from './client';
import { recipientRequestOptions } from './boxSharing';
import type { UserProfile } from '../types';

export const SHARING_SESSION_INVALIDATED = 'polypbase:sharing-session-invalidated';
function invalidateSharingSession() {
  if (typeof window !== 'undefined') window.dispatchEvent(new Event(SHARING_SESSION_INVALIDATED));
}
export const readRecipientProfile = (signal?: AbortSignal) => apiGet<UserProfile>('/api/profile/', recipientRequestOptions(signal));
export function sameRecipient(left: Pick<UserProfile, 'id' | 'email'>, right: Pick<UserProfile, 'id' | 'email'>) {
  return left.id === right.id && left.email === right.email;
}
// The second check prevents a response prepared for a superseded session from
// being displayed under another account. It is not a substitute for Django's grant checks.
export async function readForRecipient<T>(profile: Pick<UserProfile, 'id' | 'email'>, signal: AbortSignal, read: () => Promise<T>, authorize?: (current: UserProfile) => void) {
  async function verify() {
    let current: UserProfile;
    try { current = await readRecipientProfile(signal); }
    catch (error) {
      if (error instanceof ApiError && (error.status === 401 || error.status === 403)) {
        invalidateSharingSession();
        throw new ApiError(401, 'Session unavailable.');
      }
      throw error;
    }
    signal.throwIfAborted();
    if (!sameRecipient(profile, current)) {
      invalidateSharingSession();
      throw new ApiError(401, 'Session changed.');
    }
    try { authorize?.(current); }
    catch (error) {
      if (error instanceof ApiError && error.status === 403) invalidateSharingSession();
      throw error;
    }
  }
  await verify();
  let result: T;
  try { result = await read(); }
  catch (error) {
    // SessionAuthentication answers 403 for an anonymous session too. Distinguish
    // that from an authenticated invitation mismatch before asking for sign-in.
    if (error instanceof ApiError && error.status === 403) await verify();
    throw error;
  }
  signal.throwIfAborted();
  await verify();
  return result;
}
