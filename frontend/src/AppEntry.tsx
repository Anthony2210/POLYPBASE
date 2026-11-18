import { lazy, Suspense, useEffect, useLayoutEffect, useState } from 'react';
import { isSharedPath } from './utils/boxSharing';
import { createTranslator, getStoredInterfaceLanguage } from './i18n';
import PageLoader from './components/PageLoader';
import { useIsDesktopApp } from './hooks/useIsDesktopApp';

const App = lazy(() => import('./App'));
// Administration stays desktop-only; the retired personal portal is not mounted.
export default function AppEntry() {
  const [path, setPath] = useState(window.location.pathname);
  const isDesktopApp = useIsDesktopApp();
  const blockedAdministration = path.startsWith('/administration') && !isDesktopApp;
  const retiredSharing = isSharedPath(path);
  useLayoutEffect(() => {
    if (!blockedAdministration && !retiredSharing) return;
    window.history.replaceState(null, '', '/');
    setPath('/');
  }, [blockedAdministration, retiredSharing]);
  useEffect(() => {
    const sync = () => setPath(window.location.pathname);
    window.addEventListener('popstate', sync);
    return () => window.removeEventListener('popstate', sync);
  }, []);
  if (blockedAdministration || retiredSharing) return null;
  const t = createTranslator(getStoredInterfaceLanguage() ?? 'fr');
  return <Suspense fallback={<PageLoader variant="profile" label={t('pageLoading')} />}>
    <App />
  </Suspense>;
}
