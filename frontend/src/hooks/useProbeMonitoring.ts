import { useCallback, useEffect, useState } from 'react';

import { apiGet, ApiResourceCancelledError } from '../api/client';
import type { ThermalZone, ZoneProbeMonitoring, ZoneProbeReadings } from '../types';
import { getErrorMessage } from '../utils/errors';
import { probeMonitoringDelay } from '../utils/probeChart';

export default function useProbeMonitoring(zone: ThermalZone) {
  const [snapshot, setSnapshot] = useState<{ zoneId: number; monitoring: ZoneProbeMonitoring } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);
  const revalidate = useCallback(() => setReloadKey((key) => key + 1), []);
  const hasCollectedProbe = zone.probes.some((probe) => probe.is_collected);
  const monitoring = snapshot?.zoneId === zone.id ? snapshot.monitoring : zone.probe_monitoring;

  useEffect(() => {
    setSnapshot(null);
    setError(null);
  }, [zone.id, zone.probe_monitoring]);

  useEffect(() => {
    if (!hasCollectedProbe) return;
    let disposed = false;
    let timer: ReturnType<typeof setTimeout>;
    let controller: AbortController | null = null;
    async function refresh() {
      clearTimeout(timer);
      controller?.abort();
      const request = new AbortController();
      controller = request;
      const end = Date.now();
      const query = new URLSearchParams({
        start: new Date(end - 60 * 1000).toISOString(),
        end: new Date(end).toISOString(),
      });
      let delay = 60 * 1000;
      try {
        const response = await apiGet<ZoneProbeReadings>(`/api/thermal-zones/${zone.id}/probe-readings/?${query}`, {
          cache: 'no-store', signal: request.signal,
        });
        if (disposed || request.signal.aborted) return;
        setSnapshot({ zoneId: zone.id, monitoring: response.monitoring });
        setError(null);
        delay = probeMonitoringDelay(response.monitoring, Date.now());
      } catch (requestError) {
        if (disposed || request.signal.aborted || requestError instanceof ApiResourceCancelledError) return;
        setError(getErrorMessage(requestError));
      }
      if (!disposed && !request.signal.aborted) timer = setTimeout(refresh, delay);
    }
    function onVisible() {
      if (document.visibilityState === 'visible') void refresh();
    }
    void refresh();
    globalThis.addEventListener('focus', onVisible);
    document.addEventListener('visibilitychange', onVisible);
    return () => {
      disposed = true;
      clearTimeout(timer);
      controller?.abort();
      globalThis.removeEventListener('focus', onVisible);
      document.removeEventListener('visibilitychange', onVisible);
    };
  }, [hasCollectedProbe, reloadKey, zone.id, zone.probe_monitoring]);

  return { monitoring, error, revalidate };
}
