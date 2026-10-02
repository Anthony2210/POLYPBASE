import { useEffect, useState } from 'react';

import { apiGetResource, getOrganizationResourceSignal } from '../api/client';
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
  const [resource, setResource] = useState<{ source: string; url: string; contextSignal: AbortSignal } | null>(null);
  const contextSignal = getOrganizationResourceSignal();

  useEffect(() => {
    const controller = new AbortController();
    let objectUrl: string | undefined;
    const clearResource = () => {
      controller.abort(contextSignal.reason);
      setResource(null);
      if (objectUrl) URL.revokeObjectURL(objectUrl);
      objectUrl = undefined;
    };
    setResource(null);
    contextSignal.addEventListener('abort', clearResource, { once: true });
    void apiGetResource(item.qrImageUrl, { signal: controller.signal, contextSignal })
      .then((svg) => {
        if (controller.signal.aborted || contextSignal.aborted) return;
        objectUrl = URL.createObjectURL(new Blob([svg], { type: 'image/svg+xml' }));
        setResource({ source: item.qrImageUrl, url: objectUrl, contextSignal });
      })
      .catch(() => {
        if (!controller.signal.aborted) setResource(null);
      });
    return () => {
      contextSignal.removeEventListener('abort', clearResource);
      controller.abort();
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [item.qrImageUrl, contextSignal]);

  const imageUrl = resource?.source === item.qrImageUrl
    && resource.contextSignal === contextSignal && !contextSignal.aborted ? resource.url : undefined;
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
        src={imageUrl}
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
