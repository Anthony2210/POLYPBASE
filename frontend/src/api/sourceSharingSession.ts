import { ApiError, ApiResourceCancelledError, getStoredActiveOrganizationId } from './client';
import { readForRecipient } from './recipientSession';
import type { UserProfile } from '../types';

export function requireSourceMembership(profile: UserProfile, organizationId: number) {
  if (getStoredActiveOrganizationId() !== organizationId) throw new ApiResourceCancelledError();
  if (!profile.memberships.some(item => item.organization.id === organizationId && item.role === 'admin')) {
    throw new ApiError(403, 'Source Admin required.', { code: 'sharing_admin_required' });
  }
}
export function readForSourceAdmin<T>(profile: UserProfile, organizationId: number, signal: AbortSignal, operation: () => Promise<T>) {
  return readForRecipient(profile, signal, operation, current => requireSourceMembership(current, organizationId));
}
