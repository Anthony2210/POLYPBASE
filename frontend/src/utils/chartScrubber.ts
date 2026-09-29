import { parseChartDate, toChartDateString } from './chartWindow';

export type ScrubberRange = {
  extentStart: number;
  extentEnd: number;
  start: number;
  end: number;
};

// UTC day numbers are derived from local calendar fields, not elapsed milliseconds.
export function chartDay(value: string): number {
  const date = parseChartDate(value);
  return Date.UTC(date.getFullYear(), date.getMonth(), date.getDate()) / 86400000;
}

export function chartDayString(day: number): string {
  const date = new Date(day * 86400000);
  return toChartDateString(new Date(date.getUTCFullYear(), date.getUTCMonth(), date.getUTCDate()));
}

function clamp(value: number, min: number, max: number) {
  return Math.max(min, Math.min(max, value));
}

export function buildScrubberRange(
  extentStart: string,
  extentEnd: string,
  startDate: string,
  endDate: string,
): ScrubberRange {
  const first = chartDay(extentStart);
  const last = Math.max(first + 1, chartDay(extentEnd));
  const start = clamp(chartDay(startDate), first, last - 1);
  const end = clamp(chartDay(endDate), start + 1, last);
  return { extentStart: first, extentEnd: last, start, end };
}

export function moveScrubberRange(range: ScrubberRange, days: number): ScrubberRange {
  const duration = range.end - range.start;
  const start = clamp(range.start + Math.round(days), range.extentStart, range.extentEnd - duration);
  return { ...range, start, end: start + duration };
}

export function resizeScrubberRange(range: ScrubberRange, edge: 'start' | 'end', day: number): ScrubberRange {
  return edge === 'start'
    ? { ...range, start: clamp(Math.round(day), range.extentStart, range.end - 1) }
    : { ...range, end: clamp(Math.round(day), range.start + 1, range.extentEnd) };
}
