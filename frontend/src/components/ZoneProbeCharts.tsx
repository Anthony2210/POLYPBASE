import { type FormEvent, useCallback, useEffect, useId, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';

import { getOrganizationResourceSignal } from '../api/client';
import { useAnchoredPopover } from '../hooks/useAnchoredPopover';
import useLiveWindowEnd, { useDocumentVisible } from '../hooks/useLiveWindowEnd';
import useProbeReadings from '../hooks/useProbeReadings';
import type { Translator } from '../i18n';
import type { ThermalZone, ZoneProbeReadings } from '../types';
import { getDocumentLocale } from '../utils/dateFormat';
import { formatTemperature } from '../utils/environmentFormat';
import { getErrorMessage } from '../utils/errors';
import { clampProbeWindow, MIN_PROBE_WINDOW_MS, type ProbeWindow } from '../utils/probeChart';
import {
  type ChartPeriod, DEFAULT_CHART_PERIOD, DEFAULT_PERIOD_HOURS, formatCompactPeriod, formatExactPeriod,
  periodWindow, RELATIVE_PERIOD_HOURS, type RelativePeriodHours,
} from '../utils/probePeriod';
import { projectProbeReadings } from '../utils/probeReadings';
import {
  buildProbeAverage, buildProbeSeries, seriesTemperatureDomain, type ProbeSeries,
} from '../utils/probeSeries';
import PolypbaseIcon from './PolypbaseIcon';
import ProbeSeriesChart, { averageSeriesStyle, seriesClassName, type ProbeSeriesChartLabels } from './ProbeSeriesChart';

const HOUR_MS = 3_600_000;
/** Display windows only: they carry no scientific threshold. */
export const RECENT_TREND_HOURS = 6;
export const DEFAULT_CHART_HOURS = DEFAULT_PERIOD_HOURS;
const MAX_RANGE_MS = 366 * 24 * HOUR_MS;
const TREND_REFRESH_MS = 60_000;
const CHART_REFRESH_MS = 5 * 60_000;
const TREND_HEIGHT = 136;
const CHART_HEIGHT = 320;

function collectedProbes(zone: ThermalZone) {
  return zone.probes.filter((probe) => probe.is_collected)
    .map((probe) => ({ id: probe.id, code: probe.code, location: probe.location ?? '', is_active: probe.is_active ?? true }));
}

function parseTarget(value: string | null | undefined) {
  if (value === null || value === undefined) return null;
  const parsed = Number.parseFloat(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function chartLabels(t: Translator): ProbeSeriesChartLabels {
  return {
    target: t('targetTemperature'), mean: t('probeIntervalMean'),
    minimum: t('minTemperature'), maximum: t('maxTemperature'), unit: '°C',
  };
}

/** Short visible label and full name of each rolling period. */
const PERIOD_OPTION_KEYS: Partial<Record<RelativePeriodHours, {
  short: 'probePeriod_24h' | 'probePeriod_7d' | 'probePeriod_30d';
  full: 'probeRangeReset' | 'probePeriodLast7Days' | 'probePeriodLast30Days';
}>> = {
  24: { short: 'probePeriod_24h', full: 'probeRangeReset' },
  168: { short: 'probePeriod_7d', full: 'probePeriodLast7Days' },
  720: { short: 'probePeriod_30d', full: 'probePeriodLast30Days' },
};

/** datetime-local works in the browser's zone, like every date shown by the application. */
export function toLocalInputValue(time: number) {
  const date = new Date(time);
  const pad = (value: number) => String(value).padStart(2, '0');
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

export function parseRangeInputs(startValue: string, endValue: string, now: number): ProbeWindow | null {
  const start = new Date(startValue).getTime();
  const end = new Date(endValue).getTime();
  if (!Number.isFinite(start) || !Number.isFinite(end)) return null;
  if (end <= start || end > now + 60_000 || end - start > MAX_RANGE_MS || end - start < MIN_PROBE_WINDOW_MS) return null;
  return { start, end: Math.min(end, now) };
}

function summaryLabel(series: readonly ProbeSeries[], heading: string, t: Translator) {
  const facts = series.map((entry) => (entry.minimum === null || entry.maximum === null
    ? `${entry.code}: ${t('probeNoReadings')}`
    : `${entry.code}: ${t('probeChartSummary').replace('{min}', formatTemperature(entry.minimum)).replace('{max}', formatTemperature(entry.maximum))}`));
  return `${heading}. ${facts.join(' ')}`;
}

export type InitialChartRead = { settled: boolean; data: ZoneProbeReadings | null };

/**
 * The location chart's read. Its first live response also feeds the trend and
 * the probe status, so opening a location starts one series request, not three.
 */
export function useZoneChartReadings(zone: ThermalZone, enabled: boolean) {
  const [period, setPeriod] = useState<ChartPeriod>(DEFAULT_CHART_PERIOD);
  const custom = period.kind === 'custom' ? period.window : null;
  const setCustom = (range: ProbeWindow | null) => {
    setPeriod(range ? { kind: 'custom', window: range } : DEFAULT_CHART_PERIOD);
  };
  const isDefaultPeriod = period.kind === 'relative' && period.hours === DEFAULT_PERIOD_HOURS;
  const liveEnd = useLiveWindowEnd(CHART_REFRESH_MS, enabled && custom === null);
  const window = useMemo(() => (enabled ? periodWindow(period, liveEnd) : null), [enabled, liveEnd, period]);
  const readings = useProbeReadings(zone.id, window);
  const contextSignal = getOrganizationResourceSignal();
  // The outcome is recorded once per zone and context: a later chart retry or
  // window change must not put the trend and the status back into waiting.
  const [firstOutcome, setFirstOutcome] = useState<{
    context: AbortSignal; zoneId: number; data: ZoneProbeReadings | null;
  } | null>(null);
  const outcome = firstOutcome?.context === contextSignal && firstOutcome.zoneId === zone.id ? firstOutcome : null;
  useEffect(() => {
    if (outcome !== null || contextSignal.aborted) return;
    if (!isDefaultPeriod || readings.error !== null) setFirstOutcome({ context: contextSignal, zoneId: zone.id, data: null });
    else if (readings.data) setFirstOutcome({ context: contextSignal, zoneId: zone.id, data: readings.data });
  }, [contextSignal, isDefaultPeriod, outcome, readings.data, readings.error, zone.id]);
  const initial = useMemo<InitialChartRead>(
    () => ({ settled: outcome !== null || !enabled, data: outcome?.data ?? null }),
    [enabled, outcome],
  );
  return { period, setPeriod, custom, setCustom, readings, initial };
}

export type ZoneChartReadings = ReturnType<typeof useZoneChartReadings>;

function recentSeed(data: ZoneProbeReadings | null) {
  // A partial response is not evidence for the whole trend window.
  if (data?.resolution_minutes !== 1 || data.coverage_complete === false) return null;
  const end = Date.parse(data.end);
  const window = { start: end - RECENT_TREND_HOURS * HOUR_MS, end };
  return Date.parse(data.start) <= window.start ? { data: projectProbeReadings(data, window), window } : null;
}

/** Compact individual readings of the last hours, one curve per probe. */
export function ZoneThermalTrend({ initial, zone, t }: { initial: InitialChartRead; zone: ThermalZone; t: Translator }) {
  const end = useLiveWindowEnd(TREND_REFRESH_MS);
  const [firstEnd] = useState(end);
  const range = useMemo(() => ({ start: end - RECENT_TREND_HOURS * HOUR_MS, end }), [end]);
  const seed = useMemo(() => recentSeed(initial.data), [initial.data]);
  // The trend polls alone from its first refresh, or as soon as the chart cannot
  // supply it. A hidden page postpones that fallback until it is visible again.
  const needsFallback = initial.settled && seed === null;
  const visible = useDocumentVisible();
  const [fallbackStarted, setFallbackStarted] = useState(false);
  useEffect(() => {
    if (needsFallback && visible) setFallbackStarted(true);
  }, [needsFallback, visible]);
  const needsOwnRead = end !== firstEnd || fallbackStarted;
  const own = useProbeReadings(zone.id, needsOwnRead ? range : null);
  const readings = {
    data: own.data ?? seed?.data ?? null,
    window: own.data ? own.window : seed?.window ?? range,
    isLoading: needsOwnRead ? own.isLoading : !initial.settled || needsFallback,
    error: own.error,
    retry: own.retry,
  };
  const fallback = useMemo(() => collectedProbes(zone), [zone]);
  const series = useMemo(() => buildProbeSeries(readings.data, fallback), [fallback, readings.data]);
  const target = parseTarget(readings.data ? readings.data.target_temperature_c : zone.target_temperature_c);
  const domain = useMemo(() => seriesTemperatureDomain(series, target), [series, target]);
  const locale = getDocumentLocale();
  const hasReadings = series.some((entry) => entry.readings.length > 0);

  return (
    <div className="zone-thermal-trend" aria-busy={readings.isLoading && !readings.data}>
      <div className="probe-series-frame">
        <ProbeSeriesChart ariaLabel={summaryLabel(series, t('probeReadingsTitle'), t)} domain={domain} height={TREND_HEIGHT}
          labels={chartLabels(t)} locale={locale} series={series} target={target} window={readings.window} />
        {readings.data && !hasReadings ? <p className="probe-series-state"><span>{t('probeNoReadings')}</span></p> : null}
      </div>
      {readings.error ? (
        <div className="probe-series-status" role="status">
          <p>{getErrorMessage(readings.error)}</p>
          <button className="secondary-button" type="button" onClick={readings.retry}>{t('probeRetry')}</button>
        </div>
      ) : null}

    </div>
  );
}

/** One combined chart of the probes of a location, directly on its page. */
export function ZoneProbeChart({ chart, zone, t }: { chart: ZoneChartReadings; zone: ThermalZone; t: Translator }) {
  const { period, setPeriod, custom, setCustom, readings } = chart;
  const fallback = useMemo(() => collectedProbes(zone), [zone]);
  const series = useMemo(() => buildProbeSeries(readings.data, fallback), [fallback, readings.data]);
  const target = null;
  const average = useMemo(() => buildProbeAverage(readings.data), [readings.data]);
  const latestAverage = average[average.length - 1];
  const domain = useMemo(() => seriesTemperatureDomain(series, target, average), [series, target, average]);
  const [hiddenIds, setHiddenIds] = useState<ReadonlySet<number>>(new Set());
  const [isEditing, setIsEditing] = useState(false);
  const [startValue, setStartValue] = useState('');
  const [endValue, setEndValue] = useState('');
  const [rangeError, setRangeError] = useState(false);
  const locale = getDocumentLocale();
  const shownWindow = readings.window;
  const resolution = readings.data?.resolution_minutes ?? 1;
  const hasData = series.some((entry) => entry.latest !== null) || average.length > 0;
  const dateFormat = useMemo(() => new Intl.DateTimeFormat(locale, { dateStyle: 'medium', timeStyle: 'short' }), [locale]);
  const isDefaultPeriod = period.kind === 'relative' && period.hours === DEFAULT_PERIOD_HOURS;
  const heading = t('probeReadingsTitle');
  const labels = { ...chartLabels(t), keyboardDescription: t('probeCombinedChartKeyboard'),
    ...(average.length ? { average: t('probeAverage'), averageContributors: t('probeAverageContributors') } : {}) };
  const exactPeriodId = useId();
  const exactPeriod = formatExactPeriod(shownWindow, locale);
  const customActive = isEditing || period.kind === 'custom';
  // Escape and the explicit actions return focus to the button; an outside click does not.
  const closeEditor = useCallback((restoreFocus = true) => {
    setIsEditing(false);
    if (restoreFocus) customButtonRef.current?.focus({ preventScroll: true });
  }, []);
  const { anchorRef: customButtonRef, panelRef, position, id: popoverId } = useAnchoredPopover<HTMLButtonElement>(isEditing, closeEditor, 'end');

  useLayoutEffect(() => {
    // Focus the panel, not a field: focusing a date input selects its first segment.
    if (isEditing && position.visibility === 'visible') panelRef.current?.focus({ preventScroll: true });
  }, [isEditing, panelRef, position.visibility]);

  function choosePeriod(hours: RelativePeriodHours) {
    setPeriod({ kind: 'relative', hours });
    setIsEditing(false);
  }

  function toggleEditor() {
    if (isEditing) closeEditor();
    else openEditor();
  }

  function openEditor() {
    const bounds = custom ?? shownWindow;
    setStartValue(toLocalInputValue(bounds.start));
    setEndValue(toLocalInputValue(bounds.end));
    setRangeError(false);
    setIsEditing(true);
  }

  function submitRange(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const range = parseRangeInputs(startValue, endValue, Date.now());
    if (!range) { setRangeError(true); return; }
    setCustom(range);
    closeEditor();
  }

  function zoomTo(start: number, end: number) {
    setCustom(clampProbeWindow(start, end, Date.now()));
    setIsEditing(false);
  }

  function resetRange() {
    setPeriod(DEFAULT_CHART_PERIOD);
    setIsEditing(false);
  }

  function toggleSeries(id: number) {
    setHiddenIds((current) => {
      const next = new Set(current);
      if (!next.delete(id)) next.add(id);
      return next;
    });
  }

  return (
    <section className="zone-page-section zone-functional-section zone-probe-chart-section" aria-busy={readings.isLoading}>
      <div className={`probe-series-progress${readings.isLoading && (!isDefaultPeriod || !readings.data) ? ' is-loading' : ''}`} aria-hidden="true" />
      <div className="zone-probe-chart-heading">
        <h2>{heading}</h2>
        <div className="zone-probe-chart-range" role="group" aria-label={t('probePeriod')} aria-describedby={exactPeriodId}>
          {RELATIVE_PERIOD_HOURS.map((hours) => {
            const keys = PERIOD_OPTION_KEYS[hours];
            if (!keys) return null;
            const pressed = !customActive && period.kind === 'relative' && period.hours === hours;
            return (
              <button key={hours} type="button" className="zone-probe-period-option" aria-pressed={pressed}
                aria-label={t(keys.full)} title={t(keys.full)} onClick={() => choosePeriod(hours)}>
                {t(keys.short)}
              </button>
            );
          })}
          <button ref={customButtonRef} type="button" className="zone-probe-period-option zone-probe-range-button"
            aria-pressed={customActive} aria-expanded={isEditing} aria-haspopup="dialog" aria-controls={isEditing ? popoverId : undefined} title={exactPeriod}
            aria-label={custom !== null ? t('probeRangeEdit').replace('{range}', exactPeriod) : t('probePeriodCustom')}
            onClick={toggleEditor}>
            <span>{custom !== null ? formatCompactPeriod(custom, locale, Date.now()) : t('probePeriodCustom')}</span>
            {custom !== null ? <PolypbaseIcon name="edit" size={14} /> : null}
          </button>
          <span id={exactPeriodId} className="sr-only">{exactPeriod}</span>
        </div>
      </div>
      {isEditing ? createPortal(
        <div className="anchored-popover zone-probe-range-popover" id={popoverId} ref={panelRef}
          role="dialog" aria-label={t('probePeriodCustom')} tabIndex={-1} style={position}>
          <form className="zone-probe-range-form" onSubmit={submitRange}>
            <label>
              <span>{t('probeRangeStart')}</span>
              <input type="datetime-local" value={startValue} onChange={(event) => setStartValue(event.target.value)}
                max={toLocalInputValue(Date.now())} required />
            </label>
            <label>
              <span>{t('probeRangeEnd')}</span>
              <input type="datetime-local" value={endValue} onChange={(event) => setEndValue(event.target.value)}
                max={toLocalInputValue(Date.now())} required />
            </label>
            {rangeError ? <p className="inline-error" role="alert">{t('probeRangeInvalid')}</p> : null}
            <div className="zone-probe-range-actions">
              <button className="secondary-button" type="button" onClick={() => closeEditor()}>{t('cancel')}</button>
              <button className="primary-button" type="submit">{t('probeRangeApply')}</button>
            </div>
          </form>
        </div>,
        document.body,
      ) : null}
      <div className="probe-series-frame">
        <ProbeSeriesChart ariaLabel={summaryLabel(series, `${heading}, ${exactPeriod}${average.length ? `, ${t('probeAverage')}` : ''}`, t)} average={average} domain={domain} height={CHART_HEIGHT}
          hiddenIds={hiddenIds} labels={labels} locale={locale} onResetZoom={custom !== null ? resetRange : undefined}
          onZoom={zoomTo} series={series} target={target} window={shownWindow} />
        {!hasData && !readings.isLoading && !readings.error ? <p className="probe-series-state"><span>{t('probeNoReadings')}</span></p> : null}
      </div>
      {readings.error ? (
        <div className="probe-series-status" role="status">
          <p>{getErrorMessage(readings.error)}</p>
          <button className="secondary-button" type="button" onClick={readings.retry}>{t('probeRetry')}</button>
        </div>
      ) : null}
      {series.length || average.length ? (
        <table className="probe-series-legend">
          <thead>
            <tr>
              <th scope="col">{t('probeLegendProbe')}</th>
              <th scope="col">{resolution > 1 ? t('probeLatestInterval').replace('{minutes}', String(resolution)) : t('probeLatestInWindow')}</th>
              <th scope="col">{t('minTemperature')}</th>
              <th scope="col">{t('maxTemperature')}</th>
            </tr>
          </thead>
          <tbody>
            {latestAverage ? (
              <tr>
                <th scope="row"><span className="probe-series-name"><i className="probe-series-average" style={averageSeriesStyle(series)} aria-hidden="true" />{t('probeAverage')}</span></th>
                <td>
                  <strong>{formatTemperature(latestAverage.mean)}</strong>
                </td>
                <td>-</td><td>-</td>
              </tr>
            ) : null}
            {series.map((entry) => {
              const label = entry.isActive ? entry.code : `${entry.code} (${t('probeInactive')})`;
              const hidden = hiddenIds.has(entry.id);
              const name = <span className="probe-series-name"><i className={seriesClassName(entry.slot)} aria-hidden="true" />{label}{entry.location ? <small>{entry.location}</small> : null}</span>;
              return (
                <tr key={entry.id} className={hidden ? 'is-hidden' : undefined}>
                  <th scope="row">
                    {series.length > 1 ? (
                      <button type="button" aria-pressed={!hidden} title={t('probeSeriesToggle').replace('{probe}', entry.code)} onClick={() => toggleSeries(entry.id)}>{name}</button>
                    ) : name}
                  </th>
                  <td>
                    {entry.latest ? (
                      <>
                        <strong>{formatTemperature(entry.latest.value)}</strong>
                        {' '}<time dateTime={new Date(entry.latest.time).toISOString()}>{dateFormat.format(entry.latest.time)}</time>
                      </>
                    ) : '-'}
                  </td>
                  <td>{entry.minimum === null ? '-' : formatTemperature(entry.minimum)}</td>
                  <td>{entry.maximum === null ? '-' : formatTemperature(entry.maximum)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      ) : null}
    </section>
  );
}
