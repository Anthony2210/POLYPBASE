import type { AccountInvitation, AccountInvitationStatus } from '../types';

export type InvitationView = {
  status: AccountInvitationStatus;
  remainingMs: number | null;
};

/** Offset to add to the local clock to get server time, measured when a response arrived. */
export function getServerClockOffset(serverTime: string, receivedAtMs: number): number {
  const serverMs = Date.parse(serverTime);
  return Number.isNaN(serverMs) ? 0 : serverMs - receivedAtMs;
}

/**
 * Display state of one invitation at a server-corrected instant. The backend
 * status stays authoritative: a row the backend already calls expired never
 * comes back to pending, and a pending row turns expired only once its real
 * expiry has passed (valid up to and including `expires_at`).
 */
export function getInvitationView(
  invitation: Pick<AccountInvitation, 'status' | 'expires_at'>,
  serverNowMs: number,
): InvitationView {
  if (invitation.status === 'expired' || invitation.expires_at == null) {
    return { status: 'expired', remainingMs: null };
  }
  const remainingMs = Date.parse(invitation.expires_at) - serverNowMs;
  if (Number.isNaN(remainingMs) || remainingMs < 0) {
    return { status: 'expired', remainingMs: null };
  }
  return { status: 'pending', remainingMs };
}

/** True when the backend still says pending but the real expiry has passed. */
export function needsExpiryRevalidation(
  invitation: Pick<AccountInvitation, 'status' | 'expires_at'>,
  serverNowMs: number,
): boolean {
  return invitation.status === 'pending'
    && getInvitationView(invitation, serverNowMs).status === 'expired';
}

function pad(value: number): string {
  return String(value).padStart(2, '0');
}

/** Language neutral countdown: "23 h 59 min", "12 min 05 s" or "45 s". */
export function formatInvitationCountdown(remainingMs: number): string {
  const totalSeconds = Math.max(0, Math.ceil(remainingMs / 1000));
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  if (hours > 0) return `${hours} h ${pad(minutes)} min`;
  if (minutes > 0) return `${minutes} min ${pad(seconds)} s`;
  return `${seconds} s`;
}

export function getInvitationRowClassName(
  status: AccountInvitationStatus,
  tone: 'positive' | 'negative' | null,
): string {
  const classes = ['invitation-row', status === 'expired' ? 'is-expired' : 'is-pending'];
  if (tone === 'positive') classes.push('is-updated-positive');
  else if (tone === 'negative') classes.push('is-updated-negative');
  return classes.join(' ');
}

/**
 * Request generations for the invitation list, following the request-generation
 * guard used elsewhere in the app: every load and every successful mutation
 * starts a new generation, and a response may only be applied while its own
 * generation is still the latest. A GET started before a resend can therefore
 * never overwrite the state that resend returned.
 */
export type InvitationRequestGuard = {
  begin: () => number;
  invalidate: () => void;
  isCurrent: (generation: number) => boolean;
};

export function createInvitationRequestGuard(): InvitationRequestGuard {
  let latest = 0;
  return {
    begin: () => {
      latest += 1;
      return latest;
    },
    invalidate: () => {
      latest += 1;
    },
    isCurrent: (generation) => generation === latest,
  };
}

// Bounded expiry revalidation: one immediate refresh, then two spaced retries.
// After that the error stays visible with an explicit retry action.
export const INVITATION_REVALIDATION_DELAYS_MS = [0, 2000, 5000] as const;

export function getRevalidationDelay(attempt: number): number | null {
  return attempt < INVITATION_REVALIDATION_DELAYS_MS.length
    ? INVITATION_REVALIDATION_DELAYS_MS[attempt]
    : null;
}

export type RevalidationOutcome = 'refreshed' | 'exhausted' | 'cancelled';

/**
 * Ask the backend again after a local expiry. `attempts` survives across runs so
 * a backend that keeps answering "pending" cannot cause unbounded requests.
 */
export async function runInvitationRevalidation({
  attempts,
  revalidate,
  wait,
  isCancelled,
}: {
  attempts: { current: number };
  revalidate: () => Promise<boolean>;
  wait: (ms: number) => Promise<void>;
  isCancelled: () => boolean;
}): Promise<RevalidationOutcome> {
  for (;;) {
    const delay = getRevalidationDelay(attempts.current);
    if (delay == null) return 'exhausted';
    if (delay > 0) await wait(delay);
    if (isCancelled()) return 'cancelled';
    attempts.current += 1;
    const refreshed = await revalidate();
    if (isCancelled()) return 'cancelled';
    if (refreshed) return 'refreshed';
  }
}

export type InvitationsSnapshot = {
  invitations: AccountInvitation[];
  serverTime: string;
  // Local clock reading when the response arrived, to measure the clock skew.
  receivedAtMs: number;
};

export type InvitationListResponse = {
  invitations: AccountInvitation[];
  server_time: string;
};

/** Replace the row with the same id, or append it. */
export function upsertInvitation(
  invitations: readonly AccountInvitation[],
  invitation: AccountInvitation,
): AccountInvitation[] {
  return invitations.some((item) => item.id === invitation.id)
    ? invitations.map((item) => (item.id === invitation.id ? invitation : item))
    : [...invitations, invitation];
}

/**
 * Single source of truth for the Invitations list of one organization.
 *
 * - `load` applies a GET only while it is still the latest logical request.
 * - `applyMutation` merges a server-returned row (resend, creation), discards
 *   every load started earlier, and detaches the in-flight HTTP GET so a later
 *   load cannot join a request that captured the state before the mutation.
 */
export function createInvitationsStore({
  request,
  invalidateRequest,
  isActive,
  onChange,
  onError,
  now = () => Date.now(),
}: {
  request: () => Promise<InvitationListResponse>;
  invalidateRequest: () => void;
  isActive: () => boolean;
  onChange: (snapshot: InvitationsSnapshot, options: { keepError: boolean }) => void;
  onError: (error: unknown) => void;
  now?: () => number;
}) {
  const guard = createInvitationRequestGuard();
  let snapshot: InvitationsSnapshot | null = null;

  function publish(next: InvitationsSnapshot, keepError: boolean) {
    snapshot = next;
    onChange(next, { keepError });
  }

  return {
    getSnapshot: () => snapshot,

    async load({ keepError = false }: { keepError?: boolean } = {}): Promise<boolean> {
      const generation = guard.begin();
      const isCurrent = () => isActive() && guard.isCurrent(generation);
      try {
        const response = await request();
        if (!isCurrent()) return false;
        publish(
          { invitations: response.invitations, serverTime: response.server_time, receivedAtMs: now() },
          keepError,
        );
        return true;
      } catch (error) {
        if (!isCurrent()) return false;
        onError(error);
        return false;
      }
    },

    /**
     * Returns true when the row is now displayed. Without a loaded list there is
     * no server clock to anchor the countdown, so the caller reloads instead.
     */
    applyMutation(invitation: AccountInvitation, serverTime?: string): boolean {
      if (!isActive()) return false;
      guard.invalidate();
      invalidateRequest();
      if (snapshot == null) return false;
      const invitations = upsertInvitation(snapshot.invitations, invitation);
      publish(
        serverTime
          ? { invitations, serverTime, receivedAtMs: now() }
          : { ...snapshot, invitations },
        true,
      );
      return true;
    },
  };
}
