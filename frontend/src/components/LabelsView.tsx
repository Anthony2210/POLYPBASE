import { useMemo, useState } from 'react';

import { ChevronDown, ChevronRight, Printer } from 'lucide-react';

import type { Language, Translator } from '../i18n';
import type { BoxItem, UserProfile } from '../types';
import {
  DEFAULT_QR_LABEL_PRINT_SETTINGS,
  buildQrLabelItem,
  printQrLabels,
  type QrLabelItem,
} from '../utils/qrLabels';
import BoxTrackingPreview from './BoxTrackingPreview';

import PageLoader from './PageLoader';

type LabelsViewLabels = {
  allZones: string;

  noZone: string;
  qrLabelAddResults: (count: number) => string;
  qrLabelAddResultsCompact: (count: number) => string;
  qrLabelClearSelection: string;
  qrLabelNoEligibleBoxes: string;
  qrLabelNoMatches: string;

  qrLabelPrintCount: (count: number) => string;

  qrLabelSearchTitle: string;
  qrLabelSelectedSingular: string;
  qrLabelSelectedPlural: string;

  pageTitle: string;
  qrLabelSearchPlaceholder: string;
  qrLabelSpeciesCount: (count: number) => string;
  qrLabelSpeciesSelected: (count: number) => string;
  qrLabelSelectSpecies: (count: number, species: string) => string;
  qrLabelDeselectSpecies: (count: number, species: string) => string;

  selectBox: string;
  zoneLabel: string;
};

type SpeciesGroup = { key: number; name: string; boxes: BoxItem[] };

