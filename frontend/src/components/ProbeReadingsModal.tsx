import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';

import useProbeReadings from '../hooks/useProbeReadings';
import type { ThermalZone } from '../types';
import { getDocumentLocale } from '../utils/dateFormat';
import { getErrorMessage } from '../utils/errors';
import {
  canShiftProbeWindowForward, clampProbeWindow, PROBE_PERIODS, probePeriodWindow,
  probeTemperatureDomain, shiftProbeWindow, type ProbePeriodId, type ProbeWindow,
} from '../utils/probeChart';
import ModalPortal from './ModalPortal';
import PolypbaseIcon from './PolypbaseIcon';
import ProbeTemperatureChart from './ProbeTemperatureChart';

type TFunction = (key: string) => string;

export default function ProbeReadingsModal({ zone, onClose, t }: {
  zone: ThermalZone;
  onClose: () => void;
  t: TFunction;
}) {
  const dialogRef = useRef<HTMLElement>(null);
  const periodEnd = useRef(Date.now());
  const [period, setPeriod] = useState<ProbePeriodId>('24h');
  const [viewWindow, setViewWindow] = useState<ProbeWindow>(() => probePeriodWindow('24h', periodEnd.current));
  const [isZoomed, setIsZoomed] = useState(false);
  const [cursorTime, setCursorTime] = useState<number | null>(null);
  const readings = useProbeReadings(zone.id, viewWindow);
  const { data, isLoading } = readings;
  const locale = getDocumentLocale();

  useLayoutEffect(() => {
    const root = document.getElementById('root');
    const wasInert = root?.inert ?? false;
    if (root) root.inert = true;
    dialogRef.current?.focus({ preventScroll: true });
    return () => { if (root) root.inert = wasInert; };
  }, []);

  useEffect(() => {
    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === 'Escape') { onClose(); return; }
      if (event.key !== 'Tab') return;
      const dialog = dialogRef.current;
      if (!dialog) return;
      const controls = Array.from(dialog.querySelectorAll<HTMLElement>(
        'button:not(:disabled), [tabindex="0"]',
      )).filter((control) => control.getClientRects().length > 0);
      const first = controls[0];
      const last = controls[controls.length - 1];
      if (!first || !last) return;
      if (event.shiftKey && (document.activeElement === first || document.activeElement === dialog || !dialog.contains(document.activeElement))) {
        event.preventDefault(); last.focus();
      } else if (!event.shiftKey && (document.activeElement === last || document.activeElement === dialog || !dialog.contains(document.activeElement))) {
        event.preventDefault(); first.focus();
      }
    }
    globalThis.addEventListener('keydown', handleKeyDown);
    return () => globalThis.removeEventListener('keydown', handleKeyDown);
  }, [onClose]);

  const targetValue = data ? data.target_temperature_c : zone.target_temperature_c;
  const parsedTarget = targetValue === null ? null : Number.parseFloat(targetValue);
  const target = Number.isFinite(parsedTarget) ? parsedTarget : null;
  const temperatureDomain = useMemo(() => {
    const values: number[] = [];
    for (const probe of data?.probes ?? []) {
      if (probe.point_sources) {
        for (const sources of probe.point_sources) for (const source of sources) values.push(source[4], source[5]);
      } else for (const [, average, minimum, maximum] of probe.points) values.push(minimum ?? average, maximum ?? average);
    }
    return probeTemperatureDomain(values, target);
  }, [data, target]);

  function choosePeriod(next: ProbePeriodId) {
    const range = probePeriodWindow(next, periodEnd.current);
    if (range.start === viewWindow.start && range.end === viewWindow.end) readings.revalidate();
    setPeriod(next);
    setIsZoomed(false);
    setCursorTime(null);
    setViewWindow(range);
  }
  const resetZoom = useCallback(() => {
    const range = probePeriodWindow(period, periodEnd.current);
    if (range.start === viewWindow.start && range.end === viewWindow.end) readings.revalidate();
    setIsZoomed(false);
    setCursorTime(null);
    setViewWindow(range);
  }, [period, viewWindow.start, viewWindow.end, readings.revalidate]);
  const zoomTo = useCallback((start: number, end: number) => {
    const next = clampProbeWindow(start, end, Date.now());
    const base = probePeriodWindow(period, periodEnd.current);
    setIsZoomed(next.start !== base.start || next.end !== base.end);
    setViewWindow(next);
  }, [period]);
  function shift(direction: -1 | 1) {
    setCursorTime(null);
    setViewWindow((current) => shiftProbeWindow(current, direction, Date.now()));
  }

  const format = new Intl.DateTimeFormat(locale, { dateStyle: 'medium', timeStyle: 'short' });
  const chartLabels = {
    target: t('targetTemperature'), minimum: t('minTemperature'), maximum: t('maxTemperature'),
    noReadings: t('probeNoReadings'), latest: t('probeLatestInWindow'),
    keyboardDescription: t('probeChartKeyboardDescription'),
    aggregateAverage: t('probeAggregateAverage'), partialCoverage: t('probePartialCoverage'), previewCoverage: t('probeCoveragePreview'),
    aggregateCoverage: t('probeAggregateCoverage'), measurementCount: t('probeMeasurementCount'), rawReading: t('probeRawReading'),
    summary: (minimum: string, maximum: string) => t('probeChartSummary').replace('{min}', minimum).replace('{max}', maximum),
  };
  const probes = data?.probes ?? zone.probes.filter((probe) => probe.is_collected).map((probe) => ({ ...probe, points: [], point_sources: undefined }));

  return (
    <ModalPortal>
      <div className="modal-backdrop" onMouseDown={onClose}>
        <section ref={dialogRef} className="probe-readings-modal" tabIndex={-1} role="dialog"
          aria-modal="true" aria-labelledby="probe-readings-title" onMouseDown={(event) => event.stopPropagation()}>
          <div className={`probe-readings-progress${isLoading ? ' is-loading' : ''}`} aria-hidden="true" />
          <div className="probe-readings-heading">
            <div><h2 id="probe-readings-title">{t('probeReadingsTitle')}</h2><p>{zone.name}</p></div>
            <button className="modal-close-button" type="button" aria-label={t('close')} onClick={onClose}>
              <PolypbaseIcon name="close" size={18} />
            </button>
          </div>
          <div className="probe-readings-toolbar">
            <div className="chart-periods" role="group" aria-label={t('probePeriod')}>
              {PROBE_PERIODS.map((entry) => (
                <button key={entry.id} type="button" className={!isZoomed && period === entry.id ? 'is-active' : ''}
                  aria-pressed={!isZoomed && period === entry.id} onClick={() => choosePeriod(entry.id)}>
                  {t(`probePeriod_${entry.id}`)}
                </button>
              ))}
            </div>
            <div className="probe-readings-navigation">
              <button className="icon-button" type="button" aria-label={t('probeWindowPrevious')}
                title={t('probeWindowPrevious')} onClick={() => shift(-1)}>
                <PolypbaseIcon name="chevron-left" size={18} />
              </button>
              <button className="icon-button" type="button" aria-label={t('probeWindowNext')}
                title={t('probeWindowNext')} disabled={!canShiftProbeWindowForward(viewWindow, Date.now())}
                onClick={() => shift(1)}><PolypbaseIcon name="chevron-right" size={18} /></button>
              <span className="probe-reset-slot">
                {isZoomed ? <button className="secondary-button" type="button" onClick={resetZoom}>{t('probeResetZoom')}</button> : null}
              </span>
            </div>
          </div>
          <p className="probe-readings-range">
            {format.format(readings.window.start)} – {format.format(readings.window.end)}
          </p>
          <div className="probe-readings-charts" aria-busy={isLoading}>
            {probes.map((probe) => (
              <ProbeTemperatureChart key={probe.id} code={probe.is_active ? probe.code : `${probe.code} (${t('probeInactive')})`}
                locale={locale} labels={chartLabels} points={probe.points} pointSources={probe.point_sources} resolutionMinutes={data?.resolution_minutes ?? 1}
                target={target} temperatureDomain={temperatureDomain} window={readings.window}
                cursorTime={cursorTime} onCursorTimeChange={setCursorTime} onZoom={zoomTo} onResetZoom={resetZoom}
                isInitialLoading={!data && isLoading} isPartialPreview={data?.coverage_complete === false} />
            ))}
            {!isLoading && probes.length === 0 ? <p className="probe-readings-state">{t('probeNoCollectedProbe')}</p> : null}
          </div>
          {readings.error ? (
            <div className="probe-readings-status" role="status">
              <p>{getErrorMessage(readings.error)}</p>
              <button className="secondary-button" type="button" onClick={readings.retry}>{t('probeRetry')}</button>
            </div>
          ) : null}
        </section>
      </div>
    </ModalPortal>
  );
}
