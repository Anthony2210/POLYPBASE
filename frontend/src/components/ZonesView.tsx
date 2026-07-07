import { type CSSProperties, useEffect, useState } from 'react';
import { scaleLinear } from 'd3-scale';

import { getBoxStatusPresentation } from '../boxStatus';
import type { BoxItem, ThermalZone } from '../types';
import { formatDisplayDate } from '../utils/dateFormat';
import PageLoader from './PageLoader';

type Language = 'fr' | 'en';
type TFunction = (key: string) => string;

type ZoneOverviewEntry = {
  zone: ThermalZone;
  zoneBoxes: BoxItem[];
  livingBoxes: number;
  missingMeasurements: number;
  targetTemperature: number | null;
  measuredTemperature: number | null;
  referenceTemperature: number | null;
  temperatureNeedsAttention: boolean;
  salinityNeedsAttention: boolean;
  needsAttention: boolean;
};

export function ZonesView({
  boxes,
  isLoading,
  zones,
  onOpenZone,
  t,
}: {
  boxes: BoxItem[];
  isLoading: boolean;
  zones: ThermalZone[];
  onOpenZone: (id: number) => void;
  t: TFunction;
}) {
  const [sortMode, setSortMode] = useState<'location' | 'temperature'>('location');
  const zoneEntries = zones.map((zone) => buildZoneOverviewEntry(zone, boxes));
  const attentionEntries = zoneEntries.filter((entry) => entry.needsAttention);
  const sortedEntries = sortMode === 'location'
    ? zoneEntries
    : [...zoneEntries].sort(
      (first, second) => (first.referenceTemperature ?? Number.POSITIVE_INFINITY)
        - (second.referenceTemperature ?? Number.POSITIVE_INFINITY),
    );

  return (
    <section className="single-panel">
      {isLoading ? (
        <PageLoader variant="zones" label={t('zonesTitle')} />
      ) : (
        <div className="zone-overview">
          {attentionEntries.length ? (
            <section className="zone-overview-attention">
              <header>
                <div>
                  <h2>{t('zoneOverviewAttentionTitle')}</h2>
                  <p>{t('zoneOverviewAttentionDetails')}</p>
                </div>
                <span>{attentionEntries.length}</span>
              </header>
              <div className="zone-overview-attention-list">
                {attentionEntries.map((entry) => (
                  <button key={entry.zone.id} type="button" onClick={() => onOpenZone(entry.zone.id)}>
                    <strong>{entry.zone.name}</strong>
                    <small>{getZoneAttentionReasons(entry, t).join(' - ')}</small>
                  </button>
                ))}
              </div>
            </section>
          ) : null}

          <div className="zone-overview-heading">
            <span>{sortedEntries.length}</span>
            <div className="zone-overview-sort" role="tablist" aria-label={t('zonesTitle')}>
              <button
                className={sortMode === 'location' ? 'is-active' : ''}
                type="button"
                role="tab"
                aria-selected={sortMode === 'location'}
                onClick={() => setSortMode('location')}
              >
                {t('zoneOverviewSortLocation')}
              </button>
              <button
                className={sortMode === 'temperature' ? 'is-active' : ''}
                type="button"
                role="tab"
                aria-selected={sortMode === 'temperature'}
                onClick={() => setSortMode('temperature')}
              >
                {t('zoneOverviewSortTemperature')}
              </button>
            </div>
          </div>

          <div className="zone-overview-grid">
            {sortedEntries.map((entry) => (
              <button
                className={entry.needsAttention ? 'zone-card is-attention' : 'zone-card'}
                key={entry.zone.id}
                type="button"
                onClick={() => onOpenZone(entry.zone.id)}
              >
                <span className="zone-card-heading">
                  <span>
                    <strong>{entry.zone.name}</strong>
                    <small>{entry.zone.organization.name}</small>
                  </span>
                  <span className="zone-card-arrow" aria-hidden="true" />
                </span>

                <span className="zone-card-temperature">
                  <span>
                    <small>{t('temperatureShort')}</small>
                    <strong>{formatTemperature(entry.measuredTemperature ?? undefined)}</strong>
                  </span>
                  <span>
                    <small>{t('zoneTarget')}</small>
                    <strong>{formatTemperature(entry.targetTemperature ?? undefined)}</strong>
                  </span>
                </span>

                <span className="zone-card-thermal-line" aria-hidden="true">
                  <span className="zone-card-target" />
                  {entry.measuredTemperature !== null && entry.targetTemperature !== null ? (
                    <span
                      className="zone-card-current"
                      style={{
                        '--zone-temperature-position': `${getTemperatureMarkerPosition(
                          entry.measuredTemperature,
                          entry.targetTemperature,
                        )}%`,
                      } as CSSProperties}
                    />
                  ) : null}
                </span>

                <span className="zone-card-facts">
                  <span>
                    <small>{t('salinityShort')}</small>
                    <strong>{formatSalinity(entry.zone.latest_salinity?.salinity_psu)}</strong>
                    {entry.zone.latest_salinity == null ? (
                      <p className="inline-error">Salinite manquante</p>
                    ) : null}
                  </span>
                  <span>
                    <small>{t('zoneSummaryAlive')}</small>
                    <strong>{entry.livingBoxes} / {entry.zoneBoxes.length}</strong>
                  </span>
                </span>
              </button>
            ))}
          </div>
        </div>
      )}
    </section>
  );
}

