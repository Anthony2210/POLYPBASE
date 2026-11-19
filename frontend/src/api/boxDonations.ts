import type { BoxItem, PaginatedResponse } from '../types';
import { apiGet, apiPost, ApiResourceCancelledError, getOrganizationResourceSignal, getStoredActiveOrganizationId } from './client';

export type DonationState = 'pending' | 'cancelled';
export type DonationDirection = 'outgoing' | 'incoming';

type DonationIdentity = { first_name: string; last_name: string; email: string };
export type IncomingDonation = {
  proposal_id: string;
  state: DonationState;
  revision: number;
  created_at: string;
  cancelled_at: string | null;
  source_institution: { name: string };
  destination_institution: { name: string };
  box: {
    global_code: string;
    strain_code: string;
    species_scientific_name: string;
    global_strain_id: string;
    global_box_id: string;
  };
  history_verified_at: string;
};
export type OutgoingDonation = Omit<IncomingDonation, 'box'> & {
  box: IncomingDonation['box'] & { id: number; ownership_sequence: number };
  created_by: DonationIdentity;
  cancelled_by: DonationIdentity | null;
  history_verification: {
    verified_by: DonationIdentity;
    verified_at: string;
    snapshot_version: string;
    sha256: string;
    polyp_state_revision: number;
  };
};
export type DonationDetail = IncomingDonation | (OutgoingDonation & {
  history_verification: OutgoingDonation['history_verification'] & { current: boolean | null };
});
export type Donation = IncomingDonation | OutgoingDonation;

// Bind requests to the integer organization PK and its resource lifetime, never a portable UUID.
async function donationRequest<T>(organizationId: number, signal: AbortSignal, request: (options: RequestInit) => Promise<T>): Promise<T> {
  const context = getOrganizationResourceSignal();
  const controller = new AbortController();
  const abort = () => controller.abort();
  const check = () => {
    signal.throwIfAborted();
    context.throwIfAborted();
    if (getStoredActiveOrganizationId() !== organizationId) throw new ApiResourceCancelledError();
  };
  check();
  signal.addEventListener('abort', abort, { once: true });
  context.addEventListener('abort', abort, { once: true });
  try {
    const result = await request({ signal: controller.signal, cache: 'no-store' });
    check();
    return result;
  } finally {
    signal.removeEventListener('abort', abort);
    context.removeEventListener('abort', abort);
  }
}

function readDonation<T>(path: string, organizationId: number, signal: AbortSignal) {
  return donationRequest(organizationId, signal, (options) => apiGet<T>(path, options));
}

export type DonationDestination = { institution_id: string; name: string; country: string };
export type DonationSourceBox = Pick<BoxItem, 'id' | 'global_code' | 'organization' | 'status'>;
export type DonationSelection = { source_box_id: number; destination_institution_id: string };
export type DonationSubmission = DonationSelection & { request_id: string; history_verified: true; history_sha256: string };
export type DonationPreflight = { history: { sha256: string } };

export function listDonationDestinations(organizationId: number, signal: AbortSignal) {
  return readDonation<{ results: DonationDestination[] }>('/api/box-donations/destinations/', organizationId, signal);
}

export const DONATION_BOX_PAGE_SIZE = 20;
export async function listDonationSourceBoxes(organizationId: number, query: string, offset: number, signal: AbortSignal) {
  const params = new URLSearchParams({ organization: String(organizationId), status: 'active', q: query, limit: String(DONATION_BOX_PAGE_SIZE), offset: String(offset) });
  const page = await readDonation<PaginatedResponse<DonationSourceBox>>(`/api/boxes/?${params}`, organizationId, signal);
  if (page.results.some((box) => box.organization.id !== organizationId)) throw new ApiResourceCancelledError();
  return { ...page, results: page.results.filter((box) => box.status === 'active') };
}

export function preflightDonation(selection: DonationSelection, organizationId: number, signal: AbortSignal) {
  return donationRequest(organizationId, signal, (options) => apiPost<DonationPreflight>('/api/box-donations/preflight/', selection, options));
}

export function sendDonation(submission: DonationSubmission, organizationId: number, signal: AbortSignal) {
  return donationRequest(organizationId, signal, (options) => apiPost<OutgoingDonation>('/api/box-donations/outgoing/', submission, options));
}

export function listDonations(direction: 'outgoing', organizationId: number, signal: AbortSignal): Promise<{ results: OutgoingDonation[] }>;
export function listDonations(direction: 'incoming', organizationId: number, signal: AbortSignal): Promise<{ results: IncomingDonation[] }>;
export function listDonations(direction: DonationDirection, organizationId: number, signal: AbortSignal): Promise<{ results: Donation[] }>;
export function listDonations(direction: DonationDirection, organizationId: number, signal: AbortSignal) {
  return readDonation<{ results: Donation[] }>(`/api/box-donations/${direction}/`, organizationId, signal);
}
export function getDonation(proposalId: string, organizationId: number, signal: AbortSignal) {
  return readDonation<DonationDetail>(`/api/box-donations/${encodeURIComponent(proposalId)}/`, organizationId, signal);
}
