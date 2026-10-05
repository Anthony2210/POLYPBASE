export const MAX_SUBCULTURE_CHILDREN = 20;
export const MAX_ALLOCATED_POLYPS = 2147483647;

export function parseAllocatedPolyps(value: string): number | null {
  if (!/^\d+$/.test(value)) return null;
  const count = Number(value);
  return Number.isInteger(count) && count <= MAX_ALLOCATED_POLYPS ? count : null;
}

export function summarizeSubcultureAllocation(available: number | null, values: string[]) {
  const counts = values.map(parseAllocatedPolyps);
  const complete = counts.length > 0 && counts.every((count) => count !== null);
  const knownTotal = counts.reduce<number>((total, count) => total + (count ?? 0), 0);
  const allocated = complete ? knownTotal : null;
  const remaining = available !== null && allocated !== null ? available - allocated : null;
  // A partial draft can already exceed stock, but blanks never become zero allocations.
  const overAllocated = available !== null && knownTotal > available;

  return { available, allocated, remaining, complete, overAllocated };
}
