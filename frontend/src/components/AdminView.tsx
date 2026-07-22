import { type ChangeEvent, type FormEvent, useEffect, useMemo, useState } from 'react';

import { apiGet, apiPatch, apiPost } from '../api/client';
import type {
  AccountMember,
  AccountMembers,
  BoxItem,
  ExportOptions,
  MembershipRole,
  NewMemberPayload,
  Organization,
  Probe,
  ThermalZone,
  UserProfile,
} from '../types';
import type {
  BoxTransferPayload,
  BoxTransferResult,
  OrganizationPayload,
  ProbePayload,
  ThermalZonePayload,
} from '../types/admin';
import { formatDisplayDate } from '../utils/dateFormat';
import { getErrorMessage } from '../utils/errors';
import { buildQrLabelItem, printQrLabels } from '../utils/qrLabels';
import { decrementDecimalValue, incrementDecimalValue } from '../utils/stepValue';
import { getZoneOccupancyLevel } from '../utils/zoneOccupancy';
import { useConfirmAction } from './ConfirmActionModal';
import PageLoader from './PageLoader';
import SkeletonRows from './SkeletonRows';

type TFunction = (key: string) => string;

type AdminAuditLogEntry = {
  id: number;
  created_at: string;
  organization: string | null;
  user: string | null;
  action: string;
  action_label: string;
  object_type: string;
  object_id: string;
  description: string;
  metadata: Record<string, unknown>;
};

type AdminAuditActionOption = {
  value: string;
  label: string;
  count: number;
};

type AdminAuditLogResponse = {
  results: AdminAuditLogEntry[];
  limit?: number;
  offset?: number;
  has_more?: boolean;
  next_offset?: number | null;
  total_count?: number;
  action_options?: AdminAuditActionOption[];
};

const ADMIN_AUDIT_PAGE_SIZE = 40;

const ADMIN_FLOW_ITEMS = [
  { key: 'accounts', panelId: 'admin-accounts', label: 'adminTabUsers' },
  { key: 'environment', panelId: 'admin-environment', label: 'adminTabLocations' },
  { key: 'organizations', panelId: 'admin-organizations', label: 'adminTabOrganizations' },
  { key: 'transfers', panelId: 'admin-transfers', label: 'adminTabTransfers' },
  { key: 'history', panelId: 'admin-history', label: 'adminTabHistory' },
] as const;

type AdminFlowKey = (typeof ADMIN_FLOW_ITEMS)[number]['key'];

function userHasAdminRole(profile: UserProfile | null) {
  if (!profile) return false;
  if (profile.is_superuser) return true;
  return profile.memberships.some((membership) => membership.role === 'admin');
}

// Zone capacity is added a rack at a time, and salinity is read off a
// refractometer that lands on round values, so the -/+ buttons move in the
// steps the technicians actually work in. Typing stays free (see ZoneStepField).
const ZONE_CAPACITY_STEP = 10;
const ZONE_SALINITY_STEP = 5;

const emptyMemberForm = {
  username: '',
  first_name: '',
  last_name: '',
  email: '',
};

