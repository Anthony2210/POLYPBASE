import { apiGet, apiPatch, apiPost, ApiResourceCancelledError, getStoredActiveOrganizationId } from './client';

export type ShareMode = 'live' | 'frozen';
export type ShareState = 'pending' | 'invitation_expired' | 'active' | 'expired' | 'revoked' | 'unavailable';
export type ScientificField = { state: 'value'; value: string | number } | { state: 'null' | 'blank' | 'absent' };
export type ScientificTime = { precision: 'exact_date' | 'legacy_unverified' | 'iso_week'; iso_year: number; iso_week: number; measured_on: string | null };
export type ScientificSnapshot = {
  schema: 'polypbase.scientific-snapshot'; schema_version: '1.0'; disclosed: string[];
  source?: Record<string, ScientificField>; identity?: Record<string, ScientificField>;
  observations?: Array<{ time: ScientificTime; values?: Record<string, ScientificField>; provenance?: Record<string, ScientificField> }>;
};
export type ShareSelector = { name: string; group: string; mandatory: boolean; requires: string[] };
export type SharingOptions = {
  schema: string; schema_version: string; frozen_capture_available: boolean; modes: ShareMode[];
  selectors: ShareSelector[]; mandatory_selectors: string[];
  lifetime_policy: { configured: boolean; invitation_max_lifetime_seconds: number | null; access_max_lifetime_seconds: number | null };
};
export type AdminShare = {
  id: number; box: { id: number; global_code: string }; recipient_email: string; mode: ShareMode; state: ShareState;
  schema_version: string; selectors: string[]; policy_revision: number;
  invitation: { generation: number; issued_at: string; expires_at: string; accepted_at: string | null;
    delivery: { status: 'pending' | 'sent' | 'failed' | 'uncertain'; updated_at: string | null } };
  access_expires_at: string; revoked_at: string | null; created_at: string;
  created_by: { first_name: string; last_name: string; email: string } | null;
  capture: { captured_at: string; content_sha256: string } | null;
};
export type ShareSummary = {
  id: string; mode: ShareMode; read_only: true; state: ShareState; policy_revision: number;
  schema_version: string; access_expires_at: string; source_institution: { name: string };
};
export type InvitationInfo = Pick<ShareSummary, 'mode' | 'read_only' | 'access_expires_at' | 'source_institution'> & { invitation_expires_at: string };
export type SharedBox = { share: ShareSummary; snapshot: ScientificSnapshot;
  content: { kind: 'frozen_capture'; captured_at: string; content_sha256: string } | { kind: 'live_projection'; projected_at: string; consistency: 'non_atomic' } };
export type ShareInput = { box_id: number; recipient_email: string; mode: ShareMode; selectors: string[]; invitation_expires_at: string; access_expires_at: string };
export type ShareUpdate = { expected_policy_revision: number; selectors?: string[]; access_expires_at?: string; confirm_expansion?: boolean; confirm_recapture?: boolean };
export type AdminSharePage = { shares: AdminShare[]; has_more: boolean; server_time: string };

const adminPath = '/api/admin/box-shares/';
const personalPath = '/api/shared-boxes/';
// These requests never join a deduplicated GET or persist scientific content.
export const recipientRequestOptions = (signal?: AbortSignal) => ({ skipOrganizationContext: true, cache: 'no-store' as const, signal });
function sourceOptions(organizationId: number, signal?: AbortSignal) {
  if (getStoredActiveOrganizationId() !== organizationId) throw new ApiResourceCancelledError();
  return { cache: 'no-store' as const, signal };
}
export const sharingApi = {
  options: (org: number, signal?: AbortSignal) => apiGet<SharingOptions>(`${adminPath}options/`, sourceOptions(org, signal)),
  list: (org: number, offset: number, boxId?: number, signal?: AbortSignal) => apiGet<AdminSharePage>(`${adminPath}?limit=25&offset=${offset}${boxId == null ? '' : `&box_id=${boxId}`}`, sourceOptions(org, signal)),
  detail: (org: number, id: number) => apiGet<{ share: AdminShare }>(`${adminPath}${id}/`, sourceOptions(org)),
  preview: (org: number, boxId: number, selectors: string[], signal?: AbortSignal) => apiPost<{ snapshot: ScientificSnapshot }>(`${adminPath}preview/`, { box_id: boxId, selectors }, sourceOptions(org, signal)),
  create: (org: number, input: ShareInput) => apiPost<{ share: AdminShare }>(adminPath, input, sourceOptions(org)),
  update: (org: number, id: number, input: ShareUpdate) => apiPatch<{ share: AdminShare }>(`${adminPath}${id}/`, input, sourceOptions(org)),
  revoke: (org: number, id: number) => apiPost<{ share: AdminShare }>(`${adminPath}${id}/revoke/`, {}, sourceOptions(org)),
  reissue: (org: number, id: number, expires: string) => apiPost<{ share: AdminShare }>(`${adminPath}${id}/reissue-invitation/`, { invitation_expires_at: expires }, sourceOptions(org)),
  received: (signal?: AbortSignal) => apiGet<{ shares: ShareSummary[]; server_time: string }>(personalPath, recipientRequestOptions(signal)),
  read: (id: string, signal?: AbortSignal) => apiGet<SharedBox>(`${personalPath}${encodeURIComponent(id)}/`, recipientRequestOptions(signal)),
  inspect: (token: string, signal?: AbortSignal) => apiPost<{ invitation: InvitationInfo }>(`${personalPath}invitations/inspect/`, { token }, recipientRequestOptions(signal)),
  accept: (token: string, signal?: AbortSignal) => apiPost<{ share: ShareSummary }>(`${personalPath}invitations/accept/`, { token }, recipientRequestOptions(signal)),
};
