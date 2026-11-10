import { useEffect, useMemo, useState } from 'react';
import { apiGetResource, getOrganizationResourceSignal } from '../api/client';
import { isStartingLocation, type HistoricalTransition, type HistoryAssessment, type HistoryCandidate, type HistoryEvent, type StartingLocation } from '../utils/boxHistory';

export type BoxHistoryState = {
  events: HistoryEvent[];
  candidates?: HistoryCandidate[];
  assessments?: HistoryAssessment[];
  historicalTransitions?: HistoricalTransition[];
  startingLocation?: StartingLocation | null;
  loading: boolean;
  failed: boolean;
  retry: () => void;
};

export function useBoxHistory(boxId: number | undefined, refreshKey: unknown): BoxHistoryState {
  const context = getOrganizationResourceSignal();
  const [attempt, setAttempt] = useState(0);
  const scope = useMemo(() => ({ boxId, refreshKey, context, attempt }), [boxId, refreshKey, context, attempt]);
  const [result, setResult] = useState<{ scope: unknown; events: HistoryEvent[]; candidates?: HistoryCandidate[]; assessments?: HistoryAssessment[]; historicalTransitions?: HistoricalTransition[]; startingLocation?: StartingLocation | null; failed: boolean } | null>(null);
  useEffect(() => {
    if (boxId == null || context.aborted) return;
    const controller = new AbortController();
    apiGetResource(`/api/boxes/${boxId}/history/`, { signal: controller.signal, contextSignal: context })
      .then(text => {
        const response = JSON.parse(text) as { events: HistoryEvent[]; candidates?: HistoryCandidate[]; assessments?: HistoryAssessment[]; historical_transitions?: HistoricalTransition[]; starting_location?: StartingLocation | null; };
        const initial = response.starting_location;
        if (initial != null && !isStartingLocation(initial)) throw new Error('Invalid starting location response');
        if (!Array.isArray(response.events)
                  || (response.candidates != null && !Array.isArray(response.candidates))
                  || (response.assessments != null && !Array.isArray(response.assessments))
                  || (response.historical_transitions != null && !Array.isArray(response.historical_transitions))) throw new Error('Invalid history response');
        if (!controller.signal.aborted && !context.aborted) setResult({ scope, events: response.events, candidates: response.candidates ?? [], assessments: response.assessments, historicalTransitions: response.historical_transitions ?? [], startingLocation: response.starting_location ?? null, failed: false });
      }).catch(() => {
        if (!controller.signal.aborted && !context.aborted) setResult({ scope, events: [], failed: true });
      });
    return () => controller.abort();
  }, [scope]);
  const current = result?.scope === scope && !context.aborted ? result : null;
  return { events: current?.events ?? [], candidates: current?.candidates ?? [], assessments: current?.assessments ?? [], historicalTransitions: current?.historicalTransitions ?? [], startingLocation: current?.startingLocation ?? null, loading: boxId != null && !context.aborted && current == null,
    failed: current?.failed ?? false, retry: () => setAttempt(value => value + 1) };
}
