export type ChartReading = { id: number | string; date: string; polypCount: number; ephyraeCount: number };
export type ChartSeries = { polyps: boolean; ephyrae: boolean };

export type ChartDetailAnchor = { x: number; y: number };
export type ChartDetailSize = { width: number; height: number };

export function resolveChartDetail<T>(pinned: T | null, focused: T | null, hovered: T | null): T | null {
  return pinned ?? focused ?? hovered;
}

export function dismissPinnedChartDetail<T>(key: string, pinned: T | null) {
  return key === 'Escape'
    ? { handled: true, pinned: null }
    : { handled: false, pinned };
}

export function placeChartDetail(
  anchor: ChartDetailAnchor,
  bounds: ChartDetailSize,
  panel: ChartDetailSize,
  gap = 8,
  inset = 8,
) {
  const above = Math.max(0, anchor.y - gap - inset);
  const below = Math.max(0, bounds.height - anchor.y - gap - inset);
  const placement = above >= panel.height ? 'above' : below >= panel.height || below >= above ? 'below' : 'above';
  const maxHeight = placement === 'above' ? above : below;
  const height = Math.min(panel.height, maxHeight);
  const left = Math.max(inset, Math.min(bounds.width - inset - panel.width, anchor.x - panel.width / 2));
  const top = placement === 'above' ? anchor.y - gap - height : anchor.y + gap;
  return {
    left,
    top: Math.max(inset, Math.min(bounds.height - inset - height, top)),
    maxHeight,
    placement,
  };
}

export function chartBiologicalValues(
  reading: { polypCount: number; ephyraeCount: number; salinity?: string | number | null },
  labels: { polyps: string; ephyrae: string; salinity?: string },
) {
  const values: Array<{ kind: 'polyps' | 'ephyrae' | 'salinity'; label: string; value: string }> = [
    { kind: 'polyps', label: labels.polyps, value: String(reading.polypCount) },
    { kind: 'ephyrae', label: labels.ephyrae, value: String(reading.ephyraeCount) },
  ];
  if (labels.salinity && reading.salinity != null && reading.salinity !== '') {
    const numeric = typeof reading.salinity === 'number' ? reading.salinity : Number.parseFloat(reading.salinity);
    values.push({
      kind: 'salinity',
      label: labels.salinity,
      value: Number.isFinite(numeric) ? (Number.isInteger(numeric) ? String(numeric) : numeric.toFixed(1)) : String(reading.salinity),
    });
  }
  return values;
}

export function splitMeasurementsOnGaps<T extends ChartReading>(measurements: T[]): T[][] {
  const segments: T[][] = [];
  let current: T[] = [];
  measurements.forEach((measurement, index) => {
    const previous = measurements[index - 1];
    if (previous && calendarDaysBetween(previous.date, measurement.date) > 10) {
      if (current.length) segments.push(current);
      current = [];
    }
    current.push(measurement);
  });
  if (current.length) segments.push(current);
  return segments;
}

export function toggleChartSeries(current: ChartSeries, series: keyof ChartSeries): ChartSeries {
  if (current[series] && !current[series === 'polyps' ? 'ephyrae' : 'polyps']) return current;
  return { ...current, [series]: !current[series] };
}

export function selectedVisibleReading<T extends { id: number | string }>(measurements: T[], selectedId: number | string | null): T | null {
  return measurements.find((measurement) => measurement.id === selectedId) ?? null;
}

function calendarDaysBetween(first: string, second: string): number {
  const firstDay = first.slice(0, 10).split('-').map(Number);
  const secondDay = second.slice(0, 10).split('-').map(Number);
  return Math.round((Date.UTC(secondDay[0], secondDay[1] - 1, secondDay[2]) - Date.UTC(firstDay[0], firstDay[1] - 1, firstDay[2])) / 86400000);
}
