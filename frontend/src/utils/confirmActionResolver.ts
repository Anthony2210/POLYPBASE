export type PendingResolver<T> = {
  request: (settle: (value: T) => void) => boolean;
  settle: (value: T) => boolean;
  isPending: () => boolean;
};

// Keeps at most one unresolved confirmation. A second request is declined so it
// can never silently replace and orphan the first resolver.
export function createPendingResolver<T>(): PendingResolver<T> {
  let pending: ((value: T) => void) | null = null;

  return {
    request(settle) {
      if (pending) return false;
      pending = settle;
      return true;
    },
    settle(value) {
      if (!pending) return false;
      const resolve = pending;
      pending = null;
      resolve(value);
      return true;
    },
    isPending() {
      return pending !== null;
    },
  };
}
