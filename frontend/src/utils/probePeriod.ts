import type { ProbeWindow } from './probeChart';

const HOUR_MS = 3_600_000;
const DAY_MS = 24 * HOUR_MS;

/** Rolling windows offered by the period control. Display choices, not thresholds. */
export const RELATIVE_PERIOD_HOURS = [24, 7 * 24, 30 * 24] as const;
export type RelativePeriodHours = (typeof RELATIVE_PERIOD_HOURS)[number];
export const DEFAULT_PERIOD_HOURS: RelativePeriodHours = 24;

/** A rolling window ending now, or exact bounds chosen by the user. */
export type ChartPeriod =
  | { kind: 'relative'; hours: RelativePeriodHours }
  | { kind: 'custom'; window: ProbeWindow };

export const DEFAULT_CHART_PERIOD: ChartPeriod = { kind: 'relative', hours: DEFAULT_PERIOD_HOURS };

export function isRelativePeriodHours(value: number): value is RelativePeriodHours {
  return (RELATIVE_PERIOD_HOURS as readonly number[]).includes(value);
}

export function periodWindow(period: ChartPeriod, liveEnd: number): ProbeWindow {
  return period.kind === 'custom' ? period.window : { start: liveEnd - period.hours * HOUR_MS, end: liveEnd };
}

function isLocalMidnight(time: number) {
  const date = new Date(time);
  return date.getHours() === 0 && date.getMinutes() === 0 && date.getSeconds() === 0 && date.getMilliseconds() === 0;
}

function formatRange(format: Intl.DateTimeFormat, start: number, end: number) {
  // formatRange collapses the shared parts (same day, month or year).
  return typeof format.formatRange === 'function'
    ? format.formatRange(start, end)
    : `${format.format(start)} – ${format.format(end)}`;
}

/**
 * Short visible label of exact bounds. Shared parts are written once, the year
 * only when it is not the current one, and whole local days without times.
 * The exact bounds stay available through formatExactPeriod.
 */
export function formatCompactPeriod(window: ProbeWindow, locale: string, now: number) {
  const currentYear = new Date(now).getFullYear();
  const lastInstant = window.end - 1;
  const showYear = new Date(window.start).getFullYear() !== currentYear || new Date(lastInstant).getFullYear() !== currentYear;
  const year = showYear ? { year: 'numeric' as const } : {};
  if (isLocalMidnight(window.start) && isLocalMidnight(window.end)) {
    const days = new Intl.DateTimeFormat(locale, { day: 'numeric', month: 'short', ...year });
    // The end bound is exclusive: a window ending at midnight covers the previous day.
    return window.end - window.start <= DAY_MS + HOUR_MS && new Date(window.start).getDate() === new Date(lastInstant).getDate()
      ? days.format(window.start)
      : formatRange(days, window.start, lastInstant);
  }
  const times = new Intl.DateTimeFormat(locale, { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit', ...year });
  return formatRange(times, window.start, window.end);
}

export function browserTimeZone() {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || null;
  } catch {
    return null;
  }
}

/** Full, unambiguous bounds with the time zone in which they are written. */
export function formatExactPeriod(window: ProbeWindow, locale: string) {
  const format = new Intl.DateTimeFormat(locale, { dateStyle: 'medium', timeStyle: 'short' });
  const zone = browserTimeZone();
  return `${format.format(window.start)} → ${format.format(window.end)}${zone ? ` (${zone})` : ''}`;
}
