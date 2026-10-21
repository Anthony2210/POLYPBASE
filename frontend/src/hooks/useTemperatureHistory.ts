import { useEffect, useRef, useState } from 'react';

import { getOrganizationResourceSignal } from '../api/client';
import { createTemperatureHistory, loadTemperatureHistory, type TemperatureHistoryState } from '../utils/temperatureHistory';

type HistoryScope = {
  boxId: number | undefined;
  contextSignal: AbortSignal;
  refreshSource: unknown;
  load: typeof loadTemperatureHistory;
};
type HistorySelection = { scope: HistoryScope; startDate: string; endDate: string };

export function useTemperatureHistory(
  boxId: number | undefined, startDate: string, endDate: string, refreshSource?: unknown,
  load = loadTemperatureHistory,
) {
  const contextSignal = getOrganizationResourceSignal();
  const scopeRef = useRef<HistoryScope>({ boxId, contextSignal, refreshSource, load });
  if (scopeRef.current.boxId !== boxId || scopeRef.current.contextSignal !== contextSignal
    || !Object.is(scopeRef.current.refreshSource, refreshSource) || scopeRef.current.load !== load) {
    scopeRef.current = { boxId, contextSignal, refreshSource, load };
  }
  const scope = scopeRef.current;
  const selectionRef = useRef<HistorySelection>({ scope, startDate, endDate });
  if (selectionRef.current.scope !== scope || selectionRef.current.startDate !== startDate
    || selectionRef.current.endDate !== endDate) {
    selectionRef.current = { scope, startDate, endDate };
  }
  const selection = selectionRef.current;
  const [result, setResult] = useState<{ selection: HistorySelection; state: TemperatureHistoryState } | null>(null);
  const controller = useRef<{ scope: HistoryScope; history: ReturnType<typeof createTemperatureHistory> } | null>(null);

  useEffect(() => {
    if (scope.boxId == null) return;
    const entry = { scope, history: createTemperatureHistory(scope.boxId, () => {}, scope.contextSignal, scope.load) };
    controller.current = entry;
    return () => {
      entry.history.dispose();
      if (controller.current === entry) controller.current = null;
    };
  }, [scope]);

  const select = () => {
    const entry = controller.current;
    if (selectionRef.current !== selection || entry?.scope !== scope) return;
    void entry.history.select({ startDate, endDate }, (state) => {
      if (selectionRef.current === selection) setResult({ selection, state });
    });
  };
  useEffect(() => {
    const entry = controller.current;
    select();
    return () => entry?.history.cancel();
  }, [selection]);

  // Selection identities also change on A-B-A navigation before effect cleanup.
  // Only completed coverage from the unchanged Box/context may bridge a date change.
  const state = !contextSignal.aborted && result?.selection === selection
    ? result.state
    : controller.current?.scope === scope
      ? controller.current.history.snapshot({ startDate, endDate })
      : { points: [], loading: boxId != null && !contextSignal.aborted, failed: false };
  return { ...state, retry: select };
}
