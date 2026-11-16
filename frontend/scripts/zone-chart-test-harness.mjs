// Stand-in for the value returned by useZoneChartReadings (ZoneProbeCharts.tsx).
// It keeps the real shape, so panels that read chart.readings or chart.initial
// render as they would in the app. The defaults match a disabled chart: no
// collected probe, no request, no data, not loading.
const IDLE_WINDOW = Object.freeze({ start: 0, end: 0 });

export function zoneChartReadings({
  data = null, isLoading = false, error = null, window = IDLE_WINDOW, settled = !isLoading,
} = {}) {
  return {
    period: { kind: 'relative', hours: 2 },
    setPeriod() {},
    custom: null,
    setCustom() {},
    readings: { data, window, isLoading, error, retry() {}, revalidate() {} },
    // The first outcome is shared with monitoring only once a read settles.
    initial: { settled, data: settled && error === null ? data : null },
  };
}
