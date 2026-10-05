import type { IScannerControls } from '@zxing/browser';
import { useEffect, useRef, useState } from 'react';

import type { BoxItem } from '../types';
import { triggerHaptic } from '../utils/haptics';
import { getBoxCodeFromQrValue, getBoxIdFromQrValue } from '../utils/qrScanner';

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
  onResolveBoxCode,
  onSelectBox,
}: {
  autoStart?: boolean;
  boxes: BoxItem[];
  labels: TabletQrScannerLabels;
  // Looks a code up when the scanned box is not among the loaded ones.
  onResolveBoxCode?: (code: string) => Promise<number | null>;
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
  const onResolveBoxCodeRef = useRef(onResolveBoxCode);
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const scannerControlsRef = useRef<IScannerControls | null>(null);
  const [isScanning, setIsScanning] = useState(autoStart);
  const [isStarting, setIsStarting] = useState(autoStart);
  const [message, setMessage] = useState<string | null>(null);

  boxesRef.current = boxes;
  onSelectBoxRef.current = onSelectBox;
  onResolveBoxCodeRef.current = onResolveBoxCode;

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
        let isResolvingCode = false;
        const unknownCodes = new Set<string>();

        function selectScannedBox(scannedBoxId: number) {
          hasDetectedBox = true;
          triggerHaptic([10, 34, 12]);
          setMessage(found);
          setIsScanning(false);
          onSelectBoxRef.current(scannedBoxId);
        }

        const controls = await reader.decodeFromConstraints(
          {
            video: { facingMode: { ideal: 'environment' } },
            audio: false,
          },
          video,
          (result) => {
            if (!result || isCancelled || hasDetectedBox || isResolvingCode) return;

            const scannedValue = result.getText();
            const scannedBoxId = getBoxIdFromQrValue(scannedValue, boxesRef.current);
            if (scannedBoxId != null) {
              selectScannedBox(scannedBoxId);
              return;
            }

            // Not among the loaded boxes: look the code up once per distinct value.
            const resolveBoxCode = onResolveBoxCodeRef.current;
            const scannedCode = getBoxCodeFromQrValue(scannedValue);
            if (!resolveBoxCode || !scannedCode || unknownCodes.has(scannedCode)) return;

            isResolvingCode = true;
            void resolveBoxCode(scannedCode)
              .then((resolvedBoxId) => {
                if (resolvedBoxId == null) {
                  unknownCodes.add(scannedCode);
                  return;
                }
                if (!isCancelled && !hasDetectedBox) selectScannedBox(resolvedBoxId);
              })
              .catch(() => {
                unknownCodes.add(scannedCode);
              })
              .finally(() => {
                isResolvingCode = false;
              });
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
