import { type KeyboardEvent, type PointerEvent, useEffect, useId, useMemo, useRef, useState } from 'react';
import { scaleLinear, scaleTime } from 'd3-scale';
import { line } from 'd3-shape';

import { formatSignedTemperature, formatTemperature } from '../utils/environmentFormat';
import type { ProbeWindow } from '../utils/probeChart';
import {
  binIntervalsForPixels, reduceReadingsForPixels, seriesEntryAt, SERIES_STYLE_COUNT, snapCursorTime,
  splitReadingsAtGaps, stepCursorTime, type ProbeAveragePeriod, type ProbeSeries, type RawReading,
} from '../utils/probeSeries';
import { zoomProbeAt } from '../utils/probeGestures';

const MARGIN = { top: 22, right: 16, bottom: 26, left: 44 };
const MIN_TIME_TICK_SPACING_PX = 56;
const DRAG_THRESHOLD_PX = 8;

export type ProbeSeriesChartLabels = {
  target: string;
  mean: string;
  minimum: string;
  maximum: string;
  unit: string;
  keyboardDescription?: string;
  average?: string;
  averageContributors?: string;
};

export function seriesClassName(slot: number) {
  return `probe-series-${(slot % SERIES_STYLE_COUNT) + 1}`;
}

export default function ProbeSeriesChart({
  ariaLabel, average = [], domain, height, hiddenIds, labels, locale, onResetZoom, onZoom, series, target, window,
}: {
  ariaLabel: string;
  average?: readonly ProbeAveragePeriod[];
  domain: [number, number] | null;
  height: number;
  hiddenIds?: ReadonlySet<number>;
  labels: ProbeSeriesChartLabels;
  locale: string;
  /** Omit both callbacks for a read-only chart. */
  onResetZoom?: () => void;
  onZoom?: (start: number, end: number) => void;
  series: readonly ProbeSeries[];
  target: number | null;
  window: ProbeWindow;
}) {
  const canvasRef = useRef<HTMLDivElement>(null);
  const clipId = useId();
  const [width, setWidth] = useState(0);
  const [cursorTime, setCursorTime] = useState<number | null>(null);
  const [drag, setDrag] = useState<{ from: number; to: number } | null>(null);
  const dragOrigin = useRef<number | null>(null);
  const [isKeyboardCursor, setIsKeyboardCursor] = useState(false);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return undefined;
    setWidth(canvas.clientWidth);
    const observer = new ResizeObserver(([entry]) => setWidth(Math.round(entry.contentRect.width)));
    observer.observe(canvas);
    return () => observer.disconnect();
  }, []);

  const axes = useMemo(() => {
    const chartWidth = Math.max(width, MARGIN.left + MARGIN.right + 1);
    const x = scaleTime().domain([new Date(window.start), new Date(window.end)]).range([MARGIN.left, chartWidth - MARGIN.right]);
    const y = scaleLinear().domain(domain ?? [0, 1]).nice(5).range([height - MARGIN.bottom, MARGIN.top]);
    const span = window.end - window.start;
    const tickFormat = new Intl.DateTimeFormat(locale, span <= 2 * 86_400_000
      ? { hour: '2-digit', minute: '2-digit' }
      : { day: 'numeric', month: 'short' });
    const dayFormat = new Intl.DateTimeFormat(locale, { day: 'numeric', month: 'short' });
    let previousDay = '';
    const timeTicks = x.ticks(Math.max(2, Math.floor(chartWidth / 110))).map((tick) => {
      const day = dayFormat.format(tick);
      const showDay = span <= 2 * 86_400_000 && previousDay !== '' && day !== previousDay;
      previousDay = day;
      return { x: x(tick), label: showDay ? `${day} ${tickFormat.format(tick)}` : tickFormat.format(tick) };
    }).filter((tick, index, ticks) => index === 0 || tick.x - ticks[index - 1].x >= MIN_TIME_TICK_SPACING_PX);
    return { x, y, timeTicks, valueTicks: y.ticks(height < 200 ? 3 : 5) };
  }, [domain, height, locale, width, window.end, window.start]);

  // Paths depend on data and geometry only, so moving the cursor does not rebuild them.
  const drawn = useMemo(() => {
    const projectX = (time: number) => axes.x(time);
    const linePath = line<RawReading>().x((reading) => axes.x(reading[0])).y((reading) => axes.y(reading[1]));
    return series.map((entry) => {
      const segments = splitReadingsAtGaps(entry.readings, entry.cadenceMs);
      const path = segments.filter((segment) => segment.length > 1)
        .map((segment) => linePath(reduceReadingsForPixels(segment, projectX)) ?? '').join('');
      let band = '';
      let mean = '';
      let previousEnd: number | null = null;
      for (const bin of binIntervalsForPixels(entry.intervals, projectX)) {
        const left = axes.x(bin.start);
        const right = Math.max(axes.x(bin.end), left + 1);
        band += `M${left},${axes.y(bin.maximum)}H${right}V${axes.y(bin.minimum)}H${left}Z`;
        mean += `${previousEnd === bin.start ? 'V' : `M${left},`}${axes.y(bin.mean)}H${right}`;
        previousEnd = bin.end;
      }
      const dots = segments.filter((segment) => segment.length === 1).map((segment) => segment[0]);
      const lastReading = entry.readings[entry.readings.length - 1];
      return { entry, path, band, mean, dots, lastReading };
    });
  }, [axes, series]);

  const averagePath = useMemo(() => {
    let path = '';
    let previousEnd: number | null = null;
    // Do not pixel-bin server means: that would invent a new inter-probe average.
    for (const period of average) {
      path += `${previousEnd === period.start ? 'V' : `M${axes.x(period.start)},`}${axes.y(period.mean)}H${axes.x(period.end)}`;
      previousEnd = period.end;
    }
    return path;
  }, [average, axes]);

  const visible = useMemo(() => series.filter((entry) => !hiddenIds?.has(entry.id)), [hiddenIds, series]);
  const dateTimeFormat = useMemo(() => new Intl.DateTimeFormat(locale, { dateStyle: 'medium', timeStyle: 'short' }), [locale]);
  const timeFormat = useMemo(() => new Intl.DateTimeFormat(locale, { hour: '2-digit', minute: '2-digit' }), [locale]);

  const cursorInWindow = cursorTime !== null && cursorTime >= window.start && cursorTime < window.end;
  const entries = cursorInWindow
    ? visible.flatMap((entry) => { const found = seriesEntryAt(entry, cursorTime); return found ? [found] : []; })
    : [];
  const averageEntry = cursorInWindow ? average.find((period) => cursorTime >= period.start && cursorTime < period.end) : undefined;
  const averagePeriod = averageEntry
    ? `${dateTimeFormat.format(averageEntry.start)} → ${dateTimeFormat.format(averageEntry.end)}` : '';
  const averageValue = averageEntry
    ? `${formatTemperature(averageEntry.mean)}, ${(labels.averageContributors ?? '').replace('{count}', String(averageEntry.contributors))}` : '';
  const condensed = entries.find((entry) => entry.kind === 'condensed');
  const heading = condensed?.kind === 'condensed'
    ? `${dateTimeFormat.format(condensed.interval.start)} → ${dateTimeFormat.format(condensed.interval.end)}`
    : cursorInWindow ? dateTimeFormat.format(cursorTime) : '';
  const cursorX = cursorInWindow ? axes.x(cursorTime) : null;
  const canZoom = onZoom !== undefined;
  const targetVisible = target !== null && target >= axes.y.domain()[0] && target <= axes.y.domain()[1];

  function rowValue(entry: (typeof entries)[number]) {
    if (entry.kind === 'condensed') {
      return `${labels.mean} ${formatTemperature(entry.interval.mean)}, ${labels.minimum} ${formatTemperature(entry.interval.minimum)}, ${labels.maximum} ${formatTemperature(entry.interval.maximum)}`;
    }
    const gap = target === null ? '' : ` (${formatSignedTemperature(entry.value - target)})`;
    return `${formatTemperature(entry.value)}${gap}`;
  }

  function pointerX(event: PointerEvent<SVGSVGElement>) {
    const bounds = event.currentTarget.getBoundingClientRect();
    return Math.min(Math.max(event.clientX - bounds.left, MARGIN.left), width - MARGIN.right);
  }

  function moveCursor(x: number) {
    setIsKeyboardCursor(false);
    const time = axes.x.invert(x).getTime();
    setCursorTime(average.length ? time : snapCursorTime(visible, time));
  }

  function handlePointerDown(event: PointerEvent<SVGSVGElement>) {
    if (event.pointerType === 'mouse' && event.button !== 0) return;
    const x = pointerX(event);
    moveCursor(x);
    if (!canZoom) return;
    dragOrigin.current = x;
    setDrag({ from: x, to: x });
    event.currentTarget.setPointerCapture?.(event.pointerId);
  }

  function handlePointerMove(event: PointerEvent<SVGSVGElement>) {
    const x = pointerX(event);
    if (dragOrigin.current !== null) setDrag({ from: dragOrigin.current, to: x });
    if (event.pointerType === 'mouse' || dragOrigin.current !== null) moveCursor(x);
  }

  function finishDrag(event: PointerEvent<SVGSVGElement>, cancelled = false) {
    const origin = dragOrigin.current;
    dragOrigin.current = null;
    setDrag(null);
    if (event.currentTarget.hasPointerCapture?.(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
    if (origin === null || cancelled || !onZoom) return;
    const x = pointerX(event);
    if (Math.abs(x - origin) < DRAG_THRESHOLD_PX) return;
    onZoom(axes.x.invert(Math.min(origin, x)).getTime(), axes.x.invert(Math.max(origin, x)).getTime());
    setCursorTime(null);
  }

  function handleKeyDown(event: KeyboardEvent<SVGSVGElement>) {
    if (event.altKey || event.ctrlKey || event.metaKey) return;
    if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') {
      event.preventDefault();
      const from = cursorInWindow ? cursorTime : null;
      const direction = event.key === 'ArrowLeft' ? -1 : 1;
      const probeTime = stepCursorTime(visible, from, direction);
      const candidates = average.map((period) => period.start)
        .filter((time) => from === null || (direction > 0 ? time > from : time < from));
      if (probeTime !== null) candidates.push(probeTime);
      const next = candidates.length ? (direction > 0 ? Math.min(...candidates) : Math.max(...candidates)) : null;
      if (next !== null) { setIsKeyboardCursor(true); setCursorTime(next); }
    } else if (event.key === 'Escape' && cursorTime !== null) {
      setCursorTime(null);
    } else if (canZoom && (event.key === '+' || event.key === '=' || event.key === '-')) {
      event.preventDefault();
      const anchor = cursorInWindow ? cursorTime : (window.start + window.end) / 2;
      const next = zoomProbeAt(window, anchor, event.key === '-' ? 1.5 : 1 / 1.5);
      onZoom(next.start, next.end);
    }
  }

  const announcement = isKeyboardCursor && cursorInWindow
    ? `${heading}. ${entries.map((entry) => `${entry.series.code}: ${rowValue(entry)}`).join('. ')}${averageEntry ? `. ${labels.average}: ${averagePeriod}, ${averageValue}` : ''}`
    : '';
  const flipTooltip = cursorX !== null && cursorX > width / 2;

  return (
    <div className="probe-series-canvas" ref={canvasRef} style={{ height }}>
      <svg
        className={`probe-series-svg${canZoom ? ' is-zoomable' : ''}`}
        width={width}
        height={height}
        role="img"
        aria-label={ariaLabel}
        aria-description={labels.keyboardDescription}
        tabIndex={0}
        onKeyDown={handleKeyDown}
        onDoubleClick={onResetZoom}
        onPointerDown={handlePointerDown}
        onPointerMove={handlePointerMove}
        onPointerUp={(event) => finishDrag(event)}
        onPointerCancel={(event) => finishDrag(event, true)}
        onPointerLeave={(event) => { if (event.pointerType === 'mouse' && dragOrigin.current === null) setCursorTime(null); }}
      >
        {axes.valueTicks.map((tick) => (
          <g key={tick} className="probe-series-grid">
            <line x1={MARGIN.left} x2={width - MARGIN.right} y1={axes.y(tick)} y2={axes.y(tick)} />
            <text x={MARGIN.left - 6} y={axes.y(tick)} dy="0.32em" textAnchor="end">{tick.toLocaleString(locale)}</text>
          </g>
        ))}
        <text className="probe-series-unit" x={MARGIN.left - 6} y={8} textAnchor="end">{labels.unit}</text>
        {axes.timeTicks.map((tick) => (
          <text key={tick.x} className="probe-series-time" x={tick.x} y={height - 8} textAnchor="middle">{tick.label}</text>
        ))}
        <defs>
          <clipPath id={clipId}>
            <rect x={MARGIN.left} y={MARGIN.top} width={Math.max(0, width - MARGIN.left - MARGIN.right)} height={Math.max(0, height - MARGIN.top - MARGIN.bottom)} />
          </clipPath>
        </defs>
        {targetVisible ? (
          <g className="probe-series-target">
            <line x1={MARGIN.left} x2={width - MARGIN.right} y1={axes.y(target)} y2={axes.y(target)} />
          </g>
        ) : null}
        {targetVisible ? (
          // Named above the plot, so the label never covers curves near the target.
          <text className="probe-series-target-key" x={width - MARGIN.right} y={8} dy="0.32em" textAnchor="end">
            {labels.target} {formatTemperature(target)}
          </text>
        ) : null}
        <g clipPath={`url(#${clipId})`}>
          {drawn.filter(({ entry }) => !hiddenIds?.has(entry.id)).map(({ entry, path, band, mean, dots, lastReading }) => (
            <g key={entry.id} className={`probe-series ${seriesClassName(entry.slot)}`}>
              {band ? <path className="probe-series-band" d={band} /> : null}
              {mean ? <path className="probe-series-line" d={mean} /> : null}
              {path ? <path className="probe-series-line" d={path} /> : null}
              {dots.map((reading) => <circle key={reading[0]} className="probe-series-dot" cx={axes.x(reading[0])} cy={axes.y(reading[1])} r={2.5} />)}
              {lastReading ? <circle className="probe-series-dot is-latest" cx={axes.x(lastReading[0])} cy={axes.y(lastReading[1])} r={3.5} /> : null}
            </g>
          ))}
          {averagePath ? <path className="probe-series-average probe-series-average-line" d={averagePath} /> : null}
          {cursorX !== null ? (
            <g className="probe-series-cursor" aria-hidden="true">
              <line x1={cursorX} x2={cursorX} y1={MARGIN.top} y2={height - MARGIN.bottom} />
              {entries.map((entry) => entry.kind === 'raw' ? (
                <circle key={entry.series.id} className={seriesClassName(entry.series.slot)} cx={axes.x(entry.time)} cy={axes.y(entry.value)} r={4} />
              ) : null)}
            </g>
          ) : null}
          {drag && Math.abs(drag.to - drag.from) >= DRAG_THRESHOLD_PX ? (
            <rect className="probe-series-selection" x={Math.min(drag.from, drag.to)} y={MARGIN.top}
              width={Math.abs(drag.to - drag.from)} height={height - MARGIN.top - MARGIN.bottom} />
          ) : null}
        </g>
      </svg>
      {(entries.length > 0 || averageEntry) && cursorX !== null && !drag ? (
        <div className="probe-series-tooltip" aria-hidden="true"
          style={flipTooltip ? { right: Math.max(8, width - cursorX + 12) } : { left: cursorX + 12 }}>
          <small>{heading}</small>
          {entries.map((entry) => (
            <span key={entry.series.id} className={seriesClassName(entry.series.slot)}>
              <i aria-hidden="true" />
              <b>{entry.series.code}</b>
              <strong>{rowValue(entry)}</strong>
              {entry.kind === 'raw' && timeFormat.format(entry.time) !== timeFormat.format(cursorTime!)
                ? <small>{timeFormat.format(entry.time)}</small> : null}
            </span>
          ))}
          {averageEntry ? (
            <span className="probe-series-average">
              <i aria-hidden="true" />
              <b>{labels.average}</b>
              <strong>{averageValue}</strong>
              <small>{averagePeriod}</small>
            </span>
          ) : null}
          {target !== null && !condensed ? <small>{labels.target} {formatTemperature(target)}</small> : null}
        </div>
      ) : null}
      <span className="sr-only" role="status">{announcement}</span>
    </div>
  );
}
