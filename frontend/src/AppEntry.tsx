import { lazy, Suspense, useEffect, useLayoutEffect, useState } from 'react';
import { isSharedPath } from './utils/boxSharing';
import { createTranslator, getStoredInterfaceLanguage } from './i18n';
import PageLoader from './components/PageLoader';
import { useIsDesktopApp } from './hooks/useIsDesktopApp';

const App = lazy(() => import('./App'));
const SharedAccessApp = lazy(() => import('./components/sharing/SharedAccessApp'));

// Mount exactly one access context. Shared routes never bootstrap the
// institution workspace, its Box collection, recent Boxes, zones or exports.
export default function AppEntry() {
  const [path, setPath] = useState(window.location.pathname);
  const isDesktopApp = useIsDesktopApp();
  const blockedAdministration = path.startsWith('/administration') && !isDesktopApp;
  useLayoutEffect(() => {
    if (!blockedAdministration) return;
    window.history.replaceState(null, '', '/');
    setPath('/');
  }, [blockedAdministration]);
  useEffect(() => {
    const sync = () => setPath(window.location.pathname);
    window.addEventListener('popstate', sync);
    return () => window.removeEventListener('popstate', sync);
  }, []);
  if (blockedAdministration) return null;
  const t = createTranslator(getStoredInterfaceLanguage() ?? 'fr');
  return <Suspense fallback={<PageLoader variant="profile" label={t('pageLoading')} />}>
    {isSharedPath(path) ? <SharedAccessApp key={path} path={path} /> : <App />}
  </Suspense>;
}
