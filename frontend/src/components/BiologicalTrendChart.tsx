import {
  useEffect,
  useId,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent,
  type MouseEvent,
  type ReactNode,
} from 'react';
import { scaleLinear, scaleTime } from 'd3-scale';
import { line } from 'd3-shape';

import { formatDisplayDate } from '../utils/dateFormat';
import { formatReadableUserIdentity, type ReadableUserIdentity } from '../utils/userIdentity';
import {
  chartBiologicalValues,
  dismissPinnedChartDetail,
  placeChartDetail,
  resolveChartDetail,
  selectedVisibleReading,
  splitMeasurementsOnGaps,
  toggleChartSeries,
} from '../utils/chartBiology';
import {
  findChartLocationAtDate,
  findSourceChartLocationAtDate,
  resolveChartLocationPeriods,
  type ChartLocation,
} from '../utils/chartLocations';

export type TrendMeasurement = {
  id: number | string;
  date: string;
  timelineOrder?: number;
  polypCount: number;
  ephyraeCount: number;
  salinity?: string | number | null;
  enteredBy?: string | null;
  user_identity?: ReadableUserIdentity | null;
  note?: string | null;
  environmentLocations?: readonly ChartLocation[] | null;
};

export type TrendPolypState = {
  id: number | string;
  kind: 'subculture' | 'subculture_initialization';
  date: string;
  timelineOrder?: number;
  polypCount: number;
  ephyraeCount?: null;
  title: string;
  detailLines?: Array<{ label: string; value: string }>;
  enteredBy?: string | null;
  user_identity?: ReadableUserIdentity | null;
  note?: string | null;
};

export type TrendTemperature = {
  date: string;
  average_temperature_c: number;
  zone_id?: number;
  zone_name?: string;
  // Week-only observation: `date` positions the marker and is never displayed as a day.
  week?: { id: number; year: number; number: number; start: string; end: string; valueText: string };
};
export type TrendPoint = TrendMeasurement | TrendPolypState;
export type TrendLocation = ChartLocation;

export function getTrendPointId(point: TrendPoint) {
  return 'kind' in point ? `biological-${point.kind}-${point.id}` : `measurement-${point.id}`;
}

export type TrendEvent = {
  id: number | string;
  date: string;
  title: string;
  detail?: string;
  detailLines?: Array<{ label: string; value: string }>;
  // 'movement' is a persisted BoxMovement; 'location_change' is a derived location period boundary.
  kind?: 'movement' | 'subculture' | 'location_change';
  // Full timestamp, used for exact coincidence checks and event identification.
  instant?: string;
};

type TrendLabels = {
  chartTitle: string;
  closeDetail?: string;
  empty: string;
  ephyrae: string;
  enteredBy?: string;
  historicalUser?: string;
  location: string;
  sourceLocation?: string;
  locationChange: string;
  locationChangeNote?: string;
  missingReading: string;
  movement: string;
  movementAtBoundaryNote?: string;
  observation?: string;
  polyps: string;
  salinity?: string;
  temperature?: string;
  temperatureOutsideScale?: string;
  historicalTemperature?: string;
  historicalWeek?: string;
  historicalPeriod?: string;
  historicalNote?: string;
  selectReading?: string;
  selectedReading?: string;
  subculture?: string;
};

type ActiveDetail = {
  date?: string;
  id: string;
  left: number;

  top: number;
  title: string;
  // Screen readers still get the exact event time; it is not shown in the visible tooltip.
  accessibleTitle?: string;
  lines: DetailLine[];
};

type DetailLine = {
  kind: 'ephyrae' | 'location' | 'note' | 'polyps' | 'salinity' | 'user' | 'allocation' | 'temperature';
  label: string;
  value: string;
};

type LocationBand = {
  endDate: string;
  startsAt: string;
  id: number | string;
  name: string;
  startDate: string;
  width: number;
  x1: number;
};

type TimeTick = {
  date: string;
  label: string;
  x: number;
};

const BIOLOGICAL_COUNT_AXIS_MAX = 1000;
const VALUE_AXIS_TICK_COUNT = 4;
export const BIOLOGICAL_TEMPERATURE_DOMAIN = [0, 40] as const;
export const BIOLOGICAL_TEMPERATURE_TICKS = [0, 5, 10, 15, 20, 25, 30, 35, 40] as const;

function temperaturePointId(point: TrendTemperature) {
  if (point.week) return `temperature-week-${point.week.id}`;
  return `temperature-${point.zone_id ?? point.zone_name ?? ''}-${point.date}`;
}

function isTemperatureInScale(point: TrendTemperature) {
  return point.average_temperature_c >= BIOLOGICAL_TEMPERATURE_DOMAIN[0]
    && point.average_temperature_c <= BIOLOGICAL_TEMPERATURE_DOMAIN[1];
}

