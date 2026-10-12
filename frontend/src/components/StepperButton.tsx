import { type KeyboardEvent, type PointerEvent, type ReactNode, useEffect, useRef, useState } from 'react';

export default function StepperButton({
  'aria-label': ariaLabel,
  children,
  onStep,
}: {
  'aria-label': string;
  children: ReactNode;
  onStep: () => void;
}) {
  const delayRef = useRef<number | null>(null);
  const intervalRef = useRef<number | null>(null);
  // The pressed look belongs to this button instance alone, so pressing one
  // control can never mark another one pressed, including the same symbol in
  // another field. It clears as soon as this button is released.
  const [isPressed, setIsPressed] = useState(false);

  function clearRepeat() {
    if (delayRef.current != null) {
      window.clearTimeout(delayRef.current);
      delayRef.current = null;
    }

    if (intervalRef.current != null) {
      window.clearInterval(intervalRef.current);
      intervalRef.current = null;
    }

    setIsPressed(false);
  }

  useEffect(() => clearRepeat, []);

  function startRepeat(event: PointerEvent<HTMLButtonElement>) {
    event.preventDefault();
    // Preventing native pointer behavior must not leave focus on a previous control.
    event.currentTarget.focus({ preventScroll: true });
    clearRepeat();
    setIsPressed(true);
    onStep();

    delayRef.current = window.setTimeout(() => {
      intervalRef.current = window.setInterval(onStep, 95);
    }, 340);
  }

  function handleKeyboard(event: KeyboardEvent<HTMLButtonElement>) {
    if ((event.key === 'Enter' || event.key === ' ') && !event.repeat) {
      event.preventDefault();
      onStep();
    }
  }

  return (
    <button
      type="button"
      className={isPressed ? 'count-stepper-button is-pressed' : 'count-stepper-button'}
      aria-label={ariaLabel}
      onPointerDown={startRepeat}
      onPointerUp={clearRepeat}
      onPointerLeave={clearRepeat}
      onPointerCancel={clearRepeat}
      onBlur={clearRepeat}
      onKeyDown={handleKeyboard}
      onContextMenu={(event) => event.preventDefault()}
    >
      {children}
    </button>
  );
}
