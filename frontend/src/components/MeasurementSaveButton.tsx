import {
  type CSSProperties,
  type PointerEvent as ReactPointerEvent,
  useEffect,
  useRef,
  useState,
} from 'react';

import { triggerHaptic } from '../utils/haptics';

type MeasurementSaveButtonLabels = {
  hold: string;
  save: string;
  saved: string;
  saving: string;
};

export default function MeasurementSaveButton({
  isDesktop,
  isSaving,
  isSuccess,
  labels,
  onSave,
}: {
  isDesktop: boolean;
  isSaving: boolean;
  isSuccess: boolean;
  labels: MeasurementSaveButtonLabels;
  onSave: () => Promise<boolean>;
}) {
  const holdDuration = 700;
  const frameRef = useRef<number | null>(null);
  const holdStartRef = useRef<number | null>(null);
  const buttonRef = useRef<HTMLButtonElement | null>(null);
  const [isHolding, setIsHolding] = useState(false);
  const [holdProgress, setHoldProgress] = useState(0);

  useEffect(() => () => {
    if (frameRef.current != null) {
      window.cancelAnimationFrame(frameRef.current);
    }
  }, []);

  function cancelHold() {
    if (frameRef.current != null) {
      window.cancelAnimationFrame(frameRef.current);
      frameRef.current = null;
    }
    holdStartRef.current = null;
    setIsHolding(false);
    setHoldProgress(0);
  }

  function completeHold() {
    frameRef.current = null;
    holdStartRef.current = null;
    setHoldProgress(1);
    setIsHolding(false);
    if (buttonRef.current?.form && !buttonRef.current.form.reportValidity()) {
      setHoldProgress(0);
      return;
    }
    triggerHaptic(10);
    void onSave();
  }

  function updateHoldProgress(timestamp: number) {
    if (holdStartRef.current == null) return;

    const progress = Math.min((timestamp - holdStartRef.current) / holdDuration, 1);
    setHoldProgress(progress);

    if (progress >= 1) {
      completeHold();
      return;
    }

    frameRef.current = window.requestAnimationFrame(updateHoldProgress);
  }

  function startHold(event: ReactPointerEvent<HTMLButtonElement>) {
    if (isSaving) return;

    event.currentTarget.setPointerCapture?.(event.pointerId);
    holdStartRef.current = performance.now();
    setIsHolding(true);
    setHoldProgress(0);
    frameRef.current = window.requestAnimationFrame(updateHoldProgress);
  }

  if (isDesktop) {
    return (
      <button className={isSuccess ? 'measurement-save-button is-success' : 'measurement-save-button'} type="submit" disabled={isSaving}>
        <span>{isSaving ? labels.saving : isSuccess ? labels.saved : labels.save}</span>
      </button>
    );
  }

  const buttonClass = [
    'measurement-save-button',
    'is-hold-action',
    isHolding ? 'is-holding' : '',
    isSuccess ? 'is-success' : '',
  ].filter(Boolean).join(' ');

  return (
    <button
      ref={buttonRef}
      className={buttonClass}
      type="button"
      disabled={isSaving}
      title={labels.hold}
      aria-label={labels.hold}
      style={{
        '--hold-progress': `${holdProgress * 360}deg`,
        userSelect: 'none',
        WebkitUserSelect: 'none',
      } as CSSProperties}
      onPointerDown={startHold}
      onPointerUp={cancelHold}
      onPointerCancel={cancelHold}
      onPointerLeave={cancelHold}
      onContextMenu={(event) => event.preventDefault()}
      onKeyDown={(event) => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault();
          if (event.currentTarget.form && !event.currentTarget.form.reportValidity()) {
            return;
          }
          void onSave();
        }
      }}
    >
      <span className="hold-save-progress" aria-hidden="true" />
      <span>{isSaving ? labels.saving : isSuccess ? labels.saved : labels.save}</span>
    </button>
  );
}
