import type { IScannerControls } from '@zxing/browser';
import { useEffect, useRef, useState } from 'react';

import type { BoxItem } from '../types';
import { triggerHaptic } from '../utils/haptics';
import { getBoxIdFromQrValue } from '../utils/qrScanner';

export type TabletQrScannerLabels = {
  found: string;
  loading: string;
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
  const boxesRef = useRef(boxes);
  const onSelectBoxRef = useRef(onSelectBox);
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const scannerControlsRef = useRef<IScannerControls | null>(null);
  const [isScanning, setIsScanning] = useState(autoStart);
  const [isStarting, setIsStarting] = useState(autoStart);
  const [message, setMessage] = useState<string | null>(null);

  boxesRef.current = boxes;
  onSelectBoxRef.current = onSelectBox;

  useEffect(() => {
    if (!isScanning) {
      stopQrScanner(scannerControlsRef);
      setIsStarting(false);
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

            const scannedBoxId = getBoxIdFromQrValue(result.getText(), boxesRef.current);
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
        setIsStarting(false);
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
  }, [isScanning, found, permission, secureContext, unsupported]);

  return (
    <section className={isScanning ? 'tablet-scanner-panel is-scanning' : 'tablet-scanner-panel'}>
      <button
        className="scanner-preview"
        type="button"
        aria-label={isScanning ? (isStarting ? labels.loading : stop) : start}
        onClick={() => {
          setMessage(null);
          setIsScanning((current) => {
            const next = !current;
            setIsStarting(next);
            return next;
          });
        }}
      >
        {isScanning ? (
          <>
            <video ref={videoRef} muted playsInline />
            <span className="scanner-live-label" aria-live="polite">{isStarting ? labels.loading : stop}</span>
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
