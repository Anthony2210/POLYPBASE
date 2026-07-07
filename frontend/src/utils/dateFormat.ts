export function formatDisplayDate(value: string) {
  const normalizedValue = value.includes('T') ? value : `${value}T00:00:00`;

  return new Intl.DateTimeFormat('fr-FR', {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
  }).format(new Date(normalizedValue));
}
