export type ChartLocation = {
  id: number | string;
  name: string;
  startsAt: string;
  endsAt: string | null;
  endDateUnknown?: boolean;
};

export type ChartLocationPeriod = ChartLocation & {
  endDate: string;
  startDate: string;
};

export function resolveChartLocationPeriods(
  locations: ChartLocation[],
  openEndDate: string,
  unknownEndFallback: string,
): ChartLocationPeriod[] {
  const ordered = [...locations].sort((left, right) => left.startsAt.localeCompare(right.startsAt));

  return ordered.map((location, index) => {
    const startDate = location.startsAt.slice(0, 10);
    const nextStartDate = ordered[index + 1]?.startsAt.slice(0, 10);
    const endDate = location.endsAt?.slice(0, 10)
      ?? (location.endDateUnknown
        ? nextStartDate ?? laterDate(startDate, unknownEndFallback)
        : openEndDate);

    return {
      ...location,
      endDate: laterDate(startDate, endDate),
      startDate,
    };
  });
}

export function findChartLocationAtDate(locations: ChartLocation[], date: string) {
  return resolveChartLocationPeriods(locations, date, date)
    .reverse()
    .find((location) => location.startDate <= date && location.endDate >= date)
    ?.name;
}

function laterDate(first: string, second: string) {
  return first > second ? first : second;
}
