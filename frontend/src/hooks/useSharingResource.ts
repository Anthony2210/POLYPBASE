import { useCallback, useEffect, useRef, useState } from 'react';

// The resource key is checked during render too: an old result is never visible
// for one frame while a new route/account's effect is still waiting to run.
export function useSharingResource<T>(key: string, load: (signal: AbortSignal) => Promise<T>) {
  const loader = useRef(load);
  loader.current = load;
  const generation = useRef(0);
  const controller = useRef<AbortController>();
  const [state, setState] = useState<{ key: string; data?: T; error?: unknown }>({ key });
  const refresh = useCallback(() => {
    controller.current?.abort();
    const current = ++generation.current;
    const request = new AbortController();
    controller.current = request;
    setState({ key });
    const isCurrent = () => current === generation.current && !request.signal.aborted;
    const read = loader.current;
    void Promise.resolve().then(() => {
      request.signal.throwIfAborted();
      return read(request.signal);
    }).then(data => {
      if (isCurrent()) setState(previous => isCurrent() ? { key, data } : previous);
    }, error => {
      if (isCurrent()) setState(previous => isCurrent() ? { key, error } : previous);
    });
  }, [key]);
  useEffect(() => {
    refresh();
    return () => { ++generation.current; controller.current?.abort(); };
  }, [refresh]);
  const visible = state.key === key ? state : { key };
  return { data: visible.data, error: visible.error, loading: visible.data === undefined && visible.error === undefined, refresh };
}
