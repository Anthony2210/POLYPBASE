import { type ChangeEvent, type FormEvent, useEffect, useId, useLayoutEffect, useMemo, useRef, useState } from 'react';

import { getOrganizationResourceSignal } from '../api/client';
import useLiveWindowEnd, { useDocumentVisible } from '../hooks/useLiveWindowEnd';
import useProbeReadings from '../hooks/useProbeReadings';
import type { Translator } from '../i18n';
import type { ThermalZone, ZoneProbeReadings } from '../types';
import { getDocumentLocale } from '../utils/dateFormat';
import { formatSignedTemperature, formatTemperature } from '../utils/environmentFormat';
import { getErrorMessage } from '../utils/errors';
import { clampProbeWindow, MIN_PROBE_WINDOW_MS, type ProbeWindow } from '../utils/probeChart';
import {
  browserTimeZone, type ChartPeriod, DEFAULT_CHART_PERIOD, DEFAULT_PERIOD_HOURS, formatCompactPeriod, formatExactPeriod,
  isRelativePeriodHours, periodWindow, type RelativePeriodHours,
} from '../utils/probePeriod';
import { projectProbeReadings } from '../utils/probeReadings';
import {
  buildProbeAverage, buildProbeSeries, hasPartialCoverage, probeRecentChanges, seriesTemperatureDomain, type ProbeSeries,
} from '../utils/probeSeries';
import PolypbaseIcon from './PolypbaseIcon';
import ProbeSeriesChart, { seriesClassName, type ProbeSeriesChartLabels } from './ProbeSeriesChart';

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

const PERIOD_OPTION_KEYS: Record<RelativePeriodHours, 'probeRangeReset' | 'probePeriodLast7Days' | 'probePeriodLast30Days'> = {
  24: 'probeRangeReset', 168: 'probePeriodLast7Days', 720: 'probePeriodLast30Days',
};
const CUSTOM_PERIOD_VALUE = 'custom';

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
  const changes = useMemo(() => probeRecentChanges(series), [series]);
  const locale = getDocumentLocale();
  const timeFormat = useMemo(() => new Intl.DateTimeFormat(locale, { hour: '2-digit', minute: '2-digit' }), [locale]);
  const heading = t('temperatureTrendTitle').replace('{hours}', String(RECENT_TREND_HOURS));
  const hasReadings = series.some((entry) => entry.readings.length > 0);

  return (
    <div className="zone-thermal-trend" aria-busy={readings.isLoading && !readings.data}>
      <div className="probe-series-frame">
        <ProbeSeriesChart ariaLabel={summaryLabel(series, heading, t)} domain={domain} height={TREND_HEIGHT}
          labels={chartLabels(t)} locale={locale} series={series} target={target} window={readings.window} />
        {readings.data && !hasReadings ? <p className="probe-series-state"><span>{t('probeNoReadings')}</span></p> : null}
      </div>
      {readings.error ? (
        <div className="probe-series-status" role="status">
          <p>{getErrorMessage(readings.error)}</p>
          <button className="secondary-button" type="button" onClick={readings.retry}>{t('probeRetry')}</button>
        </div>
      ) : null}
      {changes.length ? (
        <p className="zone-thermal-changes">
          <small>{heading}</small>
          {changes.map((change) => (
            <span key={change.id}>
              {change.code} <strong>{formatSignedTemperature(change.delta)}°C</strong> <time dateTime={new Date(change.from).toISOString()}>{timeFormat.format(change.from)}</time> → <time dateTime={new Date(change.to).toISOString()}>{timeFormat.format(change.to)}</time>
            </span>
          ))}
        </p>
      ) : null}
    </div>
  );
}

