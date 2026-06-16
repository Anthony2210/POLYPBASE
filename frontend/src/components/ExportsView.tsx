import { useMemo, useState } from 'react';

import { apiDownload } from '../api/client';
import type { ExportOptions } from '../types';

type Language = 'fr' | 'en';
type FilterKey = 'organizations' | 'species' | 'strains' | 'boxes' | 'zones';

type ExportFilters = Record<FilterKey, number[]> & {
  dateFrom: string;
  dateTo: string;
};

type FilterOption = {
  id: number;
  label: string;
  detail?: string;
};

const emptyFilters: ExportFilters = {
  organizations: [],
  species: [],
  strains: [],
  boxes: [],
  zones: [],
  dateFrom: '',
  dateTo: '',
};

const copy = {
  fr: {
    all: 'Toutes',
    allHistory: 'Tout l’historique',
    boxes: 'Boîtes',
    boxesFound: 'boîtes',
    choosePeriod: '1. Choisir une période',
    clear: 'Effacer',
    dateFrom: 'Date de début',
    dateTo: 'Date de fin',
    download: 'Télécharger le CSV',
    downloading: 'Préparation...',
    empty: 'Aucune valeur disponible',
    error: 'Impossible de générer le fichier.',
    filterHelp: 'Ouvrez seulement les filtres dont vous avez besoin.',
    filters: '2. Affiner les données',
    format: 'CSV hebdomadaire',
    formatHelp: 'Polypes, éphyrules et température pour chaque boîte.',
    invalidPeriod: 'La date de fin doit être postérieure à la date de début.',
    noBoxes: 'Aucune boîte ne correspond à ces filtres.',
    organizations: 'Structures',
    organizationsFound: 'structures',
    optionCount: 'valeurs',
    reset: 'Tout réinitialiser',
    selected: 'sélection',
    species: 'Espèces',
    speciesFound: 'espèces',
    strains: 'Souches',
    success: 'Fichier téléchargé',
    verify: '3. Vérifier puis exporter',
    zones: 'Zones thermiques',
  },
  en: {
    all: 'All',
    allHistory: 'Full history',
    boxes: 'Boxes',
    boxesFound: 'boxes',
    choosePeriod: '1. Choose a period',
    clear: 'Clear',
    dateFrom: 'Start date',
    dateTo: 'End date',
    download: 'Download CSV',
    downloading: 'Preparing...',
    empty: 'No value available',
    error: 'The file could not be generated.',
    filterHelp: 'Open only the filters you need.',
    filters: '2. Refine the data',
    format: 'Weekly CSV',
    formatHelp: 'Polyps, ephyrae and temperature for each box.',
    invalidPeriod: 'The end date must be after the start date.',
    noBoxes: 'No box matches these filters.',
    organizations: 'Organizations',
    organizationsFound: 'organizations',
    optionCount: 'values',
    reset: 'Reset all',
    selected: 'selected',
    species: 'Species',
    speciesFound: 'species',
    strains: 'Strains',
    success: 'File downloaded',
    verify: '3. Review and export',
    zones: 'Thermal zones',
  },
};

