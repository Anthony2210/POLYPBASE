import type { BoxItem, SubcultureChildPayload } from '../types';

type ParentBoxIdentity = Pick<BoxItem, 'global_code' | 'strain'>;
type ExistingBoxIdentity = Pick<BoxItem, 'global_code'>;
type ChildIdentity = Pick<SubcultureChildPayload, 'global_code'>;

export function suggestChildIdentity(
  parentBox: ParentBoxIdentity,
  existingBoxes: ExistingBoxIdentity[],
  currentChildren: ChildIdentity[],
) {
  const parentNumber = extractBoxNumber(parentBox.global_code);
  if (!parentNumber) {
    return { globalCode: '', boxNumber: '' };
  }

  const prefix = `${parentBox.strain.code}.`;
  const width = parentNumber.length;
  const prefixPattern = new RegExp(`^${escapeRegExp(prefix)}(\\d+)$`);
  const existingCodes = [
    ...existingBoxes.map((existingBox) => existingBox.global_code),
    ...currentChildren.map((child) => child.global_code),
  ];
  const matchingNumbers = existingCodes
    .map((code) => code.match(prefixPattern)?.[1] ?? null)
    .filter((value): value is string => Boolean(value))
    .map((value) => Number(value))
    .filter(Number.isFinite);
  const nextNumber = Math.max(Number(parentNumber), ...matchingNumbers) + 1;
  const formattedNumber = String(nextNumber).padStart(Math.max(width, 3), '0');

  return {
    globalCode: `${prefix}${formattedNumber}`,
    boxNumber: formattedNumber,
  };
}

function extractBoxNumber(globalCode: string) {
  return globalCode.match(/^.*\.(\d+).*$/)?.[1] ?? null;
}

function escapeRegExp(value: string) {
  return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}
