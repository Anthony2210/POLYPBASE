import { type CSSProperties, useMemo, useState } from 'react';

import type { BoxItem, UserProfile } from '../types';
import {
  DEFAULT_QR_LABEL_PRINT_SETTINGS,
  buildQrLabelItem,
  printQrLabels,
  type QrLabelItem,
  type QrLabelPrintSettings,
} from '../utils/qrLabels';
import PageLoader from './PageLoader';

type LabelsViewLabels = {
  allZones: string;
  noZone: string;
  qrLabelAddToSelection: string;
  qrLabelClearSelection: string;
  qrLabelNoEligibleBoxes: string;
  qrLabelPage: string;
  qrLabelPerPage: string;
  qrLabelPreview: string;
  qrLabelPrintSelection: string;
  qrLabelSearchTitle: string;
  qrLabelSelectionSearch: string;
  qrLabelSelectionTitle: string;
  qrLabelSearchPlaceholder: string;
  qrLabelSettingsTitle: string;
  zoneLabel: string;
};

export default function LabelsView({
  boxes,
  isLoading,
  labels,
  onAddQrLabel,
  onClearQrLabelSelection,
  onRemoveQrLabel,
  profile,
  qrLabelSelection,
}: {
  boxes: BoxItem[];
  isLoading: boolean;
  labels: LabelsViewLabels;
  onAddQrLabel: (label: QrLabelItem) => void;
  onClearQrLabelSelection: () => void;
  onRemoveQrLabel: (labelId: number) => void;
  profile: UserProfile | null;
  qrLabelSelection: QrLabelItem[];
}) {
  const [labelSearch, setLabelSearch] = useState('');
  const [zoneFilter, setZoneFilter] = useState('all');
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

    return boxes.filter((box) => {
      if (labelOrganizationIds && !labelOrganizationIds.has(box.organization.id)) return false;
      return true;
    });
  }, [boxes, canManageQrLabels, labelOrganizationIds, profile]);
  const authorizedBoxIds = useMemo(
    () => new Set(authorizedBoxes.map((box) => box.id)),
    [authorizedBoxes],
  );
  const boxById = useMemo(
    () => new Map(authorizedBoxes.map((box) => [box.id, box])),
    [authorizedBoxes],
  );
  const eligibleLabelBoxes = useMemo(
    () => authorizedBoxes
      .filter((box) => isPrintableLabelBox(box, labelCutoffDate))
      .sort((first, second) => compareLabelBoxes(first, second, labels.noZone)),
    [authorizedBoxes, labelCutoffDate, labels.noZone],
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
      .sort((first, second) => compareLabelItems(first, second, boxById, labels.noZone)),
    [authorizedBoxIds, boxById, labels.noZone, qrLabelSelection],
  );
  const selectedLabelIds = useMemo(
    () => new Set(selectedLabels.map((label) => label.id)),
    [selectedLabels],
  );
  const labelBoxes = useMemo(() => {
    return eligibleLabelBoxes.filter((box) => {
      if (zoneFilter !== 'all' && getLabelZoneKey(box) !== zoneFilter) return false;
      if (!normalizedLabelSearch) return true;

      return [
        box.global_code,
        box.local_code,
        box.species.scientific_name,
        box.strain.code,
      ]
        .filter(Boolean)
        .some((value) => value!.toLocaleLowerCase().includes(normalizedLabelSearch));
    });
  }, [eligibleLabelBoxes, normalizedLabelSearch, zoneFilter]);
  const labelGroups = useMemo(
    () => groupLabelBoxes(labelBoxes, labels.noZone),
    [labelBoxes, labels.noZone],
  );
  const sheetRows = getSheetRows(printSettings);
  const labelsPerPage = printSettings.columns * sheetRows;
  const previewPages = useMemo(
    () => chunkLabels(selectedLabels, labelsPerPage),
    [labelsPerPage, selectedLabels],
  );
  const sheetPreviewStyle = {
    '--label-sheet-columns': String(printSettings.columns),
    '--label-sheet-rows': String(sheetRows),
  } as CSSProperties;

  if (isLoading) {
    return <PageLoader variant="profile" label={labels.qrLabelSelectionTitle} />;
  }

  if (!profile || !canManageQrLabels) return null;

  function toggleQrLabel(box: BoxItem) {
    const label = buildQrLabelItem(box);
    if (qrLabelSelection.some((item) => item.id === label.id)) {
      onRemoveQrLabel(label.id);
      return;
    }
    onAddQrLabel(label);
  }

  return (
    <section className="profile-page labels-page">
      <section className="profile-block profile-label-section">
        <div className="label-page-stack">
          <section className="label-step-card">
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
              <div className="admin-label-actions">
                <button
                  type="button"
                  disabled={!labelBoxes.length}
                  onClick={() => labelBoxes.forEach((box) => onAddQrLabel(buildQrLabelItem(box)))}
                >
                  {labels.qrLabelAddToSelection}
                </button>
                <button type="button" disabled={!qrLabelSelection.length} onClick={onClearQrLabelSelection}>
                  {labels.qrLabelClearSelection}
                </button>
              </div>
            </div>

            <div className="label-filter-panel">
              <label>
                <span>{labels.zoneLabel}</span>
                <select value={zoneFilter} onChange={(event) => setZoneFilter(event.target.value)}>
                  <option value="all">{labels.allZones}</option>
                  {zoneOptions.map((zone) => (
                    <option value={zone.key} key={zone.key}>
                      {zone.name}
                    </option>
                  ))}
                </select>
              </label>
            </div>

            <div className="admin-label-selector profile-label-selector">
              {labelGroups.map((group) => (
                <section className="label-zone-group" key={group.key}>
                  <header>
                    <strong>{group.zoneName}</strong>
                    <span>{group.boxes.length}</span>
                  </header>

                  <div className="label-zone-group-list">
                    {group.boxes.map((box) => {
                      const isSelected = selectedLabelIds.has(box.id);
                      return (
                        <label className={isSelected ? 'is-selected' : undefined} key={box.id}>
                          <input
                            type="checkbox"
                            checked={isSelected}
                            onChange={() => toggleQrLabel(box)}
                          />
                          <span>
                            <strong>{box.global_code}</strong>
                            <small>{box.species.scientific_name}</small>
                          </span>
                          <em>{box.strain.code}</em>
                        </label>
                      );
                    })}
                  </div>
                </section>
              ))}
              {!labelBoxes.length ? (
                <p className="label-empty-state">{labels.qrLabelNoEligibleBoxes}</p>
              ) : null}
            </div>

          </section>

          <section className="label-step-card label-layout-card">
            <section className="label-preview-card">
              <div className="label-panel-heading">
                <h3>{labels.qrLabelPreview}</h3>
              </div>

              <div className="label-pages-preview">
                {(previewPages.length ? previewPages : [[]]).map((pageLabels, pageIndex) => (
                  <article className="label-print-page-preview" key={`page-${pageIndex}`}>
                    <div className="label-print-page-heading">
                      <strong>
                        {labels.qrLabelPage} {pageIndex + 1}
                      </strong>
                      <span>
                        {pageLabels.length} / {labelsPerPage}
                      </span>
                    </div>
                    <div className="label-preview-stage">
                      <div className="label-sheet-preview" style={sheetPreviewStyle}>
                        {Array.from({ length: labelsPerPage }).map((_, index) => {
                          const label = pageLabels[index];
                          const globalIndex = pageIndex * labelsPerPage + index;
                          const previousLabel = selectedLabels[globalIndex - 1];
                          const startsZone = Boolean(label?.zoneName)
                            && (!previousLabel || previousLabel.zoneName !== label.zoneName);
                          return (
                            <div
                              className="label-preview-slot"
                              key={label?.id ?? `empty-${pageIndex}-${index}`}
                            >
                              {label && startsZone ? <span className="label-preview-zone-marker">{label.zoneName}</span> : null}
                              {label ? (
                                <div className="label-preview-tile">
                                  <img src={label.qrImageUrl} alt="" />
                                  <strong>{label.globalCode}</strong>
                                  <small>{label.speciesName}</small>
                                </div>
                              ) : (
                                <div className="label-preview-tile is-empty" />
                              )}
                            </div>
                          );
                        })}
                      </div>
                    </div>
                  </article>
                ))}
              </div>
            </section>

            <div className="label-print-toolbar">
              <button
                className="admin-print-labels-button profile-print-labels-button"
                type="button"
                disabled={!selectedLabels.length}
                onClick={() => printQrLabels(selectedLabels, printSettings)}
              >
                {labels.qrLabelPrintSelection}
              </button>
            </div>
          </section>
        </div>
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

function compareLabelItems(
  first: QrLabelItem,
  second: QrLabelItem,
  boxById: Map<number, BoxItem>,
  noZoneLabel: string,
) {
  const firstBox = boxById.get(first.id);
  const secondBox = boxById.get(second.id);
  if (firstBox && secondBox) return compareLabelBoxes(firstBox, secondBox, noZoneLabel);

  return first.globalCode.localeCompare(second.globalCode, 'fr', { numeric: true, sensitivity: 'base' });
}

function compareLabelBoxes(first: BoxItem, second: BoxItem, noZoneLabel: string) {
  return compareLabelValue(first.thermal_zone?.name ?? noZoneLabel, second.thermal_zone?.name ?? noZoneLabel)
    || compareLabelValue(first.species.scientific_name, second.species.scientific_name)
    || compareLabelValue(first.strain.code, second.strain.code)
    || compareLabelValue(first.global_code, second.global_code);
}

function compareLabelValue(first: string, second: string) {
  return first.localeCompare(second, 'fr', { numeric: true, sensitivity: 'base' });
}

function groupLabelBoxes(boxes: BoxItem[], noZoneLabel: string) {
  const groups = new Map<string, { key: string; zoneName: string; boxes: BoxItem[] }>();

  boxes.forEach((box) => {
    const zoneName = box.thermal_zone?.name ?? noZoneLabel;
    const key = box.thermal_zone ? `zone-${box.thermal_zone.id}` : 'zone-none';
    const group = groups.get(key) ?? { key, zoneName, boxes: [] };
    group.boxes.push(box);
    groups.set(key, group);
  });

  return Array.from(groups.values());
}

function getLabelZoneOptions(boxes: BoxItem[], noZoneLabel: string) {
  const zones = new Map<string, string>();

  boxes.forEach((box) => {
    zones.set(getLabelZoneKey(box), box.thermal_zone?.name ?? noZoneLabel);
  });

  return Array.from(zones, ([key, name]) => ({ key, name }))
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

function getSheetRows(settings: QrLabelPrintSettings) {
  const printableHeightMm = 277;
  return Math.max(
    1,
    Math.floor((printableHeightMm + settings.gapMm) / (settings.labelHeightMm + settings.gapMm)),
  );
}

function chunkLabels(labels: QrLabelItem[], size: number) {
  if (!labels.length) return [];

  const chunks: QrLabelItem[][] = [];
  for (let index = 0; index < labels.length; index += size) {
    chunks.push(labels.slice(index, index + size));
  }
  return chunks;
}
