export type ProbeWindow = { start: number; end: number };

/** Narrowest zoom, in milliseconds: below this a per-minute curve stops being readable. */
export const MIN_PROBE_WINDOW_MS = 30 * 60 * 1000;
const HOUR_MS = 60 * 60 * 1000;

/** Keep a zoom selection ordered, inside [extentStart, now] and at least the minimum width. */
export function clampProbeWindow(start: number, end: number, now: number): ProbeWindow {
  const first = Math.min(start, end);
  const last = Math.max(start, end);
  const duration = Math.max(MIN_PROBE_WINDOW_MS, Math.min(366 * 24 * HOUR_MS, last - first));
  const center = (first + last) / 2;
  const boundedEnd = Math.min(now, center + duration / 2);
  return { start: Math.round(boundedEnd - duration), end: Math.round(boundedEnd) };
}

/**
 * Split points when an expected reading is missing. The server supplies the
 * effective cadence, including condensed historical data.
 */
export function splitProbeSegments<T extends readonly [number, ...unknown[]]>(
  points: readonly T[],
  resolutionMinutes: number,
): T[][] {
  const maximumGap = resolutionMinutes * 60 * 1000;
  const segments: T[][] = [];
  let current: T[] = [];
  for (const point of points) {
    const previous = current[current.length - 1];
    if (previous && point[0] - previous[0] > maximumGap) {
      segments.push(current);
      current = [];
    }
    current.push(point);
  }
  if (current.length) segments.push(current);
  return segments;
}

/** Index of the point closest in time, or -1 for an empty series. */
export function nearestProbePointIndex(points: ReadonlyArray<readonly [number, ...unknown[]]>, time: number) {
  if (!points.length) return -1;
  let low = 0;
  let high = points.length - 1;
  while (low < high) {
    const middle = Math.floor((low + high) / 2);
    if (points[middle][0] < time) low = middle + 1;
    else high = middle;
  }
  if (low > 0 && Math.abs(points[low - 1][0] - time) <= Math.abs(points[low][0] - time)) return low - 1;
  return low;
}

/** Shared temperature domain so the probes of one zone are drawn on the same scale. */
export function probeTemperatureDomain(
  values: number[],
  target: number | null,
): [number, number] | null {
  const all = target === null ? values : [...values, target];
  const finite = all.filter((value) => Number.isFinite(value));
  if (!finite.length) return null;
  let minimum = Math.min(...finite);
  let maximum = Math.max(...finite);
  if (maximum - minimum < 1) {
    const center = (minimum + maximum) / 2;
    minimum = center - 0.5;
    maximum = center + 0.5;
  }
  const padding = (maximum - minimum) * 0.08;
  return [minimum - padding, maximum + padding];
}

/** Revalidate just after the server's freshness boundary, without granting access locally. */
export function probeMonitoringDelay(
  monitoring: { last_reading_at: string | null; fallback_after_hours: number; manual_entry_available: boolean },
  now: number,
) {
  const pollingDelay = 60 * 1000;
  if (!monitoring.last_reading_at || monitoring.manual_entry_available) return pollingDelay;
  const boundary = Date.parse(monitoring.last_reading_at) + monitoring.fallback_after_hours * HOUR_MS;
  return Number.isFinite(boundary) ? Math.min(pollingDelay, Math.max(1000, boundary - now + 1000)) : pollingDelay;
}
