import { useEffect, useState } from 'react';

const TABLET_LAYOUT_QUERY = '(min-width: 760px) and (max-width: 1023px), (min-width: 760px) and (max-width: 1180px) and (pointer: coarse)';
const PHONE_LAYOUT_QUERY = '(max-width: 759px), (max-width: 900px) and (orientation: portrait)';

function getIsTabletLayout() {
  return window.matchMedia(TABLET_LAYOUT_QUERY).matches
    && !window.matchMedia(PHONE_LAYOUT_QUERY).matches;
}

export function useIsTabletLayout() {
  const [isTablet, setIsTablet] = useState(() => getIsTabletLayout());

  useEffect(() => {
    const tabletMedia = window.matchMedia(TABLET_LAYOUT_QUERY);
    const phoneMedia = window.matchMedia(PHONE_LAYOUT_QUERY);

    function syncTabletState() {
      setIsTablet(tabletMedia.matches && !phoneMedia.matches);
    }

    syncTabletState();
    tabletMedia.addEventListener('change', syncTabletState);
    phoneMedia.addEventListener('change', syncTabletState);
    return () => {
      tabletMedia.removeEventListener('change', syncTabletState);
      phoneMedia.removeEventListener('change', syncTabletState);
    };
  }, []);

  return isTablet;
}