export default function BiologicalTrendChart({
  compact = false,
  endDate,
  events = [],
  labels,
  locations = [],
  measurements,
  polypStates = [],
  selectionScope,
  startDate,
  temperatures = [],
  temperatureStatus,
}: {
  compact?: boolean;
  endDate: string;
  events?: TrendEvent[];
  labels: TrendLabels;
  locations?: TrendLocation[];
  measurements: TrendMeasurement[];
  polypStates?: TrendPolypState[];
  selectionScope?: number | string;
  startDate: string;
  temperatures?: TrendTemperature[];
  temperatureStatus?: ReactNode;
}) {
  const [pinnedDetail, setPinnedDetail] = useState<ActiveDetail | null>(null);
  const [hoveredDetail, setHoveredDetail] = useState<ActiveDetail | null>(null);
  const [focusedDetail, setFocusedDetail] = useState<ActiveDetail | null>(null);
  const chartId = useId();
  const canvasRef = useRef<HTMLDivElement>(null);
  const svgRef = useRef<SVGSVGElement>(null);
  const tooltipRef = useRef<HTMLDivElement>(null);
  const [visibleSeries, setVisibleSeries] = useState({ polyps: true, ephyrae: true });
  const [temperatureVisible, setTemperatureVisible] = useState(true);
  const [historicalVisible, setHistoricalVisible] = useState(true);
  const [canvasSize, setCanvasSize] = useState({ width: 0, responsive: false });
  useLayoutEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const media = window.matchMedia('(max-width: 1023px), (max-width: 1180px) and (pointer: coarse)');
    function updateSize(width: number) {
      setCanvasSize((current) => current.width === width && current.responsive === media.matches
        ? current : { width, responsive: media.matches });
    }
    function measure() {
      if (!canvas) return;
      const style = getComputedStyle(canvas);
      updateSize(canvas.clientWidth - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight));
    }
    measure();
    const observer = new ResizeObserver(([entry]) => updateSize(entry.contentRect.width));
    observer.observe(canvas);
    media.addEventListener('change', measure);
    return () => {
      observer.disconnect();
      media.removeEventListener('change', measure);
    };
  }, []);
  const temperatureEnabled = labels.temperature != null;
  const layout = resolveChartLayout(compact, canvasSize.width, canvasSize.responsive);
  const geometry = useMemo(
    () => buildGeometry(measurements, locations, events, startDate, endDate, compact, layout, polypStates, temperatures, temperatureEnabled),
    [compact, endDate, events, locations, measurements, polypStates, temperatures, temperatureEnabled, startDate, layout.width, layout.responsive],
  );

  useEffect(() => {
    setPinnedDetail(null);
    setHoveredDetail(null);
    setFocusedDetail(null);
    setVisibleSeries({ polyps: true, ephyrae: true });
    setTemperatureVisible(true);
    setHistoricalVisible(true);
  }, [selectionScope]);

  useEffect(() => {
    setPinnedDetail((current) => current
      && ((current.id.startsWith('temperature-') && temperatures.some((point) => (
        temperaturePointId(point) === current.id && point.date >= startDate && point.date <= endDate
      ))) || [...measurements, ...polypStates].some((measurement) => (
        getTrendPointId(measurement) === current.id
        && measurement.date >= startDate && measurement.date <= endDate
      ))) ? current : null);
    setHoveredDetail(null);
    setFocusedDetail(null);
  }, [startDate, endDate]);

  function toggleDetail(detail: ActiveDetail) {
    setPinnedDetail((current) => current?.id === detail.id ? null : detail);
  }

  function dismissPinnedDetail(event: KeyboardEvent<SVGGElement>) {
    const result = dismissPinnedChartDetail(event.key, pinnedDetail);
    if (!result.handled) return false;
    event.preventDefault();
    setPinnedDetail(result.pinned);
    return true;
  }

  function handleDetailKey(event: KeyboardEvent<SVGGElement>, detail: ActiveDetail) {
    if (dismissPinnedDetail(event)) return;
    if (event.key !== 'Enter' && event.key !== ' ') return;
    event.preventDefault();
    toggleDetail(detail);
  }

  function stopAndToggle(event: MouseEvent<SVGGElement>, detail: ActiveDetail) {
    event.stopPropagation();
    toggleDetail(detail);
  }

  function selectMeasurement(event: MouseEvent<SVGGElement>, detail: ActiveDetail) {
    event.stopPropagation();
    setPinnedDetail(detail);
  }

  const {
    countHeight,
    countLine,
    countTicks,
    chartAreaHeight,
    end,
    eventPoints,
    maxCount,
    measurementSegments,
    polypSegments,
    padding,
    plotHeight,
    plotTop,
    plottedMeasurements,
    start,
    timeTicks,
    width,
    xPosition,
    yCount,
    plottedTemperatures,
    temperatureLine,
    temperatureTicks,
    yTemperature,
  } = geometry;
  const eventDetails = eventPoints.filter(({ event }) => event.kind !== 'location_change').map(({ event, x }) => {
    const isDerived = event.kind === 'location_change';
    const eventTitle = isDerived ? labels.locationChange : event.title || labels.movement;
    // The movement tooltip stays light: title and location only, no exact time or boundary note.
    const detail: ActiveDetail = {
      id: `event-${event.id}`,
      left: (x / width) * 100,
      top: plotTop + 6,
      title: eventTitle,
      accessibleTitle: `${eventTitle} - ${formatEventDate(event)}`,
      lines: event.detailLines?.map((item) => ({ ...item, kind: 'allocation' as const })) ?? (event.detail
        ? [{ kind: 'location' as const, label: labels.location, value: event.detail }]
        : []),
    };
    return { event, x, detail, isDerived, eventTitle };
  });
  // Equivalent HTML controls avoid SVG paint-order collisions without changing chart coordinates.
  const eventCountByX = new Map<number, number>();
  for (const { x } of eventDetails) eventCountByX.set(x, (eventCountByX.get(x) ?? 0) + 1);
  const overlappingEvents = eventDetails.filter(({ x }) => eventCountByX.get(x)! > 1);
  const visiblePoints = plottedMeasurements.filter((point) => visibleSeries.polyps || !('kind' in point));
  const pointHitAreas = buildMeasurementHitAreas(visiblePoints.map((point) => xPosition(point.date)), padding.left, width - padding.right, layout.responsive);
  const plottedReadingDetails = visiblePoints.map((measurement) => {
    const x = xPosition(measurement.date);
    const topY = Math.min(
      visibleSeries.polyps ? yCount(measurement.polypCount) : Infinity,
      visibleSeries.ephyrae && measurement.ephyraeCount != null ? yCount(measurement.ephyraeCount) : Infinity,
    );
    const detail: ActiveDetail = {
      id: getTrendPointId(measurement),
      date: measurement.date,
      left: (x / width) * 100,
      top: Number.isFinite(topY) ? topY : yCount(measurement.polypCount),
      title: 'kind' in measurement ? `${measurement.title} - ${formatDisplayDate(measurement.date)}` : formatDisplayDate(measurement.date),
      lines: buildMeasurementDetailLines(measurement, labels, locations),
    };
    return { detail, measurement, x };
  });
  const allTemperatureDetails = plottedTemperatures.map((point) => {
    const x = xPosition(point.date);
    const outsideScale = !isTemperatureInScale(point);
    // Off-scale readings use directional markers, never a dot at a false measurement.
    const y = outsideScale
      ? point.average_temperature_c < 0 ? countHeight - padding.bottom : plotTop
      : yTemperature(point.average_temperature_c);
    const detail: ActiveDetail = {
      id: temperaturePointId(point), date: point.date, left: (x / width) * 100, top: y,
      title: point.week ? `${labels.historicalWeek ?? ''} ${point.week.number}, ${point.week.year}`.trim() : formatDisplayDate(point.date),
      lines: [
        { kind: 'temperature', label: `${point.week ? labels.historicalTemperature ?? labels.temperature : labels.temperature} (°C)`, value: point.week?.valueText ?? String(point.average_temperature_c) },
        ...(point.week ? [
          { kind: 'note' as const, label: labels.historicalPeriod ?? '', value: `${formatDisplayDate(point.week.start)} - ${formatDisplayDate(point.week.end)}` },
          ...(labels.historicalNote ? [{ kind: 'note' as const, label: labels.historicalTemperature ?? labels.temperature!, value: labels.historicalNote }] : []),
        ] : []),
        ...(outsideScale && labels.temperatureOutsideScale ? [{ kind: 'note' as const, label: labels.temperature!, value: labels.temperatureOutsideScale }] : []),
        ...(point.zone_name != null ? [{ kind: 'location' as const, label: labels.location, value: point.zone_name }] : []),
      ],
    };
    return { point, detail, x, y, outsideScale };
  });
  const temperatureDetails = allTemperatureDetails.filter(({ point }) => point.week ? historicalVisible : temperatureVisible);
  const hasWeeklyTemperatures = allTemperatureDetails.some(({ point }) => point.week);
  const temperatureCountByPosition = new Map<string, number>();
  for (const { x, y } of temperatureDetails) {
    const key = `${x}:${y}`;
    temperatureCountByPosition.set(key, (temperatureCountByPosition.get(key) ?? 0) + 1);
  }
  const overlappingTemperatures = temperatureDetails.filter(({ x, y }) => temperatureCountByPosition.get(`${x}:${y}`)! > 1);
  const selectedDetail = selectedVisibleReading(plottedReadingDetails.map(({ detail }) => detail), pinnedDetail?.id ?? null);
  const selectedReading = plottedReadingDetails.find(({ detail }) => detail.id === selectedDetail?.id) ?? null;
  const pinnedVisibleDetail = pinnedDetail?.id.startsWith('temperature-')
    ? temperatureDetails.find(({ detail }) => detail.id === pinnedDetail.id)?.detail ?? null
    : pinnedDetail && (pinnedDetail.id.startsWith('measurement-') || pinnedDetail.id.startsWith('biological-')) ? selectedDetail : pinnedDetail;
  const liveDetail = (detail: ActiveDetail | null) => detail == null ? null
    : [...plottedReadingDetails, ...eventDetails, ...(temperatureEnabled ? temperatureDetails : [])]
      .find((item) => item.detail.id === detail.id)?.detail ?? null;
  const visibleDetail = resolveChartDetail(liveDetail(pinnedVisibleDetail), liveDetail(focusedDetail), liveDetail(hoveredDetail));
  const selectedTemperature = temperatureEnabled ? temperatureDetails.find(({ detail }) => detail.id === pinnedVisibleDetail?.id) : null;
  const selectionX = selectedTemperature?.x ?? selectedReading?.x;
  const hasTemperatureOverflow = temperatureDetails.some(({ outsideScale }) => outsideScale);
  const hasOverflow = plottedMeasurements.some((measurement) => (
    (visibleSeries.polyps && measurement.polypCount > maxCount)
    || (visibleSeries.ephyrae && measurement.ephyraeCount != null && measurement.ephyraeCount > maxCount)
  ));
  function handleMeasurementKey(
    event: KeyboardEvent<SVGGElement>,
    detail: ActiveDetail,
    measurementIndex: number,
    series: 'measurement' | 'temperature' = 'measurement',
  ) {
    const details = series === 'temperature' ? temperatureDetails : plottedReadingDetails;
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      setPinnedDetail(detail);
      return;
    }
    if (dismissPinnedDetail(event)) return;

    const targetIndex = event.key === 'ArrowLeft'
      ? measurementIndex - 1
      : event.key === 'ArrowRight'
        ? measurementIndex + 1
        : event.key === 'Home'
          ? 0
          : event.key === 'End'
            ? details.length - 1
            : null;
    if (targetIndex == null) return;

    event.preventDefault();
    const nextIndex = Math.max(0, Math.min(details.length - 1, targetIndex));
    const nextReading = details[nextIndex];
    if (!nextReading) return;
    event.currentTarget.ownerSVGElement
      ?.querySelector<SVGGElement>(`[data-${series}-index="${nextIndex}"]`)
      ?.focus();
  }

  useLayoutEffect(() => {
    const canvas = canvasRef.current;
    const svg = svgRef.current;
    const panel = tooltipRef.current;
    if (!visibleDetail || !canvas || !svg || !panel) return;

    function positionDetail() {
      if (!canvas || !svg || !panel || !visibleDetail) return;
      const matrix = svg.getScreenCTM();
      const offsetParent = panel.offsetParent;
      if (!matrix || !offsetParent) return;
      const bounds = canvas.getBoundingClientRect();
      const point = new DOMPoint(visibleDetail.left / 100 * width, visibleDetail.top).matrixTransform(matrix);
      const anchor = { x: point.x - bounds.left, y: point.y - bounds.top };
      if (anchor.x < 0 || anchor.x > bounds.width || anchor.y < 0 || anchor.y > bounds.height) {
        panel.style.visibility = 'hidden';
        return;
      }
      panel.style.maxWidth = `${Math.max(0, bounds.width - 16)}px`;
      const naturalHeight = Math.max(panel.scrollHeight + panel.offsetHeight - panel.clientHeight, panel.offsetHeight);
      const position = placeChartDetail(anchor, bounds, { width: panel.offsetWidth, height: naturalHeight });
      panel.style.maxHeight = `${position.maxHeight}px`;
      const parentBounds = offsetParent.getBoundingClientRect();
      panel.style.left = `${bounds.left - parentBounds.left + position.left}px`;
      panel.style.top = `${bounds.top - parentBounds.top + position.top}px`;
      panel.style.visibility = 'visible';
    }

    positionDetail();
    const observer = new ResizeObserver(positionDetail);
    observer.observe(canvas);
    observer.observe(svg);
    observer.observe(panel);
    canvas.addEventListener('scroll', positionDetail, { passive: true });
    return () => {
      observer.disconnect();
      canvas.removeEventListener('scroll', positionDetail);
    };
  });

  return (
    <div className={`bio-trend${compact ? ' is-compact' : ''}${layout.responsive ? ' is-responsive' : ''}`}>
      <div ref={canvasRef} className="bio-trend-canvas">
        <svg
          ref={svgRef}
          className="bio-trend-svg"
          viewBox={`0 0 ${width} ${countHeight}`}
          role="group"
          aria-label={labels.chartTitle}
          onClick={() => setPinnedDetail(null)}
        >
          {temperatureEnabled ? (
            <g className="bio-trend-temperature-axis" aria-label={`${labels.temperature} (°C)`}>
              <line className="bio-trend-axis" x1={width - padding.right} x2={width - padding.right} y1={plotTop} y2={countHeight - padding.bottom} />
              {temperatureTicks.map((value) => (
                <text className="bio-trend-value-label" key={value} x={width - padding.right + 8} y={yTemperature(value) + 4}>{value} °C</text>
              ))}
            </g>
          ) : null}

          {timeTicks.map((tick) => (
            <g key={tick.date} className="bio-trend-time-tick">
              <line
                x1={tick.x}
                x2={tick.x}
                y1={plotTop}
                y2={countHeight - padding.bottom}
              />
              <text
                x={tick.x}
                y={countHeight - 9}
                textAnchor="middle"
              >
                {tick.label}
              </text>
            </g>
          ))}

          {countTicks.filter((value) => value > 0 && value < maxCount).map((value) => (
            <line key={value} className="bio-trend-grid" x1={padding.left} y1={yCount(value)} x2={width - padding.right} y2={yCount(value)} />
          ))}
          <line className="bio-trend-axis" x1={padding.left} y1={countHeight - padding.bottom} x2={width - padding.right} y2={countHeight - padding.bottom} />
          <line className="bio-trend-axis" x1={padding.left} y1={plotTop} x2={padding.left} y2={countHeight - padding.bottom} />

          {selectionX != null ? (
            <line
              className="bio-trend-selection-guide"
              x1={selectionX}
              x2={selectionX}
              y1={plotTop}
              y2={countHeight - padding.bottom}
            />
          ) : null}

          {visibleSeries.polyps && polypSegments.map((segment, index) => (
            <path key={`polyps-${index}`} className="bio-trend-line is-polyps" d={countLine((point) => point.polypCount)(segment) ?? ''} />
          ))}
          {visibleSeries.ephyrae && measurementSegments.map((segment, index) => (
            <path key={`ephyrae-${index}`} className="bio-trend-line is-ephyrae" d={countLine((point) => point.ephyraeCount)(segment) ?? ''} />
          ))}

          {temperatureEnabled && temperatureVisible && plottedTemperatures.some((point) => !point.week) ? (
            <path className="bio-trend-line is-temperature" d={temperatureLine(plottedTemperatures.filter((point) => !point.week)) ?? ''} />
          ) : null}

          {eventDetails.map(({ event, x, detail, isDerived, eventTitle }) => {
            const isSubculture = event.kind === 'subculture';
            const overlaps = overlappingEvents.some((item) => item.detail.id === detail.id);
            return (
              <g
                key={event.id}
                className={`bio-trend-event is-${isDerived ? 'location-change' : event.kind ?? 'movement'}`}
                role={overlaps ? undefined : 'button'}
                tabIndex={overlaps ? undefined : 0}
                aria-hidden={overlaps || undefined}
                data-overlapping={overlaps || undefined}
                aria-label={overlaps ? undefined : detail.accessibleTitle ?? detail.title}
                transform={`translate(${x} ${padding.top})`}
                onClick={(clickEvent) => stopAndToggle(clickEvent, detail)}
                onKeyDown={(keyEvent) => handleDetailKey(keyEvent, detail)}
                onMouseEnter={() => setHoveredDetail(detail)}
                onMouseLeave={() => setHoveredDetail(null)}
                onFocus={() => setFocusedDetail(detail)}
                onBlur={() => setFocusedDetail(null)}
              >
                <line
                  className="bio-trend-event-hit-area"
                  x1={0}
                  y1={isSubculture ? plotTop - padding.top : 0}
                  x2={0}
                  y2={chartAreaHeight}
                />
                <line
                  className="bio-trend-event-line"
                  x1={0}
                  y1={isSubculture ? plotTop - padding.top : 0}
                  x2={0}
                  y2={chartAreaHeight}
                />
                {isSubculture ? <path d="M0 0 L6 6 L0 12 L-6 6 Z" /> : null}
                {!isSubculture && !overlaps ? (
                  <text
                    className="bio-trend-event-label"
                    x={-chartAreaHeight / 2}
                    y={-8}
                    textAnchor="middle"
                    transform="rotate(-90)"
                  >
                    {eventTitle.toLocaleUpperCase('fr-FR')}
                  </text>
                ) : null}
              </g>
            );
          })}

          {plottedReadingDetails.map(({ detail, measurement, x }, measurementIndex) => {
            return (
              <g
                key={getTrendPointId(measurement)}
                className={`bio-trend-measurement${'kind' in measurement ? ' is-subculture' : ''}${selectedReading?.detail.id === detail.id ? ' is-selected' : ''}`}
                data-measurement-index={measurementIndex}
                role="button"
                tabIndex={0}
                aria-pressed={selectedReading?.detail.id === detail.id}
                aria-label={`${detail.title}, ${detail.lines.map((item) => `${item.label}: ${item.value}`).join(', ')}`}
                onClick={(clickEvent) => selectMeasurement(clickEvent, detail)}
                onMouseEnter={() => setHoveredDetail(detail)}
                onMouseLeave={() => setHoveredDetail(null)}
                onFocus={() => setFocusedDetail(detail)}
                onBlur={() => setFocusedDetail(null)}
                onKeyDown={(keyEvent) => handleMeasurementKey(keyEvent, detail, measurementIndex)}
              >
                {visibleSeries.polyps && 'kind' in measurement ? <rect className="bio-trend-dot is-polyps is-subculture" x={x - 4} y={yCount(measurement.polypCount) - 4} width={8} height={8} /> : visibleSeries.polyps ? <circle className={`bio-trend-dot is-polyps${visibleSeries.ephyrae && measurement.polypCount === measurement.ephyraeCount ? ' is-overlapping' : ''}`} cx={x} cy={yCount(measurement.polypCount)} r={measurement.polypCount === 0 ? layout.zeroRadius : layout.pointRadius} /> : null}
                {visibleSeries.ephyrae && measurement.ephyraeCount != null && layout.responsive ? (
                  <path className="bio-trend-dot is-ephyrae" d={buildDiamondPath(x, yCount(measurement.ephyraeCount), measurement.ephyraeCount === 0 ? layout.zeroRadius : layout.pointRadius)} />
                ) : visibleSeries.ephyrae && measurement.ephyraeCount != null ? <circle className={`bio-trend-dot is-ephyrae${measurement.ephyraeCount === 0 ? ' is-zero' : ''}${visibleSeries.polyps && measurement.polypCount === measurement.ephyraeCount ? ' is-overlapping' : ''}`} cx={x} cy={yCount(measurement.ephyraeCount)} r={measurement.ephyraeCount === 0 ? 2.8 : 2.15} /> : null}
                {visibleSeries.polyps && measurement.polypCount > maxCount ? (
                  <path
                    className="bio-trend-overflow is-polyps"
                    d={`M${x - 4} ${plotTop + 8} L${x} ${plotTop + 1} L${x + 4} ${plotTop + 8} Z`}
                  />
                ) : null}
                {visibleSeries.ephyrae && measurement.ephyraeCount != null && measurement.ephyraeCount > maxCount ? (
                  <path
                    className="bio-trend-overflow is-ephyrae"
                    d={`M${x - 4} ${plotTop + 14} L${x} ${plotTop + 7} L${x + 4} ${plotTop + 14} Z`}
                  />
                ) : null}
                <rect className="bio-trend-hit-area" x={pointHitAreas[measurementIndex].left} y={plotTop} width={pointHitAreas[measurementIndex].width} height={plotHeight} />
              </g>
            );
          })}

          {temperatureEnabled && temperatureDetails.map(({ point, detail, x, y, outsideScale }, index) => (
            <g key={detail.id} className={`bio-trend-temperature-point${selectedTemperature?.detail.id === detail.id ? ' is-selected' : ''}`} role="button" tabIndex={0}
              data-temperature-index={index}
              aria-pressed={selectedTemperature?.detail.id === detail.id}
              aria-describedby={visibleDetail?.id === detail.id ? `${chartId}-event-detail` : undefined}
              aria-label={`${detail.title}, ${point.week ? labels.historicalTemperature : labels.temperature}: ${point.week?.valueText ?? point.average_temperature_c} °C${point.zone_name != null ? `, ${labels.location}: ${point.zone_name}` : ''}${outsideScale ? `, ${labels.temperatureOutsideScale ?? ''}` : ''}`}
              onMouseEnter={() => setHoveredDetail(detail)} onMouseLeave={() => setHoveredDetail(null)}
              onFocus={() => setFocusedDetail(detail)} onBlur={() => setFocusedDetail(null)}
              onClick={(event) => selectMeasurement(event, detail)} onKeyDown={(event) => handleMeasurementKey(event, detail, index, 'temperature')}>
              {outsideScale ? <path className="bio-trend-overflow is-temperature"
                d={point.average_temperature_c < 0
                  ? `M${x - 4} ${y - 7} L${x} ${y - 1} L${x + 4} ${y - 7} Z`
                  : `M${x - 4} ${y + 7} L${x} ${y + 1} L${x + 4} ${y + 7} Z`} />
                : point.week ? <>
                  <line className="bio-trend-week-span" x1={Math.max(padding.left, xPosition(point.week.start))}
                    x2={Math.min(width - padding.right, xPosition(point.week.end))} y1={y} y2={y} />
                  <rect className="bio-trend-dot is-temperature is-weekly"
                    x={x - layout.pointRadius} y={y - layout.pointRadius}
                    width={layout.pointRadius * 2} height={layout.pointRadius * 2} />
                </> : <circle className="bio-trend-dot is-temperature" cx={x} cy={y} r={point.average_temperature_c === 0 ? layout.zeroRadius : layout.pointRadius} />}
              <circle className="bio-trend-temperature-hit" cx={x} cy={y} r={layout.responsive ? 12 : 8} />
            </g>
          ))}

          {!plottedReadingDetails.length && !plottedTemperatures.length ? (
            <text className="bio-trend-empty" x={width / 2} y={plotTop + plotHeight / 2}>{labels.empty}</text>
          ) : null}

          <text className="bio-trend-label" x={padding.left} y={countHeight - 9}>{formatDisplayDate(start)}</text>
          <text className="bio-trend-label is-end" x={width - padding.right} y={countHeight - 9}>{formatDisplayDate(end)}</text>
          {countTicks.map((value) => (
            <text
              key={value}
              className="bio-trend-y-label bio-trend-value-label"
              x={padding.left - 8}
              y={yCount(value) + 4}
            >
              {value}
            </text>
          ))}
        </svg>

      </div>

      {overlappingEvents.length > 0 ? (
        <div className="bio-trend-event-list" role="group" aria-label={labels.chartTitle}>
          {overlappingEvents.map(({ event, detail, isDerived }) => (
            <button
              key={event.id}
              type="button"
              className={`bio-trend-event-choice is-${isDerived ? 'location-change' : event.kind ?? 'movement'}`}
              aria-label={`${detail.accessibleTitle ?? detail.title}${event.detail ? `, ${event.detail}` : ''}`}
              aria-pressed={pinnedVisibleDetail?.id === detail.id}
              aria-describedby={visibleDetail?.id === detail.id ? `${chartId}-event-detail` : undefined}
              onClick={() => toggleDetail(detail)}
              onMouseEnter={() => setHoveredDetail(detail)}
              onMouseLeave={() => setHoveredDetail(null)}
              onFocus={() => setFocusedDetail(detail)}
              onBlur={() => setFocusedDetail(null)}
              onKeyDown={(keyEvent) => {
                if (keyEvent.key === 'Escape') {
                  setPinnedDetail(null);
                  setFocusedDetail(null);
                  setHoveredDetail(null);
                }
              }}
            >
              <span>{detail.title}</span>
              {event.detail ? <small>{event.detail}</small> : null}
            </button>
          ))}
        </div>
      ) : null}

      {visibleDetail ? (
        <div id={`${chartId}-event-detail`} ref={tooltipRef} className={`bio-trend-tooltip${pinnedVisibleDetail?.id === visibleDetail.id ? ' is-pinned' : ''}`} role="group" aria-label={visibleDetail.accessibleTitle ?? visibleDetail.title}>
          <div className="bio-trend-tooltip-heading">
            <strong>{visibleDetail.title}</strong>
            {pinnedVisibleDetail?.id === visibleDetail.id ? (
              <button type="button" aria-label={labels.closeDetail} onClick={() => {
                setPinnedDetail(null);
                const series = selectedTemperature ? 'temperature' : 'measurement';
                const index = selectedTemperature ? temperatureDetails.indexOf(selectedTemperature)
                  : selectedReading ? plottedReadingDetails.indexOf(selectedReading) : -1;
                if (index >= 0) {
                  svgRef.current?.querySelector<SVGGElement>(`[data-${series}-index="${index}"]`)?.focus();
                }
              }}>×</button>
            ) : null}
          </div>
          {visibleDetail.id.startsWith('measurement-') || visibleDetail.id.startsWith('biological-') ? (
            <>
              <div className="bio-trend-tooltip-values">
                {visibleDetail.lines.filter((item) => item.kind === 'polyps' || item.kind === 'ephyrae' || item.kind === 'salinity').map((item) => (
                  <span key={item.kind} className={`is-${item.kind}`}>
                    <small>{item.label}</small><strong>{item.value}</strong>
                  </span>
                ))}
              </div>
              {visibleDetail.lines.some((item) => item.kind === 'location' || item.kind === 'user') ? (
                <div className="bio-trend-tooltip-metadata">
                  {visibleDetail.lines.filter((item) => item.kind === 'location' || item.kind === 'user').map((item) => (
                    <span className="bio-trend-tooltip-context" key={item.kind}>
                      <small>{item.label}</small><strong>{item.value}</strong>
                    </span>
                  ))}
                </div>
              ) : null}
              {visibleDetail.lines.filter((item) => item.kind === 'allocation').map((item, index) => (
                <span className="bio-trend-tooltip-context" key={`allocation-${index}`}>
                  <small>{item.label}</small><strong>{item.value}</strong>
                </span>
              ))}
              {visibleDetail.lines.filter((item) => item.kind === 'note').map((item) => (
                <span className="bio-trend-tooltip-note" key={item.kind}>
                  <small>{item.label}</small><strong>{item.value}</strong>
                </span>
              ))}
            </>
          ) : visibleDetail.id.startsWith('temperature-') ? <>
            <div className="bio-trend-tooltip-values">
              {visibleDetail.lines.filter((item) => item.kind === 'temperature').map((item) => (
                <span key={item.kind} className="is-temperature"><small>{item.label}</small><strong>{item.value}</strong></span>
              ))}
            </div>
            {visibleDetail.lines.filter((item) => item.kind !== 'temperature').map((item, index) => (
              <span className="bio-trend-tooltip-context" key={`${item.kind}-${index}`}><small>{item.label}</small><strong>{item.value}</strong></span>
            ))}
          </> : visibleDetail.lines.map((item, index) => (
            <span className="bio-trend-tooltip-context" key={`${item.kind}-${index}`}>
              <small>{item.label}</small><strong>{item.value}</strong>
            </span>
          ))}
        </div>
      ) : null}

      <div className="bio-trend-legend bio-trend-legend--footer" role="group" aria-label={labels.chartTitle}>
        <button type="button" className={`is-polyps${visibleSeries.polyps ? '' : ' is-hidden'}`} aria-pressed={visibleSeries.polyps}
          onClick={() => setVisibleSeries((current) => toggleChartSeries(current, 'polyps'))}>{labels.polyps}</button>
        <button type="button" className={`is-ephyrae${visibleSeries.ephyrae ? '' : ' is-hidden'}`} aria-pressed={visibleSeries.ephyrae}
          onClick={() => setVisibleSeries((current) => toggleChartSeries(current, 'ephyrae'))}>{labels.ephyrae}</button>
        {temperatureEnabled ? (
          <button type="button" className={`is-temperature${temperatureVisible ? '' : ' is-hidden'}`} aria-pressed={temperatureVisible}
            onClick={() => {
              setTemperatureVisible((current) => !current);
              const keepOtherSeries = (current: ActiveDetail | null) => current?.id.startsWith('temperature-') && !current.id.startsWith('temperature-week-') ? null : current;
              setPinnedDetail(keepOtherSeries);
              setHoveredDetail(keepOtherSeries);
              setFocusedDetail(keepOtherSeries);
            }}>{labels.temperature} (°C)</button>
        ) : null}
        {temperatureEnabled && hasWeeklyTemperatures && labels.historicalTemperature ? (
          <button type="button" className={`is-temperature is-weekly${historicalVisible ? '' : ' is-hidden'}`} aria-pressed={historicalVisible}
            onClick={() => {
              setHistoricalVisible((current) => !current);
              const keepOtherSeries = (current: ActiveDetail | null) => current?.id.startsWith('temperature-week-') ? null : current;
              setPinnedDetail(keepOtherSeries);
              setHoveredDetail(keepOtherSeries);
              setFocusedDetail(keepOtherSeries);
            }}>{labels.historicalTemperature} (°C)</button>
        ) : null}
        {polypStates.length > 0 && labels.subculture ? <span className="is-subculture">{labels.subculture}</span> : null}
        {hasOverflow ? <span className="is-overflow">&gt; {maxCount}</span> : null}
        {temperatureEnabled ? <div className="bio-trend-series-feedback">
          {temperatureStatus}
          {hasTemperatureOverflow ? <span className="bio-trend-temperature-overflow-note">{labels.temperatureOutsideScale}</span> : null}
        </div> : null}
      </div>
      {temperatureEnabled && overlappingTemperatures.length > 0 ? (
        <div className="bio-trend-event-list" role="group" aria-label={labels.temperature}>
          {overlappingTemperatures.map(({ detail, point }) => (
            <button key={detail.id} type="button" className="bio-trend-event-choice is-temperature"
              aria-pressed={selectedTemperature?.detail.id === detail.id}
              onClick={() => setPinnedDetail(detail)}
              onFocus={() => setFocusedDetail(detail)} onBlur={() => setFocusedDetail(null)}
              onMouseEnter={() => setHoveredDetail(detail)} onMouseLeave={() => setHoveredDetail(null)}
              onKeyDown={(event) => {
                if (event.key === 'Escape') { setPinnedDetail(null); setFocusedDetail(null); setHoveredDetail(null); }
              }}>
              <span>{detail.title} — {point.week?.valueText ?? point.average_temperature_c} °C</span>
              <small>{point.week ? labels.historicalTemperature : point.zone_name}</small>
            </button>
          ))}
        </div>
      ) : null}
    </div>
  );
}

