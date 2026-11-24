import { type FormEvent, useEffect, useRef, useState } from 'react';

import { ApiError, apiGet, apiPatch, apiPost, invalidateInFlightGet } from '../api/client';
import type {
  AccountInvitation,
  AccountInvitationResendResponse,
  AccountInvitations,
  AccountMember,
  AccountMemberCreated,
  AccountMembers,
  BoxActivatePayload,
  BoxDeactivatePayload,
  BoxInitialLocationPayload,
  BoxInventoryBatchQualifyPayload,
  BoxInventoryBatchResult,
  BoxItem,
  BoxQualifyPayload,
  EditableMeasurement,
  MembershipRole,
  NewMemberPayload,
  ResponsableRelinquishResponse,
  ThermalZone,
  UserProfile,
} from '../types';
import type { Language, Translator } from '../i18n';
import type {
  ThermalZonePayload,
  ThermalZoneDeletionEligibility,
  ThermalZoneUpdatePayload,
} from '../types/admin';
import {
  filterMembersByRole,
  getAccountMemberRoleLabel,
  getMemberRoleCounts,
  getMemberRowActions,
  type MemberRoleFilter,
  type MemberRowAction,
} from '../utils/accountMembers';
import type { BoxCollectionStatus } from '../utils/boxCollection';
import { formatRelativeDateTime } from '../utils/dateFormat';
import { formatFirstName, formatLastName, formatReadableUserIdentity } from '../utils/userIdentity';

import { getAccountErrorMessage } from '../utils/errors';
import { createInvitationsStore } from '../utils/accountInvitations';
import { beginMemberMutation, endMemberMutation } from '../utils/memberMutationLock';
import {
  getMemberRowClassName,
  MEMBER_FEEDBACK_MS,
  MEMBER_MUTATION_FEEDBACK,
  type MemberFeedbackTone,
  type MemberMutationKind,
} from '../utils/memberFeedback';
import AccountInvitationsPanel, { type InvitationsSnapshot } from './AccountInvitationsPanel';
import AdminActionPanel from './AdminActionPanel';
import AdminAuditSection from './AdminAuditSection';
import BoxInventoryAdminSection from './BoxInventoryAdminSection';
import InstitutionalExchangesPage from './sharing/InstitutionalExchangesPage';
import { useConfirmAction } from './ConfirmActionModal';
import LabConfigurationAdminSection from './LabConfigurationAdminSection';
import PageLoader from './PageLoader';
import PolypbaseIcon from './PolypbaseIcon';
import { RowActionMenu } from './RowActionMenu';
import SkeletonRows from './SkeletonRows';
import SpeciesStrainsAdminSection from './SpeciesStrainsAdminSection';
import type { AdminSpeciesTarget } from '../utils/speciesCatalog';

export type { EditableMeasurement } from '../types';

const ADMIN_FLOW_ITEMS = [
  { key: 'accounts', panelId: 'admin-accounts', label: 'adminTabUsers' },
  { key: 'inventory', panelId: 'admin-inventory', label: 'adminTabInventory' },
  { key: 'species', panelId: 'admin-species', label: 'adminTabSpecies' },
  { key: 'environment', panelId: 'admin-environment', label: 'adminTabLocations' },
  { key: 'transfers', panelId: 'admin-transfers', label: 'adminTabTransfers' },
  { key: 'history', panelId: 'admin-history', label: 'adminTabHistory' },
] as const;

export type AdminSectionKey = (typeof ADMIN_FLOW_ITEMS)[number]['key'];

function userHasAdminRole(profile: UserProfile | null) {
  if (!profile) return false;
  if (profile.is_superuser) return true;
  if (!profile.active_organization) return false;
  return profile.memberships.some(
    (membership) => membership.role === 'admin'
      && membership.organization.id === profile.active_organization?.id,
  );
}


const INVITATIONS_PATH = '/api/accounts/invitations/';

const emptyMemberForm = {
  first_name: '',
  last_name: '',
  email: '',
};

function getMemberDisplayName(
  member: Pick<AccountMember, 'first_name' | 'last_name' | 'email'>,
) {
  return formatReadableUserIdentity(member);
}

