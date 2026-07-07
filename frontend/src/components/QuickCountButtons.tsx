import { useEffect, useRef, useState } from 'react';

import { triggerHaptic } from '../utils/haptics';

export default function QuickCountButtons({
  onAdd,
  values,
}: {
  onAdd: (value: number) => void;
  values: number[];
}) {
  const [lastPressed, setLastPressed] = useState<number | null>(null);
  const resetTimerRef = useRef<number | null>(null);

  useEffect(() => () => {
    if (resetTimerRef.current != null) {
      window.clearTimeout(resetTimerRef.current);
    }
  }, []);

  function handleAdd(value: number) {
    triggerHaptic(8);
    onAdd(value);
    setLastPressed(value);
    if (resetTimerRef.current != null) {
      window.clearTimeout(resetTimerRef.current);
    }
    resetTimerRef.current = window.setTimeout(() => setLastPressed(null), 180);
  }

  return (
    <span className="quick-counts">
      {values.map((value) => (
        <button
          key={value}
          className={lastPressed === value ? 'is-pressed' : ''}
          type="button"
          onClick={() => handleAdd(value)}
        >
          +{value}
        </button>
      ))}
    </span>
  );
}