export default function LabelsView({
  boxes,
  isLoading,
  labels,
  language,
  onAddQrLabel,
  onClearQrLabelSelection,
  onRemoveQrLabel,
  onOpenBox,
  profile,
  qrLabelSelection,
  t,
}: {
  boxes: BoxItem[];
  isLoading: boolean;
  labels: LabelsViewLabels;
  language: Language;
  onAddQrLabel: (label: QrLabelItem) => void;
  onClearQrLabelSelection: () => void;
  onRemoveQrLabel: (labelId: number) => void;
  onOpenBox: (boxId: number, code: string) => void;
  profile: UserProfile | null;
  qrLabelSelection: QrLabelItem[];
  t: Translator;
}) {
  const [labelSearch, setLabelSearch] = useState('');
  const [zoneFilter, setZoneFilter] = useState('all');
  const [expandedSpecies, setExpandedSpecies] = useState<Set<number>>(() => new Set());
  const printSettings = DEFAULT_QR_LABEL_PRINT_SETTINGS;
  const labelCutoffDate = useMemo(() => getRecentLabelCutoffDate(), []);
  const canManageQrLabels = profile ? userCanManageQrLabels(profile) : false;
  const labelOrganizationIds = useMemo(
    () => (profile ? getQrLabelOrganizationIds(profile) : new Set<number>()),
    [profile],
  );
  const normalizedLabelSearch = labelSearch.trim().toLocaleLowerCase();
  const authorizedBoxes = useMemo(() => {
    if (!profile || !canManageQrLabels) return [];
    return boxes.filter((box) => !labelOrganizationIds || labelOrganizationIds.has(box.organization.id));
  }, [boxes, canManageQrLabels, labelOrganizationIds, profile]);
  const authorizedBoxIds = useMemo(() => new Set(authorizedBoxes.map((box) => box.id)), [authorizedBoxes]);
  const boxById = useMemo(() => new Map(authorizedBoxes.map((box) => [box.id, box])), [authorizedBoxes]);
  const eligibleLabelBoxes = useMemo(
    () => authorizedBoxes.filter((box) => isPrintableLabelBox(box, labelCutoffDate)),
    [authorizedBoxes, labelCutoffDate],
  );
  const zoneOptions = useMemo(
    () => getLabelZoneOptions(eligibleLabelBoxes, labels.noZone),
    [eligibleLabelBoxes, labels.noZone],
  );
  const selectedLabels = useMemo(
    () => qrLabelSelection
      .filter((label) => authorizedBoxIds.has(label.id))
      .map((label) => {
        const box = boxById.get(label.id);
        return box ? buildQrLabelItem(box, label.qrImageUrl) : label;
      })
      .sort((first, second) => compareLabelItems(first, second, boxById)),
    [authorizedBoxIds, boxById, qrLabelSelection],
  );
  const selectedLabelIds = useMemo(() => new Set(selectedLabels.map((label) => label.id)), [selectedLabels]);
  const labelBoxes = useMemo(
    () => filterLabelBoxes(eligibleLabelBoxes, zoneFilter, normalizedLabelSearch),
    [eligibleLabelBoxes, normalizedLabelSearch, zoneFilter],
  );
  const labelGroups = useMemo(() => groupLabelBoxes(labelBoxes), [labelBoxes]);

  const labelBoxesToAdd = useMemo(
    () => labelBoxes.filter((box) => !selectedLabelIds.has(box.id)),
    [labelBoxes, selectedLabelIds],
  );


  if (isLoading) return <PageLoader variant="labels" label={labels.pageTitle} />;
  if (!profile || !canManageQrLabels) return null;

  function toggleQrLabel(box: BoxItem) {
    if (selectedLabelIds.has(box.id)) onRemoveQrLabel(box.id);
    else onAddQrLabel(buildQrLabelItem(box));
  }

  return (
    <section className={`profile-page labels-page${selectedLabels.length ? ' has-selection' : ''}`}>
      <section className="profile-block profile-label-section">
        <section className="label-step-card label-selection-card">

          <div className="profile-label-toolbar">
            <label className="admin-label-search profile-label-search">
              <span>{labels.qrLabelSearchTitle}</span>
              <input
                type="search"
                value={labelSearch}
                placeholder={labels.qrLabelSearchPlaceholder}
                onChange={(event) => setLabelSearch(event.target.value)}
              />
            </label>
            <label className="label-filter-panel">
              <span>{labels.zoneLabel}</span>
              <select value={zoneFilter} onChange={(event) => setZoneFilter(event.target.value)}>
                <option value="all">{labels.allZones}</option>
                {zoneOptions.map((zone) => (
                  <option value={zone.key} key={zone.key}>{zone.name}</option>
                ))}
              </select>
            </label>

            <button
              className="secondary-button label-add-results"
              type="button"
              aria-label={labels.qrLabelAddResults(labelBoxes.length)}
              disabled={!labelBoxesToAdd.length}
              onClick={() => labelBoxesToAdd.forEach((box) => onAddQrLabel(buildQrLabelItem(box)))}
            >
              <span className="label-add-results-full">{labels.qrLabelAddResults(labelBoxes.length)}</span>
              <span className="label-add-results-compact" aria-hidden="true">{labels.qrLabelAddResultsCompact(labelBoxes.length)}</span>
            </button>
          </div>

          {selectedLabels.length > 0 ? (
            <div className="label-selection-dock">
              <div className="label-selection-bar" role="group" aria-label={labels.pageTitle}>
                <div className="label-selection-summary" role="status">
                  <strong>
                    <b>{selectedLabels.length}</b>{' '}
                    {selectedLabels.length === 1 ? labels.qrLabelSelectedSingular : labels.qrLabelSelectedPlural}
                  </strong>
                </div>
                <div className="label-selection-actions">
                  <button
                    className="label-selection-clear"
                    type="button"
                    aria-label={labels.qrLabelClearSelection}
                    onClick={onClearQrLabelSelection}
                  >
                    {labels.qrLabelClearSelection}
                  </button>
                  <button
                    className="primary-button label-selection-print"
                    type="button"
                    onClick={() => printQrLabels(selectedLabels, printSettings)}
                  >
                    <Printer size={17} aria-hidden="true" />
                    {labels.qrLabelPrintCount(selectedLabels.length)}
                  </button>
                </div>
              </div>
            </div>
          ) : null}

          <div className="profile-label-selector">
            {labelGroups.map((group) => {
              const isExpanded = Boolean(normalizedLabelSearch) || expandedSpecies.has(group.key);
              const { selectedCount, allSelected, unselectedBoxes } = getSpeciesSelectionState(group.boxes, selectedLabelIds);
              return (
                <section className="label-species-group" key={group.key}>
                  <h3>
                    <button
                      type="button"
                      aria-expanded={isExpanded}
                      aria-controls={`label-species-${group.key}`}
                      disabled={Boolean(normalizedLabelSearch)}
                      onClick={() => setExpandedSpecies((current) => {
                        const next = new Set(current);
                        if (next.has(group.key)) next.delete(group.key);
                        else next.add(group.key);
                        return next;
                      })}
                    >
                      <span className="label-species-chevron" aria-hidden="true">
                        {isExpanded ? <ChevronDown size={18} /> : <ChevronRight size={18} />}
                      </span>
                      <span className="label-species-heading-copy">
                        <strong>{group.name}</strong>
                      </span>
                      <span className="label-species-meta">
                        <span>{labels.qrLabelSpeciesCount(group.boxes.length)}</span>
                        {selectedCount ? <small>{labels.qrLabelSpeciesSelected(selectedCount)}</small> : null}
                      </span>
                    </button>
                    <label className="label-species-toggle">
                      <input
                        type="checkbox"
                        checked={allSelected}
                        aria-checked={selectedCount && !allSelected ? 'mixed' : allSelected}
                        aria-label={allSelected
                          ? labels.qrLabelDeselectSpecies(group.boxes.length, group.name)
                          : labels.qrLabelSelectSpecies(group.boxes.length, group.name)}
                        ref={(input) => { if (input) input.indeterminate = selectedCount > 0 && !allSelected; }}
                        onChange={() => {
                          if (allSelected) group.boxes.forEach((box) => onRemoveQrLabel(box.id));
                          else unselectedBoxes.forEach((box) => onAddQrLabel(buildQrLabelItem(box)));
                        }}
                      />
                    </label>
                  </h3>
                  <div className="label-species-rows" id={`label-species-${group.key}`} hidden={!isExpanded}>
                    {group.boxes.map((box) => (
                      <div
                        className={`label-box-row${selectedLabelIds.has(box.id) ? ' is-selected' : ''}`}
                        key={box.id}
                        onClick={(event) => {
                          if ((event.target as HTMLElement).closest('a, input, button')) return;
                          toggleQrLabel(box);
                        }}
                      >
                        <label className="label-box-toggle">
                          <input
                            type="checkbox"
                            checked={selectedLabelIds.has(box.id)}
                            aria-label={`${labels.selectBox} ${box.global_code}`}
                            onChange={() => toggleQrLabel(box)}
                          />
                        </label>
                        <span className="box-inventory-cell box-inventory-identity label-box-copy">
                          <BoxTrackingPreview
                            boxId={box.id}
                            code={box.global_code}
                            speciesName={box.species.scientific_name}
                            language={language}
                            onOpenBox={onOpenBox}
                            t={t}
                          />
                          <small>{box.thermal_zone?.name ?? labels.noZone}</small>
                        </span>
                      </div>
                    ))}
                  </div>
                </section>
              );
            })}
            {!labelBoxes.length ? (
              <p className="label-empty-state">
                {eligibleLabelBoxes.length ? labels.qrLabelNoMatches : labels.qrLabelNoEligibleBoxes}
              </p>
            ) : null}
          </div>
        </section>
      </section>

    </section>
  );
}

