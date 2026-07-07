import type { BoxDetail, BoxItem } from '../types';
import {
  buildQrLabelItem,
  downloadQrLabel,
  printQrLabels,
  type QrLabelItem,
} from '../utils/qrLabels';

type QrLabelModalLabels = {
  addToSelection: string;
  alreadySelected: string;
  clearSelection: string;
  close: string;
  download: string;
  help: string;
  print: string;
  printSelection: string;
  qrCode: string;
  selectionCount: string;
  title: string;
};

export default function QrLabelModal({
  box,
  labels,
  onAddToSelection,
  onClearSelection,
  onClose,
  onPrintSelection,
  qrImageUrl,
  selectedLabels,
}: {
  box: BoxItem | BoxDetail;
  labels: QrLabelModalLabels;
  onAddToSelection: (label: QrLabelItem) => void;
  onClearSelection: () => void;
  onClose: () => void;
  onPrintSelection: () => void;
  qrImageUrl: string;
  selectedLabels: QrLabelItem[];
}) {
  const label = buildQrLabelItem(box, qrImageUrl);
  const isSelected = selectedLabels.some((item) => item.id === label.id);

  return (
    <div className="modal-backdrop qr-print-backdrop" role="presentation" onClick={onClose}>
      <section
        className="qr-label-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="qr-label-title"
        onClick={(event) => event.stopPropagation()}
      >
        <header className="modal-heading qr-label-modal-heading">
          <div>
            <h2 id="qr-label-title">{labels.title}</h2>
            <span>{labels.help}</span>
          </div>
          <button type="button" aria-label={labels.close} onClick={onClose}>
            x
          </button>
        </header>

        <div className="qr-label-print-sheet">
          <div className="qr-label-main">
            <strong>{label.globalCode}</strong>
            <span>{label.speciesName}</span>
          </div>

          <div className="qr-label-code">
            <img src={label.qrImageUrl} alt={`${labels.qrCode} ${label.globalCode}`} />
            <strong>{labels.qrCode}</strong>
          </div>
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
              disabled={isSelected}
              onClick={() => onAddToSelection(label)}
            >
              {isSelected ? labels.alreadySelected : labels.addToSelection}
            </button>
            <button type="button" disabled={!selectedLabels.length} onClick={onPrintSelection}>
              {labels.printSelection}
            </button>
            <button
              type="button"
              className="is-ghost"
              disabled={!selectedLabels.length}
              onClick={onClearSelection}
            >
              {labels.clearSelection}
            </button>
          </div>
        </section>

        <footer className="qr-label-modal-actions">
          <button type="button" className="is-secondary" onClick={() => void downloadQrLabel(label)}>
            {labels.download}
          </button>
          <button type="button" onClick={() => printQrLabels([label])}>
            {labels.print}
          </button>
        </footer>
      </section>
    </div>
  );
}
