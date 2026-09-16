import type { AuditBusinessDetails } from '../types';
import { getAuditBusinessDetailContent, hasAuditBusinessDetails } from './auditPresentation';

export type AdminAuditPage<T> = {
  results: T[];
  has_more?: boolean;
  next_offset?: number | null;
  total_count?: number;
};

export type AdminAuditState<T> = {
  organizationId: number;
  entries: T[];
  hasMore: boolean;
  nextOffset: number | null;
  totalCount: number | null;
  error: string | null;
  loadMoreError: string | null;
};

export type AdminAuditOutcome<T> =
  | { kind: 'initial-page'; organizationId: number; page: AdminAuditPage<T> }
  | { kind: 'initial-error'; organizationId: number; message: string }
  | { kind: 'load-more-page'; organizationId: number; page: AdminAuditPage<T> }
  | { kind: 'load-more-error'; organizationId: number; message: string };

export function createAdminAuditState<T>(organizationId: number): AdminAuditState<T> {
  return {
    organizationId,
    entries: [],
    hasMore: false,
    nextOffset: null,
    totalCount: null,
    error: null,
    loadMoreError: null,
  };
}

export function mergeAdminAuditPage<T extends { id: number }>(current: readonly T[], incoming: readonly T[]): T[] {
  const knownIds = new Set(current.map((entry) => entry.id));
  return [...current, ...incoming.filter((entry) => !knownIds.has(entry.id))];
}

export function applyAdminAuditOutcome<T extends { id: number }>(
  state: AdminAuditState<T>,
  outcome: AdminAuditOutcome<T>,
): AdminAuditState<T> {
  if (state.organizationId !== outcome.organizationId) return state;

  switch (outcome.kind) {
    case 'initial-page':
      return {
        organizationId: state.organizationId,
        entries: outcome.page.results,
        hasMore: Boolean(outcome.page.has_more),
        nextOffset: outcome.page.next_offset ?? null,
        totalCount: outcome.page.total_count ?? null,
        error: null,
        loadMoreError: null,
      };
    case 'initial-error':
      return { ...state, error: outcome.message };
    case 'load-more-page':
      return {
        organizationId: state.organizationId,
        entries: mergeAdminAuditPage(state.entries, outcome.page.results),
        hasMore: Boolean(outcome.page.has_more),
        nextOffset: outcome.page.next_offset ?? null,
        totalCount: state.totalCount,
        error: state.error,
        loadMoreError: null,
      };
    case 'load-more-error':
      return { ...state, loadMoreError: outcome.message };
  }
}

export type AdminAuditDetailEntry = {
  business_details?: AuditBusinessDetails | null;
  edited_at?: string | null;
  editable_measurement?: unknown;
};

export function getAdminAuditBusinessDetails(entry: AdminAuditDetailEntry) {
  return getAuditBusinessDetailContent(entry.business_details);
}

export function hasAdminAuditBusinessDetails(entry: AdminAuditDetailEntry): boolean {
  return Boolean(hasAuditBusinessDetails(entry.business_details) || entry.edited_at);
}

export function buildAdminAuditQuery({
  family,
  date,
  includeOptions,
  includeTotal = false,
  limit,
  offset,
}: {
  family: string;
  date: string;
  includeOptions: boolean;
  includeTotal?: boolean;
  limit: number;
  offset: number;
}): string {
  const params = new URLSearchParams({
    limit: String(limit),
    offset: String(offset),
  });
  if (includeOptions) params.set('include_options', '1');
  if (includeTotal) params.set('include_total', '1');
  if (family) params.set('family', family);
  if (date) params.set('date', date);
  return params.toString();
}
