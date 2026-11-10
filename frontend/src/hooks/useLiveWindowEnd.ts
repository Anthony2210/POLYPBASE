import { useEffect, useState } from 'react';

export function useDocumentVisible() {
  const [visible, setVisible] = useState(() => document.visibilityState === 'visible');

  useEffect(() => {
    const update = () => setVisible(document.visibilityState === 'visible');
    update();
    document.addEventListener('visibilitychange', update);
    return () => document.removeEventListener('visibilitychange', update);
  }, []);

  return visible;
}

/** A rolling window end. It pauses while hidden or disabled, and catches up on return. */
export default function useLiveWindowEnd(intervalMs: number, enabled = true) {
  const [end, setEnd] = useState(Date.now);

  useEffect(() => {
    if (!enabled) return undefined;
    const visible = () => document.visibilityState === 'visible';
    function advance() {
      if (visible()) setEnd(Date.now());
    }
    function catchUp() {
      setEnd((current) => (visible() && Date.now() - current >= intervalMs ? Date.now() : current));
    }
    catchUp();
    const timer = globalThis.setInterval(advance, intervalMs);
    globalThis.addEventListener('focus', catchUp);
    document.addEventListener('visibilitychange', catchUp);
    return () => {
      globalThis.clearInterval(timer);
      globalThis.removeEventListener('focus', catchUp);
      document.removeEventListener('visibilitychange', catchUp);
    };
  }, [enabled, intervalMs]);

  return end;
}
