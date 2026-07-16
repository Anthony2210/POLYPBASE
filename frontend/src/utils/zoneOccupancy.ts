/**
 * Occupancy level of a thermal zone, used to warn before it runs out of room.
 *
 * - "full"    : the zone reached (or passed) its capacity  -> red
 * - "warning" : fewer than 10 free slots left              -> orange
 * - "ok"      : plenty of room, or no capacity set         -> neutral
 *
 * Capacity is optional: a zone without one is never flagged.
 */
export type ZoneOccupancyLevel = 'ok' | 'warning' | 'full';

/** Free slots below which a zone is flagged as nearly full. */
export const ZONE_NEARLY_FULL_THRESHOLD = 10;

export function getZoneOccupancyLevel(
  boxCount: number,
  capacity: number | null | undefined,
): ZoneOccupancyLevel {
  // No capacity set (or a meaningless one): nothing to warn about.
  if (capacity == null || capacity <= 0) return 'ok';
  // ">=" and not "===": a zone can already be over capacity (boxes moved in
  // before a capacity was set, or capacity lowered afterwards).
  if (boxCount >= capacity) return 'full';
  if (capacity - boxCount < ZONE_NEARLY_FULL_THRESHOLD) return 'warning';
  return 'ok';
}
