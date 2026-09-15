import type { QrLabelItem } from '../utils/qrLabels';

export type QrLabelVariant = 'trigger' | 'preview' | 'label';

export default function QrLabel({
  altLabel = 'QR code',
  className,
  item,
  showMetadata = true,
  variant = 'preview',
}: {
  altLabel?: string;
  className?: string;
  item: QrLabelItem;
  showMetadata?: boolean;
  variant?: QrLabelVariant;
}) {
  const classes = [
    'qr-label',
    `qr-label--${variant}`,
    showMetadata ? '' : 'qr-label--image-only',
    className,
  ].filter(Boolean).join(' ');

  return (
    <span className={classes}>
      <img
        className="qr-label__image"
        src={item.qrImageUrl}
        alt={`${altLabel} ${item.globalCode}`}
        decoding="async"
        loading={variant === 'preview' ? 'lazy' : 'eager'}
      />
      {showMetadata ? (
        <span className="qr-label__metadata">
          <span className="qr-label__text">
            <strong>{item.globalCode}</strong>
            <small>{item.speciesName}</small>
          </span>
        </span>
      ) : null}
    </span>
  );
}
