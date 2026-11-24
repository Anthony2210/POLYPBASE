import { type CSSProperties, type FormEvent, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import type { Language, Translator } from '../i18n';
import type { BoxItem, ThermalZone } from '../types';
import { ApiError, ApiResourceCancelledError } from '../api/client';
import useProbeMonitoring from '../hooks/useProbeMonitoring';
import type {
  ManualSalinityPayload,
  ManualSalinityUpdatePayload,
  ManualTemperaturePayload,
} from '../types/admin';
import { formatDisplayDate, formatDisplayDateTime, getDocumentLocale } from '../utils/dateFormat';
import { formatProbeReadingTime, getProbeElapsedTime } from '../utils/probeStatus';
import { getErrorMessage } from '../utils/errors';
import BoxTrackingPreview from './BoxTrackingPreview';
import DetailBackButton from './DetailBackButton';
import ModalPortal from './ModalPortal';
import PageLoader from './PageLoader';
import PolypbaseIcon from './PolypbaseIcon';
import ThermalMonitoringChart from './ThermalMonitoringChart';
import { useZoneChartReadings, ZoneProbeChart } from './ZoneProbeCharts';
import { ZoneRecentMovements } from './ZoneMovementHistory';


type ZoneOverviewEntry = {
  zone: ThermalZone;
  livingBoxes: number;
  targetTemperature: number | null;
  measuredTemperature: number | null;
  referenceTemperature: number | null;
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
  t: Translator;
}) {
  const zoneEntries = zones.map((zone) => buildZoneOverviewEntry(zone, boxes));
  const sortedEntries = [...zoneEntries].sort((first, second) => {
    const firstTemperature = first.referenceTemperature ?? Number.POSITIVE_INFINITY;
    const secondTemperature = second.referenceTemperature ?? Number.POSITIVE_INFINITY;
    return firstTemperature - secondTemperature;
  });

  return (
    <section className="zone-overview-shell">
      {isLoading ? (
        <PageLoader variant="zones" label={t('zonesTitle')} />
      ) : (
        <div className="zone-overview">
          <div className="zone-overview-grid">
            {sortedEntries.map((entry) => {
              const thermalStatus = getZoneThermalStatus(entry);
              const occupancyPercentage = getZoneOccupancyPercentage(
                entry.livingBoxes,
                entry.zone.capacity,
              );

              return (
                <article
                  className={`zone-card is-${thermalStatus}`}
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

                    <span className="zone-card-readings">
                      <span className="zone-card-reading is-primary">
                        <small>{t('temperatureShort')}</small>
                        <strong>{formatTemperature(entry.measuredTemperature ?? undefined)}</strong>
                      </span>
                      <span className="zone-card-reading">
                        <small>{t('zoneTarget')}</small>
                        <strong>{formatTemperature(entry.targetTemperature ?? undefined)}</strong>
                      </span>
                    </span>

                    <span className="zone-card-thermal-line" aria-hidden="true">
                      {entry.targetTemperature !== null ? <span className="zone-card-target" /> : null}
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
                      <span className="zone-card-fact">
                        <small>{t('zoneSalinity')}</small>
                        <strong>
                          {formatSalinity(entry.zone.latest_salinity?.salinity_psu)}
                        </strong>
                      </span>
                      <span className="zone-card-fact is-capacity">
                        <span className="zone-card-fact-heading">
                          <small>{t('zoneOccupancy')}</small>
                          <strong className="zone-occupancy">
                            {formatZoneOccupancy(entry.livingBoxes, entry.zone.capacity)}
                          </strong>
                        </span>
                        <span
                          className="zone-card-capacity-track"
                          style={{ '--zone-occupancy': `${occupancyPercentage}%` } as CSSProperties}
                          aria-hidden="true"
                        >
                          <span />
                        </span>
                      </span>
                    </span>
                  </button>
                </article>
              );
            })}
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
  canRecordManualTemperature,
  onBack,
  onOpenBox,
  onOpenBoxes,
  onOpenHistory,
  onRecordManualSalinity,
  onRefreshZoneSalinityCapability,
  onRecordManualTemperature,
  onUpdateManualSalinity,
  t,
}: {
  boxes: BoxItem[];
  isLoading: boolean;
  language: Language;
  zone: ThermalZone | null;
  canRecordManualTemperature: boolean;
  onBack: () => void;
  onOpenBox: (boxId: number) => void;
  onOpenBoxes: (zoneId: number) => void;
  onOpenHistory: (zoneId: number, direction: 'arrival' | 'departure') => void;
  onRecordManualSalinity: (zoneId: number, payload: ManualSalinityPayload) => Promise<ThermalZone>;
  onRefreshZoneSalinityCapability: (zoneId: number) => Promise<void>;
  onRecordManualTemperature: (zoneId: number, payload: ManualTemperaturePayload) => Promise<ThermalZone>;
  onUpdateManualSalinity: (
    zoneId: number,
    measurementId: number,
    payload: ManualSalinityUpdatePayload,
  ) => Promise<ThermalZone>;
  t: Translator;
}) {
  const backAction = (
    <DetailBackButton label={t('back')} onBack={onBack} desktopClassName="zone-back-button" />
  );

  if (isLoading) {
    return <PageLoader variant="zone" label={t('zoneSheet')} />;
  }

  if (!zone) {
    return (
      <section className="zone-page">
        {backAction}
        <p className="muted compact-text">{t('noZone')}</p>
      </section>
    );
  }

  const zoneBoxes = boxes.filter((box) => box.thermal_zone?.id === zone.id);
  const boxDirectoryLabel = t('zoneBoxesDirectoryAction');

  return (
    <section className="zone-page">
      {backAction}

      <header className="entity-header entity-header--zone zone-sheet-hero">
        <div className="entity-header__identity zone-sheet-title">
          <p className="box-page-label">{t('zoneSheet')}</p>
          <h2>{zone.name}</h2>
          <span>{zone.organization.name}</span>
        </div>

      </header>

      <TemperatureControlPanel
        key={`temperature-${zone.id}`}
        zone={zone}
        canRecordManualTemperature={canRecordManualTemperature}
        onRecordManualTemperature={onRecordManualTemperature}
        t={t}
      />

      <ZoneFunctionalSections
        key={zone.id}
        language={language}
        onOpenBox={onOpenBox}
        onOpenHistory={(direction) => onOpenHistory(zone.id, direction)}
        boxCount={zoneBoxes.length}
        canRecordManualSalinity={canRecordManualTemperature}
        boxDirectoryLabel={boxDirectoryLabel}
        onOpenBoxes={onOpenBoxes}
        onRecordManualSalinity={onRecordManualSalinity}
        onRefreshZoneSalinityCapability={onRefreshZoneSalinityCapability}
        onUpdateManualSalinity={onUpdateManualSalinity}
        zone={zone}
        t={t}
      />

    </section>
  );
}

function ZoneFunctionalSections({
  language,
  onOpenBox,
  onOpenHistory,
  boxCount,
  boxDirectoryLabel,
  canRecordManualSalinity,
  onOpenBoxes,
  onRecordManualSalinity,
  onRefreshZoneSalinityCapability,
  onUpdateManualSalinity,
  zone,
  t,
}: {
  language: Language;
  onOpenBox: (boxId: number) => void;
  onOpenHistory: (direction: 'arrival' | 'departure') => void;
  boxCount: number;
  boxDirectoryLabel: string;
  canRecordManualSalinity: boolean;
  onOpenBoxes: (zoneId: number) => void;
  onRecordManualSalinity: (zoneId: number, payload: ManualSalinityPayload) => Promise<ThermalZone>;
  onRefreshZoneSalinityCapability: (zoneId: number) => Promise<void>;
  onUpdateManualSalinity: (
    zoneId: number,
    measurementId: number,
    payload: ManualSalinityUpdatePayload,
  ) => Promise<ThermalZone>;
  zone: ThermalZone;
  t: Translator;
}) {
  const [editingSalinityId, setEditingSalinityId] = useState<number | null>(null);
  const [isEditingSalinity, setIsEditingSalinity] = useState(false);
  const [salinityDate, setSalinityDate] = useState(getTodayInputValue);
  const [salinityValue, setSalinityValue] = useState('');
  const [salinityNotes, setSalinityNotes] = useState('');
  const [isSavingSalinity, setIsSavingSalinity] = useState(false);
  const [salinityError, setSalinityError] = useState<string | null>(null);

  const capacity = zone.capacity;
  const occupancyPercentage = capacity === null
    ? 0
    : capacity > 0
      ? Math.min(100, Math.max(0, (boxCount / capacity) * 100))
      : boxCount > 0
        ? 100
        : 0;

  function openSalinityEditor(mode: 'create' | 'correct') {
    const latestSalinity = mode === 'correct' && zone.latest_salinity?.can_edit ? zone.latest_salinity : null;
    if (mode === 'correct' && !latestSalinity) return;
    setEditingSalinityId(latestSalinity?.id ?? null);
    setSalinityDate(latestSalinity?.measured_on ?? getTodayInputValue());
    setSalinityValue(latestSalinity == null ? '' : String(latestSalinity.salinity_psu));
    setSalinityNotes(latestSalinity?.notes ?? '');
    setSalinityError(null);
    setIsEditingSalinity(true);
  }

  function closeSalinityEditor() {
    setSalinityError(null);
    setIsEditingSalinity(false);
  }

  async function handleSalinitySubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (isSavingSalinity || !salinityValue.trim()) return;

    setIsSavingSalinity(true);
    setSalinityError(null);
    try {
      if (editingSalinityId !== null) {
        await onUpdateManualSalinity(zone.id, editingSalinityId, {
          salinity_psu: salinityValue.trim(),
          notes: salinityNotes.trim(),
        });
      } else {
        await onRecordManualSalinity(zone.id, {
          measured_on: salinityDate,
          salinity_psu: salinityValue.trim(),
          notes: salinityNotes.trim(),
        });
      }
      setIsEditingSalinity(false);
    } catch (requestError) {
      if (requestError instanceof ApiResourceCancelledError) return;
      if (requestError instanceof ApiError && requestError.data && typeof requestError.data === 'object'
        && 'code' in requestError.data && requestError.data.code === 'salinity_edit_window_expired') {
        try {
          await onRefreshZoneSalinityCapability(zone.id);
          setIsEditingSalinity(false);
          setSalinityError(t('zoneSalinityEditExpired'));
        } catch (refreshError) {
          if (refreshError instanceof ApiResourceCancelledError) return;
          setSalinityError(getErrorMessage(refreshError));
        }
      } else {
        setSalinityError(getErrorMessage(requestError));
      }
    } finally {
      setIsSavingSalinity(false);
    }
  }

  return (
    <>
      {isEditingSalinity ? (
        <section className="box-section measurement-form-section measurement-module is-expanded zone-salinity-section zone-salinity-editor-section">
          <form className="fake-form" onSubmit={handleSalinitySubmit}>
            <fieldset className="measurement-editor-fields" disabled={isSavingSalinity}>
              <div className="section-title">
                <h2>{t(editingSalinityId === null ? 'zoneSalinityCreateTitle' : 'zoneSalinityEditTitle')}</h2>
                <span>{formatDisplayDate(salinityDate)}</span>
              </div>
              <div className="measurement-entry-grid zone-salinity-entry-grid">
                <label className="measurement-date-field">
                  <span className="measurement-field-label">{t('manualSalinityDate')}</span>
                  <input
                    type="date"
                    value={salinityDate}
                    onChange={(event) => setSalinityDate(event.target.value)}
                    disabled={editingSalinityId !== null}
                    aria-invalid={salinityError ? 'true' : undefined}
                    aria-describedby={salinityError ? 'zone-salinity-error' : undefined}
                    required
                  />
                </label>
                <label>
                  <span className="measurement-field-label">{t('manualSalinityValue')}</span>
                  <input
                    type="number"
                    inputMode="decimal"
                    step="0.01"
                    value={salinityValue}
                    onChange={(event) => setSalinityValue(event.target.value)}
                    aria-invalid={salinityError ? 'true' : undefined}
                    aria-describedby={salinityError ? 'zone-salinity-error' : undefined}
                    required
                  />
                </label>
                <label className="notes-field">
                  <span className="measurement-field-label">{t('zoneSalinityNotes')}</span>
                  <textarea
                    rows={3}
                    value={salinityNotes}
                    onChange={(event) => setSalinityNotes(event.target.value)}
                  />
                </label>
              </div>
              {salinityError ? <p className="inline-error" id="zone-salinity-error">{salinityError}</p> : null}
              <div className="measurement-actions-row zone-salinity-actions">
                <button className="primary-button" type="submit" disabled={isSavingSalinity}>
                  {isSavingSalinity ? t('saving') : t('manualSalinitySave')}
                </button>
                <button className="secondary-button measurement-cancel-button" type="button" onClick={closeSalinityEditor} disabled={isSavingSalinity}>
                  {t('cancel')}
                </button>
              </div>
            </fieldset>
          </form>
        </section>
      ) : (
        <section className={`last-reading-card measurement-summary zone-functional-section zone-salinity-section${zone.latest_salinity?.notes ? ' has-comment' : ''}${canRecordManualSalinity ? ' has-edit-capability' : ''}`}>
          <div>
            <h2>{t('zoneSalinityLatestTitle')}</h2>
            <span>
              {zone.latest_salinity
                ? formatDisplayDate(zone.latest_salinity.measured_on)
                : t('zoneSalinityNoReading')}
            </span>
          </div>
          <Metric label={t('zoneSalinity')} value={formatZoneSalinity(zone.latest_salinity?.salinity_psu)} />
          <div className="last-reading-comment">
            <small>{t('lastComment')}</small>
            <p>{zone.latest_salinity?.notes || t('noComment')}</p>
          </div>
          {canRecordManualSalinity ? (
            <div className="zone-salinity-summary-actions">
              {zone.latest_salinity?.can_edit ? (
                <button
                  className="icon-button measurement-summary-edit-button"
                  type="button"
                  aria-label={t('zoneSalinityEditAction')}
                  title={t('zoneSalinityEditAction')}
                  onClick={() => openSalinityEditor('correct')}
                >
                  <PolypbaseIcon name="edit" size={18} />
                </button>
              ) : null}
              <button
                className="icon-button measurement-summary-edit-button"
                type="button"
                aria-label={t('zoneSalinityCreateAction')}
                title={t('zoneSalinityCreateAction')}
                onClick={() => openSalinityEditor('create')}
              >
                <PolypbaseIcon name="plus" size={18} />
              </button>
            </div>
          ) : null}
        </section>
      )}
      {!isEditingSalinity && salinityError ? <p className="inline-error" role="alert">{salinityError}</p> : null}

      <div className="zone-operational-layout">
        <ZoneRecentMovements
          language={language}
          onOpenBox={onOpenBox}
          onOpenHistory={onOpenHistory}
          t={t}
          zoneId={zone.id}
        />
        <div className="zone-operational-sidebar">
          <section className="zone-page-section zone-functional-section zone-occupancy-section">
            <h2>{t('zoneOccupancy')}</h2>
            <div className="zone-section-reading">
              <strong>{formatZoneOccupancy(boxCount, capacity)} {t('boxes').toLowerCase()}</strong>
              {capacity === null ? <span>{t('zoneCapacityMissing')}</span> : null}
            </div>
            {capacity !== null ? (
              <span className="zone-occupancy-track" aria-hidden="true">
                <span style={{ width: `${occupancyPercentage}%` }} />
              </span>
            ) : null}
            <button
              className="secondary-button zone-box-directory-trigger"
              type="button"
              onClick={() => onOpenBoxes(zone.id)}
            >
              {boxDirectoryLabel}
            </button>
          </section>

          <section className="zone-page-section zone-functional-section zone-probes-section">
            <h2>{t('zoneProbesTitle')}</h2>
            <div className="zone-probe-list">
              {zone.probes.length ? zone.probes.map((probe) => (
                <p key={probe.id}>
                  <strong>{probe.code}</strong>
                  {probe.location ? <span>{probe.location}</span> : null}
                </p>
              )) : <p className="muted compact-text">{t('zoneNoProbe')}</p>}
            </div>
          </section>
        </div>
      </div>
    </>
  );
}


type ZoneBoxSpeciesGroup = {
  species: BoxItem['species'];
  boxCount: number;
  strains: Array<{
    strain: BoxItem['strain'];
    boxes: BoxItem[];
  }>;
};

export function ZoneBoxesPage({
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
  t: Translator;
}) {
  const collator = useMemo(
    () => new Intl.Collator(language, { numeric: true, sensitivity: 'base' }),
    [language],
  );
  const zoneBoxes = useMemo(
    () => boxes.filter((box) => box.thermal_zone?.id === zone?.id),
    [boxes, zone?.id],
  );

  const groupedBoxes = useMemo(
    () => buildZoneBoxSpeciesGroups(zoneBoxes, collator),
    [collator, zoneBoxes],
  );

  if (isLoading) {
    return <PageLoader variant="zone" label={t('zoneBoxesDirectoryTitle')} />;
  }

  if (!zone) {
    return (
      <section className="zone-page">
        <DetailBackButton label={t('back')} onBack={onBack} desktopClassName="zone-back-button" />
        <p className="muted compact-text">{t('noZone')}</p>
      </section>
    );
  }

  return (
    <section className="zone-page zone-box-directory-page">
      <DetailBackButton label={t('back')} onBack={onBack} desktopClassName="zone-back-button" />

      <header className="entity-header entity-header--zone zone-sheet-hero zone-directory-hero">
        <div className="entity-header__identity zone-sheet-title">
          <p className="box-page-label">{t('zoneBoxesDirectoryTitle')}</p>
          <h2>{zone.name}</h2>
          <span>{zone.organization.name}</span>
        </div>
      </header>

      {groupedBoxes.length ? (
        <div className="zone-species-directory">
          {groupedBoxes.map((speciesGroup) => (
            <section className="zone-species-section" key={speciesGroup.species.id}>
              <header className="zone-species-heading">
                <div>
                  <small>{speciesGroup.species.genus_species_code}</small>
                  <h2>{speciesGroup.species.scientific_name}</h2>
                  {speciesGroup.species.common_name ? <span>{speciesGroup.species.common_name}</span> : null}
                </div>
                <p>
                  <strong>{speciesGroup.boxCount}</strong>
                  <span>{t('boxes')}</span>
                </p>
              </header>

              <div className="zone-strain-list">
                {speciesGroup.strains.map((strainGroup) => (
                  <section className="zone-strain-section" key={strainGroup.strain.id}>
                    <header className="zone-strain-heading">
                      <div>
                        <small>{t('taxonomyStrains')}</small>
                        <strong>{strainGroup.strain.code}</strong>
                      </div>
                      <span>{strainGroup.boxes.length} {t('boxes')}</span>
                    </header>
                    <div className="zone-directory-box-list">
                      {strainGroup.boxes.map((box) => {
                        const measurement = box.latest_measurement;

                        return (
                          <div className="zone-directory-box-row" key={box.id}>
                            <div className="box-inventory-identity zone-directory-box-identity">
                              <BoxTrackingPreview
                                boxId={box.id}
                                code={box.global_code}
                                speciesName={box.species.scientific_name}
                                language={language}
                                onOpenBox={(boxId) => onOpenBox(boxId)}
                                t={t}
                              />
                            </div>
                            <button
                              className="zone-directory-box-open"
                              type="button"
                              aria-label={`${t('openBox')} ${box.global_code}`}
                              onClick={() => onOpenBox(box.id)}
                            >
                              <span className="zone-directory-counts">
                                <span className="is-polyps"><strong>{box.current_polyp_state.polyp_count ?? '-'}</strong> {t('polyps')}</span>
                                <span className="is-ephyrae"><strong>{measurement?.ephyrae_count ?? '-'}</strong> {t('ephyrae')}</span>
                              </span>
                              <span className="zone-directory-dates">
                                <span>
                                  <small>{t('zoneCurrentStaySince')}</small>
                                  {box.current_location_started_at ? (
                                    <time dateTime={box.current_location_started_at}>{formatDisplayDate(box.current_location_started_at)}</time>
                                  ) : <strong>-</strong>}
                                </span>
                                <span>
                                  <small>{t('latestReadingDate')}</small>
                                  {measurement ? (
                                    <time dateTime={measurement.measured_on}>{formatDisplayDate(measurement.measured_on)}</time>
                                  ) : <strong>-</strong>}
                                </span>
                              </span>

                            </button>
                          </div>
                        );
                      })}
                    </div>
                  </section>
                ))}
              </div>
            </section>
          ))}
        </div>
      ) : (
        <div className="empty-state zone-directory-empty">
          <strong>{t('zoneBoxesDirectoryEmpty')}</strong>
        </div>
      )}
    </section>
  );
}

function buildZoneBoxSpeciesGroups(boxes: BoxItem[], collator: Intl.Collator): ZoneBoxSpeciesGroup[] {
  const speciesGroups = new Map<number, {
    species: BoxItem['species'];
    strains: Map<number, { strain: BoxItem['strain']; boxes: BoxItem[] }>;
  }>();

  for (const box of boxes) {
    const speciesGroup = speciesGroups.get(box.species.id) ?? {
      species: box.species,
      strains: new Map(),
    };
    const strainGroup = speciesGroup.strains.get(box.strain.id) ?? {
      strain: box.strain,
      boxes: [],
    };
    strainGroup.boxes.push(box);
    speciesGroup.strains.set(box.strain.id, strainGroup);
    speciesGroups.set(box.species.id, speciesGroup);
  }

  return Array.from(speciesGroups.values())
    .sort((first, second) => collator.compare(
      first.species.scientific_name,
      second.species.scientific_name,
    ))
    .map((group) => ({
      species: group.species,
      boxCount: Array.from(group.strains.values()).reduce(
        (total, strainGroup) => total + strainGroup.boxes.length,
        0,
      ),
      strains: Array.from(group.strains.values())
        .sort((first, second) => collator.compare(first.strain.code, second.strain.code))
        .map((strainGroup) => ({
          ...strainGroup,
          boxes: [...strainGroup.boxes].sort((first, second) => (
            collator.compare(first.global_code, second.global_code)
          )),
        })),
    }));
}


function buildZoneOverviewEntry(zone: ThermalZone, boxes: BoxItem[]): ZoneOverviewEntry {
  const zoneBoxes = boxes.filter((box) => box.thermal_zone?.id === zone.id);
  const activeBoxes = zoneBoxes.filter((box) => box.status === 'active');
  const targetTemperature = parseTemperatureNumber(zone.target_temperature_c);
  const measuredTemperature = parseTemperatureNumber(zone.latest_temperature?.average_temperature_c);

  return {
    zone,
    livingBoxes: activeBoxes.length,
    targetTemperature,
    measuredTemperature,
    referenceTemperature: targetTemperature ?? measuredTemperature,
  };
}

function getZoneThermalStatus(entry: ZoneOverviewEntry) {
  return entry.measuredTemperature === null ? 'missing' : 'recorded';
}

function getZoneOccupancyPercentage(boxCount: number, capacity: number | null | undefined) {
  if (!capacity || capacity <= 0) return 0;
  return Math.min(100, Math.max(0, (boxCount / capacity) * 100));
}

// The API serialises the zone salinity as a string ("35.00"); show it with the
// same single decimal as everywhere else rather than the raw stored scale.
function formatZoneSalinity(salinity: string | number | null | undefined) {
  if (salinity === null || salinity === undefined || salinity === '') return '-';
  const numeric = typeof salinity === 'number' ? salinity : Number.parseFloat(salinity);
  return Number.isFinite(numeric) ? `${numeric.toFixed(1)} PSU` : '-';
}

function formatZoneOccupancy(boxCount: number, capacity: number | null | undefined) {
  return capacity == null ? String(boxCount) : `${boxCount} / ${capacity}`;
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
  t: Translator;
}) {
  const hasCollectedProbe = zone.probes.some((probe) => probe.is_collected);
  const chart = useZoneChartReadings(zone, hasCollectedProbe);
  const [isEditingTemperature, setIsEditingTemperature] = useState(false);
  const { monitoring: probeMonitoring, error: monitoringError, revalidate: revalidateMonitoring } = useProbeMonitoring(zone, chart.initial);
  const usesProbes = probeMonitoring?.has_collected_probes ?? false;
  const [statusNow, setStatusNow] = useState(Date.now);
  const lastProbeReading = probeMonitoring?.last_reading_at;
  useEffect(() => {
    if (!usesProbes || !lastProbeReading) return;
    const updateNow = () => setStatusNow(Date.now());
    updateNow();
    const timer = globalThis.setInterval(updateNow, 60_000);
    globalThis.addEventListener('focus', updateNow);
    document.addEventListener('visibilitychange', updateNow);
    return () => {
      globalThis.clearInterval(timer);
      globalThis.removeEventListener('focus', updateNow);
      document.removeEventListener('visibilitychange', updateNow);
    };
  }, [usesProbes, lastProbeReading]);
  const elapsed = getProbeElapsedTime(lastProbeReading, statusNow);
  const elapsedDuration = elapsed
    ? t(elapsed.unit === 'minute' ? 'probeElapsedMinutes' : elapsed.unit === 'hour' ? 'probeElapsedHours' : 'probeElapsedDays')
      .replace('{count}', String(elapsed.count))
    : null;
  // The server decides when probes are silent long enough to allow manual entry.
  const canEnterManually = canRecordManualTemperature
    && (!usesProbes || (probeMonitoring?.manual_entry_available ?? false));
  const [temperatureDate, setTemperatureDate] = useState(getTodayInputValue);
  const [manualTemperature, setManualTemperature] = useState('');
  const [isSavingTemperature, setIsSavingTemperature] = useState(false);
  const [temperatureError, setTemperatureError] = useState<string | null>(null);
  const temperatureActionRef = useRef<HTMLButtonElement>(null);
  const restoreTemperatureFocus = useRef(false);
  useLayoutEffect(() => {
    if (!isEditingTemperature && restoreTemperatureFocus.current) {
      temperatureActionRef.current?.focus();
      restoreTemperatureFocus.current = false;
    }
  }, [isEditingTemperature]);


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
      setIsEditingTemperature(false);
    } catch (requestError) {
      if (requestError instanceof ApiResourceCancelledError) return;
      setTemperatureError(getErrorMessage(requestError));
    } finally {
      setIsSavingTemperature(false);
    }
  }

  function closeTemperatureEditor() {
    setTemperatureError(null);
    setManualTemperature('');
    setIsEditingTemperature(false);
  }

  return (
    <>
    <section className="zone-temperature-panel">
      <div className="zone-temperature-heading">
        <div>
          <h2>{t('temperatureControl')}</h2>
          <p>{t('thermalMonitoringPeriod')}</p>
        </div>
        {canEnterManually && !isEditingTemperature ? (
          <button
            className="icon-button zone-temperature-action"
            ref={temperatureActionRef}
            type="button"
            aria-label={t('manualTemperatureAction')}
            title={usesProbes ? t('probeManualFallbackAction') : t('manualTemperatureAction')}
            onClick={() => {
              restoreTemperatureFocus.current = true;
              setIsEditingTemperature(true);
            }}
          >
            <PolypbaseIcon name="plus" size={18} />
          </button>
        ) : null}
      </div>

      <ThermalMonitoringChart zone={zone} t={t} />

      {usesProbes && probeMonitoring || monitoringError ? (
        <div className="zone-probe-status">
          {usesProbes && probeMonitoring ? (
            <div className="zone-probe-reading">
              <div className="zone-probe-reading-heading">
                <p>
                  <small>{t('probeLastReading')}</small>
                  <strong>
                    {lastProbeReading && elapsedDuration ? (
                      <>
                        <time dateTime={lastProbeReading} title={formatDisplayDateTime(lastProbeReading)}>
                          {formatProbeReadingTime(lastProbeReading, getDocumentLocale())}
                        </time>
                        {` (${t('probeReadingAgo').replace('{duration}', elapsedDuration)})`}
                      </>
                    ) : t('probeNoReadingYet')}
                  </strong>
                </p>
              </div>
            </div>
          ) : null}
          {monitoringError ? (
            <div role="status">
              <p>{t('probeMonitoringError')}</p>
              <button className="secondary-button" type="button" onClick={revalidateMonitoring}>{t('probeRetry')}</button>
            </div>
          ) : null}
        </div>
      ) : null}

      {isEditingTemperature ? (
        <TemperatureEntryModal
          onCancel={closeTemperatureEditor}

          isSaving={isSavingTemperature}
          onSubmit={handleManualTemperatureSubmit}
          date={temperatureDate}
          onDateChange={setTemperatureDate}
          value={manualTemperature}
          onValueChange={setManualTemperature}
          error={temperatureError}
          t={t}
        />
      ) : null}
    </section>
    {hasCollectedProbe ? <ZoneProbeChart chart={chart} zone={zone} t={t} /> : null}
    </>
  );
}

