import type { BiologicalMeasurement } from '../types';
import type { TrendEvent, TrendMeasurement, TrendPolypState } from '../components/BiologicalTrendChart';

// Optional fields keep legacy responses usable while the detail contract rolls out.
export type BiologicalTimelineEntry = {
  kind: 'measurement' | 'subculture' | 'subculture_initialization';
  id: number;
  identity?: string;
  effective_date: string;
  state_sequence?: number | null;
  timestamp?: string | null;
  author?: { username?: string | null };
  polyp_count_before?: number | null;
  polyp_count_after?: number | null;
  allocated_polyps?: number | null;
  allocations?: Array<{
    child_box_id: number;
    child_global_code: string;
    allocated_polyps: number | null;
    position: number;
  }>;
  children?: Array<{ id: number; global_code: string }>;
  parent?: { id: number; global_code: string };
  event_id?: number;
  measurement?: BiologicalMeasurement;
  notes?: string;
};

export type BiologicalTimelineLabels = {
  polyps: string;
  subcultureEvent: string;
  allocatedPolyps?: string;
  parentBox?: string;
  childBoxes?: string;
  unknown?: string;
};

export function getBiologicalTimelineLabels(t: (key: string) => string): BiologicalTimelineLabels {
  const allocatedLabel = t('subcultureAllocatedPolyps');
  return {
    polyps: t('polyps'),
    subcultureEvent: t('subcultureEvent'),
    allocatedPolyps: allocatedLabel && allocatedLabel !== 'subcultureAllocatedPolyps' ? allocatedLabel : t('polyps'),
    parentBox: t('confirmDetailParentBox'),
    childBoxes: t('confirmDetailChildren'),
    unknown: t('subcultureUnknown'),
  };
}

export function prepareBiologicalChartData(
  measurements: BiologicalMeasurement[],
  timeline: readonly BiologicalTimelineEntry[] | undefined,
  labels: BiologicalTimelineLabels,
) {
  const readings = new Map<number, TrendMeasurement>(measurements.map((measurement) => [measurement.id, toTrendMeasurement(measurement)]));
  const polypStates: TrendPolypState[] = [];
  const events: Array<TrendEvent & { subcultureEventId: number }> = [];
  const seen = new Set<string>();
  [...(timeline ?? [])].sort(compareBiologicalTimelineEntries).forEach((entry, timelineOrder) => {
    const identity = `${entry.kind}:${entry.id}`;
    if (seen.has(identity)) return;
    seen.add(identity);
    if (entry.kind === 'measurement') {
      const reading = entry.measurement ? toTrendMeasurement(entry.measurement) : readings.get(entry.id);
      if (reading) readings.set(entry.id, { ...reading, timelineOrder });
      return;
    }
    const detailLines: Array<{ label: string; value: string }> = [];
    if (entry.kind === 'subculture' && entry.polyp_count_before != null && entry.polyp_count_after != null) {
      detailLines.push({ label: labels.parentBox ?? labels.polyps, value: `${entry.polyp_count_before} → ${entry.polyp_count_after} ${labels.polyps}` });
    }
    if (entry.allocated_polyps != null) {
      detailLines.push({ label: labels.allocatedPolyps ?? labels.polyps, value: String(entry.allocated_polyps) });
    }
    const allocations = [...(entry.allocations ?? [])].sort((left, right) => left.position - right.position);
    const children = allocations.length
      ? allocations.filter((allocation) => allocation.child_global_code?.trim()).map((allocation) => `${allocation.child_global_code}: ${allocation.allocated_polyps ?? labels.unknown ?? '—'}`)
      : (entry.children ?? []).map((child) => child.global_code).filter(Boolean);
    if (children.length) detailLines.push({ label: labels.childBoxes ?? labels.subcultureEvent, value: children.join(', ') });
    if (entry.parent?.global_code) detailLines.push({ label: labels.parentBox ?? labels.subcultureEvent, value: entry.parent.global_code });
    const subcultureEventId = entry.kind === 'subculture' ? entry.id : entry.event_id;
    if (subcultureEventId != null) events.push({
      id: entry.identity ?? identity,
      subcultureEventId,
      date: entry.effective_date,
      kind: 'subculture',
      title: labels.subcultureEvent,
      detailLines,
    });
    // Unknown legacy balances remain lifecycle events, never zero-valued points.
    if (entry.polyp_count_after == null) return;
    polypStates.push({
      id: entry.identity ?? identity,
      kind: entry.kind,
      date: entry.effective_date,
      timelineOrder,
      polypCount: entry.polyp_count_after,
      title: labels.subcultureEvent,
      enteredBy: entry.author?.username,
      note: entry.notes,
      detailLines,
    });
  });
  return { measurements: [...readings.values()], polypStates, events };
}

export function compareBiologicalTimelineEntries(left: BiologicalTimelineEntry, right: BiologicalTimelineEntry) {
  return left.effective_date.localeCompare(right.effective_date)
    || (left.state_sequence ?? 0) - (right.state_sequence ?? 0)
    || (left.timestamp ?? '').localeCompare(right.timestamp ?? '')
    || (left.identity ?? `${left.kind}:${left.id}`).localeCompare(right.identity ?? `${right.kind}:${right.id}`);
}

function toTrendMeasurement(measurement: BiologicalMeasurement): TrendMeasurement {
  return {
    id: measurement.id,
    date: measurement.measured_on,
    polypCount: measurement.polyp_count,
    ephyraeCount: measurement.ephyrae_count,
    salinity: measurement.salinity_psu,
    enteredBy: measurement.user,
    note: measurement.notes,
  };
}
