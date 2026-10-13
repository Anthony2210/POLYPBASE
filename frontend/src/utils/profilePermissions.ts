import type { UserProfile } from '../types';

export function getActiveOrganizationId(profile: UserProfile | null) {
  return profile?.active_organization?.id ?? null;
}

export function getMembershipRole(profile: UserProfile | null, organizationId: number | null) {
  if (!profile || organizationId == null) return null;
  return profile.memberships.find((membership) => membership.organization.id === organizationId)?.role ?? null;
}

export function userHasAdminRole(profile: UserProfile | null, activeOrganizationId: number | null = getActiveOrganizationId(profile)) {
  if (!profile) return false;
  if (profile.is_superuser) return true;
  return getMembershipRole(profile, activeOrganizationId) === 'admin';
}