export function ZoneDetailPage({
  boxes,
  isLoading,
  language,
  zone,
  onBack,
  onOpenBox,
  t,
}: {
  boxes: BoxItem[];
  isLoading: boolean;
  language: Language;
  zone: ThermalZone | null;
  onBack: () => void;
  onOpenBox: (id: number) => void;
  t: TFunction;
}) {
  const [boxFilter, setBoxFilter] = useState<'all' | 'living' | 'attention'>('all');

  if (isLoading) {
    return (
      <PageLoader variant="zone" label={t('zoneSheet')} />
    );
  }

  if (!zone) {
    return (
      <section className="zone-page">
        <button className="text-button" type="button" onClick={onBack}>{t('backToZones')}</button>
        <p className="muted compact-text">{t('noZone')}</p>
      </section>
    );
  }

  const zoneBoxes = boxes.filter((box) => box.thermal_zone?.id === zone.id);
  const livingBoxes = zoneBoxes.filter((box) => box.status === 'active');
  const attentionBoxes = zoneBoxes.filter(
    (box) => box.active_alert_count > 0 || !box.latest_measurement,
  );
  const filteredBoxes = boxFilter === 'living'
    ? livingBoxes
    : boxFilter === 'attention'
      ? attentionBoxes
      : zoneBoxes;
  const targetTemperature = parseTemperatureNumber(zone.target_temperature_c);
  const measuredTemperature = zone.latest_temperature?.average_temperature_c ?? null;
  const temperatureNeedsAttention = targetTemperature === null
    || measuredTemperature === null
    || Math.abs(measuredTemperature - targetTemperature) > 1;

  return (
    <section className="zone-page">
      <button className="text-button zone-back-button" type="button" onClick={onBack}>
        {t('backToZones')}
      </button>

      <header className="zone-sheet-hero">
        <div>
          <p className="box-page-label">{t('zoneSheet')}</p>
          <h2>{zone.name}</h2>
          <span>{zone.organization.name}</span>
        </div>
        <div className="zone-hero-summary" aria-label={t('zoneBoxesTitle')}>
          <Metric label={t('boxes')} value={String(zoneBoxes.length)} />
          <Metric label={t('zoneSummaryAlive')} value={String(livingBoxes.length)} />
          <Metric label={t('zoneSummaryAttention')} value={String(attentionBoxes.length)} />
          <Metric label={t('probes')} value={String(zone.probes.length)} />
        </div>
      </header>

      <TemperatureControlPanel zone={zone} t={t} />

      {temperatureNeedsAttention || attentionBoxes.length ? (
        <ZoneAttentionPanel
          boxes={attentionBoxes}
          temperatureNeedsAttention={temperatureNeedsAttention}
          onOpenBox={onOpenBox}
          t={t}
        />
      ) : null}

      <div className="zone-page-grid">
        <section className="zone-page-section zone-boxes-section">
          <div className="zone-boxes-heading">
            <div className="section-title">
              <h2>{t('zoneBoxesTitle')}</h2>
              <span>{filteredBoxes.length}</span>
            </div>
            <div className="zone-filter-tabs" role="tablist" aria-label={t('zoneBoxesTitle')}>
              <button
                className={boxFilter === 'all' ? 'is-active' : ''}
                type="button"
                role="tab"
                aria-selected={boxFilter === 'all'}
                onClick={() => setBoxFilter('all')}
              >
                {t('zoneFilterAll')}
              </button>
              <button
                className={boxFilter === 'living' ? 'is-active' : ''}
                type="button"
                role="tab"
                aria-selected={boxFilter === 'living'}
                onClick={() => setBoxFilter('living')}
              >
                {t('zoneFilterLiving')}
              </button>
              <button
                className={boxFilter === 'attention' ? 'is-active' : ''}
                type="button"
                role="tab"
                aria-selected={boxFilter === 'attention'}
                onClick={() => setBoxFilter('attention')}
              >
                {t('zoneFilterAttention')}
              </button>
            </div>
          </div>
          {filteredBoxes.length ? (
            <div className="zone-box-list">
              {filteredBoxes.map((box) => {
                const status = getBoxStatusPresentation(box.status, language);

                return (
                  <button
                    className={`zone-box-row is-${status.tone}`}
                    key={box.id}
                    type="button"
                    onClick={() => onOpenBox(box.id)}
                  >
                    <span className="zone-box-main">
                      <strong>{box.global_code}</strong>
                      <small>{box.species.scientific_name}</small>
                    </span>
                    <span className="zone-box-reading">
                      {box.latest_measurement ? (
                        <>
                          <strong>
                            {box.latest_measurement.polyp_count} {t('polyps')} / {box.latest_measurement.ephyrae_count} {t('ephyrae')}
                          </strong>
                          <small>{formatDisplayDate(box.latest_measurement.measured_on)}</small>
                        </>
                      ) : (
                        <strong>{t('recentMeasurementMissing')}</strong>
                      )}
                    </span>
                    {box.active_alert_count > 0 ? (
                      <span className="zone-alert-pill">{box.active_alert_count}</span>
                    ) : null}
                    <span className={`box-life-status is-${status.tone}`}>
                      {status.label}
                    </span>
                  </button>
                );
              })}
            </div>
          ) : (
            <p className="muted compact-text">{boxFilter === 'all' ? t('emptyZone') : t('zoneNoAttention')}</p>
          )}
        </section>

        <div className="zone-secondary-stack">
          <section className="zone-page-section zone-chart-section">
            <div className="section-title">
              <h2>{t('latestCounts')}</h2>
              <span>{zoneBoxes.length}</span>
            </div>
            <ZoneLatestCountsChart boxes={zoneBoxes} t={t} />
          </section>

          <section className="zone-page-section">
            <div className="section-title">
              <h2>{t('zoneProbesTitle')}</h2>
              <span>{zone.probes.length}</span>
            </div>
            <div className="probe-list">
              {zone.probes.length ? zone.probes.map((probe) => (
                <p key={probe.id}>
                  <strong>{probe.code}</strong>
                  <span>{probe.probe_type}</span>
                </p>
              )) : <p className="muted compact-text">-</p>}
            </div>
          </section>
        </div>
      </div>

      <ZoneRecentActivity boxes={zoneBoxes} onOpenBox={onOpenBox} t={t} />
    </section>
  );
}

