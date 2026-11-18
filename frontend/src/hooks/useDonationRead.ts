import { useEffect, useState } from 'react';
import { getOrganizationResourceSignal, getStoredActiveOrganizationId } from '../api/client';

type ReadState<T> = { status: 'loading' } | { status: 'error'; error: unknown } | { status: 'ready'; data: T };

export default function useDonationRead<T>(organizationId: number, resource: string, read: (signal: AbortSignal) => Promise<T>) {
  const [attempt, setAttempt] = useState(0);
  const [value, setValue] = useState<{ key: string; context: AbortSignal; state: ReadState<T> } | null>(null);
  const key = `${organizationId}:${resource}:${attempt}`;
  useEffect(() => {
    const controller = new AbortController();
    const context = getOrganizationResourceSignal();
    const current = () => !controller.signal.aborted && !context.aborted && getStoredActiveOrganizationId() === organizationId;
    const publish = (state: ReadState<T>) => {
      if (current()) setValue((previous) => current() ? { key, context, state } : previous);
    };
    publish({ status: 'loading' });
    void read(controller.signal).then(
      (data) => publish({ status: 'ready', data }),
      (error: unknown) => publish({ status: 'error', error }),
    );
    return () => controller.abort();
    // The resource key identifies the read; callbacks may be inline without restarting it.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);
  const state: ReadState<T> = value?.key === key && !value.context.aborted && getStoredActiveOrganizationId() === organizationId
    ? value.state : { status: 'loading' };
  return { state, refresh: () => setAttempt((previous) => previous + 1) };
}
