import { type CSSProperties, useEffect, useMemo, useRef, useState } from 'react';

import type { BoxItem, UserProfile } from '../types';
import {
  DEFAULT_QR_LABEL_PRINT_SETTINGS,
  buildQrLabelItem,
  getQrLabelSheetRows,
  printQrLabels,
  type QrLabelItem,
} from '../utils/qrLabels';
import PageLoader from './PageLoader';
import QrLabel from './QrLabel';

const LABEL_TABLET_MEDIA_QUERY = '(min-width: 760px) and (max-width: 1023px), (min-width: 760px) and (max-width: 1180px) and (pointer: coarse)';

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
  qrLabelSelectionEmpty: string;
  qrLabelSelectionFilter: string;
  qrLabelSelectionHelp: string;
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
  const [usesTabletZoneFilters, setUsesTabletZoneFilters] = useState(matchesLabelTabletLayout);
  const [activeWorkspacePanel, setActiveWorkspacePanel] = useState<'selection' | 'preview'>('selection');
  const [previewPageIndex, setPreviewPageIndex] = useState(0);
  const workspaceRef = useRef<HTMLElement | null>(null);
  const selectorRef = useRef<HTMLDivElement | null>(null);
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
  const activeZoneFilter = usesTabletZoneFilters
    ? zoneOptions.find((zone) => zone.key === zoneFilter)?.key ?? zoneOptions[0]?.key ?? 'all'
    : zoneFilter;
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
      if (activeZoneFilter !== 'all' && getLabelZoneKey(box) !== activeZoneFilter) return false;
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
  }, [activeZoneFilter, eligibleLabelBoxes, normalizedLabelSearch]);
  const labelGroups = useMemo(
    () => groupLabelBoxes(labelBoxes, labels.noZone),
    [labelBoxes, labels.noZone],
  );
  const labelBoxesToAdd = useMemo(
    () => labelBoxes.filter((box) => !selectedLabelIds.has(box.id)),
    [labelBoxes, selectedLabelIds],
  );
  const sheetRows = getQrLabelSheetRows(printSettings);
  const labelsPerPage = printSettings.columns * sheetRows;
  const previewPages = useMemo(
    () => chunkLabels(selectedLabels, labelsPerPage),
    [labelsPerPage, selectedLabels],
  );
  const previewPageCount = Math.max(previewPages.length, 1);
  const previewPageLabels = previewPages[previewPageIndex] ?? [];
  const sheetPreviewStyle = {
    '--label-sheet-columns': String(printSettings.columns),
    '--label-sheet-rows': String(sheetRows),
    '--label-preview-ratio': `${printSettings.labelWidthMm} / ${printSettings.labelHeightMm}`,
  } as CSSProperties;

  useEffect(() => {
    setPreviewPageIndex((currentIndex) => Math.min(currentIndex, previewPageCount - 1));
  }, [previewPageCount]);

  useEffect(() => {
    const mediaQuery = window.matchMedia(LABEL_TABLET_MEDIA_QUERY);
    const updateTabletLayout = () => setUsesTabletZoneFilters(mediaQuery.matches);

    updateTabletLayout();
    mediaQuery.addEventListener('change', updateTabletLayout);
    return () => mediaQuery.removeEventListener('change', updateTabletLayout);
  }, []);

  if (isLoading) {
    return <PageLoader variant="labels" label={labels.qrLabelSelectionTitle} />;
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

  function showWorkspacePanel(panel: 'selection' | 'preview') {
    setActiveWorkspacePanel(panel);
    requestAnimationFrame(() => {
      workspaceRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    });
  }

  function selectZoneFilter(zoneKey: string) {
    setZoneFilter(zoneKey);
    requestAnimationFrame(() => {
      selectorRef.current?.scrollIntoView({ block: 'start' });
    });
  }

  return (
    <section className="profile-page labels-page">
      <section className="profile-block profile-label-section" ref={workspaceRef}>
        <nav className="label-workspace-tabs" aria-label={labels.qrLabelSettingsTitle}>
          <button
            className={activeWorkspacePanel === 'selection' ? 'is-active' : undefined}
            type="button"
            aria-pressed={activeWorkspacePanel === 'selection'}
            onClick={() => showWorkspacePanel('selection')}
          >
            {labels.qrLabelSelectionTitle}
          </button>
          <button
            className={activeWorkspacePanel === 'preview' ? 'is-active' : undefined}
            type="button"
            aria-pressed={activeWorkspacePanel === 'preview'}
            onClick={() => showWorkspacePanel('preview')}
          >
            {labels.qrLabelPreview}
          </button>
        </nav>

        <div className="label-page-stack">
          <section className={`label-step-card label-selection-card${activeWorkspacePanel === 'selection' ? '' : ' is-tablet-hidden'}`}>
            <div className="label-tablet-zone-filters" role="group" aria-label={labels.zoneLabel}>
              {zoneOptions.map((zone) => {
                const isActive = activeZoneFilter === zone.key;
                return (
                  <button
                    type="button"
                    className={`overview-zone-progress-card label-tablet-zone-filter${isActive ? ' is-active' : ''}`}
                    aria-pressed={isActive}
                    key={zone.key}
                    onClick={() => selectZoneFilter(zone.key)}
                    onPointerUp={(event) => event.currentTarget.blur()}
                  >
                    <span className="overview-zone-progress-copy">
                      <strong>{zone.name}</strong>
                    </span>
                    <em className="overview-zone-progress-count">
                      <strong>{zone.count}</strong>
                    </em>
                  </button>
                );
              })}
            </div>

            <header className="label-selection-heading">
              <div className="label-selection-copy">
                <div>
                  <h2>{labels.qrLabelSelectionTitle}</h2>
                  <span>{selectedLabels.length} / {eligibleLabelBoxes.length}</span>
                </div>
                <p>{labels.qrLabelSelectionHelp}</p>
              </div>
              <div className="admin-label-actions">
                <button
                  type="button"
                  disabled={!labelBoxesToAdd.length}
                  onClick={() => labelBoxesToAdd.forEach((box) => onAddQrLabel(buildQrLabelItem(box)))}
                >
                  {labels.qrLabelAddToSelection}
                </button>
                <button type="button" disabled={!selectedLabels.length} onClick={onClearQrLabelSelection}>
                  {labels.qrLabelClearSelection}
                </button>
              </div>
            </header>

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
                    <option value={zone.key} key={zone.key}>
                      {zone.name}
                    </option>
                  ))}
                </select>
              </label>
            </div>

            <div className="admin-label-selector profile-label-selector" ref={selectorRef}>
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
                        <label
                          className={isSelected ? 'is-selected' : undefined}
                          key={box.id}
                          title={`${box.global_code} — ${box.species.scientific_name}`}
                        >
                          <input
                            type="checkbox"
                            checked={isSelected}
                            onChange={() => toggleQrLabel(box)}
                          />
                          <span className="label-box-copy">
                            <strong title={box.global_code}>{box.global_code}</strong>
                            <small title={box.species.scientific_name}>{box.species.scientific_name}</small>
                          </span>
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

          <section className={`label-step-card label-layout-card${activeWorkspacePanel === 'preview' ? '' : ' is-tablet-hidden'}`}>
            <section className="label-preview-card">
              <div className="label-panel-heading">
                <h3>{labels.qrLabelPreview}</h3>
                <div className="label-preview-pagination">
                  <button
                    type="button"
                    disabled={previewPageIndex === 0}
                    aria-label={`${labels.qrLabelPage} ${Math.max(previewPageIndex, 1)}`}
                    onClick={() => setPreviewPageIndex((currentIndex) => Math.max(currentIndex - 1, 0))}
                  >
                    <span aria-hidden="true">&#8249;</span>
                  </button>
                  <span>{labels.qrLabelPage} {previewPageIndex + 1} / {previewPageCount}</span>
                  <button
                    type="button"
                    disabled={previewPageIndex >= previewPageCount - 1}
                    aria-label={`${labels.qrLabelPage} ${Math.min(previewPageIndex + 2, previewPageCount)}`}
                    onClick={() => setPreviewPageIndex((currentIndex) => Math.min(currentIndex + 1, previewPageCount - 1))}
                  >
                    <span aria-hidden="true">&#8250;</span>
                  </button>
                </div>
              </div>

              <div className="label-preview-summary">
                <div>
                  <span>{labels.qrLabelSelectionFilter}</span>
                  <strong>{selectedLabels.length}</strong>
                </div>
                <div>
                  <span>{labels.qrLabelPerPage}</span>
                  <strong>{labelsPerPage}</strong>
                </div>
              </div>

              <div className="label-pages-preview">
                <article className="label-print-page-preview" key={`page-${previewPageIndex}`}>
                  <div className={`label-preview-stage${selectedLabels.length ? '' : ' is-empty'}`}>
                    {!selectedLabels.length ? (
                      <div className="label-preview-empty">
                        <strong>{labels.qrLabelSelectionEmpty}</strong>
                        <span>{labels.qrLabelSelectionHelp}</span>
                      </div>
                    ) : null}
                    <div className="label-sheet-preview" style={sheetPreviewStyle}>
                      {Array.from({ length: labelsPerPage }).map((_, index) => {
                        const label = previewPageLabels[index];
                        const globalIndex = previewPageIndex * labelsPerPage + index;
                        const previousLabel = selectedLabels[globalIndex - 1];
                        const startsZone = Boolean(label?.zoneName)
                          && (!previousLabel || previousLabel.zoneName !== label.zoneName);
                        return (
                          <div
                            className="label-preview-slot"
                            key={label?.id ?? `empty-${previewPageIndex}-${index}`}
                          >
                            {label && startsZone ? <span className="label-preview-zone-marker">{label.zoneName}</span> : null}
                            {label ? (
                              <QrLabel item={label} />
                            ) : (
                              <div className="label-preview-tile is-empty" />
                            )}
                          </div>
                        );
                      })}
                    </div>
                  </div>
                </article>
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

function matchesLabelTabletLayout() {
  return typeof window !== 'undefined' && window.matchMedia(LABEL_TABLET_MEDIA_QUERY).matches;
}

function getRecentLabelCutoffDate() {
  const date = new Date();
  date.setMonth(date.getMonth() - 15);
  date.setHours(0, 0, 0, 0);
  return date;
}

function chunkLabels(labels: QrLabelItem[], size: number) {
  if (!labels.length) return [];

  const chunks: QrLabelItem[][] = [];
  for (let index = 0; index < labels.length; index += size) {
    chunks.push(labels.slice(index, index + size));
  }
  return chunks;
}
