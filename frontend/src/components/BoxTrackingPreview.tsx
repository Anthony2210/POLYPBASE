import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { X } from 'lucide-react';

import { apiGet } from '../api/client';
import { useAnchoredPopover } from '../hooks/useAnchoredPopover';
import type { Language, Translator } from '../i18n';
import type { BoxDetail } from '../types';
import { getErrorMessage } from '../utils/errors';
import SkeletonRows from './SkeletonRows';

const TrackingChart = lazy(async () => {
  const { default: BoxTrackingChart, buildLifecycleEvents } = await import('./BoxTrackingChart');
  return { default: function PreviewChart({ detail, language, t }: { detail: BoxDetail; language: Language; t: Translator }) {
    const events = useMemo(() => buildLifecycleEvents(detail.lineage, detail.movements, {
      movementEvent: t('movementEvent'), subcultureEvent: t('subcultureEvent'),
    }), [detail, t]);
    return <BoxTrackingChart compact measurements={detail.biological_measurements} locations={detail.locations} events={events} language={language} labels={{
      chartTitle: t('chartTitle'), chartEmpty: t('chartEmpty'), polyps: t('polyps'),
      ephyraeFull: t('ephyraeFull'), missingReading: t('chartMissingReading'),
      historyEnteredBy: t('historyEnteredBy'), historyObservation: t('historyObservation'), salinityFull: t('salinityFull'),
    }} />;
  } };
});

export default function BoxTrackingPreview({ boxId, code, speciesName, language, onOpenBox, t }: {
  boxId: number;
  code: string;
  speciesName: string;
  language: Language;
  onOpenBox: (boxId: number, code: string) => void;
  t: Translator;
}) {
  const [isOpen, setIsOpen] = useState(false);
  const openTimer = useRef<number>();
  const closeTimer = useRef<number>();
  const pointerInside = useRef(false);
  const suppressFocus = useRef(false);
  const clearTimers = useCallback(() => {
    window.clearTimeout(openTimer.current);
    window.clearTimeout(closeTimer.current);
  }, []);
  const close = useCallback((restoreFocus = false) => {
    clearTimers();
    pointerInside.current = false;
    setIsOpen(false);
    if (restoreFocus) {
      suppressFocus.current = true;
      anchorRef.current?.focus({ preventScroll: true });
    }
  }, [clearTimers]);
  const { anchorRef, panelRef, position, id } = useAnchoredPopover<HTMLAnchorElement>(isOpen, close);

  useEffect(() => clearTimers, [clearTimers]);

  function enter() {
    pointerInside.current = true;
    clearTimers();
    openTimer.current = window.setTimeout(() => setIsOpen(true), 280);
  }
  function scheduleClose() {
    window.clearTimeout(openTimer.current);
    window.clearTimeout(closeTimer.current);
    closeTimer.current = window.setTimeout(() => {
      if (!pointerInside.current && !anchorRef.current?.contains(document.activeElement) && !panelRef.current?.contains(document.activeElement)) close();
    }, 220);
  }

  return (
    <>
      <a
        ref={anchorRef}
        className="box-tracking-preview-link"
        href={`/boxes/${encodeURIComponent(code)}`}
        aria-haspopup="dialog"
        aria-expanded={isOpen}
        aria-controls={isOpen ? id : undefined}
        onPointerEnter={(event) => { if (event.pointerType === 'mouse') enter(); }}
        onPointerLeave={() => { pointerInside.current = false; scheduleClose(); }}
        onFocus={() => { if (!suppressFocus.current) { clearTimers(); setIsOpen(true); } }}
        onBlur={() => { suppressFocus.current = false; scheduleClose(); }}
        onKeyDown={(event) => {
          if (event.key === 'ArrowDown') {
            event.preventDefault();
            setIsOpen(true);
            window.requestAnimationFrame(() => panelRef.current?.querySelector<HTMLButtonElement>('button')?.focus());
          }
          if (event.key === 'Tab' && !event.shiftKey && isOpen) {
            event.preventDefault();
            panelRef.current?.querySelector<HTMLButtonElement>('button')?.focus();
          }
        }}
        onClick={(event) => {
          close();
          if (event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
          event.preventDefault();
          onOpenBox(boxId, code);
        }}
      >{code}</a>
      {isOpen ? createPortal(
        <div
          className="anchored-popover box-tracking-preview"
          ref={panelRef}
          id={id}
          role="dialog"
          aria-labelledby={`${id}-title`}
          style={position}
          onPointerEnter={() => { pointerInside.current = true; clearTimers(); }}
          onPointerLeave={() => { pointerInside.current = false; scheduleClose(); }}
          onFocusCapture={clearTimers}
          onBlurCapture={scheduleClose}
          onKeyDown={(event) => {
            if (event.key !== 'Tab') return;
            const items = Array.from(panelRef.current?.querySelectorAll<HTMLElement>('button:not(:disabled), [tabindex="0"]') ?? []);
            if ((event.shiftKey && event.target === items[0]) || (!event.shiftKey && event.target === items[items.length - 1])) {
              close(true);
              if (event.shiftKey) event.preventDefault();
            }
          }}
        >
          <header className="box-tracking-preview-heading">
            <div><strong id={`${id}-title`}>{code}</strong><span>{speciesName}</span></div>
            <button type="button" aria-label={t('close')} onClick={() => close(true)}><X size={17} aria-hidden="true" /></button>
          </header>
          <BoxTrackingPreviewContent key={boxId} boxId={boxId} language={language} t={t} />
        </div>, document.body,
      ) : null}
    </>
  );
}

function BoxTrackingPreviewContent({ boxId, language, t }: {
  boxId: number;
  language: Language;
  t: Translator;
}) {
  const [detail, setDetail] = useState<BoxDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    setError(null);
    void apiGet<BoxDetail>(`/api/boxes/${boxId}/`, { signal: controller.signal })
      .then((result) => { if (!controller.signal.aborted) setDetail(result); })
      .catch((requestError) => { if (!controller.signal.aborted) setError(getErrorMessage(requestError)); });
    return () => controller.abort();
  }, [attempt, boxId]);

  const loading = <div className="box-tracking-preview-state" role="status" aria-label={t('loading')}><SkeletonRows count={3} /></div>;
  if (error) return <div className="box-tracking-preview-state" role="alert"><p>{error}</p><button type="button" onClick={() => setAttempt((current) => current + 1)}>{t('lineageRetry')}</button></div>;
  if (!detail) return loading;
  const hasTimeline = detail.biological_measurements.length > 0
    || detail.locations.length > 0
    || detail.movements.length > 0
    || detail.lineage.parents.some((relation) => relation.event != null)
    || detail.lineage.children.some((relation) => relation.event != null);
  return (
    <>
      {hasTimeline ? (
        <Suspense fallback={loading}><TrackingChart detail={detail} language={language} t={t} /></Suspense>
      ) : <div className="box-tracking-preview-state"><p>{t('noMeasurementHistory')}</p></div>}
    </>
  );
}
