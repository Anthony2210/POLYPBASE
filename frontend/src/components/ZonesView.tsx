import { type CSSProperties, type FormEvent, useEffect, useMemo, useState } from 'react';

import { scaleLinear } from 'd3-scale';

import { getBoxStatusPresentation } from '../boxStatus';
import type { BoxItem, ThermalZone, UserProfile } from '../types';
import type { ManualTemperaturePayload, ProbePayload, ThermalZonePayload } from '../types/admin';
import { formatDisplayDate } from '../utils/dateFormat';
import { getErrorMessage } from '../utils/errors';
import { getZoneOccupancyLevel } from '../utils/zoneOccupancy';
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

type ZoneAlertItem = {
  id: string;
  level: 'low' | 'medium' | 'high';
  title: string;
  message: string;
  zone: ThermalZone;
  box?: BoxItem;
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
  const [sortMode, setSortMode] = useState<'temperatureAsc' | 'temperatureDesc'>('temperatureAsc');
  const [zoneAlertModal, setZoneAlertModal] = useState<'overview' | ZoneOverviewEntry | null>(null);
  const zoneEntries = zones.map((zone) => buildZoneOverviewEntry(zone, boxes));
  const attentionEntries = zoneEntries.filter((entry) => entry.needsAttention);
  const overviewAlertItems = attentionEntries.flatMap((entry) => getZoneAlertItems(entry, t));
  const zoneAlertModalItems = zoneAlertModal
    ? zoneAlertModal === 'overview'
      ? overviewAlertItems
      : getZoneAlertItems(zoneAlertModal, t)
    : [];
  const zoneAlertModalTitle = zoneAlertModal && zoneAlertModal !== 'overview'
    ? zoneAlertModal.zone.name
    : t('zonesTitle');
  const sortedEntries = [...zoneEntries].sort((first, second) => {
    const firstTemperature = first.referenceTemperature ?? Number.POSITIVE_INFINITY;
    const secondTemperature = second.referenceTemperature ?? Number.POSITIVE_INFINITY;
    return sortMode === 'temperatureAsc'
      ? firstTemperature - secondTemperature
      : secondTemperature - firstTemperature;
  });

  return (
    <section className="single-panel">
      {isLoading ? (
        <PageLoader variant="zones" label={t('zonesTitle')} />
      ) : (
        <div className="zone-overview">
          <div className="zone-overview-heading">
            <div className="zone-overview-count">
              <span>{t('zoneOverviewHeading')}</span>
              <button
                className={attentionEntries.length ? 'zone-alert-summary' : 'zone-alert-summary is-empty'}
                type="button"
                title={t('zoneOverviewAttentionTitle')}
                onClick={() => setZoneAlertModal('overview')}
              >
                <BellIcon />
                <strong>{attentionEntries.length}</strong>
              </button>
            </div>
            <div className="zone-overview-sort" role="tablist" aria-label={t('zonesTitle')}>
              <button
                className={sortMode === 'temperatureAsc' ? 'is-active' : ''}
                type="button"
                role="tab"
                aria-selected={sortMode === 'temperatureAsc'}
                onClick={() => setSortMode('temperatureAsc')}
              >
                {t('zoneOverviewSortTemperatureAsc')}
              </button>
              <button
                className={sortMode === 'temperatureDesc' ? 'is-active' : ''}
                type="button"
                role="tab"
                aria-selected={sortMode === 'temperatureDesc'}
                onClick={() => setSortMode('temperatureDesc')}
              >
                {t('zoneOverviewSortTemperatureDesc')}
              </button>
            </div>
          </div>

          <div className="zone-overview-grid">
            {sortedEntries.map((entry) => {
              const zoneAlertCount = getZoneAlertItems(entry, t).length;

              return (
                <article
                  className={entry.needsAttention ? 'zone-card is-attention' : 'zone-card'}
                  key={entry.zone.id}
                >
                  <button
                    className="zone-card-body"
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
                        <small>{entry.salinityNeedsAttention ? t('zoneSalinityMissing') : t('salinityShort')}</small>
                        <strong>{formatSalinity(entry.zone.salinity_psu)}</strong>
                      </span>
                      <span>
                        <small>{t('zoneOccupancy')}</small>
                        <strong
                          className={`zone-occupancy is-${getZoneOccupancyLevel(
                            entry.livingBoxes,
                            entry.zone.capacity,
                          )}`}
                        >
                          {formatZoneOccupancy(entry.livingBoxes, entry.zone.capacity)}
                        </strong>
                      </span>
                    </span>
                  </button>

                  <button
                    className={entry.needsAttention ? 'zone-card-alert' : 'zone-card-alert is-empty'}
                    type="button"
                    aria-label={`${t('zoneOverviewAttentionTitle')} ${entry.zone.name}`}
                    title={`${t('zoneOverviewAttentionTitle')} ${entry.zone.name}`}
                    onClick={() => setZoneAlertModal(entry)}
                  >
                      <BellIcon />
                    <strong>{zoneAlertCount}</strong>
                  </button>
                </article>
              );
            })}
          </div>
          {zoneAlertModal ? (
            <ZoneAlertsModal
              items={zoneAlertModalItems}
              title={zoneAlertModalTitle}
              onClose={() => setZoneAlertModal(null)}
              onOpenZone={onOpenZone}
              t={t}
            />
          ) : null}
        </div>
      )}
    </section>
  );
}

