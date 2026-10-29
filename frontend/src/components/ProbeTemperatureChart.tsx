import { type KeyboardEvent, type PointerEvent, useEffect, useId, useMemo, useRef, useState } from 'react';
import { scaleLinear, scaleTime } from 'd3-scale';
import { area, line } from 'd3-shape';

import type { ProbeReadingPoint, ProbeReadingSource } from '../types';
import { formatTemperature } from '../utils/environmentFormat';
import {
  nearestProbePointIndex,
  probeCoveragePoints, probeCoverageAt, summarizeProbeCoverage,
  splitProbeSegments,
  summarizeProbePoints,
  type ProbeWindow,
} from '../utils/probeChart';

import { pinchProbeWindow, probeCursorStep, probePointerFraction, probeWheelFactor, zoomProbeAt } from '../utils/probeGestures';

type ChartLabels = {
  target: string;
  minimum: string;
  maximum: string;
  noReadings: string;
  latest: string;
  aggregateAverage?: string;
  partialCoverage?: string;
  previewCoverage?: string;
  aggregateCoverage?: string;
  measurementCount?: string;
  rawReading?: string;
  keyboardDescription: string;
  summary: (minimum: string, maximum: string) => string;
};

const MARGIN = { top: 12, right: 14, bottom: 26, left: 42 };
const DRAG_THRESHOLD_PX = 8;

