import {
  useEffect,
  useMemo,
  useState,
  type CSSProperties,
  type KeyboardEvent,
  type MouseEvent,
} from 'react';
import { scaleLinear, scaleTime } from 'd3-scale';
import { line } from 'd3-shape';

import { formatDisplayDate } from '../utils/dateFormat';
import { getZoneColor } from '../utils/zoneColor';

export type TrendMeasurement = {
  id: number | string;
  date: string;
  polypCount: number;
  ephyraeCount: number;
  salinity?: string | number | null;
  enteredBy?: string | null;
  note?: string | null;
};

export type TrendLocation = {
  id: number | string;
  name: string;
  startsAt: string;
  endsAt: string | null;
};

export type TrendEvent = {
  id: number | string;
  date: string;
  title: string;
  detail?: string;
  kind?: 'movement' | 'subculture';
};

type TrendLabels = {
  chartTitle: string;
  closeDetail?: string;
  empty: string;
  ephyrae: string;
  enteredBy?: string;
  location: string;
  missingReading: string;
  movement?: string;
  observation?: string;
  polyps: string;
  salinity?: string;
  selectReading?: string;
  selectedReading?: string;
};

type ActiveDetail = {
  date?: string;
  id: string;
  left: number;
  placement: 'above' | 'below';
  top: number;
  title: string;
  lines: DetailLine[];
};

type DetailLine = {
  kind: 'ephyrae' | 'location' | 'note' | 'polyps' | 'salinity' | 'user';
  label: string;
  value: string;
};

