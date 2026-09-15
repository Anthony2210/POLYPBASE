import type { AccountMember, MembershipRole } from '../types';

export type MemberRoleFilter = MembershipRole | 'all';

export type MemberRoleCounts = Record<MemberRoleFilter, number>;

export type MemberRowAction =
  | 'promote'
  | 'demote'
  | 'promote_to_admin'
  | 'demote_to_technician'
  | 'deactivate'
  | 'reactivate'
  | 'relinquish_responsable';

export type MemberRowActionItem = {
  action: MemberRowAction;
  danger?: boolean;
};

export type MemberRowActionContext = {
  canManageAdminMemberships: boolean;
  canRelinquishResponsable: boolean;
};

export function getAccountMemberRoleLabel(
  member: Pick<AccountMember, 'role' | 'role_label' | 'is_responsable'>,
  responsableLabel: string,
): string {
  return member.role === 'admin' && member.is_responsable
    ? responsableLabel
    : member.role_label;
}

export function getMemberRoleCounts(
  members: ReadonlyArray<Pick<AccountMember, 'role'>>,
): MemberRoleCounts {
  const counts: MemberRoleCounts = {
    all: members.length,
    admin: 0,
    lab_technician: 0,
    viewer: 0,
  };

  for (const member of members) counts[member.role] += 1;
  return counts;
}

export function filterMembersByRole(
  members: readonly AccountMember[],
  roleFilter: MemberRoleFilter,
): AccountMember[] {
  return roleFilter === 'all'
    ? [...members]
    : members.filter((member) => member.role === roleFilter);
}

/**
 * Actions offered for a member row. Capability flags come from the members API;
 * the backend remains authoritative when a mutation is submitted.
 */
export function getMemberRowActions(
  member: Pick<AccountMember, 'role' | 'is_active' | 'is_self' | 'is_responsable'>,
  context: MemberRowActionContext,
): MemberRowActionItem[] {
  if (member.is_responsable) {
    return member.is_self && context.canRelinquishResponsable
      ? [{ action: 'relinquish_responsable', danger: true }]
      : [];
  }

  const actions: MemberRowActionItem[] = [];

  if (member.role === 'viewer') {
    actions.push({ action: 'promote' });
  } else if (member.role === 'lab_technician') {
    actions.push({ action: 'demote', danger: true });
  } else if (member.role === 'admin' && context.canManageAdminMemberships && !member.is_self) {
    actions.push({ action: 'demote_to_technician', danger: true });
  }

  if (
    context.canManageAdminMemberships
    && member.role !== 'admin'
  ) {
    actions.push({ action: 'promote_to_admin' });
  }

  const canManageActivation = member.role !== 'admin' || context.canManageAdminMemberships;
  if (!member.is_self && canManageActivation) {
    actions.push(member.is_active
      ? { action: 'deactivate', danger: true }
      : { action: 'reactivate' });
  }

  return actions;
}
