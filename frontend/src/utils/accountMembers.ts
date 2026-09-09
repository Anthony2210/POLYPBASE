import type { AccountMember } from '../types';


export type MemberRowAction = 'deactivate' | 'reactivate';

export function getMemberRowAction(
  member: Pick<AccountMember, 'is_active' | 'is_self'>,
): MemberRowAction | null {
  if (member.is_self) return null;
  return member.is_active ? 'deactivate' : 'reactivate';
}
