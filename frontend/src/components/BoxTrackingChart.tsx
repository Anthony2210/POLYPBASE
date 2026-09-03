import { useEffect, useMemo, useRef, useState, type MouseEvent as ReactMouseEvent, type PointerEvent as ReactPointerEvent } from 'react';

import type { BiologicalMeasurement, BoxLocation, BoxLineage, BoxMovement } from '../types';
import type { Language } from '../i18n';
import { buildChartWindow } from '../utils/chartWindow';
import BiologicalTrendChart, { type TrendEvent, type TrendLocation, type TrendMeasurement } from './BiologicalTrendChart';
import ChartWindowControls from './ChartWindowControls';

export type BoxTrackingChartLabels = {
  chartEmpty: string;
  chartTitle: string;
  historyButton?: string;
  historyEnteredBy: string;
  historyObservation: string;
  ephyraeFull: string;
  missingReading: string;
  polyps: string;
  salinityFull: string;
};

type LifecycleEvent = {
  id: string;
  date: string;
  type: 'movement' | 'subculture';
  title: string;
  detail: string;
};

export default function BoxTrackingChart({
  compact = false,
  initialWindowOffset = 0,
  events,
  labels,
  language,
  locations,
  measurements,
  onOpenHistory,
}: {
  compact?: boolean;
  initialWindowOffset?: number;
  events: LifecycleEvent[];
  labels: BoxTrackingChartLabels;
  language: Language;
  locations: BoxLocation[];
  measurements: BiologicalMeasurement[];
  onOpenHistory?: () => void;
}) {
  const [windowOffset, setWindowOffset] = useState(initialWindowOffset);
  const [isDragging, setIsDragging] = useState(false);
  const dragOrigin = useRef<number | null>(null);
  const didDrag = useRef(false);
  const timelineKey = useMemo(
    () => [
      measurements.length,
      measurements[0]?.measured_on,
      measurements[measurements.length - 1]?.measured_on,
      locations.length,
      events.length,
    ].join('-'),
    [events.length, locations.length, measurements],
  );
  const chartSourceDates = useMemo(
    () => getSharedChartSourceDates(measurements, locations, events),
    [events, locations, measurements],
  );
  const preparedData = useMemo(
    () => prepareSharedChartData(measurements, locations, events, windowOffset),
    [events, locations, measurements, windowOffset],
  );

  useEffect(() => setWindowOffset(initialWindowOffset), [initialWindowOffset, timelineKey]);

  useEffect(() => {
    if (windowOffset > preparedData.maxWindowOffset) {
      setWindowOffset(preparedData.maxWindowOffset);
    }
  }, [preparedData.maxWindowOffset, windowOffset]);

  function canMoveWindow(months: number) {
    const targetOffset = Math.min(
      preparedData.maxWindowOffset,
      Math.max(0, windowOffset + months),
    );
    if (targetOffset === windowOffset) return false;

    const targetWindow = buildChartWindow(chartSourceDates, targetOffset, 6);
    return hasTimelineDataInWindow(
      measurements,
      locations,
      events,
      targetWindow.startDate,
      targetWindow.endDate,
    );
  }

  function moveWindow(months: number) {
    if (!canMoveWindow(months)) return;
    setWindowOffset(Math.min(
      preparedData.maxWindowOffset,
      Math.max(0, windowOffset + months),
    ));
  }

  function handlePointerDown(event: ReactPointerEvent<HTMLDivElement>) {
    if (event.pointerType === 'mouse' && event.button !== 0) return;
    dragOrigin.current = event.clientX;
    didDrag.current = false;
    setIsDragging(false);
  }

  function handlePointerMove(event: ReactPointerEvent<HTMLDivElement>) {
    if (dragOrigin.current == null) return;
    if (Math.abs(event.clientX - dragOrigin.current) >= 10) {
      didDrag.current = true;
      setIsDragging(true);
      if (!event.currentTarget.hasPointerCapture(event.pointerId)) {
        event.currentTarget.setPointerCapture(event.pointerId);
      }
    }
  }

  function handlePointerEnd(event: ReactPointerEvent<HTMLDivElement>) {
    if (dragOrigin.current == null) return;
    const distance = event.clientX - dragOrigin.current;
    dragOrigin.current = null;
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
    if (Math.abs(distance) >= 60) {
      event.preventDefault();
      moveWindow(distance < 0 ? 1 : -1);
    }
    setIsDragging(false);
  }

  function handleClickCapture(event: ReactMouseEvent<HTMLDivElement>) {
    if (!didDrag.current) return;
    event.preventDefault();
    event.stopPropagation();
    didDrag.current = false;
  }

  return (
    <div className="measurement-chart">
      <ChartWindowControls
        compact={compact}
        action={onOpenHistory ? (
          <button type="button" className="secondary-button compact-button" onClick={onOpenHistory}>
            {labels.historyButton}
          </button>
        ) : undefined}
        endDate={preparedData.endDate}
        hasNewerWindow={preparedData.hasNewerWindow}
        hasOlderWindow={preparedData.hasOlderWindow}
        canMove={canMoveWindow}
        language={language}
        longStep={6}
        onMove={moveWindow}
        startDate={preparedData.startDate}
        title={compact ? undefined : labels.chartTitle}
        windowMonths={6}
      />

      <div
        className={`chart-window-viewport${isDragging ? ' is-dragging' : ''}`}
        onPointerDown={handlePointerDown}
        onPointerMove={handlePointerMove}
        onPointerUp={handlePointerEnd}
        onPointerCancel={handlePointerEnd}
        onClickCapture={handleClickCapture}
      >
        <div className="chart-window-content">
          <BiologicalTrendChart
            compact={compact}
            detailDisplay="inline"
            startDate={preparedData.startDate}
            endDate={preparedData.endDate}
            measurements={preparedData.measurements}
            locations={preparedData.locations}
            events={preparedData.events}
            selectionScope={timelineKey}
            labels={{
              chartTitle: labels.chartTitle,
              closeDetail: language === 'fr' ? 'Fermer le détail' : 'Close details',
              empty: labels.chartEmpty,
              enteredBy: labels.historyEnteredBy,
              ephyrae: labels.ephyraeFull,
              location: language === 'fr' ? 'Emplacement' : 'Location',
              missingReading: labels.missingReading,
              movement: language === 'fr' ? 'Transfert' : 'Transfer',
              observation: labels.historyObservation,
              polyps: labels.polyps,
              salinity: labels.salinityFull,
              selectReading: language === 'fr'
                ? 'Sélectionnez un point du graphique pour afficher le relevé.'
                : 'Select a chart point to display the reading.',
              selectedReading: language === 'fr' ? 'Relevé sélectionné' : 'Selected reading',
            }}
          />
        </div>
      </div>
    </div>
  );
}

