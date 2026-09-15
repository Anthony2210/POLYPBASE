import type { TranslationKey } from '../i18n/fr';

// A completed member mutation keeps a highlight on the affected row for this
// long, then the highlight clears itself. Long enough to be noticed, short
// enough to disappear without user action.
export const MEMBER_FEEDBACK_MS = 2400;

export type MemberFeedbackTone = 'positive' | 'negative';

export type MemberMutationKind =
  | 'created'
  | 'promote'
  | 'demote'
  | 'promote_to_admin'
  | 'demote_to_technician'
  | 'deactivate'
  | 'reactivate'
  | 'relinquish_responsable';

// Message shown and announced after each member mutation succeeds, with the
// direction of the action: promotions and reactivations read as positive,
// demotions and deactivations as negative.
export const MEMBER_MUTATION_FEEDBACK: Record<
  MemberMutationKind,
  { key: TranslationKey; tone: MemberFeedbackTone }
> = {
  created: { key: 'manageMemberAdded', tone: 'positive' },
  promote: { key: 'manageMemberPromoted', tone: 'positive' },
  demote: { key: 'manageMemberDemoted', tone: 'negative' },
  promote_to_admin: { key: 'manageMemberPromotedToAdmin', tone: 'positive' },
  demote_to_technician: { key: 'manageMemberDemotedToTechnician', tone: 'negative' },
  deactivate: { key: 'manageMemberDeactivated', tone: 'negative' },
  reactivate: { key: 'manageMemberReactivated', tone: 'positive' },
  relinquish_responsable: { key: 'manageResponsableRelinquished', tone: 'negative' },
};

// Row classes: an inactive membership stays dimmed, and a just-updated one keeps
// a temporary highlight that wins over the hover and inactive states.
export function getMemberRowClassName(isActive: boolean, tone: MemberFeedbackTone | null): string {
  const classes: string[] = [];
  if (!isActive) classes.push('is-inactive');
  if (tone === 'positive') classes.push('is-updated-positive');
  else if (tone === 'negative') classes.push('is-updated-negative');
  return classes.join(' ');
}