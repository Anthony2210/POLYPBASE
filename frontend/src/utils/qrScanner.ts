import type { BoxItem } from '../types';

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
