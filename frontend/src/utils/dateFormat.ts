export function formatDisplayDate(value: string) {
  const normalizedValue = value.includes('T') ? value : `${value}T00:00:00`;

  return new Intl.DateTimeFormat('fr-FR', {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
  }).format(new Date(normalizedValue));
}

export function formatIsoWeekDateLabel(value: string) {
  const [yearPart, monthPart, dayPart] = value.slice(0, 10).split('-').map(Number);
  const date = new Date(Date.UTC(yearPart, monthPart - 1, dayPart));
  const day = date.getUTCDay() || 7;

  date.setUTCDate(date.getUTCDate() + 4 - day);

  const isoYear = date.getUTCFullYear();
  const yearStart = new Date(Date.UTC(isoYear, 0, 1));
  const week = Math.ceil((((date.getTime() - yearStart.getTime()) / 86400000) + 1) / 7);

  const longDate = new Intl.DateTimeFormat('fr-FR', {
    day: 'numeric',
    month: 'long',
    year: 'numeric',
  }).format(new Date(`${value.slice(0, 10)}T00:00:00`));

  return `S${week} ${longDate}`;
}
