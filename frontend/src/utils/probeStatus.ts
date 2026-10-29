export function getProbeElapsedTime(value: string | null | undefined, now = Date.now()) {
  if (!value) return null;
  const timestamp = Date.parse(value);
  if (!Number.isFinite(timestamp)) return null;
  const minutes = Math.floor(Math.max(0, now - timestamp) / 60_000);
  if (minutes < 60) return { count: minutes, unit: 'minute' as const };
  if (minutes < 1440) return { count: Math.floor(minutes / 60), unit: 'hour' as const };
  return { count: Math.floor(minutes / 1440), unit: 'day' as const };
}

export function formatProbeReadingTime(value: string, locale: string) {
  return new Intl.DateTimeFormat(locale, {
    day: 'numeric',
    month: 'short',
    hour: '2-digit',
    minute: '2-digit',
  }).format(new Date(value));
}
