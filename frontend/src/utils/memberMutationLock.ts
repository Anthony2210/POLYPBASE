// Tracks which memberships currently have an in-flight mutation. Each membership
// is locked independently so a mutation on one row can never make another row's
// in-flight request actionable again.
export function beginMemberMutation(busy: Set<number>, membershipId: number): boolean {
  if (busy.has(membershipId)) return false;
  busy.add(membershipId);
  return true;
}

export function endMemberMutation(busy: Set<number>, membershipId: number): void {
  busy.delete(membershipId);
}
