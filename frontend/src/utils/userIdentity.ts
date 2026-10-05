export type ReadableUserIdentity = {
  first_name: string;
  last_name: string;
  email: string;
};

export function formatFirstName(value: string): string {
  return value
    .trim()
    .replace(/\s+/g, ' ')
    .toLocaleLowerCase('fr-FR')
    .replace(/(^|[\s'’-])(\p{L})/gu, (_match, separator: string, letter: string) =>
      `${separator}${letter.toLocaleUpperCase('fr-FR')}`,
    );
}

export function formatLastName(value: string): string {
  return value.trim().replace(/\s+/g, ' ').toLocaleUpperCase('fr-FR');
}

/** Only structured identity fields are displayable; legacy usernames are not. */
export function formatReadableUserIdentity(identity: ReadableUserIdentity | null | undefined): string {
  if (!identity) return '';
  const firstName = formatFirstName(identity.first_name ?? '');
  const lastName = formatLastName(identity.last_name ?? '');
  return [firstName, lastName].filter(Boolean).join(' ') || (identity.email ?? '').trim();
}
