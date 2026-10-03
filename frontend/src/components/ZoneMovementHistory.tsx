import { useEffect, useMemo, useState } from 'react';
import { ArrowDownToLine, ArrowUpFromLine } from 'lucide-react';

import { apiGet, getStoredActiveOrganizationId } from '../api/client';
import type { Language, Translator } from '../i18n';
import type {
  PaginatedResponse,
  ThermalZone,
  ThermalZoneMovementDirection,
  ThermalZoneMovementEvent,
  ThermalZoneMovementSummary,
} from '../types';
import { formatDisplayDateTime } from '../utils/dateFormat';
import { getErrorMessage } from '../utils/errors';
import BoxTrackingPreview from './BoxTrackingPreview';
import DetailBackButton from './DetailBackButton';
import PageLoader from './PageLoader';
import SkeletonRows from './SkeletonRows';

const HISTORY_PAGE_SIZE = 24;
const FLOW_CHART_WIDTH = 640;
const FLOW_CHART_HEIGHT = 130;
const FLOW_CHART_LEFT = 34;
const FLOW_CHART_RIGHT = 12;
const FLOW_CHART_TOP = 14;
const FLOW_CHART_BASELINE = 94;

type MovementHistoryState = {
  requestKey: string;
  response: PaginatedResponse<ThermalZoneMovementEvent> | null;
  error: unknown;
  isLoading: boolean;
};

type MovementSummaryState = {
  requestKey: string;
  summary: ThermalZoneMovementSummary | null;
  error: unknown;
  isLoading: boolean;
};

function useZoneMovementHistory(
  zoneId: number | null,
  direction: ThermalZoneMovementDirection,
  limit: number,
  offset: number,
) {
  const [attempt, setAttempt] = useState(0);
  const requestKey = `${getStoredActiveOrganizationId()}:${zoneId}:${direction}:${limit}:${offset}:${attempt}`;
  const [state, setState] = useState<MovementHistoryState>({
    requestKey,
    response: null,
    error: null,
    isLoading: true,
  });

  useEffect(() => {
    let isCurrent = true;
    if (zoneId === null) {
      setState({ requestKey, response: null, error: null, isLoading: false });
      return () => {
        isCurrent = false;
      };
    }
    setState({ requestKey, response: null, error: null, isLoading: true });

    void apiGet<PaginatedResponse<ThermalZoneMovementEvent>>(
      `/api/thermal-zones/${zoneId}/history/?direction=${direction}&limit=${limit}&offset=${offset}`,
    )
      .then((response) => {
        if (isCurrent) setState({ requestKey, response, error: null, isLoading: false });
      })
      .catch((requestError) => {
        if (isCurrent) {
          setState({ requestKey, response: null, error: requestError, isLoading: false });
        }
      });

    return () => {
      isCurrent = false;
    };
  }, [direction, limit, offset, requestKey, zoneId]);

  // Hide the previous context before effects run, not just after the next request starts.
  const currentState = state.requestKey === requestKey
    ? state
    : { response: null, error: null, isLoading: zoneId !== null };
  return {
    ...currentState,
    retry: () => setAttempt((current) => current + 1),
  };
}

function useZoneMovementSummary(zoneId: number) {
  const [attempt, setAttempt] = useState(0);
  const requestKey = `${getStoredActiveOrganizationId()}:${zoneId}:${attempt}`;
  const [state, setState] = useState<MovementSummaryState>({
    requestKey,
    summary: null,
    error: null,
    isLoading: true,
  });

  useEffect(() => {
    let isCurrent = true;
    setState({ requestKey, summary: null, error: null, isLoading: true });
    void apiGet<ThermalZoneMovementSummary>(`/api/thermal-zones/${zoneId}/history/summary/`)
      .then((summary) => {
        if (isCurrent) setState({ requestKey, summary, error: null, isLoading: false });
      })
      .catch((requestError) => {
        if (isCurrent) {
          setState({ requestKey, summary: null, error: requestError, isLoading: false });
        }
      });

    return () => {
      isCurrent = false;
    };
  }, [requestKey, zoneId]);

  const currentState = state.requestKey === requestKey
    ? state
    : { summary: null, error: null, isLoading: true };
  return {
    ...currentState,
    retry: () => setAttempt((current) => current + 1),
  };
}

