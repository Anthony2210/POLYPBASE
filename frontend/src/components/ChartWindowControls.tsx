import type { ReactNode } from 'react';

import type { Language } from '../i18n';
import { formatDisplayDate } from '../utils/dateFormat';

export default function ChartWindowControls({
  action,
  compact = false,
  endDate,
  hasNewerWindow,
  hasOlderWindow,
  language,
  longStep,
  onMove,
  startDate,
  title,
  windowMonths,
}: {
  action?: ReactNode;
  compact?: boolean;
  endDate: string;
  hasNewerWindow: boolean;
  hasOlderWindow: boolean;
  language: Language;
  longStep: number;
  onMove: (months: number) => void;
  startDate: string;
  title?: string;
  windowMonths: number;
}) {
  const monthLabel = (value: number) => language === 'fr'
    ? `${value} mois`
    : `${value} month${value > 1 ? 's' : ''}`;
  const moveLabel = (direction: 'newer' | 'older', value: number) => language === 'fr'
    ? `${direction === 'older' ? 'Reculer' : 'Avancer'} de ${monthLabel(value)}`
    : `Move ${direction === 'older' ? 'back' : 'forward'} ${monthLabel(value)}`;

  return (
    <header className={`chart-window-header${compact ? ' is-compact' : ''}`}>
      {title ? <h2>{title}</h2> : null}
      <div className="chart-window-navigation" role="group" aria-label={monthLabel(windowMonths)}>
        <WindowButton
          direction="left"
          isLongStep
          accessibleLabel={moveLabel('older', longStep)}
          disabled={!hasOlderWindow}
          onClick={() => onMove(longStep)}
        />
        <WindowButton
          direction="left"
          accessibleLabel={moveLabel('older', 1)}
          disabled={!hasOlderWindow}
          onClick={() => onMove(1)}
        />
        <div className="chart-window-range" aria-live="polite">
          <small>{monthLabel(windowMonths)}</small>
          <div className="chart-window-dates">
            <time dateTime={startDate}>{formatDisplayDate(startDate)}</time>
            <span className="chart-window-date-arrow" aria-hidden="true">→</span>
            <time dateTime={endDate}>{formatDisplayDate(endDate)}</time>
          </div>
        </div>
        <WindowButton
          direction="right"
          accessibleLabel={moveLabel('newer', 1)}
          disabled={!hasNewerWindow}
          onClick={() => onMove(-1)}
        />
        <WindowButton
          direction="right"
          isLongStep
          accessibleLabel={moveLabel('newer', longStep)}
          disabled={!hasNewerWindow}
          onClick={() => onMove(-longStep)}
        />
      </div>
      {action ? <div className="chart-window-action">{action}</div> : null}
    </header>
  );
}

function WindowButton({
  accessibleLabel,
  direction,
  disabled,
  isLongStep = false,
  onClick,
}: {
  accessibleLabel: string;
  direction: 'left' | 'right';
  disabled: boolean;
  isLongStep?: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      aria-label={accessibleLabel}
      title={accessibleLabel}
      disabled={disabled}
      onClick={onClick}
    >
      <span
        className={`chart-window-icon is-${direction}${isLongStep ? ' is-double' : ''}`}
        aria-hidden="true"
      >
        <span />
        {isLongStep ? <span /> : null}
      </span>
    </button>
  );
}
