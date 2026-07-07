import type { IScannerControls } from '@zxing/browser';
import { useEffect, useRef, useState } from 'react';

import type { BoxItem } from '../types';
import { triggerHaptic } from '../utils/haptics';

type TabletQrScannerLabels = {
  found: string;
  permission: string;
  secureContext: string;
  start: string;
  stop: string;
  unsupported: string;
};

export default function TabletQrScanner({
  boxes,
  labels,
  onSelectBox,
}: {
  boxes: BoxItem[];
  labels: TabletQrScannerLabels;
  onSelectBox: (id: number) => void;
}) {
  const {
    found,
    permission,
    secureContext,
    start,
    stop,
    unsupported,
  } = labels;
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const scannerControlsRef = useRef<IScannerControls | null>(null);
  const [isScanning, setIsScanning] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => {
    if (!isScanning) {
      stopQrScanner(scannerControlsRef);
      return;
    }

    let isCancelled = false;

    async function startScanner() {
      if (!window.isSecureContext) {
        setMessage(secureContext);
        setIsScanning(false);
        return;
      }

      if (!navigator.mediaDevices?.getUserMedia) {
        setMessage(unsupported);
        setIsScanning(false);
        return;
      }

      try {
        const video = videoRef.current;
        if (!video) return;

        const { BrowserQRCodeReader } = await import('@zxing/browser');
        const reader = new BrowserQRCodeReader();
        let hasDetectedBox = false;
        const controls = await reader.decodeFromConstraints(
          {
            video: { facingMode: { ideal: 'environment' } },
            audio: false,
          },
          video,
          (result) => {
            if (!result || isCancelled || hasDetectedBox) return;

            const scannedBoxId = getBoxIdFromQrValue(result.getText(), boxes);
            if (scannedBoxId == null) return;

            hasDetectedBox = true;
            triggerHaptic([10, 34, 12]);
            setMessage(found);
            setIsScanning(false);
            onSelectBox(scannedBoxId);
          },
        );

        if (isCancelled || hasDetectedBox) {
          controls.stop();
          return;
        }

        scannerControlsRef.current = controls;
      } catch {
        setMessage(permission);
        setIsScanning(false);
      }
    }

    void startScanner();

    return () => {
      isCancelled = true;
      stopQrScanner(scannerControlsRef);
    };
  }, [isScanning, boxes, found, onSelectBox, permission, secureContext, unsupported]);

  return (
    <section className={isScanning ? 'tablet-scanner-panel is-scanning' : 'tablet-scanner-panel'}>
      <button
        className="scanner-preview"
        type="button"
        aria-label={isScanning ? stop : start}
        onClick={() => {
          setMessage(null);
          setIsScanning((current) => !current);
        }}
      >
        {isScanning ? (
          <>
            <video ref={videoRef} muted playsInline />
            <span className="scanner-live-label">{stop}</span>
          </>
        ) : (
          <span className="scanner-placeholder">
            <span className="scanner-frame" aria-hidden="true">
              <span className="scanner-corner is-top-left" />
              <span className="scanner-corner is-top-right" />
              <span className="scanner-corner is-bottom-left" />
              <span className="scanner-corner is-bottom-right" />
              <span className="scanner-dash is-left" />
              <span className="scanner-dash is-right" />
            </span>
          </span>
        )}
      </button>

      {message ? <p className="scanner-status" aria-live="polite">{message}</p> : null}
    </section>
  );
}

function stopQrScanner(scannerControlsRef: { current: IScannerControls | null }) {
  scannerControlsRef.current?.stop();
  scannerControlsRef.current = null;
}

function getBoxIdFromQrValue(value: string, boxes: BoxItem[]) {
  const trimmedValue = value.trim();
  const routeMatch = trimmedValue.match(/\/bac\/(\d+)\/?/) ?? trimmedValue.match(/\/boxes\/([^/?#]+)\/?/);

  if (routeMatch?.[1]) {
    const routeValue = decodeURIComponent(routeMatch[1]);
    const routeId = Number(routeValue);
    if (Number.isInteger(routeId)) return routeId;

    const routeBox = boxes.find((box) => box.global_code.toLowerCase() === routeValue.toLowerCase());
    if (routeBox) return routeBox.id;
  }

  const normalizedValue = trimmedValue.toLowerCase();
  const directBox = boxes.find((box) => (
    box.global_code.toLowerCase() === normalizedValue ||
    box.local_code.toLowerCase() === normalizedValue
  ));

  return directBox?.id ?? null;
}
