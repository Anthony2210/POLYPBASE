import { useEffect, useMemo, useState } from 'react';

import { apiGet, ApiResourceCancelledError, getOrganizationResourceSignal } from '../api/client';
import type { ThermalControlSummary, ThermalZone } from '../types';
import { getErrorMessage } from '../utils/errors';

type Snapshot = {
  scope: object;
  summary?: ThermalControlSummary;
  deadline: number | null;
  error: string | null;
};

export default function useThermalControl(zone: ThermalZone) {
  const contextSignal = getOrganizationResourceSignal();
  // A newly published zone can contain a mutation, even with unchanged IDs/counts.
  const scope = useMemo(() => ({}), [zone, zone.id, zone.organization.id, contextSignal]);
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);

  useEffect(() => {
    if (contextSignal.aborted) return;
    let disposed = false;
    let controller: AbortController | null = null;
    let expiredResponseCount = 0;
    let expiryTimer: ReturnType<typeof setTimeout> | undefined;
    let pollTimer: ReturnType<typeof setTimeout> | undefined;

    const active = () => !disposed && !contextSignal.aborted;
    function stop() {
      clearTimeout(expiryTimer);
      clearTimeout(pollTimer);
      controller?.abort();
    }
    function cancelContext() {
      stop();
      setSnapshot(null);
    }
    function scheduleRefresh(delay: number) {
      clearTimeout(pollTimer);
      pollTimer = setTimeout(() => { void refresh(); }, delay);
    }
    async function refresh() {
      // Focus, expiry and polling share the same in-flight read. Only context
      // disposal cancels it; a slow network must still be able to complete.
      if (!active() || controller !== null) return;
      clearTimeout(pollTimer);
      const request = new AbortController();
      controller = request;
      // Charge the whole round trip against freshness. Browser wall-clock skew
      // cannot extend a server-evaluated summary's lifetime.
      const startedAt = performance.now();
      let nextDelay = 30_000;
      try {
        const summary = await apiGet<ThermalControlSummary>(`/api/thermal-zones/${zone.id}/thermal-control/`, {
          cache: 'no-store', signal: request.signal,
        });
        if (!active() || request.signal.aborted) return;
        clearTimeout(expiryTimer);
        const evaluatedAt = Date.parse(summary.evaluated_at);
        const lifetime = summary.expires_at === null ? null : Date.parse(summary.expires_at) - evaluatedAt;
        const deadline = lifetime === null ? null : startedAt + lifetime;
        const safe = Number.isFinite(evaluatedAt)
          && (deadline === null || (Number.isFinite(deadline) && performance.now() <= deadline));
        setSnapshot({ scope, summary: safe ? summary : undefined, deadline, error: null });
        if (safe) {
          expiredResponseCount = 0;
        } else {
          // Re-evaluate promptly, but back off when transit repeatedly consumes
          // the whole validity window. Never derive a partial mean in the UI.
          nextDelay = Math.min(15_000, 1_000 * 2 ** Math.min(expiredResponseCount, 4));
          expiredResponseCount += 1;
        }
        if (safe && deadline !== null) {
          // The server includes the exact expiry instant. Cross it by 1 ms.
          expiryTimer = setTimeout(() => {
            if (!active()) return;
            setSnapshot(null);
            void refresh();
          }, Math.min(2_147_483_647, Math.max(0, deadline - performance.now()) + 1));
        }
      } catch (requestError) {
        if (!active() || request.signal.aborted) return;
        clearTimeout(expiryTimer);
        expiredResponseCount = 0;
        setSnapshot({
          scope, deadline: null,
          error: requestError instanceof ApiResourceCancelledError ? null : getErrorMessage(requestError),
        });
      } finally {
        if (controller === request) controller = null;
        if (active() && !request.signal.aborted) scheduleRefresh(nextDelay);
      }
    }
    function onVisible() {
      if (document.visibilityState === 'visible') void refresh();
    }
    contextSignal.addEventListener('abort', cancelContext, { once: true });
    globalThis.addEventListener('focus', onVisible);
    document.addEventListener('visibilitychange', onVisible);
    void refresh();
    return () => {
      disposed = true;
      stop();
      contextSignal.removeEventListener('abort', cancelContext);
      globalThis.removeEventListener('focus', onVisible);
      document.removeEventListener('visibilitychange', onVisible);
    };
  }, [scope]);

  const current = !contextSignal.aborted && snapshot?.scope === scope ? snapshot : null;
  return {
    summary: current && (current.deadline === null || performance.now() <= current.deadline)
      ? current.summary : undefined,
    error: current?.error ?? null,
  };
}