function buildMeasurementDetailLines(
  measurement: TrendPoint,
  labels: TrendLabels,
  locations: TrendLocation[],
  knownLocationName?: string,
): DetailLine[] {
  const sourceLocations = 'kind' in measurement ? null : measurement.environmentLocations;
  const locationName = sourceLocations != null
    ? findSourceChartLocationAtDate(sourceLocations, measurement.date)
    : knownLocationName ?? findChartLocationAtDate(locations, measurement.date);
  const lines: DetailLine[] = 'kind' in measurement
    ? [
      { kind: 'polyps', label: labels.polyps, value: String(measurement.polypCount) },
      ...(measurement.detailLines ?? []).map((item) => ({ ...item, kind: 'allocation' as const })),
    ]
    : chartBiologicalValues(measurement, labels);

  if (locationName) {
    lines.push({
      kind: 'location',
      label: sourceLocations != null ? labels.sourceLocation ?? labels.location : labels.location,
      value: locationName,
    });
  }

  if (labels.enteredBy) {
    lines.push({ kind: 'user', label: labels.enteredBy, value: formatReadableUserIdentity(measurement.user_identity) || labels.historicalUser || '—' });
  }

  if (labels.observation && measurement.note?.trim()) {
    lines.push({
      kind: 'note',
      label: labels.observation,
      value: measurement.note.trim(),
    });
  }

  return lines;
}

