import { ApiError } from '../api/client';
import type { ScientificField, ScientificSnapshot, ShareSelector, SharingOptions, ShareState } from '../api/boxSharing';
import { getLanguageLocale, type Language, type TranslationKey, type Translator } from '../i18n';

// Presentation labels only. Availability and dependencies always come from options/.
export const selectorLabels: Record<string, TranslationKey> = {
  'source.institution_name': 'shareInstitution', 'source.institution_portable_id': 'shareInstitutionId',
  'source.box_code': 'shareBoxCode', 'identity.species_scientific_name': 'shareSpecies',
  'identity.strain_code': 'shareStrain', 'identity.global_strain_id': 'shareStrainId',
  'observation.time': 'shareObservationTime', 'observation.polyp_count': 'sharePolyps',
  'observation.ephyrae_count': 'shareEphyrae', 'observation.strobila_count': 'shareStrobilae',
  'observation.origin': 'shareOrigin', 'observation.observer_status': 'shareObserver',
};
const stateKeys: Record<ShareState, TranslationKey> = {
  pending: 'shareStatePending', invitation_expired: 'shareStateInvitationExpired', active: 'shareStateActive',
  expired: 'shareStateExpired', revoked: 'shareStateRevoked', unavailable: 'shareStateUnavailable',
};
export const shareStateLabel = (state: ShareState, t: Translator) => t(stateKeys[state] ?? 'shareStateUnavailable');
export const selectorLabel = (name: string, t: Translator) => selectorLabels[name] ? t(selectorLabels[name]) : t('shareUnsupported');
export function validSelection(options: SharingOptions, selected: string[]) {
  return options.schema_version === '1.0' && options.mandatory_selectors.every(name => selected.includes(name))
    && selected.length > 0 && selected.every(name => {
      const option = options.selectors.find(item => item.name === name);
      return option && selectorLabels[name] && option.requires.every(required => selected.includes(required));
    });
}
export function selectorIsLocked(option: ShareSelector, options: ShareSelector[], selected: string[]) {
  return selected.includes(option.name)
    ? options.some(item => selected.includes(item.name) && item.requires.includes(option.name))
    : option.requires.some(required => !selected.includes(required));
}
export function fieldText(field: ScientificField | undefined, t: Translator, provenance = false): string {
  if (!field) return t('shareUndisclosed');
  if (field.state === 'value') {
    const enums: Record<string, TranslationKey> = { direct_entry: 'shareDirectEntry', file_import: 'shareFileImport',
      legacy_unclassified: 'shareLegacyOrigin', known: 'shareObserverKnown', not_recorded: 'shareObserverNotRecorded',
      legacy_unestablished: 'shareObserverLegacy' };
    return provenance && enums[String(field.value)] ? t(enums[String(field.value)]) : String(field.value);
  }
  return t(({ null: 'shareNull', blank: 'shareBlank', absent: 'shareAbsent' } as const)[field.state]);
}
export function snapshotSupported(snapshot: ScientificSnapshot) {
  return snapshot?.schema === 'polypbase.scientific-snapshot' && snapshot.schema_version === '1.0'
    && Array.isArray(snapshot.disclosed) && snapshot.disclosed.every(name => Boolean(selectorLabels[name]))
        && snapshot.disclosed.includes('source.institution_name');
}
export function parseInvitationToken(hash: string): string | null {
  const params = new URLSearchParams(hash.replace(/^#/, ''));
  const tokens = params.getAll('token');
  return tokens.length === 1 && tokens[0].length > 0 && tokens[0].length <= 200 ? tokens[0] : null;
}
export function isSharedPath(path: string) {
  return path === '/shared-boxes' || path.startsWith('/shared-boxes/');
}
export function sharedRoute(path: string): 'list' | 'accept' | 'invalid' | string {
  if (/^\/shared-boxes\/?$/.test(path)) return 'list';
  if (/^\/shared-boxes\/accept\/?$/.test(path)) return 'accept';
  const match = path.match(/^\/shared-boxes\/([a-f\d]{8}-[a-f\d]{4}-[a-f\d]{4}-[a-f\d]{4}-[a-f\d]{12})\/?$/i);
  return match?.[1] ?? 'invalid';
}
export function navigateShared(path: string, replace = false) {
  window.history[replace ? 'replaceState' : 'pushState'](null, '', path);
  window.dispatchEvent(new PopStateEvent('popstate'));
}
export function utcInput(value: string): string | null {
  if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(value)) return null;
  const date = new Date(`${value}:00Z`);
  return Number.isFinite(date.getTime()) && date.toISOString().slice(0, 16) === value ? date.toISOString() : null;
}
export function validExpiry(value: string, maximum: number | null, now = Date.now()) {
  const parsed = utcInput(value);
  return parsed !== null && maximum !== null && Date.parse(parsed) > now && Date.parse(parsed) <= now + maximum * 1000;
}
export function shareDate(value: string | null, language: Language) {
  if (value === null) return '—';
  const date = new Date(value);
  return Number.isFinite(date.getTime()) ? new Intl.DateTimeFormat(getLanguageLocale(language), {
    dateStyle: 'medium', timeStyle: 'short', timeZone: 'UTC',
  }).format(date) + ' UTC' : '—';
}
const errorKeys: Record<string, TranslationKey> = {
  sharing_policy_not_configured: 'shareErrorConfiguration', frozen_capture_unavailable: 'shareErrorFrozen',
  invalid_disclosure: 'shareErrorDisclosure', sharing_admin_required: 'shareErrorPermission',
  share_not_found: 'shareErrorNotFound', box_not_found: 'shareErrorBox', box_not_projectable: 'shareErrorBox',
  invitation_unavailable: 'shareErrorInvitation', invitation_expired: 'shareErrorInvitationExpired',
  invitation_recipient_mismatch: 'shareErrorMismatch', share_revoked: 'shareErrorRevoked', share_expired: 'shareErrorExpired',
  share_source_unavailable: 'shareErrorSource', share_policy_changed: 'shareErrorChanged',
  policy_revision_conflict: 'shareErrorRevision', concurrent_source_change: 'shareErrorChanged',
  disclosure_expansion_requires_confirmation: 'shareConfirmExpansion', frozen_policy_change_requires_recapture: 'shareConfirmRecapture',
  share_already_exists: 'shareErrorExists', email_delivery_unavailable: 'shareErrorDelivery',
  invitation_not_pending: 'shareErrorNotPending', invitation_expiry_out_of_bounds: 'shareErrorDates',
  access_expiry_out_of_bounds: 'shareErrorDates', invalid_recipient_email: 'shareErrorEmail', invalid_request: 'shareErrorValidation',
};
export function sharingErrorCode(error: unknown) {
  return error instanceof ApiError && error.data && typeof error.data === 'object'
    ? String((error.data as Record<string, unknown>).code ?? '') : '';
}
export function sharingError(error: unknown, t: Translator) {
  const key = errorKeys[sharingErrorCode(error)];
  if (key) return t(key);
  if (error instanceof ApiError && error.status === 403) return t('shareErrorPermission');
  if (error instanceof ApiError && error.status === 401) return t('loginRequired');
  if (error instanceof ApiError && error.status === 404) return t('shareErrorNotFound');
  return t('shareErrorNetwork');
}
