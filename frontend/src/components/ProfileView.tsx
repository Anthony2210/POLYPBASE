import { type ReactNode, useState } from 'react';

import type { UserProfile } from '../types';
import { getErrorMessage } from '../utils/errors';
import PageLoader from './PageLoader';

type ProfileLabels = {
  account: string;
  logoutAction: string;
  logoutError: string;
  profileEmail: string;
  profileLanguage: string;
  profileAdminTitle: string;
  profileAdminText: string;
  profileMemberships: string;
  profileNoEmail: string;
  profileNoMembership: string;
  profileAllOrganizationsAccess: string;
  profileLabelsMobileText: string;
  profilePreferences: string;
  roleDescAdmin: string;
  roleDescTechnician: string;
  roleDescViewer: string;
  saving: string;
  labelsTitle: string;
};

export default function ProfileView({
  isLoading,
  labels,
  onOpenLabels,
  onLogout,
  onUpdateLanguage,
  adminSection,
  profile,
}: {
  isLoading: boolean;
  labels: ProfileLabels;
  onOpenLabels: () => void;
  onLogout: () => Promise<void>;
  onUpdateLanguage: (language: string) => Promise<void>;
  adminSection?: ReactNode;
  profile: UserProfile | null;
}) {
  const [isSaving, setIsSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [isLoggingOut, setIsLoggingOut] = useState(false);
  const [logoutError, setLogoutError] = useState<string | null>(null);

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

  if (isLoading) {
    return (
      <PageLoader variant="profile" label={labels.account} />
    );
  }

  if (!profile) return null;

  const fullName = formatProfileName(profile);

  return (
    <section className="profile-page">
      <header className="profile-identity-card">
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
        <div className="profile-identity-actions">
          <button
            className="profile-sign-out"
            type="button"
            disabled={isLoggingOut}
            onClick={handleLogout}
          >
            {isLoggingOut ? labels.saving : labels.logoutAction}
          </button>
          {logoutError ? <p className="inline-error">{logoutError}</p> : null}
        </div>
      </header>

      <section className="profile-block profile-mobile-labels-link">
        <button className="profile-mobile-labels-button" type="button" onClick={onOpenLabels}>
          <span>
            <strong>{labels.labelsTitle}</strong>
            <small>{labels.profileLabelsMobileText}</small>
          </span>
          <span aria-hidden="true">›</span>
        </button>
      </section>

      <section className="profile-block">
        <div className="section-title">
          <h2>{labels.profileMemberships}</h2>
          {/* Some technical accounts can reach every organization without a membership row. */}
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
          <p className="muted compact-text">{labels.profileAllOrganizationsAccess}</p>
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

      {adminSection ? (
        <section className="profile-admin-section">
          <div className="profile-admin-heading">
            <div>
              <h2>{labels.profileAdminTitle}</h2>
            </div>
          </div>
          {adminSection}
        </section>
      ) : null}
    </section>
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

function formatProfileName(profile: UserProfile): string {
  const firstName = formatFirstName(profile.first_name);
  const lastName = formatLastName(profile.last_name);
  return [firstName, lastName].filter(Boolean).join(' ') || profile.username;
}

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
