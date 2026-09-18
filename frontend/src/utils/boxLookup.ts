import type { BoxItem } from '../types';

type RankedBox = {
  box: BoxItem;
  index: number;
  rank: number;
};

function normalize(value: string): string {
  return value.trim().toLocaleLowerCase();
}

function getMatchRank(box: BoxItem, query: string): number | null {
  const globalCode = normalize(box.global_code);
  const localCode = normalize(box.local_code);
  const boxNumber = normalize(box.box_number);
  const species = normalize(box.species.scientific_name);
  const strain = normalize(box.strain.code);
  const zone = normalize(box.thermal_zone?.name ?? '');

  if (globalCode === query) return 0;
  if (boxNumber === query) return 1;
  if (localCode === query) return 2;

  const fields = [globalCode, boxNumber, localCode, strain, species, zone];
  const prefixIndex = fields.findIndex((field) => field.startsWith(query));
  if (prefixIndex >= 0) return 10 + prefixIndex;

  const substringIndex = fields.findIndex((field) => field.includes(query));
  return substringIndex >= 0 ? 20 + substringIndex : null;
}

export function filterBoxes(boxes: BoxItem[], query: string): BoxItem[] {
  const value = normalize(query);
  if (!value) return boxes;

  return boxes
    .map<RankedBox | null>((box, index) => {
      const rank = getMatchRank(box, value);
      return rank == null ? null : { box, index, rank };
    })
    .filter((match): match is RankedBox => match !== null)
    .sort((left, right) => left.rank - right.rank || left.index - right.index)
    .map(({ box }) => box);
}
