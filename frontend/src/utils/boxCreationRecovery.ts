import { ApiError, ApiResourceCancelledError } from '../api/client';
import type { BoxCreatePayload, BoxCreationIntent } from '../types';

// Bounded automatic retries for an uncertain outcome; connectivity or reopening restarts them.
export const CREATION_RETRY_DELAYS_MS = [1000, 2000, 4000, 8000, 15000, 30000];

const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

// Two independent records per actor and institution: the raw request UUID, written
// first and readable without JSON, and the JSON envelope with the replayable payload.
// A UUID alone is inspection-only. A complete v2 envelope also carries a separate
// recovery token, scope and immutable payload; registration verifies their binding.
export type StoredCreationIntent =
  | {
    kind: 'pending';
    intent: BoxCreatePayload | BoxCreationIntent | { request_id: string };
    canRestoreDraft: boolean;
    canResume: boolean;
    digest?: string;
    // Every candidate key must be terminal before the records may be cleared.
    // Two valid but different UUIDs cannot be attributed to one another, so neither wins.
    requestIds: string[];
  }
  // Something was stored but no request UUID survives: it cannot be settled.
  | { kind: 'unidentified' };

export function creationStorageKey(actorId: number | null | undefined, organizationId: number | null | undefined) {
  return actorId == null || organizationId == null ? null : `polypbase.automaticBox.${actorId}.${organizationId}`;
}

function requestKeyStorageKey(storageKey: string) {
  return `${storageKey}.requestId`;
}


function validUuid(value: unknown): string | null {
  return typeof value === 'string' && UUID_PATTERN.test(value) ? value.toLowerCase() : null;
}

function isReplayablePayload(record: Record<string, unknown>) {
  return Number.isInteger(record.strain) && Number.isInteger(record.thermal_zone)
    && typeof record.entered_on === 'string' && record.entered_on !== ''
    && (record.notes === undefined || typeof record.notes === 'string')
    && (record.volume_liters === undefined || record.volume_liters === null || typeof record.volume_liters === 'string');
}

export function readStoredCreationIntent(storageKey: string | null): StoredCreationIntent | null {
  if (!storageKey) return null;
  let rawEnvelope: string | null;
  let rawKey: string | null;
  try {
    rawEnvelope = window.sessionStorage.getItem(storageKey);
    rawKey = window.sessionStorage.getItem(requestKeyStorageKey(storageKey));
  } catch {
    // Unreadable is not empty: a delayed request may still exist.
    return { kind: 'unidentified' };
  }
  if (rawEnvelope == null && rawKey == null) return null;

  let record: Record<string, unknown> | null = null;
  try {
    const value: unknown = rawEnvelope == null ? null : JSON.parse(rawEnvelope);
    record = value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : null;
  } catch {
    record = null;
  }
  const envelopeId = validUuid(record?.request_id);
  const keyId = validUuid(rawKey?.trim());

  if (!keyId && !envelopeId) return { kind: 'unidentified' };
  if (keyId && envelopeId && keyId !== envelopeId) {
    // Divergent records: either key may have committed or still be in flight.
    return { kind: 'pending', intent: { request_id: envelopeId }, canRestoreDraft: false, canResume: false, requestIds: [envelopeId, keyId] };
  }
  const requestId = (keyId ?? envelopeId)!;
  const canRestoreDraft = record != null && envelopeId === requestId && isReplayablePayload(record);
  const canResume = canRestoreDraft && record?.version === 2 && record.scope === storageKey
    && validUuid(record.resume_token) != null && typeof record.digest === 'string';
  const intent = canRestoreDraft ? {
    request_id: requestId,
    strain: record!.strain as number,
    thermal_zone: record!.thermal_zone as number,
    entered_on: record!.entered_on as string,
    volume_liters: record!.volume_liters as string | null | undefined,
    notes: record!.notes as string | undefined,
    ...(canResume ? { resume_token: validUuid(record!.resume_token)! } : {}),
  } : { request_id: requestId };
  return { kind: 'pending', intent, canRestoreDraft, canResume, digest: canResume ? record!.digest as string : undefined, requestIds: [requestId] };
}

export async function creationIntentDigest(storageKey: string, intent: BoxCreationIntent) {
  const content = JSON.stringify([
    storageKey, intent.request_id, intent.resume_token, intent.strain, intent.thermal_zone,
    intent.entered_on, intent.volume_liters ?? null, intent.notes ?? '',
  ]);
  const digest = await window.crypto.subtle.digest('SHA-256', new TextEncoder().encode(content));
  return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, '0')).join('');
}

/** Persist the complete envelope before any request; on failure nothing may be sent. */
export async function storeCreationIntent(storageKey: string, intent: BoxCreationIntent) {
  try {
    const digest = await creationIntentDigest(storageKey, intent);
    window.sessionStorage.setItem(requestKeyStorageKey(storageKey), intent.request_id);
    window.sessionStorage.setItem(storageKey, JSON.stringify({ ...intent, version: 2, scope: storageKey, digest }));
    return true;
  } catch {
    // Nothing was sent; any leftover key may only be inspected after a reload.
    forgetStoredCreationIntent(storageKey);
    return false;
  }
}

/** Only for server-settled keys, or a failed local write before any network request. */
export function forgetStoredCreationIntent(storageKey: string | null) {
  if (!storageKey) return;
  for (const key of [storageKey, requestKeyStorageKey(storageKey)]) {
    try {
      window.sessionStorage.removeItem(key);
    } catch {
      // A stale settled copy is resolved again harmlessly on the next load.
    }
  }
}

/** True when the server may or may not have committed the request. */
export function isUncertainCreationError(error: unknown) {
  if (error instanceof ApiResourceCancelledError) return false;
  if (!(error instanceof ApiError)) return true;
  return error.status === 0 || error.status === 408 || error.status === 429 || error.status >= 500;
}

export function todayLocalIsoDate(now = new Date()) {
  const pad = (value: number) => String(value).padStart(2, '0');
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
}
