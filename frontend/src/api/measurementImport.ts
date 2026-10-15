import { ApiError, apiDownload, apiPostForm, getStoredActiveOrganizationId } from './client';
import type { TranslationKey } from '../i18n';

export type ImportIssue = { code: string; field: string | null; column: number | null };
export type Inspection = {
  headers: string[];
  fields: string[];
  issues: ImportIssue[];
  application_available: false;
};
export type DiagnosticValue = { field: string; state: string; value: string | number | boolean | null };
export type DiagnosticRow = {
  record_number: number;
  line_start: number;
  line_end: number;
  outcome: string;
  box_reference: string | null;
  measured_on: string | null;
  precision: string | null;
  iso_year: number | null;
  iso_week: number | null;
  values: DiagnosticValue[];
  issues: ImportIssue[];
};
export const summaryFields = [
  'total_source_rows', 'ignored_rows', 'create_candidates', 'already_matching',
  'conflicts', 'unknown_boxes', 'invalid_or_blocked', 'blocked_rows',
] as const;
export type DiagnosticPreview = {
  application_available: boolean;
  preparation_fingerprint: string;
  plan_fingerprint: string;
  source_complete: boolean;
  issues: ImportIssue[];
  summary: Record<typeof summaryFields[number], number> & { ready: boolean };
  rows: DiagnosticRow[];
};

// undefined is undecided; null is a deliberate choice to ignore a source column.
export type Mapping = Record<string, string | null | undefined>;
export function mappingComplete(inspection: Inspection | null, mapping: Mapping) {
  return Boolean(inspection && inspection.headers.length && !inspection.issues.length
    && inspection.headers.every(header => Object.prototype.hasOwnProperty.call(mapping, header)
      && (mapping[header] === null || inspection.fields.includes(mapping[header] as string))));
}

export async function inspectMeasurements(file: File, delimiter: string, organizationId: number, signal: AbortSignal) {
  return apiPostForm<Inspection>('/api/measurement-import/inspect/', sourceForm(file, delimiter, organizationId), signal);
}

export async function previewMeasurements(
  file: File, delimiter: string, organizationId: number, inspection: Inspection,
  mapping: Mapping, confirmed: boolean, signal: AbortSignal,
) {
  if (!confirmed || !mappingComplete(inspection, mapping)) throw new Error('Unconfirmed mapping.');
  const form = sourceForm(file, delimiter, organizationId);
  // Only exact inspected headers are sent, including unusual names such as __proto__.
  form.set('mapping', JSON.stringify(Object.fromEntries(inspection.headers.map(header => [header, mapping[header]]))));
  form.set('confirmed', 'true');
  return apiPostForm<DiagnosticPreview>('/api/measurement-import/preview/', form, signal);
}

export type ImportReceipt = {
  id: string;
  accepted_at: string;
  source_sha256: string;
  created_count: number;
  already_matching_count: number;
  ignored_count: number;
  measurement_ids: number[];
  source_url: string;
  receipt_url: string;
};
export type ImportAcceptance = { receipt: ImportReceipt; replayed: boolean };

export function canApplyPreview(preview: DiagnosticPreview | null): preview is DiagnosticPreview {
  return Boolean(preview?.application_available && preview.source_complete && preview.summary.ready
    && preview.preparation_fingerprint && preview.plan_fingerprint);
}

export async function applyMeasurements(
  file: File, delimiter: string, organizationId: number, inspection: Inspection,
  mapping: Mapping, confirmed: boolean, preview: DiagnosticPreview, signal: AbortSignal,
) {
  if (!confirmed || !mappingComplete(inspection, mapping) || !canApplyPreview(preview)) {
    throw new Error('Unconfirmed or unavailable import.');
  }
  const form = sourceForm(file, delimiter, organizationId);
  form.set('mapping', JSON.stringify(Object.fromEntries(inspection.headers.map(header => [header, mapping[header]]))));
  form.set('confirmed', 'true');
  form.set('expected_preparation_fingerprint', preview.preparation_fingerprint);
  form.set('expected_plan_fingerprint', preview.plan_fingerprint);
  return apiPostForm<ImportAcceptance>('/api/measurement-import/apply/', form, signal);
}

export async function downloadImportResource(path: string, organizationId: number, signal: AbortSignal, fileName: string, isCurrent?: () => boolean) {
  if (getStoredActiveOrganizationId() !== organizationId) throw new Error('Organization context changed.');
  const url = new URL(path, window.location.origin);
  if (url.origin !== window.location.origin || !url.pathname.startsWith('/api/measurement-import/')) {
    throw new Error('Invalid import resource URL.');
  }
  return apiDownload(url.pathname + url.search, { signal, fileName, isCurrent });
}

