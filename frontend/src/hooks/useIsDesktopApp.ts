import { useEffect, useState } from 'react';

const DESKTOP_APP_QUERY = '(min-width: 1024px) and (hover: hover) and (pointer: fine)';

function getIsDesktopApp() {
  return window.matchMedia(DESKTOP_APP_QUERY).matches;
}

export function useIsDesktopApp() {
  const [isDesktop, setIsDesktop] = useState(() => getIsDesktopApp());

  useEffect(() => {
    const media = window.matchMedia(DESKTOP_APP_QUERY);

    function syncDesktopState() {
      setIsDesktop(media.matches);
    }

    syncDesktopState();
    media.addEventListener('change', syncDesktopState);
    return () => media.removeEventListener('change', syncDesktopState);
  }, []);

  return isDesktop;
}
