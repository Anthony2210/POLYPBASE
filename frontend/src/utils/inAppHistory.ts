export type HistoryOrganization = number | string | null;

export interface InAppHistoryContext {
  /** pathname + search + hash, not a URL or a route parser's default route. */
  path: string;
  organization: HistoryOrganization;
}

export interface InAppHistoryAdapter {
  readonly state: unknown;
  pushState(data: unknown, unused: string, url?: string | URL | null): void;
  replaceState(data: unknown, unused: string, url?: string | URL | null): void;
  back(): void;
}

export type InAppNavigationResult = 'push' | 'replace' | 'noop' | 'pending';
export type InAppBackResult = 'back' | 'fallback' | 'pending';

const STATE_KEY = '__polypbaseInAppHistory';
let sessionSequence = 0;

interface Entry extends InAppHistoryContext {
  id: number;
}

interface Marker {
  session: string;
  id: number;
  originalState?: unknown;
}

/**
 * Create once per mounted app lifetime, never from persisted history state.
 * The caller owns route rendering and calls sync with the live location on every
 * popstate. All History writes must use this instance; it never installs listeners.
 * isAppPath must positively recognize allowed routes (not a parser's '/' fallback).
 */
export function createInAppHistory(
  history: InAppHistoryAdapter,
  initial: InAppHistoryContext,
  isAppPath: (path: string) => boolean,
) {
  // This is an ownership token, not an authentication token. The ledger, not the
  // marker alone, proves that an entry was visited during this instance's lifetime.
  const session = `${Date.now()}-${++sessionSequence}-${Math.random().toString(36).slice(2)}`;
  let nextId = 0;
  let entries: Entry[] = [];
  let cursor = 0;
  let pendingBack = false;

  function checkPath(path: string) {
    // URL's base is only a parser sentinel; no request or browser global is used.
    if (!path.startsWith('/') || path.startsWith('//') || /[\\\s\u0000-\u001f\u007f]/.test(path)) {
      throw new Error('Expected an absolute in-app path');
    }
    const parsed = new URL(path, 'https://in-app.invalid');
    if (`${parsed.pathname}${parsed.search}${parsed.hash}` !== path) {
      throw new Error('Expected a canonical in-app path');
    }
    try {
      const decoded = decodeURIComponent(parsed.pathname);
      // Box codes are opaque encoded segments, so %2F is not a route separator.
      const isBoxPath = /^\/boxes\/[^/]+\/?$/.test(parsed.pathname);
      if (/[\\\u0000-\u001f\u007f]/.test(decoded)
        || /(?:^|\/)\.{1,2}(?:\/|$)/.test(decoded)
        || (/%2f/i.test(parsed.pathname) && !isBoxPath)) {
        throw new Error('Unsafe in-app path');
      }
    } catch {
      throw new Error('Unsafe in-app path');
    }
  }

  function recognized(path: string) {
    checkPath(path);
    return isAppPath(path);
  }

  function marker(): Marker | null {
    const state = history.state;
    if (!state || typeof state !== 'object' || Array.isArray(state)) return null;
    const value = (state as Record<string, unknown>)[STATE_KEY];
    if (!value || typeof value !== 'object') return null;
    const candidate = value as Marker;
    return candidate.session === session && Number.isSafeInteger(candidate.id) ? candidate : null;
  }

  function stateFor(entry: Entry, preserve: boolean) {
    const owned: Marker = { session, id: entry.id };
    const state = preserve ? history.state : null;
    if (state && typeof state === 'object' && !Array.isArray(state)
      && Object.prototype.toString.call(state) === '[object Object]') {
      const previous = (state as Record<string, unknown>)[STATE_KEY] as Marker | undefined;
      if (previous && typeof previous === 'object' && 'originalState' in previous) {
        owned.originalState = previous.originalState;
      }
      return { ...state, [STATE_KEY]: owned };
    }
    // History state may also be a primitive, array, Date, etc. Keep it intact in
    // the owned envelope rather than discarding it or pretending it is a record.
    if (state !== null && state !== undefined) owned.originalState = state;
    return { [STATE_KEY]: owned };
  }

  function sameContext(entry: Entry, context: InAppHistoryContext) {
    return entry.path === context.path && entry.organization === context.organization;
  }

  function verifiedIndex(context: InAppHistoryContext) {
    const owned = marker();
    return owned ? entries.findIndex((entry) => entry.id === owned.id && sameContext(entry, context)) : -1;
  }

  function reset(context: InAppHistoryContext): void {
    checkPath(context.path);
    const entry = { ...context, id: ++nextId };
    history.replaceState(stateFor(entry, true), '', entry.path);
    entries = [entry];
    cursor = 0;
    pendingBack = false;
  }

  /** Use the live location and active org, never an event's stale state snapshot. */
  function sync(context: InAppHistoryContext): void {
    checkPath(context.path);
    const index = verifiedIndex(context);
    if (index < 0) {
      // Unknown traversal, refresh ownership, or org mismatch starts a new root.
      reset(context);
      return;
    }
    cursor = index;
    pendingBack = false;
  }

  function canGoBack(context: InAppHistoryContext): boolean {
    if (pendingBack || verifiedIndex(context) !== cursor || cursor === 0) return false;
    const previous = entries[cursor - 1];
    return previous.organization === context.organization && previous.path !== context.path
      && recognized(context.path) && recognized(previous.path);
  }

  function push(context: InAppHistoryContext): InAppNavigationResult {
    if (!recognized(context.path)) throw new Error('Cannot push an unrecognized app route');
    if (pendingBack) return 'pending';
    const current = entries[cursor];
    const owned = marker();
    if (!owned || owned.id !== current.id || context.organization !== current.organization) {
      // Do not infer adjacency across a write outside this helper or an org switch.
      reset(context);
      return 'replace';
    }
    if (sameContext(current, context)) return 'noop';
    const entry = { ...context, id: ++nextId };
    history.pushState(stateFor(entry, false), '', entry.path);
    // A push after browser back destroys the old forward branch.
    entries = [...entries.slice(0, cursor + 1), entry];
    cursor += 1;
    return 'push';
  }

  /** Canonicalization preserves provenance; redirects should pass invalidate=true. */
  function replace(context: InAppHistoryContext, invalidate = false): InAppNavigationResult {
    checkPath(context.path);
    if (pendingBack) return 'pending';
    const current = entries[cursor];
    const owned = marker();
    if (invalidate || !isAppPath(context.path) || !owned || owned.id !== current.id
      || current.organization !== context.organization || !isAppPath(current.path)) {
      reset(context);
      return 'replace';
    }
    if (sameContext(current, context)) return 'noop';
    const entry = { ...context, id: ++nextId };
    history.replaceState(stateFor(entry, true), '', entry.path);
    entries[cursor] = entry;
    return 'replace';
  }

  /** Only a proven adjacent entry allows traversal. Otherwise replace, never push. */
  function back(context: InAppHistoryContext, fallbackPath: string): InAppBackResult {
    if (!recognized(fallbackPath)) throw new Error('Expected a recognized app fallback');
    if (pendingBack) return 'pending';
    if (canGoBack(context)) {
      pendingBack = true;
      try {
        history.back();
      } catch (error) {
        pendingBack = false;
        throw error;
      }
      return 'back';
    }
    reset({ path: fallbackPath, organization: context.organization });
    return 'fallback';
  }

  reset(initial);
  return { push, replace, back, sync, reset, canGoBack };
}
