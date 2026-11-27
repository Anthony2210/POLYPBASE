export function formatDisplayDate(value: string) {
  const normalizedValue = value.includes('T') ? value : `${value}T00:00:00`;

  return new Intl.DateTimeFormat(getDocumentLocale(), {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
  }).format(new Date(normalizedValue));
}

export function formatDisplayDateTime(value: string) {
  return new Intl.DateTimeFormat(getDocumentLocale(), {
    dateStyle: 'medium',
    timeStyle: 'short',
  }).format(new Date(value));
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
  return (
    Date.UTC(value.getFullYear(), value.getMonth(), value.getDate()) / 86400000
  );
}

function capitalize(value: string, locale: string) {
  return value.charAt(0).toLocaleUpperCase(locale) + value.slice(1);
}

function completedMonthsBetween(earlier: Date, later: Date) {
  let months =
    (later.getFullYear() - earlier.getFullYear()) * 12 +
    later.getMonth() -
    earlier.getMonth();
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

  if (dayDifference === 0)
    return { relative: `${labels.todayAt} ${time}`, exact };
  if (dayDifference === 1)
    return { relative: `${labels.yesterdayAt} ${time}`, exact };

  const formatter = new Intl.RelativeTimeFormat(locale, {
    numeric: 'always',
    style: 'short',
  });
  if (dayDifference < 7) {
    return {
      relative: capitalize(formatter.format(-dayDifference, 'day'), locale),
      exact,
    };
  }
  if (dayDifference < 30) {
    const weeks = Math.floor(dayDifference / 7);
    return {
      relative: capitalize(formatter.format(-weeks, 'week'), locale),
      exact,
    };
  }

  const months = completedMonthsBetween(date, now);
  const longFormatter = new Intl.RelativeTimeFormat(locale, {
    numeric: 'always',
    style: 'long',
  });
  if (months < 12) {
    return {
      relative: capitalize(longFormatter.format(-months, 'month'), locale),
      exact,
    };
  }
  const years = Math.floor(months / 12);
  return {
    relative: capitalize(longFormatter.format(-years, 'year'), locale),
    exact,
  };
}

export function parseIsoCalendarDate(
  value: string | null | undefined,
): Date | null {
  if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(value))
    return null;

  const date = new Date(`${value}T00:00:00Z`);
  // Date normalizes some impossible days instead of rejecting them.
  if (
    !Number.isFinite(date.getTime()) ||
    date.toISOString().slice(0, 10) !== value
  )
    return null;
  return date;
}

export function formatDisplayCalendarDate(
  value: string | null | undefined,
): string | null {
  return typeof value === 'string' && parseIsoCalendarDate(value)
    ? formatDisplayDate(value)
    : null;
}

export function formatIsoWeekDateLabel(
  value: string | null | undefined,
): string | null {
  if (typeof value !== 'string') return null;
  if (
    value.length > 10 &&
    (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?$/.test(
      value,
    ) ||
      !Number.isFinite(Date.parse(value)))
  )
    return null;

  // Use the written calendar date, even when the timestamp has an offset.
  const date = parseIsoCalendarDate(value.slice(0, 10));
  if (!date) return null;

  const thursday = new Date(date);
  thursday.setUTCDate(thursday.getUTCDate() + 4 - (thursday.getUTCDay() || 7));
  const yearStart = new Date(thursday);
  yearStart.setUTCMonth(0, 1);
  const week = Math.ceil(
    ((thursday.getTime() - yearStart.getTime()) / 86400000 + 1) / 7,
  );

  const longDate = new Intl.DateTimeFormat(getDocumentLocale(), {
    day: 'numeric',
    month: 'long',
    year: 'numeric',
    timeZone: 'UTC',
  }).format(date);

  return `S${week} ${longDate}`;
}

export function getDocumentLocale() {
  const language =
    typeof document === 'undefined' ? 'fr' : document.documentElement.lang;
  if (language.startsWith('en')) return 'en-GB';
  if (language.startsWith('ja')) return 'ja-JP';
  return 'fr-FR';
}
