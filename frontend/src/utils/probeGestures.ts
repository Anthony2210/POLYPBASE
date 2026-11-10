import type { ProbeWindow } from './probeChart';

export const MIN_GESTURE_WINDOW_MS = 30 * 60 * 1000;
const MAX_GESTURE_WINDOW_MS = 366 * 24 * 60 * 60 * 1000;

/** Keep the anchor at the same screen fraction while changing the duration. */
export function zoomProbeAt(window: ProbeWindow, anchor: number, factor: number): ProbeWindow {
  const duration = window.end - window.start;
  const fraction = Math.max(0, Math.min(1, (anchor - window.start) / duration));
  const nextDuration = Math.max(MIN_GESTURE_WINDOW_MS, Math.min(MAX_GESTURE_WINDOW_MS, duration * factor));
  const start = Math.round(anchor - nextDuration * fraction);
  return { start, end: start + Math.round(nextDuration) };
}
