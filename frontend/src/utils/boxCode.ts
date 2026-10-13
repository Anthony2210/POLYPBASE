import type { BoxItem } from '../types';
import type { QuickCreatedStrain } from '../components/QuickStrainCreator';

export function buildNextBoxCode(
  boxes: BoxItem[],
  strain: QuickCreatedStrain,
  organizationId: number,
) {
  const matchingBoxes = boxes
    .filter((box) => box.organization.id === organizationId && box.strain.id === strain.id)
    .map((box) => {
      const match = box.global_code.match(/^.*\.(\d+).*$/);
      return {
        numberText: match?.[1] ?? '',
        number: match ? Number(match[1]) : Number.NaN,
      };
    })
    .filter((item) => Number.isFinite(item.number))
    .sort((first, second) => second.number - first.number);

  const template = matchingBoxes[0];
  if (template) {
    const nextNumber = template.number + 1;
    const width = Math.max(template.numberText.length, 3);
    const boxNumber = String(nextNumber).padStart(width, '0');
    return {
      boxNumber,
      globalCode: `${strain.code}.${boxNumber}`,
    };
  }

  const boxNumber = '001';
  return {
    boxNumber,
    globalCode: `${strain.code}.${boxNumber}`,
  };
}

export function boxCodeMatchesBoxNumber(globalCode: string, boxNumber: string) {
  const codeNumber = extractBoxNumberFromCode(globalCode);
  if (!codeNumber) return true;
  return normalizeBoxNumber(codeNumber) === normalizeBoxNumber(boxNumber);
}

export function extractBoxNumberFromCode(globalCode: string) {
  return globalCode.trim().match(/^.*\.(\d+).*$/)?.[1] ?? null;
}

export function normalizeBoxNumber(value: string) {
  const normalized = value.trim();
  return /^\d+$/.test(normalized) ? String(Number.parseInt(normalized, 10)) : normalized;
}