function buildZoneOverviewEntry(zone: ThermalZone, boxes: BoxItem[]): ZoneOverviewEntry {
  const zoneBoxes = boxes.filter((box) => box.thermal_zone?.id === zone.id);
  const targetTemperature = parseTemperatureNumber(zone.target_temperature_c);
  const measuredTemperature = zone.latest_temperature?.average_temperature_c ?? null;
  const missingMeasurements = zoneBoxes.filter((box) => !box.latest_measurement).length;
  const temperatureNeedsAttention = targetTemperature === null
    || measuredTemperature === null
    || Math.abs(measuredTemperature - targetTemperature) > 1;
  const salinityNeedsAttention = zone.latest_salinity?.salinity_psu == null;

  return {
    zone,
    zoneBoxes,
    livingBoxes: zoneBoxes.filter((box) => box.status === 'active').length,
    missingMeasurements,
    targetTemperature,
    measuredTemperature,
    referenceTemperature: targetTemperature ?? measuredTemperature,
    temperatureNeedsAttention,
    salinityNeedsAttention,
    needsAttention:
      temperatureNeedsAttention
      || salinityNeedsAttention
      || zone.probes.length === 0
      || missingMeasurements > 0,
  };
}

function getZoneAttentionReasons(entry: ZoneOverviewEntry, t: TFunction) {
  const reasons: string[] = [];

  if (entry.temperatureNeedsAttention) {
    reasons.push(
      entry.targetTemperature === null || entry.measuredTemperature === null
        ? t('temperatureMissing')
        : t('zoneOverviewThermalGap'),
    );
  }

  if (entry.salinityNeedsAttention) {
    reasons.push('Salinite manquante');
  }

  if (!entry.zone.probes.length) reasons.push(t('zoneOverviewNoProbe'));

  if (entry.missingMeasurements) {
    reasons.push(`${entry.missingMeasurements} ${t('zoneOverviewMissingMeasurements')}`);
  }

  return reasons;
}

