import { useEffect, useRef, useState } from 'react';

import { getOrganizationResourceSignal } from '../api/client';
import { createTemperatureHistory, type TemperatureHistoryState } from '../utils/temperatureHistory';

export function useTemperatureHistory(
  boxId: number | undefined, startDate: string, endDate: string, refreshSource?: unknown,
) {
  const contextSignal = getOrganizationResourceSignal();
  const key = `${boxId}:${startDate}:${endDate}`;
  const [result, setResult] = useState<TemperatureHistoryState & { key: string; contextSignal: AbortSignal; refreshSource: unknown } | null>(null);
  const controller = useRef<ReturnType<typeof createTemperatureHistory> | null>(null);
  useEffect(() => {
    if (boxId == null) return;
    const current = createTemperatureHistory(boxId,
      (state) => setResult({ ...state, key, contextSignal, refreshSource }), contextSignal);
    controller.current = current;
    void current.select({ startDate, endDate });
    return () => { current.dispose(); controller.current = null; };
  }, [boxId, startDate, endDate, key, contextSignal, refreshSource]);
  // Hide the previous window synchronously, before effect cleanup or an A-B-A remount.
  const state = result?.key === key && result.contextSignal === contextSignal && result.refreshSource === refreshSource && !contextSignal.aborted
    ? result : { points: [], loading: boxId != null && !contextSignal.aborted, failed: false };
  return { ...state, retry: () => { void controller.current?.select({ startDate, endDate }); } };
}
