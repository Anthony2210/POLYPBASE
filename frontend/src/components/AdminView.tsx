import { type FormEvent, useEffect, useMemo, useRef, useState } from 'react';

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
  ExportOptions,
  MembershipRole,
  NewMemberPayload,
  Organization,
  ResponsableRelinquishResponse,
  ThermalZone,
  UserProfile,
} from '../types';
import type { Language, Translator } from '../i18n';
import type {
  OrganizationPayload,
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

import { getAccountErrorMessage, getErrorMessage } from '../utils/errors';
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
import TaxonomyAdminSection from './TaxonomyAdminSection';

export type { EditableMeasurement } from '../types';

const ADMIN_FLOW_ITEMS = [
  { key: 'accounts', panelId: 'admin-accounts', label: 'adminTabUsers', scope: 'aquarium' },
  { key: 'inventory', panelId: 'admin-inventory', label: 'adminTabInventory', scope: 'aquarium' },
  { key: 'species', panelId: 'admin-species', label: 'adminTabSpecies', scope: 'aquarium' },
  { key: 'references', panelId: 'admin-taxonomy', label: 'adminTabReferences', scope: 'shared' },
  { key: 'environment', panelId: 'admin-environment', label: 'adminTabLocations', scope: 'aquarium' },
  { key: 'transfers', panelId: 'admin-transfers', label: 'adminTabTransfers', scope: 'aquarium' },
  { key: 'history', panelId: 'admin-history', label: 'adminTabHistory', scope: 'aquarium' },
  { key: 'organizations', panelId: 'admin-organizations', label: 'adminTabOrganizations', scope: 'superuser' },
] as const;

export type AdminSectionKey = (typeof ADMIN_FLOW_ITEMS)[number]['key'];

const DISABLED_ADMIN_SECTIONS: ReadonlySet<AdminSectionKey> = new Set([
  'references',
  'organizations',
]);

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

function getDigitsOnly(value: string) {
  return value.replace(/\D/g, '');
}

const FALLBACK_REGION_CODES = [
  'AD', 'AE', 'AF', 'AG', 'AI', 'AL', 'AM', 'AO', 'AQ', 'AR', 'AS', 'AT',
  'AU', 'AW', 'AX', 'AZ', 'BA', 'BB', 'BD', 'BE', 'BF', 'BG', 'BH', 'BI',
  'BJ', 'BL', 'BM', 'BN', 'BO', 'BQ', 'BR', 'BS', 'BT', 'BV', 'BW', 'BY',
  'BZ', 'CA', 'CC', 'CD', 'CF', 'CG', 'CH', 'CI', 'CK', 'CL', 'CM', 'CN',
  'CO', 'CR', 'CU', 'CV', 'CW', 'CX', 'CY', 'CZ', 'DE', 'DJ', 'DK', 'DM',
  'DO', 'DZ', 'EC', 'EE', 'EG', 'EH', 'ER', 'ES', 'ET', 'FI', 'FJ', 'FK',
  'FM', 'FO', 'FR', 'GA', 'GB', 'GD', 'GE', 'GF', 'GG', 'GH', 'GI', 'GL',
  'GM', 'GN', 'GP', 'GQ', 'GR', 'GS', 'GT', 'GU', 'GW', 'GY', 'HK', 'HM',
  'HN', 'HR', 'HT', 'HU', 'ID', 'IE', 'IL', 'IM', 'IN', 'IO', 'IQ', 'IR',
  'IS', 'IT', 'JE', 'JM', 'JO', 'JP', 'KE', 'KG', 'KH', 'KI', 'KM', 'KN',
  'KP', 'KR', 'KW', 'KY', 'KZ', 'LA', 'LB', 'LC', 'LI', 'LK', 'LR', 'LS',
  'LT', 'LU', 'LV', 'LY', 'MA', 'MC', 'MD', 'ME', 'MF', 'MG', 'MH', 'MK',
  'ML', 'MM', 'MN', 'MO', 'MP', 'MQ', 'MR', 'MS', 'MT', 'MU', 'MV', 'MW',
  'MX', 'MY', 'MZ', 'NA', 'NC', 'NE', 'NF', 'NG', 'NI', 'NL', 'NO', 'NP',
  'NR', 'NU', 'NZ', 'OM', 'PA', 'PE', 'PF', 'PG', 'PH', 'PK', 'PL', 'PM',
  'PN', 'PR', 'PS', 'PT', 'PW', 'PY', 'QA', 'RE', 'RO', 'RS', 'RU', 'RW',
  'SA', 'SB', 'SC', 'SD', 'SE', 'SG', 'SH', 'SI', 'SJ', 'SK', 'SL', 'SM',
  'SN', 'SO', 'SR', 'SS', 'ST', 'SV', 'SX', 'SY', 'SZ', 'TC', 'TD', 'TF',
  'TG', 'TH', 'TJ', 'TK', 'TL', 'TM', 'TN', 'TO', 'TR', 'TT', 'TV', 'TW',
  'TZ', 'UA', 'UG', 'UM', 'US', 'UY', 'UZ', 'VA', 'VC', 'VE', 'VG', 'VI',
  'VN', 'VU', 'WF', 'WS', 'YE', 'YT', 'ZA', 'ZM', 'ZW',
];

const COUNTRY_OPTIONS = buildCountryOptions();

const CITY_OPTIONS_BY_REGION: Record<string, string[]> = {
  BE: ['Anvers', 'Bruxelles', 'Gand', 'Liège', 'Mons', 'Namur'],
  CA: ['Montréal', 'Québec', 'Toronto', 'Vancouver'],
  CH: ['Bâle', 'Genève', 'Lausanne', 'Zurich'],
  DE: ['Berlin', 'Bonn', 'Hambourg', 'Munich', 'Stuttgart'],
  ES: ['Barcelone', 'Madrid', 'Séville', 'Valence'],
  FR: [
    'Amnéville', 'Biarritz', 'Boulogne-sur-Mer', 'Brest', 'La Rochelle',
    'Lyon', 'Marseille', 'Montpellier', 'Nancy', 'Nantes', 'Paris',
    'Saint-Malo', 'Strasbourg', 'Toulouse',
  ],
  GB: ['Birmingham', 'Bristol', 'Édimbourg', 'Londres', 'Manchester'],
  IT: ['Gênes', 'Milan', 'Naples', 'Rome', 'Venise'],
  JP: ['Fukuoka', 'Hiroshima', 'Kyoto', 'Nagoya', 'Osaka', 'Tokyo', 'Yokohama'],
  MC: ['Monaco'],
  NL: ['Amsterdam', 'La Haye', 'Rotterdam', 'Utrecht'],
  PT: ['Lisbonne', 'Porto'],
  US: ['Atlanta', 'Boston', 'Chicago', 'Monterey', 'New York', 'San Diego', 'Seattle', 'Washington'],
};

function buildCountryOptions() {
  const regionCodes = getRegionCodes();
  const locale = typeof navigator === 'undefined' ? 'fr' : navigator.language;
  const displayNames = typeof Intl.DisplayNames === 'function'
    ? new Intl.DisplayNames([locale], { type: 'region' })
    : null;

  return regionCodes
    .map((code) => ({
      code,
      name: displayNames?.of(code) ?? code,
    }))
    .filter((option) => option.name && option.name !== option.code)
    .sort((first, second) => first.name.localeCompare(second.name));
}

function getRegionCodes() {
  return FALLBACK_REGION_CODES;
}

function getCountryOption(countryName: string) {
  const normalizedCountry = countryName.trim().toLocaleLowerCase();
  if (!normalizedCountry) return null;
  return COUNTRY_OPTIONS.find((option) => option.name.toLocaleLowerCase() === normalizedCountry) ?? null;
}

function getCityOptions(countryName: string, extraCities: string[] = []) {
  const countryOption = getCountryOption(countryName);
  const predefinedCities = countryOption ? CITY_OPTIONS_BY_REGION[countryOption.code] ?? [] : [];
  return Array.from(new Set([...predefinedCities, ...extraCities].filter(Boolean))).sort((first, second) =>
    first.localeCompare(second),
  );
}

function cityMatchesCountry(cityName: string, countryName: string) {
  const normalizedCity = cityName.trim().toLocaleLowerCase();
  if (!normalizedCity) return true;

  const countryOption = getCountryOption(countryName);
  if (!countryOption) return true;

  const knownCountryCodesForCity = Object.entries(CITY_OPTIONS_BY_REGION)
    .filter(([, cities]) => cities.some((city) => city.toLocaleLowerCase() === normalizedCity))
    .map(([countryCode]) => countryCode);

  return knownCountryCodesForCity.length === 0 || knownCountryCodesForCity.includes(countryOption.code);
}

function filterSuggestions(value: string, options: string[], limit = 8) {
  const normalizedValue = value.trim().toLocaleLowerCase();
  const filteredOptions = normalizedValue
    ? options.filter((option) => option.toLocaleLowerCase().includes(normalizedValue))
    : options;
  return filteredOptions.slice(0, limit);
}

function SuggestionInput({
  id,
  value,
  options,
  onChange,
}: {
  id: string;
  value: string;
  options: string[];
  onChange: (value: string) => void;
}) {
  const [isFocused, setIsFocused] = useState(false);
  const suggestions = useMemo(() => filterSuggestions(value, options), [options, value]);
  const exactMatch = suggestions.some((option) => option.toLocaleLowerCase() === value.trim().toLocaleLowerCase());
  const shouldShowSuggestions = isFocused && suggestions.length > 0 && (!exactMatch || suggestions.length > 1);

  return (
    <div
      className="admin-suggest-field"
      onBlurCapture={(event) => {
        const next = event.relatedTarget;
        // Keep the menu open while focus stays inside the field (input or a
        // suggestion button); otherwise the menu disappears under the focused
        // button and keyboard focus is lost.
        if (next instanceof Node && event.currentTarget.contains(next)) return;
        window.setTimeout(() => setIsFocused(false), 120);
      }}
    >
      <input
        id={id}
        type="text"
        value={value}
        autoComplete="off"
        onChange={(event) => onChange(event.target.value)}
        onFocus={() => setIsFocused(true)}
      />
      {shouldShowSuggestions ? (
        <div className="admin-suggest-menu" role="listbox">
          {suggestions.map((option) => (
            <button
              key={option}
              type="button"
              role="option"
              onMouseDown={(event) => event.preventDefault()}
              onClick={() => {
                onChange(option);
                setIsFocused(false);
              }}
            >
              {option}
            </button>
          ))}
        </div>
      ) : null}
    </div>
  );
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

function OrganizationCreateForm({
  organizations,
  profile: _profile,
  onCreateOrganization,
  t,
}: {
  organizations: Organization[];
  profile: UserProfile;
  onCreateOrganization: (payload: OrganizationPayload) => Promise<void>;
  t: Translator;
}) {
  const [name, setName] = useState('');
  const [country, setCountry] = useState('');
  const [city, setCity] = useState('');
  const [contactName, setContactName] = useState('');
  const [contactEmail, setContactEmail] = useState('');
  const [contactPhone, setContactPhone] = useState('');
  const [postalAddress, setPostalAddress] = useState('');
  const [isSaving, setIsSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const existingCities = useMemo(() => organizations.map((organization) => organization.city).filter((city): city is string => city != null && city !== ''), [organizations]);
  const cityOptions = useMemo(() => getCityOptions(country, existingCities), [country, existingCities]);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (isSaving) return;

    setIsSaving(true);
    setError(null);

    const selectedCountry = getCountryOption(country);
    if (country.trim() && !selectedCountry) {
      setError(t('adminInvalidCountry'));
      setIsSaving(false);
      return;
    }
    if (!cityMatchesCountry(city, selectedCountry?.name ?? '')) {
      setError(t('adminInvalidCityCountry'));
      setIsSaving(false);
      return;
    }

    // The model has no phone/address/contact-name fields: fold them into notes.
    const notes = [
      contactName.trim() && `Contact : ${contactName.trim()}`,
      contactPhone.trim() && `Tél : ${contactPhone.trim()}`,
      postalAddress.trim() && `Adresse : ${postalAddress.trim()}`,
    ]
      .filter(Boolean)
      .join('\n');

    try {
      await onCreateOrganization({
        name: name.trim(),
        city: city.trim(),
        country: selectedCountry?.name ?? '',
        contact_email: contactEmail.trim(),
        notes,
      });
      setName('');
      setCountry('');
      setCity('');
      setContactName('');
      setContactEmail('');
      setContactPhone('');
      setPostalAddress('');
    } catch (requestError) {
      setError(getErrorMessage(requestError));
    } finally {
      setIsSaving(false);
    }
  }

  return (
    <form className="admin-form admin-organization-form" onSubmit={handleSubmit}>
      <label>
        <span>{t('adminOrganizationName')}</span>
        <input required type="text" value={name} onChange={(event) => setName(event.target.value)} />
      </label>
      <label>
        <span>{t('adminCountry')}</span>
        <SuggestionInput
          id="admin-country"
          value={country}
          options={COUNTRY_OPTIONS.map((option) => option.name)}
          onChange={setCountry}
        />
      </label>
      <label>
        <span>{t('adminCity')}</span>
        <SuggestionInput
          id="admin-city"
          value={city}
          options={cityOptions}
          onChange={setCity}
        />
      </label>
      <label>
        <span>{t('adminContactName')}</span>
        <input type="text" value={contactName} onChange={(event) => setContactName(event.target.value)} />
      </label>
      <label>
        <span>{t('adminContactEmail')}</span>
        <input type="email" value={contactEmail} onChange={(event) => setContactEmail(event.target.value)} />
      </label>
      <label>
        <span>{t('adminContactPhone')}</span>
        <input
          type="tel"
          inputMode="numeric"
          pattern="[0-9]*"
          value={contactPhone}
          onChange={(event) => setContactPhone(getDigitsOnly(event.target.value))}
        />
      </label>
      <label className="admin-wide-field">
        <span>{t('adminPostalAddress')}</span>
        <textarea rows={3} value={postalAddress} onChange={(event) => setPostalAddress(event.target.value)} />
      </label>
      <button type="submit" disabled={isSaving || !name.trim()}>
        {isSaving ? t('saving') : t('adminAddOrganization')}
      </button>
      {error ? <p className="inline-error">{error}</p> : null}
    </form>
  );
}

type OrganizationNotes = {
  contactName: string;
  phone: string;
  address: string;
  extraNotes: string;
};

function parseOrganizationNotes(notes: string): OrganizationNotes {
  const parsed: OrganizationNotes = {
    contactName: '',
    phone: '',
    address: '',
    extraNotes: '',
  };
  const extraLines: string[] = [];

  notes.split(/\r?\n/).forEach((rawLine) => {
    const line = rawLine.trim();
    if (!line) return;
    const [rawKey, ...rest] = line.split(':');
    const value = rest.join(':').trim();
    const key = rawKey.trim().toLocaleLowerCase('fr-FR');
    if (value && key === 'contact') {
      parsed.contactName = value;
      return;
    }
    if (value && (key === 'tél' || key === 'tel' || key === 'telephone' || key === 'téléphone')) {
      parsed.phone = value;
      return;
    }
    if (value && key === 'adresse') {
      parsed.address = value;
      return;
    }
    extraLines.push(line);
  });

  parsed.extraNotes = extraLines.join('\n');
  return parsed;
}

function getOrganizationLocation(organization: Organization) {
  return [organization.city, organization.country].filter(Boolean).join(' - ') || '-';
}

function getOrganizationSearchText(organization: Organization) {
  const notes = parseOrganizationNotes(organization.notes ?? '');
  return [
    organization.name,
    organization.city,
    organization.country,
    organization.contact_email,
    notes.contactName,
    notes.phone,
    notes.address,
    notes.extraNotes,
  ]
    .join(' ')
    .toLocaleLowerCase('fr-FR');
}

function OrganizationManagementList({
  organizations,
  profile,
  onDeleteOrganization,
  onUpdateOrganization,
  t,
}: {
  organizations: Organization[];
  profile: UserProfile;
  onDeleteOrganization: (organizationId: number) => Promise<void>;
  onUpdateOrganization: (organizationId: number, payload: OrganizationPayload) => Promise<void>;
  t: Translator;
}) {
  const [editingId, setEditingId] = useState<number | null>(null);
  const [name, setName] = useState('');
  const [city, setCity] = useState('');
  const [country, setCountry] = useState('');
  const [contactEmail, setContactEmail] = useState('');
  const [notes, setNotes] = useState('');
  const [isSaving, setIsSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const { confirmAction, confirmActionModal } = useConfirmAction();
  const existingCities = useMemo(() => organizations.map((organization) => organization.city).filter((city): city is string => city != null && city !== ''), [organizations]);
  const cityOptions = useMemo(() => getCityOptions(country, existingCities), [country, existingCities]);
  const [organizationSearch, setOrganizationSearch] = useState('');
  const [countryFilter, setCountryFilter] = useState('all');
  const canManageOrganizations = userHasAdminRole(profile);
  const countryOptions = useMemo(
    () =>
      Array.from(new Set(organizations.map((organization) => organization.country).filter((country): country is string => country != null && country !== '')))
        .sort((a, b) => a.localeCompare(b, 'fr-FR')),
    [organizations],
  );
  const visibleOrganizations = useMemo(() => {
    const normalizedSearch = organizationSearch.trim().toLocaleLowerCase('fr-FR');
    return organizations
      .filter((organization) => {
        const matchesSearch = normalizedSearch
          ? getOrganizationSearchText(organization).includes(normalizedSearch)
          : true;
        const matchesCountry = countryFilter === 'all' || organization.country === countryFilter;
        return matchesSearch && matchesCountry;
      })
      .sort((a, b) => a.name.localeCompare(b.name, 'fr-FR'));
  }, [countryFilter, organizationSearch, organizations]);

  function startEdit(organization: Organization) {
    if (!canManageOrganizations) return;
    setEditingId(organization.id);
    setName(organization.name);
    setCity(organization.city ?? '');
    setCountry(organization.country ?? '');
    setContactEmail(organization.contact_email ?? '');
    setNotes(organization.notes ?? '');
    setError(null);
  }

  function cancelEdit() {
    setEditingId(null);
    setName('');
    setCity('');
    setCountry('');
    setContactEmail('');
    setNotes('');
    setError(null);
  }

  async function handleUpdate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (editingId == null || isSaving || !name.trim()) return;

    setIsSaving(true);
    setError(null);

    const selectedCountry = getCountryOption(country);
    if (country.trim() && !selectedCountry) {
      setError(t('adminInvalidCountry'));
      setIsSaving(false);
      return;
    }
    if (!cityMatchesCountry(city, selectedCountry?.name ?? '')) {
      setError(t('adminInvalidCityCountry'));
      setIsSaving(false);
      return;
    }

    try {
      await onUpdateOrganization(editingId, {
        name: name.trim(),
        city: city.trim(),
        country: selectedCountry?.name ?? '',
        contact_email: contactEmail.trim(),
        notes: notes.trim(),
      });
      cancelEdit();
    } catch (requestError) {
      setError(getErrorMessage(requestError));
    } finally {
      setIsSaving(false);
    }
  }

  async function handleDelete(organization: Organization) {
    if (isSaving) return;

    const confirmed = await confirmAction({
      title: t('confirmDeleteOrganizationTitle'),
      message: t('confirmDeleteOrganizationMessage'),
      confirmLabel: t('confirmDeleteOrganizationAction'),
      cancelLabel: t('confirmCancel'),
      variant: 'danger',
      details: [
        { label: t('confirmDetailOrganization'), value: organization.name },
      ],
    });
    if (!confirmed) return;

    setIsSaving(true);
    setError(null);

    try {
      await onDeleteOrganization(organization.id);
      if (editingId === organization.id) cancelEdit();
    } catch (requestError) {
      setError(getErrorMessage(requestError));
    } finally {
      setIsSaving(false);
    }
  }

  return (
    <div className="admin-organization-manager">
      <div className="organization-directory-toolbar">
        <label className="organization-search-field">
          <span>{t('manageSearchLabel')}</span>
          <input
            type="search"
            value={organizationSearch}
            placeholder={t('adminOrganizationSearchPlaceholder')}
            onChange={(event) => setOrganizationSearch(event.target.value)}
          />
        </label>
        <label className="organization-country-filter">
          <span>{t('adminCountry')}</span>
          <select value={countryFilter} onChange={(event) => setCountryFilter(event.target.value)}>
            <option value="all">{t('adminOrganizationAllCountries')}</option>
            {countryOptions.map((countryName) => (
              <option key={countryName} value={countryName}>
                {countryName}
              </option>
            ))}
          </select>
        </label>
        <span className="organization-visible-count">
          {visibleOrganizations.length} / {organizations.length}
        </span>
      </div>

      {visibleOrganizations.length ? (
        <div className="admin-organization-list">
          {visibleOrganizations.map((organization) => {
            const parsedNotes = parseOrganizationNotes(organization.notes ?? '');
            const contactName = parsedNotes.contactName || t('adminOrganizationContact');
            const isEditing = editingId === organization.id;
            return (
          <article
            className={isEditing ? 'admin-organization-item organization-card is-editing' : 'admin-organization-item organization-card'}
            key={organization.id}
          >
            {isEditing ? (
              <form className="admin-form admin-organization-edit-form" onSubmit={handleUpdate}>
                <div className="organization-edit-heading admin-wide-field">
                  <strong>{t('adminEditOrganization')}</strong>
                  <span>{organization.name}</span>
                </div>
                <label>
                  <span>{t('adminOrganizationName')}</span>
                  <input required type="text" value={name} onChange={(event) => setName(event.target.value)} />
                </label>
                <label>
                  <span>{t('adminCountry')}</span>
                  <SuggestionInput
                    id="admin-edit-country"
                    value={country}
                    options={COUNTRY_OPTIONS.map((option) => option.name)}
                    onChange={setCountry}
                  />
                </label>
                <label>
                  <span>{t('adminCity')}</span>
                  <SuggestionInput
                    id="admin-edit-city"
                    value={city}
                    options={cityOptions}
                    onChange={setCity}
                  />
                </label>
                <label>
                  <span>{t('adminContactEmail')}</span>
                  <input type="email" value={contactEmail} onChange={(event) => setContactEmail(event.target.value)} />
                </label>
                <label className="admin-wide-field">
                  <span>{t('adminTransferNotes')}</span>
                  <textarea rows={3} value={notes} onChange={(event) => setNotes(event.target.value)} />
                </label>
                <div className="admin-organization-actions">
                  <button type="submit" disabled={isSaving || !name.trim()}>
                    {isSaving ? t('saving') : t('adminSaveOrganization')}
                  </button>
                  <button type="button" className="is-secondary" onClick={cancelEdit}>
                    {t('adminCancelEdit')}
                  </button>
                </div>
              </form>
            ) : (
              <>
                <header className="organization-card-head">
                  <div>
                    <strong>{organization.name}</strong>
                    <span>{getOrganizationLocation(organization)}</span>
                  </div>
                  <div className="organization-card-head-actions">
                    {organization.country ? <em>{organization.country}</em> : null}
                    {canManageOrganizations ? (
                      <button
                        className="organization-edit-button"
                        type="button"
                        aria-label={`${t('adminEditOrganization')} ${organization.name}`}
                        onClick={() => startEdit(organization)}
                      >
                        <PolypbaseIcon name="edit" size={17} />
                      </button>
                    ) : null}
                  </div>
                </header>
                <dl className="organization-contact-grid">
                  <div>
                    <dt>{t('adminOrganizationContact')}</dt>
                    <dd>{contactName}</dd>
                  </div>
                  <div>
                    <dt>{t('adminContactEmail')}</dt>
                    <dd>
                      {organization.contact_email ? (
                        <a href={`mailto:${organization.contact_email}`}>{organization.contact_email}</a>
                      ) : (
                        t('adminOrganizationNoEmail')
                      )}
                    </dd>
                  </div>
                  <div>
                    <dt>{t('adminContactPhone')}</dt>
                    <dd>{parsedNotes.phone || t('adminOrganizationNoPhone')}</dd>
                  </div>
                  <div>
                    <dt>{t('adminPostalAddress')}</dt>
                    <dd>{parsedNotes.address || t('adminOrganizationNoAddress')}</dd>
                  </div>
                </dl>
                {parsedNotes.extraNotes ? (
                  <p className="organization-note">{parsedNotes.extraNotes}</p>
                ) : null}
                {profile.is_superuser ? (
                  <div className="admin-organization-actions">
                    <button
                      type="button"
                      className="is-danger"
                      disabled={isSaving}
                      onClick={() => void handleDelete(organization)}
                    >
                      {t('adminDeleteOrganization')}
                    </button>
                  </div>
                ) : null}
              </>
            )}
          </article>
            );
          })}
        </div>
      ) : (
        <p className="empty-state organization-empty-state">{t('adminOrganizationNoResults')}</p>
      )}

      {error ? <p className="inline-error">{error}</p> : null}
      {confirmActionModal}
    </div>
  );
}

function AdminFlowNav({
  activeSection,
  aquariumName,
  isSuperuser,
  onSelect,
  t,
}: {
  activeSection: AdminSectionKey;
  aquariumName: string;
  isSuperuser: boolean;
  onSelect: (section: AdminSectionKey) => void;
  t: Translator;
}) {
  const aquariumItems = ADMIN_FLOW_ITEMS.filter((item) => item.scope === 'aquarium');
  const sharedItems = ADMIN_FLOW_ITEMS.filter((item) => item.scope === 'shared');
  const superuserItems = ADMIN_FLOW_ITEMS.filter((item) => item.scope === 'superuser');

  function renderItems(items: typeof ADMIN_FLOW_ITEMS[number][]) {
    return items.map((item) => {
      const isDisabled = DISABLED_ADMIN_SECTIONS.has(item.key);
      return (
        <button
          key={item.key}
          type="button"
          className={activeSection === item.key ? 'admin-flow-step is-active' : 'admin-flow-step'}
          aria-controls={isDisabled ? undefined : item.panelId}
          aria-current={activeSection === item.key ? 'page' : undefined}
          disabled={isDisabled}
          onClick={() => onSelect(item.key)}
        >
          {t(item.label)}
        </button>
      );
    });
  }

  return (
    <aside className="admin-sidebar">
      <nav className="admin-flow-nav" aria-label={t('adminFlowLabel')}>
        <div className="admin-nav-group">
          <span className="admin-nav-group-label">{aquariumName}</span>
          {renderItems(aquariumItems)}
        </div>
        <div className="admin-nav-group">
          <span className="admin-nav-group-label">{t('adminSharedNavigation')}</span>
          {renderItems(sharedItems)}
        </div>
        {isSuperuser ? (
          <div className="admin-nav-group">
            <span className="admin-nav-group-label">{t('adminSuperuserNavigation')}</span>
            {renderItems(superuserItems)}
          </div>
        ) : null}
      </nav>
    </aside>
  );
}

function OrganizationsAdminSection({
  organizations,
  profile,
  onCreateOrganization,
  onDeleteOrganization,
  onUpdateOrganization,
  t,
}: {
  organizations: Organization[];
  profile: UserProfile;
  onCreateOrganization: (payload: OrganizationPayload) => Promise<void>;
  onDeleteOrganization: (organizationId: number) => Promise<void>;
  onUpdateOrganization: (organizationId: number, payload: OrganizationPayload) => Promise<void>;
  t: Translator;
}) {
  const [isCreateOpen, setIsCreateOpen] = useState(false);
  const countryCount = useMemo(
    () => new Set(organizations.map((organization) => organization.country).filter(Boolean)).size,
    [organizations],
  );

  return (
    <section className="admin-section admin-organizations-section" id="admin-organizations">
      <div className="admin-organizations-topbar">
        <div>
          <h2>{t('adminOrganizationsTitle')}</h2>
        </div>
        <button className="admin-primary-action" type="button" onClick={() => setIsCreateOpen(true)}>
          <PolypbaseIcon name="plus" size={18} />
          {t('adminAddOrganization')}
        </button>
      </div>

      <div className="admin-organizations-summary" aria-label={t('adminOrganizationsTitle')}>
        <span>
          <strong>{organizations.length}</strong>
          {t('adminOrganizationsKnownCount')}
        </span>
        <span>
          <strong>{countryCount}</strong>
          {t('adminOrganizationsCountriesCount')}
        </span>
      </div>

      {isCreateOpen ? (
        <AdminActionPanel title={t('adminAddOrganization')} closeLabel={t('close')} onClose={() => setIsCreateOpen(false)}>
          <OrganizationCreateForm
            organizations={organizations}
            profile={profile}
            onCreateOrganization={onCreateOrganization}
            t={t}
          />
        </AdminActionPanel>
      ) : null}

      <div className="admin-organizations-workspace">
        <section className="organization-directory-panel" aria-labelledby="organization-directory-title">
          <div className="organization-directory-heading">
            <h3 id="organization-directory-title">{t('adminExistingOrganizations')}</h3>
          </div>
          <OrganizationManagementList
            organizations={organizations}
            profile={profile}
            onDeleteOrganization={onDeleteOrganization}
            onUpdateOrganization={onUpdateOrganization}
            t={t}
          />
        </section>
      </div>
    </section>
  );
}

export default function AdminView({
  activeOrganizationId,
  activeSection,
  boxes,
  boxCollectionStatus,
  onRetryBoxCollection,
  exportOptions,
  isLoading,
  isOptionsLoading,
  language,
  profile,
  onResponsableChange,
  onSelectSection,
  onRequestOptions,
  onCreateZone,
  onUpdateZone,
  onLoadZoneDeletion,
  onDeleteZone,
  onCreateOrganization,
  onDeleteOrganization,
  onUpdateOrganization,
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
  exportOptions: ExportOptions | null;
  isLoading: boolean;
  isOptionsLoading: boolean;
  language: Language;
  profile: UserProfile | null;
  onResponsableChange: (organizationId: number, isResponsable: boolean) => void;
  onSelectSection: (section: AdminSectionKey) => void;
  onRequestOptions: () => void;
  onCreateZone: (payload: ThermalZonePayload) => Promise<{ id: number }>;
  onUpdateZone: (zoneId: number, payload: ThermalZoneUpdatePayload) => Promise<void>;
  onLoadZoneDeletion: (zoneId: number) => Promise<ThermalZoneDeletionEligibility>;
  onDeleteZone: (zoneId: number) => Promise<void>;
  onCreateOrganization: (payload: OrganizationPayload) => Promise<void>;
  onDeleteOrganization: (organizationId: number) => Promise<void>;
  onUpdateOrganization: (organizationId: number, payload: OrganizationPayload) => Promise<void>;
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
  const displayedSection = DISABLED_ADMIN_SECTIONS.has(activeSection) ? 'accounts' : activeSection;
  const activeSectionNeedsOptions = displayedSection === 'organizations';

  useEffect(() => {
    const sectionIsUnavailable = DISABLED_ADMIN_SECTIONS.has(activeSection);
    const organizationAccessIsDenied = Boolean(
      profile && !profile.is_superuser && activeSection === 'organizations',
    );
    if (sectionIsUnavailable || organizationAccessIsDenied) onSelectSection('accounts');
  }, [activeSection, onSelectSection, profile]);

  function selectAdminSection(section: AdminSectionKey) {
    if (DISABLED_ADMIN_SECTIONS.has(section)) return;
    onSelectSection(section);
    if (section === 'organizations' && !exportOptions) {
      onRequestOptions();
    }
  }

  if (isLoading) {
    return <PageLoader variant="admin" label={t('adminTitle')} />;
  }

  if (!profile || !userHasAdminRole(profile)) return null;

  const organizations = exportOptions?.organizations ?? profile.organizations;
  const activeOrganizationName = profile.active_organization?.name ?? '-';

  // A Species page is a full entity page, like a Box or a Location: the
  // Administration section list returns with the catalog.
  const isSpeciesPage = displayedSection === 'species' && speciesTarget !== null;

  return (
    <section className="admin-panel">
      <div className={isSpeciesPage ? 'admin-workspace is-entity-page' : 'admin-workspace'}>
        {isSpeciesPage ? null : (
          <AdminFlowNav
            activeSection={displayedSection}
            aquariumName={activeOrganizationName}
            isSuperuser={profile.is_superuser}
            onSelect={selectAdminSection}
            t={t}
          />
        )}

        <div className="admin-flow-panel">
          {activeSectionNeedsOptions && isOptionsLoading ? (
            <PageLoader variant="admin" label={t('loading')} />
          ) : null}

          {displayedSection === 'accounts' ? (
            <AccountManagementSection
              key={profile.active_organization?.id ?? 'no-organization'}
              organizationName={activeOrganizationName}
              onResponsableChange={onResponsableChange}
              t={t}
            />
          ) : null}

          {displayedSection === 'inventory' ? (
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

          {displayedSection === 'species' && activeOrganizationId != null ? (
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

          {displayedSection === 'references' && activeOrganizationId != null ? (
            <TaxonomyAdminSection
              key={activeOrganizationId}
              organizationName={profile?.organizations.find((organization) => organization.id === activeOrganizationId)?.name ?? ''}
              onOpenSpeciesWorkspace={() => selectAdminSection('species')}
              t={t}
            />
          ) : null}

          {displayedSection === 'environment' && activeOrganizationId != null ? (
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

          {displayedSection === 'organizations' && profile.is_superuser && !isOptionsLoading ? (
            <OrganizationsAdminSection
              organizations={organizations}
              profile={profile}
              onCreateOrganization={onCreateOrganization}
              onDeleteOrganization={onDeleteOrganization}
              onUpdateOrganization={onUpdateOrganization}
              t={t}
            />
          ) : null}

          {displayedSection === 'transfers' && activeOrganizationId != null ? (
            <InstitutionalExchangesPage
              key={`${profile.id}:${activeOrganizationId}`}
              organizationId={activeOrganizationId}
              language={language}
              onOpenBox={onOpenBox}
              t={t}
            />
          ) : null}

          {displayedSection === 'history' && activeOrganizationId != null ? (
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
