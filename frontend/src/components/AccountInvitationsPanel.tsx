import { useEffect, useRef, useState } from 'react';

import type { AccountInvitation } from '../types';
import {
  formatInvitationCountdown,
  getInvitationRowClassName,
  getInvitationView,
  getServerClockOffset,
  type InvitationsSnapshot,
  needsExpiryRevalidation,
  runInvitationRevalidation,
} from '../utils/accountInvitations';

type TFunction = (key: string) => string;

export type { InvitationsSnapshot };

function wait(ms: number) {
  return new Promise<void>((resolve) => {
    window.setTimeout(resolve, ms);
  });
}

export function AccountInvitationsTable({
  invitations,
  serverNowMs,
  busyIds,
  feedback,
  getDisplayName,
  onResend,
  t,
}: {
  invitations: readonly AccountInvitation[];
  serverNowMs: number;
  busyIds: ReadonlySet<number>;
  feedback: { id: number; tone: 'positive' | 'negative' } | null;
  getDisplayName: (invitation: AccountInvitation) => string;
  onResend: (invitation: AccountInvitation) => void;
  t: TFunction;
}) {
  return (
    <div className="member-table-shell">
      <table className="member-table invitation-table">
        <thead>
          <tr>
            <th scope="col">{t('manageColUser')}</th>
            <th scope="col">{t('manageColRole')}</th>
            <th scope="col">{t('manageColStatus')}</th>
            <th scope="col">{t('manageColValidity')}</th>
            <th scope="col" className="member-action-heading" />
          </tr>
        </thead>
        <tbody>
          {invitations.map((invitation) => {
            const view = getInvitationView(invitation, serverNowMs);
            const name = getDisplayName(invitation);
            const isExpired = view.status === 'expired';
            const isBusy = busyIds.has(invitation.id);
            return (
              <tr
                key={invitation.id}
                className={getInvitationRowClassName(
                  view.status,
                  feedback?.id === invitation.id ? feedback.tone : null,
                )}
              >
                <td>
                  <span className="member-identity">
                    <strong>{name}</strong>
                    {invitation.full_name.trim() && invitation.email ? (
                      <small>{invitation.email}</small>
                    ) : null}
                  </span>
                </td>
                <td>
                  <span className="member-role-value">{invitation.role_label}</span>
                </td>
                <td>
                  <span className={isExpired ? 'member-state is-off' : 'member-state is-on'}>
                    {isExpired ? t('manageInvitationExpired') : t('manageInvitationPending')}
                  </span>
                </td>
                <td className="invitation-validity">
                  {view.remainingMs != null && invitation.expires_at ? (
                    <time dateTime={invitation.expires_at}>
                      {`${t('manageInvitationExpiresIn')} ${formatInvitationCountdown(view.remainingMs)}`}
                    </time>
                  ) : (
                    <span aria-hidden="true">-</span>
                  )}
                </td>
                <td className="member-action-cell">
                  {invitation.can_resend ? (
                    <button
                      type="button"
                      className="invitation-resend"
                      aria-label={`${t('manageInvitationResendFor')} ${name}`}
                      aria-disabled={isBusy}
                      aria-busy={isBusy}
                      onClick={() => {
                        if (!isBusy) onResend(invitation);
                      }}
                    >
                      {isBusy ? t('manageInvitationResending') : t('manageInvitationResend')}
                    </button>
                  ) : null}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

export default function AccountInvitationsPanel({
  snapshot,
  busyIds,
  feedback,
  getDisplayName,
  onResend,
  onRevalidate,
  t,
}: {
  snapshot: InvitationsSnapshot;
  busyIds: ReadonlySet<number>;
  feedback: { id: number; tone: 'positive' | 'negative' } | null;
  getDisplayName: (invitation: AccountInvitation) => string;
  onResend: (invitation: AccountInvitation) => void;
  // Resolves true when fresh backend state was applied.
  onRevalidate: () => Promise<boolean>;
  t: TFunction;
}) {
  const { invitations, serverTime, receivedAtMs } = snapshot;
  const offsetMs = getServerClockOffset(serverTime, receivedAtMs);
  const [localNowMs, setLocalNowMs] = useState(() => Date.now());
  const serverNowMs = localNowMs + offsetMs;
  const hasPending = invitations.some(
    (invitation) => getInvitationView(invitation, serverNowMs).status === 'pending',
  );
  const needsRevalidation = invitations.some((invitation) =>
    needsExpiryRevalidation(invitation, serverNowMs));
  const revalidationsRef = useRef(0);

  // The local clock only drives the display; it ticks while a countdown is visible.
  useEffect(() => {
    setLocalNowMs(Date.now());
    if (!hasPending) return undefined;
    const timer = window.setInterval(() => setLocalNowMs(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [hasPending, snapshot]);

  // A row the local clock calls expired is only resendable once the backend
  // agrees, so ask it again with bounded retries. A failed refresh leaves the
  // error with its retry action visible in the section.
  useEffect(() => {
    if (!needsRevalidation) {
      revalidationsRef.current = 0;
      return undefined;
    }
    let cancelled = false;
    runInvitationRevalidation({
      attempts: revalidationsRef,
      revalidate: onRevalidate,
      wait,
      isCancelled: () => cancelled,
    });
    return () => {
      cancelled = true;
    };
  }, [needsRevalidation, snapshot]);

  return (
    <AccountInvitationsTable
      invitations={invitations}
      serverNowMs={serverNowMs}
      busyIds={busyIds}
      feedback={feedback}
      getDisplayName={getDisplayName}
      onResend={onResend}
      t={t}
    />
  );
}
