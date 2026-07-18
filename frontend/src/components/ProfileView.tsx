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
  profilePreferences: string;
  roleDescAdmin: string;
  roleDescTechnician: string;
  roleDescViewer: string;
  saving: string;
};

export default function ProfileView({
  isLoading,
  labels,
  onLogout,
  onUpdateLanguage,
  adminSection,
  profile,
}: {
  isLoading: boolean;
  labels: ProfileLabels;
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

  const fullName = [profile.first_name, profile.last_name].filter(Boolean).join(' ') || profile.username;
  const initials = getProfileInitials(profile);

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

function getProfileInitials(profile: UserProfile): string {
  const fromName = [profile.first_name, profile.last_name]
    .filter(Boolean)
    .map((part) => part.charAt(0))
    .join('');
  const initials = fromName || profile.username.slice(0, 2);
  return initials.toUpperCase();
}