function sourceForm(file: File, delimiter: string, organizationId: number) {
  if (getStoredActiveOrganizationId() !== organizationId) throw new Error('Organization context changed.');
  const form = new FormData();
  form.set('file', file);
  form.set('delimiter', delimiter);
  return form;
}

export function diagnosticErrorKey(error: unknown): TranslationKey {
  if (error instanceof ApiError) {
    const code = (error.data as { error_code?: string } | null)?.error_code;
    if (error.status === 413 || code === 'file_too_large' || code === 'request_too_large') return 'csvErrorSize';
    if (error.status === 401 || error.status === 403) return 'csvErrorPermission';
    if (code === 'invalid_mapping' || code === 'mapping_not_confirmed') return 'csvErrorMapping';
    if (code === 'stale_preparation' || code === 'stale_plan' || code === 'plan_not_applicable' || error.status === 409) return 'csvErrorStale';
    if (error.status === 400) return 'csvErrorConfiguration';
  }
  return 'csvErrorRequest';
}

export const fieldLabels: Record<string, TranslationKey> = {
  box_reference: 'csvFieldBox', measured_on: 'csvFieldDate', iso_year: 'csvFieldYear', iso_week: 'csvFieldWeek',
  polyp_count: 'csvFieldPolyps', ephyrae_count: 'csvFieldEphyrae', strobila_count: 'csvFieldStrobila',
  salinity_psu: 'csvFieldSalinity', culture_status: 'csvFieldStatus', needs_attention: 'csvFieldAttention',
  notes: 'csvFieldNotes', observer_reference: 'csvFieldObserver',
};

export const outcomeLabels: Record<string, TranslationKey> = {
  IGNORED: 'csvOutcomeIgnored', INVALID: 'csvOutcomeInvalid', UNKNOWN_BOX: 'csvOutcomeUnknown',
  CREATE: 'csvOutcomeCreate', ALREADY_MATCHES: 'csvOutcomeMatches', CONFLICT: 'csvOutcomeConflict', BLOCKED: 'csvOutcomeBlocked',
};

export const issueLabels: Record<string, TranslationKey> = {
  byte_limit: 'csvErrorSize', invalid_utf8: 'csvIssueUtf8', nul_character: 'csvIssueNul',
  duplicate_or_ambiguous_header: 'csvIssueHeaders', missing_header: 'csvIssueHeaderMissing',
  malformed_csv: 'csvIssueMalformed', column_limit: 'csvIssueColumns', field_limit: 'csvIssueFieldLimit', row_limit: 'csvIssueRows',
  incomplete_header_mapping: 'csvIssueMappingIncomplete', duplicate_target_field: 'csvIssueMappingDuplicate',
  missing_required_mapping: 'csvIssueMappingRequired', missing_time_mapping: 'csvIssueMappingTime',
  invalid_mapping: 'csvErrorMapping', mapping_not_confirmed: 'csvErrorMapping', row_width: 'csvIssueWidth',
  unknown_field: 'csvIssueUnknownField', expected_source_string: 'csvIssueSourceString', required: 'csvIssueRequired',
  numeric_formula: 'csvIssueFormula', non_negative_integer_required: 'csvIssueInteger', count_out_of_range: 'csvIssueCountRange',
  decimal_format: 'csvIssueDecimal', decimal_out_of_range: 'csvIssueDecimalRange', invalid_choice: 'csvIssueChoice',
  boolean_format: 'csvIssueBoolean', iso_date_required: 'csvIssueDate', iso_week_format: 'csvIssueWeek',
  invalid_iso_week: 'csvIssueWeek', date_week_mismatch: 'csvIssueDateWeek', date_or_iso_week_required: 'csvIssueTime',
  unsupported_preparation_version: 'csvIssueVersion', incomplete_preparation: 'csvIssueIncomplete', invalid_source_row: 'csvOutcomeInvalid',
  exact_date_conflict: 'csvIssueDateConflict', supplied_value_conflict: 'csvIssueValueConflict', unknown_box: 'csvIssueUnknownBox',
  duplicate_source_slot: 'csvIssueDuplicate', contradictory_source_slot: 'csvIssueContradictory', inactive_box: 'csvIssueInactive',
  week_precision_persistence_required: 'csvIssueWeekPersistence', week_subculture_chronology_conflict: 'csvIssueChronology',
  observer_resolution_unsupported: 'csvIssueObserver',
};
