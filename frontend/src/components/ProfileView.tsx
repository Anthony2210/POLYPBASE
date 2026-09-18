import { useState } from 'react';

import type { Language, Translator } from '../i18n';
import type { UserProfile } from '../types';
import { getAccountMemberRoleLabel } from '../utils/accountMembers';
import { getErrorMessage } from '../utils/errors';
import PageLoader from './PageLoader';
import PolypbaseIcon from './PolypbaseIcon';
import ProfileActionsSection from './ProfileActionsSection';

type ProfileLabels = {
  account: string;
  logoutAction: string;
  logoutError: string;
  profileEmail: string;
  profileLanguage: string;
  profileAdminAction: string;
  profileMemberships: string;
  profileNoEmail: string;
  profileNoMembership: string;
  profileAllOrganizationsAccess: string;
  profilePreferences: string;
  profileActiveOrganization: string;
  profileDefaultOrganization: string;
  profileFullAccess: string;
  roleResponsable: string;
  roleDescAdmin: string;
  roleDescTechnician: string;
  roleDescViewer: string;
  saving: string;
};

export default function ProfileView({
  isLoading,
  labels,
  language,
  canOpenAdmin,
  onOpenAdmin,
  onOpenBox,
  onSelectOrganization,
  onLogout,
  onUpdateLanguage,
  activeOrganizationId,
  profile,
  t,
}: {
  isLoading: boolean;
  activeOrganizationId: number | null;
  canOpenAdmin: boolean;
  labels: ProfileLabels;
  language: Language;
  onOpenAdmin: () => void;
  onOpenBox: (boxId: number, code: string) => void;
  onSelectOrganization: (organizationId: number) => void;
  onLogout: () => Promise<void>;
  onUpdateLanguage: (language: string) => Promise<void>;
  profile: UserProfile | null;
  t: Translator;
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
  const organizations = getSelectableOrganizations(profile);

  return (
    <section className="profile-page">
      <header className="profile-identity-card">
        <div className="profile-identity-main">
          <p className="eyebrow">{labels.account}</p>
          <h2>{fullName}</h2>
          <div className="profile-identity-meta">
            <span className="profile-meta-item">
              <small>{labels.profileEmail}</small>
              {profile.email || labels.profileNoEmail}
            </span>
          </div>
        </div>
        <div className="profile-identity-actions">
          {canOpenAdmin ? (
            <button
              className="secondary-button button-icon-label profile-admin-action"
              type="button"
              onClick={onOpenAdmin}
            >
              <PolypbaseIcon name="settings" size={17} />
              {labels.profileAdminAction}
            </button>
          ) : null}
        </div>
      </header>


      {organizations.length > 1 ? (
        <section className="profile-block profile-organization-context">
          <div className="section-title">
            <div>
              <h2>{labels.profileActiveOrganization}</h2>
            </div>
          </div>
          <div className="profile-organization-options">
            {organizations.map((organization) => {
              const membership = profile.memberships.find((item) => item.organization.id === organization.id);
              const isActive = organization.id === activeOrganizationId;
              return (
                <button
                  key={organization.id}
                  className={isActive ? 'is-active' : ''}
                  type="button"
                  aria-pressed={isActive}
                  onClick={() => onSelectOrganization(organization.id)}
                >
                  <span className="profile-organization-identity">
                    <strong>{organization.name}</strong>
                    <small>
                      {membership
                        ? getAccountMemberRoleLabel(membership, labels.roleResponsable)
                        : labels.profileFullAccess}
                    </small>
                  </span>
                  {isActive ? (
                    <span className="profile-organization-state">{labels.profileDefaultOrganization}</span>
                  ) : null}
                </button>
              );
            })}
          </div>
        </section>
      ) : null}

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

      <ProfileActionsSection
        activeOrganizationId={activeOrganizationId}
        key={activeOrganizationId ?? 'none'}
        language={language}
        onOpenBox={onOpenBox}
        t={t}
      />

      <div className="profile-logout-row">
        <button
          className="profile-sign-out"
          type="button"
          disabled={isLoggingOut}
          onClick={handleLogout}
        >
          <span className="button-icon-label">
            {!isLoggingOut ? <PolypbaseIcon name="logout" size={17} /> : null}
            {isLoggingOut ? labels.saving : labels.logoutAction}
          </span>
        </button>
        {logoutError ? <p className="inline-error">{logoutError}</p> : null}
      </div>
    </section>
  );
}

function formatProfileName(profile: UserProfile): string {
  const firstName = formatFirstName(profile.first_name);
  const lastName = formatLastName(profile.last_name);
  return [firstName, lastName].filter(Boolean).join(' ') || profile.email || '—';
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

function getSelectableOrganizations(profile: UserProfile) {
  const organizations = profile.memberships.length
    ? profile.memberships.map((membership) => membership.organization)
    : profile.organizations;

  return organizations.filter(
    (organization, index) =>
      organizations.findIndex((item) => item.id === organization.id) === index,
  );
}
