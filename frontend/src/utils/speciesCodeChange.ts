// Pure helpers for the read-only Species code-change preview. The server plan
// is the only source of counts, actions and reasons; nothing here mutates or
// predicts an identifier.

export type CodeChangeAction = 'rename' | 'unchanged' | 'blocked';

// A blocker or a warning. Every detail is optional: it depends on the code.
export type CodeChangeIssue = {
  code: string;
  category?: string;
  strain_id?: number;
  box_id?: number;
  box_ids?: number[];
  identifier?: string;
  scope?: string;
  scientific_name?: string;
  count?: number;
};

export type CodeChangeStrain = {
  id: number;
  current_code: string;
  proposed_code: string | null;
  ownership?: string;
  action: CodeChangeAction | string;
  reasons?: string[];
  box_count?: number;
};

export type CodeChangeBox = {
  id: number;
  strain_id: number;
  status?: string;
  current_local_id: string;
  proposed_local_id: string | null;
  previous_local_id?: string | null;
  global_id?: string | null;
  is_merged_alias?: boolean;
  action: CodeChangeAction | string;
  reasons?: string[];
};

export type CodeChangeSummary = Partial<Record<
  | 'strains' | 'strains_to_rename' | 'strains_blocked' | 'strains_unchanged'
  | 'boxes' | 'boxes_to_rename' | 'boxes_blocked' | 'boxes_unchanged'
  | 'boxes_missing_global_id' | 'blockers',
  number
>>;

export type CodeChangeDependency = { kind: string; effect?: string; count?: number };

export type SpeciesCodeChangePlan = {
  organization_id: number;
  institution_species: { id: number; species_id?: number; scientific_name?: string; revision?: number };
  current_code: string | null;
  proposed_code: string;
  feasible: boolean;
  blockers?: CodeChangeIssue[];
  warnings?: CodeChangeIssue[];
  strains?: CodeChangeStrain[];
  boxes?: CodeChangeBox[];
  summary?: CodeChangeSummary;
  dependencies?: CodeChangeDependency[];
  plan_fingerprint?: string;
  advisory_only?: boolean;
  revalidation_required?: boolean;
  mutation_available?: boolean;
};

export type CodeChangeScope = { organizationId: number; profileId: number; proposedCode: string };

export function codeChangePreviewPath(profileId: number, proposedCode: string) {
  return `/api/admin/institution-species/${profileId}/code-change-preview/?proposed_code=${encodeURIComponent(proposedCode)}`;
}

// Same normalization as the server: outer spaces dropped, uppercase.
export function normalizeSpeciesCode(value: string) {
  return value.replace(/\s+/g, '').toUpperCase().slice(0, 3);
}

export type ProposedCodeProblem = 'required' | 'invalid_format' | 'unchanged';

// Mirrors the server's input rule so an obviously unusable code is not sent.
// Collisions and every other condition are decided by the server only.
export function getProposedCodeProblem(value: string, currentCode: string | null): ProposedCodeProblem | null {
  const code = normalizeSpeciesCode(value);
  if (!code) return 'required';
  if (!/^[A-Z]{3}$/.test(code)) return 'invalid_format';
  if (currentCode !== null && code === currentCode) return 'unchanged';
  return null;
}

// The server's own field refusal for the proposed code (HTTP 400).
export function getProposedCodeRefusal(data: unknown): ProposedCodeProblem | null {
  const errors = (data as { proposed_code?: unknown } | null)?.proposed_code;
  if (!Array.isArray(errors)) return null;
  if (errors.includes('required')) return 'required';
  return errors.includes('invalid_format') ? 'invalid_format' : null;
}

// A plan is shown only for the institution, Species and code it was computed for.
export function isPlanFor(plan: SpeciesCodeChangePlan | null | undefined, scope: CodeChangeScope) {
  return Boolean(plan)
    && plan?.organization_id === scope.organizationId
    && plan?.institution_species?.id === scope.profileId
    && plan?.proposed_code === scope.proposedCode;
}

export type CodeChangeIssueGroup = {
  code: string;
  // Local identifiers of this institution's records concerned by the issue.
  identifiers: string[];
  // Records concerned, including those the plan does not enumerate.
  recordCount: number;
  // Own-institution Species named by the server for a code already assigned.
  scientificName: string | null;
  // A conflict outside the institution is reported without any detail.
  isOutsideInstitution: boolean;
};

// One entry per server reason, in first-seen order, with the records it concerns.
export function groupCodeChangeIssues(
  issues: readonly CodeChangeIssue[] | undefined,
  plan: Pick<SpeciesCodeChangePlan, 'strains' | 'boxes'>,
): CodeChangeIssueGroup[] {
  const strainCodes = new Map((plan.strains ?? []).map((item) => [item.id, item.current_code]));
  const boxCodes = new Map((plan.boxes ?? []).map((item) => [item.id, item.current_local_id]));
  const groups = new Map<string, CodeChangeIssueGroup & { seen: Set<string> }>();
  for (const issue of issues ?? []) {
    let group = groups.get(issue.code);
    if (!group) {
      group = { code: issue.code, identifiers: [], recordCount: 0, scientificName: null, isOutsideInstitution: false, seen: new Set() };
      groups.set(issue.code, group);
    }
    if (issue.scope === 'outside_institution') group.isOutsideInstitution = true;
    if (issue.scientific_name && !group.scientificName) group.scientificName = issue.scientific_name;
    const records: Array<[string, string | undefined]> = [];
    if (typeof issue.strain_id === 'number') records.push([`strain:${issue.strain_id}`, strainCodes.get(issue.strain_id)]);
    for (const boxId of [...(typeof issue.box_id === 'number' ? [issue.box_id] : []), ...(issue.box_ids ?? [])]) {
      records.push([`box:${boxId}`, boxCodes.get(boxId)]);
    }
    if (!records.length && typeof issue.count === 'number') group.recordCount += issue.count;
    for (const [key, identifier] of records) {
      if (group.seen.has(key)) continue;
      group.seen.add(key);
      group.recordCount += 1;
      if (identifier) group.identifiers.push(identifier);
    }
  }
  return [...groups.values()].map(({ seen: _seen, ...group }) => group);
}

// Count reported by the server for a dependency; null when it is not supplied,
// which is different from a reported zero.
export function dependencyCount(plan: Pick<SpeciesCodeChangePlan, 'dependencies'>, kind: string) {
  const count = plan.dependencies?.find((item) => item.kind === kind)?.count;
  return typeof count === 'number' ? count : null;
}

// Split an identifier so its Species code can be shown distinctly from the
// collection location, Strain number and Box number, which never change.
export function splitSpeciesCodePrefix(identifier: string, speciesCode: string | null) {
  return speciesCode && identifier.startsWith(`${speciesCode}-`)
    ? { code: speciesCode, rest: identifier.slice(speciesCode.length) }
    : { code: '', rest: identifier };
}