function userCanManageQrLabels(profile: UserProfile) {
  if (profile.is_superuser) return true;
  return profile.memberships.some(
    (membership) => membership.role === 'admin' || membership.role === 'lab_technician',
  );
}

function getQrLabelOrganizationIds(profile: UserProfile) {
  if (profile.is_superuser) return null;
  return new Set(
    profile.memberships
      .filter((membership) => membership.role === 'admin' || membership.role === 'lab_technician')
      .map((membership) => membership.organization.id),
  );
}

function isPrintableLabelBox(box: BoxItem, cutoffDate: Date) {
  if (box.status !== 'active') return false;
  if (!box.latest_measurement?.measured_on) return false;
  const measuredOn = new Date(`${box.latest_measurement.measured_on}T00:00:00`);
  return Number.isFinite(measuredOn.getTime()) && measuredOn >= cutoffDate;
}

function compareLabelItems(first: QrLabelItem, second: QrLabelItem, boxById: Map<number, BoxItem>) {
  const firstBox = boxById.get(first.id);
  const secondBox = boxById.get(second.id);
  if (firstBox && secondBox) return compareLabelBoxes(firstBox, secondBox);
  return compareLabelValue(first.speciesName, second.speciesName)
    || compareLabelValue(first.globalCode, second.globalCode)
    || first.id - second.id;
}

