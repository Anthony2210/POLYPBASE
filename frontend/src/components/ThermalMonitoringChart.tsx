import { useMemo, useState } from 'react';

import useThermalMonitoring from '../hooks/useThermalMonitoring';
import type { Translator } from '../i18n';
import type { ThermalZone } from '../types';
import { getDocumentLocale } from '../utils/dateFormat';
import { formatTemperature, formatSignedTemperature } from '../utils/environmentFormat';
import { formatExactPeriod } from '../utils/probePeriod';
import { buildProbeAverage, buildProbeSeries, seriesTemperatureDomain } from '../utils/probeSeries';
import ProbeSeriesChart, { averageSeriesStyle, seriesClassName } from './ProbeSeriesChart';

const CHART_HEIGHT = 210;

/** The historical renderer, with a fixed server window and independent summary. */
export default function ThermalMonitoringChart({ zone, t }: { zone: ThermalZone; t: Translator }) {
  const { data, error, isLoading, retry } = useThermalMonitoring(zone);
  const [hiddenIds, setHiddenIds] = useState<ReadonlySet<number>>(new Set());
  const locale = getDocumentLocale();
  const series = useMemo(() => {
    const entries = buildProbeSeries(data);
    // Keep collected probes in the same color slots as the historical chart;
    // other probe types follow them rather than shifting their existing colors.
    const collectedIds = entries.filter((entry) => zone.probes.some((probe) => probe.id === entry.id && probe.is_collected)).map((entry) => entry.id);
    const ids = [...collectedIds, ...entries.filter((entry) => !collectedIds.includes(entry.id)).map((entry) => entry.id)];
    return entries.map((entry) => ({ ...entry, slot: ids.indexOf(entry.id) }));
  }, [data, zone.probes]);
  const average = useMemo(() => buildProbeAverage(data), [data]);
  const target = data?.target_temperature_c ?? null;
  const domain = useMemo(() => seriesTemperatureDomain(series, target, average), [series, target, average]);
  const window = data ? { start: Date.parse(data.start), end: Date.parse(data.end) } : null;
  const summary = data?.summary;
  const exactPeriod = window ? formatExactPeriod(window, locale) : '';
  const hasData = (summary?.measurement_count ?? 0) > 0;
  const labels = {
    target: t('targetTemperature'), mean: t('probeIntervalMean'),
    minimum: t('minTemperature'), maximum: t('maxTemperature'), unit: '°C',
    keyboardDescription: t('thermalMonitoringKeyboard'),
    ...(average.length ? { average: t('probeAverage'), averageContributors: t('probeAverageContributors') } : {}),
  };

  function toggleSeries(id: number) {
    setHiddenIds((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  }

  return (
    <div className="thermal-monitoring" aria-busy={isLoading}>
      <div className="thermal-monitoring-plot">
        <div className="probe-series-frame thermal-monitoring-frame">
          {window ? (
            <ProbeSeriesChart ariaLabel={`${t('temperatureControl')}, ${exactPeriod}`} average={average}
              domain={domain} height={CHART_HEIGHT} hiddenIds={hiddenIds} labels={labels} locale={locale}
              series={series} target={target} window={window} />
          ) : null}
          {!hasData && !error ? (
            <p className="probe-series-state" role="status"><span>{t(isLoading ? 'loading' : 'thermalMonitoringEmpty')}</span></p>
          ) : null}
          {error ? (
            <div className="probe-series-status" role="status">
              <p>{t('temperatureRecentError')}</p>
              <button className="secondary-button" type="button" onClick={retry}>{t('probeRetry')}</button>
            </div>
          ) : null}
        </div>
        {series.length ? (
          <ul className="thermal-monitoring-legend" aria-label={t('probeLegendProbe')}>
            {average.length ? (
              <li><span className="probe-series-name"><i className="probe-series-average" style={averageSeriesStyle(series)} aria-hidden="true" />{t('probeAverage')}</span></li>
            ) : null}
            {series.map((entry) => (
              <li key={entry.id} className={hiddenIds.has(entry.id) ? 'is-hidden' : undefined}>
                <button type="button" aria-pressed={!hiddenIds.has(entry.id)}
                  title={t('probeSeriesToggle').replace('{probe}', entry.code)} onClick={() => toggleSeries(entry.id)}>
                  <span className="probe-series-name"><i className={seriesClassName(entry.slot)} aria-hidden="true" />
                    {entry.code}{!entry.isActive ? ` (${t('probeInactive')})` : ''}
                    {entry.location ? <small>{entry.location}</small> : null}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        ) : null}
      </div>
      <dl className="thermal-monitoring-metrics">
        <div><dt>{t('temperatureAverage')}</dt><dd>{formatTemperature(summary?.mean_temperature_c)}</dd></div>
        <div><dt>{t('thermalMonitoringDeviation')}</dt><dd>{summary?.target_deviation_c == null ? '-' : `${formatSignedTemperature(summary.target_deviation_c)}°C`}</dd></div>
        <div><dt>{t('thermalMonitoringExtrema')}</dt><dd>{formatTemperature(summary?.min_temperature_c)} / {formatTemperature(summary?.max_temperature_c)}</dd></div>
      </dl>
    </div>
  );
}