function ZoneManagementModal({
  mode,
  onClose,
  onCreateProbe,
  onCreateZone,
  onUpdateZone,
  profile,
  selectedZone,
  zones,
  t,
}: {
  mode: 'create' | 'edit' | 'probe';
  onClose: () => void;
  onCreateProbe: (payload: ProbePayload) => Promise<void>;
  onCreateZone: (payload: ThermalZonePayload) => Promise<void>;
  onUpdateZone: (zoneId: number, payload: ThermalZonePayload) => Promise<void>;
  profile: UserProfile | null;
  selectedZone?: ThermalZone | null;
  zones: ThermalZone[];
  t: TFunction;
}) {
  const adminOrganizations = useMemo(() => getAdminOrganizations(profile), [profile]);
  const defaultOrganizationId = selectedZone?.organization.id ?? adminOrganizations[0]?.id ?? null;
  const defaultZone = selectedZone ?? zones[0] ?? null;
  const [zoneForm, setZoneForm] = useState({
    organization: defaultOrganizationId ? String(defaultOrganizationId) : '',
    name: selectedZone?.name ?? '',
    zoneType: selectedZone?.zone_type ?? 'cabinet',
    targetTemperature: selectedZone?.target_temperature_c ?? '',
    capacity: selectedZone?.capacity != null ? String(selectedZone.capacity) : '',
    salinity: selectedZone?.salinity_psu ?? '',
  });
  const [probeForm, setProbeForm] = useState({
    thermalZone: defaultZone ? String(defaultZone.id) : '',
    code: '',
    probeType: 'temperature',
    location: '',
  });
  const [isSaving, setIsSaving] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);

  const title = mode === 'probe'
    ? t('zoneAddProbeTitle')
    : mode === 'edit'
      ? t('zoneEditTitle')
      : t('zoneAddTitle');

  async function handleZoneSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (isSaving || !zoneForm.organization || !zoneForm.name.trim()) return;

    setIsSaving(true);
    setFormError(null);

    const payload: ThermalZonePayload = {
      organization: Number(zoneForm.organization),
      name: zoneForm.name.trim(),
      zone_type: zoneForm.zoneType,
      target_temperature_c: zoneForm.targetTemperature.trim() || null,
      capacity: zoneForm.capacity.trim() ? Number.parseInt(zoneForm.capacity, 10) : null,
      salinity_psu: zoneForm.salinity.trim() || null,
    };

    try {
      if (mode === 'edit' && selectedZone) {
        await onUpdateZone(selectedZone.id, payload);
      } else {
        await onCreateZone(payload);
      }
      onClose();
    } catch (requestError) {
      setFormError(getErrorMessage(requestError));
    } finally {
      setIsSaving(false);
    }
  }

  async function handleProbeSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (isSaving || !probeForm.thermalZone || !probeForm.code.trim()) return;

    setIsSaving(true);
    setFormError(null);

    try {
      await onCreateProbe({
        thermal_zone: Number(probeForm.thermalZone),
        code: probeForm.code.trim(),
        probe_type: probeForm.probeType.trim() || 'temperature',
        location: probeForm.location.trim(),
      });
      onClose();
    } catch (requestError) {
      setFormError(getErrorMessage(requestError));
    } finally {
      setIsSaving(false);
    }
  }

  return (
    <div className="modal-backdrop" role="presentation" onClick={onClose}>
      <section className="zone-management-modal" role="dialog" aria-modal="true" aria-label={title} onClick={(event) => event.stopPropagation()}>
        <header className="modal-heading">
          <div>
            <p className="modal-kicker">{mode === 'probe' ? t('probes') : t('zonesTitle')}</p>
            <h2>{title}</h2>
          </div>
          <button className="icon-button" type="button" onClick={onClose} aria-label={t('close')}>×</button>
        </header>

        {mode === 'probe' ? (
          <form className="zone-management-form" onSubmit={handleProbeSubmit}>
            <label>
              <span>{t('adminProbeZone')}</span>
              <select
                value={probeForm.thermalZone}
                onChange={(event) => setProbeForm((current) => ({ ...current, thermalZone: event.target.value }))}
                required
              >
                {zones.map((zone) => (
                  <option key={zone.id} value={zone.id}>{zone.name}</option>
                ))}
              </select>
            </label>
            <label>
              <span>{t('adminProbeCode')}</span>
              <input
                value={probeForm.code}
                onChange={(event) => setProbeForm((current) => ({ ...current, code: event.target.value }))}
                required
              />
            </label>
            <label>
              <span>{t('adminProbeType')}</span>
              <input
                value={probeForm.probeType}
                onChange={(event) => setProbeForm((current) => ({ ...current, probeType: event.target.value }))}
              />
            </label>
            <label>
              <span>{t('adminProbeLocation')}</span>
              <input
                value={probeForm.location}
                onChange={(event) => setProbeForm((current) => ({ ...current, location: event.target.value }))}
              />
            </label>
            {formError ? <p className="inline-error">{formError}</p> : null}
            <button type="submit" disabled={isSaving || !probeForm.thermalZone || !probeForm.code.trim()}>
              {isSaving ? t('saving') : t('adminAddProbe')}
            </button>
          </form>
        ) : (
          <form className="zone-management-form" onSubmit={handleZoneSubmit}>
            <label>
              <span>{t('adminZoneOrganization')}</span>
              <select
                value={zoneForm.organization}
                onChange={(event) => setZoneForm((current) => ({ ...current, organization: event.target.value }))}
                disabled={mode === 'edit'}
                required
              >
                {adminOrganizations.map((organization) => (
                  <option key={organization.id} value={organization.id}>{organization.name}</option>
                ))}
              </select>
            </label>
            <label>
              <span>{t('adminZoneName')}</span>
              <input
                value={zoneForm.name}
                onChange={(event) => setZoneForm((current) => ({ ...current, name: event.target.value }))}
                required
              />
            </label>
            <label>
              <span>{t('adminZoneType')}</span>
              <select
                value={zoneForm.zoneType}
                onChange={(event) => setZoneForm((current) => ({ ...current, zoneType: event.target.value }))}
              >
                <option value="cabinet">{t('adminZoneTypeCabinet')}</option>
                <option value="incubator">{t('adminZoneTypeIncubator')}</option>
              </select>
            </label>
            <label>
              <span>{t('adminTargetTemperature')}</span>
              <input
                type="number"
                inputMode="decimal"
                step="0.1"
                value={zoneForm.targetTemperature}
                onChange={(event) => setZoneForm((current) => ({ ...current, targetTemperature: event.target.value }))}
              />
            </label>
            <label>
              <span>{t('adminZoneCapacity')}</span>
              <input
                type="number"
                inputMode="numeric"
                min="0"
                value={zoneForm.capacity}
                onChange={(event) => setZoneForm((current) => ({ ...current, capacity: event.target.value }))}
              />
            </label>
            <label>
              <span>{t('adminZoneSalinity')}</span>
              <input
                type="number"
                inputMode="decimal"
                step="0.1"
                value={zoneForm.salinity}
                onChange={(event) => setZoneForm((current) => ({ ...current, salinity: event.target.value }))}
              />
            </label>
            {formError ? <p className="inline-error">{formError}</p> : null}
            <button type="submit" disabled={isSaving || !zoneForm.organization || !zoneForm.name.trim()}>
              {isSaving ? t('saving') : mode === 'edit' ? t('adminSaveZone') : t('adminCreateZone')}
            </button>
          </form>
        )}
      </section>
    </div>
  );
}