function ZoneAttentionPanel({
  boxes,
  temperatureNeedsAttention,
  onOpenBox,
  t,
}: {
  boxes: BoxItem[];
  temperatureNeedsAttention: boolean;
  onOpenBox: (id: number) => void;
  t: TFunction;
}) {
  return (
    <section className="zone-attention-panel">
      <div className="zone-attention-heading">
        <h2>{t('zoneAttentionTitle')}</h2>
        <span>{boxes.length + Number(temperatureNeedsAttention)}</span>
      </div>
      <div className="zone-attention-items">
        {temperatureNeedsAttention ? (
          <article className="zone-attention-item is-temperature">
            <strong>{t('temperatureControl')}</strong>
            <span>{t('boxAttention')}</span>
          </article>
        ) : null}
        {boxes.map((box) => (
          <button
            className="zone-attention-item"
            key={box.id}
            type="button"
            onClick={() => onOpenBox(box.id)}
          >
            <strong>{box.global_code}</strong>
            <span>
              {!box.latest_measurement
                ? t('recentMeasurementMissing')
                : `${box.active_alert_count} ${t('boxAttention').toLocaleLowerCase()}`}
            </span>
          </button>
        ))}
      </div>
    </section>
  );
}

function ZoneRecentActivity({
  boxes,
  onOpenBox,
  t,
}: {
  boxes: BoxItem[];
  onOpenBox: (id: number) => void;
  t: TFunction;
}) {
  const recentMeasurements = boxes
    .filter((box) => box.latest_measurement)
    .sort((first, second) => {
      const firstDate = first.latest_measurement?.measured_on ?? '';
      const secondDate = second.latest_measurement?.measured_on ?? '';
      return secondDate.localeCompare(firstDate);
    })
    .slice(0, 5);

  return (
    <section className="zone-page-section zone-activity-section">
      <div className="section-title">
        <h2>{t('zoneActivityTitle')}</h2>
        <span>{recentMeasurements.length}</span>
      </div>
      {recentMeasurements.length ? (
        <div className="zone-activity-list">
          {recentMeasurements.map((box) => {
            const measurement = box.latest_measurement;
            if (!measurement) return null;

            return (
              <button key={box.id} type="button" onClick={() => onOpenBox(box.id)}>
                <span className="zone-activity-date">{formatDisplayDate(measurement.measured_on)}</span>
                <span>
                  <strong>{box.global_code}</strong>
                  <small>{box.species.scientific_name}</small>
                </span>
                <span className="zone-activity-values">
                  <strong>{measurement.polyp_count}</strong>
                  <small>{t('polyps')}</small>
                </span>
                <span className="zone-activity-values">
                  <strong>{measurement.ephyrae_count}</strong>
                  <small>{t('ephyrae')}</small>
                </span>
              </button>
            );
          })}
        </div>
      ) : (
        <p className="muted compact-text">{t('zoneNoRecentActivity')}</p>
      )}
    </section>
  );
}