function MovementRows({
  direction,
  language,
  movements,
  onOpenBox,
  showRelatedZone,
  t,
}: {
  direction: ThermalZoneMovementDirection;
  language: Language;
  movements: ThermalZoneMovementEvent[];
  onOpenBox: (boxId: number) => void;
  showRelatedZone: boolean;
  t: Translator;
}) {
  const DirectionIcon = direction === 'arrival' ? ArrowDownToLine : ArrowUpFromLine;
  const directionLabel = t(direction === 'arrival' ? 'zoneMovementEntries' : 'zoneMovementExits');

  return (
    <div className="zone-movement-list">
      {movements.map((movement) => {
        const relatedZone = movement.related_zone_name
          ? (direction === 'arrival'
              ? t('zoneMovementFrom').replace('{zone}', movement.related_zone_name)
              : t('zoneMovementTo').replace('{zone}', movement.related_zone_name))
          : null;

        return (
          <div className={`zone-movement-row is-${direction}`} key={`${movement.event_type}-${movement.location_id}`}>
            <DirectionIcon className="zone-movement-direction-icon" size={17} strokeWidth={1.8} aria-hidden="true" />
            <div className="box-inventory-identity zone-movement-identity">
              <BoxTrackingPreview
                boxId={movement.box_id}
                code={movement.box_code}
                language={language}
                onOpenBox={(boxId) => onOpenBox(boxId)}
                t={t}
              />
              {showRelatedZone && relatedZone ? <span className="zone-movement-related">{relatedZone}</span> : null}
            </div>
            <button
              className="zone-movement-open"
              type="button"
              aria-label={`${directionLabel}, ${movement.box_code}, ${formatDisplayDateTime(movement.occurred_at)}`}
              onClick={() => onOpenBox(movement.box_id)}
            >
              <time dateTime={movement.occurred_at}>{formatDisplayDateTime(movement.occurred_at)}</time>
            </button>
          </div>
        );
      })}
    </div>
  );
}

function getCountTicks(maximum: number) {
  if (maximum <= 0) return [0];
  const step = Math.max(1, Math.ceil(maximum / 3));
  const upperBound = Math.ceil(maximum / step) * step;
  const ticks: number[] = [];
  for (let value = 0; value <= upperBound; value += step) ticks.push(value);
  return ticks;
}

function WeeklyMovementChart({ summary, t }: { summary: ThermalZoneMovementSummary; t: Translator }) {
  const maximum = Math.max(0, ...summary.weeks.flatMap((week) => [week.entry_count, week.exit_count]));
  const ticks = useMemo(() => getCountTicks(maximum), [maximum]);
  const axisMaximum = Math.max(1, ticks[ticks.length - 1] ?? 1);
  const plotWidth = FLOW_CHART_WIDTH - FLOW_CHART_LEFT - FLOW_CHART_RIGHT;
  const plotHeight = FLOW_CHART_BASELINE - FLOW_CHART_TOP;
  const groupWidth = plotWidth / Math.max(1, summary.weeks.length);
  const barWidth = Math.min(16, groupWidth * 0.24);
  const yForValue = (value: number) => FLOW_CHART_BASELINE - (value / axisMaximum) * plotHeight;

  return (
    <div className="zone-movement-flow-chart">
      <div className="zone-movement-flow-legend" aria-hidden="true">
        <span className="is-entry">{t('zoneMovementEntries')}</span>
        <span className="is-exit">{t('zoneMovementExits')}</span>
      </div>
      <svg
        viewBox={`0 0 ${FLOW_CHART_WIDTH} ${FLOW_CHART_HEIGHT}`}
        role="img"
        aria-label={t('zoneMovementFlowChartLabel')}
      >
        {ticks.map((tick) => {
          const y = yForValue(tick);
          return (
            <g className="zone-movement-flow-grid" key={tick}>
              <line x1={FLOW_CHART_LEFT} x2={FLOW_CHART_WIDTH - FLOW_CHART_RIGHT} y1={y} y2={y} />
              <text x={FLOW_CHART_LEFT - 8} y={y + 4}>{tick}</text>
            </g>
          );
        })}
        {summary.weeks.map((week, index) => {
          const center = FLOW_CHART_LEFT + groupWidth * index + groupWidth / 2;
          const entryY = yForValue(week.entry_count);
          const exitY = yForValue(week.exit_count);
          const hasBoth = week.entry_count > 0 && week.exit_count > 0;
          return (
            <g className="zone-movement-week-slot" key={week.week_start}>
              <rect
                className="zone-movement-flow-entry"
                x={hasBoth ? center - barWidth - 2 : center - barWidth / 2}
                y={entryY}
                width={barWidth}
                height={FLOW_CHART_BASELINE - entryY}
              />
              {week.exit_count > 0 ? (
                <rect
                  className="zone-movement-flow-exit"
                  x={hasBoth ? center + 2 : center - barWidth / 2}
                  y={exitY}
                  width={barWidth}
                  height={FLOW_CHART_BASELINE - exitY}
                />
              ) : null}
              <text className="zone-movement-flow-week" x={center} y={FLOW_CHART_BASELINE + 19}>
                {t('zoneMovementWeekShort').replace('{week}', String(week.iso_week))}
              </text>
            </g>
          );
        })}
      </svg>
      <ul className="sr-only">
        {summary.weeks.map((week) => (
          <li key={week.week_start}>
            {t('zoneMovementWeekSummary')
              .replace('{week}', String(week.iso_week))
              .replace('{year}', String(week.iso_year))
              .replace('{entries}', String(week.entry_count))
              .replace('{exits}', String(week.exit_count))}
          </li>
        ))}
      </ul>
    </div>
  );
}

