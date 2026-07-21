import { useCallback, useEffect, useState } from 'react';

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
  resolve: (confirmed: boolean) => void;
};

export function useConfirmAction() {
  const [pendingAction, setPendingAction] = useState<PendingConfirmAction | null>(null);

  const confirmAction = useCallback((options: ConfirmActionOptions) => new Promise<boolean>((resolve) => {
    setPendingAction({ ...options, resolve });
  }), []);

  const resolveAction = useCallback((confirmed: boolean) => {
    setPendingAction((currentAction) => {
      currentAction?.resolve(confirmed);
      return null;
    });
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
  action: ConfirmActionOptions;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  const variant = action.variant ?? 'default';
  const visibleDetails = (action.details ?? []).filter((detail) => detail.value !== null && detail.value !== undefined && detail.value !== '');

  useEffect(() => {
    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === 'Escape') {
        onCancel();
      }
    }

    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [onCancel]);

  return (
    <div className="modal-backdrop confirm-action-backdrop" role="presentation" onMouseDown={onCancel}>
      <section
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
  );
}
