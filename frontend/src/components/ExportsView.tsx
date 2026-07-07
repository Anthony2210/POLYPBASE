import { useEffect, useMemo, useState } from 'react';
import { scaleLinear, scalePoint, type ScaleLinear } from 'd3-scale';
import { line } from 'd3-shape';

import { apiDownload, apiGet } from '../api/client';
import type { ExportOptions } from '../types';
import PageLoader from './PageLoader';

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

type ExportPreviewPoint = {
  label: string;
  polyp_count: number;
  ephyrae_count: number;
  average_temperature_c: number | null;
  measurement_count: number;
};

type ExportPreview = {
  points: ExportPreviewPoint[];
  metadata: {
    box_count: number;
    measurement_count: number;
    week_count: number;
    date_from: string | null;
    date_to: string | null;
  };
};

type ComparisonMode = 'boxes' | 'strains';
type PreviewMetric = 'general' | 'polyps' | 'ephyrae' | 'temperature';

// Metrics overlaid when the "general" view is selected.
const GENERAL_METRICS: PreviewMetric[] = ['polyps', 'ephyrae', 'temperature'];

type ComparisonGroup = {
  id: number;
  key: string;
  label: string;
  detail: string;
  mode: ComparisonMode;
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

const DEFAULT_WEEKS_WINDOW = 20;
const MIN_WEEKS_WINDOW = 4;

const copy = {
  fr: {
    all: 'Toutes',
    allCharts: 'Voir tous les graphiques',
    comparisonByBoxes: 'Par bo\u00eete',
    comparisonByStrains: 'Par souche',
    comparisonTitle: 'Comparer les relev\u00e9s',
    close: 'Fermer',
    filtersTitle: 'Filtres',
    periodTitle: 'P\u00e9riode',
    selectedMeasurementsTitle: 'Relev\u00e9s s\u00e9lectionn\u00e9s',
    allHistory: "Tout l'historique",
    weeksWindowLabel: 'Semaines affich\u00e9es',
    weeksShown: 'derni\u00e8res semaines',
    allWeeksShort: "Tout l'historique",
    selectFilterPrompt: 'S\u00e9lectionnez au moins un filtre (structure, esp\u00e8ce, souche, armoire, bo\u00eete ou p\u00e9riode) pour afficher les graphiques.',
    boxes: 'Boîtes',
    boxesFound: 'boîtes',
    clear: 'Effacer',
    dateFrom: 'Date de début',
    dateTo: 'Date de fin',
    download: 'Télécharger le CSV',
    downloading: 'Préparation...',
    empty: 'Aucune valeur disponible',
    error: 'Impossible de générer le fichier.',
    format: 'CSV hebdomadaire',
    formatHelp: 'Polypes, éphyrules et température pour chaque boîte.',
    invalidPeriod: 'La date de fin doit être postérieure à la date de début.',
    noBoxes: 'Aucune boîte ne correspond à ces filtres.',
    organizations: 'Structures',
    organizationsFound: 'structures',
    optionCount: 'valeurs',
    previewEmpty: 'Aucune donnée à afficher avec cette sélection.',
    previewError: 'Impossible de charger l’aperçu.',
    previewLoading: 'Chargement de l’aperçu...',
    previewMeasurements: 'relevés',
    previewTitle: 'Aperçu des données sélectionnées',
    reset: 'Tout réinitialiser',
    searchBoxes: 'Rechercher une boîte',
    searchBoxesPlaceholder: 'Code global, local, espèce ou souche',
    selected: 'sélection',
    species: 'Espèces',
    speciesFound: 'espèces',
    strains: 'Souches',
    temperature: 'Température',
    success: 'Fichier téléchargé',
    zones: 'Armoires thermiques',
  },
  en: {
    all: 'All',
    allCharts: 'View all charts',
    comparisonByBoxes: 'By box',
    comparisonByStrains: 'By strain',
    comparisonTitle: 'Compare measurements',
    close: 'Close',
    filtersTitle: 'Filters',
    periodTitle: 'Period',
    selectedMeasurementsTitle: 'Selected measurements',
    allHistory: 'Full history',
    weeksWindowLabel: 'Weeks shown',
    weeksShown: 'last weeks',
    allWeeksShort: 'Full history',
    selectFilterPrompt: 'Select at least one filter (organization, species, strain, zone, box or period) to display the charts.',
    boxes: 'Boxes',
    boxesFound: 'boxes',
    clear: 'Clear',
    dateFrom: 'Start date',
    dateTo: 'End date',
    download: 'Download CSV',
    downloading: 'Preparing...',
    empty: 'No value available',
    error: 'The file could not be generated.',
    format: 'Weekly CSV',
    formatHelp: 'Polyps, ephyrae and temperature for each box.',
    invalidPeriod: 'The end date must be after the start date.',
    noBoxes: 'No box matches these filters.',
    organizations: 'Organizations',
    organizationsFound: 'organizations',
    optionCount: 'values',
    previewEmpty: 'No data to show for this selection.',
    previewError: 'Unable to load the preview.',
    previewLoading: 'Loading preview...',
    previewMeasurements: 'measurements',
    previewTitle: 'Selected data preview',
    reset: 'Reset all',
    searchBoxes: 'Search for a box',
    searchBoxesPlaceholder: 'Global code, local code, species or strain',
    selected: 'selected',
    species: 'Species',
    speciesFound: 'species',
    strains: 'Strains',
    temperature: 'Temperature',
    success: 'File downloaded',
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
  const [preview, setPreview] = useState<ExportPreview | null>(null);
  const [isPreviewLoading, setIsPreviewLoading] = useState(false);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [comparisonMode, setComparisonMode] = useState<ComparisonMode>('boxes');
  const [previewMetric, setPreviewMetric] = useState<PreviewMetric>('general');
  const [weeksWindow, setWeeksWindow] = useState(DEFAULT_WEEKS_WINDOW);
  const [comparisonPreviews, setComparisonPreviews] = useState<Record<string, ExportPreview>>({});
  const [isComparisonLoading, setIsComparisonLoading] = useState(false);
  const [isAllComparisonsOpen, setIsAllComparisonsOpen] = useState(false);
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
  const comparisonGroups = useMemo(
    () => (exportState && options
      ? buildComparisonGroups(exportState.matchingBoxes, options, comparisonMode)
      : []),
    [comparisonMode, exportState, options],
  );
  const sidebarComparisonGroups = useMemo(
    () => comparisonGroups.slice(0, 3),
    [comparisonGroups],
  );
  const requestedComparisonGroups = useMemo(
    () => (isAllComparisonsOpen ? comparisonGroups : sidebarComparisonGroups),
    [comparisonGroups, isAllComparisonsOpen, sidebarComparisonGroups],
  );

  useEffect(() => {
    let ignore = false;

    async function loadPreview() {
      if (!hasFilters || !exportState?.matchingBoxes.length || invalidPeriod) {
        setPreview(null);
        setPreviewError(null);
        setIsPreviewLoading(false);
        return;
      }

      setIsPreviewLoading(true);
      setPreviewError(null);

      try {
        const query = buildExportQuery(filters);
        const data = await apiGet<ExportPreview>(
          `/api/exports/measurements/preview/${query ? `?${query}` : ''}`,
        );
        if (!ignore) setPreview(data);
      } catch (previewLoadError) {
        if (!ignore) {
          setPreview(null);
          setPreviewError(
            previewLoadError instanceof Error ? previewLoadError.message : labels.previewError,
          );
        }
      } finally {
        if (!ignore) setIsPreviewLoading(false);
      }
    }

    void loadPreview();

    return () => {
      ignore = true;
    };
  }, [exportState?.matchingBoxes.length, filters, invalidPeriod, labels.previewError]);

  useEffect(() => {
    let ignore = false;

    async function loadComparisons() {
      if (!hasFilters || !requestedComparisonGroups.length || invalidPeriod) {
        setComparisonPreviews({});
        setIsComparisonLoading(false);
        return;
      }

      setIsComparisonLoading(true);
      try {
        const loadedPreviews = await Promise.all(
          requestedComparisonGroups.map(async (group) => {
            const query = buildExportQuery(buildComparisonFilters(filters, group));
            const preview = await apiGet<ExportPreview>(
              `/api/exports/measurements/preview/${query ? `?${query}` : ''}`,
            );
            return [group.key, preview] as const;
          }),
        );
        if (!ignore) setComparisonPreviews(Object.fromEntries(loadedPreviews));
      } catch {
        if (!ignore) setComparisonPreviews({});
      } finally {
        if (!ignore) setIsComparisonLoading(false);
      }
    }

    void loadComparisons();

    return () => {
      ignore = true;
    };
  }, [filters, invalidPeriod, requestedComparisonGroups]);

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

  const totalWeeks = preview?.points.length ?? 0;
  const effectiveWeeks = totalWeeks ? Math.min(weeksWindow, totalWeeks) : weeksWindow;
  const primaryPoints = preview ? preview.points.slice(-effectiveWeeks) : [];

  return (
    <section className="export-page">
      <section className="export-step">
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
            searchable
            onToggle={(id) => toggleFilter('boxes', id)}
            onClear={() => clearFilter('boxes')}
          />
        </div>
      </section>

      <section className="export-preview">
        <header className="export-step-heading">
          <h2>{labels.selectedMeasurementsTitle}</h2>
          {preview ? (
            <span>
              {preview.metadata.measurement_count} {labels.previewMeasurements}
            </span>
          ) : null}
        </header>

        {hasFilters ? (
          <>
        <div className="export-comparison-toolbar">
          <div className="export-chart-tabs">
            <div className="export-comparison-tabs" role="tablist" aria-label={labels.comparisonTitle}>
              <button
                className={comparisonMode === 'boxes' ? 'is-active' : ''}
                type="button"
                role="tab"
                aria-selected={comparisonMode === 'boxes'}
                onClick={() => setComparisonMode('boxes')}
              >
                {labels.comparisonByBoxes}
              </button>
              <button
                className={comparisonMode === 'strains' ? 'is-active' : ''}
                type="button"
                role="tab"
                aria-selected={comparisonMode === 'strains'}
                onClick={() => setComparisonMode('strains')}
              >
                {labels.comparisonByStrains}
              </button>
            </div>
            <div className="export-metric-tabs" role="tablist" aria-label={labels.selectedMeasurementsTitle}>
              {(['general', 'polyps', 'ephyrae', 'temperature'] as PreviewMetric[]).map((metric) => (
                <button
                  className={previewMetric === metric ? 'is-active' : ''}
                  key={metric}
                  type="button"
                  role="tab"
                  aria-selected={previewMetric === metric}
                  onClick={() => setPreviewMetric(metric)}
                >
                  {getPreviewMetricLabel(metric, language, labels)}
                </button>
              ))}
            </div>
          </div>
          {comparisonGroups.length > 3 ? (
            <button
              className="export-all-charts"
              type="button"
              onClick={() => setIsAllComparisonsOpen(true)}
            >
              {labels.allCharts} ({comparisonGroups.length})
            </button>
          ) : null}
        </div>

        <div className="export-chart-layout">
          <div className="export-chart-primary">
            <ExportPreviewChart
              labels={labels}
              language={language}
              isLoading={isPreviewLoading}
              error={previewError}
              metric={previewMetric}
              points={primaryPoints}
            />
            {totalWeeks > MIN_WEEKS_WINDOW ? (
              <div className="export-weeks-window">
                <label>
                  <span>{labels.weeksWindowLabel}</span>
                  <input
                    type="range"
                    min={MIN_WEEKS_WINDOW}
                    max={totalWeeks}
                    step={1}
                    value={effectiveWeeks}
                    onChange={(event) => setWeeksWindow(Number(event.target.value))}
                  />
                </label>
                <span className="export-weeks-window-value">
                  {effectiveWeeks >= totalWeeks
                    ? labels.allWeeksShort
                    : `${effectiveWeeks} ${labels.weeksShown}`}
                </span>
              </div>
            ) : null}
          </div>

          {sidebarComparisonGroups.length ? (
            <aside className="export-chart-comparisons" aria-label={labels.comparisonTitle}>
              {sidebarComparisonGroups.map((group) => (
                <article className="export-comparison-card" key={group.key}>
                  <header>
                    <strong>{group.label}</strong>
                    <span>{group.detail}</span>
                  </header>
                  <ExportPreviewChart
                    compact
                    labels={labels}
                    language={language}
                    isLoading={isComparisonLoading}
                    error={null}
                    metric={previewMetric}
                    points={(comparisonPreviews[group.key]?.points ?? []).slice(-effectiveWeeks)}
                  />
                </article>
              ))}
            </aside>
          ) : null}
        </div>
          </>
        ) : (
          <p className="export-chart-state export-charts-prompt">{labels.selectFilterPrompt}</p>
        )}
      </section>

      <section className="export-review">
        <div>
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

      {isAllComparisonsOpen ? (
        <ExportChartsModal
          groups={comparisonGroups}
          previews={comparisonPreviews}
          labels={labels}
          language={language}
          isLoading={isComparisonLoading}
          metric={previewMetric}
          weeksWindow={effectiveWeeks}
          onClose={() => setIsAllComparisonsOpen(false)}
        />
      ) : null}
    </section>
  );
}

function ExportPreviewChart({
  points,
  labels,
  language,
  isLoading,
  error,
  metric,
  compact = false,
}: {
  points: ExportPreviewPoint[];
  labels: (typeof copy)[Language];
  language: Language;
  isLoading: boolean;
  error: string | null;
  metric: PreviewMetric;
  compact?: boolean;
}) {
  const [hoveredIndex, setHoveredIndex] = useState<number | null>(null);

  if (isLoading) {
    return (
      <div className="export-chart-state export-chart-loading" aria-busy="true">
        <span className="sr-only">{labels.previewLoading}</span>
      </div>
    );
  }

  if (error) {
    return <div className="export-chart-state is-error">{error}</div>;
  }

  const isGeneral = metric === 'general';
  const drawnMetrics: PreviewMetric[] = isGeneral ? GENERAL_METRICS : [metric];

  const hasMeasurement = points.some((point) =>
    drawnMetrics.some((drawn) => !isMissingMeasurement(point, drawn)));
  if (!points.length || !hasMeasurement) {
    return <div className="export-chart-state">{labels.previewEmpty}</div>;
  }

  const width = compact ? 440 : 900;
  const height = compact ? 180 : 320;
  const padding = compact
    ? { top: 18, right: 20, bottom: 32, left: isGeneral ? 20 : 40 }
    : { top: 30, right: 34, bottom: 48, left: isGeneral ? 34 : 58 };
  const innerHeight = height - padding.top - padding.bottom;

  // Each metric keeps its own scale so trends stay readable side by side.
  const domains = {} as Record<PreviewMetric, [number, number]>;
  for (const drawn of drawnMetrics) {
    const values = points
      .map((point) => getPreviewMetricValue(point, drawn))
      .filter((value): value is number => value !== null);
    domains[drawn] = getChartDomain(values, drawn);
  }

  const lastMeasuredIndex = points.reduce(
    (lastIndex, point, index) =>
      (drawnMetrics.some((drawn) => !isMissingMeasurement(point, drawn)) ? index : lastIndex),
    -1,
  );
  const activeIndex = hoveredIndex ?? lastMeasuredIndex;
  const activePoint = points[activeIndex];
  const ariaLabel = isGeneral
    ? getPreviewMetricLabel('general', language, labels)
    : getPreviewMetricLabel(metric, language, labels);

  // D3 scales: x maps the week index to a pixel, y is per-metric (each series
  // keeps its own domain). The d3 line generator draws the path and breaks it
  // on missing weeks via .defined().
  const indices = points.map((_, index) => index);
  const xScale = scalePoint<number>()
    .domain(indices)
    .range([padding.left, width - padding.right]);
  const yScales = {} as Record<PreviewMetric, ScaleLinear<number, number>>;
  for (const drawn of drawnMetrics) {
    yScales[drawn] = scaleLinear()
      .domain(domains[drawn])
      .range([height - padding.bottom, padding.top]);
  }

  const xPosition = (index: number) => xScale(index) ?? padding.left;

  function linePath(drawn: PreviewMetric) {
    const yScale = yScales[drawn];
    const generator = line<number>()
      .defined((index) => getPreviewMetricValue(points[index], drawn) !== null)
      .x((index) => xPosition(index))
      .y((index) => yScale(getPreviewMetricValue(points[index], drawn) as number));
    return generator(indices) ?? '';
  }

  const dotRadius = compact ? 3.5 : 5;

  return (
    <div className={compact ? 'export-chart-card is-compact' : 'export-chart-card'}>
      <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label={ariaLabel}>
        <line
          className="export-chart-axis"
          x1={padding.left}
          x2={padding.left}
          y1={padding.top}
          y2={height - padding.bottom}
        />
        <line
          className="export-chart-axis"
          x1={padding.left}
          x2={width - padding.right}
          y1={height - padding.bottom}
          y2={height - padding.bottom}
        />
        {[0, 1 / 3, 2 / 3, 1].map((ratio) => {
          const y = padding.top + innerHeight - ratio * innerHeight;
          return (
            <g key={ratio}>
              <line
                className="export-chart-grid"
                x1={padding.left}
                x2={width - padding.right}
                y1={y}
                y2={y}
              />
              {/* Axis numbers only make sense for a single scale. */}
              {!isGeneral ? (
                <text className="export-chart-label" x={padding.left - 10} y={y + 4}>
                  {formatAxisValue(domains[metric][0] + (domains[metric][1] - domains[metric][0]) * ratio, metric)}
                </text>
              ) : null}
            </g>
          );
        })}
        {drawnMetrics.map((drawn) => (
          <path key={drawn} className={`export-chart-line is-${drawn}`} d={linePath(drawn)} />
        ))}
        {points.map((point, index) => {
          const x = xPosition(index);
          const allMissing = drawnMetrics.every((drawn) => isMissingMeasurement(point, drawn));
          return (
            <g
              key={point.label}
              className={index === activeIndex ? 'export-chart-hit is-active' : 'export-chart-hit'}
              tabIndex={0}
              onBlur={() => setHoveredIndex(null)}
              onFocus={() => setHoveredIndex(index)}
              onMouseEnter={() => setHoveredIndex(index)}
              onMouseLeave={() => setHoveredIndex(null)}
            >
              <rect
                x={x - 18}
                y={padding.top}
                width={36}
                height={innerHeight}
                rx={8}
              />
              {allMissing ? (
                <g className="export-chart-missing" aria-hidden="true">
                  <line x1={x - 5} x2={x + 5} y1={height - padding.bottom - 8} y2={height - padding.bottom - 8} />
                  <circle cx={x} cy={height - padding.bottom - 8} r={3.5} />
                </g>
              ) : (
                drawnMetrics.map((drawn) => {
                  const value = getPreviewMetricValue(point, drawn);
                  if (value === null) return null;
                  return (
                    <circle
                      key={drawn}
                      className={`export-chart-dot is-${drawn}`}
                      cx={x}
                      cy={yScales[drawn](value)}
                      r={dotRadius}
                    />
                  );
                })
              )}
              {shouldShowWeekLabel(index, points.length, compact) ? (
                <text className="export-chart-week" x={x} y={height - 16}>
                  {formatWeekLabel(point.label)}
                </text>
              ) : null}
              <title>{buildPointTooltip(point, drawnMetrics, language, labels)}</title>
            </g>
          );
        })}
      </svg>

      <div className="export-chart-details">
        <strong>{formatWeekDetail(activePoint.label, language)}</strong>
        {drawnMetrics.every((drawn) => isMissingMeasurement(activePoint, drawn)) ? (
          <span className="is-missing">{getNoMeasurementLabel(language)}</span>
        ) : (
          <>
            {drawnMetrics.map((drawn) => {
              const value = getPreviewMetricValue(activePoint, drawn);
              return (
                <span key={drawn} className={`is-${drawn}`}>
                  {getPreviewMetricLabel(drawn, language, labels)} : {value === null
                    ? getNoMeasurementLabel(language)
                    : formatPreviewMetricValue(value, drawn)}
                </span>
              );
            })}
            {!isGeneral ? (
              <span>{activePoint.measurement_count} {labels.previewMeasurements}</span>
            ) : null}
          </>
        )}
      </div>

      <div className="chart-legend export-chart-legend">
        {drawnMetrics.map((drawn) => (
          <span key={drawn} className={`is-${drawn}`}>
            {getPreviewMetricLabel(drawn, language, labels)}
          </span>
        ))}
        <span className="is-missing">{getNoMeasurementLabel(language)}</span>
      </div>
    </div>
  );
}

function buildPointTooltip(
  point: ExportPreviewPoint,
  drawnMetrics: PreviewMetric[],
  language: Language,
  labels: (typeof copy)[Language],
) {
  const week = formatWeekDetail(point.label, language);
  const parts = drawnMetrics
    .map((drawn) => {
      const value = getPreviewMetricValue(point, drawn);
      if (value === null) return null;
      return `${getPreviewMetricLabel(drawn, language, labels)}: ${formatPreviewMetricValue(value, drawn)}`;
    })
    .filter((part): part is string => part !== null);
  if (!parts.length) return `${week} · ${getNoMeasurementLabel(language)}`;
  return `${week} · ${parts.join(' · ')}`;
}

function getPreviewMetricValue(point: ExportPreviewPoint | undefined, metric: PreviewMetric) {
  if (!point) return null;
  if (metric === 'temperature') return point.average_temperature_c;
  return metric === 'polyps' ? point.polyp_count : point.ephyrae_count;
}

function isMissingMeasurement(point: ExportPreviewPoint | undefined, metric: PreviewMetric) {
  if (!point) return true;
  if (metric === 'temperature') return point.average_temperature_c === null;
  return point.measurement_count === 0;
}

function getChartDomain(values: number[], metric: PreviewMetric): [number, number] {
  const minimum = Math.min(...values);
  const maximum = Math.max(...values);

  if (metric !== 'temperature') {
    return [0, Math.max(1, maximum * 1.08)];
  }

  const padding = Math.max(0.25, (maximum - minimum) * 0.16);
  return [minimum - padding, maximum + padding];
}

function formatAxisValue(value: number, metric: PreviewMetric) {
  return metric === 'temperature' ? `${value.toFixed(1)}°` : String(Math.round(value));
}

function formatPreviewMetricValue(value: number | null, metric: PreviewMetric) {
  if (value === null) return '-';
  return metric === 'temperature' ? `${value.toFixed(1)}°C` : String(value);
}

function getPreviewMetricLabel(
  metric: PreviewMetric,
  language: Language,
  labels: (typeof copy)[Language],
) {
  if (metric === 'general') return language === 'fr' ? 'Général' : 'General';
  if (metric === 'polyps') return language === 'fr' ? 'Polypes' : 'Polyps';
  if (metric === 'ephyrae') return language === 'fr' ? 'Éphyrules' : 'Ephyrae';
  return labels.temperature;
}

function getNoMeasurementLabel(language: Language) {
  return language === 'fr' ? 'Aucun relevé' : 'No measurement';
}

function shouldShowWeekLabel(index: number, total: number, compact: boolean) {
  if (total <= (compact ? 3 : 7)) return true;
  const targetLabelCount = compact ? 3 : 6;
  const interval = Math.ceil((total - 1) / (targetLabelCount - 1));
  return index === 0 || index === total - 1 || index % interval === 0;
}

function formatWeekDetail(label: string, language: Language) {
  const match = label.match(/^(\d{4})_S(\d{1,2})$/);
  if (!match) return label;
  return language === 'fr'
    ? `Semaine ${match[2]} · ${match[1]}`
    : `Week ${match[2]} · ${match[1]}`;
}

function ExportChartsModal({
  groups,
  previews,
  labels,
  language,
  isLoading,
  metric,
  weeksWindow,
  onClose,
}: {
  groups: ComparisonGroup[];
  previews: Record<string, ExportPreview>;
  labels: (typeof copy)[Language];
  language: Language;
  isLoading: boolean;
  metric: PreviewMetric;
  weeksWindow: number;
  onClose: () => void;
}) {
  return (
    <div className="modal-backdrop export-charts-backdrop" onMouseDown={onClose}>
      <section
        className="export-charts-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="export-charts-title"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <header>
          <h2 id="export-charts-title">{labels.comparisonTitle}</h2>
          <button type="button" onClick={onClose}>
            {labels.close}
          </button>
        </header>
        <div className="export-charts-modal-grid">
          {groups.map((group) => (
            <article className="export-comparison-card" key={group.key}>
              <header>
                <strong>{group.label}</strong>
                <span>{group.detail}</span>
              </header>
              <ExportPreviewChart
                compact
                labels={labels}
                language={language}
                isLoading={isLoading}
                error={null}
                metric={metric}
                points={(previews[group.key]?.points ?? []).slice(-weeksWindow)}
              />
            </article>
          ))}
        </div>
      </section>
    </div>
  );
}

function FilterDisclosure({
  title,
  options,
  selectedIds,
  labels,
  searchable = false,
  onToggle,
  onClear,
}: {
  title: string;
  options: FilterOption[];
  selectedIds: number[];
  labels: (typeof copy)[Language];
  searchable?: boolean;
  onToggle: (id: number) => void;
  onClear: () => void;
}) {
  const [query, setQuery] = useState('');
  const selectedOptions = options.filter((option) => selectedIds.includes(option.id));
  const normalizedQuery = query.trim().toLocaleLowerCase();
  const visibleOptions = normalizedQuery
    ? options.filter((option) => [option.label, option.detail]
      .filter(Boolean)
      .some((value) => value!.toLocaleLowerCase().includes(normalizedQuery)))
    : options;
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
            {visibleOptions.length} {labels.optionCount}
          </span>
          {selectedIds.length ? (
            <button type="button" onClick={onClear}>
              {labels.clear}
            </button>
          ) : null}
        </div>
        {searchable ? (
          <label className="export-box-search">
            <span>{labels.searchBoxes}</span>
            <input
              type="search"
              value={query}
              placeholder={labels.searchBoxesPlaceholder}
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

function buildExportQuery(filters: ExportFilters) {
  const params = new URLSearchParams();
  for (const key of ['organizations', 'species', 'strains', 'boxes', 'zones'] as FilterKey[]) {
    if (filters[key].length) params.set(key, filters[key].join(','));
  }
  if (filters.dateFrom) params.set('date_from', filters.dateFrom);
  if (filters.dateTo) params.set('date_to', filters.dateTo);
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

function buildComparisonGroups(
  boxes: ExportOptions['boxes'],
  options: ExportOptions,
  mode: ComparisonMode,
): ComparisonGroup[] {
  if (mode === 'boxes') {
    return boxes.map((box) => {
      const strain = options.strains.find((candidate) => candidate.id === box.strain_id);
      return {
        id: box.id,
        key: `box-${box.id}`,
        label: box.global_code,
        detail: strain ? `${strain.species_name} · ${strain.code}` : box.local_code || '',
        mode,
      };
    });
  }

  return Array.from(new Set(boxes.map((box) => box.strain_id)))
    .filter((strainId): strainId is number => strainId !== null)
    .map((strainId) => {
      const strain = options.strains.find((candidate) => candidate.id === strainId);
      return {
        id: strainId,
        key: `strain-${strainId}`,
        label: strain?.code || String(strainId),
        detail: strain?.species_name || '',
        mode,
      };
    });
}

function buildComparisonFilters(filters: ExportFilters, group: ComparisonGroup): ExportFilters {
  return {
    ...filters,
    boxes: group.mode === 'boxes' ? [group.id] : filters.boxes,
    strains: group.mode === 'strains' ? [group.id] : filters.strains,
  };
}

function getOrganizationName(options: ExportOptions, organizationId: number) {
  return options.organizations.find((organization) => organization.id === organizationId)?.name;
}

function formatWeekLabel(label: string) {
  const match = label.match(/^\d{4}_S(\d{1,2})$/);
  return match ? `S${match[1]}` : label;
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
