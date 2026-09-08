import { type ReactNode, useLayoutEffect, useRef, useEffect } from 'react';

import ModalPortal from './ModalPortal';
import PolypbaseIcon from './PolypbaseIcon';

const FOCUSABLE_SELECTOR =
  'a[href], button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled), [tabindex]:not([tabindex="-1"])';

export default function AdminActionPanel({
  children,
  title,
  closeLabel,
  onClose,
  wide = false,
}: {
  children: ReactNode;
  title: string;
  closeLabel: string;
  onClose: () => void;
  wide?: boolean;
}) {
  const panelRef = useRef<HTMLElement>(null);
  const returnFocusRef = useRef<HTMLElement | null>(null);

  // Capture the control that opened the panel, then move keyboard focus inside
  // the dialog. The panel is portaled to the body, so the opener still holds
  // focus when this runs.
  useLayoutEffect(() => {
    returnFocusRef.current = document.activeElement as HTMLElement | null;
    const panel = panelRef.current;
    if (panel) {
      const firstFocusable = panel.querySelector<HTMLElement>(FOCUSABLE_SELECTOR);
      (firstFocusable ?? panel).focus();
    }
  }, []);

  useEffect(() => {
    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === 'Escape') {
        onClose();
        return;
      }
      if (event.key !== 'Tab') return;

      const panel = panelRef.current;
      if (!panel) return;
      const focusable = Array.from(panel.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR));
      if (!focusable.length) return;

      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      const active = document.activeElement;
      if (event.shiftKey) {
        if (active === first || !panel.contains(active)) {
          event.preventDefault();
          last.focus();
        }
      } else if (active === last || !panel.contains(active)) {
        event.preventDefault();
        first.focus();
      }
    }

    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [onClose]);

  // Restore focus to the control that opened the panel when it closes.
  useEffect(() => {
    return () => {
      returnFocusRef.current?.focus();
    };
  }, []);

  return (
    <ModalPortal>
      <div className="admin-action-backdrop" role="presentation" onMouseDown={onClose}>
        <aside
          ref={panelRef}
          tabIndex={-1}
          className={wide ? 'admin-action-panel is-wide' : 'admin-action-panel'}
          role="dialog"
          aria-modal="true"
          aria-label={title}
          onMouseDown={(event) => event.stopPropagation()}
        >
          <header className="admin-action-panel__header">
            <h2>{title}</h2>
            <button className="icon-button" type="button" aria-label={closeLabel} title={closeLabel} onClick={onClose}>
              <PolypbaseIcon name="close" size={19} />
            </button>
          </header>
          <div className="admin-action-panel__body">{children}</div>
        </aside>
      </div>
    </ModalPortal>
  );
}