function RecentMovementColumn({
  direction,
  language,
  movements,
  onOpenBox,
  onOpenHistory,
  t,
}: {
  direction: ThermalZoneMovementDirection;
  language: Language;
  movements: ThermalZoneMovementEvent[];
  onOpenBox: (boxId: number) => void;
  onOpenHistory: (direction: ThermalZoneMovementDirection) => void;
  t: Translator;
}) {
  const isArrival = direction === 'arrival';
  return (
    <section className={`zone-recent-movement-column is-${direction}`}>
      <h3>{t(isArrival ? 'zoneMovementEntries' : 'zoneMovementExits')}</h3>
      {movements.length ? (
        <MovementRows
          direction={direction}
          language={language}
          movements={movements}
          onOpenBox={onOpenBox}
          showRelatedZone={false}
          t={t}
        />
      ) : (
        <p className="muted compact-text">{t(isArrival ? 'zoneMovementEntriesEmpty' : 'zoneMovementExitsEmpty')}</p>
      )}
      <button className="secondary-button zone-movement-history-action" type="button" onClick={() => onOpenHistory(direction)}>
        {t(isArrival ? 'zoneMovementAllEntries' : 'zoneMovementAllExits')}
      </button>
    </section>
  );
}

export function ZoneRecentMovements({
  language,
  onOpenBox,
  onOpenHistory,
  t,
  zoneId,
}: {
  language: Language;
  onOpenBox: (boxId: number) => void;
  onOpenHistory: (direction: ThermalZoneMovementDirection) => void;
  t: Translator;
  zoneId: number;
}) {
  const { summary, error, isLoading, retry } = useZoneMovementSummary(zoneId);

  return (
    <section className="zone-page-section zone-recent-movements" aria-busy={isLoading}>
      <h2>{t('zoneRecentMovementsTitle')}</h2>
      <p className="sr-only" role="status">{isLoading ? t('loading') : ''}</p>
      {isLoading ? (
        <SkeletonRows count={3} />
      ) : error ? (
        <div className="zone-movement-state" role="status">
          <p>{getErrorMessage(error, t('auditValueUnavailable'))}</p>
          <button className="secondary-button" type="button" onClick={retry}>{t('zoneMovementRetry')}</button>
        </div>
      ) : summary ? (
        <>
          {summary.weeks.length > 0 ? (
            <WeeklyMovementChart summary={summary} t={t} />
          ) : (
            <p className="muted compact-text">{t('zoneMovementEmpty')}</p>
          )}
          <div className="zone-recent-movement-columns">
            <RecentMovementColumn
              direction="arrival"
              language={language}
              movements={summary.recent_arrivals}
              onOpenBox={onOpenBox}
              onOpenHistory={onOpenHistory}
              t={t}
            />
            <RecentMovementColumn
              direction="departure"
              language={language}
              movements={summary.recent_departures}
              onOpenBox={onOpenBox}
              onOpenHistory={onOpenHistory}
              t={t}
            />
          </div>
        </>
      ) : null}
    </section>
  );
}

