import type { BoxDetail, BoxItem } from '../types';
import { buildQrLabelItem, downloadQrLabel, printQrLabels } from '../utils/qrLabels';

type QrLabelModalLabels = {
  close: string;
  download: string;
  help: string;
  print: string;
  qrCode: string;
  title: string;
};

export default function QrLabelModal({
  box,
  labels,
  onClose,
  qrImageUrl,
}: {
  box: BoxItem | BoxDetail;
  labels: QrLabelModalLabels;
  onClose: () => void;
  qrImageUrl: string;
}) {
  const label = buildQrLabelItem(box, qrImageUrl);

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
