import type { PersonalAction, PersonalActionDetails, PersonalActionsResponse } from '../types';
import { filterAuditDisplayRecord, getMetadataRecord } from './auditPresentation';

/**
 * Append one page of personal actions, ignoring rows already displayed. The
 * endpoint paginates by offset, so a row can legitimately appear twice when the
 * underlying log grows between two requests.
 */
export function mergePersonalActionPage(
  current: readonly PersonalAction[],
  incoming: readonly PersonalAction[],
): PersonalAction[] {
  const knownIds = new Set(current.map((entry) => entry.id));
  return [...current, ...incoming.filter((entry) => !knownIds.has(entry.id))];
}

/**
 * Data and error state of the personal action list. The initial load and the
 * pagination have separate error slots: a failed "load more" must never hide
 * the actions that are already displayed.
 */
export type PersonalActionsState = {
  entries: PersonalAction[];
  hasMore: boolean;
  nextOffset: number | null;
  error: string | null;
  loadMoreError: string | null;
};

export type PersonalActionsOutcome =
  | { kind: 'initial-page'; page: PersonalActionsResponse }
  | { kind: 'initial-error'; message: string }
  | { kind: 'load-more-page'; page: PersonalActionsResponse }
  | { kind: 'load-more-error'; message: string };

export const EMPTY_PERSONAL_ACTIONS_STATE: PersonalActionsState = {
  entries: [],
  hasMore: false,
  nextOffset: null,
  error: null,
  loadMoreError: null,
};

export function applyPersonalActionsOutcome(
  state: PersonalActionsState,
  outcome: PersonalActionsOutcome,
): PersonalActionsState {
  switch (outcome.kind) {
    case 'initial-page':
      return {
        entries: outcome.page.results,
        hasMore: Boolean(outcome.page.has_more),
        nextOffset: outcome.page.next_offset ?? null,
        error: null,
        loadMoreError: null,
      };
    case 'initial-error':
      return { ...state, error: outcome.message };
    case 'load-more-page':
      return {
        entries: mergePersonalActionPage(state.entries, outcome.page.results),
        hasMore: Boolean(outcome.page.has_more),
        nextOffset: outcome.page.next_offset ?? null,
        error: state.error,
        loadMoreError: null,
      };
    case 'load-more-error':
      // Entries, hasMore and nextOffset are preserved so a retry requests the
      // same page instead of restarting the list.
      return { ...state, loadMoreError: outcome.message };
  }
}

export function getPersonalActionDetails(details: PersonalActionDetails | undefined): {
  values: Record<string, unknown> | null;
  changes: Record<string, unknown> | null;
} {
  return {
    values: filterAuditDisplayRecord(getMetadataRecord(details?.values)),
    changes: filterAuditDisplayRecord(getMetadataRecord(details?.changes)),
  };
}

/** Details are only offered when they carry useful business information. */
export function hasPersonalActionDetails(details: PersonalActionDetails | undefined): boolean {
  const { values, changes } = getPersonalActionDetails(details);
  return Boolean(values && Object.keys(values).length) || Boolean(changes && Object.keys(changes).length);
}
