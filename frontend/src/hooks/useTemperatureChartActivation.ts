import { useEffect, useMemo, useRef, useState } from 'react';

import { getOrganizationResourceSignal } from '../api/client';

// Once visible, keep loading and coverage alive until the Box/context is replaced.
export function useTemperatureChartActivation(boxId: number) {
  const contextSignal = getOrganizationResourceSignal();
  const scope = useMemo(() => ({ boxId, contextSignal }), [boxId, contextSignal]);
  const [activatedScope, setActivatedScope] = useState<unknown>(null);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (contextSignal.aborted || activatedScope === scope) return;
    const element = ref.current;
    if (!element) return;
    // Older browsers still get complete temperatures, behind the same concurrency limit.
    if (typeof IntersectionObserver === 'undefined') {
      setActivatedScope(scope);
      return;
    }
    let disposed = false;
    const observer = new IntersectionObserver((entries) => {
      if (disposed || contextSignal.aborted || !entries.some((entry) => entry.isIntersecting)) return;
      setActivatedScope(scope);
      observer.disconnect();
    }, { rootMargin: '0px', threshold: 0 });
    observer.observe(element);
    const stop = () => { disposed = true; observer.disconnect(); };
    contextSignal.addEventListener('abort', stop, { once: true });
    return () => {
      stop();
      contextSignal.removeEventListener('abort', stop);
    };
  }, [scope, activatedScope, contextSignal]);

  return { ref, active: !contextSignal.aborted && activatedScope === scope };
}