function TemperatureControlPanel({ zone, t }: { zone: ThermalZone; t: TFunction }) {
  const targetTemperature = parseTemperatureNumber(zone.target_temperature_c);
  const measuredTemperature = zone.latest_temperature?.average_temperature_c ?? null;
  const minTemperature = zone.latest_temperature?.min_temperature_c ?? null;
  const maxTemperature = zone.latest_temperature?.max_temperature_c ?? null;
  const hasTemperature = measuredTemperature !== null && targetTemperature !== null;
  const delta = hasTemperature ? measuredTemperature - targetTemperature : null;
  const absoluteDelta = delta === null ? null : Math.abs(delta);
  const statusClass = absoluteDelta === null
    ? 'is-missing'
    : absoluteDelta <= 0.5
      ? 'is-ok'
      : 'is-watch';
  const measuredLeft = hasTemperature ? getTemperatureMarkerPosition(measuredTemperature, targetTemperature) : 50;
  const minLeft = hasTemperature && minTemperature !== null
    ? getTemperatureMarkerPosition(minTemperature, targetTemperature)
    : null;
  const maxLeft = hasTemperature && maxTemperature !== null
    ? getTemperatureMarkerPosition(maxTemperature, targetTemperature)
    : null;
  const rangeStart = minLeft !== null && maxLeft !== null
    ? Math.min(minLeft, maxLeft)
    : measuredLeft;
  const rangeWidth = minLeft !== null && maxLeft !== null
    ? Math.max(Math.abs(maxLeft - minLeft), 0.6)
    : 0.6;
  const [isGaugeReady, setIsGaugeReady] = useState(false);

  useEffect(() => {
    setIsGaugeReady(false);
    const animationFrame = window.requestAnimationFrame(() => setIsGaugeReady(true));
    return () => window.cancelAnimationFrame(animationFrame);
  }, [zone.id, targetTemperature, measuredTemperature, minTemperature, maxTemperature]);

  const gaugeStyle = {
    '--temperature-current-position': `${measuredLeft}%`,
    '--temperature-range-start': `${rangeStart}%`,
    '--temperature-range-width': `${rangeWidth}%`,
  } as CSSProperties;

  return (
    <section className={`zone-temperature-panel ${statusClass}`}>
      <div className="zone-temperature-heading">
        <div>
          <h2>{t('temperatureControl')}</h2>
          <p>{zone.latest_temperature ? formatDisplayDate(zone.latest_temperature.date) : t('temperatureMissing')}</p>
        </div>
      </div>

      <div
        className={isGaugeReady ? 'temperature-gauge is-ready' : 'temperature-gauge'}
        style={gaugeStyle}
        aria-label={t('temperatureControl')}
      >
        <span className="temperature-gauge-safe-band" aria-hidden="true" />
        <span className="temperature-gauge-track" aria-hidden="true" />
        {hasTemperature ? <span className="temperature-gauge-range" aria-hidden="true" /> : null}
        {targetTemperature !== null ? (
          <span className="temperature-gauge-target" aria-hidden="true">
            <span>{formatTemperature(targetTemperature)}</span>
          </span>
        ) : null}
        {minLeft !== null ? <span className="temperature-gauge-cap is-min" style={{ left: `${minLeft}%` }} aria-hidden="true" /> : null}
        {maxLeft !== null ? <span className="temperature-gauge-cap is-max" style={{ left: `${maxLeft}%` }} aria-hidden="true" /> : null}
        {hasTemperature ? (
          <span className="temperature-gauge-current">
            {measuredTemperature === null ? '-' : formatTemperature(measuredTemperature)}
          </span>
        ) : null}
      </div>

      <div className="temperature-scale-labels">
        <span>{targetTemperature === null ? '-' : formatTemperature(targetTemperature - 3)}</span>
        <strong>{t('targetTemperature')}</strong>
        <span>{targetTemperature === null ? '-' : formatTemperature(targetTemperature + 3)}</span>
      </div>

      <div className="temperature-details-grid">
        <Metric label={t('targetTemperature')} value={formatTemperatureValue(zone.target_temperature_c)} />
        <Metric label={t('measuredTemperature')} value={formatTemperature(measuredTemperature ?? undefined)} />
        <Metric label={t('minTemperature')} value={formatTemperature(minTemperature ?? undefined)} />
        <Metric label={t('maxTemperature')} value={formatTemperature(maxTemperature ?? undefined)} />
      </div>
    </section>
  );
}

