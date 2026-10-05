import { useMemo, useState } from 'react';

import type { BiologicalMeasurement, BoxLocation, BoxLineage, BoxMovement } from '../types';
import { createTranslator, type Language } from '../i18n';
import { buildChartWindow, getLatestChartWindowOffset, parseChartDate, toChartDateString } from '../utils/chartWindow';
import BiologicalTrendChart, { type TrendEvent, type TrendLocation, type TrendPolypState } from './BiologicalTrendChart';
import { getBiologicalTimelineLabels, prepareBiologicalChartData, type BiologicalTimelineEntry } from '../utils/biologicalTimeline';
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
  subcultureEvent?: string;
  allocatedPolyps?: string;
  parentBox?: string;
  childBoxes?: string;
};

type LifecycleEvent = {
  id: string;
  date: string;
  type: 'movement' | 'subculture';
  title: string;
  detail: string;
  subcultureEventId?: number;
};

export default function BoxTrackingChart({
  compact = false,
  initialWindowOffset,
  biologicalTimeline,
  events,
  labels,
  language,
  locations,
  measurements,
  onOpenHistory,
}: {
  compact?: boolean;
  initialWindowOffset?: number;
  biologicalTimeline?: readonly BiologicalTimelineEntry[];
  events: LifecycleEvent[];
  labels: BoxTrackingChartLabels;
  language: Language;
  locations: BoxLocation[];
  measurements: BiologicalMeasurement[];
  onOpenHistory?: () => void;
}) {
  const timelineLabels = useMemo(() => getBiologicalTimelineLabels(createTranslator(language)), [language]);
  const biologicalData = useMemo(() => prepareBiologicalChartData(measurements, biologicalTimeline, {
    polyps: labels.polyps,
    subcultureEvent: labels.subcultureEvent ?? timelineLabels.subcultureEvent,
    allocatedPolyps: labels.allocatedPolyps ?? timelineLabels.allocatedPolyps,
    parentBox: labels.parentBox ?? timelineLabels.parentBox,
    childBoxes: labels.childBoxes ?? timelineLabels.childBoxes,
  }), [biologicalTimeline, labels, measurements, timelineLabels]);
  const chartEvents = useMemo(() => {
    const timelineEventIds = new Set(biologicalData.events.map((event) => event.subcultureEventId));
    const legacyEvents: TrendEvent[] = events
      .filter((event) => event.subcultureEventId == null || !timelineEventIds.has(event.subcultureEventId))
      .map((event) => ({ ...event, kind: event.type }));
    return [...legacyEvents, ...biologicalData.events];
  }, [biologicalData.events, events]);
  const timelineKey = useMemo(
    () => [
      measurements.length,
      measurements[0]?.measured_on,
      measurements[measurements.length - 1]?.measured_on,
      locations.length,
      events.length,
      biologicalTimeline?.map((entry) => `${entry.kind}:${entry.id}:${entry.polyp_count_after}`).join(','),
    ].join('-'),
    [biologicalTimeline, events.length, locations.length, measurements],
  );
  const boxKey = `${measurements[0]?.id ?? ''}:${locations[0]?.id ?? ''}:${events[0]?.id ?? ''}:${biologicalData.polypStates[0]?.id ?? ''}`;
  const chartSourceDates = useMemo(
    () => getSharedChartSourceDates(measurements, locations, chartEvents, biologicalData.polypStates),
    [biologicalData.polypStates, chartEvents, locations, measurements],
  );
  const defaultWindowOffset = useMemo(
    () => initialWindowOffset ?? getLatestChartWindowOffset(
      chartSourceDates,
      [...measurements.map((measurement) => measurement.measured_on), ...biologicalData.polypStates.map((point) => point.date)],
      6,
    ),
    [biologicalData.polypStates, chartSourceDates, initialWindowOffset, measurements],
  );
  const defaultWindow = useMemo(
    () => buildChartWindow(chartSourceDates, defaultWindowOffset, 6),
    [chartSourceDates, defaultWindowOffset],
  );
  const latestWindow = useMemo(() => buildChartWindow(chartSourceDates, 0, 6), [chartSourceDates]);
  const earliestDate = useMemo(() => chartSourceDates
    .filter((date) => !Number.isNaN(parseChartDate(date).getTime()))
    .map((date) => date.slice(0, 10))
    .sort()[0], [chartSourceDates]);
  let extentStart = earliestDate ?? defaultWindow.startDate;
  const extentEnd = latestWindow.endDate;
  if (extentStart >= extentEnd) {
    const previousDay = parseChartDate(extentEnd);
    previousDay.setDate(previousDay.getDate() - 1);
    extentStart = toChartDateString(previousDay);
  }
  const [selectedWindow, setSelectedWindow] = useState({
    boxKey,
    startDate: defaultWindow.startDate,
    endDate: defaultWindow.endDate,
  });
  const currentWindow = selectedWindow.boxKey === boxKey ? selectedWindow : defaultWindow;
  const endDate = currentWindow.endDate <= extentStart
    ? extentEnd
    : currentWindow.endDate > extentEnd ? extentEnd : currentWindow.endDate;
  const startDate = currentWindow.startDate < extentStart
    ? extentStart
    : currentWindow.startDate >= endDate ? extentStart : currentWindow.startDate;
  const preparedData = useMemo(
    () => prepareSharedChartData(biologicalData, locations, chartEvents, startDate, endDate),
    [biologicalData, chartEvents, locations, startDate, endDate],
  );

  return (
    <div className="measurement-chart">
      <ChartWindowControls
        compact={compact}
        action={onOpenHistory ? (
          <button type="button" className="secondary-button compact-button" onClick={onOpenHistory}>
            {labels.historyButton}
          </button>
        ) : undefined}
        endDate={endDate}
        extentEnd={extentEnd}
        extentStart={extentStart}
        language={language}
        onChange={(nextStart, nextEnd) => setSelectedWindow({ boxKey, startDate: nextStart, endDate: nextEnd })}
        startDate={startDate}
        title={compact ? undefined : labels.chartTitle}
      />

      <div className="chart-window-viewport">
        <div className="chart-window-content">
          <BiologicalTrendChart
            compact={compact}
            startDate={preparedData.startDate}
            endDate={preparedData.endDate}
            measurements={preparedData.measurements}
            polypStates={preparedData.polypStates}
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
              subculture: labels.subcultureEvent ?? timelineLabels.subcultureEvent,
            }}
          />
        </div>
      </div>
    </div>
  );
}

