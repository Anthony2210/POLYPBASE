import { useEffect, useLayoutEffect, useRef, useState } from 'react';

const FOCUSABLE_SELECTOR =
  'a[href], button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled), [tabindex]:not([tabindex="-1"])';

// Move and subculture submissions may open a confirmation before saving starts.
export default function useMutationDialog<T extends HTMLElement>(isSaving: boolean, onClose: () => void) {
  const dialogRef = useRef<HTMLElement>(null);
  const initialFocusRef = useRef<T>(null);
  const submittingRef = useRef(false);
  const savingRef = useRef(isSaving);
  savingRef.current = isSaving;
  const [isSubmitting, setIsSubmitting] = useState(false);
  const isBusy = isSaving || isSubmitting;

  function close() {
    if (!savingRef.current && !submittingRef.current) onClose();
  }

  useLayoutEffect(() => {
    const active = document.activeElement;
    const returnFocus = active instanceof HTMLElement ? active : null;
    (initialFocusRef.current ?? dialogRef.current)?.focus();
    return () => {
      if (returnFocus?.isConnected) returnFocus.focus();
    };
  }, []);

  useLayoutEffect(() => {
    const dialog = dialogRef.current;
    const dialogs = document.querySelectorAll('[role="dialog"][aria-modal="true"]');
    if (!dialog || dialogs[dialogs.length - 1] !== dialog) return;
    if (isBusy) dialog.focus();
    else if (document.activeElement === dialog || !dialog.contains(document.activeElement)) {
      (initialFocusRef.current ?? dialog).focus();
    }
  });

  useEffect(() => {
    function handleKeyDown(event: KeyboardEvent) {
      const dialog = dialogRef.current;
      const dialogs = document.querySelectorAll('[role="dialog"][aria-modal="true"]');
      // Only the top dialog owns keyboard focus, including nested confirmations.
      if (!dialog || dialogs[dialogs.length - 1] !== dialog) return;
      if (event.key === 'Escape') {
        event.preventDefault();
        close();
        return;
      }
      if (event.key !== 'Tab') return;
      const controls = Array.from(dialog.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR));
      const first = controls[0];
      const last = controls[controls.length - 1];
      const active = document.activeElement;
      if (!first || !last) {
        event.preventDefault();
        dialog.focus();
      } else if (event.shiftKey && (active === first || active === dialog || !dialog.contains(active))) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && (active === last || active === dialog || !dialog.contains(active))) {
        event.preventDefault();
        first.focus();
      }
    }
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [onClose]);

  async function submit(operation: () => Promise<void>) {
    if (savingRef.current || submittingRef.current) return;
    submittingRef.current = true;
    setIsSubmitting(true);
    try {
      await operation();
    } finally {
      submittingRef.current = false;
      setIsSubmitting(false);

    }
  }

  return { dialogRef, initialFocusRef, isBusy, close, submit };
}