export function resolveChartLayout(compact: boolean, availableWidth = 0, responsive = false) {
  return {
    responsive,
    width: responsive && availableWidth > 0 ? availableWidth : compact ? 640 : 860,
    countHeight: compact ? 238 : 260,
    pointRadius: responsive ? 3 : 2.15,
    zeroRadius: responsive ? 3.4 : 2.8,
  };
}

export function buildDiamondPath(x: number, y: number, radius: number) {
  return `M${x} ${y - radius} L${x + radius} ${y} L${x} ${y + radius} L${x - radius} ${y} Z`;
}

export function splitTrendPointsOnGaps(points: TrendPoint[]): TrendPoint[][] {
  const segments: TrendPoint[][] = [];
  const calendarDay = (date: string) => {
    const [year, month, day] = date.slice(0, 10).split('-').map(Number);
    return Date.UTC(year, month - 1, day);
  };
  points.forEach((point, index) => {
    const previous = points[index - 1];
    if (!previous || (calendarDay(point.date) - calendarDay(previous.date)) / 86400000 > 10) {
      segments.push([]);
    }
    segments[segments.length - 1].push(point);
  });
  return segments;
}

export function buildMeasurementHitAreas(positions: number[], left: number, right: number, responsive: boolean) {
  return positions.map((x, index) => {
    const firstTie = positions.indexOf(x);
    const lastTie = positions.lastIndexOf(x);
    if (firstTie !== lastTie) {
      const radius = responsive ? 22 : 13;
      const start = responsive ? Math.max(left, x - radius, firstTie > 0 ? (positions[firstTie - 1] + x) / 2 : left) : x - radius;
      const end = responsive ? Math.min(right, x + radius, lastTie + 1 < positions.length ? (x + positions[lastTie + 1]) / 2 : right) : x + radius;
      const width = Math.max(0, end - start) / (lastTie - firstTie + 1);
      return { left: start + (index - firstTie) * width, width };
    }
    if (!responsive) return { left: x - 13, width: 26 };
    // Partition dense readings at their midpoints rather than letting later
    // hit rectangles cover earlier readings. Keyboard access also keeps ties reachable.
    const start = Math.max(left, x - 22, index > 0 ? (positions[index - 1] + x) / 2 : left);
    const end = Math.min(right, x + 22, index + 1 < positions.length ? (x + positions[index + 1]) / 2 : right);
    return { left: start, width: Math.max(0, end - start) };
  });
}

