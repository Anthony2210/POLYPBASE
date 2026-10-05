import type { BoxItem } from '../types';

const MAX_QR_BOX_CODE_LENGTH = 80;

/**
 * The box code a scanned value may stand for, to look up when the box is not
 * among the loaded ones. Values carrying a numeric `/bac/` id need no lookup.
 */
export function getBoxCodeFromQrValue(value: string) {
  const trimmedValue = value.trim();
  if (/\/bac\/\d+\/?/.test(trimmedValue)) return null;

  const routeMatch = trimmedValue.match(/\/boxes\/([^/?#]+)\/?/);
  let code = trimmedValue;
  if (routeMatch?.[1]) {
    try {
      code = decodeURIComponent(routeMatch[1]);
    } catch {
      return null;
    }
  }
  return code && code.length <= MAX_QR_BOX_CODE_LENGTH ? code : null;
}

export function getBoxIdFromQrValue(value: string, boxes: BoxItem[]) {
  const trimmedValue = value.trim();
  const routeMatch = trimmedValue.match(/\/bac\/(\d+)\/?/) ?? trimmedValue.match(/\/boxes\/([^/?#]+)\/?/);

  if (routeMatch?.[1]) {
    const routeValue = decodeURIComponent(routeMatch[1]);
    const routeId = Number(routeValue);
    if (Number.isInteger(routeId)) return routeId;

    const routeBox = boxes.find((box) => box.global_code.toLowerCase() === routeValue.toLowerCase());
    if (routeBox) return routeBox.id;
  }

  const normalizedValue = trimmedValue.toLowerCase();
  const directBox = boxes.find((box) => (
    box.global_code.toLowerCase() === normalizedValue
    || box.local_code.toLowerCase() === normalizedValue
  ));

  return directBox?.id ?? null;
}
