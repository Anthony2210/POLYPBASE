export type ChartWindow = {
  endDate: string;
  hasNewerWindow: boolean;
  hasOlderWindow: boolean;
  maxOffset: number;
  offset: number;
  startDate: string;
};

export function buildChartWindow(
  sourceDates: Array<string | null | undefined>,
  requestedOffset: number,
  windowMonths: number,
): ChartWindow {
  const dates = sourceDates
    .filter((value): value is string => Boolean(value && isValidChartDate(value)))
    .map((value) => value.slice(0, 10))
    .sort();
  const today = startOfLocalDay(new Date());
  const latestDataDate = dates.length ? parseChartDate(dates[dates.length - 1]) : today;
  const anchorDate = latestDataDate > today ? latestDataDate : today;
  const firstDate = dates.length
    ? parseChartDate(dates[0])
    : addChartMonths(anchorDate, -windowMonths);
  const maxOffset = getMaximumOffset(firstDate, anchorDate, windowMonths);
  const offset = Math.min(maxOffset, Math.max(0, Math.round(requestedOffset)));
  const endDate = addChartMonths(anchorDate, -offset);
  const startDate = addChartMonths(endDate, -windowMonths);

  return {
    endDate: toChartDateString(endDate),
    hasNewerWindow: offset > 0,
    hasOlderWindow: offset < maxOffset,
    maxOffset,
    offset,
    startDate: toChartDateString(startDate),
  };
}

export function getLatestChartWindowOffset(
  sourceDates: string[],
  focusDates: string[] = sourceDates,
  windowMonths = 6,
) {
  const dates = sourceDates.filter(isValidChartDate).sort();
  const focusedDates = focusDates.filter(isValidChartDate).sort();
  const latest = focusedDates[focusedDates.length - 1];
  if (!latest) return 0;
  const date = parseChartDate(latest);
  const today = startOfLocalDay(new Date());
  const latestSourceDate = dates.length ? parseChartDate(dates[dates.length - 1]) : date;
  const anchorDate = latestSourceDate > today ? latestSourceDate : today;
  const months = (
    (anchorDate.getFullYear() - date.getFullYear()) * 12
    + anchorDate.getMonth()
    - date.getMonth()
  );
  const requestedOffset = Math.max(
    0,
    months - (addChartMonths(anchorDate, -months) < date ? 1 : 0),
  );
  return buildChartWindow(
    dates.length ? dates : focusedDates,
    requestedOffset,
    windowMonths,
  ).offset;
}

export function addChartMonths(date: Date, months: number) {
  const result = new Date(date);
  const day = result.getDate();
  result.setDate(1);
  result.setMonth(result.getMonth() + months);
  const lastDay = new Date(result.getFullYear(), result.getMonth() + 1, 0).getDate();
  result.setDate(Math.min(day, lastDay));
  return result;
}

export function parseChartDate(value: string) {
  return new Date(`${value.slice(0, 10)}T00:00:00`);
}

export function toChartDateString(date: Date) {
  const localDate = new Date(date);
  localDate.setMinutes(localDate.getMinutes() - localDate.getTimezoneOffset());
  return localDate.toISOString().slice(0, 10);
}

function getMaximumOffset(firstDate: Date, anchorDate: Date, windowMonths: number) {
  let offset = 0;
  while (addChartMonths(addChartMonths(anchorDate, -offset), -windowMonths) > firstDate) {
    offset += 1;
  }
  return offset;
}

function isValidChartDate(value: string) {
  return !Number.isNaN(parseChartDate(value).getTime());
}

function startOfLocalDay(date: Date) {
  return new Date(date.getFullYear(), date.getMonth(), date.getDate());
}