export default function ExportsView({
  options,
  language,
  isLoading,
}: {
  options: ExportOptions | null;
  language: Language;
  isLoading: boolean;
}) {
  const [filters, setFilters] = useState<ExportFilters>(emptyFilters);
  const [isDownloading, setIsDownloading] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const labels = copy[language];

  const exportState = useMemo(() => {
    if (!options) return null;

    const matchingBoxes = filterBoxes(options, filters);
    const matchingOrganizationIds = new Set(matchingBoxes.map((box) => box.organization_id));
    const matchingSpeciesIds = new Set(matchingBoxes.map((box) => box.species_id));

    return {
      matchingBoxes,
      organizationCount: matchingOrganizationIds.size,
      speciesCount: matchingSpeciesIds.size,
      groups: {
        organizations: buildOrganizationOptions(options),
        species: buildSpeciesOptions(
          options,
          withSelected(availableIds(options, filters, 'species', 'species_id'), filters.species),
        ),
        strains: buildStrainOptions(
          options,
          withSelected(availableIds(options, filters, 'strains', 'strain_id'), filters.strains),
        ),
        zones: buildZoneOptions(
          options,
          withSelected(
            availableIds(options, filters, 'zones', 'thermal_zone_id'),
            filters.zones,
          ),
        ),
        boxes: buildBoxOptions(
          options,
          withSelected(availableIds(options, filters, 'boxes', 'id'), filters.boxes),
        ),
      },
    };
  }, [filters, options]);

  const invalidPeriod = Boolean(
    filters.dateFrom && filters.dateTo && filters.dateFrom > filters.dateTo,
  );
  const hasFilters = (Object.keys(emptyFilters) as Array<keyof ExportFilters>).some((key) => {
    const value = filters[key];
    return Array.isArray(value) ? value.length > 0 : Boolean(value);
  });

  function updateDate(key: 'dateFrom' | 'dateTo', value: string) {
    setFilters((current) => ({ ...current, [key]: value }));
    clearFeedback();
  }

  function toggleFilter(key: FilterKey, id: number) {
    setFilters((current) => ({
      ...current,
      [key]: current[key].includes(id)
        ? current[key].filter((value) => value !== id)
        : [...current[key], id],
    }));
    clearFeedback();
  }

  function clearFilter(key: FilterKey) {
    setFilters((current) => ({ ...current, [key]: [] }));
    clearFeedback();
  }

  function clearFeedback() {
    setMessage(null);
    setError(null);
  }

  async function handleDownload() {
    if (!exportState?.matchingBoxes.length || invalidPeriod) return;

    setIsDownloading(true);
    clearFeedback();

    try {
      const params = new URLSearchParams();
      for (const key of ['organizations', 'species', 'strains', 'boxes', 'zones'] as FilterKey[]) {
        if (filters[key].length) params.set(key, filters[key].join(','));
      }
      if (filters.dateFrom) params.set('date_from', filters.dateFrom);
      if (filters.dateTo) params.set('date_to', filters.dateTo);

      const query = params.toString();
      const fileName = await apiDownload(
        `/api/exports/measurements.csv${query ? `?${query}` : ''}`,
      );
      setMessage(`${labels.success} : ${fileName}`);
    } catch (downloadError) {
      setError(downloadError instanceof Error ? downloadError.message : labels.error);
    } finally {
      setIsDownloading(false);
    }
  }

  if (isLoading || !exportState) {
    return (
      <section className="export-page" aria-busy="true">
        <div className="skeleton-stack">
          <span className="skeleton-row" />
          <span className="skeleton-row" />
          <span className="skeleton-row" />
        </div>
      </section>
    );
  }

  return (
    <section className="export-page">
      <section className="export-step">
        <header className="export-step-heading">
          <h2>{labels.choosePeriod}</h2>
          <span>
            {filters.dateFrom || filters.dateTo
              ? formatPeriod(filters.dateFrom, filters.dateTo, language)
              : labels.allHistory}
          </span>
        </header>

        <div className="export-period">
          <label>
            {labels.dateFrom}
            <input
              type="date"
              max={filters.dateTo || undefined}
              value={filters.dateFrom}
              onChange={(event) => updateDate('dateFrom', event.target.value)}
            />
          </label>
          <label>
            {labels.dateTo}
            <input
              type="date"
              min={filters.dateFrom || undefined}
              value={filters.dateTo}
              onChange={(event) => updateDate('dateTo', event.target.value)}
            />
          </label>
        </div>
        {invalidPeriod ? <p className="inline-error">{labels.invalidPeriod}</p> : null}
      </section>

      <section className="export-step">
        <header className="export-step-heading">
          <div>
            <h2>{labels.filters}</h2>
            <p>{labels.filterHelp}</p>
          </div>
          {hasFilters ? (
            <button
              className="export-reset"
              type="button"
              onClick={() => {
                setFilters(emptyFilters);
                clearFeedback();
              }}
            >
              {labels.reset}
            </button>
          ) : null}
        </header>

        <div className="export-filter-list">
          <FilterDisclosure
            title={labels.organizations}
            options={exportState.groups.organizations}
            selectedIds={filters.organizations}
            labels={labels}
            onToggle={(id) => toggleFilter('organizations', id)}
            onClear={() => clearFilter('organizations')}
          />
          <FilterDisclosure
            title={labels.species}
            options={exportState.groups.species}
            selectedIds={filters.species}
            labels={labels}
            onToggle={(id) => toggleFilter('species', id)}
            onClear={() => clearFilter('species')}
          />
          <FilterDisclosure
            title={labels.strains}
            options={exportState.groups.strains}
            selectedIds={filters.strains}
            labels={labels}
            onToggle={(id) => toggleFilter('strains', id)}
            onClear={() => clearFilter('strains')}
          />
          <FilterDisclosure
            title={labels.zones}
            options={exportState.groups.zones}
            selectedIds={filters.zones}
            labels={labels}
            onToggle={(id) => toggleFilter('zones', id)}
            onClear={() => clearFilter('zones')}
          />
          <FilterDisclosure
            title={labels.boxes}
            options={exportState.groups.boxes}
            selectedIds={filters.boxes}
            labels={labels}
            onToggle={(id) => toggleFilter('boxes', id)}
            onClear={() => clearFilter('boxes')}
          />
        </div>
      </section>

      <section className="export-review">
        <div>
          <span className="export-review-label">{labels.verify}</span>
          {exportState.matchingBoxes.length ? (
            <p className="export-review-count">
              <strong>{exportState.matchingBoxes.length}</strong> {labels.boxesFound}
              <span>·</span>
              <strong>{exportState.speciesCount}</strong> {labels.speciesFound}
              <span>·</span>
              <strong>{exportState.organizationCount}</strong> {labels.organizationsFound}
            </p>
          ) : (
            <p className="export-no-result">{labels.noBoxes}</p>
          )}
          <p className="export-format">
            <strong>{labels.format}</strong>
            <span>{labels.formatHelp}</span>
          </p>
        </div>

        <button
          type="button"
          disabled={isDownloading || invalidPeriod || !exportState.matchingBoxes.length}
          onClick={handleDownload}
        >
          {isDownloading ? labels.downloading : labels.download}
        </button>
      </section>

      {message ? <p className="inline-success export-feedback">{message}</p> : null}
      {error ? <p className="inline-error export-feedback">{error}</p> : null}
    </section>
  );
}

