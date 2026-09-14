import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';

import { createPendingResolver } from '../utils/confirmActionResolver';
import ModalPortal from './ModalPortal';

export type ConfirmActionVariant = 'default' | 'warning' | 'danger';

export type ConfirmActionDetail = {
  label: string;
  value: string | number | null | undefined;
};

export type ConfirmActionOptions = {
  title: string;
  message?: string;
  details?: ConfirmActionDetail[];
  confirmLabel: string;
  cancelLabel: string;
  variant?: ConfirmActionVariant;
};

type PendingConfirmAction = ConfirmActionOptions & {
  returnFocus: HTMLElement | null;
};

const FOCUSABLE_SELECTOR =
  'a[href], button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled), [tabindex]:not([tabindex="-1"])';

export function useConfirmAction() {
  const [pendingAction, setPendingAction] = useState<PendingConfirmAction | null>(null);
  const resolverRef = useRef(createPendingResolver<boolean>());

  // Settle a confirmation still owned by this hook instance when its owner
  // unmounts, so the awaiting caller never hangs and the resolver is cleared.
  // The resolver is per instance, so this can never cancel another consumer.
  useEffect(() => {
    const resolver = resolverRef.current;
    return () => {
      resolver.settle(false);
    };
  }, []);

  const confirmAction = useCallback((options: ConfirmActionOptions) => new Promise<boolean>((resolve) => {
    // Capture the opener before React updates the DOM: the originating control
    // may be disabled or removed once the confirmation is open.
    const active = document.activeElement;
    if (!resolverRef.current.request(resolve)) {
      // A confirmation is already unresolved; decline this one instead of
      // orphaning the first resolver.
      resolve(false);
      return;
    }
    setPendingAction({
      ...options,
      returnFocus: active instanceof HTMLElement ? active : null,
    });
  }), []);

  const resolveAction = useCallback((confirmed: boolean) => {
    if (!resolverRef.current.settle(confirmed)) return;
    setPendingAction(null);
  }, []);

  const confirmActionModal = pendingAction ? (
    <ConfirmActionModal
      action={pendingAction}
      onCancel={() => resolveAction(false)}
      onConfirm={() => resolveAction(true)}
    />
  ) : null;

  return { confirmAction, confirmActionModal };
}

function ConfirmActionModal({
  action,
  onCancel,
  onConfirm,
}: {
  action: PendingConfirmAction;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  const variant = action.variant ?? 'default';
  const visibleDetails = (action.details ?? []).filter((detail) => detail.value !== null && detail.value !== undefined && detail.value !== '');
  const dialogRef = useRef<HTMLElement>(null);

  // Move keyboard focus into the dialog as soon as it opens.
  useLayoutEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    const firstFocusable = dialog.querySelector<HTMLElement>(FOCUSABLE_SELECTOR);
    (firstFocusable ?? dialog).focus();
  }, []);

  useEffect(() => {
    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === 'Escape') {
        onCancel();
        return;
      }
      if (event.key !== 'Tab') return;

      const dialog = dialogRef.current;
      if (!dialog) return;
      const focusable = Array.from(dialog.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR));
      if (!focusable.length) return;

      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      const active = document.activeElement;
      if (event.shiftKey) {
        if (active === first || !dialog.contains(active)) {
          event.preventDefault();
          last.focus();
        }
      } else if (active === last || !dialog.contains(active)) {
        event.preventDefault();
        first.focus();
      }
    }

    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [onCancel]);

  // Restore focus to the control that opened the dialog. The originating
  // row-menu item may already be gone, so only restore to a connected node.
  useEffect(() => {
    const returnFocus = action.returnFocus;
    return () => {
      if (returnFocus && returnFocus.isConnected) returnFocus.focus();
    };
  }, [action.returnFocus]);

  return (
    <ModalPortal>
      <div className="modal-backdrop confirm-action-backdrop" role="presentation" onMouseDown={onCancel}>
      <section
        ref={dialogRef}
        tabIndex={-1}
        className={`confirm-action-modal is-${variant}`}
        role="dialog"
        aria-modal="true"
        aria-labelledby="confirm-action-title"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <header className="confirm-action-heading">
          <span className="confirm-action-mark" aria-hidden="true">
            !
          </span>
          <div>
            <p>Confirmation</p>
            <h2 id="confirm-action-title">{action.title}</h2>
          </div>
          <button className="modal-close-button" type="button" aria-label={action.cancelLabel} onClick={onCancel}>
            x
          </button>
        </header>

        {action.message ? <p className="confirm-action-message">{action.message}</p> : null}

        {visibleDetails.length ? (
          <dl className="confirm-action-details">
            {visibleDetails.map((detail) => (
              <div key={detail.label}>
                <dt>{detail.label}</dt>
                <dd>{detail.value}</dd>
              </div>
            ))}
          </dl>
        ) : null}

        <footer className="confirm-action-footer">
          <button className="confirm-action-cancel" type="button" onClick={onCancel}>
            {action.cancelLabel}
          </button>
          <button className={`confirm-action-submit is-${variant}`} type="button" onClick={onConfirm}>
            {action.confirmLabel}
          </button>
        </footer>
        </section>
      </div>
    </ModalPortal>
  );
}
