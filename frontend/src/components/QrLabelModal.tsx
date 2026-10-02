import { type CSSProperties, useEffect, useId, useLayoutEffect, useRef, useState } from 'react';

import type { BoxDetail, BoxItem } from '../types';
import {
  DEFAULT_QR_LABEL_PRINT_SETTINGS,
  buildQrLabelItem,
  downloadQrLabel,
  getQrLabelPreviewCssVariables,
  getQrLabelPreparationMessage,
  printQrLabels,
  type QrLabelItem,
  type QrLabelPreparationLabels,
  type QrLabelPreparationResult,
} from '../utils/qrLabels';
import ModalPortal from './ModalPortal';
import PolypbaseIcon from './PolypbaseIcon';
import QrLabel from './QrLabel';

type QrLabelModalLabels = QrLabelPreparationLabels & {
  addToSelection: string;
  alreadySelected: string;
  close: string;
  download: string;
  help: string;
  print: string;
  qrCode: string;
  selectionCount: string;
  title: string;
  viewSelection: string;
};

const FOCUSABLE_SELECTOR =
  'a[href], button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled), [tabindex]:not([tabindex="-1"])';

type PreparationAction = 'download' | 'print';

export default function QrLabelModal({
  box,
  labels,
  onAddToSelection,
  onClose,
  onViewSelection,
  qrImageUrl,
  selectedLabels,
}: {
  box: BoxItem | BoxDetail;
  labels: QrLabelModalLabels;
  onAddToSelection: (label: QrLabelItem) => void;
  onClose: () => void;
  onViewSelection: () => void;
  qrImageUrl: string;
  selectedLabels: QrLabelItem[];
}) {
  const dialogRef = useRef<HTMLElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);
  const preparingRef = useRef(false);
  const [pendingAction, setPendingAction] = useState<PreparationAction | null>(null);
  const [failure, setFailure] = useState<{
    action: PreparationAction;
    result: Extract<QrLabelPreparationResult, { status: 'failed' }>;
  } | null>(null);
  const titleId = useId();
  const helpId = useId();
  const isPreparing = pendingAction !== null;

  useLayoutEffect(() => {
    const active = document.activeElement;
    const returnFocus = active instanceof HTMLElement ? active : null;
    closeRef.current?.focus();
    return () => {
      if (returnFocus?.isConnected) returnFocus.focus();
    };
  }, []);

  useLayoutEffect(() => {
    if (isPreparing) dialogRef.current?.focus();
    else if (document.activeElement === dialogRef.current) closeRef.current?.focus();
  }, [isPreparing]);

  useEffect(() => {
    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === 'Escape') {
        event.preventDefault();
        if (!preparingRef.current) onClose();
        return;
      }
      if (event.key !== 'Tab') return;
      const dialog = dialogRef.current;
      if (!dialog) return;
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

  async function handlePreparation(action: PreparationAction) {
    if (preparingRef.current) return;
    preparingRef.current = true;
    setPendingAction(action);
    setFailure(null);
    const result = action === 'download' ? await downloadQrLabel(label) : await printQrLabels([label]);
    setFailure(result.status === 'failed' ? { action, result } : null);
    preparingRef.current = false;
    setPendingAction(null);
  }

  const label = buildQrLabelItem(box, qrImageUrl);
  const isSelected = selectedLabels.some((item) => item.id === label.id);
  // The frame is the query container the label reads its physical geometry from.
  const frameStyle = getQrLabelPreviewCssVariables(DEFAULT_QR_LABEL_PRINT_SETTINGS) as CSSProperties;

  return (
    <ModalPortal>
      <div className="modal-backdrop qr-print-backdrop" role="presentation" onClick={() => { if (!preparingRef.current) onClose(); }}>
      <section
        ref={dialogRef}
        tabIndex={-1}
        className="qr-label-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={helpId}
        onClick={(event) => event.stopPropagation()}
      >
        <header className="modal-heading qr-label-modal-heading">
          <div>
            <h2 id={titleId}>{labels.title}</h2>
            <span id={helpId}>{labels.help}</span>
          </div>
          <button ref={closeRef} type="button" aria-label={labels.close} disabled={isPreparing} onClick={onClose}>
            <PolypbaseIcon name="close" size={19} />
          </button>
        </header>

        <div className="qr-label-print-frame" style={frameStyle}>
          <QrLabel
            altLabel={labels.qrCode}
            className="qr-label-print-sheet"
            item={label}
            variant="label"
          />
        </div>

        <section className="qr-label-selection-panel">
          <div>
            <strong>{selectedLabels.length}</strong>
            <span>{labels.selectionCount}</span>
          </div>
          <div className="qr-label-selection-actions">
            <button
              type="button"
              className={isSelected ? 'is-secondary is-selected' : 'is-secondary'}
              disabled={isSelected || isPreparing}
              onClick={() => onAddToSelection(label)}
            >
              {isSelected ? labels.alreadySelected : labels.addToSelection}
            </button>
            <button type="button" disabled={!selectedLabels.length || isPreparing} onClick={onViewSelection}>
              {labels.viewSelection}
            </button>
          </div>
        </section>

        <div role="status">{isPreparing ? labels.qrLabelPreparing : ''}</div>
        {failure ? (
          <div>
            <p className="inline-error" role="alert">{getQrLabelPreparationMessage(failure.result.reason, labels)}</p>
            <button className="secondary-button" type="button" disabled={isPreparing} onClick={() => void handlePreparation(failure.action)}>
              {labels.qrLabelRetry}
            </button>
          </div>
        ) : null}

        <footer className="qr-label-modal-actions">
          <button type="button" className="is-secondary" disabled={isPreparing} onClick={() => void handlePreparation('download')}>
            <span className="button-icon-label">
              <PolypbaseIcon name="download" size={17} />
              {labels.download}
            </span>
          </button>
          <button type="button" disabled={isPreparing} onClick={() => void handlePreparation('print')}>
            <span className="button-icon-label">
              <PolypbaseIcon name="print" size={17} />
              {labels.print}
            </span>
          </button>
        </footer>
        </section>
      </div>
    </ModalPortal>
  );
}
