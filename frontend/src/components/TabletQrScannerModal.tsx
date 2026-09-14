import { useEffect, useLayoutEffect, useRef } from 'react';

import type { BoxItem } from '../types';
import ModalPortal from './ModalPortal';
import PolypbaseIcon from './PolypbaseIcon';
import TabletQrScanner, { type TabletQrScannerLabels } from './TabletQrScanner';

const FOCUSABLE_SELECTOR =
  'button:not(:disabled), [href], input:not(:disabled), select:not(:disabled), textarea:not(:disabled), [tabindex]:not([tabindex="-1"])';

type TabletQrScannerModalLabels = TabletQrScannerLabels & {
  close: string;
  description: string;
  title: string;
};

export default function TabletQrScannerModal({
  boxes,
  labels,
  onClose,
  onSelectBox,
}: {
  boxes: BoxItem[];
  labels: TabletQrScannerModalLabels;
  onClose: () => void;
  onSelectBox: (id: number) => void;
}) {
  const dialogRef = useRef<HTMLElement | null>(null);
  const closeButtonRef = useRef<HTMLButtonElement | null>(null);
  const returnFocusRef = useRef<HTMLElement | null>(null);

  useLayoutEffect(() => {
    returnFocusRef.current = document.activeElement as HTMLElement | null;
    closeButtonRef.current?.focus();
  }, []);

  useEffect(() => {
    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === 'Escape') {
        onClose();
        return;
      }
      if (event.key !== 'Tab') return;

      const dialog = dialogRef.current;
      if (!dialog) return;
      const focusable = Array.from(dialog.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR));
      if (!focusable.length) return;

      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      const active = document.activeElement;
      if (event.shiftKey) {
        if (active === first || !dialog.contains(active)) {
          event.preventDefault();
          last.focus();
        }
      } else if (active === last || !dialog.contains(active)) {
        event.preventDefault();
        first.focus();
      }
    }

    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [onClose]);

  useEffect(() => () => {
    returnFocusRef.current?.focus();
  }, []);

  return (
    <ModalPortal>
      <div className="modal-backdrop tablet-scanner-backdrop" role="presentation" onMouseDown={onClose}>
        <section
          ref={dialogRef}
          className="tablet-scanner-modal"
          role="dialog"
          aria-modal="true"
          aria-labelledby="tablet-scanner-title"
          aria-describedby="tablet-scanner-description"
          onMouseDown={(event) => event.stopPropagation()}
        >
          <header className="tablet-scanner-modal-heading">
            <div>
              <h2 id="tablet-scanner-title">{labels.title}</h2>
              <p id="tablet-scanner-description">{labels.description}</p>
            </div>
            <button
              ref={closeButtonRef}
              className="icon-button tablet-scanner-close"
              type="button"
              aria-label={labels.close}
              title={labels.close}
              onClick={onClose}
            >
              <PolypbaseIcon name="close" size={22} />
            </button>
          </header>

          <TabletQrScanner
            autoStart
            boxes={boxes}
            labels={labels}
            onSelectBox={onSelectBox}
          />
        </section>
      </div>
    </ModalPortal>
  );
}