function hasTimelineDataInWindow(
  measurements: BiologicalMeasurement[],
  locations: BoxLocation[],
  events: LifecycleEvent[],
  startDate: string,
  endDate: string,
) {
  if (measurements.some((measurement) => (
    measurement.measured_on >= startDate && measurement.measured_on <= endDate
  ))) return true;
  if (events.some((event) => event.date >= startDate && event.date <= endDate)) return true;
  return locations.some((location) => (
    location.starts_at.slice(0, 10) <= endDate
    && (
      location.end_date_unknown
        ? location.starts_at.slice(0, 10) >= startDate
        : !location.ends_at || location.ends_at.slice(0, 10) >= startDate
    )
  ));
}

function prepareSharedChartData(
  measurements: BiologicalMeasurement[],
  locations: BoxLocation[],
  events: LifecycleEvent[],
  requestedWindowOffset: number,
) {
  const chartWindow = buildChartWindow(
    getSharedChartSourceDates(measurements, locations, events),
    requestedWindowOffset,
    6,
  );
  const startText = chartWindow.startDate;
  const endText = chartWindow.endDate;

  const sharedMeasurements: TrendMeasurement[] = measurements
    .map((measurement) => ({
      id: measurement.id,
      date: measurement.measured_on,
      polypCount: measurement.polyp_count,
      ephyraeCount: measurement.ephyrae_count,
      salinity: measurement.salinity_psu,
      enteredBy: measurement.user,
      note: measurement.notes,
    }));
  const sharedLocations: TrendLocation[] = locations.map((location) => ({
    id: location.id,
    name: location.thermal_zone.name,
    startsAt: location.starts_at,
    endsAt: location.ends_at,
    endDateUnknown: location.end_date_unknown,
  }));
  const sharedEvents: TrendEvent[] = events
    .filter((event) => event.date >= startText && event.date <= endText)
    .map((event) => ({
      id: event.id,
      date: event.date,
      title: event.title,
      detail: event.detail,
      kind: event.type,
    }));

  return {
    startDate: startText,
    endDate: endText,
    measurements: sharedMeasurements,
    locations: sharedLocations,
    events: sharedEvents,
    hasNewerWindow: chartWindow.hasNewerWindow,
    hasOlderWindow: chartWindow.hasOlderWindow,
    maxWindowOffset: chartWindow.maxOffset,
  };
}

function getSharedChartSourceDates(
  measurements: BiologicalMeasurement[],
  locations: BoxLocation[],
  events: LifecycleEvent[],
) {
  const measurementDates = measurements.map((measurement) => measurement.measured_on);
  const eventDates = events.map((event) => event.date);
  const locationDates = locations.flatMap((location) => [
    location.starts_at.slice(0, 10),
    location.ends_at?.slice(0, 10),
  ]).filter(Boolean) as string[];

  return [...measurementDates, ...eventDates, ...locationDates];
}

export function buildLifecycleEvents(
  lineage: BoxLineage,
  movements: BoxMovement[],
  labels: { movementEvent: string; subcultureEvent: string },
) {
  const events = new Map<string, LifecycleEvent>();

  movements.forEach((movement) => {
    const date = movement.moved_at.slice(0, 10);
    events.set(`move-${movement.id}`, {
      id: `move-${movement.id}`,
      date,
      type: 'movement',
      title: labels.movementEvent,
      detail: movement.from_thermal_zone
        ? `${movement.from_thermal_zone.name} -> ${movement.to_thermal_zone.name}`
        : movement.to_thermal_zone.name,
    });
  });

  lineage.parents.forEach((relation) => {
    if (!relation.event) return;
    events.set(`subculture-parent-${relation.event.id}`, {
      id: `subculture-parent-${relation.event.id}`,
      date: relation.event.event_date,
      type: 'subculture',
      title: labels.subcultureEvent,
      detail: relation.box.global_code,
    });
  });

  lineage.children.forEach((relation) => {
    if (!relation.event) return;
    events.set(`subculture-child-${relation.event.id}-${relation.box.id}`, {
      id: `subculture-child-${relation.event.id}-${relation.box.id}`,
      date: relation.event.event_date,
      type: 'subculture',
      title: labels.subcultureEvent,
      detail: relation.box.global_code,
    });
  });

  return [...events.values()].sort((left, right) => left.date.localeCompare(right.date));
}
