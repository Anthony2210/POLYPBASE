/**
 * Stepping helpers for the +/- number fields.
 *
 * The fields keep their value as a string so the user can type freely: the
 * buttons move by a fixed step, but nothing constrains a hand-typed value to a
 * multiple of that step. (A native `step` attribute would also reject the
 * off-step values, which is why these fields drive the arrows themselves.)
 */

export function parsePositiveDecimal(value: string) {
  const parsedValue = Number.parseFloat(value.replace(',', '.'));
  return Number.isFinite(parsedValue) && parsedValue >= 0 ? parsedValue : 0;
}

export function formatDecimalValue(value: number) {
  const roundedValue = Math.max(Math.round(value * 10) / 10, 0);
  return Number.isInteger(roundedValue) ? String(roundedValue) : roundedValue.toFixed(1);
}

export function incrementDecimalValue(currentValue: string, increment: number) {
  return formatDecimalValue(parsePositiveDecimal(currentValue) + increment);
}

export function decrementDecimalValue(currentValue: string, decrement: number) {
  return formatDecimalValue(parsePositiveDecimal(currentValue) - decrement);
}