function prepareSharedChartData(
  biologicalData: ReturnType<typeof prepareBiologicalChartData>,
  locations: BoxLocation[],
  events: TrendEvent[],
  startText: string,
  endText: string,
) {
  const sharedMeasurements = biologicalData.measurements;
  const sharedLocations: TrendLocation[] = locations.map((location) => ({
    id: location.id,
    name: location.thermal_zone.name,
    startsAt: location.starts_at,
    endsAt: location.ends_at,
    endDateUnknown: location.end_date_unknown,
  }));
  const sharedEvents = events.filter((event) => event.date >= startText && event.date <= endText);

  return {
    startDate: startText,
    endDate: endText,
    measurements: sharedMeasurements,
    polypStates: biologicalData.polypStates,
    locations: sharedLocations,
    events: sharedEvents,
  };
}

function getSharedChartSourceDates(
  measurements: BiologicalMeasurement[],
  locations: BoxLocation[],
  events: TrendEvent[],
  polypStates: TrendPolypState[] = [],
) {
  const measurementDates = measurements.map((measurement) => measurement.measured_on);
  const eventDates = events.map((event) => event.date);
  const locationDates = locations.flatMap((location) => [
    location.starts_at.slice(0, 10),
    location.ends_at?.slice(0, 10),
  ]).filter(Boolean) as string[];

  return [...measurementDates, ...polypStates.map((point) => point.date), ...eventDates, ...locationDates];
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
      subcultureEventId: relation.event.id,
      date: relation.event.event_date,
      type: 'subculture',
      title: labels.subcultureEvent,
      detail: relation.box.global_code,
    });
  });

  lineage.children.forEach((relation) => {
    if (!relation.event) return;
    const eventKey = `subculture-child-${relation.event.id}`;
    const previous = events.get(eventKey);
    events.set(eventKey, {
      id: eventKey,
      subcultureEventId: relation.event.id,
      date: relation.event.event_date,
      type: 'subculture',
      title: labels.subcultureEvent,
      detail: previous ? `${previous.detail}, ${relation.box.global_code}` : relation.box.global_code,
    });
  });

  return [...events.values()].sort((left, right) => left.date.localeCompare(right.date));
}