type LocationBand = {
  endDate: string;
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

export default function BiologicalTrendChart({
  compact = false,
  detailDisplay = 'floating',
  endDate,
  events = [],
  labels,
  locations = [],
  measurements,
  selectionScope,
  startDate,
}: {
  compact?: boolean;
  detailDisplay?: 'floating' | 'inline';
  endDate: string;
  events?: TrendEvent[];
  labels: TrendLabels;
  locations?: TrendLocation[];
  measurements: TrendMeasurement[];
  selectionScope?: number | string;
  startDate: string;
}) {
  const [activeDetail, setActiveDetail] = useState<ActiveDetail | null>(null);
  const geometry = useMemo(
    () => buildGeometry(measurements, locations, events, startDate, endDate, compact),
    [compact, endDate, events, locations, measurements, startDate],
  );

  useEffect(() => {
    if (detailDisplay === 'floating') {
      setActiveDetail(null);
    }
  }, [detailDisplay, endDate, startDate]);

  useEffect(() => {
    setActiveDetail(null);
  }, [selectionScope]);

  function toggleDetail(detail: ActiveDetail) {
    setActiveDetail((current) => current?.id === detail.id ? null : detail);
  }

  function handleDetailKey(event: KeyboardEvent<SVGGElement>, detail: ActiveDetail) {
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
    setActiveDetail(detail);
  }

  const {
    countHeight,
    countLine,
    chartAreaHeight,
    end,
    eventPoints,
    locationBands,
    maxCount,
    measurementSegments,
    missingRanges,
    padding,
    plotHeight,
    plotTop,
    plottedMeasurements,
    start,
    timeTicks,
    width,
    xPosition,
    yCount,
    zoneBandHeight,
  } = geometry;
  const latestMeasurement = useMemo(() => {
    const sortedMeasurements = [...measurements].sort((left, right) => left.date.localeCompare(right.date));
    return sortedMeasurements[sortedMeasurements.length - 1] ?? null;
  }, [measurements]);
  const latestDetail: ActiveDetail | null = latestMeasurement
    ? {
        id: `measurement-${latestMeasurement.id}`,
        date: latestMeasurement.date,
        left: 0,
        placement: 'above',
        top: 0,
        title: formatDisplayDate(latestMeasurement.date),
        lines: buildMeasurementDetailLines(latestMeasurement, labels, locations),
      }
    : null;
  const inlineDetail = activeDetail?.id.startsWith('measurement-')
    ? activeDetail
    : latestDetail;
  const plottedReadingDetails = plottedMeasurements.map((measurement) => {
    const x = xPosition(measurement.date);
    const topY = Math.min(yCount(measurement.polypCount), yCount(measurement.ephyraeCount));
    const locationName = findLocationAtDate(locations, measurement.date);
    const detail: ActiveDetail = {
      id: `measurement-${measurement.id}`,
      date: measurement.date,
      left: (x / width) * 100,
      placement: topY < plotTop + 82 ? 'below' : 'above',
      top: Math.max(plotTop + 10, topY - 8),
      title: formatDisplayDate(measurement.date),
      lines: buildMeasurementDetailLines(measurement, labels, locations, locationName),
    };
    return { detail, measurement, x };
  });
  const selectedDetailId = detailDisplay === 'inline' ? inlineDetail?.id : activeDetail?.id;
  const selectedReading = plottedReadingDetails.find(({ detail }) => detail.id === selectedDetailId) ?? null;
  const inlineReadoutLines = inlineDetail?.lines.filter((item) => item.kind !== 'location') ?? [];
  const inlineReadoutColumnCount = Math.max(
    1,
    inlineReadoutLines.filter((item) => item.kind !== 'note').length,
  );
  const hasOverflow = plottedMeasurements.some((measurement) => (
    measurement.polypCount > maxCount || measurement.ephyraeCount > maxCount
  ));
  function handleMeasurementKey(
    event: KeyboardEvent<SVGGElement>,
    detail: ActiveDetail,
    measurementIndex: number,
  ) {
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      setActiveDetail(detail);
      return;
    }

    const targetIndex = event.key === 'ArrowLeft'
      ? measurementIndex - 1
      : event.key === 'ArrowRight'
        ? measurementIndex + 1
        : event.key === 'Home'
          ? 0
          : event.key === 'End'
            ? plottedReadingDetails.length - 1
            : null;
    if (targetIndex == null) return;

    event.preventDefault();
    const nextIndex = Math.max(0, Math.min(plottedReadingDetails.length - 1, targetIndex));
    const nextReading = plottedReadingDetails[nextIndex];
    if (!nextReading) return;
    event.currentTarget.ownerSVGElement
      ?.querySelector<SVGGElement>(`[data-measurement-index="${nextIndex}"]`)
      ?.focus();
  }

  return (
    <div className={`${compact ? 'bio-trend is-compact' : 'bio-trend'}${detailDisplay === 'inline' ? ' has-inline-detail' : ''}`}>
      {detailDisplay === 'inline' && inlineDetail ? (
        <div className="bio-trend-heading">
          <section className="bio-trend-readout" aria-live="polite">
            <header className="bio-trend-readout-header">
              <span>{labels.selectedReading ?? labels.chartTitle}</span>
              <time dateTime={inlineDetail.date}>{inlineDetail.title}</time>
            </header>
            <dl
              className="bio-trend-detail-values"
              style={{ '--detail-columns': inlineReadoutColumnCount } as CSSProperties}
            >
              {inlineReadoutLines.map((item) => (
                <div key={`${item.kind}-${item.label}`} className={`is-${item.kind}`}>
                  <dt>{item.label}</dt>
                  <dd>{item.value}</dd>
                </div>
              ))}
            </dl>
          </section>
        </div>
      ) : null}

      <div className="bio-trend-canvas">
        <svg
          className="bio-trend-svg"
          viewBox={`0 0 ${width} ${countHeight}`}
          role="img"
          aria-label={labels.chartTitle}
          onClick={() => {
            if (detailDisplay === 'floating') {
              setActiveDetail(null);
            }
          }}
        >
          {locationBands.map((band) => {
            const zoneColor = getZoneColor(band.name);
            return (
              <g
                key={band.id}
                className="bio-trend-location-band"
                style={{
                  '--zone-band-color': zoneColor.background,
                  '--zone-border-color': zoneColor.border,
                  '--zone-text-color': zoneColor.text,
                } as CSSProperties}
              >
                <rect
                  x={band.x1}
                  y={padding.top}
                  width={band.width}
                  height={zoneBandHeight}
                  rx={compact ? 2 : 3}
                />
                <line
                  className="bio-trend-location-band-baseline"
                  x1={band.x1}
                  x2={band.x1 + band.width}
                  y1={padding.top + zoneBandHeight}
                  y2={padding.top + zoneBandHeight}
                />
                {band.width >= (compact ? 72 : 92) ? (
                  <text
                    x={band.x1 + 8}
                    y={padding.top + zoneBandHeight / 2}
                    dominantBaseline="middle"
                  >
                    {band.name}
                  </text>
                ) : null}
                {band.x1 > padding.left + 1 ? (
                  <line x1={band.x1} x2={band.x1} y1={padding.top} y2={padding.top + zoneBandHeight} />
                ) : null}
              </g>
            );
          })}

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

          {[0.25, 0.5, 0.75].map((ratio) => {
            const y = plotTop + ratio * plotHeight;
            return <line key={ratio} className="bio-trend-grid" x1={padding.left} y1={y} x2={width - padding.right} y2={y} />;
          })}
          <line className="bio-trend-axis" x1={padding.left} y1={countHeight - padding.bottom} x2={width - padding.right} y2={countHeight - padding.bottom} />
          <line className="bio-trend-axis" x1={padding.left} y1={plotTop} x2={padding.left} y2={countHeight - padding.bottom} />

          {missingRanges.map((range) => (
            <g key={`${range.start}-${range.end}`} className="bio-trend-missing-range">
              <rect
                className="bio-trend-missing-wash"
                x={range.x1}
                y={plotTop}
                width={range.width}
                height={plotHeight}
              />
              <title>{`${labels.missingReading}: ${formatDisplayDate(range.start)} - ${formatDisplayDate(range.end)}`}</title>
            </g>
          ))}

          {selectedReading ? (
            <line
              className="bio-trend-selection-guide"
              x1={selectedReading.x}
              x2={selectedReading.x}
              y1={plotTop}
              y2={countHeight - padding.bottom}
            />
          ) : null}

          {measurementSegments.map((segment, index) => (
            <path key={`polyps-${index}`} className="bio-trend-line is-polyps" d={countLine((point) => point.polypCount)(segment) ?? ''} />
          ))}
          {measurementSegments.map((segment, index) => (
            <path key={`ephyrae-${index}`} className="bio-trend-line is-ephyrae" d={countLine((point) => point.ephyraeCount)(segment) ?? ''} />
          ))}

          {eventPoints.map(({ event, x }) => {
            const eventTitle = event.title || labels.movement || 'Transfert';
            const detail = {
              id: `event-${event.id}`,
              left: (x / width) * 100,
              placement: 'below' as const,
              top: plotTop + 6,
              title: `${eventTitle} - ${formatDisplayDate(event.date)}`,
              lines: event.detail
                ? [{ kind: 'location' as const, label: labels.location, value: event.detail }]
                : [],
            };
            return (
              <g
                key={event.id}
                className={`bio-trend-event is-${event.kind ?? 'movement'}`}
                role="button"
                tabIndex={0}
                aria-label={`${eventTitle} ${formatDisplayDate(event.date)}`}
                transform={`translate(${x} ${padding.top})`}
                onClick={(clickEvent) => stopAndToggle(clickEvent, detail)}
                onKeyDown={(keyEvent) => handleDetailKey(keyEvent, detail)}
              >
                <line
                  className="bio-trend-event-hit-area"
                  x1={0}
                  y1={event.kind === 'movement' ? 0 : plotTop - padding.top}
                  x2={0}
                  y2={chartAreaHeight}
                />
                <line
                  className="bio-trend-event-line"
                  x1={0}
                  y1={event.kind === 'movement' ? 0 : plotTop - padding.top}
                  x2={0}
                  y2={chartAreaHeight}
                />
                {event.kind !== 'movement' ? <path d="M0 0 L6 6 L0 12 L-6 6 Z" /> : null}
                {event.kind === 'movement' ? (
                  <text
                    className="bio-trend-event-label"
                    x={-chartAreaHeight / 2}
                    y={-8}
                    textAnchor="middle"
                    transform="rotate(-90)"
                  >
                    {(labels.movement ?? 'Transfert').toLocaleUpperCase('fr-FR')}
                  </text>
                ) : null}
              </g>
            );
          })}

          {plottedReadingDetails.map(({ detail, measurement, x }, measurementIndex) => {
            return (
              <g
                key={measurement.id}
                className={`bio-trend-measurement${selectedDetailId === detail.id ? ' is-selected' : ''}`}
                data-measurement-index={measurementIndex}
                role="button"
                tabIndex={0}
                aria-pressed={selectedDetailId === detail.id}
                aria-label={`${formatDisplayDate(measurement.date)}, ${detail.lines.map((item) => `${item.label}: ${item.value}`).join(', ')}`}
                onClick={(clickEvent) => selectMeasurement(clickEvent, detail)}
                onKeyDown={(keyEvent) => handleMeasurementKey(keyEvent, detail, measurementIndex)}
              >
                <title>{`${formatDisplayDate(measurement.date)} - ${labels.polyps}: ${measurement.polypCount}, ${labels.ephyrae}: ${measurement.ephyraeCount}`}</title>
                <circle className="bio-trend-dot is-polyps" cx={x} cy={yCount(measurement.polypCount)} r={measurement.polypCount === 0 ? 3 : 2.35} />
                <circle className="bio-trend-dot is-ephyrae" cx={x} cy={yCount(measurement.ephyraeCount)} r={measurement.ephyraeCount === 0 ? 3 : 2.35} />
                {measurement.polypCount > maxCount ? (
                  <path
                    className="bio-trend-overflow is-polyps"
                    d={`M${x - 4} ${plotTop + 8} L${x} ${plotTop + 1} L${x + 4} ${plotTop + 8} Z`}
                  />
                ) : null}
                {measurement.ephyraeCount > maxCount ? (
                  <path
                    className="bio-trend-overflow is-ephyrae"
                    d={`M${x - 4} ${plotTop + 14} L${x} ${plotTop + 7} L${x + 4} ${plotTop + 14} Z`}
                  />
                ) : null}
                <rect className="bio-trend-hit-area" x={x - 13} y={plotTop} width={26} height={plotHeight} />
              </g>
            );
          })}

          {!plottedMeasurements.length ? (
            <text className="bio-trend-empty" x={width / 2} y={plotTop + plotHeight / 2}>{labels.empty}</text>
          ) : null}

          <text className="bio-trend-label" x={padding.left} y={countHeight - 9}>{formatDisplayDate(start)}</text>
          <text className="bio-trend-label is-end" x={width - padding.right} y={countHeight - 9}>{formatDisplayDate(end)}</text>
          {[maxCount, 750, 500, 250, 0].map((value) => (
            <text
              key={value}
              className="bio-trend-y-label"
              x={padding.left - 8}
              y={yCount(value) + 4}
            >
              {value}
            </text>
          ))}
        </svg>

        {detailDisplay === 'floating' && activeDetail ? (
          <div
            className={`bio-trend-tooltip is-${activeDetail.placement}`}
            style={{ left: `${Math.min(86, Math.max(14, activeDetail.left))}%`, top: activeDetail.top }}
          >
            <strong>{activeDetail.title}</strong>
            {activeDetail.lines.map((item) => (
              <span key={item.label}>
                <small>{item.label}</small>
                <strong>{item.value}</strong>
              </span>
            ))}
          </div>
        ) : null}
      </div>

      <div className="bio-trend-legend bio-trend-legend--footer" aria-label={labels.chartTitle}>
        <span className="is-polyps">{labels.polyps}</span>
        <span className="is-ephyrae">{labels.ephyrae}</span>
        {missingRanges.length ? <span className="is-missing">{labels.missingReading}</span> : null}
        {hasOverflow ? <span className="is-overflow">&gt; {maxCount}</span> : null}
      </div>
    </div>
  );
}

