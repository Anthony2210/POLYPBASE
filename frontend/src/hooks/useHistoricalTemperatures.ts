import { useEffect, useMemo, useState } from 'react';

import { getOrganizationResourceSignal } from '../api/client';
import type { HistoricalTemperaturePoint } from '../types';
import { loadHistoricalTemperatures } from '../utils/historicalTemperature';

type Result = {
  scope: unknown;
  points: HistoricalTemperaturePoint[];
  failed: boolean;
};

// Loads the Box's whole chart extent once, so moving the visible window does not refetch.
// `refreshKey` identifies the published Box projection: count, week or identity corrections can
// change historical evidence eligibility, so
// points loaded for another key are discarded at once rather than shown stale.
export function useHistoricalTemperatures(
  boxId: number | undefined, startDate: string, endDate: string, refreshKey: unknown = '',
) {
  const contextSignal = getOrganizationResourceSignal();
  const [attempt, setAttempt] = useState(0);
  const [result, setResult] = useState<Result | null>(null);
  const scope = useMemo(
    () => ({ boxId, startDate, endDate, refreshKey, contextSignal, attempt }),
    [boxId, startDate, endDate, refreshKey, contextSignal, attempt],
  );

  useEffect(() => {
    if (boxId == null || contextSignal.aborted) return;
    const controller = new AbortController();
    const stop = () => controller.abort();
    contextSignal.addEventListener('abort', stop, { once: true });
    loadHistoricalTemperatures(boxId, { startDate, endDate }, controller.signal).then(
      (points) => { if (!controller.signal.aborted) setResult({ scope, points, failed: false }); },
      () => { if (!controller.signal.aborted) setResult({ scope, points: [], failed: true }); },
    );
    return () => { stop(); contextSignal.removeEventListener('abort', stop); };
  }, [scope]);

  // A result for another Box, extent, institution or attempt is never displayed.
  const current = result?.scope === scope ? result : null;
  return {
    points: current?.points ?? [],
    loading: boxId != null && !contextSignal.aborted && current == null,
    failed: current?.failed ?? false,
    retry: () => setAttempt((value) => value + 1),
  };
}
