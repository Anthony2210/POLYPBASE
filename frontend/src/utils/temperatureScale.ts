export type TemperatureScaleInput = {
  target?: number | null;
  average?: number | null;
  minimum?: number | null;
  maximum?: number | null;
};

export type TemperatureScale = {
  domain: readonly [number, number];
  ticks: number[];
  project: (value: number) => number;
};

const MINIMUM_VISUAL_SPAN_C = 4;
const MINIMUM_PADDING_C = 0.5;
const TARGET_TICK_COUNT = 5;
const DRAWING_START_PERCENT = 7;
const DRAWING_END_PERCENT = 93;

export function buildTemperatureScale(input: TemperatureScaleInput): TemperatureScale | null {
  const values = [input.target, input.average, input.minimum, input.maximum]
    .filter((value): value is number => typeof value === 'number' && Number.isFinite(value));

  if (!values.length) return null;

  let lower = Math.min(...values);
  let upper = Math.max(...values);
  const factualSpan = upper - lower;
  const visualSpan = Math.max(factualSpan, MINIMUM_VISUAL_SPAN_C);
  const center = (lower + upper) / 2;

  lower = center - visualSpan / 2;
  upper = center + visualSpan / 2;

  const padding = Math.max(factualSpan * 0.18, MINIMUM_PADDING_C);
  lower -= padding;
  upper += padding;

  const step = niceStep((upper - lower) / (TARGET_TICK_COUNT - 1));
  const domainMinimum = roundToStep(Math.floor(lower / step) * step, step);
  const domainMaximum = roundToStep(Math.ceil(upper / step) * step, step);
  const domainSpan = domainMaximum - domainMinimum;
  const ticks: number[] = [];

  for (let value = domainMinimum; value <= domainMaximum + step / 2; value += step) {
    ticks.push(normalizeZero(roundToStep(value, step)));
  }

  return {
    domain: [normalizeZero(domainMinimum), normalizeZero(domainMaximum)],
    ticks,
    project(value: number) {
      const ratio = domainSpan === 0 ? 0.5 : (value - domainMinimum) / domainSpan;
      const guardedRatio = Math.min(1, Math.max(0, ratio));
      return DRAWING_START_PERCENT
        + guardedRatio * (DRAWING_END_PERCENT - DRAWING_START_PERCENT);
    },
  };
}

function niceStep(roughStep: number) {
  const exponent = Math.floor(Math.log10(roughStep));
  const magnitude = 10 ** exponent;
  const normalized = roughStep / magnitude;
  const niceNormalized = normalized <= 1
    ? 1
    : normalized <= 2
      ? 2
      : normalized <= 2.5
        ? 2.5
        : normalized <= 5
          ? 5
          : 10;
  return niceNormalized * magnitude;
}

function roundToStep(value: number, step: number) {
  const precision = Math.max(0, -Math.floor(Math.log10(step)) + 2);
  return Number(value.toFixed(precision));
}

function normalizeZero(value: number) {
  return Object.is(value, -0) ? 0 : value;
}
