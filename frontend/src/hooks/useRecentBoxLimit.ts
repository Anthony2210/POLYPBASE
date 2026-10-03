import { useEffect, useState } from 'react';

import { TABLET_LAYOUT_QUERY } from './useIsTabletLayout';

export function useRecentBoxLimit() {
  // Portrait tablets use the phone search layout, but keep the tablet recent count.
  const [limit, setLimit] = useState(() => window.matchMedia(TABLET_LAYOUT_QUERY).matches ? 6 : 5);

  useEffect(() => {
    const media = window.matchMedia(TABLET_LAYOUT_QUERY);
    function syncLimit() {
      setLimit(media.matches ? 6 : 5);
    }
    syncLimit();
    media.addEventListener('change', syncLimit);
    return () => media.removeEventListener('change', syncLimit);
  }, []);

  return limit;
}