type MemberFieldErrors = Partial<Record<'email', string>>;

function getMemberFieldError(error: unknown, field: keyof MemberFieldErrors) {
  if (!(error instanceof ApiError) || !error.data || typeof error.data !== 'object') return null;
  const value = (error.data as Record<string, unknown>)[field];
  if (typeof value === 'string') return value;
  if (Array.isArray(value)) {
    const message = value.find((item): item is string => typeof item === 'string');
    return message ?? null;
  }
  return null;
}

function AccountManagementSection({
  organizationName,
  onResponsableChange,
  t,
}: {
  organizationName: string;
  onResponsableChange: (organizationId: number, isResponsable: boolean) => void;
  t: Translator;
}) {
  const [data, setData] = useState<AccountMembers | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [form, setForm] = useState(emptyMemberForm);
  const [organizationId, setOrganizationId] = useState<number | null>(null);
  const [role, setRole] = useState<MembershipRole>('viewer');
  const [isAdding, setIsAdding] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [formFieldErrors, setFormFieldErrors] = useState<MemberFieldErrors>({});
  const [feedback, setFeedback] = useState<{
    membershipId: number;
    message: string;
    tone: MemberFeedbackTone;
  } | null>(null);
  const feedbackTimerRef = useRef<number | null>(null);
  const busyMemberIdsRef = useRef<Set<number>>(new Set());
  const [busyMemberIds, setBusyMemberIds] = useState<ReadonlySet<number>>(() => new Set());
  const [roleFilter, setRoleFilter] = useState<MemberRoleFilter>('all');
  const [isMemberFormOpen, setIsMemberFormOpen] = useState(false);
  const [isInvitationInfoOpen, setIsInvitationInfoOpen] = useState(false);
  const [invitationSnapshot, setInvitationSnapshot] = useState<InvitationsSnapshot | null>(null);
  const [invitationsError, setInvitationsError] = useState<string | null>(null);
  const isMountedRef = useRef(true);
  const tRef = useRef(t);
  tRef.current = t;
  // One store per mounted section; the section is keyed by organization.
  const invitationsStoreRef = useRef<ReturnType<typeof createInvitationsStore> | null>(null);
  if (invitationsStoreRef.current == null) {
    invitationsStoreRef.current = createInvitationsStore({
      request: () => apiGet<AccountInvitations>(INVITATIONS_PATH),
      invalidateRequest: () => invalidateInFlightGet(INVITATIONS_PATH),
      isActive: () => isMountedRef.current,
      onChange: (snapshot, { keepError }) => {
        setInvitationSnapshot(snapshot);
        if (!keepError) setInvitationsError(null);
      },
      onError: (requestError) => setInvitationsError(getAccountErrorMessage(requestError, tRef.current)),
    });
  }
  const { confirmAction, confirmActionModal } = useConfirmAction();

  useEffect(() => {
    isMountedRef.current = true;
    return () => {
      isMountedRef.current = false;
    };
  }, []);

  // Resolves true only when this response became the displayed state.
  function loadInvitations(options?: { keepError?: boolean }): Promise<boolean> {
    return invitationsStoreRef.current!.load(options);
  }

  useEffect(() => {
    let isActive = true;

    loadInvitations();

    async function loadMembers() {
      try {
        setIsLoading(true);
        const response = await apiGet<AccountMembers>('/api/accounts/members/');
        if (!isActive) return;
        setData(response);
        setOrganizationId(response.manageable_organizations[0]?.id ?? null);
        setRole(response.roles.find((roleOption) => roleOption.value === 'viewer')?.value
          ?? response.roles[0]?.value
          ?? 'viewer');
      } catch (requestError) {
        if (!isActive) return;
        setLoadError(getAccountErrorMessage(requestError, t));
      } finally {
        if (isActive) setIsLoading(false);
      }
    }

    loadMembers();
    return () => {
      isActive = false;
    };
  }, []);

  function upsertMember(updated: AccountMember) {
    setData((current) => {
      if (!current) return current;
      const exists = current.members.some((member) => member.membership_id === updated.membership_id);
      const members = exists
        ? current.members.map((member) =>
            member.membership_id === updated.membership_id ? updated : member,
          )
        : [...current.members, updated];
      members.sort(
        (a, b) =>
          a.organization.name.localeCompare(b.organization.name) ||
          getMemberDisplayName(a).localeCompare(getMemberDisplayName(b)) ||
          a.email.localeCompare(b.email),
      );
      return { ...current, members };
    });
  }

  async function handleAddMember(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (isAdding || organizationId == null || data == null) return;

    setIsAdding(true);
    setFormError(null);
    setFormFieldErrors({});

    const payload: NewMemberPayload = {
      ...form,
      email: form.email.trim(),
      first_name: formatFirstName(form.first_name),
      last_name: formatLastName(form.last_name),
      organization_id: organizationId,
      role,
    };

    try {
      // A new access is an invitation until the person chooses a password, so it
      // belongs to the Invitations subsection and not to the members list.
      const member = await apiPost<AccountMemberCreated>('/api/accounts/members/', payload);
      if (!isMountedRef.current) return;
      // Show the server's own row for the new invitation right away, so an
      // overlapping resend that discards the refresh below cannot hide it.
      invitationsStoreRef.current!.applyMutation(member.invitation);

      // Reconcile ordering and clock with a fresh list; a failure leaves the
      // row visible and shows the retry action.
      await loadInvitations();
      if (!isMountedRef.current) return;
      setForm(emptyMemberForm);
      setRole(data.roles.find((roleOption) => roleOption.value === 'viewer')?.value
        ?? data.roles[0]?.value
        ?? 'viewer');
      setFormFieldErrors({});
      setIsMemberFormOpen(false);
      showMemberFeedback(member.membership_id, 'created');
    } catch (requestError) {
      const fieldErrors = {
        email: getMemberFieldError(requestError, 'email') ?? undefined,
      };
      setFormFieldErrors(fieldErrors);
      setFormError(fieldErrors.email ? null : getAccountErrorMessage(requestError, t));
    } finally {
      setIsAdding(false);
    }
  }

  // Clears any pending row feedback when the section unmounts.
  useEffect(() => () => {
    if (feedbackTimerRef.current != null) window.clearTimeout(feedbackTimerRef.current);
  }, []);

  function showMemberFeedback(membershipId: number, kind: MemberMutationKind) {
    if (feedbackTimerRef.current != null) window.clearTimeout(feedbackTimerRef.current);
    const { key, tone } = MEMBER_MUTATION_FEEDBACK[kind];
    setFeedback({ membershipId, message: t(key), tone });
    feedbackTimerRef.current = window.setTimeout(() => {
      feedbackTimerRef.current = null;
      setFeedback(null);
    }, MEMBER_FEEDBACK_MS);
  }

  function beginBusyMember(membershipId: number): boolean {
    if (!beginMemberMutation(busyMemberIdsRef.current, membershipId)) return false;
    setBusyMemberIds(new Set(busyMemberIdsRef.current));
    return true;
  }

  function endBusyMember(membershipId: number) {
    endMemberMutation(busyMemberIdsRef.current, membershipId);
    setBusyMemberIds(new Set(busyMemberIdsRef.current));
  }

  async function handleInvitationResend(invitation: AccountInvitation) {
    if (!invitation.can_resend) return;
    setInvitationsError(null);
    if (!beginBusyMember(invitation.id)) return;

    try {
      const response = await apiPost<AccountInvitationResendResponse>(
        `/api/accounts/invitations/${invitation.id}/resend/`,
        {},
      );
      if (!isMountedRef.current) return;
      // Discards every list request started before this success.
      invitationsStoreRef.current!.applyMutation(response.invitation, response.server_time);
      setInvitationsError(null);
      showMemberFeedback(invitation.id, 'resend_invitation');
    } catch (requestError) {
      if (!isMountedRef.current) return;
      setInvitationsError(getAccountErrorMessage(requestError, t));
      // The invitation changed under us (resent elsewhere or accepted): resync,
      // keeping the explanation visible.
      if (requestError instanceof ApiError && requestError.status === 409) {
        await loadInvitations({ keepError: true });
      }
    } finally {
      endBusyMember(invitation.id);
    }
  }

  function memberActionLabel(action: MemberRowAction): string {
    switch (action) {
      case 'promote':
        return t('managePromoteToTechnician');
      case 'demote':
        return t('manageDemoteToViewer');
      case 'promote_to_admin':
        return t('managePromoteToAdmin');
      case 'demote_to_technician':
        return t('manageDemoteToTechnician');
      case 'deactivate':
        return t('manageDeactivate');
      case 'reactivate':
        return t('manageReactivate');
      case 'relinquish_responsable':
        return t('manageRelinquishResponsable');
    }
  }

  async function handleMemberAction(member: AccountMember, action: MemberRowAction) {
    setLoadError(null);

    if (action === 'deactivate' || action === 'relinquish_responsable') {
      const isRelinquishing = action === 'relinquish_responsable';
      const confirmed = await confirmAction({
        title: t(isRelinquishing
          ? 'manageRelinquishResponsableConfirmTitle'
          : 'manageDeactivateConfirmTitle'),
        message: t(isRelinquishing
          ? 'manageRelinquishResponsableConfirmMessage'
          : 'manageDeactivateConfirmMessage'),
        details: [
          { label: t('manageConfirmMember'), value: getMemberDisplayName(member) },
          { label: t('manageConfirmOrganization'), value: organizationName },
        ],
        confirmLabel: t(isRelinquishing ? 'manageRelinquishResponsable' : 'manageDeactivate'),
        cancelLabel: t('confirmCancel'),
        variant: 'danger',
      });

      if (!confirmed) return;
    }

    // The dialog and the confirmation resolver already protect the confirmation
    // phase; the lock only needs to cover the in-flight request.
    if (!beginBusyMember(member.membership_id)) return;

    try {
      if (action === 'relinquish_responsable') {
        const response = await apiPost<ResponsableRelinquishResponse>(
          '/api/accounts/responsable/relinquish/',
          {},
        );
        setData((current) => current
          ? {
              ...current,
              members: current.members.map((item) =>
                item.membership_id === response.member.membership_id ? response.member : item,
              ),
              roles: response.roles,
              can_manage_admin_memberships: response.can_manage_admin_memberships,
              can_relinquish_responsable: response.can_relinquish_responsable,
            }
          : current);
        setRole((currentRole) => response.roles.some((roleOption) => roleOption.value === currentRole)
          ? currentRole
          : response.roles.find((roleOption) => roleOption.value === 'viewer')?.value
            ?? response.roles[0]?.value
            ?? 'viewer');
        onResponsableChange(response.member.organization.id, response.member.is_responsable);
        showMemberFeedback(member.membership_id, action);
      } else {
        const payload = action === 'promote'
          ? { role: 'lab_technician' as MembershipRole }
          : action === 'demote'
            ? { role: 'viewer' as MembershipRole }
            : action === 'promote_to_admin'
              ? { role: 'admin' as MembershipRole }
              : action === 'demote_to_technician'
                ? { role: 'lab_technician' as MembershipRole }
                : { is_active: action === 'reactivate' };
        const updated = await apiPatch<AccountMember>(
          `/api/accounts/members/${member.membership_id}/`,
          payload,
        );
        upsertMember(updated);
        showMemberFeedback(member.membership_id, action);
      }
    } catch (requestError) {
      setLoadError(getAccountErrorMessage(requestError, t));
    } finally {
      endBusyMember(member.membership_id);
    }
  }

  if (isLoading) {
    return (
      <section className="profile-block">
        <div className="section-title">
          <h2>{t('manageAccountsTitle')}</h2>
        </div>
        <SkeletonRows count={3} />
      </section>
    );
  }

  if (loadError && !data) {
    return (
      <section className="profile-block">
        <div className="section-title">
          <h2>{t('manageAccountsTitle')}</h2>
        </div>
        <p className="inline-error">{loadError}</p>
      </section>
    );
  }

  if (!data) return null;

  const roles = data.roles;
  const memberCounts = getMemberRoleCounts(data.members);
  const filteredMembers = filterMembersByRole(data.members, roleFilter);

  function toggleRoleFilter(nextRole: MembershipRole) {
    setRoleFilter((currentRole) => (currentRole === nextRole ? 'all' : nextRole));
  }

  const selectedRoleDescription = role === 'admin'
    ? t('roleDescAdmin')
    : role === 'lab_technician'
      ? t('roleDescTechnician')
      : t('roleDescViewer');

  return (
    <section className="admin-section account-management" id="admin-accounts">
      <div className="admin-section-heading account-management-heading section-title">
        <div>
          <h2>{t('manageAccountsTitle')}</h2>
        </div>
        <button
          className="admin-primary-action"
          type="button"
          onClick={() => {
            setFormError(null);
            setFormFieldErrors({});
            setIsInvitationInfoOpen(false);
            setIsMemberFormOpen(true);
          }}
        >
          <PolypbaseIcon name="plus" size={18} />
          {t('manageAddTitle')}
        </button>
      </div>

      {isMemberFormOpen ? (
        <AdminActionPanel title={t('manageAddTitle')} closeLabel={t('close')} onClose={() => setIsMemberFormOpen(false)}>
          <form className="member-add-form" onSubmit={handleAddMember}>
            <div className="member-password-flow">
              <div className="member-password-flow-heading">
                <strong>{t('manageTemporaryPasswordTitle')}</strong>
                <button
                  type="button"
                  className="member-info-button"
                  aria-label={t('manageInvitationInfoLabel')}
                  aria-expanded={isInvitationInfoOpen}
                  onClick={() => setIsInvitationInfoOpen((open) => !open)}
                >
                  <PolypbaseIcon name="info" size={16} />
                </button>
              </div>
              {isInvitationInfoOpen ? (
                <span className="member-password-flow-help">{t('manageTemporaryPasswordText')}</span>
              ) : null}
            </div>
            <div className="member-add-grid">
              <label>
                {t('manageFieldFirstName')}
                <input
                  value={form.first_name}
                  onChange={(event) => setForm((current) => ({ ...current, first_name: event.target.value }))}
                  onBlur={() => setForm((current) => ({ ...current, first_name: formatFirstName(current.first_name) }))}
                />
              </label>
              <label>
                {t('manageFieldLastName')}
                <input
                  value={form.last_name}
                  onChange={(event) =>
                    setForm((current) => ({ ...current, last_name: event.target.value.toLocaleUpperCase('fr-FR') }))
                  }
                  onBlur={() => setForm((current) => ({ ...current, last_name: formatLastName(current.last_name) }))}
                />
              </label>
              <label>
                {t('manageFieldEmail')}
                <input
                  required
                  type="email"
                  autoComplete="email"
                  aria-invalid={Boolean(formFieldErrors.email)}
                  value={form.email}
                  onChange={(event) => {
                    setForm((current) => ({ ...current, email: event.target.value }));
                    setFormFieldErrors((current) => ({ ...current, email: undefined }));
                  }}
                />
                {formFieldErrors.email ? <small className="member-field-error">{formFieldErrors.email}</small> : null}
              </label>
              <label className="member-role-choice">
                {t('manageFieldRole')}
                <select value={role} onChange={(event) => setRole(event.target.value as MembershipRole)}>
                  {roles.map((roleOption) => (
                    <option key={roleOption.value} value={roleOption.value}>
                      {roleOption.label}
                    </option>
                  ))}
                </select>
                <small>{selectedRoleDescription}</small>
              </label>
            </div>

            {formError ? <p className="inline-error">{formError}</p> : null}

            <button type="submit" disabled={isAdding || organizationId == null}>
              {isAdding ? t('manageAdding') : t('manageAddAction')}
            </button>
          </form>
        </AdminActionPanel>
      ) : null}


      <p className="sr-only" role="status">{feedback?.message ?? ''}</p>
      {loadError && data ? <p className="inline-error">{loadError}</p> : null}
      <div className="account-overview">
        <button
          type="button"
          className={roleFilter === 'all' ? 'account-overview-card is-active' : 'account-overview-card'}
          onClick={() => setRoleFilter('all')}
        >
          <strong>{memberCounts.all}</strong>
          <span>{t('manageAllAccounts')}</span>
        </button>
        <button
          type="button"
          className={roleFilter === 'admin' ? 'account-overview-card is-active' : 'account-overview-card'}
          onClick={() => toggleRoleFilter('admin')}
        >
          <strong>{memberCounts.admin}</strong>
          <span>{t('manageAdminAccounts')}</span>
        </button>
        <button
          type="button"
          className={
            roleFilter === 'lab_technician' ? 'account-overview-card is-active' : 'account-overview-card'
          }
          onClick={() => toggleRoleFilter('lab_technician')}
        >
          <strong>{memberCounts.lab_technician}</strong>
          <span>{t('manageTechnicianAccounts')}</span>
        </button>
        <button
          type="button"
          className={roleFilter === 'viewer' ? 'account-overview-card is-active' : 'account-overview-card'}
          onClick={() => toggleRoleFilter('viewer')}
        >
          <strong>{memberCounts.viewer}</strong>
          <span>{t('manageViewerAccounts')}</span>
        </button>
      </div>

      {filteredMembers.length ? (
        <div className="member-table-shell">
          <table className="member-table">
            <thead>
              <tr>
                <th scope="col">{t('manageColUser')}</th>
                <th scope="col">{t('manageColRole')}</th>
                <th scope="col">{t('manageColLastLogin')}</th>
                <th scope="col">{t('manageColStatus')}</th>
                <th scope="col" className="member-action-heading" />
              </tr>
            </thead>
            <tbody>
              {filteredMembers.map((member) => {
                const memberName = getMemberDisplayName(member);
                const lastLogin = member.last_login
                  ? formatRelativeDateTime(member.last_login, {
                      todayAt: t('manageLastLoginTodayAt'),
                      yesterdayAt: t('manageLastLoginYesterdayAt'),
                    })
                  : null;
                const memberActions = getMemberRowActions(member, {
                  canManageAdminMemberships: data.can_manage_admin_memberships,
                  canRelinquishResponsable: data.can_relinquish_responsable,
                }).map((item) => ({
                  ...item,
                  label: memberActionLabel(item.action),
                }));
                return (
                  <tr
                    key={member.membership_id}
                    className={getMemberRowClassName(
                      member.is_active,
                      feedback?.membershipId === member.membership_id ? feedback.tone : null,
                    )}
                  >
                    <td>
                      <span className="member-identity">
                        <strong>{memberName}</strong>
                        {memberName !== member.email && member.email ? <small>{member.email}</small> : null}
                      </span>
                    </td>
                    <td>
                      <span className="member-role-value">
                        {getAccountMemberRoleLabel(member, t('roleResponsable'))}
                      </span>
                    </td>
                    <td className="member-last-login">
                      {member.last_login && lastLogin ? (
                        <time
                          dateTime={member.last_login}
                          title={lastLogin.exact}
                          aria-label={`${lastLogin.relative}. ${lastLogin.exact}`}
                        >
                          {lastLogin.relative}
                        </time>
                      ) : t('manageNeverConnected')}
                    </td>
                    <td>
                      <span className={member.is_active ? 'member-state is-on' : 'member-state is-off'}>
                        {member.is_active ? t('manageStatusActive') : t('manageStatusInactive')}
                      </span>
                    </td>
                    <td className="member-action-cell">
                      <RowActionMenu<MemberRowAction>
                        disabled={busyMemberIds.has(member.membership_id) || memberActions.length === 0}
                        actions={memberActions}
                        ariaLabel={`${t('manageMemberActions')} ${memberName}`}
                        onAction={(action) => handleMemberAction(member, action)}
                      />
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="muted compact-text">
          {data.members.length ? t('manageNoFilteredMembers') : t('manageNoMembers')}
        </p>
      )}
      <section className="account-invitations" aria-labelledby="account-invitations-title">
        <h3 id="account-invitations-title">{t('manageInvitationsTitle')}</h3>
        {invitationsError ? (
          <div className="inline-error invitation-error" role="alert">
            <span>{invitationsError}</span>
            <button className="secondary-button" type="button" onClick={() => loadInvitations()}>
              {t('manageInvitationsRetry')}
            </button>
          </div>
        ) : null}
        {!invitationSnapshot ? (
          invitationsError ? null : <SkeletonRows count={3} />
        ) : invitationSnapshot.invitations.length ? (
          <AccountInvitationsPanel
            snapshot={invitationSnapshot}
            busyIds={busyMemberIds}
            feedback={feedback ? { id: feedback.membershipId, tone: feedback.tone } : null}
            getDisplayName={getMemberDisplayName}
            onResend={handleInvitationResend}
            onRevalidate={loadInvitations}
            t={t}
          />
        ) : (
          <p className="muted compact-text">{t('manageNoInvitations')}</p>
        )}
      </section>
      {confirmActionModal}
    </section>
  );
}

function AdminFlowNav({
  activeSection,
  aquariumName,
  onSelect,
  t,
}: {
  activeSection: AdminSectionKey;
  aquariumName: string;
  onSelect: (section: AdminSectionKey) => void;
  t: Translator;
}) {
  return (
    <aside className="admin-sidebar">
      <nav className="admin-flow-nav" aria-label={t('adminFlowLabel')}>
        <div className="admin-nav-group">
          <span className="admin-nav-group-label">{aquariumName}</span>
          {ADMIN_FLOW_ITEMS.map((item) => (
            <button
              key={item.key}
              type="button"
              className={activeSection === item.key ? 'admin-flow-step is-active' : 'admin-flow-step'}
              aria-controls={item.panelId}
              aria-current={activeSection === item.key ? 'page' : undefined}
              onClick={() => onSelect(item.key)}
            >
              {t(item.label)}
            </button>
          ))}
        </div>
      </nav>
    </aside>
  );
}

export default function AdminView({
  activeOrganizationId,
  activeSection,
  boxes,
  boxCollectionStatus,
  onRetryBoxCollection,
  isLoading,
  language,
  profile,
  onResponsableChange,
  onSelectSection,
  onCreateZone,
  onUpdateZone,
  onLoadZoneDeletion,
  onDeleteZone,
  onAssignBoxLocation,
  onOpenBox,
  onOpenZone,
  onBatchQualifyBoxes,
  onDeactivateBox,
  onQualifyBox,
  onReactivateBox,
  onEditMeasurement,
  speciesTarget,
  onOpenSpecies,
  onCloseSpecies,
  onCreateBoxForStrain,
  t,
  zones,
}: {
  activeOrganizationId: number | null;
  activeSection: AdminSectionKey;
  // Species page opened from the Administration > Species catalog.
  speciesTarget: AdminSpeciesTarget | null;
  onOpenSpecies: (target: AdminSpeciesTarget) => void;
  onCloseSpecies: () => void;
  // Continues a Strain creation with the existing New Box form (absent: no offer).
  onCreateBoxForStrain?: (strainId: number) => void;
  onEditMeasurement: (measurement: EditableMeasurement) => void;
  // Species pages need every box; sharing uses bounded server-side lookup.
  boxes: BoxItem[];
  boxCollectionStatus: BoxCollectionStatus;
  onRetryBoxCollection: () => void;
  isLoading: boolean;
  language: Language;
  profile: UserProfile | null;
  onResponsableChange: (organizationId: number, isResponsable: boolean) => void;
  onSelectSection: (section: AdminSectionKey) => void;
  onCreateZone: (payload: ThermalZonePayload) => Promise<{ id: number }>;
  onUpdateZone: (zoneId: number, payload: ThermalZoneUpdatePayload) => Promise<void>;
  onLoadZoneDeletion: (zoneId: number) => Promise<ThermalZoneDeletionEligibility>;
  onDeleteZone: (zoneId: number) => Promise<void>;
  onAssignBoxLocation: (boxId: number, payload: BoxInitialLocationPayload) => Promise<void>;
  onOpenBox: (boxId: number, code: string) => void;
  onOpenZone: (zoneId: number) => void;
  onBatchQualifyBoxes: (payload: BoxInventoryBatchQualifyPayload) => Promise<BoxInventoryBatchResult>;
  onDeactivateBox: (boxId: number, payload: BoxDeactivatePayload) => Promise<void>;
  onQualifyBox: (boxId: number, payload: BoxQualifyPayload) => Promise<void>;
  onReactivateBox: (boxId: number, payload: BoxActivatePayload) => Promise<void>;
  t: Translator;
  zones: ThermalZone[];
}) {
  if (isLoading) {
    return <PageLoader variant="admin" label={t('adminTitle')} />;
  }

  if (!profile || !userHasAdminRole(profile)) return null;

  const activeOrganizationName = profile.active_organization?.name ?? '-';

  // A Species page is a full entity page, like a Box or a Location: the
  // Administration section list returns with the catalog.
  const isSpeciesPage = activeSection === 'species' && speciesTarget !== null;

  return (
    <section className="admin-panel">
      <div className={isSpeciesPage ? 'admin-workspace is-entity-page' : 'admin-workspace'}>
        {isSpeciesPage ? null : (
          <AdminFlowNav
            activeSection={activeSection}
            aquariumName={activeOrganizationName}
            onSelect={onSelectSection}
            t={t}
          />
        )}

        <div className="admin-flow-panel">
          {activeSection === 'accounts' ? (
            <AccountManagementSection
              key={profile.active_organization?.id ?? 'no-organization'}
              organizationName={activeOrganizationName}
              onResponsableChange={onResponsableChange}
              t={t}
            />
          ) : null}

          {activeSection === 'inventory' ? (
            <BoxInventoryAdminSection
              key={profile.active_organization?.id ?? 'no-organization'}
              language={language}
              onAssignLocation={onAssignBoxLocation}
              onOpenBox={onOpenBox}
              onOpenZone={onOpenZone}
              onBatchQualify={onBatchQualifyBoxes}
              onDeactivate={onDeactivateBox}
              onQualify={onQualifyBox}
              onReactivate={onReactivateBox}
              t={t}
              zones={zones}
            />
          ) : null}

          {activeSection === 'species' && activeOrganizationId != null ? (
            <SpeciesStrainsAdminSection
              key={activeOrganizationId}
              target={speciesTarget}
              organizationId={activeOrganizationId}
              organizationName={profile?.active_organization?.name ?? ''}
              boxes={boxes}
              boxCollectionStatus={boxCollectionStatus}
              onRetryBoxCollection={onRetryBoxCollection}
              language={language}
              onOpenSpecies={onOpenSpecies}
              onOpenBox={onOpenBox}
              onOpenZone={onOpenZone}
              onBack={onCloseSpecies}
              onCreateBox={onCreateBoxForStrain}
              t={t}
            />
          ) : null}

          {activeSection === 'environment' && activeOrganizationId != null ? (
            <LabConfigurationAdminSection
              key={activeOrganizationId}
              organizationId={activeOrganizationId}
              zones={zones}
              onCreateZone={onCreateZone}
              onUpdateZone={onUpdateZone}
              onLoadZoneDeletion={onLoadZoneDeletion}
              onDeleteZone={onDeleteZone}
              onOpenZone={onOpenZone}
              t={t}
            />
          ) : null}

          {activeSection === 'transfers' && activeOrganizationId != null ? (
            <InstitutionalExchangesPage
              key={`${profile.id}:${activeOrganizationId}`}
              organizationId={activeOrganizationId}
              language={language}
              onOpenBox={onOpenBox}
              t={t}
            />
          ) : null}

          {activeSection === 'history' && activeOrganizationId != null ? (
            <AdminAuditSection
              activeOrganizationId={activeOrganizationId}
              key={activeOrganizationId}
              language={language}
              onEditMeasurement={onEditMeasurement}
              onOpenBox={onOpenBox}
              t={t}
            />
          ) : null}
        </div>
      </div>
    </section>
  );
}