export function buildGeometry(
  measurements: TrendMeasurement[],
  locations: TrendLocation[],
  events: TrendEvent[],
  startDate: string,
  endDate: string,
  compact: boolean,
  layout = resolveChartLayout(compact),
  polypStates: TrendPolypState[] = [],
  temperatures: TrendTemperature[] = [],
  temperatureEnabled = true,
) {
  const { width, countHeight } = layout;
  const plottedTemperatures = temperatures
    .filter((point) => point.date >= startDate && point.date <= endDate
      && typeof point.average_temperature_c === 'number' && Number.isFinite(point.average_temperature_c))
    .slice().sort((left, right) => left.date.localeCompare(right.date));
  const padding = layout.responsive
    ? { top: 8, right: 12, bottom: 34, left: 44 }
    : compact
    ? { top: 6, right: 22, bottom: 34, left: 44 }
    : { top: 8, right: 26, bottom: 34, left: 44 };
  if (temperatureEnabled) padding.right = 62;
  const start = normalizeDate(startDate);
  const requestedEnd = normalizeDate(endDate);
  const end = requestedEnd <= start ? addDays(start, 1) : requestedEnd;
  const startText = toDateString(start);
  const endText = toDateString(end);
  const plottedMeasurements: TrendPoint[] = [...measurements, ...polypStates]
    .filter((point) => point.date >= startText && point.date <= endText)
    .sort((left, right) => left.date.localeCompare(right.date) || (left.timelineOrder ?? 0) - (right.timelineOrder ?? 0));
  const plottedEvents = [...events]
    .filter((event) => isChartEventInRange(event.date, startText, endText))
    .sort((left, right) => left.date.localeCompare(right.date));
  const xScale = scaleTime().domain([start, end]).range([padding.left, width - padding.right]);
  const xPosition = (date: string) => xScale(normalizeDate(date));
  const measurementDates = [...measurements, ...polypStates]
    .map((measurement) => measurement.date)
    .sort();
  const latestMeasurementDate = measurementDates[measurementDates.length - 1] ?? '';
  const locationBands = buildLocationBands(
    locations,
    startText,
    endText,
    latestMeasurementDate,
    xPosition,
  );
  const zoneBandHeight = 0;
  const zoneBandGap = 0;
  const plotTop = padding.top + zoneBandHeight + zoneBandGap;
  const plotHeight = countHeight - padding.bottom - plotTop;
  const chartAreaHeight = countHeight - padding.bottom - padding.top;
  const maxCount = BIOLOGICAL_COUNT_AXIS_MAX;
  const yCount = scaleLinear()
    .domain([0, maxCount])
    .range([countHeight - padding.bottom, plotTop])
    .clamp(true);
  const countLine = (selector: (point: TrendPoint) => number | null | undefined) => line<TrendPoint>()
    .defined((point) => selector(point) != null)
    .x((point) => xPosition(point.date))
    .y((point) => yCount(selector(point)!));

  const yTemperature = scaleLinear()
    .domain(BIOLOGICAL_TEMPERATURE_DOMAIN)
    .range([countHeight - padding.bottom, plotTop]);
  // Sparse historical readings remain connected; no daily samples are synthesized.
  const temperatureLine = line<TrendTemperature>()
    .defined(isTemperatureInScale)
    .x((point) => xPosition(point.date)).y((point) => yTemperature(point.average_temperature_c));
  const countTicks = yCount.ticks(VALUE_AXIS_TICK_COUNT);
  const temperatureTicks = BIOLOGICAL_TEMPERATURE_TICKS;

  const timeTicks = buildTimeTicks(start, end, xPosition, padding, width, compact, layout.responsive);
  const transitions = buildZoneTransitions(locationBands);
  // Persisted movements sit at their own date; they are never snapped to a period boundary.
  const explicitEventPoints = plottedEvents.map((event) => ({
    event,
    x: xPosition(event.date),
    coincidesWithBoundary: event.kind === 'movement'
      && transitions.some((transition) => isSameInstant(event.instant ?? event.date, transition.startsAt)),
  }));
  // A derived boundary is dropped only when a persisted movement happened at the exact same instant.
  const generatedTransferPoints = transitions
    .filter((transition) => !explicitEventPoints.some(({ event }) => (
      event.kind === 'movement' && isSameInstant(event.instant ?? event.date, transition.startsAt)
    )))
    .map((transition): { event: TrendEvent; x: number; coincidesWithBoundary: boolean } => ({
      event: {
        id: `zone-transition-${transition.id}`,
        date: transition.date,
        ...(transition.startsAt.length > 10 ? { instant: transition.startsAt } : {}),
        detail: `${transition.from} -> ${transition.to}`,
        kind: 'location_change' as const,
        title: '',
      },
      x: transition.x,
      coincidesWithBoundary: false,
    }));

  return {
    countHeight,
    countLine,
    countTicks,
    chartAreaHeight,
    end: endText,
    eventPoints: [...explicitEventPoints, ...generatedTransferPoints]
      .sort((left, right) => left.x - right.x
        || Number(right.event.kind === 'location_change') - Number(left.event.kind === 'location_change')),
    locationBands,
    hitAreas: buildMeasurementHitAreas(plottedMeasurements.map((point) => xPosition(point.date)), padding.left, width - padding.right, layout.responsive),
    maxCount,
    measurementSegments: splitMeasurementsOnGaps(plottedMeasurements.filter((point): point is TrendMeasurement => !('kind' in point))),
    polypSegments: splitTrendPointsOnGaps(plottedMeasurements),
    padding,
    plotHeight,
    plotTop,
    plottedMeasurements,
    plottedTemperatures,
    temperatureLine,
    temperatureTicks,
    yTemperature,
    start: startText,
    timeTicks,
    width,
    xPosition,
    yCount,
    zoneBandHeight,
  };
}

