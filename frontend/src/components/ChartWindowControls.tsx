import { useRef, type KeyboardEvent, type PointerEvent, type ReactNode } from 'react';

import { translations, type Language } from '../i18n';
import {
  buildScrubberRange,
  chartDayString,
  moveScrubberRange,
  resizeScrubberRange,
  type ScrubberRange,
} from '../utils/chartScrubber';

type Target = 'start' | 'end' | 'window';
type Drag = { pointerId: number; target: Target; x: number; width: number; range: ScrubberRange };


export default function ChartWindowControls({
  action,
  compact = false,
  language,
  title,
  extentStart,
  extentEnd,
  startDate,
  endDate,
  onChange,
}: {
  action?: ReactNode;
  compact?: boolean;
  language: Language;
  title?: string;
  extentStart: string;
  extentEnd: string;
  startDate: string;
  endDate: string;
  onChange: (startDate: string, endDate: string) => void;
}) {
  const trackRef = useRef<HTMLDivElement>(null);
  const dragRef = useRef<Drag | null>(null);
  const range = buildScrubberRange(extentStart, extentEnd, startDate, endDate);
  const text = translations[language];
  const formatter = new Intl.DateTimeFormat(language === 'fr' ? 'fr-FR' : 'en-GB', {
    day: 'numeric', month: 'short', year: 'numeric',
  });
  const formatDay = (day: number) => formatter.format(new Date(`${chartDayString(day)}T00:00:00`));
  const duration = range.extentEnd - range.extentStart;
  const left = `${(range.start - range.extentStart) / duration * 100}%`;
  const right = `${(range.end - range.extentStart) / duration * 100}%`;
  const windowRight = `${(range.extentEnd - range.end) / duration * 100}%`;

  function emit(next: ScrubberRange) {
    if (next.start !== range.start || next.end !== range.end) {
      onChange(chartDayString(next.start), chartDayString(next.end));
    }
  }

  function change(target: Target, origin: ScrubberRange, days: number) {
    return target === 'window'
      ? moveScrubberRange(origin, days)
      : resizeScrubberRange(origin, target, origin[target] + days);
  }

  function pointerDown(event: PointerEvent<HTMLDivElement>, target: Target) {
    event.stopPropagation();
    if (dragRef.current || !event.isPrimary || (event.pointerType === 'mouse' && event.button !== 0)) return;
    const width = trackRef.current?.getBoundingClientRect().width ?? 0;
    if (!width) return;
    dragRef.current = { pointerId: event.pointerId, target, x: event.clientX, width, range };
    event.currentTarget.setPointerCapture(event.pointerId);
  }

  function trackPointerDown(event: PointerEvent<HTMLDivElement>) {
    if (event.target !== event.currentTarget || !event.isPrimary || (event.pointerType === 'mouse' && event.button !== 0)) return;
    const bounds = event.currentTarget.getBoundingClientRect();
    const center = range.extentStart + (event.clientX - bounds.left) / bounds.width * duration;
    const next = moveScrubberRange(range, Math.round(center - (range.start + range.end) / 2));
    emit(next);
    dragRef.current = { pointerId: event.pointerId, target: 'window', x: event.clientX, width: bounds.width, range: next };
    event.currentTarget.setPointerCapture(event.pointerId);
  }

  function pointerMove(event: PointerEvent<HTMLDivElement>) {
    const drag = dragRef.current;
    if (!drag || drag.pointerId !== event.pointerId) return;
    const days = Math.round((event.clientX - drag.x) / drag.width * duration);
    emit(change(drag.target, drag.range, days));
  }

  function pointerEnd(event: PointerEvent<HTMLDivElement>) {
    if (dragRef.current?.pointerId !== event.pointerId) return;
    dragRef.current = null;
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
  }

  function keyDown(event: KeyboardEvent<HTMLDivElement>, target: Target) {
    let next: ScrubberRange;
    if (event.key === 'Home' || event.key === 'End') {
      const atEnd = event.key === 'End';
      next = target === 'window'
        ? moveScrubberRange(range, (atEnd ? range.extentEnd - (range.end - range.start) : range.extentStart) - range.start)
        : resizeScrubberRange(range, target, target === 'start'
          ? (atEnd ? range.end - 1 : range.extentStart)
          : (atEnd ? range.extentEnd : range.start + 1));
    } else if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') {
      next = change(target, range, (event.key === 'ArrowRight' ? 1 : -1) * (event.shiftKey ? 7 : 1));
    } else {
      return;
    }
    event.preventDefault();
    emit(next);
  }

  const pointerHandlers = {
    onPointerMove: pointerMove,
    onPointerUp: pointerEnd,
    onPointerCancel: pointerEnd,
    onLostPointerCapture: pointerEnd,
  };

  return (
    <header className={`chart-window-header${compact ? ' is-compact' : ''}`}>
      {title ? <h2>{title}</h2> : null}
      <div className="chart-window-navigation">
        <div className="chart-window-range">
          <small>{text.chartDisplayedPeriod}</small>
          <div className="chart-window-dates">
            <time dateTime={chartDayString(range.start)}>{formatDay(range.start)}</time>
            <span>{text.chartPeriodTo}</span>
            <time dateTime={chartDayString(range.end)}>{formatDay(range.end)}</time>
          </div>
        </div>
        <div className="chart-scrubber" role="group" aria-label={text.chartDisplayedPeriod}>
          <div ref={trackRef} className="chart-scrubber-track" onPointerDown={trackPointerDown} {...pointerHandlers}>
            <div
              className="chart-scrubber-window"
              style={{ left, right: windowRight }}
              role="slider"
              tabIndex={0}
              aria-label={text.chartMovePeriod}
              aria-valuemin={range.extentStart}
              aria-valuemax={range.extentEnd - (range.end - range.start)}
              aria-valuenow={range.start}
              aria-valuetext={`${formatDay(range.start)} ${text.chartPeriodTo} ${formatDay(range.end)}`}
              onKeyDown={(event) => keyDown(event, 'window')}
              onPointerDown={(event) => pointerDown(event, 'window')}
              {...pointerHandlers}
            />
            {(['start', 'end'] as const).map((edge) => (
              <div
                key={edge}
                className={`chart-scrubber-thumb is-${edge}`}
                style={{ left: edge === 'start' ? left : right }}
                role="slider"
                tabIndex={0}
                aria-label={edge === 'start' ? text.chartStartPeriod : text.chartEndPeriod}
                aria-valuemin={edge === 'start' ? range.extentStart : range.start + 1}
                aria-valuemax={edge === 'start' ? range.end - 1 : range.extentEnd}
                aria-valuenow={range[edge]}
                aria-valuetext={formatDay(range[edge])}
                onKeyDown={(event) => keyDown(event, edge)}
                onPointerDown={(event) => pointerDown(event, edge)}
                {...pointerHandlers}
              />
            ))}
          </div>
          <div className="chart-scrubber-extent">
            <span>{text.chartTotalPeriod}</span>
            <time dateTime={chartDayString(range.extentStart)}>{formatDay(range.extentStart)}</time>
            <span>{text.chartPeriodTo}</span>
            <time dateTime={chartDayString(range.extentEnd)}>{formatDay(range.extentEnd)}</time>
          </div>
        </div>
      </div>
      {action ? <div className="chart-window-action">{action}</div> : null}
    </header>
  );
}
