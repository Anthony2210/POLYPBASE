import { ApiError } from '../api/client';
import type { TranslationKey } from '../i18n/fr';

export function getErrorMessage(
  error: unknown,
  fallback = 'Impossible de joindre l API Django.',
) {
  if (error instanceof ApiError) {
    return error.message;
  }

  return fallback;
}

// Expected account-management refusals carry a stable backend code so the
// interface can translate them instead of showing the raw API prose.
const ACCOUNT_ERROR_KEYS: Record<string, TranslationKey> = {
  membership_admin_required: 'manageErrorMembershipAdminRequired',
  responsable_required: 'manageErrorResponsableRequired',
  responsable_membership_protected: 'manageErrorResponsableProtected',
  active_responsable_required: 'manageErrorActiveResponsableRequired',
  last_active_responsable: 'manageErrorLastActiveResponsable',
};

export function getAccountErrorMessage(
  error: unknown,
  t: (key: TranslationKey) => string,
  fallback = 'Impossible de joindre l API Django.',
) {
  if (error instanceof ApiError) {
    const code = getErrorCode(error.data);
    const key = code ? ACCOUNT_ERROR_KEYS[code] : undefined;
    return key ? t(key) : error.message;
  }

  return fallback;
}

function getErrorCode(data: unknown): string | null {
  if (data && typeof data === 'object' && !Array.isArray(data)) {
    const code = (data as Record<string, unknown>).code;
    if (typeof code === 'string') return code;
  }

  return null;
}
