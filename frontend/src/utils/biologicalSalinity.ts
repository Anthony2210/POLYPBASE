// Normalize display without rounding scientific values or converting absence to zero.
export function formatBiologicalSalinity(value: string) {
  const trimmed = value.trim();
  if (!/^\d+(?:\.\d+)?$/.test(trimmed)) return trimmed;
  return trimmed.replace(/(\.\d*?)0+$/, '$1').replace(/\.$/, '');
}

export function stepBiologicalSalinity(value: string, increment: number) {
  const trimmed = value.trim();
  const match = /^(\d+)(?:\.(\d{1,2}))?$/.exec(trimmed);
  if (!match && trimmed) return value;
  // Integer hundredths avoid binary floating-point rounding during the coarse step.
  const hundredths = match ? Number(`${match[1]}${(match[2] ?? '').padEnd(2, '0')}`) : 0;
  const step = increment * 100;
  if (!Number.isSafeInteger(hundredths) || !Number.isSafeInteger(step)
    || !Number.isSafeInteger(hundredths + step)) return value;
  const result = Math.max(hundredths + step, 0);
  return formatBiologicalSalinity(`${Math.floor(result / 100)}.${String(result % 100).padStart(2, '0')}`);
}