function TemperatureEntryModal({
  onCancel, isSaving, onSubmit, date, onDateChange, value, onValueChange, error, t,
}: {
  onCancel: () => void;

  isSaving: boolean;
  onSubmit: (event: FormEvent<HTMLFormElement>) => void;
  date: string;
  onDateChange: (value: string) => void;
  value: string;
  onValueChange: (value: string) => void;
  error: string | null;
  t: Translator;
}) {
  const dialogRef = useRef<HTMLElement>(null);
  const savingRef = useRef(isSaving);
  savingRef.current = isSaving;

  useLayoutEffect(() => {
    dialogRef.current?.focus({ preventScroll: true });
  }, []);

  useEffect(() => {
    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === 'Escape') {
        if (!savingRef.current) onCancel();
        return;
      }
      if (event.key !== 'Tab') return;
      const dialog = dialogRef.current;
      if (!dialog) return;
      const controls = Array.from(dialog.querySelectorAll<HTMLElement>('input:not(:disabled), button:not(:disabled)'));
      const first = controls[0];
      const last = controls[controls.length - 1];
      if (!first || !last) return;
      if (event.shiftKey && (document.activeElement === first || !dialog.contains(document.activeElement))) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && (document.activeElement === last || !dialog.contains(document.activeElement))) {
        event.preventDefault();
        first.focus();
      }
    }
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [onCancel]);

  return (
    <ModalPortal>
      <div className="modal-backdrop" onMouseDown={() => { if (!isSaving) onCancel(); }}>
        <section
          ref={dialogRef}
          className="zone-temperature-modal"
          tabIndex={-1}
          role="dialog"
          aria-modal="true"
          aria-labelledby="zone-temperature-modal-title"
          onMouseDown={(event) => event.stopPropagation()}
        >
          <div className="zone-temperature-modal-heading">
            <h2 id="zone-temperature-modal-title">{t('manualTemperatureTitle')}</h2>
            <button className="modal-close-button" type="button" aria-label={t('cancel')} onClick={onCancel} disabled={isSaving}>
              <PolypbaseIcon name="close" size={18} />
            </button>
          </div>
          <form className="zone-inline-editor" onSubmit={onSubmit}>
          <label>
            <span className="measurement-field-label">{t('manualTemperatureDate')}</span>
            <input
              type="date"
              value={date}
              onChange={(event) => onDateChange(event.target.value)}
              aria-invalid={error ? 'true' : undefined}
              aria-describedby={error ? 'zone-temperature-error' : undefined}
              required
            />
          </label>
          <label>
            <span className="measurement-field-label">{t('manualTemperatureValue')}</span>
            <input
              type="number"
              inputMode="decimal"
              step="0.01"
              value={value}
              onChange={(event) => onValueChange(event.target.value)}
              aria-invalid={error ? 'true' : undefined}
              aria-describedby={error ? 'zone-temperature-error' : undefined}
              required
            />
          </label>
          {error ? <p className="inline-error" id="zone-temperature-error" role="alert">{error}</p> : null}
          <div className="zone-inline-editor-actions">
            <button className="primary-button" type="submit" disabled={isSaving}>
              {isSaving ? t('saving') : t('manualTemperatureSave')}
            </button>
            <button className="secondary-button" type="button" onClick={onCancel} disabled={isSaving}>
              {t('cancel')}
            </button>
          </div>
          </form>
        </section>
      </div>
    </ModalPortal>
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