function findLocationAtDate(locations: TrendLocation[], date: string) {
  return locations.find((location) => (
    location.startsAt.slice(0, 10) <= date
    && (!location.endsAt || location.endsAt.slice(0, 10) >= date)
  ))?.name;
}

function buildMeasurementDetailLines(
  measurement: TrendMeasurement,
  labels: TrendLabels,
  locations: TrendLocation[],
  knownLocationName?: string,
): DetailLine[] {
  const locationName = knownLocationName ?? findLocationAtDate(locations, measurement.date);
  const lines: DetailLine[] = [
    {
      kind: 'polyps',
      label: labels.polyps,
      value: String(measurement.polypCount),
    },
    {
      kind: 'ephyrae',
      label: labels.ephyrae,
      value: String(measurement.ephyraeCount),
    },
  ];

  if (labels.salinity) {
    lines.push({
      kind: 'salinity',
      label: labels.salinity,
      value: measurement.salinity == null ? '—' : formatDecimal(measurement.salinity),
    });
  }

  lines.push({
    kind: 'location',
    label: labels.location,
    value: locationName ?? '—',
  });

  if (labels.enteredBy) {
    lines.push({
      kind: 'user',
      label: labels.enteredBy,
      value: measurement.enteredBy?.trim() || '—',
    });
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

function buildGeometry(
  measurements: TrendMeasurement[],
  locations: TrendLocation[],
  events: TrendEvent[],
  startDate: string,
  endDate: string,
  compact: boolean,
) {
  // The overview still needs enough drawing space to remain readable inside a
  // two-column card layout. CSS scales this wider canvas without crushing text.
  const width = compact ? 640 : 860;
  const countHeight = compact ? 238 : 260;
  const padding = compact
    ? { top: 6, right: 22, bottom: 34, left: 44 }
    : { top: 8, right: 26, bottom: 34, left: 44 };
  const start = normalizeDate(startDate);
  const requestedEnd = normalizeDate(endDate);
  const end = requestedEnd <= start ? addDays(start, 1) : requestedEnd;
  const startText = toDateString(start);
  const endText = toDateString(end);
  const plottedMeasurements = [...measurements]
    .filter((point) => point.date >= startText && point.date <= endText)
    .sort((left, right) => left.date.localeCompare(right.date));
  const plottedEvents = [...events]
    .filter((event) => event.date >= startText && event.date <= endText)
    .sort((left, right) => left.date.localeCompare(right.date));
  const xScale = scaleTime().domain([start, end]).range([padding.left, width - padding.right]);
  const xPosition = (date: string) => xScale(normalizeDate(date));
  const locationBands = buildLocationBands(locations, startText, endText, xPosition);
  const zoneBandHeight = locationBands.length ? (compact ? 18 : 22) : 0;
  const zoneBandGap = locationBands.length ? (compact ? 4 : 6) : 0;
  const plotTop = padding.top + zoneBandHeight + zoneBandGap;
  const plotHeight = countHeight - padding.bottom - plotTop;
  const chartAreaHeight = countHeight - padding.bottom - padding.top;
  const maxCount = BIOLOGICAL_COUNT_AXIS_MAX;
  const yCount = scaleLinear()
    .domain([0, maxCount])
    .range([countHeight - padding.bottom, plotTop])
    .clamp(true);
  const countLine = (selector: (point: TrendMeasurement) => number) => line<TrendMeasurement>()
    .x((point) => xPosition(point.date))
    .y((point) => yCount(selector(point)));

  const timeTicks = buildTimeTicks(start, end, xPosition, padding, width, compact);
  const explicitEventPoints = plottedEvents.map((event) => ({
    event,
    x: resolveEventX(event, locationBands, xPosition),
  }));
  const generatedTransferPoints = buildZoneTransitions(locationBands)
    .filter((transition) => !explicitEventPoints.some(({ event, x }) => (
      event.kind === 'movement' && Math.abs(x - transition.x) <= 2
    )))
    .map((transition) => ({
      event: {
        id: `zone-transition-${transition.id}`,
        date: transition.date,
        detail: `${transition.from} -> ${transition.to}`,
        kind: 'movement' as const,
        title: '',
      },
      x: transition.x,
    }));

  return {
    countHeight,
    countLine,
    chartAreaHeight,
    end: endText,
    eventPoints: [...explicitEventPoints, ...generatedTransferPoints]
      .sort((left, right) => left.x - right.x),
    locationBands,
    maxCount,
    measurementSegments: splitMeasurementsOnGaps(plottedMeasurements),
    missingRanges: buildMissingRanges(plottedMeasurements, startText, endText).map((range) => ({
      ...range,
      x1: xPosition(range.start),
      width: Math.max(3, xPosition(range.end) - xPosition(range.start)),
    })),
    padding,
    plotHeight,
    plotTop,
    plottedMeasurements,
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
): TimeTick[] {
  const candidates: Date[] = [];
  const cursor = new Date(start.getFullYear(), start.getMonth() + 1, 1);
  while (cursor < end) {
    candidates.push(new Date(cursor));
    cursor.setMonth(cursor.getMonth() + 1);
  }

  const maxTicks = compact ? 3 : 6;
  const step = Math.max(1, Math.ceil(candidates.length / maxTicks));
  const edgeClearance = compact ? 104 : 92;
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
  xPosition: (date: string) => number,
): LocationBand[] {
  return [...locations]
    .filter((location) => location.startsAt.slice(0, 10) <= endDate && (!location.endsAt || location.endsAt.slice(0, 10) >= startDate))
    .sort((left, right) => left.startsAt.localeCompare(right.startsAt))
    .map((location) => {
      const clippedStart = location.startsAt.slice(0, 10) < startDate ? startDate : location.startsAt.slice(0, 10);
      const rawEnd = location.endsAt?.slice(0, 10) ?? endDate;
      const clippedEnd = rawEnd > endDate ? endDate : rawEnd;
      const x1 = xPosition(clippedStart);
      const x2 = xPosition(clippedEnd);
      return {
        endDate: clippedEnd,
        id: location.id,
        name: location.name,
        startDate: clippedStart,
        width: Math.max(2, x2 - x1),
        x1,
      };
    });
}

function buildZoneTransitions(locationBands: LocationBand[]) {
  return locationBands.flatMap((band, index) => {
    const previousBand = locationBands[index - 1];
    if (!previousBand || previousBand.name === band.name) return [];

    return [{
      date: band.startDate,
      from: previousBand.name,
      id: `${previousBand.id}-${band.id}`,
      to: band.name,
      x: band.x1,
    }];
  });
}

function resolveEventX(
  event: TrendEvent,
  locationBands: LocationBand[],
  xPosition: (date: string) => number,
) {
  const eventX = xPosition(event.date);
  if (event.kind !== 'movement') return eventX;

  const eventDay = event.date.slice(0, 10);
  const boundaries = locationBands.flatMap((band) => [
    { date: band.startDate, x: band.x1 },
    { date: band.endDate, x: band.x1 + band.width },
  ]);
  const exactBoundary = boundaries.find((boundary) => boundary.date === eventDay);
  if (exactBoundary) return exactBoundary.x;

  const closestBoundary = boundaries.reduce<{ distance: number; x: number } | null>((closest, boundary) => {
    const distance = Math.abs(boundary.x - eventX);
    if (!closest || distance < closest.distance) {
      return { distance, x: boundary.x };
    }
    return closest;
  }, null);

  return closestBoundary && closestBoundary.distance <= 18 ? closestBoundary.x : eventX;
}

function splitMeasurementsOnGaps(measurements: TrendMeasurement[]) {
  const segments: TrendMeasurement[][] = [];
  let current: TrendMeasurement[] = [];
  measurements.forEach((measurement, index) => {
    const previous = measurements[index - 1];
    if (previous && daysBetween(previous.date, measurement.date) > 10) {
      if (current.length) segments.push(current);
      current = [];
    }
    current.push(measurement);
  });
  if (current.length) segments.push(current);
  return segments;
}

function buildMissingRanges(
  measurements: TrendMeasurement[],
  startDate: string,
  endDate: string,
) {
  const expectedIntervalDays = 7;
  const missingThresholdDays = 10;

  if (!measurements.length) return [{ start: startDate, end: endDate }];

  const ranges: Array<{ start: string; end: string }> = [];
  const firstMeasurement = measurements[0];
  const lastMeasurement = measurements[measurements.length - 1];

  if (daysBetween(startDate, firstMeasurement.date) > missingThresholdDays) {
    ranges.push({
      start: startDate,
      end: toDateString(addDays(normalizeDate(firstMeasurement.date), -1)),
    });
  }

  measurements.forEach((measurement, index) => {
    const previous = measurements[index - 1];
    if (!previous || daysBetween(previous.date, measurement.date) <= missingThresholdDays) return;
    ranges.push({
      start: toDateString(addDays(normalizeDate(previous.date), expectedIntervalDays + 1)),
      end: toDateString(addDays(normalizeDate(measurement.date), -1)),
    });
  });

  if (daysBetween(lastMeasurement.date, endDate) > missingThresholdDays) {
    ranges.push({
      start: toDateString(addDays(normalizeDate(lastMeasurement.date), expectedIntervalDays + 1)),
      end: endDate,
    });
  }

  return ranges;
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

function formatDecimal(value: string | number) {
  const numeric = typeof value === 'number' ? value : Number.parseFloat(value);
  if (!Number.isFinite(numeric)) return String(value);
  return Number.isInteger(numeric) ? String(numeric) : numeric.toFixed(1);
}
