import { useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from 'react';

import { apiDownload, apiGet } from '../api/client';
import type { BoxDetail, ExportOptions } from '../types';
import { addChartMonths, parseChartDate, toChartDateString } from '../utils/chartWindow';
import BiologicalTrendChart from './BiologicalTrendChart';
import PageLoader from './PageLoader';
import PolypbaseIcon from './PolypbaseIcon';

type Language = 'fr' | 'en';
type FilterKey = 'species' | 'strains' | 'boxes' | 'zones';

type ExportFilters = Record<FilterKey, number[]> & {
  dateFrom: string;
  dateTo: string;
  includeOtherZones: boolean;
};

type FilterOption = {
  id: number;
  label: string;
  detail?: string;
};

type PreviewBoxOption = {
  id: number;
  label: string;
  detail: string;
  zone: string | null;
};

type BoxTrendPreview = Pick<
  BoxDetail,
  'id' | 'biological_measurements' | 'locations' | 'movements'
>;

type ExportEligibility = {
  box_ids: number[];
  measurement_count: number;
};

const PREVIEW_PICKER_PAGE_SIZE = 16;

const emptyFilters: ExportFilters = {
  species: [],
  strains: [],
  boxes: [],
  zones: [],
  dateFrom: '',
  dateTo: '',
  includeOtherZones: false,
};

const copy = {
  fr: {
    all: 'Toutes',
    filtersTitle: 'Filtres',
    periodTitle: 'P\u00e9riode',
    selectedMeasurementsTitle: 'Analyse des relev\u00e9s',
    allHistory: "Tout l'historique",
    boxes: 'Boîtes',
    boxesFound: 'boîtes',
    boxPreviewHelp: 'Lecture détaillée d’une boîte sur la période d’export.',
    clear: 'Effacer',
    dateFrom: 'Date de début',
    dateRangeSeparator: 'au',
    dateTo: 'Date de fin',
    download: 'Télécharger le CSV',
    downloading: 'Préparation...',
    empty: 'Aucune valeur disponible',
    error: 'Impossible de générer le fichier.',
    format: 'CSV hebdomadaire',
    formatHelp: 'Polypes, éphyrules et température pour chaque boîte.',
    invalidPeriod: 'La date de fin doit être postérieure à la date de début.',
    eligibilityError: 'Impossible de vérifier les relevés correspondant aux filtres.',
    noBoxes: 'Aucune boîte ne correspond à ces filtres.',
    noLocation: 'Sans emplacement',
    noPreviewBox: 'Aucune boîte trouvée',
    optionCount: 'valeurs',
    previewEmpty: 'Aucun relevé à afficher pour cette boîte et cette période.',
    previewEmptyHint: 'Modifiez la période ou consultez une autre boîte.',
    previewEmptyTitle: 'Aucun relevé sur cette période',
    previewError: 'Impossible de charger les relevés de cette boîte.',
    previewLoading: 'Chargement des relevés...',
    previewMeasurements: 'Relevés',
    previewPagination: 'Pages des boîtes',
    previewPaginationNext: 'Page suivante',
    previewPaginationPage: 'Page',
    previewPaginationPrevious: 'Page précédente',
    previewPeriod: 'Période analysée',
    previewReading: 'Relevé consulté',
    previewTitle: 'Évolution des relevés',
    previewZone: 'Emplacement actuel',
    previousBox: 'Boîte précédente',
    nextBox: 'Boîte suivante',
    reset: 'Tout réinitialiser',
    searchBoxes: 'Rechercher une boîte',
    searchBoxesPlaceholder: 'Code global, local, espèce ou souche',
    searchPreviewBoxPlaceholder: 'Code, espèce, souche ou emplacement',
    searchSpecies: 'Rechercher une espèce',
    searchSpeciesPlaceholder: 'Nom scientifique ou commun',
    searchStrains: 'Rechercher une souche',
    searchStrainsPlaceholder: 'Code souche ou espèce',
    selected: 'sélection',
    selectPreviewBox: 'Boîte analysée',
    selectPreviewBoxPlaceholder: 'Choisir une boîte',
    species: 'Espèces',
    speciesFound: 'espèces',
    strains: 'Souches',
    temperature: 'Température',
    success: 'Fichier téléchargé',
    zones: 'Emplacements thermiques',
    zoneScopeLabel: 'Inclure aussi les relevés réalisés dans d’autres emplacements',
    zoneScopeHelp: 'Sinon, seuls les relevés réalisés dans les emplacements sélectionnés sont conservés.',
  },
  en: {
    all: 'All',
    filtersTitle: 'Filters',
    periodTitle: 'Period',
    selectedMeasurementsTitle: 'Measurement review',
    allHistory: 'Full history',
    boxes: 'Boxes',
    boxesFound: 'boxes',
    boxPreviewHelp: 'Detailed review of one box over the export period.',
    clear: 'Clear',
    dateFrom: 'Start date',
    dateRangeSeparator: 'to',
    dateTo: 'End date',
    download: 'Download CSV',
    downloading: 'Preparing...',
    empty: 'No value available',
    error: 'The file could not be generated.',
    format: 'Weekly CSV',
    formatHelp: 'Polyps, ephyrae and temperature for each box.',
    invalidPeriod: 'The end date must be after the start date.',
    eligibilityError: 'Unable to check measurements matching the filters.',
    noBoxes: 'No box matches these filters.',
    noLocation: 'No location',
    noPreviewBox: 'No box found',
    optionCount: 'values',
    previewEmpty: 'No measurement to show for this box and period.',
    previewEmptyHint: 'Change the period or review another box.',
    previewEmptyTitle: 'No measurement in this period',
    previewError: 'Unable to load this box’s measurements.',
    previewLoading: 'Loading measurements...',
    previewMeasurements: 'Measurements',
    previewPagination: 'Box pages',
    previewPaginationNext: 'Next page',
    previewPaginationPage: 'Page',
    previewPaginationPrevious: 'Previous page',
    previewPeriod: 'Analysed period',
    previewReading: 'Reviewed measurement',
    previewTitle: 'Measurement trends',
    previewZone: 'Current location',
    previousBox: 'Previous box',
    nextBox: 'Next box',
    reset: 'Reset all',
    searchBoxes: 'Search for a box',
    searchBoxesPlaceholder: 'Global code, local code, species or strain',
    searchPreviewBoxPlaceholder: 'Code, species, strain or location',
    searchSpecies: 'Search for a species',
    searchSpeciesPlaceholder: 'Scientific or common name',
    searchStrains: 'Search for a strain',
    searchStrainsPlaceholder: 'Strain code or species',
    selected: 'selected',
    selectPreviewBox: 'Analysed box',
    selectPreviewBoxPlaceholder: 'Choose a box',
    species: 'Species',
    speciesFound: 'species',
    strains: 'Strains',
    temperature: 'Temperature',
    success: 'File downloaded',
    zones: 'Thermal zones',
    zoneScopeLabel: 'Also include measurements recorded in other locations',
    zoneScopeHelp: 'Otherwise, only measurements recorded in the selected locations are kept.',
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
  const [selectedPreviewBoxId, setSelectedPreviewBoxId] = useState<number | null>(null);
  const [trendCache, setTrendCache] = useState<Record<string, BoxTrendPreview>>({});
  const [eligibleBoxIds, setEligibleBoxIds] = useState<Set<number> | null>(null);
  const [isEligibilityLoading, setIsEligibilityLoading] = useState(false);
  const [isPreviewLoading, setIsPreviewLoading] = useState(false);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const previewPanelElementRef = useRef<HTMLDivElement | null>(null);
  const previewMinimumHeightRef = useRef<number | null>(null);
  const previewScrollPositionRef = useRef<number | null>(null);
  const labels = copy[language];
  const invalidPeriod = Boolean(
    filters.dateFrom && filters.dateTo && filters.dateFrom > filters.dateTo,
  );

  useEffect(() => {
    if (!options) return;
    if (invalidPeriod) {
      setEligibleBoxIds(new Set());
      setIsEligibilityLoading(false);
      return;
    }

    const controller = new AbortController();
    setIsEligibilityLoading(true);
    const timeoutId = window.setTimeout(async () => {
      try {
        const query = buildEligibilityQuery(filters);
        const eligibility = await apiGet<ExportEligibility>(
          `/api/exports/eligible-boxes/${query ? `?${query}` : ''}`,
          { signal: controller.signal },
        );
        setEligibleBoxIds(new Set(eligibility.box_ids));
      } catch (eligibilityLoadError) {
        if (isAbortError(eligibilityLoadError)) return;
        setEligibleBoxIds((current) => current ?? new Set());
        setError(
          eligibilityLoadError instanceof Error
            ? eligibilityLoadError.message
            : labels.eligibilityError,
        );
      } finally {
        if (!controller.signal.aborted) setIsEligibilityLoading(false);
      }
    }, 120);

    return () => {
      window.clearTimeout(timeoutId);
      controller.abort();
    };
  }, [
    filters.dateFrom,
    filters.dateTo,
    filters.includeOtherZones,
    filters.zones,
    invalidPeriod,
    labels.eligibilityError,
    options,
  ]);

  const exportState = useMemo(() => {
    if (!options || eligibleBoxIds === null) return null;

    const eligibleOptions = {
      ...options,
      boxes: options.boxes.filter((box) => eligibleBoxIds.has(box.id)),
    };
    const matchingBoxes = filterBoxes(eligibleOptions, filters);
    const matchingSpeciesIds = new Set(matchingBoxes.map((box) => box.species_id));

    return {
      matchingBoxes,
      speciesCount: matchingSpeciesIds.size,
      groups: {
        species: buildSpeciesOptions(
          options,
          withSelected(
            availableIds(eligibleOptions, filters, 'species', 'species_id'),
            filters.species,
          ),
        ),
        strains: buildStrainOptions(
          options,
          withSelected(
            availableIds(eligibleOptions, filters, 'strains', 'strain_id'),
            filters.strains,
          ),
        ),
        zones: buildZoneOptions(
          options,
          new Set(options.zones.map((zone) => zone.id)),
        ),
        boxes: buildBoxOptions(
          options,
          withSelected(availableIds(eligibleOptions, filters, 'boxes', 'id'), filters.boxes),
        ),
      },
    };
  }, [eligibleBoxIds, filters, options]);
  const hasFilters = (Object.keys(emptyFilters) as Array<keyof ExportFilters>).some((key) => {
    const value = filters[key];
    return Array.isArray(value) ? value.length > 0 : Boolean(value);
  });
  const previewBoxOptions = useMemo(
    () => (exportState && options
      ? buildPreviewBoxOptions(exportState.matchingBoxes, options)
      : []),
    [exportState, options],
  );
  const selectedPreviewIndex = previewBoxOptions.findIndex(
    (box) => box.id === selectedPreviewBoxId,
  );
  const selectedPreviewOption = selectedPreviewIndex >= 0
    ? previewBoxOptions[selectedPreviewIndex]
    : null;
  const previewWindow = useMemo(
    () => buildPreviewWindow(filters.dateFrom, filters.dateTo),
    [filters.dateFrom, filters.dateTo],
  );
  const selectedPreviewCacheKey = selectedPreviewBoxId === null
    ? null
    : [
        selectedPreviewBoxId,
        previewWindow.startDate,
        previewWindow.endDate,
        filters.zones.join(','),
        filters.includeOtherZones ? 'all-zones' : 'selected-zones',
      ].join(':');
  const selectedPreviewDetail = selectedPreviewCacheKey === null
    ? null
    : trendCache[selectedPreviewCacheKey] ?? null;

  useEffect(() => {
    if (
      selectedPreviewBoxId !== null
      && !previewBoxOptions.some((box) => box.id === selectedPreviewBoxId)
    ) {
      setSelectedPreviewBoxId(null);
      setPreviewError(null);
    }
  }, [previewBoxOptions, selectedPreviewBoxId]);

  useEffect(() => {
    const controller = new AbortController();

    async function loadSelectedBox() {
      if (
        selectedPreviewBoxId === null
        || selectedPreviewCacheKey === null
        || trendCache[selectedPreviewCacheKey]
        || invalidPeriod
      ) {
        setIsPreviewLoading(false);
        return;
      }

      setIsPreviewLoading(true);
      setPreviewError(null);

      try {
        const query = new URLSearchParams({
          date_from: previewWindow.startDate,
          date_to: previewWindow.endDate,
        });
        if (filters.zones.length) query.set('zones', filters.zones.join(','));
        if (filters.zones.length && filters.includeOtherZones) {
          query.set('include_other_zones', 'true');
        }
        const detail = await apiGet<BoxTrendPreview>(
          `/api/exports/boxes/${selectedPreviewBoxId}/trend/?${query}`,
          { signal: controller.signal },
        );
        setTrendCache((current) => {
          const next = { ...current, [selectedPreviewCacheKey]: detail };
          const keys = Object.keys(next);
          if (keys.length > 20) delete next[keys[0]];
          return next;
        });
      } catch (previewLoadError) {
        if (isAbortError(previewLoadError)) return;
        setPreviewError(
          previewLoadError instanceof Error ? previewLoadError.message : labels.previewError,
        );
      } finally {
        if (!controller.signal.aborted) setIsPreviewLoading(false);
      }
    }

    void loadSelectedBox();

    return () => controller.abort();
  }, [
    invalidPeriod,
    labels.previewError,
    filters.includeOtherZones,
    filters.zones,
    previewWindow.endDate,
    previewWindow.startDate,
    selectedPreviewBoxId,
    selectedPreviewCacheKey,
    trendCache,
  ]);

  useLayoutEffect(() => {
    const scrollPosition = previewScrollPositionRef.current;
    if (scrollPosition === null) return;

    window.scrollTo(0, scrollPosition);
    const frame = window.requestAnimationFrame(() => {
      window.scrollTo(0, scrollPosition);
    });
    return () => window.cancelAnimationFrame(frame);
  }, [selectedPreviewBoxId]);

  useLayoutEffect(() => {
    const scrollPosition = previewScrollPositionRef.current;
    if (scrollPosition === null || isPreviewLoading) return;

    const frame = window.requestAnimationFrame(() => {
      window.scrollTo(0, scrollPosition);
      previewScrollPositionRef.current = null;
      previewMinimumHeightRef.current = null;
    });
    return () => window.cancelAnimationFrame(frame);
  }, [isPreviewLoading, previewError, selectedPreviewDetail]);

  function updateDate(key: 'dateFrom' | 'dateTo', value: string) {
    setFilters((current) => ({ ...current, [key]: value }));
    clearFeedback();
  }

  function toggleFilter(key: FilterKey, id: number) {
    setFilters((current) => {
      const nextValues = current[key].includes(id)
        ? current[key].filter((value) => value !== id)
        : [...current[key], id];
      return {
        ...current,
        [key]: nextValues,
        includeOtherZones: key === 'zones' && nextValues.length === 0
          ? false
          : current.includeOtherZones,
      };
    });
    clearFeedback();
  }

  function clearFilter(key: FilterKey) {
    setFilters((current) => ({
      ...current,
      [key]: [],
      includeOtherZones: key === 'zones' ? false : current.includeOtherZones,
    }));
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
      const query = buildExportQuery(filters);
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
    return <PageLoader variant="exports" label={labels.previewLoading} />;
  }

  function selectPreviewBox(boxId: number) {
    if (boxId === selectedPreviewBoxId) return;
    const currentPanelHeight = previewPanelElementRef.current?.getBoundingClientRect().height;
    if (currentPanelHeight) {
      previewMinimumHeightRef.current = Math.max(
        previewMinimumHeightRef.current ?? 0,
        Math.ceil(currentPanelHeight),
      );
    }
    previewScrollPositionRef.current = window.scrollY;
    setIsPreviewLoading(true);
    setSelectedPreviewBoxId(boxId);
    setPreviewError(null);
  }

  const visiblePreviewMeasurements = selectedPreviewDetail
    ? selectedPreviewDetail.biological_measurements.filter((measurement) => (
        measurement.measured_on >= previewWindow.startDate
        && measurement.measured_on <= previewWindow.endDate
      )).sort((left, right) => left.measured_on.localeCompare(right.measured_on))
    : [];
  const visiblePreviewMeasurementCount = visiblePreviewMeasurements.length;

  function selectAdjacentPreviewBox(offset: number) {
    const nextIndex = selectedPreviewIndex + offset;
    const nextBox = previewBoxOptions[nextIndex];
    if (!nextBox) return;
    selectPreviewBox(nextBox.id);
  }

  return (
    <section className="export-page">
      <section className="export-step export-period-step">
        <header className="export-step-heading">
          <h2>{labels.periodTitle}</h2>
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
          <span className="export-period-separator" aria-hidden="true">
            {labels.dateRangeSeparator}
          </span>
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
          <h2>{labels.filtersTitle}</h2>
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
            title={labels.species}
            options={exportState.groups.species}
            selectedIds={filters.species}
            labels={labels}
            searchable
            searchLabel={labels.searchSpecies}
            searchPlaceholder={labels.searchSpeciesPlaceholder}
            onToggle={(id) => toggleFilter('species', id)}
            onClear={() => clearFilter('species')}
          />
          <FilterDisclosure
            title={labels.strains}
            options={exportState.groups.strains}
            selectedIds={filters.strains}
            labels={labels}
            searchable
            searchLabel={labels.searchStrains}
            searchPlaceholder={labels.searchStrainsPlaceholder}
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
            extraContent={filters.zones.length ? (
              <label className="export-zone-scope">
                <input
                  type="checkbox"
                  checked={filters.includeOtherZones}
                  onChange={(event) => {
                    setFilters((current) => ({
                      ...current,
                      includeOtherZones: event.target.checked,
                    }));
                    clearFeedback();
                  }}
                />
                <span>
                  <strong>{labels.zoneScopeLabel}</strong>
                  <small>{labels.zoneScopeHelp}</small>
                </span>
              </label>
            ) : null}
          />
          <FilterDisclosure
            title={labels.boxes}
            options={exportState.groups.boxes}
            selectedIds={filters.boxes}
            labels={labels}
            searchable
            onToggle={(id) => toggleFilter('boxes', id)}
            onClear={() => clearFilter('boxes')}
          />
        </div>
      </section>

      <section className="export-preview">
        <header className="export-step-heading">
          <div>
            <h2>{labels.selectedMeasurementsTitle}</h2>
            <p>{labels.boxPreviewHelp}</p>
          </div>
          <span aria-live="polite">
            {isEligibilityLoading ? '...' : previewBoxOptions.length} {labels.boxesFound}
          </span>
        </header>

        <div className="export-preview-selector">
          <button
            type="button"
            title={labels.previousBox}
            aria-label={labels.previousBox}
            disabled={selectedPreviewIndex <= 0}
            onClick={() => selectAdjacentPreviewBox(-1)}
          >
            <PolypbaseIcon name="chevron-left" size={18} />
          </button>
          <PreviewBoxPicker
            emptyLabel={labels.noPreviewBox}
            label={labels.selectPreviewBox}
            placeholder={labels.selectPreviewBoxPlaceholder}
            resultsLabel={labels.boxesFound}
            searchLabel={labels.searchBoxes}
            searchPlaceholder={labels.searchPreviewBoxPlaceholder}
            paginationLabel={labels.previewPagination}
            paginationNextLabel={labels.previewPaginationNext}
            paginationPageLabel={labels.previewPaginationPage}
            paginationPreviousLabel={labels.previewPaginationPrevious}
            options={previewBoxOptions}
            selectedId={selectedPreviewBoxId}
            onSelect={selectPreviewBox}
          />
          <button
            type="button"
            title={labels.nextBox}
            aria-label={labels.nextBox}
            disabled={selectedPreviewIndex < 0 || selectedPreviewIndex >= previewBoxOptions.length - 1}
            onClick={() => selectAdjacentPreviewBox(1)}
          >
            <PolypbaseIcon name="chevron-right" size={18} />
          </button>
        </div>

        {isPreviewLoading ? (
          <div
            ref={previewPanelElementRef}
            className="export-chart-state"
            style={{ minHeight: previewMinimumHeightRef.current ?? undefined }}
            aria-busy="true"
          >
            {labels.previewLoading}
          </div>
        ) : null}
        {previewError ? <div className="export-chart-state is-error">{previewError}</div> : null}
        {!selectedPreviewOption && !isPreviewLoading ? (
          <div className="export-chart-state is-empty">{labels.boxPreviewHelp}</div>
        ) : null}
        {selectedPreviewOption && selectedPreviewDetail && !previewError ? (
          <div
            ref={previewPanelElementRef}
            className={`export-trend-panel${visiblePreviewMeasurementCount ? '' : ' is-empty'}`}
          >
            <section className="export-trend-overview">
              <dl className="export-trend-context">
                <div>
                  <dt>{labels.previewPeriod}</dt>
                  <dd>{formatPeriod(previewWindow.startDate, previewWindow.endDate, language)}</dd>
                </div>
                <div>
                  <dt>{labels.previewZone}</dt>
                  <dd>{selectedPreviewOption.zone || labels.noLocation}</dd>
                </div>
                <div>
                  <dt>{labels.previewMeasurements}</dt>
                  <dd>{visiblePreviewMeasurementCount}</dd>
                </div>
              </dl>
            </section>
            {visiblePreviewMeasurementCount > 0 ? (
              <BiologicalTrendChart
                detailDisplay="inline"
                startDate={previewWindow.startDate}
                endDate={previewWindow.endDate}
                measurements={visiblePreviewMeasurements.map((measurement) => ({
                  id: measurement.id,
                  date: measurement.measured_on,
                  polypCount: measurement.polyp_count,
                  ephyraeCount: measurement.ephyrae_count,
                  salinity: measurement.salinity_psu,
                  enteredBy: measurement.user,
                  note: measurement.notes,
                }))}
                locations={selectedPreviewDetail.locations.map((location) => ({
                  id: location.id,
                  name: location.thermal_zone.name,
                  startsAt: location.starts_at,
                  endsAt: location.ends_at,
                  endDateUnknown: location.end_date_unknown,
                }))}
                events={selectedPreviewDetail.movements.map((movement) => ({
                  id: `movement-${movement.id}`,
                  date: movement.moved_at,
                  title: language === 'fr' ? 'Transfert' : 'Transfer',
                  detail: movement.to_thermal_zone.name,
                  kind: 'movement' as const,
                }))}
                selectionScope={`${selectedPreviewDetail.id}-${previewWindow.startDate}-${previewWindow.endDate}`}
                labels={{
                  chartTitle: labels.previewTitle,
                  empty: labels.previewEmpty,
                  enteredBy: language === 'fr' ? 'Saisi par' : 'Entered by',
                  ephyrae: language === 'fr' ? 'Éphyrules' : 'Ephyrae',
                  location: language === 'fr' ? 'Emplacement' : 'Location',
                  missingReading: language === 'fr' ? 'Période sans relevé' : 'Period without measurement',
                  movement: language === 'fr' ? 'Transfert' : 'Transfer',
                  observation: language === 'fr' ? 'Observation' : 'Observation',
                  polyps: language === 'fr' ? 'Polypes' : 'Polyps',
                  salinity: 'PSU',
                  selectReading: language === 'fr'
                    ? 'Sélectionnez un point pour consulter le relevé.'
                    : 'Select a point to review the measurement.',
                  selectedReading: labels.previewReading,
                }}
              />
            ) : (
              <div className="export-trend-empty">
                <strong>{labels.previewEmptyTitle}</strong>
                <span>{labels.previewEmptyHint}</span>
              </div>
            )}
          </div>
        ) : null}
      </section>

      <section className="export-review">
        <div>
          {exportState.matchingBoxes.length ? (
            <p className="export-review-count">
              <strong>{exportState.matchingBoxes.length}</strong> {labels.boxesFound}
              <strong>{exportState.speciesCount}</strong> {labels.speciesFound}
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
          disabled={
            isDownloading
            || isEligibilityLoading
            || invalidPeriod
            || !exportState.matchingBoxes.length
          }
          onClick={handleDownload}
        >
          <span className="button-icon-label">
            {!isDownloading ? <PolypbaseIcon name="download" size={18} /> : null}
            {isDownloading ? labels.downloading : labels.download}
          </span>
        </button>
      </section>

      {message ? <p className="inline-success export-feedback">{message}</p> : null}
      {error ? <p className="inline-error export-feedback">{error}</p> : null}
    </section>
  );
}

function PreviewBoxPicker({
  emptyLabel,
  label,
  onSelect,
  options,
  paginationLabel,
  paginationNextLabel,
  paginationPageLabel,
  paginationPreviousLabel,
  placeholder,
  resultsLabel,
  searchLabel,
  searchPlaceholder,
  selectedId,
}: {
  emptyLabel: string;
  label: string;
  onSelect: (boxId: number) => void;
  options: PreviewBoxOption[];
  paginationLabel: string;
  paginationNextLabel: string;
  paginationPageLabel: string;
  paginationPreviousLabel: string;
  placeholder: string;
  resultsLabel: string;
  searchLabel: string;
  searchPlaceholder: string;
  selectedId: number | null;
}) {
  const selectedOption = options.find((option) => option.id === selectedId) ?? null;
  const selectedIndex = options.findIndex((option) => option.id === selectedId);
  const [query, setQuery] = useState('');
  const [isOpen, setIsOpen] = useState(false);
  const [page, setPage] = useState(0);
  const optionsElementRef = useRef<HTMLDivElement | null>(null);
  const queryTokens = normalizeSearchText(query).split(/\s+/).filter(Boolean);
  const matchingOptions = queryTokens.length
    ? options.filter((option) => {
        const searchableText = normalizeSearchText(
          [option.label, option.detail, option.zone].filter(Boolean).join(' '),
        );
        return queryTokens.every((token) => matchesSearchToken(searchableText, token));
      })
    : options;
  const pageCount = Math.ceil(matchingOptions.length / PREVIEW_PICKER_PAGE_SIZE);
  const currentPage = pageCount ? Math.min(page, pageCount - 1) : 0;
  const pageStart = currentPage * PREVIEW_PICKER_PAGE_SIZE;
  const visibleOptions = matchingOptions.slice(
    pageStart,
    pageStart + PREVIEW_PICKER_PAGE_SIZE,
  );
  const paginationItems = buildPaginationItems(currentPage, pageCount);

  function selectOption(option: PreviewBoxOption) {
    onSelect(option.id);
    setQuery('');
    setIsOpen(false);
  }

  function goToPage(nextPage: number) {
    setPage(Math.min(Math.max(0, nextPage), Math.max(0, pageCount - 1)));
    if (optionsElementRef.current) optionsElementRef.current.scrollTop = 0;
  }

  return (
    <div
      className="export-preview-picker"
      onBlur={(event) => {
        if (!event.currentTarget.contains(event.relatedTarget as Node | null)) {
          setIsOpen(false);
          setQuery('');
        }
      }}
    >
      <button
        className="export-preview-picker-trigger"
        type="button"
        aria-expanded={isOpen}
        aria-controls="export-preview-box-options"
        aria-haspopup="listbox"
        onClick={() => {
          if (!isOpen) {
            setPage(selectedIndex < 0 ? 0 : Math.floor(selectedIndex / PREVIEW_PICKER_PAGE_SIZE));
          }
          setIsOpen((current) => !current);
          setQuery('');
        }}
      >
        <span>
          <small>{label}</small>
          <strong>{selectedOption?.label ?? placeholder}</strong>
          {selectedOption ? <em>{selectedOption.detail}</em> : null}
        </span>
      </button>
      {isOpen ? (
        <div className="export-preview-picker-panel">
          <label className="export-preview-picker-search">
            <span>{searchLabel}</span>
            <input
              type="search"
              role="combobox"
              autoComplete="off"
              autoFocus
              aria-expanded="true"
              aria-controls="export-preview-box-options"
              placeholder={searchPlaceholder}
              value={query}
              onChange={(event) => {
                setQuery(event.target.value);
                setPage(0);
              }}
              onKeyDown={(event) => {
                if (event.key === 'Escape') {
                  setIsOpen(false);
                  setQuery('');
                } else if (event.key === 'Enter' && visibleOptions[0]) {
                  event.preventDefault();
                  selectOption(visibleOptions[0]);
                }
              }}
            />
          </label>
          <div className="export-preview-picker-summary">
            <strong>{matchingOptions.length}</strong>
            <span>{resultsLabel}</span>
          </div>
          <div
            ref={optionsElementRef}
            id="export-preview-box-options"
            className="export-preview-picker-options"
            role="listbox"
          >
            {visibleOptions.map((option) => (
              <button
                key={option.id}
                type="button"
                role="option"
                aria-selected={option.id === selectedId}
                onClick={() => selectOption(option)}
              >
                <strong>{option.label}</strong>
                <span>{option.detail}</span>
                <small>{option.zone ?? ''}</small>
              </button>
            ))}
            {!visibleOptions.length ? <p>{emptyLabel}</p> : null}
          </div>
          {pageCount > 1 ? (
            <nav className="export-preview-picker-pagination" aria-label={paginationLabel}>
              <button
                type="button"
                aria-label={paginationPreviousLabel}
                disabled={currentPage === 0}
                onClick={() => goToPage(currentPage - 1)}
              >
                <PolypbaseIcon name="chevron-left" size={15} />
              </button>
              {paginationItems.map((item, index) => (
                item === null ? (
                  <span key={`ellipsis-${index}`} aria-hidden="true">...</span>
                ) : (
                  <button
                    key={item}
                    type="button"
                    aria-current={item === currentPage ? 'page' : undefined}
                    aria-label={`${paginationPageLabel} ${item + 1}`}
                    onClick={() => goToPage(item)}
                  >
                    {item + 1}
                  </button>
                )
              ))}
              <button
                type="button"
                aria-label={paginationNextLabel}
                disabled={currentPage === pageCount - 1}
                onClick={() => goToPage(currentPage + 1)}
              >
                <PolypbaseIcon name="chevron-right" size={15} />
              </button>
            </nav>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

function buildPaginationItems(currentPage: number, pageCount: number): Array<number | null> {
  if (pageCount <= 7) return Array.from({ length: pageCount }, (_, index) => index);

  const pages = new Set<number>([0, pageCount - 1]);
  if (currentPage <= 3) {
    [1, 2, 3, 4].forEach((page) => pages.add(page));
  } else if (currentPage >= pageCount - 4) {
    for (let page = pageCount - 5; page < pageCount - 1; page += 1) pages.add(page);
  } else {
    [currentPage - 1, currentPage, currentPage + 1].forEach((page) => pages.add(page));
  }

  const sortedPages = Array.from(pages).sort((left, right) => left - right);
  const items: Array<number | null> = [];
  sortedPages.forEach((page, index) => {
    const previousPage = sortedPages[index - 1];
    if (index > 0 && page - previousPage > 1) items.push(null);
    items.push(page);
  });
  return items;
}

function normalizeSearchText(value: string) {
  return value
    .normalize('NFD')
    .replace(/[\u0300-\u036f]/g, '')
    .trim()
    .toLocaleLowerCase();
}

function matchesSearchToken(searchableText: string, token: string) {
  if (!/^\d+(?:[.,]\d+)?$/.test(token)) return searchableText.includes(token);
  const escapedToken = token.replaceAll('.', '\\.');
  return new RegExp('(^|\\D)' + escapedToken + '(?=\\D|$)').test(searchableText);
}

function FilterDisclosure({
  title,
  options,
  selectedIds,
  labels,
  searchable = false,
  searchLabel,
  searchPlaceholder,
  extraContent,
  onToggle,
  onClear,
}: {
  title: string;
  options: FilterOption[];
  selectedIds: number[];
  labels: (typeof copy)[Language];
  searchable?: boolean;
  searchLabel?: string;
  searchPlaceholder?: string;
  extraContent?: ReactNode;
  onToggle: (id: number) => void;
  onClear: () => void;
}) {
  const [query, setQuery] = useState('');
  const [isOpen, setIsOpen] = useState(false);
  const selectedOptions = options.filter((option) => selectedIds.includes(option.id));
  const normalizedQuery = query.trim().toLocaleLowerCase();
  const matchingOptions = normalizedQuery
    ? options.filter((option) => [option.label, option.detail]
      .filter(Boolean)
      .some((value) => value!.toLocaleLowerCase().includes(normalizedQuery)))
    : options;
  const visibleOptions = searchable ? matchingOptions.slice(0, 60) : matchingOptions;
  const summary =
    selectedOptions.length === 0
      ? labels.all
      : selectedOptions.length <= 2
        ? selectedOptions.map((option) => option.label).join(', ')
        : `${selectedOptions.length} ${labels.selected}`;

  return (
    <details
      className="export-filter"
      onToggle={(event) => setIsOpen(event.currentTarget.open)}
    >
      <summary>
        <strong>{title}</strong>
        <span>{summary}</span>
      </summary>
      {isOpen ? <div className="export-filter-content">
        <div className="export-filter-actions">
          <span>
            {matchingOptions.length} {labels.optionCount}
          </span>
          {selectedIds.length ? (
            <button type="button" onClick={onClear}>
              {labels.clear}
            </button>
          ) : null}
        </div>
        {searchable ? (
          <label className="export-box-search">
            <span>{searchLabel ?? labels.searchBoxes}</span>
            <input
              type="search"
              value={query}
              placeholder={searchPlaceholder ?? labels.searchBoxesPlaceholder}
              onChange={(event) => setQuery(event.target.value)}
            />
          </label>
        ) : null}
        <div className="export-filter-options">
          {!visibleOptions.length ? <span className="muted">{labels.empty}</span> : null}
          {visibleOptions.map((option) => (
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
        {matchingOptions.length > visibleOptions.length ? (
          <p className="export-filter-limit">{visibleOptions.length} / {matchingOptions.length}</p>
        ) : null}
        {extraContent}
      </div> : null}
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
      matchesFilter(filters.species, box.species_id, excludedFilter === 'species') &&
      matchesFilter(filters.strains, box.strain_id, excludedFilter === 'strains') &&
      matchesFilter(filters.boxes, box.id, excludedFilter === 'boxes')
    );
  });
}

function matchesFilter(values: number[], value: number | null, excluded: boolean) {
  return excluded || values.length === 0 || (value !== null && values.includes(value));
}

function buildExportQuery(filters: ExportFilters) {
  const params = new URLSearchParams();
  for (const key of ['species', 'strains', 'boxes', 'zones'] as FilterKey[]) {
    if (filters[key].length) params.set(key, filters[key].join(','));
  }
  if (filters.dateFrom) params.set('date_from', filters.dateFrom);
  if (filters.dateTo) params.set('date_to', filters.dateTo);
  if (filters.zones.length && filters.includeOtherZones) {
    params.set('include_other_zones', 'true');
  }
  return params.toString();
}

function buildEligibilityQuery(filters: ExportFilters) {
  const params = new URLSearchParams();
  if (filters.zones.length) params.set('zones', filters.zones.join(','));
  if (filters.dateFrom) params.set('date_from', filters.dateFrom);
  if (filters.dateTo) params.set('date_to', filters.dateTo);
  if (filters.zones.length && filters.includeOtherZones) {
    params.set('include_other_zones', 'true');
  }
  return params.toString();
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

function buildPreviewBoxOptions(
  boxes: ExportOptions['boxes'],
  options: ExportOptions,
): PreviewBoxOption[] {
  const speciesById = new Map(options.species.map((species) => [species.id, species.name]));
  const strainsById = new Map(options.strains.map((strain) => [strain.id, strain]));
  const zonesById = new Map(options.zones.map((zone) => [zone.id, zone.name]));

  return boxes
    .map((box) => {
      const strain = strainsById.get(box.strain_id);
      return {
        id: box.id,
        label: box.global_code,
        detail: [speciesById.get(box.species_id), strain?.code].filter(Boolean).join(', '),
        zone: box.thermal_zone_id === null ? null : zonesById.get(box.thermal_zone_id) ?? null,
      };
    })
    .sort((left, right) => left.label.localeCompare(right.label));
}

function buildPreviewWindow(dateFrom: string, dateTo: string) {
  const today = toChartDateString(new Date());
  let endDate = dateTo || today;
  if (dateFrom && !dateTo && dateFrom > endDate) endDate = dateFrom;

  return {
    endDate,
    startDate: dateFrom || (dateTo
      ? toChartDateString(addChartMonths(parseChartDate(dateTo), -6))
      : toChartDateString(addChartMonths(parseChartDate(endDate), -6))),
  };
}

function isAbortError(error: unknown) {
  return error instanceof DOMException && error.name === 'AbortError';
}

function formatPeriod(dateFrom: string, dateTo: string, language: Language) {
  const formatter = new Intl.DateTimeFormat(language === 'fr' ? 'fr-FR' : 'en-GB', {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
  });
  const start = dateFrom ? formatter.format(new Date(`${dateFrom}T00:00:00`)) : '…';
  const end = dateTo ? formatter.format(new Date(`${dateTo}T00:00:00`)) : '…';
  return language === 'fr' ? `du ${start} au ${end}` : `from ${start} to ${end}`;
}
