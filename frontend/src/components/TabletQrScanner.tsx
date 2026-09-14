import type { IScannerControls } from '@zxing/browser';
import { useEffect, useRef, useState } from 'react';

import type { BoxItem } from '../types';
import { getBoxIdFromQrValue } from '../utils/boxLookup';
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
  autoStart = false,
  boxes,
  labels,
  onSelectBox,
}: {
  autoStart?: boolean;
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
  const onSelectBoxRef = useRef(onSelectBox);
  const [isScanning, setIsScanning] = useState(autoStart);
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => {
    onSelectBoxRef.current = onSelectBox;
  }, [onSelectBox]);

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
            onSelectBoxRef.current(scannedBoxId);
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
  }, [isScanning, boxes, found, permission, secureContext, unsupported]);

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