/** One combined chart of the probes of a location, directly on its page. */
export function ZoneProbeChart({ chart, zone, t }: { chart: ZoneChartReadings; zone: ThermalZone; t: Translator }) {
  const { period, setPeriod, custom, setCustom, readings } = chart;
  const fallback = useMemo(() => collectedProbes(zone), [zone]);
  const series = useMemo(() => buildProbeSeries(readings.data, fallback), [fallback, readings.data]);
  const target = parseTarget(readings.data ? readings.data.target_temperature_c : zone.target_temperature_c);
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
  const isPartialPreview = !isDefaultPeriod && readings.data?.coverage_complete === false;
  const partial = hasPartialCoverage(series, shownWindow)
    || average.some((entry) => entry.start < shownWindow.start || entry.end > shownWindow.end);
  const heading = t('probeReadingsTitle');
  const labels = { ...chartLabels(t), keyboardDescription: t('probeCombinedChartKeyboard'),
    ...(average.length ? { average: t('probeAverage'), averageContributors: t('probeAverageContributors') } : {}) };
  const startInputRef = useRef<HTMLInputElement>(null);
  const rangeButtonRef = useRef<HTMLButtonElement>(null);
  const periodSelectRef = useRef<HTMLSelectElement>(null);
  const restoreRangeFocus = useRef(false);
  const exactPeriodId = useId();
  const exactPeriod = formatExactPeriod(shownWindow, locale);
  const timeZone = browserTimeZone();
  const periodValue = isEditing || period.kind === 'custom' ? CUSTOM_PERIOD_VALUE : String(period.hours);

  useLayoutEffect(() => {
    if (isEditing) startInputRef.current?.focus();
    else if (restoreRangeFocus.current) {
      restoreRangeFocus.current = false;
      (rangeButtonRef.current ?? periodSelectRef.current)?.focus();
    }
  }, [isEditing]);

  function closeEditor() {
    restoreRangeFocus.current = true;
    setIsEditing(false);
  }

  function choosePeriod(event: ChangeEvent<HTMLSelectElement>) {
    const { value } = event.target;
    if (value === CUSTOM_PERIOD_VALUE) { openEditor(); return; }
    const hours = Number(value);
    if (!isRelativePeriodHours(hours)) return;
    setPeriod({ kind: 'relative', hours });
    setIsEditing(false);
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
        <div className="zone-probe-chart-range">
          <select ref={periodSelectRef} className="zone-probe-period-select" value={periodValue} onChange={choosePeriod}
            aria-label={t('probePeriod')} aria-describedby={exactPeriodId} title={exactPeriod}>
            {Object.entries(PERIOD_OPTION_KEYS).map(([hours, key]) => <option key={hours} value={hours}>{t(key)}</option>)}
            <option value={CUSTOM_PERIOD_VALUE}>{t('probePeriodCustom')}</option>
          </select>
          {custom !== null && !isEditing ? (
            <button ref={rangeButtonRef} className="zone-probe-range-button" type="button" title={exactPeriod}
              aria-label={t('probeRangeEdit').replace('{range}', exactPeriod)} onClick={openEditor}>
              <span>{formatCompactPeriod(custom, locale, Date.now())}</span>
              <PolypbaseIcon name="edit" size={15} />
            </button>
          ) : null}
          <span id={exactPeriodId} className="sr-only">{exactPeriod}</span>
        </div>
      </div>
      {isEditing ? (
        <form className="zone-probe-range-form" onSubmit={submitRange}>
          {timeZone ? <p className="zone-probe-range-zone">{t('probeTimeZone').replace('{zone}', timeZone)}</p> : null}
          <label>
            <span>{t('probeRangeStart')}</span>
            <input ref={startInputRef} type="datetime-local" value={startValue} onChange={(event) => setStartValue(event.target.value)}
              max={toLocalInputValue(Date.now())} required />
          </label>
          <label>
            <span>{t('probeRangeEnd')}</span>
            <input type="datetime-local" value={endValue} onChange={(event) => setEndValue(event.target.value)}
              max={toLocalInputValue(Date.now())} required />
          </label>
          <div className="zone-probe-range-actions">
            <button className="primary-button" type="submit">{t('probeRangeApply')}</button>
            <button className="secondary-button" type="button" onClick={closeEditor}>{t('cancel')}</button>
          </div>
          {rangeError ? <p className="inline-error" role="alert">{t('probeRangeInvalid')}</p> : null}
        </form>
      ) : null}
      {resolution > 1 && hasData ? (
        <p className="zone-probe-chart-note">{t('probeCondensedNotice').replace('{minutes}', String(resolution))}</p>
      ) : null}
      <div className="probe-series-frame">
        <ProbeSeriesChart ariaLabel={summaryLabel(series, `${heading}, ${exactPeriod}${average.length ? `, ${t('probeAverage')}` : ''}`, t)} average={average} domain={domain} height={CHART_HEIGHT}
          hiddenIds={hiddenIds} labels={labels} locale={locale} onResetZoom={custom !== null ? resetRange : undefined}
          onZoom={zoomTo} series={series} target={target} window={shownWindow} />
        {!hasData && !readings.isLoading && !readings.error ? <p className="probe-series-state"><span>{t('probeNoReadings')}</span></p> : null}
      </div>
      {partial || isPartialPreview ? (
        <p className="zone-probe-chart-note">{isPartialPreview ? t('probeCoveragePreview') : t('probePartialCoverage')}</p>
      ) : null}
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
              <th scope="col">{resolution > 1 ? t('probeLatestInterval') : t('probeLatestInWindow')}</th>
              <th scope="col">{t('minTemperature')}</th>
              <th scope="col">{t('maxTemperature')}</th>
            </tr>
          </thead>
          <tbody>
            {latestAverage ? (
              <tr>
                <th scope="row"><span className="probe-series-name"><i className="probe-series-average" aria-hidden="true" />{t('probeAverage')}</span></th>
                <td>
                  <strong>{formatTemperature(latestAverage.mean)}</strong>
                  {' '}<time dateTime={new Date(latestAverage.start).toISOString()}>{dateFormat.format(latestAverage.start)}</time>
                  {' → '}<time dateTime={new Date(latestAverage.end).toISOString()}>{dateFormat.format(latestAverage.end)}</time>
                  {' '}<small>{t('probeAverageContributors').replace('{count}', String(latestAverage.contributors))}</small>
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
