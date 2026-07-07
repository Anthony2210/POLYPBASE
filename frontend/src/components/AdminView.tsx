import { type FormEvent, useEffect, useMemo, useState } from 'react';

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
  OrganizationPayload,
  ProbePayload,
  ThermalZonePayload,
} from '../types/admin';
import { formatDisplayDate } from '../utils/dateFormat';
import { getErrorMessage } from '../utils/errors';
import PageLoader from './PageLoader';
import SkeletonRows from './SkeletonRows';

type TFunction = (key: string) => string;

function userHasAdminRole(profile: UserProfile | null) {
  if (!profile) return false;
  if (profile.is_superuser) return true;
  return profile.memberships.some((membership) => membership.role === 'admin');
}

const emptyMemberForm = {
  username: '',
  first_name: '',
  last_name: '',
  email: '',
};

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

  return (
    <section className="admin-section account-management">
      <div className="admin-section-heading account-management-heading">
        <div>
          <h2>{t('manageAccountsTitle')}</h2>
          <p>{t('manageAccountsSubtitle')}</p>
        </div>
        <span className="account-count">{data.members.length}</span>
      </div>

      <div className="account-overview">
        <article>
          <strong>{activeMemberCount}</strong>
          <span>{t('manageActiveAccounts')}</span>
        </article>
        <article>
          <strong>{adminMemberCount}</strong>
          <span>{t('manageAdminAccounts')}</span>
        </article>
        <article>
          <strong>{technicianMemberCount}</strong>
          <span>{t('manageTechnicianAccounts')}</span>
        </article>
        <article>
          <strong>{viewerMemberCount}</strong>
          <span>{t('manageViewerAccounts')}</span>
        </article>
      </div>

      <form className="member-add-form" onSubmit={handleAddMember}>
        <div className="member-add-header">
          <p className="member-add-title">{t('manageAddTitle')}</p>
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
            />
          </label>
          <label>
            {t('manageFieldLastName')}
            <input
              value={form.last_name}
              onChange={(event) => setForm((current) => ({ ...current, last_name: event.target.value }))}
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

      {data.members.length ? (
        <div className="member-table">
          <div className="member-table-head">
            <span>{t('manageColUser')}</span>
            <span>{t('manageColOrganization')}</span>
            <span>{t('manageColRole')}</span>
            <span>{t('manageColLastLogin')}</span>
            <span>{t('manageColStatus')}</span>
          </div>
          {data.members.map((member) => (
            <div
              key={member.membership_id}
              className={member.is_active ? 'member-table-row' : 'member-table-row is-inactive'}
            >
              <span className="member-identity">
                <strong>{member.full_name}</strong>
                <small>
                  @{member.username}
                  {member.email ? ` · ${member.email}` : ''}
                </small>
              </span>
              <span>{member.organization.name}</span>
              <span>
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
              </span>
              <span className="member-last-login">
                {member.last_login ? formatDisplayDate(member.last_login) : t('manageNeverConnected')}
              </span>
              <span className="member-status">
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
                <span className={member.is_active ? 'member-state is-on' : 'member-state is-off'}>
                  {member.is_active ? t('manageStatusActive') : t('manageStatusInactive')}
                </span>
              </span>
            </div>
          ))}
        </div>
      ) : (
        <p className="muted compact-text">{t('manageNoMembers')}</p>
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
      });
      setName('');
      setTargetTemperature('');
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
  const [location, setLocation] = useState('');
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
        location: location.trim(),
      });
      setCode('');
      setLocation('');
      setMessage(t('adminProbeCreated'));
    } catch (requestError) {
      setError(getErrorMessage(requestError));
    } finally {
      setIsSaving(false);
    }
  }

  return (
    <form className="admin-form" onSubmit={handleSubmit}>
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
      <label>
        <span>{t('adminProbeLocation')}</span>
        <input
          type="text"
          value={location}
          onChange={(event) => setLocation(event.target.value)}
        />
      </label>
      <button type="submit" disabled={isSaving || !code.trim()}>
        {isSaving ? t('saving') : t('adminAddProbe')}
      </button>
      {message ? <p className="inline-success">{message}</p> : null}
      {error ? <p className="inline-error">{error}</p> : null}
    </form>
  );
}

