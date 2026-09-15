import { type CSSProperties } from 'react';

import type { BoxDetail, BoxItem } from '../types';
import {
  DEFAULT_QR_LABEL_PRINT_SETTINGS,
  buildQrLabelItem,
  downloadQrLabel,
  getQrLabelPreviewCssVariables,
  printQrLabels,
  type QrLabelItem,
} from '../utils/qrLabels';
import ModalPortal from './ModalPortal';
import PolypbaseIcon from './PolypbaseIcon';
import QrLabel from './QrLabel';

type QrLabelModalLabels = {
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
  const label = buildQrLabelItem(box, qrImageUrl);
  const isSelected = selectedLabels.some((item) => item.id === label.id);
  // The frame is the query container the label reads its physical geometry from.
  const frameStyle = getQrLabelPreviewCssVariables(DEFAULT_QR_LABEL_PRINT_SETTINGS) as CSSProperties;

  return (
    <ModalPortal>
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
              disabled={isSelected}
              onClick={() => onAddToSelection(label)}
            >
              {isSelected ? labels.alreadySelected : labels.addToSelection}
            </button>
            <button type="button" disabled={!selectedLabels.length} onClick={onViewSelection}>
              {labels.viewSelection}
            </button>
          </div>
        </section>

        <footer className="qr-label-modal-actions">
          <button type="button" className="is-secondary" onClick={() => void downloadQrLabel(label)}>
            <span className="button-icon-label">
              <PolypbaseIcon name="download" size={17} />
              {labels.download}
            </span>
          </button>
          <button type="button" onClick={() => printQrLabels([label])}>
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