export default function ProbeTemperatureChart({
  code,
  locale,
  labels,
  onZoom,
  onResetZoom,
  cursorTime,
  onCursorTimeChange,
  points,
  pointSources,
  resolutionMinutes,
  target,
  temperatureDomain,
  window,
  isInitialLoading = false,
  isPartialPreview = false,
}: {
  code: string;
  locale: string;
  labels: ChartLabels;
  onZoom: (start: number, end: number) => void;
  onResetZoom: () => void;
  cursorTime: number | null;
  onCursorTimeChange: (time: number | null) => void;
  points: ProbeReadingPoint[];
  pointSources?: ProbeReadingSource[][];
  resolutionMinutes: number;
  target: number | null;
  temperatureDomain: [number, number] | null;
  window: ProbeWindow;
  isInitialLoading?: boolean;
  isPartialPreview?: boolean;
}) {
  const canvasRef = useRef<HTMLDivElement>(null);
  const clipId = useId();
  const svgRef = useRef<SVGSVGElement>(null);
  const pointerInside = useRef(false);
  const keyboardCursor = useRef(false);
  const [width, setWidth] = useState(0);
  const [drag, setDrag] = useState<{ from: number; to: number } | null>(null);
  const pointers = useRef(new Map<number, { x: number; clientX: number; clientY: number }>());
  const gesture = useRef<
    | { kind: 'drag'; window: ProbeWindow; from: number; startX: number; startY: number; moved: boolean }
    | { kind: 'pinch'; window: ProbeWindow; ids: number[]; initial: number[]; distance: number }
    | null
  >(null);
  const lastTouch = useRef(Number.NEGATIVE_INFINITY);
  const lastTap = useRef<{ time: number; x: number; y: number } | null>(null);
  const latest = useRef({ window, onZoom, onCursorTimeChange, width });
  latest.current = { window, onZoom, onCursorTimeChange: setCursorAt, width };

  useEffect(() => {
    const svg = svgRef.current;
    if (!svg) return;
    function wheel(event: WheelEvent) {
      // Scrolling can move the SVG under a stationary pointer; that is not a zoom gesture.
      if (!event.deltaY || !pointerInside.current) return;
      const bounds = svg!.getBoundingClientRect();
      if (event.clientX < bounds.left || event.clientX > bounds.right
        || event.clientY < bounds.top || event.clientY > bounds.bottom) return;
      event.preventDefault();
      if (pointers.current.size) return;
      const current = latest.current;
      const fraction = probePointerFraction(event.clientX - bounds.left, MARGIN.left, current.width - MARGIN.right);
      const time = current.window.start + fraction * (current.window.end - current.window.start);
      const next = zoomProbeAt(current.window, time, probeWheelFactor(event.deltaY, event.deltaMode, bounds.height));
      current.onCursorTimeChange(time);
      current.onZoom(next.start, next.end);
    }
    svg.addEventListener('wheel', wheel, { passive: false });
    return () => svg.removeEventListener('wheel', wheel);
  }, []);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return undefined;
    setWidth(canvas.clientWidth);
    const observer = new ResizeObserver(([entry]) => setWidth(Math.round(entry.contentRect.width)));
    observer.observe(canvas);
    return () => observer.disconnect();
  }, []);

  const coverage = useMemo(() => pointSources ? probeCoveragePoints(points, pointSources, window) : null,
    [points, pointSources, window.start, window.end]);
  const rawSources = coverage?.flatMap((entry) => entry.sources.filter((source) => source[1] === null)) ?? [];
  const intervals = coverage?.flatMap((entry) => entry.sources.filter((source) => source[1] !== null).map((source) => ({ source, partial: source[0] < window.start || source[1]! > window.end }))) ?? [];
  const height = width < 520 ? 240 : 300;
  const geometry = useMemo(() => {
    const chartWidth = Math.max(width, MARGIN.left + MARGIN.right + 1);
    const x = scaleTime()
      .domain([new Date(window.start), new Date(window.end)])
      .range([MARGIN.left, chartWidth - MARGIN.right]);
    const y = scaleLinear()
      .domain(temperatureDomain ?? [0, 1])
      .nice(5)
      .range([height - MARGIN.bottom, MARGIN.top]);
    const segments = coverage === null ? splitProbeSegments(points, resolutionMinutes)
      : resolutionMinutes === 1 && coverage.every((entry) => !entry.aggregate)
        ? splitProbeSegments(rawSources.map((source): ProbeReadingPoint => [source[0], Number(source[2]), null, null]), 1) : [];
    const linePath = line<ProbeReadingPoint>()
      .x((point) => x(point[0]))
      .y((point) => y(point[1]));
    const bandPath = area<ProbeReadingPoint>()
      .x((point) => x(point[0]))
      .y0((point) => y(point[2] ?? point[1]))
      .y1((point) => y(point[3] ?? point[1]));
    const hasBand = points.some((point) => point[2] !== null && point[3] !== null);
    const span = window.end - window.start;
    const timeFormat = new Intl.DateTimeFormat(locale, span <= 2 * 86400000
      ? { hour: '2-digit', minute: '2-digit' }
      : { day: 'numeric', month: 'short' });
    const dayFormat = new Intl.DateTimeFormat(locale, { day: 'numeric', month: 'short' });
    let previousDay = '';
    const timeTicks = x.ticks(Math.max(2, Math.floor(width / 110))).map((tick) => {
      const day = dayFormat.format(tick);
      const showDay = span <= 2 * 86400000 && day !== previousDay && previousDay !== '';
      previousDay = day;
      return { x: x(tick), label: showDay ? `${day} ${timeFormat.format(tick)}` : timeFormat.format(tick) };
    });
    return {
      x,
      y,
      timeTicks,
      valueTicks: y.ticks(4),
      lines: segments.map((segment) => linePath(segment) ?? ''),
      bands: hasBand ? segments.map((segment) => bandPath(segment) ?? '') : [],
      isolatedPoints: segments.filter((segment) => segment.length === 1).map((segment) => segment[0]),
    };
  }, [height, locale, points, coverage, resolutionMinutes, temperatureDomain, width, window.end, window.start]);

  const coverageSummary = coverage ? summarizeProbeCoverage(coverage) : null;
  const summary = coverage === null ? summarizeProbePoints(points) : coverageSummary;
  const exactFormat = new Intl.DateTimeFormat(locale, { dateStyle: 'medium', timeStyle: 'short' });
  const cursorVisible = cursorTime !== null && cursorTime >= window.start && cursorTime <= window.end;
  const nearestIndex = cursorVisible && cursorTime !== null
    ? nearestProbePointIndex(points, cursorTime) : -1;
  const candidate = nearestIndex >= 0 ? points[nearestIndex] : null;
  const hoveredCoverage = coverage && cursorVisible && cursorTime !== null ? probeCoverageAt(coverage, cursorTime) : null;
  const hovered = coverage === null
    ? candidate && cursorTime !== null && Math.abs(candidate[0] - cursorTime) < 1 ? candidate : null
    : hoveredCoverage?.point ?? null;

  function setCursorAt(time: number) {
    if (coverage !== null) {
      const raw = rawSources.reduce<ProbeReadingSource | null>((nearest, source) =>
        !nearest || Math.abs(source[0] - time) < Math.abs(nearest[0] - time) ? source : nearest, null);
      onCursorTimeChange(raw && Math.abs(raw[0] - time) <= 30000 ? raw[0] : time);
      return;
    }
    const index = nearestProbePointIndex(points, time);
    const point = index >= 0 ? points[index] : null;
    const instant = point && Math.abs(point[0] - time) <= resolutionMinutes * 30000 ? point[0] : time;
    onCursorTimeChange(instant);
  }

  function pointerX(event: PointerEvent<SVGSVGElement>) {
    const bounds = event.currentTarget.getBoundingClientRect();
    return Math.min(Math.max(event.clientX - bounds.left, MARGIN.left), width - MARGIN.right);
  }

  function fraction(x: number) {
    return probePointerFraction(x, MARGIN.left, width - MARGIN.right);
  }

  function handlePointerDown(event: PointerEvent<SVGSVGElement>) {
    keyboardCursor.current = false;
    if (event.pointerType === 'mouse' && event.button !== 0) return;
    if (event.pointerType === 'mouse') pointerInside.current = true;
    if (event.pointerType === 'touch') lastTouch.current = event.timeStamp;
    event.currentTarget.focus({ preventScroll: true });
    event.currentTarget.setPointerCapture(event.pointerId);
    const x = pointerX(event);
    pointers.current.set(event.pointerId, { x, clientX: event.clientX, clientY: event.clientY });
    if (pointers.current.size === 1) {
      gesture.current = { kind: 'drag', window: { ...latest.current.window }, from: x,
        startX: event.clientX, startY: event.clientY, moved: false };
      setDrag({ from: x, to: x });
      setCursorAt(geometry.x.invert(x).getTime());
    } else if (pointers.current.size === 2) {
      const entries = [...pointers.current.entries()];
      gesture.current = { kind: 'pinch', window: { ...latest.current.window },
        ids: entries.map(([id]) => id), initial: entries.map(([, position]) => fraction(position.x)),
        distance: Math.hypot(entries[1][1].clientX - entries[0][1].clientX, entries[1][1].clientY - entries[0][1].clientY) };
      lastTap.current = null;
      setDrag(null);
    }
  }

  function handlePointerMove(event: PointerEvent<SVGSVGElement>) {
    keyboardCursor.current = false;
    if (event.pointerType === 'mouse') pointerInside.current = true;
    const x = pointerX(event);
    if (pointers.current.has(event.pointerId)) pointers.current.set(event.pointerId, { x, clientX: event.clientX, clientY: event.clientY });
    const active = gesture.current;
    if (active?.kind === 'pinch') {
      const [a, b] = active.ids.map((id) => pointers.current.get(id));
      if (a !== undefined && b !== undefined) {
        const next = pinchProbeWindow(active.window, active.initial[0], active.initial[1], fraction(a.x), fraction(b.x),
          active.distance, Math.hypot(b.clientX - a.clientX, b.clientY - a.clientY));
        onZoom(next.start, next.end);
      }
      return;
    }
    if (active?.kind === 'drag') {
      active.moved ||= Math.hypot(event.clientX - active.startX, event.clientY - active.startY) >= DRAG_THRESHOLD_PX;
      setDrag({ from: active.from, to: x });
    }
    if (event.pointerType === 'mouse' || pointers.current.has(event.pointerId)) setCursorAt(geometry.x.invert(x).getTime());
  }

  function finishPointer(event: PointerEvent<SVGSVGElement>, cancelled = false) {
    if (!pointers.current.has(event.pointerId)) return;
    pointers.current.delete(event.pointerId);
    if (event.pointerType === 'touch') lastTouch.current = event.timeStamp;
    const active = gesture.current;
    if (active?.kind === 'drag' && !cancelled) {
      const x = pointerX(event);
      if (Math.abs(x - active.from) >= DRAG_THRESHOLD_PX) {
        const duration = active.window.end - active.window.start;
        onZoom(active.window.start + fraction(Math.min(active.from, x)) * duration,
          active.window.start + fraction(Math.max(active.from, x)) * duration);
        lastTap.current = null;
      } else if (!active.moved && event.pointerType === 'touch') {
        const previous = lastTap.current;
        const time = event.timeStamp;
        if (previous && time - previous.time < 350 && Math.hypot(event.clientX - previous.x, event.clientY - previous.y) < 24) {
          onResetZoom();
          lastTap.current = null;
        } else lastTap.current = { time, x: event.clientX, y: event.clientY };
      } else lastTap.current = null;
    }
    if (cancelled) lastTap.current = null;
    if (!pointers.current.size) gesture.current = null;
    setDrag(null);
    if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
  }

  function handleKeyDown(event: KeyboardEvent<SVGSVGElement>) {
    if (event.altKey || event.ctrlKey || event.metaKey) return;
    if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') {
      keyboardCursor.current = true;
      event.preventDefault();
      if (coverage !== null) {
        const anchors = [...new Set(coverage.flatMap((entry) => entry.anchors))].sort((a, b) => a - b);
        const next = event.key === 'ArrowRight'
          ? anchors.find((time) => cursorTime === null || time > cursorTime)
          : [...anchors].reverse().find((time) => cursorTime === null || time < cursorTime);
        if (next !== undefined) onCursorTimeChange(next);
      } else setCursorAt(probeCursorStep(window, cursorTime, event.key === 'ArrowLeft' ? -1 : 1, resolutionMinutes));
    } else if (event.key === '+' || event.key === '=' || event.key === '-') {
      keyboardCursor.current = true;
      event.preventDefault();
      const anchor = cursorVisible && cursorTime !== null ? cursorTime : (window.start + window.end) / 2;
      const next = zoomProbeAt(window, anchor, event.key === '-' ? 1.5 : 1 / 1.5);
      onZoom(next.start, next.end);
    }
  }

  const coverageWarnings = [coverageSummary?.partial ? labels.partialCoverage : null,
    isPartialPreview ? labels.previewCoverage : null].filter(Boolean).join(' ');
  const ariaSummary = `${code}. ${coverageWarnings ? `${coverageWarnings} ` : ''}${summary
    ? coverageWarnings
      ? `${labels.minimum} ${formatTemperature(summary.minimum)}, ${labels.maximum} ${formatTemperature(summary.maximum)}`
      : labels.summary(formatTemperature(summary.minimum), formatTemperature(summary.maximum))
    : labels.noReadings}`;
  const hoveredX = cursorVisible && cursorTime !== null ? geometry.x(cursorTime) : null;

  return (
    <article className="probe-chart">
      <header className="probe-chart-heading">
        <h3>{code}</h3>
        {summary ? (
          <dl className="probe-chart-facts">
            <div>
              <dt>{coverageSummary?.latestAggregate ? labels.aggregateAverage : labels.latest}</dt>
              <dd>{formatTemperature(summary.latest[1])}</dd>
            </div>
            <div>
              <dt>{labels.minimum}</dt>
              <dd>{formatTemperature(summary.minimum)}</dd>
            </div>
            <div>
              <dt>{labels.maximum}</dt>
              <dd>{formatTemperature(summary.maximum)}</dd>
            </div>
          </dl>
        ) : null}
      </header>
      {coverageWarnings ? <p className="probe-chart-coverage-note">{coverageWarnings}</p> : null}
      <div className="probe-chart-canvas" ref={canvasRef}>
        <svg
          ref={svgRef}
          className="probe-chart-svg"
          tabIndex={0}
          aria-description={labels.keyboardDescription}
          onKeyDown={handleKeyDown}
          onDoubleClick={(event) => { if (event.timeStamp - lastTouch.current > 500) onResetZoom(); }}
          width={width}
          height={height}
          role="img"
          aria-label={ariaSummary}
          onPointerDown={handlePointerDown}
          onPointerMove={handlePointerMove}
          onPointerUp={(event) => finishPointer(event)}
          onPointerCancel={(event) => finishPointer(event, true)}
          onLostPointerCapture={(event) => finishPointer(event, true)}
          onPointerLeave={(event) => {
            const related = event.relatedTarget as Element | null;
            if (related && typeof related.closest === 'function' && related.closest('.probe-chart-tooltip')) return;
            if (event.pointerType === 'mouse') {
              pointerInside.current = false;
              if (!gesture.current && !canvasRef.current?.contains?.(event.relatedTarget as Node | null)) onCursorTimeChange(null);
            }
          }}
        >
          {geometry.valueTicks.map((tick) => (
            <g key={`y-${tick}`} className="probe-chart-value-tick">
              <line x1={MARGIN.left} x2={width - MARGIN.right} y1={geometry.y(tick)} y2={geometry.y(tick)} />
              <text x={MARGIN.left - 6} y={geometry.y(tick)} dy="0.32em" textAnchor="end">
                {tick.toLocaleString(locale)}
              </text>
            </g>
          ))}
          {geometry.timeTicks.map((tick) => (
            <text
              key={`x-${tick.x}`}
              className="probe-chart-time-tick"
              x={tick.x}
              y={height - 8}
              textAnchor="middle"
            >
              {tick.label}
            </text>
          ))}
          {target !== null && target >= geometry.y.domain()[0] && target <= geometry.y.domain()[1] ? (
            <g className="probe-chart-target">
              <line x1={MARGIN.left} x2={width - MARGIN.right} y1={geometry.y(target)} y2={geometry.y(target)} />
              <text x={width - MARGIN.right - 4} y={geometry.y(target) - 5} textAnchor="end">
                {labels.target} {formatTemperature(target)}
              </text>
            </g>
          ) : null}
          <defs><clipPath id={clipId}><rect x={MARGIN.left} y={MARGIN.top} width={Math.max(0, width - MARGIN.left - MARGIN.right)} height={height - MARGIN.top - MARGIN.bottom} /></clipPath></defs>
          <g clipPath={"url(#" + clipId + ")"}>
            {intervals.map(({ source, partial }, index) => (
              <g key={`interval-${index}`} className={`probe-chart-interval${partial ? ' is-partial' : ''}`}>
                <rect className="probe-chart-band" x={geometry.x(source[0])} width={Math.max(0, geometry.x(source[1]!) - geometry.x(source[0]))}
                  y={geometry.y(source[5])} height={Math.max(1, geometry.y(source[4]) - geometry.y(source[5]))} />
                <line x1={geometry.x(source[0])} x2={geometry.x(source[1]!)}
                  y1={geometry.y(Number(source[2]) / source[3])} y2={geometry.y(Number(source[2]) / source[3])} />
              </g>
            ))}
            {rawSources.map((source, index) => (
              <circle key={`raw-${index}`} className="probe-chart-point" cx={geometry.x(source[0])} cy={geometry.y(Number(source[2]))} r={2.5} />
            ))}
            {geometry.bands.map((path, index) => (
              <path key={`band-${index}`} className="probe-chart-band" d={path} />
            ))}
            {geometry.lines.map((path, index) => (
              <path key={`line-${index}`} className="probe-chart-line" d={path} />
            ))}
            {coverage === null && geometry.isolatedPoints.map((point) => (
              <circle key={point[0]} className="probe-chart-point" cx={geometry.x(point[0])} cy={geometry.y(point[1])} r={2.5} />
            ))}
            {hoveredX !== null ? (
              <g className="probe-chart-hover" aria-hidden="true">
                <line x1={hoveredX} x2={hoveredX} y1={MARGIN.top} y2={height - MARGIN.bottom} />
                {hovered && !hoveredCoverage?.aggregate ? <circle cx={geometry.x(hoveredCoverage?.sources[0][0] ?? hovered[0])} cy={geometry.y(hoveredCoverage ? Number(hoveredCoverage.sources[0][2]) : hovered[1])} r={4} /> : null}
              </g>
            ) : null}
            {drag && Math.abs(drag.to - drag.from) >= DRAG_THRESHOLD_PX ? (
              <rect
                className="probe-chart-selection"
                x={Math.min(drag.from, drag.to)}
                y={MARGIN.top}
                width={Math.abs(drag.to - drag.from)}
                height={height - MARGIN.top - MARGIN.bottom}
              />
            ) : null}
          </g>
        </svg>
        {!summary && !isInitialLoading ? <p className="probe-chart-empty">{labels.noReadings}</p> : null}
        {hovered && hoveredX !== null && !drag ? (
          <div
            className="probe-chart-tooltip"
            style={{ left: Math.min(Math.max(10, hoveredX + 10), Math.max(10, width - 240)) }}
            aria-hidden={hoveredCoverage ? undefined : 'true'}
            tabIndex={hoveredCoverage ? 0 : undefined}
            role={hoveredCoverage ? 'region' : undefined}
            aria-label={hoveredCoverage ? (hoveredCoverage.aggregate ? labels.aggregateCoverage : labels.rawReading) : undefined}
            onPointerLeave={(event) => {
              if (event.pointerType === 'mouse' && !canvasRef.current?.contains?.(event.relatedTarget as Node | null)) onCursorTimeChange(null);
            }}
          >
            {hoveredCoverage ? <>
              <small>{hoveredCoverage.aggregate ? labels.aggregateCoverage : labels.rawReading}</small>
              {hoveredCoverage.sources.map(([start, end], index) => <small key={index}>
                {exactFormat.format(start)}{end !== null ? ` – ${exactFormat.format(end)}` : ''}
              </small>)}
              <span>{labels.measurementCount}: {hoveredCoverage.count}</span>
              {hoveredCoverage.aggregate ? <span>{labels.aggregateAverage}</span> : null}
              {hoveredCoverage.partial ? <span>{labels.partialCoverage}</span> : null}
              {isPartialPreview ? <span>{labels.previewCoverage}</span> : null}
            </> : <small>{exactFormat.format(cursorTime!)}</small>}
            <strong>{hovered ? formatTemperature(hovered[1]) : labels.noReadings}</strong>
            {hovered && hovered[2] !== null && hovered[3] !== null ? (
              <span>
                {labels.minimum} {formatTemperature(hovered[2])}, {labels.maximum} {formatTemperature(hovered[3])}
              </span>
            ) : null}
          </div>
        ) : null}
      </div>
      <span className="sr-only" role="status">
        {keyboardCursor.current && cursorVisible && cursorTime !== null
          ? `${code}. ${isPartialPreview ? labels.previewCoverage : ''} ${hoveredCoverage ? `${hoveredCoverage.aggregate ? labels.aggregateAverage : labels.rawReading}. ${hoveredCoverage.sources.map(([start, end]) => `${exactFormat.format(start)}${end === null ? '' : ` – ${exactFormat.format(end)}`}`).join('; ')}. ${labels.measurementCount}: ${hoveredCoverage.count}. ${hoveredCoverage.partial ? labels.partialCoverage : ''}` : exactFormat.format(cursorTime)} — ${hovered ? formatTemperature(hovered[1]) : labels.noReadings}`
          : ''}
      </span>
    </article>
  );
}
