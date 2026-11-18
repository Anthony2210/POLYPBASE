import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import { apiGet, ApiResourceCancelledError, getOrganizationResourceSignal } from '../api/client';
import type { ThermalZone, ZoneThermalMonitoring } from '../types';
import { getErrorMessage } from '../utils/errors';

type Snapshot = {
  scope: object;
  data: ZoneThermalMonitoring | null;
  error: string | null;
  isLoading: boolean;
};

export default function useThermalMonitoring(zone: ThermalZone) {
  const contextSignal = getOrganizationResourceSignal();
  const scope = useMemo(() => ({}), [zone, zone.id, zone.organization.id, contextSignal]);
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const refreshRef = useRef<{ scope: object; refresh: () => Promise<void> } | null>(null);
  const retry = useCallback(() => {
    if (refreshRef.current?.scope === scope) void refreshRef.current.refresh();
  }, [scope]);

  useEffect(() => {
    if (contextSignal.aborted) return;
    let disposed = false;
    let controller: AbortController | null = null;
    let pollTimer: ReturnType<typeof setTimeout> | undefined;

    const active = () => !disposed && !contextSignal.aborted;
    function stop() {
      clearTimeout(pollTimer);
      controller?.abort();
    }
    function cancelContext() {
      stop();
      setSnapshot(null);
    }
    async function refresh() {
      // Foreground events and retry share a slow request rather than replacing it.
      if (!active() || controller !== null || document.visibilityState !== 'visible') return;
      clearTimeout(pollTimer);
      const request = new AbortController();
      controller = request;
      // A rolling window must not display the preceding window during a new read.
      setSnapshot({ scope, data: null, error: null, isLoading: true });
      try {
        const data = await apiGet<ZoneThermalMonitoring>(`/api/thermal-zones/${zone.id}/thermal-monitoring/`, {
          cache: 'no-store', signal: request.signal,
        });
        if (!active() || request.signal.aborted) return;
        if (data.zone_id !== zone.id) throw new Error('Thermal monitoring response belongs to another zone.');
        setSnapshot({ scope, data, error: null, isLoading: false });
      } catch (requestError) {
        if (!active() || request.signal.aborted) return;
        setSnapshot({
          scope, data: null, isLoading: false,
          error: requestError instanceof ApiResourceCancelledError ? null : getErrorMessage(requestError),
        });
      } finally {
        if (controller === request) controller = null;
        if (active() && !request.signal.aborted && document.visibilityState === 'visible') {
          pollTimer = setTimeout(() => { void refresh(); }, 30_000);
        }
      }
    }
    function onVisible() {
      if (document.visibilityState === 'visible') void refresh();
      else clearTimeout(pollTimer);
    }
    refreshRef.current = { scope, refresh };
    contextSignal.addEventListener('abort', cancelContext, { once: true });
    globalThis.addEventListener('focus', onVisible);
    document.addEventListener('visibilitychange', onVisible);
    void refresh();
    return () => {
      disposed = true;
      stop();
      if (refreshRef.current?.scope === scope) refreshRef.current = null;
      contextSignal.removeEventListener('abort', cancelContext);
      globalThis.removeEventListener('focus', onVisible);
      document.removeEventListener('visibilitychange', onVisible);
    };
  }, [scope]);

  const current = !contextSignal.aborted && snapshot?.scope === scope ? snapshot : null;
  return {
    data: current?.data ?? null,
    error: current?.error ?? null,
    isLoading: !contextSignal.aborted && (current?.isLoading ?? true),
    retry,
  };
}