function OrganizationCreateForm({
  profile,
  onCreateOrganization,
  t,
}: {
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

  if (!profile.is_superuser) {
    return <p className="muted compact-text">{t('adminSuperuserOnly')}</p>;
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (isSaving) return;

    setIsSaving(true);
    setError(null);
    setMessage(null);

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
        country: country.trim(),
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
        <input type="text" value={country} onChange={(event) => setCountry(event.target.value)} />
      </label>
      <label>
        <span>{t('adminCity')}</span>
        <input type="text" value={city} onChange={(event) => setCity(event.target.value)} />
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
        <input type="tel" value={contactPhone} onChange={(event) => setContactPhone(event.target.value)} />
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
  onCreateTransfer: (payload: BoxTransferPayload) => Promise<void>;
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
  const [targetOrgId, setTargetOrgId] = useState<number | null>(null);
  const [notes, setNotes] = useState('');
  const [isSaving, setIsSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  const selectedBox = transferableBoxes.find((box) => box.id === boxId) ?? null;
  // The target cannot be the box's current organization.
  const targetOrganizations = organizations.filter(
    (organization) => organization.id !== selectedBox?.organization.id,
  );

  if (!transferableBoxes.length) {
    return <p className="muted compact-text">{t('adminTransferNoBox')}</p>;
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (isSaving || boxId == null || targetOrgId == null) return;

    setIsSaving(true);
    setError(null);
    setMessage(null);

    try {
      await onCreateTransfer({ box: boxId, to_organization: targetOrgId, notes: notes.trim() });
      setNotes('');
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
      <label>
        <span>{t('adminTransferBox')}</span>
        <select value={boxId ?? ''} onChange={(event) => setBoxId(Number(event.target.value))}>
          {transferableBoxes.map((box) => (
            <option key={box.id} value={box.id}>{box.global_code}</option>
          ))}
        </select>
      </label>
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
      <label className="admin-wide-field">
        <span>{t('adminTransferNotes')}</span>
        <textarea rows={2} value={notes} onChange={(event) => setNotes(event.target.value)} />
      </label>
      <button type="submit" disabled={isSaving || targetOrgId == null}>
        {isSaving ? t('saving') : t('adminPrepareTransfer')}
      </button>
      {message ? <p className="inline-success">{message}</p> : null}
      {error ? <p className="inline-error">{error}</p> : null}
    </form>
  );
}

export default function AdminView({
  boxes,
  exportOptions,
  isLoading,
  profile,
  onCreateZone,
  onCreateProbe,
  onCreateOrganization,
  onCreateTransfer,
  t,
  zones,
}: {
  boxes: BoxItem[];
  exportOptions: ExportOptions | null;
  isLoading: boolean;
  profile: UserProfile | null;
  onCreateZone: (payload: ThermalZonePayload) => Promise<void>;
  onCreateProbe: (payload: ProbePayload) => Promise<void>;
  onCreateOrganization: (payload: OrganizationPayload) => Promise<void>;
  onCreateTransfer: (payload: BoxTransferPayload) => Promise<void>;
  t: TFunction;
  zones: ThermalZone[];
}) {
  if (isLoading) {
    return <PageLoader variant="admin" label={t('adminTitle')} />;
  }

  if (!profile || !userHasAdminRole(profile)) return null;

  const organizations = exportOptions?.organizations ?? profile.organizations;

  return (
    <section className="admin-panel">
      <AccountManagementSection t={t} />

      <div className="admin-two-columns">
        <section className="admin-section">
          <div className="admin-section-heading">
            <div>
              <h2>{t('adminZonesProbesTitle')}</h2>
              <p>{t('adminZonesProbesText')}</p>
            </div>
          </div>

          <div className="admin-form-grid">
            <ZoneCreateForm profile={profile} onCreateZone={onCreateZone} t={t} />
            <ProbeCreateForm profile={profile} zones={zones} onCreateProbe={onCreateProbe} t={t} />
          </div>
        </section>

        <section className="admin-section">
          <div className="admin-section-heading">
            <div>
              <h2>{t('adminOrganizationsTitle')}</h2>
              <p>{t('adminOrganizationsText')}</p>
            </div>
          </div>

          <OrganizationCreateForm
            profile={profile}
            onCreateOrganization={onCreateOrganization}
            t={t}
          />

          <div className="admin-inline-list">
            <strong>{t('adminExistingOrganizations')}</strong>
            <div>
              {organizations.slice(0, 4).map((organization) => (
                <span key={organization.id}>{organization.name}</span>
              ))}
            </div>
          </div>
        </section>
      </div>

      <section className="admin-section admin-transfer-section">
        <div className="admin-section-heading">
          <div>
            <h2>{t('adminTransferTitle')}</h2>
            <p>{t('adminTransferText')}</p>
          </div>
        </div>

        <TransferCreateForm
          profile={profile}
          boxes={boxes}
          organizations={organizations}
          onCreateTransfer={onCreateTransfer}
          t={t}
        />
      </section>
    </section>
  );
}

