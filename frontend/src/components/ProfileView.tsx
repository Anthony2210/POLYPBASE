import { useMemo, useState } from 'react';

import type { BoxItem, UserProfile } from '../types';
import { getErrorMessage } from '../utils/errors';
import { buildQrLabelItem, type QrLabelItem } from '../utils/qrLabels';
import PageLoader from './PageLoader';

type ProfileLabels = {
  account: string;
  logoutAction: string;
  logoutError: string;
  profileEmail: string;
  profileLanguage: string;
  profileMemberships: string;
  profileNoEmail: string;
  profileNoMembership: string;
  profileSuperuserAllOrganizations: string;
  profilePreferences: string;
  noZone: string;
  qrLabelAddToSelection: string;
  qrLabelAlreadySelected: string;
  qrLabelClearSelection: string;
  qrLabelPrintSelection: string;
  qrLabelSelectionCount: string;
  qrLabelSelectionEmpty: string;
  qrLabelSelectionHelp: string;
  qrLabelSelectionSearch: string;
  qrLabelSelectionTitle: string;
  qrLabelSearchPlaceholder: string;
  roleDescAdmin: string;
  roleDescTechnician: string;
  roleDescViewer: string;
  saving: string;
};

export default function ProfileView({
  boxes,
  isLoading,
  labels,
  onAddQrLabel,
  onClearQrLabelSelection,
  onLogout,
  onPrintQrLabelSelection,
  onRemoveQrLabel,
  onUpdateLanguage,
  profile,
  qrLabelSelection,
}: {
  boxes: BoxItem[];
  isLoading: boolean;
  labels: ProfileLabels;
  onAddQrLabel: (label: QrLabelItem) => void;
  onClearQrLabelSelection: () => void;
  onLogout: () => Promise<void>;
  onPrintQrLabelSelection: () => void;
  onRemoveQrLabel: (labelId: number) => void;
  onUpdateLanguage: (language: string) => Promise<void>;
  profile: UserProfile | null;
  qrLabelSelection: QrLabelItem[];
}) {
  const [isSaving, setIsSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [isLoggingOut, setIsLoggingOut] = useState(false);
  const [logoutError, setLogoutError] = useState<string | null>(null);
  const [labelSearch, setLabelSearch] = useState('');

  async function handleLanguage(language: string) {
    setIsSaving(true);
    setSaveError(null);
    try {
      await onUpdateLanguage(language);
    } catch (requestError) {
      setSaveError(getErrorMessage(requestError));
    } finally {
      setIsSaving(false);
    }
  }

  async function handleLogout() {
    if (isLoggingOut) return;

    setIsLoggingOut(true);
    setLogoutError(null);
    try {
      await onLogout();
    } catch {
      setLogoutError(labels.logoutError);
      setIsLoggingOut(false);
    }
  }

  const canManageQrLabels = profile ? userCanManageQrLabels(profile) : false;
  const labelOrganizationIds = useMemo(
    () => (profile ? getQrLabelOrganizationIds(profile) : new Set<number>()),
    [profile],
  );
  const normalizedLabelSearch = labelSearch.trim().toLocaleLowerCase();
  const labelBoxes = useMemo(() => {
    if (!profile || !canManageQrLabels) return [];

    return boxes.filter((box) => {
      if (labelOrganizationIds && !labelOrganizationIds.has(box.organization.id)) return false;
      if (!normalizedLabelSearch) return true;

      return [
        box.global_code,
        box.local_code,
        box.species.scientific_name,
        box.strain.code,
      ]
        .filter(Boolean)
        .some((value) => value!.toLocaleLowerCase().includes(normalizedLabelSearch));
    });
  }, [boxes, canManageQrLabels, labelOrganizationIds, normalizedLabelSearch, profile]);

  if (isLoading) {
    return (
      <PageLoader variant="profile" label={labels.account} />
    );
  }

  if (!profile) return null;

  const fullName = [profile.first_name, profile.last_name].filter(Boolean).join(' ') || profile.username;
  const initials = getProfileInitials(profile);

  function toggleQrLabel(box: BoxItem) {
    const label = buildQrLabelItem(box);
    if (qrLabelSelection.some((item) => item.id === label.id)) {
      onRemoveQrLabel(label.id);
      return;
    }
    onAddQrLabel(label);
  }

  return (
    <section className="profile-page">
      <header className="profile-identity-card">
        <div className="profile-avatar" aria-hidden="true">
          {initials}
        </div>
        <div className="profile-identity-main">
          <p className="eyebrow">{labels.account}</p>
          <h2>{fullName}</h2>
          <p className="profile-username">@{profile.username}</p>
          <div className="profile-identity-meta">
            <span className="profile-meta-item">
              <small>{labels.profileEmail}</small>
              {profile.email || labels.profileNoEmail}
            </span>
          </div>
        </div>
      </header>

      <section className="profile-block">
        <div className="section-title">
          <h2>{labels.profileMemberships}</h2>
          {/* Superusers bypass memberships: show how many organizations they reach. */}
          <span>
            {profile.memberships.length || (profile.is_superuser ? profile.organizations.length : 0)}
          </span>
        </div>

        {profile.memberships.length ? (
          <div className="profile-membership-list">
            {profile.memberships.map((membership) => (
              <article
                key={`${membership.organization.id}-${membership.role}`}
                className="profile-membership-card"
              >
                <div className="profile-membership-head">
                  <strong>{membership.organization.name}</strong>
                  <span className={`profile-role-tag is-${membership.role}`}>
                    {membership.role_label}
                  </span>
                </div>
                <p>{getRoleDescription(membership.role, labels)}</p>
              </article>
            ))}
          </div>
        ) : profile.is_superuser ? (
          <p className="muted compact-text">{labels.profileSuperuserAllOrganizations}</p>
        ) : (
          <p className="muted compact-text">{labels.profileNoMembership}</p>
        )}
      </section>

      <section className="profile-block">
        <div className="section-title">
          <h2>{labels.profilePreferences}</h2>
        </div>
        <label className="profile-language-select">
          <span>{labels.profileLanguage}</span>
          <select
            value={profile.interface_language}
            disabled={isSaving}
            onChange={(event) => void handleLanguage(event.target.value)}
          >
            {profile.available_languages.map((language) => (
              <option key={language.code} value={language.code}>{language.label}</option>
            ))}
          </select>
        </label>

        {saveError ? <p className="inline-error">{saveError}</p> : null}
      </section>

      {canManageQrLabels ? (
        <section className="profile-block profile-label-section">
          <div className="section-title">
            <div>
              <h2>{labels.qrLabelSelectionTitle}</h2>
              <p>{labels.qrLabelSelectionHelp}</p>
            </div>
            <span>{qrLabelSelection.length}</span>
          </div>

          <div className="profile-label-toolbar">
            <label className="admin-label-search profile-label-search">
              <span>{labels.qrLabelSelectionSearch}</span>
              <input
                type="search"
                value={labelSearch}
                placeholder={labels.qrLabelSearchPlaceholder}
                onChange={(event) => setLabelSearch(event.target.value)}
              />
            </label>
            <div className="admin-label-actions">
              <button
                type="button"
                disabled={!labelBoxes.length}
                onClick={() => labelBoxes.forEach((box) => onAddQrLabel(buildQrLabelItem(box)))}
              >
                {labels.qrLabelAddToSelection}
              </button>
              <button type="button" disabled={!qrLabelSelection.length} onClick={onClearQrLabelSelection}>
                {labels.qrLabelClearSelection}
              </button>
            </div>
          </div>

          <div className="admin-label-selector profile-label-selector">
            {labelBoxes.map((box) => {
              const isSelected = qrLabelSelection.some((label) => label.id === box.id);
              return (
                <label key={box.id}>
                  <input
                    type="checkbox"
                    checked={isSelected}
                    onChange={() => toggleQrLabel(box)}
                  />
                  <span>
                    <strong>{box.global_code}</strong>
                    <small>{box.species.scientific_name}</small>
                  </span>
                  <em>{box.thermal_zone?.name ?? labels.noZone}</em>
                </label>
              );
            })}
          </div>

          <div className="profile-selected-labels">
            <div>
              <strong>{qrLabelSelection.length}</strong>
              <span>{labels.qrLabelSelectionCount}</span>
            </div>
            {qrLabelSelection.length ? (
              <ul>
                {qrLabelSelection.map((label) => (
                  <li key={label.id}>
                    <span>
                      <strong>{label.globalCode}</strong>
                      <small>{label.speciesName}</small>
                    </span>
                    <button type="button" onClick={() => onRemoveQrLabel(label.id)}>×</button>
                  </li>
                ))}
              </ul>
            ) : (
              <p>{labels.qrLabelSelectionEmpty}</p>
            )}
          </div>

          <button
            className="admin-print-labels-button profile-print-labels-button"
            type="button"
            disabled={!qrLabelSelection.length}
            onClick={onPrintQrLabelSelection}
          >
            {labels.qrLabelPrintSelection}
          </button>
        </section>
      ) : null}

      <section className="profile-block profile-session-block">
        <button
          className="profile-sign-out"
          type="button"
          disabled={isLoggingOut}
          onClick={handleLogout}
        >
          {isLoggingOut ? labels.saving : labels.logoutAction}
        </button>
        {logoutError ? <p className="inline-error">{logoutError}</p> : null}
      </section>
    </section>
  );
}

function userCanManageQrLabels(profile: UserProfile) {
  if (profile.is_superuser) return true;
  return profile.memberships.some(
    (membership) => membership.role === 'admin' || membership.role === 'lab_technician',
  );
}

function getQrLabelOrganizationIds(profile: UserProfile) {
  if (profile.is_superuser) return null;

  return new Set(
    profile.memberships
      .filter((membership) => membership.role === 'admin' || membership.role === 'lab_technician')
      .map((membership) => membership.organization.id),
  );
}

function getRoleDescription(role: UserProfile['memberships'][number]['role'], labels: ProfileLabels) {
  switch (role) {
    case 'admin':
      return labels.roleDescAdmin;
    case 'lab_technician':
      return labels.roleDescTechnician;
    default:
      return labels.roleDescViewer;
  }
}

function getProfileInitials(profile: UserProfile): string {
  const fromName = [profile.first_name, profile.last_name]
    .filter(Boolean)
    .map((part) => part.charAt(0))
    .join('');
  const initials = fromName || profile.username.slice(0, 2);
  return initials.toUpperCase();
}
