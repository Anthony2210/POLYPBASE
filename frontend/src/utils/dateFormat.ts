export function formatDisplayDate(value: string) {
  const normalizedValue = value.includes('T') ? value : `${value}T00:00:00`;

  return new Intl.DateTimeFormat(getDocumentLocale(), {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
  }).format(new Date(normalizedValue));
}

export type RelativeDateTimeLabels = {
  todayAt: string;
  yesterdayAt: string;
};

export type RelativeDateTime = {
  relative: string;
  exact: string;
};

function calendarDayNumber(value: Date) {
  return Date.UTC(value.getFullYear(), value.getMonth(), value.getDate()) / 86400000;
}

function capitalize(value: string, locale: string) {
  return value.charAt(0).toLocaleUpperCase(locale) + value.slice(1);
}

function completedMonthsBetween(earlier: Date, later: Date) {
  let months = (later.getFullYear() - earlier.getFullYear()) * 12
    + later.getMonth() - earlier.getMonth();
  if (later.getDate() < earlier.getDate()) months -= 1;
  return Math.max(1, months);
}

export function formatRelativeDateTime(
  value: string,
  labels: RelativeDateTimeLabels,
  now = new Date(),
  locale = getDocumentLocale(),
): RelativeDateTime {
  const date = new Date(value);
  const exact = new Intl.DateTimeFormat(locale, {
    dateStyle: 'short',
    timeStyle: 'short',
  }).format(date);
  const time = new Intl.DateTimeFormat(locale, {
    hour: '2-digit',
    minute: '2-digit',
  }).format(date);
  const dayDifference = calendarDayNumber(now) - calendarDayNumber(date);

  if (dayDifference === 0) return { relative: `${labels.todayAt} ${time}`, exact };
  if (dayDifference === 1) return { relative: `${labels.yesterdayAt} ${time}`, exact };

  const formatter = new Intl.RelativeTimeFormat(locale, {
    numeric: 'always',
    style: 'short',
  });
  if (dayDifference < 7) {
    return { relative: capitalize(formatter.format(-dayDifference, 'day'), locale), exact };
  }
  if (dayDifference < 30) {
    const weeks = Math.floor(dayDifference / 7);
    return { relative: capitalize(formatter.format(-weeks, 'week'), locale), exact };
  }

  const months = completedMonthsBetween(date, now);
  const longFormatter = new Intl.RelativeTimeFormat(locale, {
    numeric: 'always',
    style: 'long',
  });
  if (months < 12) {
    return { relative: capitalize(longFormatter.format(-months, 'month'), locale), exact };
  }
  const years = Math.floor(months / 12);
  return { relative: capitalize(longFormatter.format(-years, 'year'), locale), exact };
}

export function formatIsoWeekDateLabel(value: string) {
  const [yearPart, monthPart, dayPart] = value.slice(0, 10).split('-').map(Number);
  const date = new Date(Date.UTC(yearPart, monthPart - 1, dayPart));
  const day = date.getUTCDay() || 7;

  date.setUTCDate(date.getUTCDate() + 4 - day);

  const isoYear = date.getUTCFullYear();
  const yearStart = new Date(Date.UTC(isoYear, 0, 1));
  const week = Math.ceil((((date.getTime() - yearStart.getTime()) / 86400000) + 1) / 7);

  const longDate = new Intl.DateTimeFormat(getDocumentLocale(), {
    day: 'numeric',
    month: 'long',
    year: 'numeric',
  }).format(new Date(`${value.slice(0, 10)}T00:00:00`));

  return `S${week} ${longDate}`;
}

function getDocumentLocale() {
  const language = typeof document === 'undefined' ? 'fr' : document.documentElement.lang;
  if (language.startsWith('en')) return 'en-GB';
  if (language.startsWith('ja')) return 'ja-JP';
  return 'fr-FR';
}