function compareLabelBoxes(first: BoxItem, second: BoxItem) {
  return compareLabelValue(first.species.scientific_name, second.species.scientific_name)
    || first.species.id - second.species.id
    || compareLabelValue(first.global_code, second.global_code)
    || first.id - second.id;
}

function compareLabelValue(first: string, second: string) {
  return first.localeCompare(second, 'fr', { numeric: true, sensitivity: 'base' });
}

function filterLabelBoxes(boxes: BoxItem[], zoneFilter: string, normalizedLabelSearch: string) {
  return boxes.filter((box) => {
    if (zoneFilter !== 'all' && getLabelZoneKey(box) !== zoneFilter) return false;
    if (!normalizedLabelSearch) return true;
    return [box.global_code, box.local_code, box.species.scientific_name, box.strain.code]
      .filter(Boolean)
      .some((value) => value!.toLocaleLowerCase().includes(normalizedLabelSearch));
  });
}

function getSpeciesSelectionState(boxes: BoxItem[], selectedIds: Set<number>) {
  const unselectedBoxes = boxes.filter((box) => !selectedIds.has(box.id));
  return {
    selectedCount: boxes.length - unselectedBoxes.length,
    allSelected: unselectedBoxes.length === 0,
    unselectedBoxes,
  };
}

function groupLabelBoxes(boxes: BoxItem[]): SpeciesGroup[] {
  const groups = new Map<number, SpeciesGroup>();
  boxes.forEach((box) => {
    const key = box.species.id;
    const group = groups.get(key) ?? { key, name: box.species.scientific_name, boxes: [] };
    group.boxes.push(box);
    groups.set(key, group);
  });
  return Array.from(groups.values())
    .map((group) => ({ ...group, boxes: group.boxes.sort((first, second) =>
      compareLabelValue(first.global_code, second.global_code) || first.id - second.id) }))
    .sort((first, second) => compareLabelValue(first.name, second.name) || first.key - second.key);
}

function getLabelZoneOptions(boxes: BoxItem[], noZoneLabel: string) {
  const zones = new Map<string, { name: string; count: number }>();
  boxes.forEach((box) => {
    const key = getLabelZoneKey(box);
    const zone = zones.get(key);
    zones.set(key, {
      name: box.thermal_zone?.name ?? noZoneLabel,
      count: (zone?.count ?? 0) + 1,
    });
  });
  return Array.from(zones, ([key, zone]) => ({ key, ...zone }))
    .sort((first, second) => compareLabelValue(first.name, second.name));
}

function getLabelZoneKey(box: BoxItem) {
  return box.thermal_zone ? `zone-${box.thermal_zone.id}` : 'zone-none';
}

function getRecentLabelCutoffDate() {
  const date = new Date();
  date.setMonth(date.getMonth() - 15);
  date.setHours(0, 0, 0, 0);
  return date;
}