function formatFirstName(value: string) {
  return value
    .trim()
    .replace(/\s+/g, ' ')
    .toLocaleLowerCase('fr-FR')
    .replace(/(^|[\s'-])(\p{L})/gu, (_match, separator: string, letter: string) => {
      return `${separator}${letter.toLocaleUpperCase('fr-FR')}`;
    });
}

function formatLastName(value: string) {
  return value.trim().replace(/\s+/g, ' ').toLocaleUpperCase('fr-FR');
}

function getMemberDisplayName(member: AccountMember) {
  const displayName = member.full_name.trim();
  if (!displayName) return member.username;

  const parts = displayName.split(/\s+/).filter(Boolean);
  if (parts.length === 1) return formatFirstName(parts[0]);

  const lastName = parts[parts.length - 1] ?? '';
  const firstNames = parts.slice(0, -1).map(formatFirstName).join(' ');
  return `${firstNames} ${formatLastName(lastName)}`;
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
    <div className="admin-suggest-field">
      <input
        id={id}
        type="text"
        value={value}
        autoComplete="off"
        onBlur={() => window.setTimeout(() => setIsFocused(false), 120)}
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

function AccountManagementSection({ t }: { t: TFunction }) {
  const [data, setData] = useState<AccountMembers | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [form, setForm] = useState(emptyMemberForm);
  const [organizationId, setOrganizationId] = useState<number | null>(null);
  const [role, setRole] = useState<MembershipRole>('viewer');
  const [isAdding, setIsAdding] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [rowBusyId, setRowBusyId] = useState<number | null>(null);
  const [accountSearch, setAccountSearch] = useState('');
  const [roleFilter, setRoleFilter] = useState<MembershipRole | 'all'>('all');
  const [statusFilter, setStatusFilter] = useState<'all' | 'active' | 'inactive'>('all');

  useEffect(() => {
    let isActive = true;

    async function loadMembers() {
      try {
        setIsLoading(true);
        const response = await apiGet<AccountMembers>('/api/accounts/members/');
        if (!isActive) return;
        setData(response);
        setOrganizationId(response.manageable_organizations[0]?.id ?? null);
      } catch (requestError) {
        if (!isActive) return;
        setLoadError(getErrorMessage(requestError));
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
          a.username.localeCompare(b.username),
      );
      return { ...current, members };
    });
  }

  async function handleAddMember(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (isAdding || organizationId == null) return;

    setIsAdding(true);
    setFormError(null);
    setMessage(null);

    const payload: NewMemberPayload = {
      ...form,
      username: form.username.trim(),
      first_name: formatFirstName(form.first_name),
      last_name: formatLastName(form.last_name),
      organization_id: organizationId,
      role,
    };

    try {
      const member = await apiPost<AccountMember>('/api/accounts/members/', payload);
      upsertMember(member);
      setForm(emptyMemberForm);
      setRole('viewer');
      setMessage(t('manageMemberAdded'));
    } catch (requestError) {
      setFormError(getErrorMessage(requestError));
    } finally {
      setIsAdding(false);
    }
  }

  async function handleRoleChange(member: AccountMember, nextRole: MembershipRole) {
    if (nextRole === member.role) return;
    setRowBusyId(member.membership_id);
    setMessage(null);
    setLoadError(null);
    try {
      const updated = await apiPatch<AccountMember>(
        `/api/accounts/members/${member.membership_id}/`,
        { role: nextRole },
      );
      upsertMember(updated);
      setMessage(t('manageRoleUpdated'));
    } catch (requestError) {
      setLoadError(getErrorMessage(requestError));
    } finally {
      setRowBusyId(null);
    }
  }

  async function handleToggleActive(member: AccountMember) {
    setRowBusyId(member.membership_id);
    setMessage(null);
    setLoadError(null);
    try {
      const updated = await apiPatch<AccountMember>(
        `/api/accounts/members/${member.membership_id}/`,
        { is_active: !member.is_active },
      );
      upsertMember(updated);
    } catch (requestError) {
      setLoadError(getErrorMessage(requestError));
    } finally {
      setRowBusyId(null);
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

  const organizations = data.manageable_organizations;
  const roles = data.roles;
  const activeMemberCount = data.members.filter((member) => member.is_active).length;
  const adminMemberCount = data.members.filter((member) => member.role === 'admin').length;
  const technicianMemberCount = data.members.filter(
    (member) => member.role === 'lab_technician',
  ).length;
  const viewerMemberCount = data.members.filter((member) => member.role === 'viewer').length;
  const normalizedAccountSearch = accountSearch.trim().toLocaleLowerCase('fr-FR');
  const filteredMembers = data.members.filter((member) => {
    const matchesSearch = normalizedAccountSearch
      ? [
          member.full_name,
          member.username,
          member.email,
          member.organization.name,
          member.role_label,
        ].some((value) => value.toLocaleLowerCase('fr-FR').includes(normalizedAccountSearch))
      : true;
    const matchesRole = roleFilter === 'all' || member.role === roleFilter;
    const matchesStatus =
      statusFilter === 'all' ||
      (statusFilter === 'active' ? member.is_active : !member.is_active);
    return matchesSearch && matchesRole && matchesStatus;
  });

  function toggleRoleFilter(nextRole: MembershipRole) {
    setRoleFilter((currentRole) => (currentRole === nextRole ? 'all' : nextRole));
    setStatusFilter('all');
  }

  function toggleStatusFilter(nextStatus: 'active' | 'inactive') {
    setStatusFilter((currentStatus) => (currentStatus === nextStatus ? 'all' : nextStatus));
    setRoleFilter('all');
  }

  return (
    <section className="admin-section account-management" id="admin-accounts">
      <div className="admin-section-heading account-management-heading">
        <div>
          <h2>{t('manageAccountsTitle')}</h2>
        </div>
      </div>

      <div className="account-overview">
        <button
          type="button"
          className={statusFilter === 'active' ? 'account-overview-card is-active' : 'account-overview-card'}
          onClick={() => toggleStatusFilter('active')}
        >
          <strong>{activeMemberCount}</strong>
          <span>{t('manageActiveAccounts')}</span>
        </button>
        <button
          type="button"
          className={roleFilter === 'admin' ? 'account-overview-card is-active' : 'account-overview-card'}
          onClick={() => toggleRoleFilter('admin')}
        >
          <strong>{adminMemberCount}</strong>
          <span>{t('manageAdminAccounts')}</span>
        </button>
        <button
          type="button"
          className={
            roleFilter === 'lab_technician' ? 'account-overview-card is-active' : 'account-overview-card'
          }
          onClick={() => toggleRoleFilter('lab_technician')}
        >
          <strong>{technicianMemberCount}</strong>
          <span>{t('manageTechnicianAccounts')}</span>
        </button>
        <button
          type="button"
          className={roleFilter === 'viewer' ? 'account-overview-card is-active' : 'account-overview-card'}
          onClick={() => toggleRoleFilter('viewer')}
        >
          <strong>{viewerMemberCount}</strong>
          <span>{t('manageViewerAccounts')}</span>
        </button>
      </div>

      <form className="member-add-form" onSubmit={handleAddMember}>
        <div className="member-add-header">
          <div>
            <p className="member-add-title">{t('manageAddTitle')}</p>
            <small>{t('manageAddSubtitle')}</small>
          </div>
          <div className="member-password-flow">
            <strong>{t('manageTemporaryPasswordTitle')}</strong>
            <span>{t('manageTemporaryPasswordText')}</span>
          </div>
        </div>
        <div className="member-add-grid">
          <label>
            {t('manageFieldUsername')}
            <input
              required
              value={form.username}
              onChange={(event) => setForm((current) => ({ ...current, username: event.target.value }))}
            />
          </label>
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
              value={form.email}
              onChange={(event) => setForm((current) => ({ ...current, email: event.target.value }))}
            />
          </label>
          {organizations.length > 1 ? (
            <label>
              {t('manageFieldOrganization')}
              <select
                value={organizationId ?? ''}
                onChange={(event) => setOrganizationId(Number(event.target.value))}
              >
                {organizations.map((organization) => (
                  <option key={organization.id} value={organization.id}>
                    {organization.name}
                  </option>
                ))}
              </select>
            </label>
          ) : null}
          <label>
            {t('manageFieldRole')}
            <select value={role} onChange={(event) => setRole(event.target.value as MembershipRole)}>
              {roles.map((roleOption) => (
                <option key={roleOption.value} value={roleOption.value}>
                  {roleOption.label}
                </option>
              ))}
            </select>
          </label>
        </div>

        {formError ? <p className="inline-error">{formError}</p> : null}

        <button type="submit" disabled={isAdding || organizationId == null}>
          {isAdding ? t('manageAdding') : t('manageAddAction')}
        </button>
      </form>

      {message ? <p className="inline-success">{message}</p> : null}
      {loadError && data ? <p className="inline-error">{loadError}</p> : null}

      <div className="account-list-toolbar">
        <label className="account-search-field">
          <span>{t('manageSearchLabel')}</span>
          <input
            type="search"
            value={accountSearch}
            placeholder={t('manageSearchPlaceholder')}
            onChange={(event) => setAccountSearch(event.target.value)}
          />
        </label>
        <label className="account-compact-filter">
          <span>{t('manageColRole')}</span>
          <select
            value={roleFilter}
            onChange={(event) => {
              setRoleFilter(event.target.value as MembershipRole | 'all');
              setStatusFilter('all');
            }}
          >
            <option value="all">{t('manageRoleAll')}</option>
            {roles.map((roleOption) => (
              <option key={roleOption.value} value={roleOption.value}>
                {roleOption.label}
              </option>
            ))}
          </select>
        </label>
        <div className="account-status-filter" aria-label={t('manageColStatus')}>
          <button
            type="button"
            className={statusFilter === 'all' ? 'is-active' : ''}
            onClick={() => setStatusFilter('all')}
          >
            {t('manageStatusAll')}
          </button>
          <button
            type="button"
            className={statusFilter === 'active' ? 'is-active' : ''}
            onClick={() => {
              setStatusFilter('active');
              setRoleFilter('all');
            }}
          >
            {t('manageStatusActive')}
          </button>
          <button
            type="button"
            className={statusFilter === 'inactive' ? 'is-active' : ''}
            onClick={() => {
              setStatusFilter('inactive');
              setRoleFilter('all');
            }}
          >
            {t('manageStatusInactive')}
          </button>
        </div>
        <span className="account-visible-count">
          {filteredMembers.length} / {data.members.length}
        </span>
      </div>

      {filteredMembers.length ? (
        <div className="member-card-list">
          {filteredMembers.map((member) => (
            <article
              key={member.membership_id}
              className={member.is_active ? 'member-card' : 'member-card is-inactive'}
            >
              <div className="member-card-main">
                <span className="member-identity">
                  <strong>{getMemberDisplayName(member)}</strong>
                  <small>
                    @{member.username}
                    {member.email ? ` · ${member.email}` : ''}
                  </small>
                  <em>{member.organization.name}</em>
                </span>
              </div>

              <div className="member-card-controls">
                <label className="member-role-field">
                  <span>{t('manageColRole')}</span>
                  <select
                    value={member.role}
                    disabled={member.is_self || rowBusyId === member.membership_id}
                    onChange={(event) =>
                      handleRoleChange(member, event.target.value as MembershipRole)
                    }
                  >
                    {roles.map((roleOption) => (
                      <option key={roleOption.value} value={roleOption.value}>
                        {roleOption.label}
                      </option>
                    ))}
                  </select>
                </label>

                <span className="member-last-login">
                  <small>{t('manageColLastLogin')}</small>
                  <strong>
                    {member.last_login ? formatDisplayDate(member.last_login) : t('manageNeverConnected')}
                  </strong>
                </span>

                <span className="member-status">
                  <span className={member.is_active ? 'member-state is-on' : 'member-state is-off'}>
                    {member.is_active ? t('manageStatusActive') : t('manageStatusInactive')}
                  </span>
                  {member.is_self ? (
                    <em className="member-self-tag">{t('manageStatusSelf')}</em>
                  ) : (
                    <button
                      type="button"
                      className="member-toggle"
                      disabled={rowBusyId === member.membership_id}
                      onClick={() => handleToggleActive(member)}
                    >
                      {member.is_active ? t('manageDeactivate') : t('manageReactivate')}
                    </button>
                  )}
                </span>
              </div>
            </article>
          ))}
        </div>
      ) : (
        <p className="muted compact-text">
          {data.members.length ? t('manageNoFilteredMembers') : t('manageNoMembers')}
        </p>
      )}
    </section>
  );
}

const PROBE_TYPE_OPTIONS = [
  { value: 'lorawan', label: 'LoRaWAN' },
  { value: 'iminilide', label: 'iMinilide' },
  { value: 'manual', label: 'Manuel / Manual' },
  { value: 'other', label: 'Autre / Other' },
];

function getAdminOrganizationIds(profile: UserProfile): Set<number> | null {
  // null means "all organizations" (superuser).
  if (profile.is_superuser) return null;
  return new Set(
    profile.memberships
      .filter((membership) => membership.role === 'admin')
      .map((membership) => membership.organization.id),
  );
}

function sortAdminZones(a: ThermalZone, b: ThermalZone) {
  const aTemperature = Number.parseFloat(a.target_temperature_c ?? '');
  const bTemperature = Number.parseFloat(b.target_temperature_c ?? '');
  const aRank = Number.isFinite(aTemperature) ? aTemperature : Number.POSITIVE_INFINITY;
  const bRank = Number.isFinite(bTemperature) ? bTemperature : Number.POSITIVE_INFINITY;
  if (aRank !== bRank) return aRank - bRank;
  return a.name.localeCompare(b.name, 'fr-FR');
}

function formatZoneTemperature(value: string | number | null | undefined) {
  if (value === null || value === undefined || value === '') return '-';
  const numericValue = typeof value === 'number' ? value : Number.parseFloat(value);
  if (!Number.isFinite(numericValue)) return `${value}`;
  return `${numericValue.toFixed(1)}°C`;
}

function formatZoneSalinity(value: string | number | null | undefined) {
  if (value === null || value === undefined || value === '') return '-';
  const numericValue = typeof value === 'number' ? value : Number.parseFloat(value);
  if (!Number.isFinite(numericValue)) return `${value}`;
  return Number.isInteger(numericValue) ? `${numericValue}` : numericValue.toFixed(1);
}

function getProbeTypeLabel(probeType: string) {
  return PROBE_TYPE_OPTIONS.find((option) => option.value === probeType)?.label ?? probeType;
}

function ZoneCreateForm({
  profile,
  onCreateZone,
  t,
}: {
  profile: UserProfile;
  onCreateZone: (payload: ThermalZonePayload) => Promise<void>;
  t: TFunction;
}) {
  const adminOrganizations = useMemo(() => {
    const adminOrgIds = getAdminOrganizationIds(profile);
    if (adminOrgIds === null) return profile.organizations;
    return profile.organizations.filter((organization) => adminOrgIds.has(organization.id));
  }, [profile]);

  const [organizationId, setOrganizationId] = useState<number | null>(
    adminOrganizations[0]?.id ?? null,
  );
  const [name, setName] = useState('');
  const [zoneType, setZoneType] = useState('cabinet');
  const [targetTemperature, setTargetTemperature] = useState('');
  const [capacity, setCapacity] = useState('');
  const [salinity, setSalinity] = useState('');
  const [isSaving, setIsSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  if (!adminOrganizations.length) {
    return <p className="muted compact-text">{t('adminZoneNoOrganization')}</p>;
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (isSaving || organizationId == null) return;

    setIsSaving(true);
    setError(null);
    setMessage(null);

    try {
      await onCreateZone({
        organization: organizationId,
        name: name.trim(),
        zone_type: zoneType,
        target_temperature_c: targetTemperature.trim() || null,
        capacity: capacity ? Number(capacity) : null,
        salinity_psu: salinity.trim() || null,
      });
      setName('');
      setTargetTemperature('');
      setCapacity('');
      setSalinity('');
      setMessage(t('adminZoneCreated'));
    } catch (requestError) {
      setError(getErrorMessage(requestError));
    } finally {
      setIsSaving(false);
    }
  }

  return (
    <form className="admin-form" onSubmit={handleSubmit}>
      {adminOrganizations.length > 1 ? (
        <label>
          <span>{t('adminZoneOrganization')}</span>
          <select
            value={organizationId ?? ''}
            onChange={(event) => setOrganizationId(Number(event.target.value))}
          >
            {adminOrganizations.map((organization) => (
              <option key={organization.id} value={organization.id}>
                {organization.name}
              </option>
            ))}
          </select>
        </label>
      ) : null}
      <label>
        <span>{t('adminZoneName')}</span>
        <input
          required
          placeholder="Étuve 13"
          type="text"
          value={name}
          onChange={(event) => setName(event.target.value)}
        />
      </label>
      <label>
        <span>{t('adminZoneType')}</span>
        <select value={zoneType} onChange={(event) => setZoneType(event.target.value)}>
          <option value="cabinet">{t('adminZoneTypeCabinet')}</option>
          <option value="incubator">{t('adminZoneTypeIncubator')}</option>
        </select>
      </label>
      <label>
        <span>{t('adminTargetTemperature')}</span>
        <input
          placeholder="15.0"
          type="number"
          step="0.1"
          value={targetTemperature}
          onChange={(event) => setTargetTemperature(event.target.value)}
        />
      </label>
      <label>
        <span>{t('adminZoneCapacity')}</span>
        <ZoneStepField
          ariaPrefix={t('adminZoneCapacity')}
          step={ZONE_CAPACITY_STEP}
          value={capacity}
          onChange={setCapacity}
        />
      </label>
      <label>
        <span>{t('adminZoneSalinity')}</span>
        <ZoneStepField
          ariaPrefix={t('adminZoneSalinity')}
          step={ZONE_SALINITY_STEP}
          value={salinity}
          onChange={setSalinity}
          placeholder="35"
        />
      </label>
      <button type="submit" disabled={isSaving || !name.trim()}>
        {isSaving ? t('saving') : t('adminCreateZone')}
      </button>
      {message ? <p className="inline-success">{message}</p> : null}
      {error ? <p className="inline-error">{error}</p> : null}
    </form>
  );
}

function ProbeCreateForm({
  profile,
  zones,
  onCreateProbe,
  t,
}: {
  profile: UserProfile;
  zones: ThermalZone[];
  onCreateProbe: (payload: ProbePayload) => Promise<void>;
  t: TFunction;
}) {
  const zoneChoices = useMemo(() => {
    const adminOrgIds = getAdminOrganizationIds(profile);
    return zones.filter(
      (zone) => zone.is_active && (adminOrgIds === null || adminOrgIds.has(zone.organization.id)),
    );
  }, [profile, zones]);

  const [zoneId, setZoneId] = useState<number | null>(zoneChoices[0]?.id ?? null);
  const [code, setCode] = useState('');
  const [probeType, setProbeType] = useState('lorawan');
  const [isSaving, setIsSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  if (!zoneChoices.length) {
    return <p className="muted compact-text">{t('adminProbeNoZone')}</p>;
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (isSaving || zoneId == null) return;

    setIsSaving(true);
    setError(null);
    setMessage(null);

    try {
      await onCreateProbe({
        thermal_zone: zoneId,
        code: code.trim(),
        probe_type: probeType,
        location: '',
      });
      setCode('');
      setMessage(t('adminProbeCreated'));
    } catch (requestError) {
      setError(getErrorMessage(requestError));
    } finally {
      setIsSaving(false);
    }
  }

  return (
    <form className="admin-form probe-create-form" onSubmit={handleSubmit}>
      <label>
        <span>{t('adminProbeCode')}</span>
        <input
          required
          placeholder="SONDE-15-01"
          type="text"
          value={code}
          onChange={(event) => setCode(event.target.value)}
        />
      </label>
      <label>
        <span>{t('adminProbeType')}</span>
        <select value={probeType} onChange={(event) => setProbeType(event.target.value)}>
          {PROBE_TYPE_OPTIONS.map((option) => (
            <option key={option.value} value={option.value}>{option.label}</option>
          ))}
        </select>
      </label>
      <label>
        <span>{t('adminProbeZone')}</span>
        <select
          value={zoneId ?? ''}
          onChange={(event) => setZoneId(Number(event.target.value))}
        >
          {zoneChoices.map((zone) => (
            <option key={zone.id} value={zone.id}>{zone.name}</option>
          ))}
        </select>
      </label>
      <button type="submit" disabled={isSaving || !code.trim()}>
        {isSaving ? t('saving') : t('adminAddProbe')}
      </button>
      {message ? <p className="inline-success">{message}</p> : null}
      {error ? <p className="inline-error">{error}</p> : null}
    </form>
  );
}

/** Editable values of a zone row: kept as strings so an emptied field clears it. */
/**
 * Number field whose -/+ buttons move by `step` while typing stays free.
 *
 * The input deliberately carries no `step` attribute: browsers count valid
 * values from `min` and reject anything off-step, which would refuse a capacity
 * of 45 or a salinity read at 32. The buttons provide the increment instead.
 */
function ZoneStepField({
  ariaPrefix,
  step,
  value,
  onChange,
  placeholder,
}: {
  ariaPrefix: string;
  step: number;
  value: string;
  onChange: (next: string) => void;
  placeholder?: string;
}) {
  return (
    <div className="zone-step-field">
      <button
        type="button"
        aria-label={`${ariaPrefix} -${step}`}
        onClick={() => onChange(decrementDecimalValue(value, step))}
      >
        -
      </button>
      <input
        min="0"
        type="number"
        inputMode="decimal"
        placeholder={placeholder}
        value={value}
        onChange={(event) => onChange(event.target.value)}
      />
      <button
        type="button"
        aria-label={`${ariaPrefix} +${step}`}
        onClick={() => onChange(incrementDecimalValue(value, step))}
      >
        +
      </button>
    </div>
  );
}

type ZoneDraft = { capacity: string; salinity: string };

function buildZoneDrafts(zones: ThermalZone[]): Record<number, ZoneDraft> {
  return Object.fromEntries(
    zones.map((zone) => [
      zone.id,
      { capacity: zone.capacity?.toString() ?? '', salinity: zone.salinity_psu ?? '' },
    ]),
  );
}

function ZoneCapacityManager({
  profile,
  zones,
  onUpdateZone,
  t,
}: {
  profile: UserProfile;
  zones: ThermalZone[];
  onUpdateZone: (zoneId: number, payload: ThermalZonePayload) => Promise<void>;
  t: TFunction;
}) {
  const adminOrgIds = useMemo(() => getAdminOrganizationIds(profile), [profile]);
  const editableZones = useMemo(
    () =>
      zones
        .filter((zone) => adminOrgIds === null || adminOrgIds.has(zone.organization.id))
        .sort(sortAdminZones),
    [adminOrgIds, zones],
  );
  const [drafts, setDrafts] = useState<Record<number, ZoneDraft>>(() => buildZoneDrafts(editableZones));
  const [busyZoneId, setBusyZoneId] = useState<number | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [zoneSearch, setZoneSearch] = useState('');

  useEffect(() => {
    setDrafts(buildZoneDrafts(editableZones));
  }, [editableZones]);

  const normalizedZoneSearch = zoneSearch.trim().toLocaleLowerCase('fr-FR');
  const visibleZones = editableZones.filter((zone) => {
    if (!normalizedZoneSearch) return true;
    return [
      zone.name,
      zone.organization.name,
      zone.zone_type,
      zone.target_temperature_c ?? '',
      `${zone.target_temperature_c ?? ''}°C`,
    ].some((value) => value.toLocaleLowerCase('fr-FR').includes(normalizedZoneSearch));
  });

  if (!editableZones.length) return null;

  function updateDraft(zoneId: number, field: keyof ZoneDraft, value: string) {
    setDrafts((current) => ({
      ...current,
      [zoneId]: { ...current[zoneId], [field]: value },
    }));
  }

  async function saveZone(zone: ThermalZone) {
    const draft = drafts[zone.id] ?? { capacity: '', salinity: '' };
    const capacityValue = draft.capacity.trim();
    const salinityValue = draft.salinity.trim();
    setBusyZoneId(zone.id);
    setMessage(null);
    setError(null);

    try {
      await onUpdateZone(zone.id, {
        organization: zone.organization.id,
        name: zone.name,
        zone_type: zone.zone_type,
        target_temperature_c: zone.target_temperature_c,
        capacity: capacityValue ? Number(capacityValue) : null,
        // An emptied field clears the value rather than keeping the old one.
        salinity_psu: salinityValue || null,
      });
      setMessage(t('adminZoneUpdated'));
    } catch (requestError) {
      setError(getErrorMessage(requestError));
    } finally {
      setBusyZoneId(null);
    }
  }

  return (
    <div className="zone-capacity-manager environment-directory">
      <div className="environment-directory-toolbar">
        <label className="environment-search-field">
          <span>{t('manageSearchLabel')}</span>
          <input
            type="search"
            value={zoneSearch}
            placeholder={t('adminEnvironmentSearchPlaceholder')}
            onChange={(event) => setZoneSearch(event.target.value)}
          />
        </label>
        <span className="environment-visible-count">
          {visibleZones.length} / {editableZones.length}
        </span>
      </div>

      {visibleZones.length ? (
        <div className="environment-zone-grid">
          {visibleZones.map((zone) => {
          // Orange when fewer than 10 slots remain, red once full.
          const occupancy = getZoneOccupancyLevel(zone.box_count, zone.capacity);
          const probeCount = zone.probes?.length ?? 0;
          return (
            <article className="environment-zone-editor" key={zone.id}>
              <header className="environment-zone-editor-head">
                <div>
                  <span className="environment-zone-type">
                    {zone.zone_type === 'incubator' ? t('adminZoneTypeIncubator') : t('adminZoneTypeCabinet')}
                  </span>
                  <h4>{zone.name}</h4>
                  <p>{zone.organization.name}</p>
                </div>
                <span className={`zone-occupancy is-${occupancy}`}>
                  {zone.box_count}{zone.capacity ? ` / ${zone.capacity}` : ''}
                </span>
              </header>

              <dl className="environment-zone-metrics">
                <div>
                  <dt>{t('adminTargetTemperature')}</dt>
                  <dd>{formatZoneTemperature(zone.target_temperature_c)}</dd>
                </div>
                <div>
                  <dt>{t('adminZoneSalinity')}</dt>
                  <dd>{formatZoneSalinity(zone.salinity_psu)}</dd>
                </div>
                <div>
                  <dt>{t('adminZoneProbes')}</dt>
                  <dd>{probeCount}</dd>
                </div>
              </dl>

              <div className="environment-probe-list" aria-label={t('adminZoneProbes')}>
                {probeCount ? (
                  zone.probes.map((probe) => (
                    <span key={probe.id} className={probe.is_active ? 'probe-chip' : 'probe-chip is-inactive'}>
                      <strong>{probe.code}</strong>
                      <small>{getProbeTypeLabel(probe.probe_type)}</small>
                    </span>
                  ))
                ) : (
                  <span className="probe-chip is-empty">{t('adminEnvironmentNoProbe')}</span>
                )}
              </div>

              <div className="environment-zone-edit-fields">
                <label>
                  <small>{t('adminZoneCapacity')}</small>
                  <ZoneStepField
                    ariaPrefix={`${zone.name} ${t('adminZoneCapacity')}`}
                    step={ZONE_CAPACITY_STEP}
                    value={drafts[zone.id]?.capacity ?? ''}
                    onChange={(next) => updateDraft(zone.id, 'capacity', next)}
                  />
                </label>
                <label>
                  <small>{t('adminZoneSalinity')}</small>
                  <ZoneStepField
                    ariaPrefix={`${zone.name} ${t('adminZoneSalinity')}`}
                    step={ZONE_SALINITY_STEP}
                    value={drafts[zone.id]?.salinity ?? ''}
                    onChange={(next) => updateDraft(zone.id, 'salinity', next)}
                    placeholder="35"
                  />
                </label>
                <button
                  type="button"
                  className="environment-zone-save"
                  disabled={busyZoneId === zone.id}
                  onClick={() => void saveZone(zone)}
                >
                  {busyZoneId === zone.id ? t('saving') : t('adminSaveZone')}
                </button>
              </div>
            </article>
          );
        })}
        </div>
      ) : (
        <p className="environment-empty-state">{t('adminEnvironmentNoZoneResults')}</p>
      )}
      {message ? <p className="inline-success">{message}</p> : null}
      {error ? <p className="inline-error">{error}</p> : null}
    </div>
  );
}

function OrganizationCreateForm({
  organizations,
  profile,
  onCreateOrganization,
  t,
}: {
  organizations: Organization[];
  profile: UserProfile;
  onCreateOrganization: (payload: OrganizationPayload) => Promise<void>;
  t: TFunction;
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
  const [message, setMessage] = useState<string | null>(null);
  const existingCities = useMemo(() => organizations.map((organization) => organization.city).filter(Boolean), [organizations]);
  const cityOptions = useMemo(() => getCityOptions(country, existingCities), [country, existingCities]);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (isSaving) return;

    setIsSaving(true);
    setError(null);
    setMessage(null);

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
      setMessage(t('adminOrganizationCreated'));
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
      {message ? <p className="inline-success">{message}</p> : null}
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

function PencilIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
      <path d="M15.8 4.8l3.4 3.4" />
      <path d="M5 19l3.9-.8L19.1 8a2.4 2.4 0 0 0-3.4-3.4L5.6 14.8 5 19z" />
      <path d="M12.8 7.7l3.5 3.5" />
    </svg>
  );
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
  t: TFunction;
}) {
  const [editingId, setEditingId] = useState<number | null>(null);
  const [name, setName] = useState('');
  const [city, setCity] = useState('');
  const [country, setCountry] = useState('');
  const [contactEmail, setContactEmail] = useState('');
  const [notes, setNotes] = useState('');
  const [isSaving, setIsSaving] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const { confirmAction, confirmActionModal } = useConfirmAction();
  const existingCities = useMemo(() => organizations.map((organization) => organization.city).filter(Boolean), [organizations]);
  const cityOptions = useMemo(() => getCityOptions(country, existingCities), [country, existingCities]);
  const [organizationSearch, setOrganizationSearch] = useState('');
  const [countryFilter, setCountryFilter] = useState('all');
  const canManageOrganizations = userHasAdminRole(profile);
  const countryOptions = useMemo(
    () =>
      Array.from(new Set(organizations.map((organization) => organization.country).filter(Boolean)))
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
    setMessage(null);
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
    setMessage(null);

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
      setMessage(t('adminOrganizationUpdated'));
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
    setMessage(null);

    try {
      await onDeleteOrganization(organization.id);
      if (editingId === organization.id) cancelEdit();
      setMessage(t('adminOrganizationDeleted'));
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
                        <PencilIcon />
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
        <p className="organization-empty-state">{t('adminOrganizationNoResults')}</p>
      )}

      {message ? <p className="inline-success">{message}</p> : null}
      {error ? <p className="inline-error">{error}</p> : null}
      {confirmActionModal}
    </div>
  );
}

function TransferCreateForm({
  profile,
  boxes,
  organizations,
  onCreateTransfer,
  t,
}: {
  profile: UserProfile;
  boxes: BoxItem[];
  organizations: Array<{ id: number; name: string }>;
  onCreateTransfer: (payload: BoxTransferPayload) => Promise<BoxTransferResult>;
  t: TFunction;
}) {
  const transferableBoxes = useMemo(() => {
    const adminOrgIds = getAdminOrganizationIds(profile);
    return boxes.filter(
      (box) =>
        box.status === 'active' &&
        (adminOrgIds === null || adminOrgIds.has(box.organization.id)),
    );
  }, [profile, boxes]);

  const [boxId, setBoxId] = useState<number | null>(transferableBoxes[0]?.id ?? null);
  const [boxQuery, setBoxQuery] = useState('');
  const [targetOrgId, setTargetOrgId] = useState<number | null>(null);
  const [polypCount, setPolypCount] = useState('');
  const [notes, setNotes] = useState('');
  const [isSaving, setIsSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [preparedTransfer, setPreparedTransfer] = useState<PreparedTransfer | null>(null);
  const normalizedBoxQuery = boxQuery.trim().toLocaleLowerCase();
  const visibleTransferableBoxes = normalizedBoxQuery
    ? transferableBoxes.filter((box) => [
      box.global_code,
      box.local_code,
      box.species.scientific_name,
      box.species.common_name,
      box.strain.code,
      box.thermal_zone?.name,
      box.organization.name,
    ].some((value) => (value ?? '').toLocaleLowerCase().includes(normalizedBoxQuery)))
    : transferableBoxes;
  const sortedVisibleTransferableBoxes = useMemo(
    () => visibleTransferableBoxes.slice().sort(compareTransferBoxes),
    [visibleTransferableBoxes],
  );
  const transferBoxGroups = useMemo(
    () => groupTransferBoxes(sortedVisibleTransferableBoxes, t('noZone')),
    [sortedVisibleTransferableBoxes, t],
  );

  const selectedBox = transferableBoxes.find((box) => box.id === boxId) ?? null;
  // The target cannot be the box's current organization.
  const targetOrganizations = organizations.filter(
    (organization) => organization.id !== selectedBox?.organization.id,
  );

  useEffect(() => {
    if (boxId !== null && transferableBoxes.some((box) => box.id === boxId)) return;
    setBoxId(transferableBoxes[0]?.id ?? null);
  }, [boxId, transferableBoxes]);

  useEffect(() => {
    if (targetOrgId === null || targetOrganizations.some((organization) => organization.id === targetOrgId)) return;
    setTargetOrgId(null);
  }, [targetOrgId, targetOrganizations]);

  if (!transferableBoxes.length) {
    return <p className="muted compact-text">{t('adminTransferNoBox')}</p>;
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (isSaving || boxId == null || targetOrgId == null || !polypCount) return;

    setIsSaving(true);
    setError(null);
    setMessage(null);
    setPreparedTransfer(null);

    try {
      const transfer = await onCreateTransfer({
        box: boxId,
        to_organization: targetOrgId,
        polyp_count: Number(polypCount),
        notes: notes.trim(),
      });
      const box = transferableBoxes.find((item) => item.id === boxId);
      const targetOrganization = organizations.find((organization) => organization.id === targetOrgId);
      if (box && targetOrganization) {
        setPreparedTransfer({
          transfer,
          box,
          targetOrganization,
          exportData: buildTransferExportData(transfer, box, targetOrganization),
        });
      }
      setNotes('');
      setPolypCount('');
      setTargetOrgId(null);
      setMessage(t('adminTransferCreated'));
    } catch (requestError) {
      setError(getErrorMessage(requestError));
    } finally {
      setIsSaving(false);
    }
  }

  return (
    <form className="admin-transfer-form" onSubmit={handleSubmit}>
      <div className="transfer-box-picker">
        <div className="transfer-box-picker-head">
          <label className="transfer-box-search-field">
            <span>{t('adminTransferBox')}</span>
            <input
              type="search"
              value={boxQuery}
              placeholder={t('adminTransferBoxSearchPlaceholder')}
              onChange={(event) => setBoxQuery(event.target.value)}
            />
          </label>
          <span className="transfer-box-result-count">
            {sortedVisibleTransferableBoxes.length}
          </span>
        </div>

        {transferBoxGroups.length ? (
          <div className="transfer-box-results">
            {transferBoxGroups.map((group) => (
              <section className="transfer-box-group" key={group.key}>
                <header>
                  <strong>{group.zoneName}</strong>
                  <span>{group.boxes.length}</span>
                </header>
                <div className="transfer-box-group-list">
                  {group.boxes.map((box) => {
                    const isSelected = box.id === boxId;
                    return (
                      <button
                        type="button"
                        className={`transfer-box-option${isSelected ? ' is-selected' : ''}`}
                        key={box.id}
                        onClick={() => setBoxId(box.id)}
                      >
                        <span>
                          <strong>{box.global_code}</strong>
                          <small>{box.species.scientific_name}</small>
                        </span>
                        <em>{box.strain.code}</em>
                      </button>
                    );
                  })}
                </div>
              </section>
            ))}
          </div>
        ) : (
          <small className="inline-error">{t('adminTransferBoxSearchEmpty')}</small>
        )}
      </div>
      {selectedBox ? (
        <section className="transfer-selected-box-card" aria-label={t('adminTransferSelectedBox')}>
          <div>
            <span>{t('adminTransferSelectedBox')}</span>
            <strong>{selectedBox.global_code}</strong>
            <em>{selectedBox.species.scientific_name}</em>
          </div>
          <dl>
            <div>
              <dt>{t('adminTransferCurrentOrganization')}</dt>
              <dd>{selectedBox.organization.name}</dd>
            </div>
            <div>
              <dt>{t('adminTransferCurrentZone')}</dt>
              <dd>{selectedBox.thermal_zone?.name ?? '-'}</dd>
            </div>
          </dl>
        </section>
      ) : null}
      <div className="transfer-destination-grid">
        <label>
          <span>{t('adminTransferTarget')}</span>
          <select
            value={targetOrgId ?? ''}
            onChange={(event) => setTargetOrgId(event.target.value ? Number(event.target.value) : null)}
          >
            <option value="" disabled>{t('adminOrganizations')}</option>
            {targetOrganizations.map((organization) => (
              <option key={organization.id} value={organization.id}>{organization.name}</option>
            ))}
          </select>
        </label>
        <label>
          <span>{t('adminTransferPolyps')}</span>
          <input
            required
            min="1"
            step="1"
            inputMode="numeric"
            type="number"
            value={polypCount}
            onChange={(event) => setPolypCount(event.target.value)}
          />
        </label>
      </div>
      <label className="admin-wide-field">
        <span>{t('adminTransferNotes')}</span>
        <textarea rows={2} value={notes} onChange={(event) => setNotes(event.target.value)} />
      </label>
      <button type="submit" disabled={isSaving || targetOrgId == null || !polypCount}>
        {isSaving ? t('saving') : t('adminPrepareTransfer')}
      </button>
      {message ? <p className="inline-success">{message}</p> : null}
      {error ? <p className="inline-error">{error}</p> : null}
      {preparedTransfer ? (
        <section className="transfer-package" aria-label={t('adminTransferPackageTitle')}>
          <div>
            <strong>{t('adminTransferPackageTitle')}</strong>
            <p>
              {preparedTransfer.box.global_code} - {preparedTransfer.targetOrganization.name}
            </p>
          </div>
          <div className="transfer-package-actions">
            <button
              type="button"
              onClick={() => downloadTransferCsv(preparedTransfer, profile.interface_language === 'fr')}
            >
              {t('adminTransferDownloadData')}
            </button>
            <button
              type="button"
              onClick={() => printQrLabels([buildQrLabelItem(preparedTransfer.box)])}
            >
              {t('adminTransferPrintLabel')}
            </button>
          </div>
        </section>
      ) : null}
    </form>
  );
}

function compareTransferBoxes(first: BoxItem, second: BoxItem) {
  return compareAdminText(first.thermal_zone?.name ?? '', second.thermal_zone?.name ?? '')
    || compareAdminText(first.species.scientific_name, second.species.scientific_name)
    || compareAdminText(first.strain.code, second.strain.code)
    || compareAdminText(first.global_code, second.global_code);
}

function compareAdminText(first: string, second: string) {
  return first.localeCompare(second, 'fr', { numeric: true, sensitivity: 'base' });
}

function groupTransferBoxes(boxes: BoxItem[], noZoneLabel: string) {
  const groups = new Map<string, { key: string; zoneName: string; boxes: BoxItem[] }>();

  boxes.forEach((box) => {
    const key = box.thermal_zone ? `zone-${box.thermal_zone.id}` : 'zone-none';
    const zoneName = box.thermal_zone?.name ?? noZoneLabel;
    const group = groups.get(key) ?? { key, zoneName, boxes: [] };
    group.boxes.push(box);
    groups.set(key, group);
  });

  return Array.from(groups.values()).sort((first, second) => compareAdminText(first.zoneName, second.zoneName));
}

type TransferCsvRow = Record<string, string>;

function TransferImportForm({ profile, zones, boxes, t }: {
  profile: UserProfile;
  zones: ThermalZone[];
  boxes: BoxItem[];
  t: TFunction;
}) {
  const adminOrgIds = getAdminOrganizationIds(profile);
  const organizations = profile.organizations.filter(
    (organization) => adminOrgIds === null || adminOrgIds.has(organization.id),
  );
  const [sourceData, setSourceData] = useState<TransferCsvRow | null>(null);
  const [globalCode, setGlobalCode] = useState('');
  const [importedBox, setImportedBox] = useState<BoxItem | null>(null);
  const [organizationId, setOrganizationId] = useState<number | null>(organizations[0]?.id ?? null);
  const availableZones = zones.filter(
    (zone) => zone.is_active && zone.organization.id === organizationId,
  );
  const [zoneId, setZoneId] = useState<number | null>(null);
  const [isSaving, setIsSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const { confirmAction, confirmActionModal } = useConfirmAction();
  const suggestedCode = sourceData ? suggestTransferBoxCode(boxes, sourceData.strain_code) : '';
  const codeExists = boxes.some(
    (box) => box.global_code.toLocaleLowerCase() === globalCode.trim().toLocaleLowerCase(),
  );

  useEffect(() => {
    if (zoneId !== null && availableZones.some((zone) => zone.id === zoneId)) return;
    setZoneId(availableZones[0]?.id ?? null);
  }, [availableZones, zoneId]);

  async function handleFile(event: ChangeEvent<HTMLInputElement>) {
    setError(null);
    setMessage(null);
    setImportedBox(null);
    const file = event.target.files?.[0];
    if (!file) {
      setSourceData(null);
      setGlobalCode('');
      return;
    }
    try {
      const parsed = parseTransferCsv(await file.text());
      if (parsed.format !== 'polypbase.box_transfer.v1') throw new Error('format');
      const missing = REQUIRED_TRANSFER_CSV_FIELDS.filter((field) => !parsed[field]?.trim());
      if (missing.length) throw new Error(`missing:${missing.join(', ')}`);
      setSourceData(parsed);
      setGlobalCode(suggestTransferBoxCode(boxes, parsed.strain_code));
    } catch (parseError) {
      setSourceData(null);
      setGlobalCode('');
      const detail = parseError instanceof Error && parseError.message.startsWith('missing:')
        ? `${t('adminTransferImportMissing')} : ${parseError.message.slice(8)}`
        : t('adminTransferImportInvalid');
      setError(detail);
    }
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!sourceData || organizationId == null || zoneId == null || isSaving || codeExists) return;
    const confirmed = await confirmAction({
      title: t('adminTransferImportConfirmTitle'),
      message: t('adminTransferImportConfirmMessage'),
      confirmLabel: t('adminTransferImportAction'),
      cancelLabel: t('confirmCancel'),
      details: [
        { label: t('confirmDetailBox'), value: globalCode },
        { label: t('adminTransferPolyps'), value: sourceData.transferred_polyp_count },
      ],
    });
    if (!confirmed) return;
    setIsSaving(true);
    setError(null);
    try {
      const box = await apiPost<BoxItem>('/api/box-transfer-imports/', {
        source_data: sourceData,
        organization: organizationId,
        thermal_zone: zoneId,
        global_code: globalCode.trim(),
      });
      setMessage(t('adminTransferImportSuccess'));
      setImportedBox(box);
    } catch (requestError) {
      setError(getErrorMessage(requestError));
    } finally {
      setIsSaving(false);
    }
  }

  return (
    <form className="admin-transfer-form transfer-import-form" onSubmit={handleSubmit}>
      <div className="transfer-import-title-row">
        <h3>{t('adminTransferImportTitle')}</h3>
        <div className="transfer-csv-info-wrap">
          <button type="button" className="transfer-csv-info-button" aria-label={t('adminTransferImportExample')}>
            i
          </button>
          <button
            type="button"
            className="transfer-csv-tooltip"
            onClick={() => downloadTransferCsvExample(profile.interface_language === 'fr')}
          >
            {t('adminTransferCsvTemplate')}
          </button>
        </div>
      </div>
      <label className="admin-wide-field">
        <span>{t('adminTransferImportFile')}</span>
        <input accept=".csv,text/csv" type="file" onChange={(event) => void handleFile(event)} />
      </label>
      {sourceData ? (
        <section className="transfer-import-preview">
          <strong>{t('adminTransferImportPreview')}</strong>
          <dl>
            <div><dt>{t('adminTransferTarget')}</dt><dd>{sourceData.target_organization_name || '-'}</dd></div>
            <div><dt>{t('adminOrganizations')}</dt><dd>{sourceData.source_organization_name}</dd></div>
            <div><dt>{t('species')}</dt><dd>{sourceData.species_scientific_name}</dd></div>
            <div><dt>{t('boxStrain')}</dt><dd>{sourceData.strain_code}</dd></div>
            <div><dt>{t('adminTransferPolyps')}</dt><dd>{sourceData.transferred_polyp_count}</dd></div>
            <div><dt>{t('adminTargetTemperature')}</dt><dd>{sourceData.target_temperature_c || '-'}</dd></div>
            <div><dt>{t('salinityFull')}</dt><dd>{sourceData.latest_salinity_psu || '-'}</dd></div>
            <div><dt>{t('cultureStatus')}</dt><dd>{sourceData.latest_culture_status || '-'}</dd></div>
            <div><dt>{t('parents')}</dt><dd>{sourceData.parent_box_codes || '-'}</dd></div>
            <div><dt>{t('adminTransferNotes')}</dt><dd>{sourceData.transfer_notes || sourceData.latest_notes || '-'}</dd></div>
          </dl>
        </section>
      ) : null}
      <label>
        <span>{t('adminTransferImportOrganization')}</span>
        <select value={organizationId ?? ''} onChange={(event) => setOrganizationId(Number(event.target.value))}>
          {organizations.map((organization) => <option key={organization.id} value={organization.id}>{organization.name}</option>)}
        </select>
      </label>
      <label>
        <span>{t('adminTransferImportZone')}</span>
        <select value={zoneId ?? ''} onChange={(event) => setZoneId(Number(event.target.value))}>
          {availableZones.map((zone) => <option key={zone.id} value={zone.id}>{zone.name}</option>)}
        </select>
      </label>
      <label>
        <span>{t('adminTransferImportCode')}</span>
        <input required value={globalCode} onChange={(event) => setGlobalCode(event.target.value)} />
        <small>{t('adminTransferImportCodeHint')}</small>
        {codeExists ? (
          <span className="inline-error">
            {t('adminTransferImportCodeExists')}{' '}
            <button type="button" onClick={() => setGlobalCode(suggestedCode)}>
              {t('adminTransferImportUseSuggestion')} : {suggestedCode}
            </button>
          </span>
        ) : null}
      </label>
      <button type="submit" disabled={!sourceData || zoneId == null || !globalCode.trim() || codeExists || isSaving}>
        {isSaving ? t('saving') : t('adminTransferImportAction')}
      </button>
      {message ? <p className="inline-success">{message}</p> : null}
      {importedBox ? (
        <a className="admin-transfer-open-box" href={`/boxes/${encodeURIComponent(importedBox.global_code)}`}>
          {t('adminTransferImportOpenBox')} · {importedBox.global_code}
        </a>
      ) : null}
      {error ? <p className="inline-error">{error}</p> : null}
      {confirmActionModal}
    </form>
  );
}

const REQUIRED_TRANSFER_CSV_FIELDS = [
  'format',
  'transfer_id',
  'source_organization_name',
  'source_global_code',
  'species_scientific_name',
  'strain_code',
  'transferred_polyp_count',
];

function suggestTransferBoxCode(boxes: BoxItem[], strainCode: string) {
  const cleanStrainCode = strainCode.trim();
  if (!cleanStrainCode) return '';
  const prefix = `${cleanStrainCode}.`;
  const usedNumbers = boxes
    .filter((box) => box.global_code.startsWith(prefix))
    .map((box) => box.global_code.slice(prefix.length))
    .filter((value) => /^\d+$/.test(value))
    .map(Number);
  const nextNumber = Math.max(0, ...usedNumbers) + 1;
  return `${prefix}${String(nextNumber).padStart(3, '0')}`;
}

function parseTransferCsv(raw: string): TransferCsvRow {
  const rows = parseCsvRows(raw.replace(/^\uFEFF/, ''));
  if (rows.length < 2 || rows[0].length !== rows[1].length) throw new Error('csv');
  return Object.fromEntries(rows[0].map((header, index) => [normalizeTransferCsvHeader(header), rows[1][index] ?? '']));
}

const FRENCH_TRANSFER_HEADER_ALIASES: Record<string, string> = {
  format: 'format',
  'identifiant transfert': 'transfer_id',
  'date transfert': 'transfer_date',
  'id structure expéditrice': 'source_organization_id',
  'structure expéditrice': 'source_organization_name',
  'id structure destinataire': 'target_organization_id',
  'structure destinataire': 'target_organization_name',
  préparateur: 'prepared_by',
  'polypes transférés': 'transferred_polyp_count',
  'notes transfert': 'transfer_notes',
  'id boîte source': 'source_box_id',
  'code boîte source': 'source_global_code',
  'code local source': 'source_local_code',
  'numéro boîte source': 'source_box_number',
  'statut boîte source': 'source_box_status',
  'date entrée boîte source': 'source_box_entered_on',
  'nom scientifique': 'species_scientific_name',
  'nom commun': 'species_common_name',
  'code espèce': 'species_code',
  'code souche': 'strain_code',
  'numéro souche': 'strain_number',
  'code origine souche': 'strain_origin_code',
  'type origine': 'origin_type',
  'institution origine': 'origin_institution',
  'description origine': 'origin_description',
  'boîtes parentes': 'parent_box_codes',
  emplacement: 'thermal_zone_name',
  'température consigne (°c)': 'target_temperature_c',
  'date dernier relevé': 'latest_measurement_date',
  'polypes dernier relevé': 'latest_polyp_count',
  'éphyrules dernier relevé': 'latest_ephyrae_count',
  'état culture': 'latest_culture_status',
  'salinité (psu)': 'latest_salinity_psu',
  'notes dernier relevé': 'latest_notes',
  'lien qr source': 'source_qr_url',
};

function normalizeTransferCsvHeader(header: string) {
  const trimmed = header.trim();
  return FRENCH_TRANSFER_HEADER_ALIASES[trimmed.toLocaleLowerCase('fr-FR')] ?? trimmed;
}

function parseCsvRows(raw: string) {
  const rows: string[][] = [];
  let row: string[] = [];
  let cell = '';
  let quoted = false;
  for (let index = 0; index < raw.length; index += 1) {
    const char = raw[index];
    if (char === '"') {
      if (quoted && raw[index + 1] === '"') {
        cell += '"';
        index += 1;
      } else {
        quoted = !quoted;
      }
    } else if (char === ',' && !quoted) {
      row.push(cell); cell = '';
    } else if ((char === '\n' || char === '\r') && !quoted) {
      if (char === '\r' && raw[index + 1] === '\n') index += 1;
      row.push(cell); cell = '';
      if (row.some((value) => value !== '')) rows.push(row);
      row = [];
    } else {
      cell += char;
    }
  }
  if (cell || row.length) { row.push(cell); rows.push(row); }
  if (quoted) throw new Error('csv');
  return rows;
}

type PreparedTransfer = {
  transfer: BoxTransferResult;
  box: BoxItem;
  targetOrganization: { id: number; name: string };
  exportData: TransferExportData;
};

type TransferExportData = {
  format: string;
  transfer: {
    id: number;
    date: string;
    notes: string;
    polyp_count: number;
    prepared_by: string | null;
    parent_box_codes: string[];
    source_organization: Organization;
    target_organization: { id: number; name: string };
  };
  box: {
    id: number;
    global_code: string;
    local_code: string;
    box_number: string;
    status: string;
    entered_on: string | null;
    species: BoxItem['species'];
    strain: BoxItem['strain'];
    thermal_zone: BoxItem['thermal_zone'];
    latest_measurement: BoxItem['latest_measurement'];
    latest_salinity_psu: string | null;
    origin: BoxTransferResult['origin'];
    qr_scan_url: string;
  };
};

function buildTransferExportData(
  transfer: BoxTransferResult,
  box: BoxItem,
  targetOrganization: { id: number; name: string },
): TransferExportData {
  return {
    format: 'polypbase.box_transfer.v1',
    transfer: {
      id: transfer.id,
      date: transfer.transfer_date,
      notes: transfer.notes,
      polyp_count: transfer.polyp_count,
      prepared_by: transfer.prepared_by,
      parent_box_codes: transfer.parent_box_codes,
      source_organization: box.organization,
      target_organization: targetOrganization,
    },
    box: {
      id: box.id,
      global_code: box.global_code,
      local_code: box.local_code,
      box_number: box.box_number,
      status: box.status,
      entered_on: box.entered_on,
      species: box.species,
      strain: box.strain,
      thermal_zone: box.thermal_zone,
      latest_measurement: box.latest_measurement,
      latest_salinity_psu: box.latest_salinity_psu,
      origin: transfer.origin,
      qr_scan_url: new URL(`/bac/${box.id}/`, window.location.origin).href,
    },
  };
}

function downloadTransferCsv(preparedTransfer: PreparedTransfer, useFrenchHeaders: boolean) {
  const { transfer, box } = preparedTransfer.exportData;
  const columns: Array<[string, string, unknown]> = [
    ['format', 'Format', preparedTransfer.exportData.format],
    ['transfer_id', 'Identifiant transfert', transfer.id],
    ['transfer_date', 'Date transfert', transfer.date],
    ['source_organization_name', 'Structure expéditrice', transfer.source_organization.name],
    ['target_organization_name', 'Structure destinataire', transfer.target_organization.name],
    ['prepared_by', 'Préparateur', transfer.prepared_by],
    ['transferred_polyp_count', 'Polypes transférés', transfer.polyp_count],
    ['transfer_notes', 'Notes transfert', transfer.notes],
    ['source_global_code', 'Code boîte source', box.global_code],
    ['species_scientific_name', 'Nom scientifique', box.species.scientific_name],
    ['species_common_name', 'Nom commun', box.species.common_name],
    ['species_code', 'Code espèce', box.species.genus_species_code],
    ['strain_code', 'Code souche', box.strain.code],
    ['strain_origin_code', 'Code origine souche', box.strain.origin_code],
    ['origin_institution', 'Institution origine', box.origin?.institution],
    ['parent_box_codes', 'Boîtes parentes', transfer.parent_box_codes.join('|')],
    ['target_temperature_c', 'Température consigne (°C)', box.thermal_zone?.target_temperature_c],
    ['latest_culture_status', 'État culture', box.latest_measurement?.culture_status],
    ['latest_salinity_psu', 'Salinité (PSU)', box.latest_salinity_psu],
    ['latest_notes', 'Notes dernier relevé', box.latest_measurement?.notes],
    ['source_qr_url', 'Lien QR source', box.qr_scan_url],
  ];
  const csv = `${columns.map(([technical, french]) => csvCell(useFrenchHeaders ? french : technical)).join(',')}\r\n${columns
    .map(([, , value]) => csvCell(value))
    .join(',')}\r\n`;
  const fileName = `transfert_${sanitizeFilePart(preparedTransfer.box.global_code)}_${preparedTransfer.transfer.transfer_date}.csv`;
  const blob = new Blob([`\uFEFF${csv}`], {
    type: 'text/csv;charset=utf-8',
  });
  const objectUrl = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = objectUrl;
  link.download = fileName;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(objectUrl);
}

function csvCell(value: unknown) {
  if (value == null) return '';
  const text = String(value);
  return /[",\r\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
}

function downloadTransferCsvExample(useFrenchHeaders: boolean) {
  const columns: Array<[string, string, string]> = [
    ['format', 'Format', 'polypbase.box_transfer.v1'],
    ['transfer_id', 'Identifiant transfert', 'EXEMPLE-001'],
    ['transfer_date', 'Date transfert', '2026-07-21'],
    ['source_organization_name', 'Structure expéditrice', 'Aquarium expéditeur'],
    ['target_organization_name', 'Structure destinataire', 'Aquarium destinataire'],
    ['prepared_by', 'Préparateur', 'Nom du préparateur'],
    ['transferred_polyp_count', 'Polypes transférés', '50'],
    ['transfer_notes', 'Notes transfert', 'Conditions ou précautions de transport'],
    ['source_global_code', 'Code boîte source', 'SOUCHE.001'],
    ['species_scientific_name', 'Nom scientifique', 'Aurelia aurita'],
    ['species_common_name', 'Nom commun', 'Aurélie'],
    ['species_code', 'Code espèce', 'AAU'],
    ['strain_code', 'Code souche', '1-ATL'],
    ['strain_origin_code', 'Code origine souche', 'ATL'],
    ['origin_institution', 'Institution origine', 'Institution source'],
    ['parent_box_codes', 'Boîtes parentes', 'SOUCHE.000'],
    ['target_temperature_c', 'Température consigne (°C)', '15'],
    ['latest_culture_status', 'État culture', 'good'],
    ['latest_salinity_psu', 'Salinité (PSU)', '35'],
    ['latest_notes', 'Notes dernier relevé', 'Culture en bon état'],
    ['source_qr_url', 'Lien QR source', 'https://example.org/bac/1/'],
  ];
  const csv = `${columns.map(([technical, french]) => csvCell(useFrenchHeaders ? french : technical)).join(',')}\r\n${columns
    .map(([, , value]) => csvCell(value))
    .join(',')}\r\n`;
  const blob = new Blob([`\uFEFF${csv}`], { type: 'text/csv;charset=utf-8' });
  const objectUrl = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = objectUrl;
  link.download = useFrenchHeaders ? 'exemple_transfert_polypbase.csv' : 'polypbase_transfer_example.csv';
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(objectUrl);
}

function sanitizeFilePart(value: string) {
  return value.trim().replace(/[^a-z0-9._-]+/gi, '_') || 'boite';
}

function AdminAuditLogSection({ t }: { t: TFunction }) {
  const [entries, setEntries] = useState<AdminAuditLogEntry[]>([]);
  const [actionOptions, setActionOptions] = useState<AdminAuditActionOption[]>([]);
  const [actionFilter, setActionFilter] = useState('');
  const [dateFilter, setDateFilter] = useState('');
  const [hasMore, setHasMore] = useState(false);
  const [nextOffset, setNextOffset] = useState<number | null>(null);
  const [expandedEntryId, setExpandedEntryId] = useState<number | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [isLoadingMore, setIsLoadingMore] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let isActive = true;

    async function loadAuditLog() {
      const params = new URLSearchParams({
        limit: String(ADMIN_AUDIT_PAGE_SIZE),
        offset: '0',
        include_options: '1',
      });
      if (actionFilter) params.set('action', actionFilter);
      if (dateFilter) params.set('date', dateFilter);

      try {
        setIsLoading(true);
        setError(null);
        const response = await apiGet<AdminAuditLogResponse>(`/api/accounts/audit-log/?${params.toString()}`);
        if (!isActive) return;
        setEntries(response.results);
        setActionOptions(response.action_options ?? []);
        setHasMore(Boolean(response.has_more));
        setNextOffset(response.next_offset ?? null);
        setExpandedEntryId(null);
      } catch (requestError) {
        if (!isActive) return;
        setError(getErrorMessage(requestError));
      } finally {
        if (isActive) setIsLoading(false);
      }
    }

    loadAuditLog();
    return () => {
      isActive = false;
    };
  }, [actionFilter, dateFilter]);

  async function loadMoreAuditLog() {
    if (isLoadingMore || !hasMore || nextOffset == null) return;

    const params = new URLSearchParams({
      limit: String(ADMIN_AUDIT_PAGE_SIZE),
      offset: String(nextOffset),
    });
    if (actionFilter) params.set('action', actionFilter);
    if (dateFilter) params.set('date', dateFilter);

    try {
      setIsLoadingMore(true);
      setError(null);
      const response = await apiGet<AdminAuditLogResponse>(`/api/accounts/audit-log/?${params.toString()}`);
      setEntries((current) => {
        const knownIds = new Set(current.map((entry) => entry.id));
        const nextEntries = response.results.filter((entry) => !knownIds.has(entry.id));
        return [...current, ...nextEntries];
      });
      setHasMore(Boolean(response.has_more));
      setNextOffset(response.next_offset ?? null);
    } catch (requestError) {
      setError(getErrorMessage(requestError));
    } finally {
      setIsLoadingMore(false);
    }
  }

  const dayGroups = useMemo(() => groupAuditEntriesByDay(entries), [entries]);
  const hasFilters = Boolean(actionFilter || dateFilter);

  return (
    <section className="admin-section admin-audit-section admin-audit-page" id="admin-history">
      <div className="admin-audit-page-heading">
        <div>
          <h2>{t('adminAuditTitle')}</h2>
        </div>
        <strong>
          {entries.length} {t('adminAuditLoaded')}
          {hasMore ? ' +' : ''}
        </strong>
      </div>

      <div className="admin-audit-filters" aria-label={t('adminAuditDialogText')}>
        <label>
          <span>{t('adminAuditFilterAction')}</span>
          <select value={actionFilter} onChange={(event) => setActionFilter(event.target.value)}>
            <option value="">{t('adminAuditAllActions')}</option>
            {actionOptions.map((option) => (
              <option value={option.value} key={option.value}>
                {getFrenchAuditAction({
                  id: 0,
                  created_at: '',
                  organization: null,
                  user: null,
                  action: option.value,
                  action_label: option.label,
                  object_type: '',
                  object_id: '',
                  description: '',
                  metadata: {},
                })}
                {option.count ? ` (${option.count})` : ''}
              </option>
            ))}
          </select>
        </label>
        <label>
          <span>{t('adminAuditFilterDate')}</span>
          <input type="date" value={dateFilter} onChange={(event) => setDateFilter(event.target.value)} />
        </label>
        {hasFilters ? (
          <button
            className="admin-audit-clear"
            type="button"
            onClick={() => {
              setActionFilter('');
              setDateFilter('');
            }}
          >
            {t('adminAuditClearFilters')}
          </button>
        ) : null}
      </div>

      <div className="admin-audit-page-body">
        {isLoading ? (
          <SkeletonRows count={6} />
        ) : error ? (
          <p className="inline-error">{error}</p>
        ) : dayGroups.length ? (
          <div className="admin-audit-timeline">
            {dayGroups.map((group) => (
              <section className="admin-audit-day-group" key={group.key}>
                <h3>{group.label}</h3>
                <div className="admin-audit-table">
                  {group.entries.map((entry) => {
                    const isExpanded = expandedEntryId === entry.id;
                    return (
                      <article className="admin-audit-entry" key={entry.id}>
                        <button
                          className="admin-audit-row"
                          type="button"
                          aria-expanded={isExpanded}
                          onClick={() => setExpandedEntryId(isExpanded ? null : entry.id)}
                        >
                          <span>
                            <strong>{formatAuditDate(entry.created_at)}</strong>
                            <small>{entry.organization ?? '-'}</small>
                          </span>
                          <span>{entry.user ?? '-'}</span>
                          <span>
                            <strong>{getFrenchAuditAction(entry)}</strong>
                            <small>{formatAuditDescription(entry)}</small>
                          </span>
                          <span>
                            <strong>{entry.object_id || '-'}</strong>
                            <small>{formatAuditObjectType(entry.object_type)}</small>
                          </span>
                        </button>
                        {isExpanded ? <AuditLogDetails entry={entry} /> : null}
                      </article>
                    );
                  })}
                </div>
              </section>
            ))}
          </div>
        ) : (
          <p className="muted compact-text">{t('adminAuditEmpty')}</p>
        )}
        {!isLoading && hasMore ? (
          <button className="admin-audit-load-more" type="button" disabled={isLoadingMore} onClick={loadMoreAuditLog}>
            {isLoadingMore ? t('loading') : t('adminAuditLoadMore')}
          </button>
        ) : null}
      </div>
    </section>
  );
}

function AuditLogDetails({ entry }: { entry: AdminAuditLogEntry }) {
  const metadataEntries = Object.entries(entry.metadata ?? {});
  const values = filterAuditDisplayRecord(getMetadataRecord(entry.metadata?.valeurs));
  const changes = filterAuditDisplayRecord(getMetadataRecord(entry.metadata?.modifications));
  const remainingMetadataEntries = metadataEntries.filter(([key]) => !['valeurs', 'modifications'].includes(key));

  return (
    <div className="admin-audit-details">
      <div>
        <small>Date</small>
        <strong>{formatAuditDate(entry.created_at)}</strong>
      </div>
      <div>
        <small>Structure</small>
        <strong>{entry.organization ?? '-'}</strong>
      </div>
      <div>
        <small>Utilisateur</small>
        <strong>{entry.user ?? '-'}</strong>
      </div>
      <div>
        <small>Action</small>
        <strong>{getFrenchAuditAction(entry)}</strong>
      </div>
      <div>
        <small>Objet</small>
        <strong>{entry.object_id || '-'}</strong>
      </div>
      <div>
        <small>Type</small>
        <strong>{formatAuditObjectType(entry.object_type)}</strong>
      </div>
      <div className="admin-audit-detail-wide">
        <small>Description</small>
        <strong>{formatAuditDescription(entry)}</strong>
      </div>
      {values ? (
        <div className="admin-audit-detail-wide">
          <small>Valeurs enregistrées</small>
          <dl>
            {Object.entries(values).map(([key, value]) => (
              <div key={key}>
                <dt>{formatAuditMetadataKey(key)}</dt>
                <dd>{formatAuditMetadataValue(value)}</dd>
              </div>
            ))}
          </dl>
        </div>
      ) : null}
      {changes ? (
        <div className="admin-audit-detail-wide">
          <small>Changements</small>
          <dl>
            {Object.entries(changes).map(([key, value]) => (
              <div key={key}>
                <dt>{formatAuditMetadataKey(key)}</dt>
                <dd>{formatAuditChange(value)}</dd>
              </div>
            ))}
          </dl>
        </div>
      ) : null}
      {remainingMetadataEntries.length ? (
        <div className="admin-audit-detail-wide">
          <small>Détails techniques</small>
          <dl>
            {remainingMetadataEntries.map(([key, value]) => (
              <div key={key}>
                <dt>{formatAuditMetadataKey(key)}</dt>
                <dd>{formatAuditMetadataValue(value)}</dd>
              </div>
            ))}
          </dl>
        </div>
      ) : null}
    </div>
  );
}

function getMetadataRecord(value: unknown): Record<string, unknown> | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
  return value as Record<string, unknown>;
}

const hiddenAuditDisplayKeys = new Set(['strobiles', 'statut_culture', 'a_verifier']);

function filterAuditDisplayRecord(record: Record<string, unknown> | null) {
  if (!record) return null;
  return Object.fromEntries(
    Object.entries(record).filter(([key]) => !hiddenAuditDisplayKeys.has(key)),
  );
}

function formatAuditChange(value: unknown) {
  const change = getMetadataRecord(value);
  if (!change || !('avant' in change) || !('apres' in change)) {
    return formatAuditMetadataValue(value);
  }
  return `${formatAuditMetadataValue(change.avant)} -> ${formatAuditMetadataValue(change.apres)}`;
}

function formatAuditDescription(entry: AdminAuditLogEntry) {
  const description = entry.description || '';
  const metadata = entry.metadata ?? {};

  if (description.startsWith('Biological measurement edited for ')) {
    return `Relevé biologique modifié pour le ${formatTechnicalDate(description.replace('Biological measurement edited for ', ''))}`;
  }
  if (description.startsWith('Biological measurement for ')) {
    return `Relevé biologique enregistré pour le ${formatTechnicalDate(description.replace('Biological measurement for ', ''))}`;
  }
  if (description.startsWith('Box opened: ')) {
    return `Fiche boîte ouverte : ${description.replace('Box opened: ', '')}`;
  }
  if (description.startsWith('QR scan of ')) {
    return `QR code scanné : ${description.replace('QR scan of ', '')}`;
  }
  if (description.startsWith('Box archived: ')) {
    return `Boîte mise inactive : ${description.replace('Box archived: ', '')}`;
  }
  if (description.startsWith('Box activated: ')) {
    return `Boîte remise active : ${description.replace('Box activated: ', '')}`;
  }
  if (description.startsWith('Box created manually: ')) {
    return `Boîte créée manuellement : ${description.replace('Box created manually: ', '')}`;
  }
  if (description.startsWith('Box moved to ')) {
    return `Boîte déplacée vers ${description.replace('Box moved to ', '')}`;
  }
  if (description.startsWith('Subculture created from ')) {
    return `Repiquage créé depuis ${description.replace('Subculture created from ', '')}`;
  }
  if (description.startsWith('Manual temperature recorded: ')) {
    return `Température manuelle enregistrée : ${description.replace('Manual temperature recorded: ', '')}`;
  }
  if (description.startsWith('Thermal zone created: ')) {
    return `Emplacement créé : ${description.replace('Thermal zone created: ', '')}`;
  }
  if (description.startsWith('Thermal zone updated: ')) {
    return `Emplacement modifié : ${description.replace('Thermal zone updated: ', '')}`;
  }
  if (description.startsWith('Probe created: ')) {
    return `Sonde ajoutée : ${description.replace('Probe created: ', '')}`;
  }
  if (description.startsWith('Box transfer prepared: ')) {
    return `Transfert préparé : ${description.replace('Box transfer prepared: ', '')}`;
  }
  if (description === 'Member access created') {
    return 'Accès utilisateur créé';
  }
  if (description === 'Member access restored') {
    return 'Accès utilisateur réactivé';
  }
  if (description === 'Member access updated') {
    return 'Accès utilisateur modifié';
  }
  if (description === 'Weekly biological measurement CSV export') {
    return 'Export CSV hebdomadaire des relevés biologiques';
  }
  if (description === 'Organization created') {
    return 'Structure créée';
  }
  if (description === 'Organization updated') {
    return 'Structure modifiée';
  }
  if (description === 'Organization deleted') {
    return 'Structure supprimée';
  }

  if (metadata && typeof metadata === 'object' && 'source' in metadata && metadata.source === 'web_app') {
    return 'Action effectuée depuis l’application';
  }
  return description || '-';
}

function formatAuditObjectType(value: string) {
  const labels: Record<string, string> = {
    box: 'Boîte',
    measurement: 'Relevé',
    measurements: 'Relevés',
    thermal_zone: 'Emplacement',
    organization: 'Structure',
    user: 'Utilisateur',
    account: 'Compte',
    probe: 'Sonde',
  };
  return labels[value] ?? (value || '-');
}

function formatAuditMetadataKey(key: string) {
  const labels: Record<string, string> = {
    acces_actif: 'Accès actif',
    a_verifier: 'A vérifier',
    ancienne_zone: 'Ancienne zone',
    apres: 'Après',
    avant: 'Avant',
    box_id: 'Identifiant boîte',
    box_count: 'Nombre de boîtes',
    capacite: 'Capacité',
    child_global_codes: 'Boîtes créées',
    child_box_ids: 'Identifiants des boîtes créées',
    code: 'Code',
    code_global: 'Code global',
    date: 'Date',
    date_deplacement: 'Date du déplacement',
    date_entree: 'Date d’entrée',
    email: 'Email',
    email_contact: 'Email contact',
    ephyrules: 'Éphyrules',
    espece: 'Espèce',
    emplacement: 'Emplacement',
    file_name: 'Fichier',
    filters: 'Filtres',
    from_thermal_zone_name: 'Ancienne zone',
    initial_polyp_counts: 'Polypes initiaux',
    measurement_count: 'Nombre de relevés',
    measurement_id: 'Identifiant relevé',
    membership_id: 'Identifiant accès',
    movement_id: 'Identifiant déplacement',
    nom: 'Nom',
    nouvelle_zone: 'Nouvelle zone',
    numero_boite: 'Numéro de boîte',
    note: 'Note',
    notes: 'Notes',
    pays: 'Pays',
    position: 'Position',
    probe_id: 'Identifiant sonde',
    polypes: 'Polypes',
    raison_arret: 'Raison d’arrêt',
    role: 'Rôle',
    salinite_psu: 'Salinité (PSU)',
    source: 'Source',
    souche: 'Souche',
    statut: 'Statut',
    structure: 'Structure',
    statut_culture: 'Statut culture',
    strobiles: 'Strobiles',
    subculture_event_id: 'Identifiant repiquage',
    temperature_consigne: 'Température consigne',
    temperature_c: 'Température mesurée',
    thermal_zone_id: 'Identifiant emplacement',
    to_organization: 'Structure destinataire',
    to_thermal_zone_name: 'Nouvelle zone',
    transfer_id: 'Identifiant transfert',
    type: 'Type',
    user_id: 'Identifiant utilisateur',
    identifiant: 'Identifiant',
    ville: 'Ville',
    volume_litres: 'Volume (L)',
    week_count: 'Nombre de semaines',
  };
  return labels[key] ?? key.replace(/_/g, ' ');
}

function formatAuditMetadataValue(value: unknown) {
  if (value === null || value === undefined || value === '') return '-';
  if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') {
    return translateAuditValue(value);
  }
  return JSON.stringify(value);
}

function translateAuditValue(value: string | number | boolean) {
  if (typeof value === 'boolean') return value ? 'Oui' : 'Non';
  if (typeof value === 'number') return String(value);

  const labels: Record<string, string> = {
    active: 'Active',
    archived: 'Inactive',
    stopped: 'Arrêtée',
    lost: 'Perdue',
    not_specified: 'Non précisé',
    good: 'Bon',
    medium: 'Moyen',
    bad: 'Mauvais',
    dead: 'Mort',
    web_app: 'Application web',
    csv: 'CSV',
    measurements: 'Relevés',
    box: 'Boîte',
    admin: 'Administrateur',
    lab_technician: 'Technicien',
    viewer: 'Lecteur',
    cabinet: 'Armoire',
    incubator: 'Étuve',
    manual: 'Saisie manuelle',
    other: 'Autre',
  };
  return labels[value] ?? formatTechnicalDate(value);
}

function formatTechnicalDate(value: string) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) return value;
  const [year, month, day] = value.split('-');
  return `${day}/${month}/${year}`;
}

