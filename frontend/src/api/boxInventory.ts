import type { BoxInventoryResponse } from '../types';
import { apiGet } from './client';

type InventoryCounters = Pick<BoxInventoryResponse['summary'], 'pending_review_count' | 'active_without_location_count'>;

export async function getBoxInventoryCounters(response: { summary?: Partial<InventoryCounters> }): Promise<InventoryCounters> {
  const summary = response.summary;
  // Older API responses expose filtered totals but do not yet include summary.
  const [pendingCount, unlocatedCount] = await Promise.all([
    summary?.pending_review_count ?? apiGet<BoxInventoryResponse>(
      '/api/admin/box-inventory/?limit=1&offset=0&status=pending_review',
    ).then((result) => result.count),
    summary?.active_without_location_count ?? apiGet<BoxInventoryResponse>(
      '/api/admin/box-inventory/?limit=1&offset=0&status=active&location=none',
    ).then((result) => result.count),
  ]);
  return { pending_review_count: pendingCount, active_without_location_count: unlocatedCount };
}