function buildTimeTicks(
  start: Date,
  end: Date,
  xPosition: (date: string) => number,
  padding: { left: number; right: number },
  width: number,
  compact: boolean,
  responsive = false,
): TimeTick[] {
  const candidates: Date[] = [];
  const cursor = new Date(start.getFullYear(), start.getMonth() + 1, 1);
  while (cursor < end) {
    candidates.push(new Date(cursor));
    cursor.setMonth(cursor.getMonth() + 1);
  }

  const edgeClearance = responsive ? 116 : compact ? 104 : 92;
  const maxTicks = responsive
    ? Math.max(1, Math.floor((width - padding.left - padding.right - 2 * edgeClearance) / 88))
    : compact ? 3 : 6;
  const step = Math.max(1, Math.ceil(candidates.length / maxTicks));
  return candidates
    .filter((_, index) => index % step === 0)
    .map((date) => {
      const dateText = toDateString(date);
      return {
        date: dateText,
        label: `${String(date.getMonth() + 1).padStart(2, '0')}/${date.getFullYear()}`,
        x: xPosition(dateText),
      };
    })
    .filter((tick) => (
      tick.x >= padding.left + edgeClearance
      && tick.x <= width - padding.right - edgeClearance
    ));
}

function buildLocationBands(
  locations: TrendLocation[],
  startDate: string,
  endDate: string,
  latestMeasurementDate: string,
  xPosition: (date: string) => number,
): LocationBand[] {
  return resolveChartLocationPeriods(locations, endDate, latestMeasurementDate)
    .filter((location) => (
      location.startDate <= endDate && location.endDate >= startDate
    ))
    .map((location) => {
      const clippedStart = location.startDate < startDate ? startDate : location.startDate;
      const clippedEnd = location.endDate > endDate ? endDate : location.endDate;
      const x1 = xPosition(clippedStart);
      const x2 = xPosition(clippedEnd);
      return {
        endDate: clippedEnd,
        id: location.id,
        name: location.name,
        startsAt: location.startsAt,
        startDate: clippedStart,
        width: Math.max(2, x2 - x1),
        x1,
      };
    });
}

