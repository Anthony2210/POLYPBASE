import type { ProbeWindow } from './probeChart';

export const MIN_GESTURE_WINDOW_MS = 30 * 60 * 1000;
const MAX_GESTURE_WINDOW_MS = 366 * 24 * 60 * 60 * 1000;

export function probePointerFraction(x: number, left: number, right: number) {
  return Math.max(0, Math.min(1, (x - left) / Math.max(1, right - left)));
}

/** Keep the anchor at the same screen fraction while changing the duration. */
export function zoomProbeAt(window: ProbeWindow, anchor: number, factor: number): ProbeWindow {
  const duration = window.end - window.start;
  const fraction = Math.max(0, Math.min(1, (anchor - window.start) / duration));
  const nextDuration = Math.max(MIN_GESTURE_WINDOW_MS, Math.min(MAX_GESTURE_WINDOW_MS, duration * factor));
  const start = Math.round(anchor - nextDuration * fraction);
  return { start, end: start + Math.round(nextDuration) };
}

/** Always use the original two-finger baseline, not a window published mid-gesture. */
export function pinchProbeWindow(
  baseline: ProbeWindow, initialA: number, initialB: number, currentA: number, currentB: number,
  initialDistance = Math.abs(initialB - initialA), distance = Math.abs(currentB - currentA),
): ProbeWindow {
  if (initialDistance < 0.001 || distance < 0.001) return baseline;
  const anchor = baseline.start + (baseline.end - baseline.start) * (initialA + initialB) / 2;
  const zoomed = zoomProbeAt(baseline, anchor, initialDistance / distance);
  const duration = zoomed.end - zoomed.start;
  const start = Math.round(anchor - duration * (currentA + currentB) / 2);
  return { start, end: start + duration };
}

export function probeWheelFactor(delta: number, mode: number, pageHeight: number) {
  const pixels = delta * (mode === 1 ? 16 : mode === 2 ? pageHeight : 1);
  return Math.exp(Math.max(-1, Math.min(1, pixels * 0.002)));
}

export function probeCursorStep(window: ProbeWindow, cursor: number | null, direction: -1 | 1, resolutionMinutes: number) {
  const current = cursor ?? (window.start + window.end) / 2;
  return Math.max(window.start, Math.min(window.end, current + direction * resolutionMinutes * 60000));
}
