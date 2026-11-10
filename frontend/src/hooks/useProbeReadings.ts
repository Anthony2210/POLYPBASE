import { useCallback, useEffect, useMemo, useState } from 'react';

import { apiGet, getOrganizationResourceSignal } from '../api/client';
import type { ZoneProbeReadings } from '../types';
import type { ProbeWindow } from '../utils/probeChart';
import { ProbeReadingsLoader, type ProbeReadingsView } from '../utils/probeReadings';

const IDLE_WINDOW: ProbeWindow = { start: 0, end: 0 };

/** Pass null while another read already supplies the data: nothing is requested. */
export default function useProbeReadings(zoneId: number, window: ProbeWindow | null) {
  const [revision, refresh] = useState(0);
  const contextSignal = getOrganizationResourceSignal();
  const loader = useMemo(() => new ProbeReadingsLoader(zoneId, (range, signal) => {
    const query = new URLSearchParams({
      start: new Date(range.start).toISOString(), end: new Date(range.end).toISOString(),
      group_sources: '1',
    });
    return apiGet<ZoneProbeReadings>(`/api/thermal-zones/${zoneId}/probe-readings/?${query}`, {
      cache: 'no-store', signal,
    });
  }, () => refresh((version) => version + 1), contextSignal), [contextSignal, zoneId]);

  useEffect(() => {
    loader.activate();
    return () => loader.dispose();
  }, [loader]);
  useEffect(() => { if (window) loader.select(window); }, [loader, window?.start, window?.end]);
  const retry = useCallback(() => loader.retry(), [loader]);
  const revalidate = useCallback(() => { if (window) loader.select(window); }, [loader, window?.start, window?.end]);
  const view = useMemo((): ProbeReadingsView => (window
    ? loader.read(window)
    : { data: null, window: IDLE_WINDOW, isLoading: false, error: null }), [
    loader, window?.start, window?.end, revision, contextSignal.aborted,
  ]);
  return { ...view, retry, revalidate };
}