function getAdminOrganizations(profile: UserProfile | null): Array<{ id: number; name: string }> {
  if (!profile) return [];
  if (profile.is_superuser) return profile.organizations;

  const organizationMap = new Map<number, { id: number; name: string }>();
  for (const membership of profile.memberships) {
    if (membership.role === 'admin') {
      organizationMap.set(membership.organization.id, membership.organization);
    }
  }

  return Array.from(organizationMap.values()).sort((first, second) => first.name.localeCompare(second.name));
}

function BellIcon() {
  return (
    <svg className="bell-icon" viewBox="0 0 24 24" aria-hidden="true" focusable="false">
      <path
        d="M18 9.8c0-3.3-2.1-5.8-5.1-6.3V2h-1.8v1.5C8.1 4 6 6.5 6 9.8v3.9l-1.5 2.4v1.1h15v-1.1L18 13.7V9.8Z"
        fill="none"
        stroke="currentColor"
        strokeLinejoin="round"
        strokeWidth="1.7"
      />
      <path d="M9.8 18.8a2.3 2.3 0 0 0 4.4 0" fill="none" stroke="currentColor" strokeLinecap="round" strokeWidth="1.7" />
    </svg>
  );
}

export function ZoneDetailPage({
  boxes,
  isLoading,
  language,
  zone,
  canRecordManualTemperature,
  onBack,
  onRecordManualTemperature,
  onOpenBox,
  t,
}: {
  boxes: BoxItem[];
  isLoading: boolean;
  language: Language;
  zone: ThermalZone | null;
  canRecordManualTemperature: boolean;
  onBack: () => void;
  onRecordManualTemperature: (zoneId: number, payload: ManualTemperaturePayload) => Promise<ThermalZone>;
  onOpenBox: (id: number) => void;
  t: TFunction;
}) {
  const [boxFilter, setBoxFilter] = useState<'all' | 'living' | 'attention'>('all');
  const [isZoneAlertsOpen, setIsZoneAlertsOpen] = useState(false);

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
  const attentionBoxes = livingBoxes.filter(
    (box) => box.active_alert_count > 0 || !box.latest_measurement,
  );
  const zoneOverviewEntry = buildZoneOverviewEntry(zone, boxes);
  const zoneAlertItems = getZoneAlertItems(zoneOverviewEntry, t);
  const zoneAlertCount = zoneAlertItems.length;
  const filteredBoxes = boxFilter === 'living'
    ? livingBoxes
    : boxFilter === 'attention'
      ? attentionBoxes
      : zoneBoxes;
  const sortedFilteredBoxes = [...filteredBoxes].sort((first, second) => {
    if (first.status !== second.status) {
      return first.status === 'active' ? -1 : 1;
    }

    const firstAlertCount = first.active_alert_count ?? 0;
    const secondAlertCount = second.active_alert_count ?? 0;
    if (firstAlertCount !== secondAlertCount) {
      return secondAlertCount - firstAlertCount;
    }

    const firstDate = first.latest_measurement?.measured_on ?? '';
    const secondDate = second.latest_measurement?.measured_on ?? '';
    if (firstDate !== secondDate) {
      return secondDate.localeCompare(firstDate);
    }

    return first.global_code.localeCompare(second.global_code);
  });

  return (
    <section className="zone-page">
      <button className="text-button zone-back-button" type="button" onClick={onBack}>
        {t('backToZones')}
      </button>

      <header className="zone-sheet-hero">
        <div className="zone-sheet-title">
          <p className="box-page-label">{t('zoneSheet')}</p>
          <h2>{zone.name}</h2>
          <span>{zone.organization.name}</span>
        </div>
        <div className="zone-hero-summary" aria-label={t('zoneBoxesTitle')}>
          <Metric label={t('zoneSummaryAlive')} value={String(livingBoxes.length)} />
          <Metric label={t('zoneCapacity')} value={formatZoneCapacity(zone.capacity)} />
          <Metric label={t('zoneSalinity')} value={formatZoneSalinity(zone.salinity_psu)} />
          <Metric label={t('zoneSummaryAttention')} value={String(attentionBoxes.length)} />
          <Metric label={t('probes')} value={String(zone.probes.length)} />
        </div>
        <div className="zone-hero-actions">
          <button
            className={zoneAlertCount ? 'box-alert-trigger zone-alert-trigger' : 'box-alert-trigger zone-alert-trigger is-empty'}
            type="button"
            aria-label={`${t('zoneOverviewAttentionTitle')} (${zoneAlertCount})`}
            title={`${t('zoneOverviewAttentionTitle')} (${zoneAlertCount})`}
            onClick={() => setIsZoneAlertsOpen(true)}
          >
            <BellIcon />
            <strong>{zoneAlertCount}</strong>
          </button>
        </div>
      </header>

      <TemperatureControlPanel
        zone={zone}
        canRecordManualTemperature={canRecordManualTemperature}
        onRecordManualTemperature={onRecordManualTemperature}
        t={t}
      />

      <div className="zone-page-grid">
        <section className="zone-page-section zone-boxes-section">
          <div className="zone-boxes-heading">
            <div className="section-title">
              <h2>{t('zoneBoxesTitle')}</h2>
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
              {sortedFilteredBoxes.map((box) => {
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
                            {box.latest_measurement.polyp_count} {t('polyps')}
                          </strong>
                          <small>
                            {box.latest_measurement.ephyrae_count} {t('ephyrae')} · {formatDisplayDate(box.latest_measurement.measured_on)}
                          </small>
                        </>
                      ) : (
                        <strong>{t('recentMeasurementMissing')}</strong>
                      )}
                    </span>
                    {box.active_alert_count > 0 ? (
                      <span className="zone-alert-pill">{box.active_alert_count}</span>
                    ) : null}
                    {box.status !== 'active' ? (
                      <span className={`box-life-status is-${status.tone}`}>
                        {status.label}
                      </span>
                    ) : null}
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
            </div>
            <ZoneLatestCountsChart boxes={livingBoxes} t={t} />
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

      {isZoneAlertsOpen ? (
        <ZoneAlertsModal
          items={zoneAlertItems}
          title={zone.name}
          onClose={() => setIsZoneAlertsOpen(false)}
          onOpenBox={onOpenBox}
          onOpenZone={() => undefined}
          t={t}
        />
      ) : null}

    </section>
  );
}

function buildZoneOverviewEntry(zone: ThermalZone, boxes: BoxItem[]): ZoneOverviewEntry {
  const zoneBoxes = boxes.filter((box) => box.thermal_zone?.id === zone.id);
  const activeBoxes = zoneBoxes.filter((box) => box.status === 'active');
  const targetTemperature = parseTemperatureNumber(zone.target_temperature_c);
  const measuredTemperature = parseTemperatureNumber(zone.latest_temperature?.average_temperature_c);
  const missingMeasurements = activeBoxes.filter((box) => !box.latest_measurement).length;
  const temperatureNeedsAttention = targetTemperature === null
    || measuredTemperature === null
    || Math.abs(measuredTemperature - targetTemperature) > 1;
  // The reference salinity is the one kept by hand on the zone (like the
  // capacity). The SalinityMeasurement table it used to read has no ingestion,
  // so it was always empty and every zone looked like it was missing a value.
  const salinityNeedsAttention = zone.salinity_psu == null || zone.salinity_psu === '';

  return {
    zone,
    zoneBoxes,
    livingBoxes: activeBoxes.length,
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

function getZoneAlertItems(entry: ZoneOverviewEntry, t: TFunction): ZoneAlertItem[] {
  const alerts: ZoneAlertItem[] = [];

  if (entry.temperatureNeedsAttention) {
    alerts.push({
      id: `${entry.zone.id}-temperature`,
      level: 'high',
      title: t('temperatureControl'),
      message: entry.targetTemperature === null || entry.measuredTemperature === null
        ? t('temperatureMissing')
        : t('zoneOverviewThermalGap'),
      zone: entry.zone,
    });
  }

  if (entry.salinityNeedsAttention) {
    alerts.push({
      id: `${entry.zone.id}-salinity`,
      level: 'medium',
      title: t('zoneSalinity'),
      message: t('zoneSalinityMissing'),
      zone: entry.zone,
    });
  }

  if (!entry.zone.probes.length) {
    alerts.push({
      id: `${entry.zone.id}-probe`,
      level: 'medium',
      title: t('zoneProbesTitle'),
      message: t('zoneOverviewNoProbe'),
      zone: entry.zone,
    });
  }

  if (entry.missingMeasurements) {
    alerts.push({
      id: `${entry.zone.id}-measurements`,
      level: 'low',
      title: t('latestReadingDate'),
      message: `${entry.missingMeasurements} ${t('zoneOverviewMissingMeasurements')}`,
      zone: entry.zone,
    });
  }

  return alerts;
}

function formatZoneCapacity(capacity: number | null | undefined) {
  return capacity ? String(capacity) : '-';
}

// The API serialises the zone salinity as a string ("35.00"); show it with the
// same single decimal as everywhere else rather than the raw stored scale.
function formatZoneSalinity(salinity: string | null | undefined) {
  if (salinity === null || salinity === undefined || salinity === '') return '-';
  const numeric = Number.parseFloat(salinity);
  return Number.isFinite(numeric) ? `${numeric.toFixed(1)} PSU` : '-';
}

function formatZoneOccupancy(boxCount: number, capacity: number | null | undefined) {
  return capacity ? `${boxCount} / ${capacity}` : String(boxCount);
}

function ZoneAlertsModal({
  items,
  onClose,
  onOpenBox,
  onOpenZone,
  t,
  title,
}: {
  items: ZoneAlertItem[];
  onClose: () => void;
  onOpenBox?: (id: number) => void;
  onOpenZone: (id: number) => void;
  t: TFunction;
  title: string;
}) {
  return (
    <div className="modal-backdrop" role="presentation" onMouseDown={onClose}>
      <section
        className="box-checks-modal zone-alerts-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="zone-alerts-title"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <header className="box-checks-heading zone-alerts-heading">
          <div>
            <h2 id="zone-alerts-title">{t('zoneOverviewAttentionTitle')}</h2>
            <p>{title}</p>
          </div>
          <button type="button" aria-label={t('close')} onClick={onClose}>×</button>
        </header>

        <div className="box-checks-list zone-alerts-list">
          {items.map((item) => (
            <article className={`box-check-item zone-alert-item is-${item.level}`} key={item.id}>
              <span className="check-severity">{getZoneAlertLevelLabel(item.level, t)}</span>
              <div>
                <small>{item.zone.name}</small>
                <strong>{item.title}</strong>
                <p>{item.message}</p>
              </div>
              <div>
                <button
                  type="button"
                  onClick={() => {
                    onClose();
                    if (item.box && onOpenBox) {
                      onOpenBox(item.box.id);
                    } else {
                      onOpenZone(item.zone.id);
                    }
                  }}
                >
                  {t('openBox')}
                </button>
              </div>
            </article>
          ))}

          {!items.length ? (
            <article className="box-check-empty">
              <span className="check-empty-icon">
                <BellIcon />
              </span>
              <div>
                <strong>{t('boxChecksEmptyTitle')}</strong>
                <p>{t('boxChecksEmptyText')}</p>
              </div>
            </article>
          ) : null}
        </div>
      </section>
    </div>
  );
}

function getZoneAlertLevelLabel(level: ZoneAlertItem['level'], t: TFunction) {
  if (level === 'high') return t('checkImportanceHigh');
  if (level === 'medium') return t('checkImportanceMedium');
  return t('checkImportanceInfo');
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

function TemperatureControlPanel({
  zone,
  canRecordManualTemperature,
  onRecordManualTemperature,
  t,
}: {
  zone: ThermalZone;
  canRecordManualTemperature: boolean;
  onRecordManualTemperature: (zoneId: number, payload: ManualTemperaturePayload) => Promise<ThermalZone>;
  t: TFunction;
}) {
  const targetTemperature = parseTemperatureNumber(zone.target_temperature_c);
  const measuredTemperature = parseTemperatureNumber(zone.latest_temperature?.average_temperature_c);
  const minTemperature = parseTemperatureNumber(zone.latest_temperature?.min_temperature_c);
  const maxTemperature = parseTemperatureNumber(zone.latest_temperature?.max_temperature_c);
  const measurementCount = zone.latest_temperature?.measurement_count ?? 0;
  const hasTemperature = measuredTemperature !== null && targetTemperature !== null;
  const hasTemperatureRange = hasTemperature
    && minTemperature !== null
    && maxTemperature !== null
    && (measurementCount > 1 || Math.abs(maxTemperature - minTemperature) > 0.05);
  const delta = hasTemperature ? measuredTemperature - targetTemperature : null;
  const absoluteDelta = delta === null ? null : Math.abs(delta);
  const statusClass = absoluteDelta === null
    ? 'is-missing'
    : absoluteDelta <= 0.5
      ? 'is-ok'
      : 'is-watch';
  const statusLabel = absoluteDelta === null
    ? t('temperatureMissing')
    : absoluteDelta <= 0.5
      ? t('temperatureOk')
      : t('temperatureWatch');
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
  const [temperatureDate, setTemperatureDate] = useState(getTodayInputValue);
  const [manualTemperature, setManualTemperature] = useState('');
  const [isSavingTemperature, setIsSavingTemperature] = useState(false);
  const [temperatureError, setTemperatureError] = useState<string | null>(null);
  const temperatureSourceLabel = zone.latest_temperature
    ? measurementCount > 1
      ? `${t('temperatureContinuousReading')} · ${measurementCount} ${t('temperatureSamples')}`
      : t('temperatureManualReading')
    : t('temperatureMissing');

  useEffect(() => {
    setIsGaugeReady(false);
    const animationFrame = window.requestAnimationFrame(() => setIsGaugeReady(true));
    return () => window.cancelAnimationFrame(animationFrame);
  }, [zone.id, targetTemperature, measuredTemperature, minTemperature, maxTemperature]);

  async function handleManualTemperatureSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (isSavingTemperature || !manualTemperature.trim()) return;

    setIsSavingTemperature(true);
    setTemperatureError(null);

    try {
      await onRecordManualTemperature(zone.id, {
        measured_on: temperatureDate,
        temperature_c: manualTemperature.trim(),
      });
      setManualTemperature('');
    } catch (requestError) {
      setTemperatureError(getErrorMessage(requestError));
    } finally {
      setIsSavingTemperature(false);
    }
  }

  const gaugeStyle = {
    '--temperature-current-position': `${measuredLeft}%`,
    '--temperature-range-start': `${hasTemperatureRange ? rangeStart : measuredLeft}%`,
    '--temperature-range-width': `${hasTemperatureRange ? rangeWidth : 0}%`,
  } as CSSProperties;
  const gaugeClassName = [
    'temperature-gauge',
    hasTemperatureRange ? 'is-range' : 'is-point',
    isGaugeReady ? 'is-ready' : '',
  ].filter(Boolean).join(' ');

  return (
    <section className={`zone-temperature-panel ${statusClass}`}>
      <div className="zone-temperature-heading">
        <div>
          <h2>{t('temperatureControl')}</h2>
          <p>{zone.latest_temperature ? formatDisplayDate(zone.latest_temperature.date) : t('temperatureMissing')}</p>
          <span className="temperature-source-note">{temperatureSourceLabel}</span>
        </div>
        <span className={`temperature-status-chip ${statusClass}`}>{statusLabel}</span>
      </div>

      <div
        className={gaugeClassName}
        style={gaugeStyle}
        aria-label={t('temperatureControl')}
      >
        <span className="temperature-gauge-safe-band" aria-hidden="true" />
        <span className="temperature-gauge-track" aria-hidden="true" />
        {hasTemperatureRange ? <span className="temperature-gauge-range" aria-hidden="true" /> : null}
        {targetTemperature !== null ? (
          <span className="temperature-gauge-target" aria-hidden="true">
            <span>{formatTemperature(targetTemperature)}</span>
          </span>
        ) : null}
        {hasTemperatureRange && minLeft !== null ? <span className="temperature-gauge-cap is-min" style={{ left: `${minLeft}%` }} aria-hidden="true" /> : null}
        {hasTemperatureRange && maxLeft !== null ? <span className="temperature-gauge-cap is-max" style={{ left: `${maxLeft}%` }} aria-hidden="true" /> : null}
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
        <Metric label={t('minTemperature')} value={hasTemperatureRange ? formatTemperature(minTemperature ?? undefined) : '-'} />
        <Metric label={t('maxTemperature')} value={hasTemperatureRange ? formatTemperature(maxTemperature ?? undefined) : '-'} />
      </div>

      {canRecordManualTemperature ? (
        <form className="manual-temperature-form" onSubmit={handleManualTemperatureSubmit}>
          <div className="manual-temperature-heading">
            <strong>{t('manualTemperatureTitle')}</strong>
            <span>{zone.name}</span>
          </div>
          <label>
            <span>{t('manualTemperatureDate')}</span>
            <input
              type="date"
              value={temperatureDate}
              onChange={(event) => setTemperatureDate(event.target.value)}
              required
            />
          </label>
          <label>
            <span>{t('manualTemperatureValue')}</span>
            <input
              type="number"
              inputMode="decimal"
              step="0.1"
              value={manualTemperature}
              onChange={(event) => setManualTemperature(event.target.value)}
              required
            />
          </label>
          <button type="submit" disabled={isSavingTemperature || !manualTemperature.trim()}>
            {isSavingTemperature ? t('saving') : t('manualTemperatureSave')}
          </button>
          {temperatureError ? <p className="inline-error">{temperatureError}</p> : null}
        </form>
      ) : null}
    </section>
  );
}

function ZoneLatestCountsChart({ boxes, t }: { boxes: BoxItem[]; t: TFunction }) {
  const measuredBoxes = boxes
    .filter((box) => box.latest_measurement)
    .sort((first, second) => {
      const firstDate = first.latest_measurement?.measured_on ?? '';
      const secondDate = second.latest_measurement?.measured_on ?? '';
      return secondDate.localeCompare(firstDate);
    })
    .slice(0, 6);

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
          <article className="zone-count-row" key={box.id}>
            <div className="zone-count-identity">
              <strong>{box.global_code}</strong>
              <span>{box.species.scientific_name}</span>
              <small>{formatDisplayDate(measurement.measured_on)}</small>
            </div>
            <div className="zone-count-values">
              <span>
                <strong>{measurement.polyp_count}</strong>
                <small>{t('polyps')}</small>
              </span>
              <span>
                <strong>{measurement.ephyrae_count}</strong>
                <small>{t('ephyrae')}</small>
              </span>
            </div>
            <div className="zone-count-bars">
              <i className="zone-count-bar is-polyps" style={{ width: polypWidth }} />
              <i className="zone-count-bar is-ephyrae" style={{ width: ephyraeWidth }} />
            </div>
          </article>
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

function getTodayInputValue() {
  const today = new Date();
  today.setMinutes(today.getMinutes() - today.getTimezoneOffset());
  return today.toISOString().slice(0, 10);
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
