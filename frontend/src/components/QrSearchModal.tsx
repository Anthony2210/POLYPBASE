import { type KeyboardEvent, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';

import type { Translator } from '../i18n';
import type { BoxItem } from '../types';
import type { BoxCollectionState } from '../utils/boxCollection';
import { filterBoxes } from '../utils/boxLookup';
import BoxSearchStatus from './BoxSearchStatus';
import ModalPortal from './ModalPortal';
import PolypbaseIcon from './PolypbaseIcon';
import SearchField from './SearchField';
import TabletQrScanner from './TabletQrScanner';

export default function QrSearchModal({
  boxes,
  boxCollectionStatus,
  onRequestBoxCollection,
  onRetryBoxCollection,
  onResolveBoxCode,
  t,
  onClose,
  onSelectBox,
}: {
  boxes: BoxItem[];
  boxCollectionStatus: BoxCollectionState['status'];
  onRequestBoxCollection: () => void;
  onRetryBoxCollection: () => void;
  onResolveBoxCode: (code: string) => Promise<number | null>;
  t: Translator;
  onClose: () => void;
  onSelectBox: (boxId: number) => void;
}) {
  const dialogRef = useRef<HTMLElement | null>(null);
  const returnFocusRef = useRef<HTMLElement | null>(null);
  const focusGenerationRef = useRef(0);
  const [query, setQuery] = useState('');
  const [highlightedIndex, setHighlightedIndex] = useState(0);
  const isBoxCollectionReady = boxCollectionStatus === 'ready';
  const hasQuery = query.trim() !== '';
  const results = useMemo(
    () => (hasQuery && isBoxCollectionReady ? filterBoxes(boxes, query).slice(0, 5) : []),
    [boxes, hasQuery, isBoxCollectionReady, query],
  );

  // Scanning needs no list; typing a search does.
  useEffect(() => {
    if (hasQuery) onRequestBoxCollection();
  }, [hasQuery, onRequestBoxCollection]);

  useLayoutEffect(() => {
    if (focusGenerationRef.current === 0) {
      returnFocusRef.current = document.activeElement instanceof HTMLElement
        ? document.activeElement
        : null;
    }
    const generation = ++focusGenerationRef.current;
    dialogRef.current?.querySelector<HTMLButtonElement>('.modal-close-button')?.focus();
    return () => {
      // StrictMode replays setup before this runs; do not restore or recapture its opener.
      queueMicrotask(() => {
        if (focusGenerationRef.current !== generation || document.activeElement !== document.body) return;
        if (returnFocusRef.current?.isConnected) returnFocusRef.current?.focus({ preventScroll: true });
      });
    };
  }, []);

  useEffect(() => {
    function handleKeyDown(event: globalThis.KeyboardEvent) {
      if (event.key === 'Escape') {
        event.preventDefault();
        onClose();
        return;
      }
      if (event.key !== 'Tab') return;

      const dialog = dialogRef.current;
      if (!dialog) return;
      const focusable = Array.from(dialog.querySelectorAll<HTMLElement>(
        'button:not(:disabled), input:not(:disabled), [tabindex]:not([tabindex="-1"])',
      ));
      if (!focusable.length) return;

      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      const active = document.activeElement;
      if (event.shiftKey && (active === first || !dialog.contains(active))) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && (active === last || !dialog.contains(active))) {
        event.preventDefault();
        first.focus();
      }
    }

    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [onClose]);

  function selectHighlightedResult() {
    const selected = results[highlightedIndex] ?? results[0];
    if (selected) {
      onSelectBox(selected.id);
      return;
    }
    // While the full list loads, submitting an exact box code still opens that box.
    if (hasQuery && !isBoxCollectionReady) {
      void onResolveBoxCode(query)
        .then((boxId) => {
          if (boxId != null) onSelectBox(boxId);
        })
        .catch(() => {
          // The search keeps its loading or retry state; nothing else to report.
        });
    }
  }

  function handleSearchKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (!results.length) return;
    if (event.key === 'ArrowDown') {
      event.preventDefault();
      setHighlightedIndex((current) => (current + 1) % results.length);
    } else if (event.key === 'ArrowUp') {
      event.preventDefault();
      setHighlightedIndex((current) => (current - 1 + results.length) % results.length);
    }
  }

  return (
    <ModalPortal>
      <div className="modal-backdrop qr-search-backdrop" role="presentation" onMouseDown={onClose}>
        <section
          ref={dialogRef}
          className="qr-search-modal"
          role="dialog"
          aria-modal="true"
          aria-labelledby="qr-search-title"
          onMouseDown={(event) => event.stopPropagation()}
        >
          <header className="qr-search-heading">
            <h2 id="qr-search-title">{t('searchOrScan')}</h2>
            <button className="modal-close-button" type="button" aria-label={t('close')} onClick={onClose}>
              <PolypbaseIcon name="close" size={18} />
            </button>
          </header>

          <div className="qr-search-content">
            <TabletQrScanner
              autoStart
              boxes={boxes}
              labels={{
                found: t('qrScannerFound'),
                loading: t('qrScannerLoading'),
                permission: t('qrScannerPermission'),
                secureContext: t('qrScannerSecureContext'),
                start: t('qrScannerStart'),
                stop: t('qrScannerStop'),
                unsupported: t('qrScannerUnsupported'),
              }}
              onResolveBoxCode={onResolveBoxCode}
              onSelectBox={onSelectBox}
            />

            <div className="qr-search-manual">
              <SearchField
                activeDescendant={results[highlightedIndex] ? `qr-search-result-${results[highlightedIndex].id}` : undefined}
                controls="qr-search-results"
                expanded={results.length > 0}
                labels={{ label: t('searchOrScan'), placeholder: t('searchPlaceholder') }}
                value={query}
                onChange={(value) => {
                  setQuery(value);
                  setHighlightedIndex(0);
                }}
                onKeyDown={handleSearchKeyDown}
                onSubmit={selectHighlightedResult}
              />

              {results.length > 0 ? (
                <div className="qr-search-results" id="qr-search-results" role="listbox">
                  <div className="qr-search-results-heading">
                    <span>{t('suggestions')}</span>
                    <span>{results.length}</span>
                  </div>
                  {results.map((box, index) => (
                    <button
                      key={box.id}
                      id={`qr-search-result-${box.id}`}
                      className={index === highlightedIndex ? 'qr-search-result is-selected' : 'qr-search-result'}
                      type="button"
                      role="option"
                      aria-selected={index === highlightedIndex}
                      onClick={() => onSelectBox(box.id)}
                    >
                      <span>
                        <strong>{box.global_code}</strong>
                        <small>{box.species.scientific_name}</small>
                      </span>
                      <small>{box.thermal_zone?.name ?? t('noZone')}</small>
                    </button>
                  ))}
                </div>
              ) : hasQuery && !isBoxCollectionReady ? (
                <BoxSearchStatus status={boxCollectionStatus} onRetry={onRetryBoxCollection} t={t} />
              ) : hasQuery ? (
                <p className="qr-search-empty">{t('searchNoResults')}</p>
              ) : null}
            </div>
          </div>
        </section>
      </div>
    </ModalPortal>
  );
}