function getFrenchAuditAction(entry: AdminAuditLogEntry) {
  switch (entry.action) {
    case 'entry':
      return 'Nouvelle donnée enregistrée';
    case 'update':
      return 'Modification enregistrée';
    case 'creation':
      return 'Création enregistrée';
    case 'archive':
      return 'Archivage enregistré';
    case 'subculture':
      return 'Repiquage enregistré';
    case 'transfer':
      return 'Transfert préparé';
    case 'import':
      return 'Import enregistré';
    case 'export':
      return 'Export effectué';
    case 'login':
      return 'Connexion';
    default:
      return entry.action_label || entry.action || '-';
  }
}

function formatAuditDate(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat('fr-FR', {
    day: '2-digit',
    month: '2-digit',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  }).format(date);
}

function formatAuditDay(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat('fr-FR', {
    weekday: 'long',
    day: 'numeric',
    month: 'long',
    year: 'numeric',
  }).format(date);
}

function getAuditDayKey(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, '0');
  const day = String(date.getDate()).padStart(2, '0');
  return `${year}-${month}-${day}`;
}

function groupAuditEntriesByDay(entries: AdminAuditLogEntry[]) {
  const groups: Array<{ key: string; label: string; entries: AdminAuditLogEntry[] }> = [];
  entries.forEach((entry) => {
    const key = getAuditDayKey(entry.created_at);
    const currentGroup = groups.find((group) => group.key === key);
    if (currentGroup) {
      currentGroup.entries.push(entry);
      return;
    }
    groups.push({
      key,
      label: formatAuditDay(entry.created_at),
      entries: [entry],
    });
  });
  return groups;
}

