import type { ReactNode } from 'react';

import type { Language } from '../i18n';
import { formatDisplayDate } from '../utils/dateFormat';
import PolypbaseIcon, { type PolypbaseIconName } from './PolypbaseIcon';

export default function ChartWindowControls({
  action,
  canMove,
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
  canMove?: (months: number) => boolean;
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
  const formattedStartDate = formatDisplayDate(startDate);
  const formattedEndDate = formatDisplayDate(endDate);
  const rangeLabel = language === 'fr'
    ? `de ${monthLabel(windowMonths)}`
    : `for ${monthLabel(windowMonths)}`;
  const displayedPeriodLabel = language === 'fr' ? 'Période affichée' : 'Displayed period';
  const datePrefix = language === 'fr' ? 'du' : 'from';
  const dateSeparator = language === 'fr' ? 'au' : 'to';

  return (
    <header className={`chart-window-header${compact ? ' is-compact' : ''}`}>
      {title ? <h2>{title}</h2> : null}
      <div className="chart-window-navigation" role="group" aria-label={monthLabel(windowMonths)}>
        <div className="chart-window-step-group is-older">
          <WindowButton
            direction="left"
            isLongStep
            accessibleLabel={moveLabel('older', longStep)}
            disabled={canMove ? !canMove(longStep) : !hasOlderWindow}
            onClick={() => onMove(longStep)}
          />
          <WindowButton
            direction="left"
            accessibleLabel={moveLabel('older', 1)}
            disabled={canMove ? !canMove(1) : !hasOlderWindow}
            onClick={() => onMove(1)}
          />
        </div>
        <div className="chart-window-range" aria-live="polite">
          <span className="chart-window-range-label">
            <small>{displayedPeriodLabel} {rangeLabel}</small>
          </span>
          <div className="chart-window-dates">
            <span className="chart-window-date-prefix">{datePrefix}</span>
            <time dateTime={startDate}>{formattedStartDate}</time>
            <span className="chart-window-date-separator">{dateSeparator}</span>
            <time dateTime={endDate}>{formattedEndDate}</time>
          </div>
        </div>
        <div className="chart-window-step-group is-newer">
          <WindowButton
            direction="right"
            accessibleLabel={moveLabel('newer', 1)}
            disabled={canMove ? !canMove(-1) : !hasNewerWindow}
            onClick={() => onMove(-1)}
          />
          <WindowButton
            direction="right"
            isLongStep
            accessibleLabel={moveLabel('newer', longStep)}
            disabled={canMove ? !canMove(-longStep) : !hasNewerWindow}
            onClick={() => onMove(-longStep)}
          />
        </div>
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
  const iconName: PolypbaseIconName = direction === 'left'
    ? (isLongStep ? 'chevrons-left' : 'chevron-left')
    : (isLongStep ? 'chevrons-right' : 'chevron-right');

  return (
    <button
      type="button"
      aria-label={accessibleLabel}
      title={accessibleLabel}
      disabled={disabled}
      onClick={onClick}
    >
      <PolypbaseIcon name={iconName} size={18} strokeWidth={2.1} />
    </button>
  );
}