function FilterDisclosure({
  title,
  options,
  selectedIds,
  labels,
  onToggle,
  onClear,
}: {
  title: string;
  options: FilterOption[];
  selectedIds: number[];
  labels: (typeof copy)[Language];
  onToggle: (id: number) => void;
  onClear: () => void;
}) {
  const selectedOptions = options.filter((option) => selectedIds.includes(option.id));
  const summary =
    selectedOptions.length === 0
      ? labels.all
      : selectedOptions.length <= 2
        ? selectedOptions.map((option) => option.label).join(', ')
        : `${selectedOptions.length} ${labels.selected}`;

  return (
    <details className="export-filter">
      <summary>
        <strong>{title}</strong>
        <span>{summary}</span>
      </summary>
      <div className="export-filter-content">
        <div className="export-filter-actions">
          <span>
            {options.length} {labels.optionCount}
          </span>
          {selectedIds.length ? (
            <button type="button" onClick={onClear}>
              {labels.clear}
            </button>
          ) : null}
        </div>
        <div className="export-filter-options">
          {!options.length ? <span className="muted">{labels.empty}</span> : null}
          {options.map((option) => (
            <label key={option.id}>
              <input
                type="checkbox"
                checked={selectedIds.includes(option.id)}
                onChange={() => onToggle(option.id)}
              />
              <span>
                <strong>{option.label}</strong>
                {option.detail ? <small>{option.detail}</small> : null}
              </span>
            </label>
          ))}
        </div>
      </div>
    </details>
  );
}

function filterBoxes(
  options: ExportOptions,
  filters: ExportFilters,
  excludedFilter?: FilterKey,
) {
  return options.boxes.filter((box) => {
    return (
      matchesFilter(filters.organizations, box.organization_id, excludedFilter === 'organizations') &&
      matchesFilter(filters.species, box.species_id, excludedFilter === 'species') &&
      matchesFilter(filters.strains, box.strain_id, excludedFilter === 'strains') &&
      matchesFilter(filters.zones, box.thermal_zone_id, excludedFilter === 'zones') &&
      matchesFilter(filters.boxes, box.id, excludedFilter === 'boxes')
    );
  });
}

function matchesFilter(values: number[], value: number | null, excluded: boolean) {
  return excluded || values.length === 0 || (value !== null && values.includes(value));
}

function availableIds(
  options: ExportOptions,
  filters: ExportFilters,
  excludedFilter: FilterKey,
  field: 'id' | 'species_id' | 'strain_id' | 'thermal_zone_id',
) {
  return new Set(
    filterBoxes(options, filters, excludedFilter)
      .map((box) => box[field])
      .filter((value): value is number => value !== null),
  );
}

function withSelected(available: Set<number>, selectedIds: number[]) {
  selectedIds.forEach((id) => available.add(id));
  return available;
}

function buildOrganizationOptions(options: ExportOptions): FilterOption[] {
  return options.organizations.map((organization) => ({
    id: organization.id,
    label: organization.name,
  }));
}

function buildSpeciesOptions(options: ExportOptions, available: Set<number>): FilterOption[] {
  return options.species
    .filter((species) => available.has(species.id))
    .map((species) => ({ id: species.id, label: species.name }));
}

function buildStrainOptions(options: ExportOptions, available: Set<number>): FilterOption[] {
  return options.strains
    .filter((strain) => available.has(strain.id))
    .map((strain) => ({
      id: strain.id,
      label: strain.code,
      detail: strain.species_name,
    }));
}

function buildZoneOptions(options: ExportOptions, available: Set<number>): FilterOption[] {
  return options.zones
    .filter((zone) => available.has(zone.id))
    .map((zone) => ({
      id: zone.id,
      label: zone.name,
      detail: getOrganizationName(options, zone.organization_id),
    }));
}

function buildBoxOptions(options: ExportOptions, available: Set<number>): FilterOption[] {
  return options.boxes
    .filter((box) => available.has(box.id))
    .map((box) => ({
      id: box.id,
      label: box.global_code,
      detail: box.local_code || undefined,
    }));
}

function getOrganizationName(options: ExportOptions, organizationId: number) {
  return options.organizations.find((organization) => organization.id === organizationId)?.name;
}

function formatPeriod(dateFrom: string, dateTo: string, language: Language) {
  const formatter = new Intl.DateTimeFormat(language === 'fr' ? 'fr-FR' : 'en-GB', {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
  });
  const start = dateFrom ? formatter.format(new Date(`${dateFrom}T00:00:00`)) : '…';
  const end = dateTo ? formatter.format(new Date(`${dateTo}T00:00:00`)) : '…';
  return `${start} → ${end}`;
}
