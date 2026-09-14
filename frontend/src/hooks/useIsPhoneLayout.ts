import { useEffect, useState } from 'react';

const PHONE_LAYOUT_QUERY = '(max-width: 759px), (max-width: 900px) and (orientation: portrait)';

function getIsPhoneLayout() {
  return window.matchMedia(PHONE_LAYOUT_QUERY).matches;
}

export function useIsPhoneLayout() {
  const [isPhone, setIsPhone] = useState(() => getIsPhoneLayout());

  useEffect(() => {
    const media = window.matchMedia(PHONE_LAYOUT_QUERY);

    function syncPhoneState() {
      setIsPhone(media.matches);
    }

    syncPhoneState();
    media.addEventListener('change', syncPhoneState);
    return () => media.removeEventListener('change', syncPhoneState);
  }, []);

  return isPhone;
}
