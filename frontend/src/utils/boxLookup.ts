import type { BoxItem } from '../types';

export function filterBoxes(boxes: BoxItem[], query: string): BoxItem[] {
  const value = query.trim().toLowerCase();
  if (!value) return boxes;

  return boxes.filter((box) => [
    box.global_code,
    box.local_code,
    box.box_number,
    box.species.scientific_name,
    box.strain.code,
    box.thermal_zone?.name ?? '',
  ].some((field) => field.toLowerCase().includes(value)));
}