function ZoneLatestCountsChart({ boxes, t }: { boxes: BoxItem[]; t: TFunction }) {
  const measuredBoxes = boxes
    .filter((box) => box.latest_measurement)
    .slice(0, 8);

  if (!measuredBoxes.length) {
    return <p className="muted compact-text chart-empty">{t('noZoneChart')}</p>;
  }

  const maxValue = Math.max(
    1,
    ...measuredBoxes.flatMap((box) => [
      box.latest_measurement?.polyp_count ?? 0,
      box.latest_measurement?.ephyrae_count ?? 0,
    ]),
  );
  // D3 scale maps a count to a bar width (in %), with a small minimum so an
  // empty bar stays visible.
  const widthScale = scaleLinear().domain([0, maxValue]).range([2, 100]);

  return (
    <div className="zone-count-chart">
      {measuredBoxes.map((box) => {
        const measurement = box.latest_measurement;
        if (!measurement) return null;
        const polypWidth = `${widthScale(measurement.polyp_count)}%`;
        const ephyraeWidth = `${widthScale(measurement.ephyrae_count)}%`;

        return (
          <div className="zone-count-row" key={box.id}>
            <div>
              <strong>{box.global_code}</strong>
              <small>{formatDisplayDate(measurement.measured_on)}</small>
            </div>
            <div className="zone-count-bars">
              <span className="zone-count-bar is-polyps" style={{ width: polypWidth }}>
                {measurement.polyp_count}
              </span>
              <span className="zone-count-bar is-ephyrae" style={{ width: ephyraeWidth }}>
                {measurement.ephyrae_count}
              </span>
            </div>
          </div>
        );
      })}
      <div className="chart-legend">
        <span className="is-polyps">{t('polyps')}</span>
        <span className="is-ephyrae">{t('ephyraeFull')}</span>
      </div>
    </div>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <span className="metric">
      <small>{label}</small>
      <strong>{value}</strong>
    </span>
  );
}

function formatTemperature(value: number | undefined) {
  return value === undefined ? '-' : `${value.toFixed(1)}°C`;
}

function formatTemperatureValue(value: string | number | null | undefined) {
  if (value === null || value === undefined || value === '') return '-';
  const numericValue = typeof value === 'number' ? value : Number.parseFloat(value);
  return Number.isFinite(numericValue) ? `${numericValue.toFixed(1)}°C` : '-';
}

function parseTemperatureNumber(value: string | number | null | undefined) {
  if (value === null || value === undefined || value === '') return null;
  const numericValue = typeof value === 'number' ? value : Number.parseFloat(value);
  return Number.isFinite(numericValue) ? numericValue : null;
}

function getTemperatureMarkerPosition(value: number, target: number) {
  const relativePosition = 50 + ((value - target) / 3) * 50;
  return Math.min(100, Math.max(0, relativePosition));
}

function formatSalinity(value: string | number | null | undefined) {
  if (value === null || value === undefined || value === '') return '-';
  const numeric = typeof value === 'string' ? Number.parseFloat(value) : value;
  return Number.isNaN(numeric) ? '-' : numeric.toFixed(1);
}