export default function ZoneMovementHistoryPage({
  direction,
  isLoading,
  language,
  onBack,
  onChangeDirection,
  onOpenBox,
  t,
  zone,
}: {
  direction: ThermalZoneMovementDirection;
  isLoading: boolean;
  language: Language;
  onBack: () => void;
  onChangeDirection: (direction: ThermalZoneMovementDirection) => void;
  onOpenBox: (boxId: number) => void;
  t: Translator;
  zone: ThermalZone | null;
}) {
  const contextKey = `${getStoredActiveOrganizationId()}:${zone?.id ?? null}:${direction}`;
  const [pagination, setPagination] = useState({ contextKey, offset: 0 });
  const offset = pagination.contextKey === contextKey ? pagination.offset : 0;
  if (pagination.contextKey !== contextKey) setPagination({ contextKey, offset: 0 });
  const setOffset = (nextOffset: number) => setPagination({ contextKey, offset: nextOffset });
  const history = useZoneMovementHistory(zone?.id ?? null, direction, HISTORY_PAGE_SIZE, offset);

  if (isLoading) return <PageLoader variant="zone" label={t('zoneMovementHistoryTitle')} />;

  if (!zone) {
    return (
      <section className="zone-page">
        <DetailBackButton label={t('back')} onBack={onBack} desktopClassName="zone-back-button" />
        <p className="muted compact-text">{t('noZone')}</p>
      </section>
    );
  }

  const response = history.response;
  const totalPages = Math.max(1, Math.ceil((response?.count ?? 0) / HISTORY_PAGE_SIZE));
  const currentPage = Math.floor(offset / HISTORY_PAGE_SIZE) + 1;
  const startResult = response?.count ? offset + 1 : 0;
  const endResult = response ? Math.min(offset + response.results.length, response.count) : 0;

  return (
    <section className="zone-page zone-movement-history-page">
      <DetailBackButton label={t('back')} onBack={onBack} desktopClassName="zone-back-button" />

      <header className="entity-header entity-header--zone zone-sheet-hero zone-history-hero">
        <div className="entity-header__identity zone-sheet-title">
          <p className="box-page-label">{t('zoneMovementHistoryTitle')}</p>
          <h2>{zone.name}</h2>
          <span>{zone.organization.name}</span>
        </div>
      </header>

      <section className="zone-page-section zone-movement-history-section" aria-busy={history.isLoading}>
        <div className="zone-movement-direction-switch" role="group" aria-label={t('zoneMovementDirectionFilter')}>
          {(['arrival', 'departure'] as const).map((option) => (
            <button
              className={`secondary-button is-${option}`}
              type="button"
              key={option}
              aria-pressed={direction === option}
              onClick={() => onChangeDirection(option)}
            >
              {t(option === 'arrival' ? 'zoneMovementEntries' : 'zoneMovementExits')}
            </button>
          ))}
        </div>

        <p className="sr-only" role="status">{history.isLoading ? t('loading') : ''}</p>
        {history.isLoading ? (
          <SkeletonRows count={8} />
        ) : history.error ? (
          <div className="zone-movement-state" role="status">
            <p>{getErrorMessage(history.error, t('auditValueUnavailable'))}</p>
            <button className="secondary-button" type="button" onClick={history.retry}>{t('zoneMovementRetry')}</button>
          </div>
        ) : response?.results.length ? (
          <MovementRows
            direction={direction}
            language={language}
            movements={response.results}
            onOpenBox={onOpenBox}
            showRelatedZone
            t={t}
          />
        ) : (
          <p className="muted compact-text">{t(direction === 'arrival' ? 'zoneMovementEntriesEmpty' : 'zoneMovementExitsEmpty')}</p>
        )}

        {response && response.count > 0 ? (
          <nav className="zone-movement-pagination" aria-label={t('zoneMovementPagination')}>
            <span>
              {t('zoneMovementRange')
                .replace('{start}', String(startResult))
                .replace('{end}', String(endResult))
                .replace('{count}', String(response.count))}
            </span>
            <div>
              <button
                className="secondary-button"
                type="button"
                disabled={!response.previous || history.isLoading}
                onClick={() => setOffset(Math.max(0, offset - HISTORY_PAGE_SIZE))}
              >
                {t('zoneMovementPrevious')}
              </button>
              <strong>{currentPage} / {totalPages}</strong>
              <button
                className="secondary-button"
                type="button"
                disabled={!response.next || history.isLoading}
                onClick={() => setOffset(offset + HISTORY_PAGE_SIZE)}
              >
                {t('zoneMovementNext')}
              </button>
            </div>
          </nav>
        ) : null}
      </section>
    </section>
  );
}
