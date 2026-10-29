import { useCallback, useEffect, useMemo, useState } from 'react';

import { apiGet, getOrganizationResourceSignal } from '../api/client';
import type { ZoneProbeReadings } from '../types';
import type { ProbeWindow } from '../utils/probeChart';
import { ProbeReadingsLoader } from '../utils/probeReadings';

export default function useProbeReadings(zoneId: number, window: ProbeWindow) {
  const [revision, refresh] = useState(0);
  const contextSignal = getOrganizationResourceSignal();
  const loader = useMemo(() => new ProbeReadingsLoader(zoneId, (range, signal) => {
    const query = new URLSearchParams({
      start: new Date(range.start).toISOString(), end: new Date(range.end).toISOString(),
    });
    return apiGet<ZoneProbeReadings>(`/api/thermal-zones/${zoneId}/probe-readings/?${query}`, {
      cache: 'no-store', signal,
    });
  }, () => refresh((version) => version + 1), contextSignal), [contextSignal, zoneId]);

  useEffect(() => {
    loader.activate();
    return () => loader.dispose();
  }, [loader]);
  useEffect(() => { loader.select(window); }, [loader, window.start, window.end]);
  const retry = useCallback(() => loader.retry(), [loader]);
  const revalidate = useCallback(() => loader.select(window), [loader, window.start, window.end]);
  const view = useMemo(() => loader.read(window), [
    loader, window.start, window.end, revision, contextSignal.aborted,
  ]);
  return { ...view, retry, revalidate };
}
