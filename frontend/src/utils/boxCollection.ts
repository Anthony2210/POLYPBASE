import type { BoxItem, Dashboard } from '../types';

export function upsertBoxes(currentBoxes: BoxItem[], incomingBoxes: BoxItem[]) {
  const boxesById = new Map(currentBoxes.map((box) => [box.id, box]));

  for (const box of incomingBoxes) {
    boxesById.set(box.id, box);
  }

  return [...boxesById.values()].sort((left, right) => left.global_code.localeCompare(right.global_code));
}

/**
 * The complete Box list is loaded on demand, so `data.boxes` can hold a partial
 * set of known boxes until a route that needs every box has loaded it.
 */
export type BoxCollectionStatus = 'idle' | 'loading' | 'ready' | 'error';

export type BoxCollectionState = {
  status: BoxCollectionStatus;
  loadedAt: number | null;
};

export const IDLE_BOX_COLLECTION: BoxCollectionState = { status: 'idle', loadedAt: null };

// A loaded list is refreshed in the background when a route needs it again later.
export const BOX_COLLECTION_MAX_AGE_MS = 5 * 60 * 1000;

export function isBoxCollectionStale(state: BoxCollectionState, now: number) {
  return state.status === 'ready' && (state.loadedAt === null || now - state.loadedAt > BOX_COLLECTION_MAX_AGE_MS);
}

export function shouldLoadBoxCollection(state: BoxCollectionState, now: number) {
  return state.status === 'idle' || isBoxCollectionStale(state, now);
}

/**
 * Merge a freshly loaded list into the known boxes. A box that changed, or that
 * was added, while the list was loading is newer than the loaded snapshot.
 */
export function mergeLoadedBoxes(
  currentBoxes: BoxItem[],
  loadedBoxes: BoxItem[],
  baselineBoxes: BoxItem[],
) {
  const baselineById = new Map(baselineBoxes.map((box) => [box.id, box]));
  const newerBoxes = currentBoxes.filter((box) => baselineById.get(box.id) !== box);
  return upsertBoxes(loadedBoxes, newerBoxes);
}

type BoxCollectionNeed = {
  activeTab: string;
  isBoxRoute: boolean;
  hasSearch: boolean;
  zoneId?: number | null;
  zoneHistory?: boolean;
  adminSection?: string;
  isAdminAvailable: boolean;
};

/** Routes and interactions that genuinely need every box of the organization. */
export function needsFullBoxCollection(need: BoxCollectionNeed) {
  if (need.activeTab === 'pilotage') return !need.isBoxRoute && need.hasSearch;
  if (need.activeTab === 'zones') return !(need.zoneId != null && need.zoneHistory);
  if (need.activeTab === 'labels') return true;
  if (need.activeTab === 'admin') return need.isAdminAvailable && need.adminSection === 'transfers';
  return false;
}

/** Box ids behind the dashboard's recent accesses, newest first, without loading boxes. */
export function getRecentBoxIds(dashboard: Dashboard, limit = 6) {
  const ids: number[] = [];
  for (const access of dashboard.recent_accesses) {
    const boxId = access.metadata?.box_id;
    if (typeof boxId === 'number' && Number.isSafeInteger(boxId) && boxId > 0 && !ids.includes(boxId)) {
      ids.push(boxId);
    }
  }
  return ids.slice(0, limit);
}
