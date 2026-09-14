import type { AccountMember } from '../types';

export type MemberRowAction = 'promote' | 'demote' | 'deactivate' | 'reactivate';

export type MemberRowActionItem = {
  action: MemberRowAction;
  danger?: boolean;
};

/**
 * Actions offered for a member row.
 *
 * Role transitions stay inside the viewer / lab technician pair. Administrators
 * are intentionally not editable from this interface, so no role action is
 * offered for them; the row communicates that limitation separately. Managing
 * your own activation stays blocked, as before.
 */
export function getMemberRowActions(
  member: Pick<AccountMember, 'role' | 'is_active' | 'is_self'>,
): MemberRowActionItem[] {
  const actions: MemberRowActionItem[] = [];

  if (member.role === 'viewer') {
    actions.push({ action: 'promote' });
  } else if (member.role === 'lab_technician') {
    actions.push({ action: 'demote', danger: true });
  }

  if (!member.is_self) {
    if (member.is_active) actions.push({ action: 'deactivate', danger: true });
    else actions.push({ action: 'reactivate' });
  }

  return actions;
}