function buildZoneTransitions(locationBands: LocationBand[]) {
  return locationBands.flatMap((band, index) => {
    const previousBand = locationBands[index - 1];
    if (!previousBand || previousBand.name === band.name || daysBetween(previousBand.endDate, band.startDate) > 1) return [];

    return [{
      date: band.startDate,
      from: previousBand.name,
      id: `${previousBand.id}-${band.id}`,
      startsAt: band.startsAt,
      to: band.name,
      x: band.x1,
    }];
  });
}

export function formatEventDate(event: TrendEvent) {
  const instant = event.instant ?? event.date;
  if (instant.length <= 10 || Number.isNaN(Date.parse(instant))) return formatDisplayDate(event.date);
  // Keep the localized date convention and show the exact UTC time, including supplied precision.
  const utc = new Date(instant).toISOString();
  const time = utc.slice(11).replace(/\.000Z$/, 'Z').replace(/Z$/, ' UTC');
  return `${formatDisplayDate(utc.slice(0, 10))} ${time}`;
}

// Two timestamps coincide only when both are known to the same precision and equal.
function isSameInstant(first: string, second: string) {
  const firstHasTime = first.length > 10;
  if (firstHasTime !== second.length > 10) return false;
  if (!firstHasTime) return first === second;
  const firstTime = Date.parse(first);
  return !Number.isNaN(firstTime) && firstTime === Date.parse(second);
}

export function isChartEventInRange(value: string, startDate: string, endDate: string) {
  // Date-only history keeps calendar precision; timestamps use the selector's local days.
  if (value.length === 10) return value >= startDate && value <= endDate;
  const instant = new Date(value).getTime();
  return instant >= normalizeDate(startDate).getTime()
    && instant < addDays(normalizeDate(endDate), 1).getTime();
}

function normalizeDate(value: string) {
  return new Date(`${value.slice(0, 10)}T00:00:00`);
}

function addDays(date: Date, days: number) {
  const result = new Date(date);
  result.setDate(result.getDate() + days);
  return result;
}

function toDateString(date: Date) {
  const result = new Date(date);
  result.setMinutes(result.getMinutes() - result.getTimezoneOffset());
  return result.toISOString().slice(0, 10);
}

function daysBetween(first: string, second: string) {
  return Math.round((normalizeDate(second).getTime() - normalizeDate(first).getTime()) / 86_400_000);
}
