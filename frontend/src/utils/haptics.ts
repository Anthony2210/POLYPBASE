export function triggerHaptic(pattern: number | number[]) {
  if (typeof navigator === 'undefined' || typeof navigator.vibrate !== 'function') return;

  try {
    navigator.vibrate(pattern);
  } catch {
    // Haptic feedback is optional and unsupported by several browsers.
  }
}
