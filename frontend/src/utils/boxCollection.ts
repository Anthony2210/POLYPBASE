import type { BoxItem } from '../types';

export function upsertBoxes(currentBoxes: BoxItem[], incomingBoxes: BoxItem[]) {
  const boxesById = new Map(currentBoxes.map((box) => [box.id, box]));

  for (const box of incomingBoxes) {
    boxesById.set(box.id, box);
  }

  return [...boxesById.values()].sort((left, right) => left.global_code.localeCompare(right.global_code));
}