function AdminFlowNav({
  activeSection,
  onSelect,
  t,
}: {
  activeSection: AdminFlowKey;
  onSelect: (section: AdminFlowKey) => void;
  t: TFunction;
}) {
  return (
    <nav className="admin-flow-nav" aria-label={t('adminFlowLabel')}>
      {ADMIN_FLOW_ITEMS.map((item) => (
        <span className="admin-flow-step" key={item.key}>
          <button
            type="button"
            className={activeSection === item.key ? 'is-active' : ''}
            aria-controls={item.panelId}
            aria-current={activeSection === item.key ? 'page' : undefined}
            onClick={() => onSelect(item.key)}
          >
            {t(item.label)}
          </button>
        </span>
      ))}
    </nav>
  );
}

type EnvironmentAction = 'zone' | 'probe';

function EnvironmentAdminSection({
  profile,
  zones,
  onCreateZone,
  onCreateProbe,
  onUpdateZone,
  t,
}: {
  profile: UserProfile;
  zones: ThermalZone[];
  onCreateZone: (payload: ThermalZonePayload) => Promise<void>;
  onCreateProbe: (payload: ProbePayload) => Promise<void>;
  onUpdateZone: (zoneId: number, payload: ThermalZonePayload) => Promise<void>;
  t: TFunction;
}) {
  const [activeAction, setActiveAction] = useState<EnvironmentAction>('zone');
  const adminOrgIds = useMemo(() => getAdminOrganizationIds(profile), [profile]);
  const adminEditableZones = useMemo(
    () =>
      zones
        .filter((zone) => zone.is_active && (adminOrgIds === null || adminOrgIds.has(zone.organization.id)))
        .sort(sortAdminZones),
    [adminOrgIds, zones],
  );
  const adminProbeCount = adminEditableZones.reduce(
    (total, zone) => total + (zone.probes?.length ?? 0),
    0,
  );
  const activeBoxCount = adminEditableZones.reduce((total, zone) => total + zone.box_count, 0);
  const capacityCount = adminEditableZones.reduce((total, zone) => total + (zone.capacity ?? 0), 0);

  return (
    <section className="admin-section admin-environment-section" id="admin-environment">
      <div className="admin-environment-topbar">
        <div>
          <h2>{t('adminZonesProbesTitle')}</h2>
        </div>
        <div className="admin-environment-summary" aria-label={t('adminZonesProbesTitle')}>
          <span>
            <strong>{adminEditableZones.length}</strong>
            {t('adminEnvironmentZonesCount')}
          </span>
          <span>
            <strong>{adminProbeCount}</strong>
            {t('adminEnvironmentProbesCount')}
          </span>
          <span>
            <strong>{capacityCount ? `${activeBoxCount} / ${capacityCount}` : activeBoxCount}</strong>
            {t('adminEnvironmentCapacityCount')}
          </span>
        </div>
      </div>

      <div className="admin-environment-workspace">
        <aside className="environment-action-panel">
          <div className="environment-action-tabs" role="tablist" aria-label={t('adminZonesProbesTitle')}>
            <button
              type="button"
              className={activeAction === 'zone' ? 'is-active' : ''}
              aria-selected={activeAction === 'zone'}
              onClick={() => setActiveAction('zone')}
            >
              {t('adminEnvironmentZonePanel')}
            </button>
            <button
              type="button"
              className={activeAction === 'probe' ? 'is-active' : ''}
              aria-selected={activeAction === 'probe'}
              onClick={() => setActiveAction('probe')}
            >
              {t('adminEnvironmentProbePanel')}
            </button>
          </div>

          <div className="environment-action-body">
            {activeAction === 'zone' ? (
              <ZoneCreateForm profile={profile} onCreateZone={onCreateZone} t={t} />
            ) : (
              <ProbeCreateForm profile={profile} zones={zones} onCreateProbe={onCreateProbe} t={t} />
            )}
          </div>
        </aside>

        <section className="environment-settings-panel" aria-labelledby="environment-settings-title">
          <div className="environment-settings-heading">
            <h3 id="environment-settings-title">{t('adminEnvironmentSettingsPanel')}</h3>
          </div>
          <ZoneCapacityManager profile={profile} zones={zones} onUpdateZone={onUpdateZone} t={t} />
        </section>
      </div>
    </section>
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
  t: TFunction;
}) {
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
      </div>

      <div className="admin-organizations-workspace">
        <aside className="organization-create-panel">
          <h3>{t('adminAddOrganization')}</h3>
          <OrganizationCreateForm
            organizations={organizations}
            profile={profile}
            onCreateOrganization={onCreateOrganization}
            t={t}
          />
        </aside>

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

function TransfersAdminSection({
  profile,
  boxes,
  organizations,
  onCreateTransfer,
  zones,
  t,
}: {
  profile: UserProfile;
  boxes: BoxItem[];
  organizations: Array<{ id: number; name: string }>;
  onCreateTransfer: (payload: BoxTransferPayload) => Promise<BoxTransferResult>;
  zones: ThermalZone[];
  t: TFunction;
}) {
  const adminOrgIds = getAdminOrganizationIds(profile);
  const transferableBoxCount = boxes.filter(
    (box) =>
      box.status === 'active' &&
      (adminOrgIds === null || adminOrgIds.has(box.organization.id)),
  ).length;
  const activeZoneCount = zones.filter(
    (zone) =>
      zone.is_active &&
      (adminOrgIds === null || adminOrgIds.has(zone.organization.id)),
  ).length;

  return (
    <section className="admin-section admin-transfer-section transfer-admin-section" id="admin-transfers">
      <div className="transfer-admin-hero">
        <div>
          <h2>{t('adminTransferTitle')}</h2>
        </div>
        <div className="transfer-admin-metrics" aria-label={t('adminTransferTitle')}>
          <span>
            <strong>{transferableBoxCount}</strong>
            {t('adminTransferBoxesAvailable')}
          </span>
          <span>
            <strong>{organizations.length}</strong>
            {t('adminTransferKnownInstitutions')}
          </span>
          <span>
            <strong>{activeZoneCount}</strong>
            {t('adminTransferActiveZones')}
          </span>
        </div>
      </div>

      <div className="transfer-admin-workspace">
        <section className="transfer-work-card transfer-work-card-outgoing">
          <header className="transfer-work-card-head">
            <h3>{t('adminTransferOutgoingTitle')}</h3>
          </header>
          <TransferCreateForm
            profile={profile}
            boxes={boxes}
            organizations={organizations}
            onCreateTransfer={onCreateTransfer}
            t={t}
          />
        </section>

        <section className="transfer-work-card transfer-work-card-incoming">
          <header className="transfer-work-card-head">
            <h3>{t('adminTransferIncomingTitle')}</h3>
          </header>
          <TransferImportForm profile={profile} zones={zones} boxes={boxes} t={t} />
        </section>
      </div>
    </section>
  );
}

export default function AdminView({
  boxes,
  exportOptions,
  isLoading,
  profile,
  onCreateZone,
  onUpdateZone,
  onCreateProbe,
  onCreateOrganization,
  onDeleteOrganization,
  onUpdateOrganization,
  onCreateTransfer,
  t,
  zones,
}: {
  boxes: BoxItem[];
  exportOptions: ExportOptions | null;
  isLoading: boolean;
  profile: UserProfile | null;
  onCreateZone: (payload: ThermalZonePayload) => Promise<void>;
  onUpdateZone: (zoneId: number, payload: ThermalZonePayload) => Promise<void>;
  onCreateProbe: (payload: ProbePayload) => Promise<void>;
  onCreateOrganization: (payload: OrganizationPayload) => Promise<void>;
  onDeleteOrganization: (organizationId: number) => Promise<void>;
  onUpdateOrganization: (organizationId: number, payload: OrganizationPayload) => Promise<void>;
  onCreateTransfer: (payload: BoxTransferPayload) => Promise<BoxTransferResult>;
  t: TFunction;
  zones: ThermalZone[];
}) {
  const [activeAdminSection, setActiveAdminSection] = useState<AdminFlowKey>('accounts');

  if (isLoading) {
    return <PageLoader variant="admin" label={t('adminTitle')} />;
  }

  if (!profile || !userHasAdminRole(profile)) return null;

  const organizations = exportOptions?.organizations ?? profile.organizations;

  return (
    <section className="admin-panel">
      <AdminFlowNav
        activeSection={activeAdminSection}
        onSelect={setActiveAdminSection}
        t={t}
      />

      <div className="admin-flow-panel">
        {activeAdminSection === 'accounts' ? <AccountManagementSection t={t} /> : null}

        {activeAdminSection === 'environment' ? (
          <EnvironmentAdminSection
            profile={profile}
            zones={zones}
            onCreateZone={onCreateZone}
            onCreateProbe={onCreateProbe}
            onUpdateZone={onUpdateZone}
            t={t}
          />
        ) : null}

        {activeAdminSection === 'organizations' ? (
          <OrganizationsAdminSection
            organizations={organizations}
            profile={profile}
            onCreateOrganization={onCreateOrganization}
            onDeleteOrganization={onDeleteOrganization}
            onUpdateOrganization={onUpdateOrganization}
            t={t}
          />
        ) : null}

        {activeAdminSection === 'transfers' ? (
          <TransfersAdminSection
            profile={profile}
            boxes={boxes}
            organizations={organizations}
            onCreateTransfer={onCreateTransfer}
            zones={zones}
            t={t}
          />
        ) : null}

        {activeAdminSection === 'history' ? <AdminAuditLogSection t={t} /> : null}
      </div>
    </section>
  );
}
