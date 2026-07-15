import { useMemo, useState } from 'react';

import type { BoxItem, UserProfile } from '../types';
import { buildQrLabelItem, type QrLabelItem } from '../utils/qrLabels';
import PageLoader from './PageLoader';

type LabelsViewLabels = {
  noZone: string;
  qrLabelAddToSelection: string;
  qrLabelClearSelection: string;
  qrLabelPrintSelection: string;
  qrLabelSelectionCount: string;
  qrLabelSelectionEmpty: string;
  qrLabelSelectionHelp: string;
  qrLabelSelectionSearch: string;
  qrLabelSelectionTitle: string;
  qrLabelSearchPlaceholder: string;
};

export default function LabelsView({
  boxes,
  isLoading,
  labels,
  onAddQrLabel,
  onClearQrLabelSelection,
  onPrintQrLabelSelection,
  onRemoveQrLabel,
  profile,
  qrLabelSelection,
}: {
  boxes: BoxItem[];
  isLoading: boolean;
  labels: LabelsViewLabels;
  onAddQrLabel: (label: QrLabelItem) => void;
  onClearQrLabelSelection: () => void;
  onPrintQrLabelSelection: () => void;
  onRemoveQrLabel: (labelId: number) => void;
  profile: UserProfile | null;
  qrLabelSelection: QrLabelItem[];
}) {
  const [labelSearch, setLabelSearch] = useState('');
  const canManageQrLabels = profile ? userCanManageQrLabels(profile) : false;
  const labelOrganizationIds = useMemo(
    () => (profile ? getQrLabelOrganizationIds(profile) : new Set<number>()),
    [profile],
  );
  const normalizedLabelSearch = labelSearch.trim().toLocaleLowerCase();
  const labelBoxes = useMemo(() => {
    if (!profile || !canManageQrLabels) return [];

    return boxes.filter((box) => {
      if (labelOrganizationIds && !labelOrganizationIds.has(box.organization.id)) return false;
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
  }, [boxes, canManageQrLabels, labelOrganizationIds, normalizedLabelSearch, profile]);

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
        <div className="section-title">
          <div>
            <h2>{labels.qrLabelSelectionTitle}</h2>
            <p>{labels.qrLabelSelectionHelp}</p>
          </div>
          <span>{qrLabelSelection.length}</span>
        </div>

        <div className="profile-label-toolbar">
          <label className="admin-label-search profile-label-search">
            <span>{labels.qrLabelSelectionSearch}</span>
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

        <div className="admin-label-selector profile-label-selector">
          {labelBoxes.map((box) => {
            const isSelected = qrLabelSelection.some((label) => label.id === box.id);
            return (
              <label key={box.id}>
                <input
                  type="checkbox"
                  checked={isSelected}
                  onChange={() => toggleQrLabel(box)}
                />
                <span>
                  <strong>{box.global_code}</strong>
                  <small>{box.species.scientific_name}</small>
                </span>
                <em>{box.thermal_zone?.name ?? labels.noZone}</em>
              </label>
            );
          })}
        </div>

        <div className="profile-selected-labels">
          <div>
            <strong>{qrLabelSelection.length}</strong>
            <span>{labels.qrLabelSelectionCount}</span>
          </div>
          {qrLabelSelection.length ? (
            <ul>
              {qrLabelSelection.map((label) => (
                <li key={label.id}>
                  <span>
                    <strong>{label.globalCode}</strong>
                    <small>{label.speciesName}</small>
                  </span>
                  <button type="button" onClick={() => onRemoveQrLabel(label.id)}>x</button>
                </li>
              ))}
            </ul>
          ) : (
            <p>{labels.qrLabelSelectionEmpty}</p>
          )}
        </div>

        <button
          className="admin-print-labels-button profile-print-labels-button"
          type="button"
          disabled={!qrLabelSelection.length}
          onClick={onPrintQrLabelSelection}
        >
          {labels.qrLabelPrintSelection}
        </button>
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
