import { ArrowLeft, CirclePause, CirclePlay, GitFork, Pencil, Route } from 'lucide-react';
import {
  lazy,
  Suspense,
  type FormEvent,
  type KeyboardEvent,
  type PointerEvent,
  type ReactNode,
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from 'react';

import {
  ApiError,
  ApiResourceCancelledError,
  apiDelete,
  apiGet,
  apiPatch,
  apiPost,
  getStoredActiveOrganizationId,
  setActiveOrganizationContext,
} from './api/client';
import { getBoxStatusPresentation } from './boxStatus';
import {
  createTranslator,
  getStoredInterfaceLanguage,
  resolveLanguage,
  setStoredInterfaceLanguage,
  type Language,
  type TranslationKey,
  type Translator,
} from './i18n';
import type { AdminSectionKey, EditableMeasurement } from './components/AdminView';
import BoxLifecycleModal, {
  type BoxLifecycleAction,
  type BoxLifecycleSubmission,
} from './components/BoxLifecycleModal';

// A measurement handed over by the history so the box sheet can open with it
// already filled in.
type HistoryMeasurementPrefill = EditableMeasurement;
import type { BoxInsightTab } from './components/BoxInsights';
import { useConfirmAction, type ConfirmActionOptions } from './components/ConfirmActionModal';
import ApplicationErrorNotice from './components/ApplicationErrorNotice';
import DetailBackButton from './components/DetailBackButton';
import { RowActionMenu, type RowActionMenuItem } from './components/RowActionMenu';
import LoginPage from './components/LoginPage';
import PasswordResetPage from './components/PasswordResetPage';
import MeasurementSaveButton from './components/MeasurementSaveButton';
import ModalPortal from './components/ModalPortal';
import MoveBoxModal from './components/MoveBoxModal';
import PageLoader from './components/PageLoader';
import PolypbaseIcon, { type PolypbaseIconName } from './components/PolypbaseIcon';
import ProfileView from './components/ProfileView';
import QuickCountButtons from './components/QuickCountButtons';
import QuickStrainCreator, { type QuickCreatedStrain } from './components/QuickStrainCreator';
import QrLabel from './components/QrLabel';
import QrLabelModal from './components/QrLabelModal';
import SearchField from './components/SearchField';
import SubcultureModal from './components/SubcultureModal';
import TabletQrScanner from './components/TabletQrScanner';
import TabletQrScannerModal from './components/TabletQrScannerModal';
import { useIsDesktopApp } from './hooks/useIsDesktopApp';
import { useIsPhoneLayout } from './hooks/useIsPhoneLayout';
import { useIsTabletLayout } from './hooks/useIsTabletLayout';
import { useRecentBoxLimit } from './hooks/useRecentBoxLimit';
import type {
  BiologicalMeasurement,
  BoxActivatePayload,
  BoxCreatePayload,
  BoxDeactivatePayload,
  BoxDetail,
  BoxInitialLocationPayload,
  BoxInventoryBatchQualifyPayload,
  BoxInventoryBatchResult,
  BoxItem,
  BoxLineage,
  BoxMovement,
  BoxMovePayload,
  BoxQualifyPayload,
  Dashboard,
  CurrentPolypState,
  ExportOptions,
  LineageGraph,
  Organization,
  OverviewBox,
  OverviewResponse,
  PaginatedResponse,
  Probe,
  SubculturePayload,
  SubcultureResult,
  ThermalZone,
  UserProfile,
  ZoneSalinityMeasurement,
} from './types';
import type {
  BoxTransferPayload,
  BoxTransferResult,
  ManualSalinityPayload,
  ManualSalinityUpdatePayload,
  ManualTemperaturePayload,
  OrganizationPayload,
  ProbePayload,
  ThermalZonePayload,
  TaxonomyReferences,
} from './types/admin';
import { getAccountMemberRoleLabel } from './utils/accountMembers';
import {
  IDLE_BOX_COLLECTION,
  getRecentBoxIds,
  mergeLoadedBoxes,
  needsFullBoxCollection,
  shouldLoadBoxCollection,
  upsertBoxes,
  type BoxCollectionState,
} from './utils/boxCollection';
import { filterBoxes } from './utils/boxLookup';
import {
  findMeasurementForWeek,
  formatMeasurementCount,
  getMeasurementEditorMode,
  getMeasurementFormValues,
  isMeasurementEditWindowExpired,
  isMeasurementPayloadUnchanged,
  isMeasurementWeekConflict,
} from './utils/boxMeasurement';
import { formatDisplayDate } from './utils/dateFormat';
import { getErrorMessage } from './utils/errors';
import {
  getPasswordResetRoute,
  isPublicAuthPath,
  requiresSignInRecovery,
  shouldRedirectToLogin,
} from './utils/authRouting';
import { formatBiologicalSalinity, stepBiologicalSalinity } from './utils/biologicalSalinity';
import { triggerHaptic } from './utils/haptics';
import { createInAppHistory } from './utils/inAppHistory';
import { PHONE_NAVIGATION_ITEMS, type PhoneDestination } from './utils/phoneNavigation';
import { buildQrLabelItem, getBoxQrImageUrl, getBoxScanUrl, type QrLabelItem } from './utils/qrLabels';
import { isRouteRequestCurrent } from './utils/routeSafety';

const AdminView = lazy(() => import('./components/AdminView'));
const BoxInsights = lazy(() => import('./components/BoxInsights'));
const MeasurementHistoryModal = lazy(() =>
  import('./components/BoxInsights').then((module) => ({ default: module.MeasurementHistoryModal })),
);
const ExportsView = lazy(() => import('./components/ExportsView'));
const LabelsView = lazy(() => import('./components/LabelsView'));
const OverviewView = lazy(() => import('./components/OverviewView'));
const ZoneDetailPage = lazy(() =>
  import('./components/ZonesView').then((module) => ({ default: module.ZoneDetailPage })),
);
const ZoneBoxesPage = lazy(() =>
  import('./components/ZonesView').then((module) => ({ default: module.ZoneBoxesPage })),
);
const ZoneMovementHistoryPage = lazy(() => import('./components/ZoneMovementHistory'));
const ZonesView = lazy(() =>
  import('./components/ZonesView').then((module) => ({ default: module.ZonesView })),
);

// Vite provides its exact development proxy origin; production accepts only the app origin.
declare const __API_PROXY_ORIGIN__: string | null;

// The full box list is filtered client-side and loaded on demand; exhaust pages rather than assuming a total limit.
const BOX_LIST_LIMIT = 100;
const PILOTAGE_RESULT_LIMIT = 15;
const PHONE_RESULT_LIMIT = 5;
const DIALOG_FOCUSABLE_SELECTOR =
  'a[href], button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled), [tabindex]:not([tabindex="-1"])';

// Biological controls step by a tenth while preserving the field's hundredths.
// The field starts on the control
// salinity of the box's zone -- the environment it is known to sit in -- and the
// technician overrides it when the refractometer disagrees. It stays empty while
// the zone has no salinity set, rather than storing a value nobody measured.
const SALINITY_STEP = 0.1;

type TabId = 'pilotage' | 'overview' | 'zones' | 'exports' | 'labels' | 'admin' | 'profile';

const TAB_ICONS: Record<TabId, PolypbaseIconName> = {
  pilotage: 'box-alt',
  overview: 'overview',
  zones: 'location',
  exports: 'export-data',
  labels: 'qr-scan',
  admin: 'settings',
  profile: 'user',
};

const SIDEBAR_COLLAPSED_STORAGE_KEY = 'polypbase.sidebarCollapsed';

function getStoredSidebarCollapsed(): boolean {
  try {
    return window.localStorage.getItem(SIDEBAR_COLLAPSED_STORAGE_KEY) === 'true';
  } catch {
    return false;
  }
}

function setStoredSidebarCollapsed(collapsed: boolean): void {
  try {
    window.localStorage.setItem(SIDEBAR_COLLAPSED_STORAGE_KEY, String(collapsed));
  } catch {
    // Local storage unavailable
  }
}

type AppData = {
  boxes: BoxItem[];
  boxDetails: Record<number, BoxDetail>;
  zones: ThermalZone[];
  dashboard: Dashboard | null;
  overview: OverviewBox[] | null;
  exportOptions: ExportOptions | null;
  profile: UserProfile | null;
};

type ApplicationError = {
  message: string;
  requiresAuthentication: boolean;
};

type MeasurementPayload = {
  measured_on: string;
  polyp_count: number;
  ephyrae_count: number;
  salinity_psu: string | null;
  notes: string;
};

type RouteState = {
  tab: TabId;
  boxCode: string | null;
  boxId: number | null;
  scanBoxId?: number;
  zoneId?: number | null;
  zoneBoxes?: boolean;
  zoneHistory?: boolean;
  zoneHistoryDirection?: 'arrival' | 'departure';
  adminSection?: AdminSectionKey;
};

const ADMIN_SECTION_PATHS: Record<AdminSectionKey, string> = {
  accounts: '/administration/team',
  inventory: '/administration/box-inventory',
  references: '/administration/reference-data',
  environment: '/administration/laboratory',
  transfers: '/administration/transfers',
  history: '/administration/history',
  organizations: '/administration/institutions',
};


type TFunction = Translator;
type ConfirmAction = (options: ConfirmActionOptions) => Promise<boolean>;

const labTabs: TabId[] = ['pilotage', 'overview', 'zones', 'labels', 'profile'];
const desktopTabs: TabId[] = ['pilotage', 'overview', 'zones', 'exports', 'labels', 'profile'];

export default function App() {
  const [route, setRoute] = useState<RouteState>(() => getCurrentRoute());
  const [isLoginRoute, setIsLoginRoute] = useState(() => window.location.pathname === '/login');
  // Reached from the link emailed by the "forgot password" flow, so it has to
  // render before any authentication check.
  const [passwordReset, setPasswordReset] = useState(() => getPasswordResetRoute(window.location.pathname));
  const [search, setSearch] = useState('');
  const [recentBoxIds, setRecentBoxIds] = useState<number[]>([]);
  const [qrLabelSelection, setQrLabelSelection] = useState<QrLabelItem[]>([]);
  const [isPhoneQrOpen, setIsPhoneQrOpen] = useState(false);
  const [isCreateBoxOpen, setIsCreateBoxOpen] = useState(false);
  // Values carried over when the history sends the user to correct a
  // measurement; consumed once by the box sheet, then cleared.
  const [measurementPrefill, setMeasurementPrefill] = useState<HistoryMeasurementPrefill | null>(null);
  const [activeOrganizationId, setActiveOrganizationId] = useState<number | null>(() => getStoredActiveOrganizationId());
  const [needsOrganizationChoice, setNeedsOrganizationChoice] = useState(false);
  const [isOrganizationMenuOpen, setIsOrganizationMenuOpen] = useState(false);
  const lastRecordedBoxIdRef = useRef<number | null>(null);
  const navigationGenerationRef = useRef(0);
  const inAppHistoryRef = useRef<ReturnType<typeof createInAppHistory> | null>(null);
  const navigationOrganizationRef = useRef(activeOrganizationId);
  const organizationRequestGenerationRef = useRef(0);
  const openBoxRequestGenerationRef = useRef(0);
  const [data, setData] = useState<AppData>({
    boxes: [],
    boxDetails: {},
    zones: [],
    dashboard: null,
    overview: null,
    exportOptions: null,
    profile: null,
  });
  const [isLoading, setIsLoading] = useState(true);
  const [isBoxLoading, setIsBoxLoading] = useState(false);
  // data.boxes holds the boxes known so far. It is complete only while
  // boxCollection.status is 'ready'; routes that need every box wait for that.
  const [boxCollection, setBoxCollection] = useState<BoxCollectionState>(IDLE_BOX_COLLECTION);
  const boxCollectionRef = useRef<BoxCollectionState>(IDLE_BOX_COLLECTION);
  const boxCollectionRequestRef = useRef<{ generation: number; promise: Promise<void> } | null>(null);
  const requestedRecentBoxIdsRef = useRef(new Set<number>());
  const [resolvedBoxCode, setResolvedBoxCode] = useState<{
    organizationId: number | null;
    code: string;
    boxId: number | null;
  } | null>(null);
  const [exportOptionsRequested, setExportOptionsRequested] = useState(false);
  const [error, setError] = useState<ApplicationError | null>(null);
  const [refreshRecovery, setRefreshRecovery] = useState<(() => Promise<void>) | null>(null);
  const [isRecoveringRefresh, setIsRecoveringRefresh] = useState(false);

  useEffect(() => {
    setRefreshRecovery(null);
    setIsRecoveringRefresh(false);
  }, [activeOrganizationId]);

  const activeTab = route.tab;
  const isBoxRoute = route.boxCode != null || route.boxId != null;
  const isZoneRoute = activeTab === 'zones' && route.zoneId != null;
  const language = getLanguage(data.profile);
  const t = useMemo(() => createTranslator(language), [language]);
  const { confirmAction, confirmActionModal } = useConfirmAction();
  const isDesktopApp = useIsDesktopApp();
  const isPhoneLayout = useIsPhoneLayout();
  const isTabletLayout = useIsTabletLayout();
  const recentBoxLimit = useRecentBoxLimit();
  const [isSidebarCollapsed, setIsSidebarCollapsed] = useState(() => getStoredSidebarCollapsed());
  const [isTabletScannerOpen, setIsTabletScannerOpen] = useState(false);

  function toggleSidebar() {
    setIsSidebarCollapsed((collapsed) => {
      const next = !collapsed;
      setStoredSidebarCollapsed(next);
      return next;
    });
  }

  const isEffectiveCollapsed = isDesktopApp && isSidebarCollapsed;
  // The tablet rail is always icon-only, so it hides labels just like the collapsed desktop sidebar.
  const isNavLabelHidden = isEffectiveCollapsed || isTabletLayout;
  const hasAdminRole = userHasAdminRole(data.profile, activeOrganizationId);
  const canCreateBox = userCanCreateBoxes(data.profile);

  useEffect(() => {
    document.documentElement.lang = language;
  }, [language]);

  useEffect(() => {
    if (activeTab !== 'pilotage' || isBoxRoute || isPhoneLayout) setIsCreateBoxOpen(false);
  }, [activeTab, isBoxRoute, isPhoneLayout]);
  const canUseAdmin = hasAdminRole;
  const navigationPolicyRef = useRef({ isDesktopApp, canUseAdmin });
  navigationPolicyRef.current = { isDesktopApp, canUseAdmin };

  useLayoutEffect(() => {
    getInAppHistory();
  }, []);
  const isExportOptionsLoading = (
    activeTab === 'exports' || exportOptionsRequested
  ) && data.exportOptions === null;
  const isOverviewLoading = activeTab === 'overview' && data.overview === null;
  const zonePageKey = route.zoneHistory ? 'history' : route.zoneBoxes ? 'boxes' : 'detail';
  const workspacePageKey = `${activeOrganizationId ?? 'none'}-${activeTab}-${route.boxCode ?? route.boxId ?? 'list'}-${route.zoneId ?? 'list'}-${zonePageKey}-${route.adminSection ?? 'default'}`;
  const brandOrganizationName = getBrandOrganizationName(data.profile, t);
  const selectableOrganizations = useMemo(() => getSelectableOrganizations(data.profile), [data.profile]);
  const activeOrganization = useMemo(
    () => getOrganizationById(data.profile, activeOrganizationId),
    [activeOrganizationId, data.profile],
  );
  const availableTabs = useMemo(() => {
    if (!isDesktopApp) return labTabs;
    return desktopTabs;
  }, [isDesktopApp]);

  const operationGeneration = organizationRequestGenerationRef.current;

  function getOperationRequests(generation = organizationRequestGenerationRef.current) {
    const assertCurrent = () => {
      if (generation !== organizationRequestGenerationRef.current) {
        throw new ApiResourceCancelledError();
      }
    };
    async function run<T>(request: () => Promise<T>): Promise<T> {
      assertCurrent();
      try {
        const result = await request();
        assertCurrent();
        return result;
      } catch (requestError) {
        assertCurrent();
        throw requestError;
      }
    }
    return {
      apiGet: <T,>(path: string) => run(() => apiGet<T>(path)),
      apiPost: <T,>(path: string, payload: unknown) => run(() => apiPost<T>(path, payload)),
      apiPatch: <T,>(path: string, payload: unknown) => run(() => apiPatch<T>(path, payload)),
      apiDelete: <T,>(path: string) => run(() => apiDelete<T>(path)),
      assertCurrent,
      setData: (update: (current: AppData) => AppData) => setData((current) => (
        generation === organizationRequestGenerationRef.current ? update(current) : current
      )),
    };
  }

  async function fetchAllPages<T extends { id: number }>(
    path: string,
    stopWhen?: (pageItems: T[]) => boolean,
  ): Promise<T[]> {
    const { apiGet } = getOperationRequests();
    const results: T[] = [];
    const seenIds = new Set<number>();
    const visited = new Set<string>();
    const collectionPath = new URL(path, window.location.origin).pathname;
    let next: string | null = path;
    while (next) {
      const url = new URL(next, window.location.origin);
      const requestPath = `${url.pathname}${url.search}`;
      const isTrustedOrigin = url.origin === window.location.origin
        || (__API_PROXY_ORIGIN__ !== null && url.origin === __API_PROXY_ORIGIN__);
      if (!isTrustedOrigin || !['http:', 'https:'].includes(url.protocol)
        || url.username || url.password || url.hash || !url.pathname.startsWith('/api/')
        || url.pathname !== collectionPath || visited.has(requestPath)) {
        throw new Error('Invalid pagination link.');
      }
      // Normalize DRF proxy-origin links to same-origin requests and cycle keys.
      visited.add(requestPath);
      const page: PaginatedResponse<T> = await apiGet<PaginatedResponse<T>>(requestPath);
      for (const item of page.results) {
        if (!Number.isSafeInteger(item.id)) throw new Error('Invalid pagination item ID.');
        if (seenIds.has(item.id)) continue;
        seenIds.add(item.id);
        results.push(item);
      }
      next = stopWhen?.(page.results) ? null : page.next;
    }
    return results;
  }

  async function fetchScopedData(profile: UserProfile, organizationId: number) {
    setActiveOrganizationContext(organizationId);
    const scopedProfile = setProfileActiveOrganization(profile, organizationId);
    // The complete Box list is not part of the bootstrap: see requestBoxCollection.
    const [zones, dashboard] = await Promise.all([
      fetchAllPages<ThermalZone>('/api/thermal-zones/?limit=80'),
      apiGet<Dashboard>('/api/dashboard/'),
    ]);

    return {
      boxes: [] as BoxItem[],
      boxDetails: {},
      zones,
      dashboard,
      overview: null,
      exportOptions: null,
      profile: scopedProfile,
    };
  }

  function writeBoxCollection(next: BoxCollectionState) {
    boxCollectionRef.current = next;
    setBoxCollection(next);
  }

  // Box data belongs to one organization session: forget it on every replacement.
  function resetBoxCollection() {
    boxCollectionRequestRef.current = null;
    requestedRecentBoxIdsRef.current = new Set();
    writeBoxCollection(IDLE_BOX_COLLECTION);
    setResolvedBoxCode(null);
  }

  /**
   * Load every box of the active organization, only when a route needs it.
   * Concurrent callers share one request; a loaded list is refreshed in the
   * background once it is old. A failure stays local to the routes that need
   * the list and never replaces the whole application with an error.
   */
  function requestBoxCollection(options: { retry?: boolean } = {}): Promise<void> {
    const generation = organizationRequestGenerationRef.current;
    const pending = boxCollectionRequestRef.current;
    if (pending && pending.generation === generation) return pending.promise;

    const current = boxCollectionRef.current;
    const isRetry = options.retry === true && current.status === 'error';
    if (!isRetry && !shouldLoadBoxCollection(current, Date.now())) return Promise.resolve();

    const baselineBoxes = data.boxes;
    const { setData } = getOperationRequests(generation);
    if (current.status !== 'ready') writeBoxCollection({ status: 'loading', loadedAt: null });

    const request: Promise<void> = fetchAllPages<BoxItem>(`/api/boxes/?limit=${BOX_LIST_LIMIT}`)
      .then((boxes) => {
        if (generation !== organizationRequestGenerationRef.current) return;
        setData((latest) => ({ ...latest, boxes: mergeLoadedBoxes(latest.boxes, boxes, baselineBoxes) }));
        writeBoxCollection({ status: 'ready', loadedAt: Date.now() });
      })
      .catch(() => {
        if (generation !== organizationRequestGenerationRef.current) return;
        // A failed background refresh keeps the list that was already loaded.
        if (boxCollectionRef.current.status === 'loading') writeBoxCollection({ status: 'error', loadedAt: null });
      })
      .finally(() => {
        if (boxCollectionRequestRef.current?.promise === request) boxCollectionRequestRef.current = null;
      });
    boxCollectionRequestRef.current = { generation, promise: request };
    return request;
  }

  // Resolve one box through the organization-scoped search, without the full list.
  async function findBoxBySearch(code: string, matches: (box: BoxItem) => boolean): Promise<BoxItem | null> {
    const query = code.trim();
    if (!query) return null;
    const results = await fetchAllPages<BoxItem>(
      `/api/boxes/?limit=${BOX_LIST_LIMIT}&q=${encodeURIComponent(query)}`,
      (page) => page.some(matches),
    );
    return results.find(matches) ?? null;
  }

  async function findBoxIdByCode(code: string): Promise<number | null> {
    const needle = code.trim().toLowerCase();
    const box = await findBoxBySearch(code, (item) => (
      item.global_code.toLowerCase() === needle || item.local_code.toLowerCase() === needle
    ));
    return box?.id ?? null;
  }

  function updateProfileMembershipResponsable(organizationId: number, isResponsable: boolean) {
    setData((current) => current.profile
      ? {
          ...current,
          profile: {
            ...current.profile,
            memberships: current.profile.memberships.map((membership) =>
              membership.organization.id === organizationId
                ? { ...membership, is_responsable: isResponsable }
                : membership,
            ),
          },
        }
      : current);
  }

  async function chooseOrganization(organizationId: number) {
    if (!data.profile) return;

    if (!needsOrganizationChoice && organizationId === activeOrganizationId) {
      setIsOrganizationMenuOpen(false);
      return;
    }

    const requestGeneration = ++organizationRequestGenerationRef.current;
    openBoxRequestGenerationRef.current += 1;
    resetBoxCollection();
    setIsBoxLoading(false);
    setIsOrganizationMenuOpen(false);
    setNeedsOrganizationChoice(false);
    setIsCreateBoxOpen(false);
    const organizationPath = isBoxRoute || isZoneRoute
      ? (activeTab === 'zones' ? '/zones' : '/')
      : getCurrentAppPath();
    resetNavigation(organizationPath, organizationId);
    setActiveOrganizationId(organizationId);
    setIsLoading(true);
    setError(null);
    setSearch('');
    setRecentBoxIds([]);
    setQrLabelSelection([]);
    setMeasurementPrefill(null);
    setExportOptionsRequested(false);
    setData({
      boxes: [],
      boxDetails: {},
      zones: [],
      dashboard: null,
      overview: null,
      exportOptions: null,
      profile: setProfileActiveOrganization(data.profile, organizationId),
    });

    if (isBoxRoute || isZoneRoute) {
      setRoute(getCurrentRoute());
    }

    try {
      const nextData = await fetchScopedData(data.profile, organizationId);
      if (requestGeneration !== organizationRequestGenerationRef.current) return;
      setData(nextData);
      setRecentBoxIds(getRecentBoxIds(nextData.dashboard));
    } catch (requestError) {
      if (requestGeneration !== organizationRequestGenerationRef.current) return;
      const applicationError = await getApplicationError(requestError);
      if (requestGeneration === organizationRequestGenerationRef.current) setError(applicationError);
    } finally {
      if (requestGeneration === organizationRequestGenerationRef.current) setIsLoading(false);
    }
  }

  useEffect(() => {
    function syncRoute() {
      const path = getCurrentAppPath();
      if (isPublicAuthPath(window.location.pathname)) {
        resetNavigation(path, null);
      } else {
        getInAppHistory().sync({ path, organization: navigationOrganizationRef.current });
        navigationGenerationRef.current += 1;
      }
      setIsTabletScannerOpen(false);
      setRoute(getCurrentRoute());
      setIsLoginRoute(window.location.pathname === '/login');
      setPasswordReset(getPasswordResetRoute(window.location.pathname));
    }

    window.addEventListener('popstate', syncRoute);
    return () => window.removeEventListener('popstate', syncRoute);
  }, []);

  useEffect(() => {
    if (!isTabletLayout) setIsTabletScannerOpen(false);
  }, [isTabletLayout]);

  useEffect(() => {
    setIsPhoneQrOpen(false);
    setIsTabletScannerOpen(false);
  }, [activeOrganizationId]);

  useEffect(() => {
    if (isPublicAuthPath(window.location.pathname)) {
      setError(null);
      setIsLoading(false);
      return;
    }

    let isActive = true;
    const requestGeneration = ++organizationRequestGenerationRef.current;
    const isCurrentRequest = () => isActive && requestGeneration === organizationRequestGenerationRef.current;

    async function loadData() {
      let profileLoaded = false;

      try {
        setIsLoading(true);
        setError(null);
        resetBoxCollection();

        const profile = await apiGet<UserProfile>('/api/profile/', { skipOrganizationContext: true });
        profileLoaded = true;

        if (!isCurrentRequest()) return;

        setStoredInterfaceLanguage(profile.interface_language);

        const organizations = getSelectableOrganizations(profile);
        const preferredOrganizationId = activeOrganizationId ?? getStoredActiveOrganizationId();
        const resolvedOrganizationId = resolveActiveOrganizationId(profile, preferredOrganizationId);

        if (organizations.length > 1 && resolvedOrganizationId == null) {
          setActiveOrganizationContext(null);
          updateNavigationOrganization(null);
          setNeedsOrganizationChoice(true);
          setData({
            boxes: [],
            boxDetails: {},
            zones: [],
            dashboard: null,
            overview: null,
            exportOptions: null,
            profile,
          });
          setRecentBoxIds([]);
          return;
        }

        if (resolvedOrganizationId == null) {
          updateNavigationOrganization(null);
          setData({
            boxes: [],
            boxDetails: {},
            zones: [],
            dashboard: null,
            overview: null,
            exportOptions: null,
            profile,
          });
          setRecentBoxIds([]);
          setNeedsOrganizationChoice(false);
          return;
        }

        updateNavigationOrganization(resolvedOrganizationId);
        setNeedsOrganizationChoice(false);

        const nextData = await fetchScopedData(profile, resolvedOrganizationId);
        if (!isCurrentRequest()) return;

        setData(nextData);
        setRecentBoxIds(getRecentBoxIds(nextData.dashboard));
      } catch (requestError) {
        if (!isCurrentRequest()) return;

        const status = requestError instanceof ApiError ? requestError.status : null;
        if (shouldRedirectToLogin(profileLoaded, status)) {
          const requestedPath = `${window.location.pathname}${window.location.search}`;
          const loginPath = `/login?next=${encodeURIComponent(requestedPath)}`;
          resetNavigation(loginPath, null);
          setIsLoginRoute(true);
          setError(null);
          return;
        }

        const applicationError = await getApplicationError(requestError);
        if (!isCurrentRequest()) return;
        setError(applicationError);
      } finally {
        if (isCurrentRequest()) {
          setIsLoading(false);
        }
      }
    }

    loadData();

    return () => {
      isActive = false;
    };
  }, [isLoginRoute, passwordReset]);

  const isScanReady = !isLoginRoute && !passwordReset && !isLoading && !needsOrganizationChoice
    && activeOrganizationId != null && data.profile != null && error == null;

  useEffect(() => {
    if (!isScanReady || route.scanBoxId == null) return;

    let isActive = true;
    const organizationGeneration = organizationRequestGenerationRef.current;
    const requestGeneration = ++openBoxRequestGenerationRef.current;
    const navigationGeneration = navigationGenerationRef.current;
    const isCurrentRequest = () => isActive
      && organizationGeneration === organizationRequestGenerationRef.current
      && isRouteRequestCurrent(
        requestGeneration,
        openBoxRequestGenerationRef.current,
        navigationGeneration,
        navigationGenerationRef.current,
      );

    async function handoffScan() {
      setIsBoxLoading(true);
      try {
        const result = await apiPost<{ global_code: string }>(`/api/boxes/${route.scanBoxId}/scan/`, {});
        if (!isCurrentRequest()) return;
        replaceRoute(
          { tab: 'pilotage', boxCode: result.global_code, boxId: route.scanBoxId },
          `/boxes/${encodeURIComponent(result.global_code)}`,
        );
      } catch (requestError) {
        if (!isCurrentRequest()) return;
        const applicationError = await getApplicationError(requestError);
        if (!isCurrentRequest()) return;
        setError(applicationError);
      } finally {
        if (organizationGeneration === organizationRequestGenerationRef.current
          && requestGeneration === openBoxRequestGenerationRef.current) setIsBoxLoading(false);
      }
    }

    void handoffScan();
    return () => {
      isActive = false;
    };
  }, [activeOrganizationId, isScanReady, route.scanBoxId]);

  const loadOverviewHistory = useCallback(async (boxId: number) => {
    const { apiGet, assertCurrent } = getOperationRequests(operationGeneration);
    const detail = await apiGet<BoxDetail>(`/api/boxes/${boxId}/`);
    assertCurrent();
    return detail;
  }, [activeOrganizationId, operationGeneration]);

  useEffect(() => {
    if (isLoginRoute || needsOrganizationChoice || activeOrganizationId == null || activeTab !== 'overview' || data.overview !== null) return;

    let isActive = true;
    const requestGeneration = organizationRequestGenerationRef.current;
    const isCurrentRequest = () => isActive && requestGeneration === organizationRequestGenerationRef.current;

    async function loadOverview() {
      try {
        const overview = await apiGet<OverviewResponse>('/api/overview/active-boxes/?months=3');
        if (!isCurrentRequest()) return;
        setData((current) => ({ ...current, overview: overview.results.map((box) => ({
                  ...box, history_start_date: overview.history_start_date, history_end_date: overview.history_end_date,
                })) }));
      } catch (requestError) {
        if (!isCurrentRequest()) return;
        const applicationError = await getApplicationError(requestError);
        if (!isCurrentRequest()) return;
        setError(applicationError);
      }
    }

    loadOverview();

    return () => {
      isActive = false;
    };
  }, [activeOrganizationId, activeTab, data.overview, isLoginRoute, needsOrganizationChoice]);

  const isBoxCollectionReady = boxCollection.status === 'ready';
  const hasSearch = search.trim() !== '';
  const needsBoxCollection = needsFullBoxCollection({
    activeTab,
    isBoxRoute,
    hasSearch,
    zoneId: route.zoneId,
    zoneHistory: route.zoneHistory,
    adminSection: route.adminSection,
    isAdminAvailable: canUseAdmin && isDesktopApp,
  });

  useEffect(() => {
    if (!needsBoxCollection) {
      // Leaving the routes that need the list lets the next visit try again.
      if (boxCollectionRef.current.status === 'error') writeBoxCollection(IDLE_BOX_COLLECTION);
      return;
    }
    if (isLoginRoute || isLoading || needsOrganizationChoice || activeOrganizationId == null || !data.profile) return;
    void requestBoxCollection();
  }, [
    activeOrganizationId,
    boxCollection.loadedAt,
    boxCollection.status,
    data.profile,
    isLoading,
    isLoginRoute,
    needsBoxCollection,
    needsOrganizationChoice,
  ]);

  // A partial list must never answer a search as if it were complete.
  const filteredBoxes = useMemo(
    () => (isBoxCollectionReady ? filterBoxes(data.boxes, search) : []),
    [data.boxes, isBoxCollectionReady, search],
  );

  useEffect(() => {
    if (activeTab === 'pilotage' && !isBoxRoute) setSearch('');
  }, [activeTab, isBoxRoute]);

  const isBoxCodeResolved = resolvedBoxCode != null
    && resolvedBoxCode.organizationId === activeOrganizationId
    && resolvedBoxCode.code === route.boxCode;

  // A box reached by code (direct URL, QR) is found among the known boxes first,
  // then through the organization-scoped search; the full list is not needed.
  const selectedBoxId = useMemo(() => {
    if (route.boxId != null) return route.boxId;
    if (route.boxCode) {
      const knownBox = data.boxes.find((box) => box.global_code === route.boxCode);
      if (knownBox) return knownBox.id;
      return isBoxCodeResolved ? resolvedBoxCode?.boxId ?? null : null;
    }
    return null;
  }, [data.boxes, isBoxCodeResolved, resolvedBoxCode, route.boxCode, route.boxId]);

  const selectedBox = useMemo(() => {
    if (selectedBoxId == null) return null;
    return data.boxes.find((box) => box.id === selectedBoxId) ?? null;
  }, [data.boxes, selectedBoxId]);
  const isBoxCodePending = isBoxRoute && route.boxCode != null && route.boxId == null
    && selectedBoxId == null && !isBoxCodeResolved;

  useEffect(() => {
    if (!isBoxCodePending || route.boxCode == null) return;
    if (isLoginRoute || isLoading || needsOrganizationChoice || activeOrganizationId == null) return;

    const code = route.boxCode;
    const organizationId = activeOrganizationId;
    let isActive = true;
    const requestGeneration = organizationRequestGenerationRef.current;
    const isCurrentRequest = () => isActive && requestGeneration === organizationRequestGenerationRef.current;

    async function resolveBoxCode() {
      try {
        const box = await findBoxBySearch(code, (item) => item.global_code === code);
        if (!isCurrentRequest()) return;
        setResolvedBoxCode({ organizationId, code, boxId: box?.id ?? null });
      } catch (requestError) {
        if (!isCurrentRequest()) return;
        const applicationError = await getApplicationError(requestError);
        if (!isCurrentRequest()) return;
        setError(applicationError);
      }
    }

    void resolveBoxCode();
    return () => {
      isActive = false;
    };
  }, [activeOrganizationId, isBoxCodePending, isLoading, isLoginRoute, needsOrganizationChoice, route.boxCode]);
  const selectedZone = useMemo(() => {
    if (route.zoneId == null) return null;
    return data.zones.find((zone) => zone.id === route.zoneId) ?? null;
  }, [data.zones, route.zoneId]);

  const selectedBoxDetail = selectedBoxId != null ? data.boxDetails[selectedBoxId] ?? null : null;
  // The detail request starts after the first render of a box reached by id or code.
  const isBoxDetailPending = selectedBoxId != null && !selectedBoxDetail && !selectedBox;

  useEffect(() => {
    let isActive = true;
    const requestGeneration = organizationRequestGenerationRef.current;
    const isCurrentRequest = () => isActive && requestGeneration === organizationRequestGenerationRef.current;

    async function loadBoxDetail(boxId: number) {
      try {
        setIsBoxLoading(true);
        const detail = await apiGet<BoxDetail>(`/api/boxes/${boxId}/`);
        if (!isCurrentRequest()) return;
        setData((current) => mergeBoxDetail(current, detail));
      } catch (requestError) {
        if (!isCurrentRequest()) return;
        const applicationError = await getApplicationError(requestError);
        if (!isCurrentRequest()) return;
        setError(applicationError);
      } finally {
        if (isCurrentRequest()) setIsBoxLoading(false);
      }
    }

    if (selectedBoxId != null && !selectedBoxDetail) {
      loadBoxDetail(selectedBoxId);
    }

    return () => {
      isActive = false;
    };
  }, [activeOrganizationId, selectedBoxId, Boolean(selectedBoxDetail)]);

  useEffect(() => {
    if (selectedBoxId == null) {
      lastRecordedBoxIdRef.current = null;
      return;
    }

    setRecentBoxIds((currentIds) => [
      selectedBoxId,
      ...currentIds.filter((currentId) => currentId !== selectedBoxId),
    ].slice(0, 6));

    if (lastRecordedBoxIdRef.current === selectedBoxId) return;
    lastRecordedBoxIdRef.current = selectedBoxId;

    // Access tracking must never prevent someone from opening a box.
    const generation = organizationRequestGenerationRef.current;
    void apiPost<void>(`/api/boxes/${selectedBoxId}/access/`, {}).catch(() => {
      if (generation === organizationRequestGenerationRef.current && lastRecordedBoxIdRef.current === selectedBoxId) {
        lastRecordedBoxIdRef.current = null;
      }
    });
  }, [selectedBoxId]);

  const recentBoxes = useMemo(() => {
    return recentBoxIds
      .map((boxId) => data.boxes.find((box) => box.id === boxId))
      .filter((box): box is BoxItem => Boolean(box))
      .slice(0, recentBoxLimit);
  }, [data.boxes, recentBoxIds, recentBoxLimit]);

  // Recent boxes come from the dashboard's recent accesses. Only the few that
  // are displayed are fetched, one by one, instead of downloading every box.
  useEffect(() => {
    if (activeTab !== 'pilotage' || isBoxRoute || isLoading || needsOrganizationChoice) return;
    if (activeOrganizationId == null || !data.profile) return;

    const requestedIds = requestedRecentBoxIdsRef.current;
    const missingIds = recentBoxIds
      .slice(0, recentBoxLimit)
      .filter((boxId) => !requestedIds.has(boxId) && !data.boxes.some((box) => box.id === boxId));
    if (!missingIds.length) return;

    missingIds.forEach((boxId) => requestedIds.add(boxId));
    const { apiGet, setData } = getOperationRequests();
    void Promise.allSettled(missingIds.map((boxId) => apiGet<BoxDetail>(`/api/boxes/${boxId}/`)))
      .then((results) => {
        const loadedBoxes: BoxDetail[] = [];
        results.forEach((result, index) => {
          if (result.status === 'fulfilled') loadedBoxes.push(result.value);
          else requestedIds.delete(missingIds[index]);
        });
        // A failed recent box is only left out; it is retried on a later visit.
        if (loadedBoxes.length) {
          setData((current) => ({ ...current, boxes: upsertBoxes(current.boxes, loadedBoxes) }));
        }
      });
  }, [
    activeOrganizationId,
    activeTab,
    data.boxes,
    data.profile,
    isBoxRoute,
    isLoading,
    needsOrganizationChoice,
    recentBoxIds,
    recentBoxLimit,
  ]);

  /**
   * Open a box sheet from the history with its measurement form pre-filled.
   *
   * Saving keeps the same date, and the API stores one measurement per box and
   * date, so the correction overwrites that measurement instead of adding one.
   */
  function editMeasurementFromHistory(measurement: HistoryMeasurementPrefill) {
    setMeasurementPrefill(measurement);
    openBox(measurement.box_id, measurement.box_code);
  }

  function openBox(boxId: number, fallbackCode?: string) {
    const organizationGeneration = organizationRequestGenerationRef.current;
    const requestGeneration = ++openBoxRequestGenerationRef.current;
    const box = data.boxes.find((item) => item.id === boxId);
    if (box) {
      setIsBoxLoading(false);
      navigateTo({ tab: 'pilotage', boxCode: box.global_code, boxId: null }, `/boxes/${encodeURIComponent(box.global_code)}`);
      return;
    }

    if (fallbackCode) {
      navigateTo({ tab: 'pilotage', boxCode: fallbackCode, boxId }, `/boxes/${encodeURIComponent(fallbackCode)}`);
    }

    const navigationGeneration = navigationGenerationRef.current;
    setIsBoxLoading(true);
    void apiGet<BoxDetail>(`/api/boxes/${boxId}/`)
      .then((detail) => {
        if (organizationGeneration !== organizationRequestGenerationRef.current) return;
        if (!isRouteRequestCurrent(
          requestGeneration,
          openBoxRequestGenerationRef.current,
          navigationGeneration,
          navigationGenerationRef.current,
        )) return;
        setData((current) => mergeBoxDetail(current, detail));
        navigateTo({ tab: 'pilotage', boxCode: detail.global_code, boxId }, `/boxes/${encodeURIComponent(detail.global_code)}`);
      })
      .catch(async (requestError) => {
        if (organizationGeneration !== organizationRequestGenerationRef.current) return;
        if (!isRouteRequestCurrent(
          requestGeneration,
          openBoxRequestGenerationRef.current,
          navigationGeneration,
          navigationGenerationRef.current,
        )) return;
        const applicationError = await getApplicationError(requestError);
        if (organizationGeneration === organizationRequestGenerationRef.current && isRouteRequestCurrent(
          requestGeneration,
          openBoxRequestGenerationRef.current,
          navigationGeneration,
          navigationGenerationRef.current,
        )) setError(applicationError);
      })
      .finally(() => {
        if (organizationGeneration === organizationRequestGenerationRef.current
          && requestGeneration === openBoxRequestGenerationRef.current) setIsBoxLoading(false);
      });
  }

  function openZone(zoneId: number) {
    navigateTo({ tab: 'zones', boxCode: null, boxId: null, zoneId }, `/zones/${zoneId}`);
  }

  function openZoneBoxes(zoneId: number) {
    navigateTo(
      { tab: 'zones', boxCode: null, boxId: null, zoneId, zoneBoxes: true },
      `/zones/${zoneId}/boxes`,
    );
  }

  function openZoneHistory(zoneId: number, direction: 'arrival' | 'departure' = 'arrival') {
    const currentRoute = getCurrentRoute();
    const navigate = currentRoute.zoneHistory && currentRoute.zoneId === zoneId ? replaceRoute : navigateTo;
    navigate(
      { tab: 'zones', boxCode: null, boxId: null, zoneId, zoneHistory: true, zoneHistoryDirection: direction },
      `/zones/${zoneId}/history?direction=${direction}`,
    );
  }

  function openTab(tab: TabId) {
    if (tab === 'pilotage') setSearch('');
    if (tab === 'admin') {
      openAdminSection('accounts');
      return;
    }
    const paths: Record<TabId, string> = {
      pilotage: '/',
      overview: '/overview',
      zones: '/zones',
      exports: '/exports',
      labels: '/labels',
      admin: ADMIN_SECTION_PATHS.accounts,
      profile: '/profile',
    };
    navigateTo({ tab, boxCode: null, boxId: null }, paths[tab]);
  }

  function openScannedBox(boxId: number) {
    setIsTabletScannerOpen(false);
    openBox(boxId);
  }

  function openAdminSection(section: AdminSectionKey) {
    navigateTo(
      { tab: 'admin', boxCode: null, boxId: null, adminSection: section },
      ADMIN_SECTION_PATHS[section],
    );
  }

  function addQrLabelToSelection(label: QrLabelItem) {
    setQrLabelSelection((current) => (
      current.some((item) => item.id === label.id) ? current : [...current, label]
    ));
  }

  function clearQrLabelSelection() {
    setQrLabelSelection([]);
  }

  function removeQrLabelFromSelection(labelId: number) {
    setQrLabelSelection((current) => current.filter((label) => label.id !== labelId));
  }

  function openQrLabelSelection() {
    openTab('labels');
  }

  useEffect(() => {
    if (!isPhoneLayout) setIsPhoneQrOpen(false);
  }, [isPhoneLayout]);

  useLayoutEffect(() => {
    if (activeTab === 'admin' && !isDesktopApp) {
      replaceRoute({ tab: 'pilotage', boxCode: null, boxId: null }, '/', true);
      return;
    }
    if (isLoading || !data.profile) return;
    if (
      availableTabs.includes(activeTab)
      || (activeTab === 'admin' && canUseAdmin && isDesktopApp)
    ) return;

    replaceRoute({ tab: 'pilotage', boxCode: null, boxId: null }, '/', true);
  }, [activeTab, availableTabs, canUseAdmin, data.profile, isDesktopApp, isLoading]);

  useEffect(() => {
    const shouldLoadExportOptions =
      activeTab === 'exports' || exportOptionsRequested;
    if (
      isLoginRoute ||
      needsOrganizationChoice ||
      activeOrganizationId == null ||
      data.exportOptions ||
      !shouldLoadExportOptions
    ) return;

    let isActive = true;
    const requestGeneration = organizationRequestGenerationRef.current;
    const isCurrentRequest = () => isActive && requestGeneration === organizationRequestGenerationRef.current;

    async function loadExportOptions() {
      try {
        const exportOptions = await apiGet<ExportOptions>('/api/exports/options/');
        if (!isCurrentRequest()) return;
        setData((current) => ({ ...current, exportOptions }));
        setExportOptionsRequested(false);
      } catch (requestError) {
        if (!isCurrentRequest()) return;
        const applicationError = await getApplicationError(requestError);
        if (!isCurrentRequest()) return;
        setError(applicationError);
        setExportOptionsRequested(false);
      }
    }

    void loadExportOptions();

    return () => {
      isActive = false;
    };
  }, [activeOrganizationId, activeTab, data.exportOptions, exportOptionsRequested, isLoginRoute, needsOrganizationChoice]);

  function getInAppHistory() {
    if (!inAppHistoryRef.current) {
      inAppHistoryRef.current = createInAppHistory(
        window.history,
        { path: getCurrentAppPath(), organization: navigationOrganizationRef.current },
        (path) => {
          const { isDesktopApp, canUseAdmin } = navigationPolicyRef.current;
          return isRecognizedAppPath(path, isDesktopApp, canUseAdmin);
        },
      );
    }
    return inAppHistoryRef.current;
  }

  function resetNavigation(path: string, organization = navigationOrganizationRef.current) {
    getInAppHistory().reset({ path, organization });
    navigationOrganizationRef.current = organization;
    navigationGenerationRef.current += 1;
  }

  function updateNavigationOrganization(organization: number | null) {
    if (navigationOrganizationRef.current !== organization) {
      resetNavigation(getCurrentAppPath(), organization);
    }
    setActiveOrganizationId(organization);
  }

  function goBack(fallbackPath: string) {
    const result = getInAppHistory().back(
      { path: getCurrentAppPath(), organization: navigationOrganizationRef.current },
      fallbackPath,
    );
    if (result === 'pending') return;
    // Invalidate async box enrichment immediately, not only when popstate arrives.
    navigationGenerationRef.current += 1;
    if (result === 'fallback') setRoute(getCurrentRoute());
  }

  function closeBoxPage() {
    setSearch('');
    goBack('/');
  }

  function closeZonePage() {
    goBack('/zones');
  }

  function closeZoneSubview(zoneId: number) {
    goBack(`/zones/${zoneId}`);
  }

  function navigateTo(nextRoute: RouteState, path: string) {
    const { isDesktopApp, canUseAdmin } = navigationPolicyRef.current;
    if (!isRecognizedAppPath(path, isDesktopApp, canUseAdmin)) {
      replaceRoute({ tab: 'pilotage', boxCode: null, boxId: null }, '/', true);
      return;
    }
    const result = getInAppHistory().push({ path, organization: navigationOrganizationRef.current });
    if (result === 'pending') return;
    navigationGenerationRef.current += 1;
    // A same-path noop can still enrich boxId after the fallback-code request.
    setRoute(nextRoute);
  }

  function replaceRoute(nextRoute: RouteState, path: string, invalidate = false) {
    if (invalidate) {
      resetNavigation(path);
    } else {
      const result = getInAppHistory().replace({ path, organization: navigationOrganizationRef.current });
      if (result === 'pending') return;
      navigationGenerationRef.current += 1;
    }
    setRoute(nextRoute);
  }

  async function updateLanguage(language: string) {
    const { apiPatch, setData, assertCurrent } = getOperationRequests(operationGeneration);
    const previousLanguage = getLanguage(data.profile);
    const nextLanguage = setStoredInterfaceLanguage(language);

    setData((current) => ({
      ...current,
      profile: current.profile
        ? { ...current.profile, interface_language: nextLanguage }
        : null,
    }));

    try {
      const profile = await apiPatch<UserProfile>('/api/profile/', {
        interface_language: nextLanguage,
      });
      assertCurrent();

      setStoredInterfaceLanguage(profile.interface_language);
      setData((current) => ({
        ...current,
        profile,
      }));
    } catch (requestError) {
      if (requestError instanceof ApiResourceCancelledError) throw requestError;
      setStoredInterfaceLanguage(previousLanguage);
      setData((current) => ({
        ...current,
        profile: current.profile
          ? { ...current.profile, interface_language: previousLanguage }
          : null,
      }));
      throw requestError;
    }
  }

  async function logoutCurrentUser() {
    await apiPost<void>('/api/auth/logout/', {});
    organizationRequestGenerationRef.current += 1;
    openBoxRequestGenerationRef.current += 1;
    setRefreshRecovery(null);

    setData({
      boxes: [],
      boxDetails: {},
      zones: [],
      dashboard: null,
      overview: null,
      exportOptions: null,
      profile: null,
    });
    setRecentBoxIds([]);
    setActiveOrganizationId(null);
    setNeedsOrganizationChoice(false);
    setIsOrganizationMenuOpen(false);
    resetNavigation('/login', null);
    setRoute(getCurrentRoute());
    setError(null);
    setIsLoginRoute(true);
  }

  async function refreshAfterMutation(refresh: () => Promise<unknown>, assertCurrent: () => void) {
    const generation = organizationRequestGenerationRef.current;
    assertCurrent();
    try {
      await refresh();
      assertCurrent();
    } catch (requestError) {
      assertCurrent();
      setRefreshRecovery((current) => {
        if (generation !== organizationRequestGenerationRef.current) return current;
        const recovery = async () => {
          assertCurrent();
          await refresh();
          assertCurrent();
          setRefreshRecovery((pending) => pending === recovery ? null : pending);
        };
        return recovery;
      });
    }
  }

  async function recoverMutationRefresh() {
    if (!refreshRecovery || isRecoveringRefresh) return;
    const generation = organizationRequestGenerationRef.current;
    setIsRecoveringRefresh(true);
    try {
      await refreshRecovery();
    } catch {
      // Keep the read-only recovery available; never repeat the mutation.
    } finally {
      if (generation === organizationRequestGenerationRef.current) setIsRecoveringRefresh(false);
    }
  }

  function applyMeasurementResult(boxId: number, measurement: BiologicalMeasurement) {
    const { setData } = getOperationRequests(operationGeneration);
    setData((current) => {
      const detail = current.boxDetails[boxId];
      if (!detail) return current;
      const measurements = [measurement, ...detail.biological_measurements.filter((item) => item.id !== measurement.id)]
        .sort((left, right) => right.measured_on.localeCompare(left.measured_on));
      return {
        ...mergeBoxDetail(current, {
          ...detail,
          biological_measurements: measurements,
          latest_measurement: measurements[0] ?? null,
          current_polyp_state: { polyp_count: null, revision: '', source: null },
          latest_salinity_psu: measurements.find((item) => item.salinity_psu != null)?.salinity_psu ?? null,
        }),
        overview: null,
      };
    });
  }

  async function refreshBoxAfterMeasurement(boxId: number) {
    const { apiGet, setData, assertCurrent } = getOperationRequests(operationGeneration);
    const detail = await apiGet<BoxDetail>(`/api/boxes/${boxId}/`);
    assertCurrent();
    setData((current) => ({
      ...mergeBoxDetail(current, detail),
      overview: null,
    }));
    assertCurrent();
    return detail;
  }

  async function createMeasurement(boxId: number, payload: MeasurementPayload) {
    const { apiPost, assertCurrent } = getOperationRequests(operationGeneration);
    try {
      const created = await apiPost<BiologicalMeasurement>(`/api/boxes/${boxId}/measurements/`, payload);
      assertCurrent();
      applyMeasurementResult(boxId, created);
      await refreshAfterMutation(() => refreshBoxAfterMeasurement(boxId), assertCurrent);
      assertCurrent();
      return created;
    } catch (requestError) {
      if (isMeasurementWeekConflict(requestError) || isMeasurementEditWindowExpired(requestError)) {
        try {
          await refreshBoxAfterMeasurement(boxId);
          assertCurrent();
        } catch {
          assertCurrent();
        }
      }
      throw requestError;
    }
  }

  async function createBox(payload: BoxCreatePayload) {
    const { apiPost, setData, assertCurrent } = getOperationRequests(operationGeneration);
    const detail = await apiPost<BoxDetail>('/api/boxes/', payload);
    assertCurrent();

    setData((current) => ({
      ...mergeBoxDetail(current, detail),
      boxes: upsertBoxes(current.boxes, [detail]),
      overview: null,
      exportOptions: null,
    }));
    assertCurrent();
    return detail;
  }

  async function updateMeasurement(boxId: number, measurementId: number, payload: MeasurementPayload) {
    const { apiPatch, assertCurrent } = getOperationRequests(operationGeneration);
    try {
      const updated = await apiPatch<BiologicalMeasurement>(
        `/api/boxes/${boxId}/measurements/${measurementId}/`,
        payload,
      );
      assertCurrent();
      applyMeasurementResult(boxId, updated);
      await refreshAfterMutation(() => refreshBoxAfterMeasurement(boxId), assertCurrent);
      assertCurrent();
      return updated;
    } catch (requestError) {
      if (isMeasurementWeekConflict(requestError) || isMeasurementEditWindowExpired(requestError)) {
        try {
          await refreshBoxAfterMeasurement(boxId);
          assertCurrent();
        } catch {
          assertCurrent();
        }
      }
      throw requestError;
    }
  }

  async function createSubculture(boxId: number, payload: SubculturePayload) {
    const { apiGet, apiPost, setData, assertCurrent } = getOperationRequests(operationGeneration);
    let result: SubcultureResult;
    try {
      result = await apiPost<SubcultureResult>(`/api/boxes/${boxId}/subcultures/`, payload);
    } catch (requestError) {
      assertCurrent();
      if (requestError instanceof ApiError && requestError.status === 409
          && requestError.data && typeof requestError.data === 'object'
          && 'code' in requestError.data && requestError.data.code === 'subculture_current_state_changed') {
        if ('current_polyp_state' in requestError.data) {
          const state = requestError.data.current_polyp_state as CurrentPolypState;
          setData((current) => {
            const detail = current.boxDetails[boxId];
            return detail ? mergeBoxDetail(current, { ...detail, current_polyp_state: state }) : current;
          });
        }
        try {
          await refreshBoxAfterMeasurement(boxId);
        } catch {
          assertCurrent();
        }
      }
      throw requestError;
    }
    assertCurrent();
    setData((current) => {
      const detail = current.boxDetails[boxId];
      const updated = detail ? mergeBoxDetail(current, {
        ...detail,
        current_polyp_state: {
          ...(typeof result.allocated_polyp_count === 'number' && typeof result.parent_polyp_count_after === 'number'
              && result.allocations.length > 0 && result.allocations.every((allocation) => allocation.allocated_polyps !== null)
            ? { polyp_count: result.parent_polyp_count_after, source: null }
            : detail.current_polyp_state),
          // Every committed event invalidates the intent, even without a quantitative transition.
          revision: '',
        },
      }) : current;
      return {
        ...updated,
        boxes: upsertBoxes(updated.boxes, result.children),
        overview: null,
        exportOptions: null,
      };
    });
    await refreshAfterMutation(async () => {
      const detail = await apiGet<BoxDetail>(`/api/boxes/${boxId}/`);
      assertCurrent();
      setData((current) => mergeBoxDetail(current, detail));
    }, assertCurrent);
    assertCurrent();
    return result;
  }

  async function moveBox(boxId: number, payload: BoxMovePayload) {
    const { apiGet, apiPost, setData, assertCurrent } = getOperationRequests(operationGeneration);
    try {
      const detail = await apiPost<BoxDetail>(`/api/boxes/${boxId}/move/`, payload);
      assertCurrent();
      setData((current) => ({
        ...mergeBoxDetail(current, detail),
        overview: null,
        exportOptions: null,
      }));
      await refreshAfterMutation(async () => {
        assertCurrent();
        const zones = await fetchAllPages<ThermalZone>('/api/thermal-zones/?limit=80');
        assertCurrent();
        setData((current) => ({ ...current, zones }));
      }, assertCurrent);
      assertCurrent();
      return detail;
    } catch (requestError) {
      if (isBoxLocationChangedError(requestError)) {
        try {
          const [detail, zones] = await Promise.all([
            apiGet<BoxDetail>(`/api/boxes/${boxId}/`),
            apiGet<PaginatedResponse<ThermalZone>>('/api/thermal-zones/?limit=80'),
          ]);
          assertCurrent();
          setData((current) => ({
            ...mergeBoxDetail(current, detail),
            zones: zones.results,
            overview: null,
            exportOptions: null,
          }));
        } catch {
          assertCurrent();
          // Keep the original conflict visible if the targeted refresh also fails.
        }
      }
      throw requestError;
    }
  }

  async function deactivateBox(boxId: number, payload: BoxDeactivatePayload) {
    const { apiPost, setData, assertCurrent } = getOperationRequests(operationGeneration);
    const detail = await apiPost<BoxDetail>(`/api/boxes/${boxId}/deactivate/`, payload);
    assertCurrent();

    setData((current) => ({
      ...mergeBoxDetail(current, detail),
      boxes: upsertBoxes(current.boxes, [detail]),
      overview: null,
      exportOptions: null,
    }));
    assertCurrent();
  }

  async function reactivateBox(boxId: number, payload: BoxActivatePayload) {
    const { apiPost, setData, assertCurrent } = getOperationRequests(operationGeneration);
    const detail = await apiPost<BoxDetail>(`/api/boxes/${boxId}/activate/`, payload);
    assertCurrent();

    setData((current) => ({
      ...mergeBoxDetail(current, detail),
      boxes: upsertBoxes(current.boxes, [detail]),
      overview: null,
      exportOptions: null,
    }));
    assertCurrent();
  }

  async function qualifyBox(boxId: number, payload: BoxQualifyPayload) {
    const { apiPost, setData, assertCurrent } = getOperationRequests(operationGeneration);
    const detail = await apiPost<BoxDetail>(`/api/boxes/${boxId}/qualify/`, payload);
    assertCurrent();

    setData((current) => ({
      ...mergeBoxDetail(current, detail),
      boxes: upsertBoxes(current.boxes, [detail]),
      overview: null,
      exportOptions: null,
    }));
    assertCurrent();
  }

  async function assignBoxInitialLocation(boxId: number, payload: BoxInitialLocationPayload) {
    const { apiGet, apiPost, setData, assertCurrent } = getOperationRequests(operationGeneration);
    const detail = await apiPost<BoxDetail>(
      `/api/admin/box-inventory/${boxId}/assign-location/`,
      payload,
    );
    assertCurrent();
    setData((current) => ({
      ...mergeBoxDetail(current, detail),
      boxes: upsertBoxes(current.boxes, [detail]),
      overview: null,
      exportOptions: null,
    }));
    await refreshAfterMutation(async () => {
      const zones = await apiGet<PaginatedResponse<ThermalZone>>('/api/thermal-zones/?limit=80');
      assertCurrent();
      setData((current) => ({ ...current, zones: zones.results }));
    }, assertCurrent);
    assertCurrent();
  }

  async function qualifyBoxesBatch(payload: BoxInventoryBatchQualifyPayload) {
    const { apiPost, setData, assertCurrent } = getOperationRequests(operationGeneration);
    const result = await apiPost<BoxInventoryBatchResult>(
      '/api/admin/box-inventory/batch-qualify/',
      payload,
    );
    assertCurrent();
    const successfulStatuses = new Map(
      result.successes.map((item) => [item.box_id, item.status]),
    );

    setData((current) => {
      const boxDetails = { ...current.boxDetails };
      result.successes.forEach((item) => {
        const detail = boxDetails[item.box_id];
        if (!detail) return;
        boxDetails[item.box_id] = {
          ...detail,
          status: item.status,
          thermal_zone: item.status === 'inactive' ? null : detail.thermal_zone,
        };
      });

      return {
        ...current,
        boxes: current.boxes.map((box) => {
          const nextStatus = successfulStatuses.get(box.id);
          if (!nextStatus) return box;
          return {
            ...box,
            status: nextStatus,
            thermal_zone: nextStatus === 'inactive' ? null : box.thermal_zone,
          };
        }),
        boxDetails,
        overview: null,
        exportOptions: null,
      };
    });
    assertCurrent();
    return result;
  }

  async function loadLineageGraph(boxId: number) {
    const { apiGet, assertCurrent } = getOperationRequests(operationGeneration);
    const graph = await apiGet<LineageGraph>(`/api/boxes/${boxId}/lineage/`);
    assertCurrent();
    return graph;
  }

  async function createThermalZone(payload: ThermalZonePayload) {
    const { apiGet, apiPost, setData, assertCurrent } = getOperationRequests(operationGeneration);
    await apiPost<ThermalZone>('/api/thermal-zones/', payload);
    assertCurrent();
    const zones = await apiGet<PaginatedResponse<ThermalZone>>('/api/thermal-zones/?limit=80');
    assertCurrent();
    setData((current) => ({ ...current, zones: zones.results }));
    assertCurrent();
  }

  async function updateThermalZone(zoneId: number, payload: ThermalZonePayload) {
    const { apiGet, apiPatch, setData, assertCurrent } = getOperationRequests(operationGeneration);
    await apiPatch<ThermalZone>(`/api/thermal-zones/${zoneId}/`, payload);
    assertCurrent();
    const zones = await apiGet<PaginatedResponse<ThermalZone>>('/api/thermal-zones/?limit=80');
    assertCurrent();
    setData((current) => ({ ...current, zones: zones.results }));
    assertCurrent();
  }

  async function recordManualTemperature(zoneId: number, payload: ManualTemperaturePayload) {
    const { apiPost, setData, assertCurrent } = getOperationRequests(operationGeneration);
    const zone = await apiPost<ThermalZone>(`/api/thermal-zones/${zoneId}/temperature/`, payload);
    assertCurrent();
    setData((current) => ({
      ...current,
      zones: upsertThermalZones(current.zones, [zone]),
      overview: null,
      exportOptions: null,
    }));
    assertCurrent();
    return zone;
  }

  async function refreshZoneSalinityCapability(zoneId: number) {
    const { apiGet, setData, assertCurrent } = getOperationRequests(operationGeneration);
    const readings = await apiGet<ZoneSalinityMeasurement[]>(
      `/api/thermal-zones/${zoneId}/salinity/history/`,
    );
    assertCurrent();
    setData((current) => ({
      ...current,
      zones: current.zones.map((zone) => zone.id === zoneId
        ? { ...zone, latest_salinity: readings[0] ?? null }
        : zone),
    }));
    assertCurrent();
  }

  async function recordManualSalinity(zoneId: number, payload: ManualSalinityPayload) {
    const { apiPost, setData, assertCurrent } = getOperationRequests(operationGeneration);
    const zone = await apiPost<ThermalZone>(`/api/thermal-zones/${zoneId}/salinity/`, payload);
    assertCurrent();
    setData((current) => ({
      ...current,
      zones: upsertThermalZones(current.zones, [zone]),
    }));
    assertCurrent();
    return zone;
  }

  async function updateManualSalinity(
    zoneId: number,
    measurementId: number,
    payload: ManualSalinityUpdatePayload,
  ) {
    const { apiPatch, setData, assertCurrent } = getOperationRequests(operationGeneration);
    const zone = await apiPatch<ThermalZone>(
      `/api/thermal-zones/${zoneId}/salinity/${measurementId}/`,
      payload,
    );
    assertCurrent();
    setData((current) => ({
      ...current,
      zones: upsertThermalZones(current.zones, [zone]),
    }));
    assertCurrent();
    return zone;
  }

  async function createProbe(payload: ProbePayload) {
    const { apiGet, apiPost, setData, assertCurrent } = getOperationRequests(operationGeneration);
    await apiPost<Probe>('/api/probes/', payload);
    assertCurrent();
    // Probes are nested inside the zone payload, so refresh the zones list.
    const zones = await apiGet<PaginatedResponse<ThermalZone>>('/api/thermal-zones/?limit=80');
    assertCurrent();
    setData((current) => ({ ...current, zones: zones.results }));
    assertCurrent();
  }

  async function createOrganization(payload: OrganizationPayload) {
    const { apiGet, apiPost, setData, assertCurrent } = getOperationRequests(operationGeneration);
    await apiPost<Organization>('/api/organizations/', payload);
    assertCurrent();
    // Refresh linked lists so the new organization is usable immediately.
    const exportOptions = await apiGet<ExportOptions>('/api/exports/options/');
    assertCurrent();
    const profile = await apiGet<UserProfile>('/api/profile/');
    assertCurrent();
    setData((current) => ({ ...current, exportOptions, profile }));
    assertCurrent();
  }

  async function updateOrganization(organizationId: number, payload: OrganizationPayload) {
    const { apiGet, apiPatch, setData, assertCurrent } = getOperationRequests(operationGeneration);
    await apiPatch<Organization>(`/api/organizations/${organizationId}/`, payload);
    assertCurrent();
    const exportOptions = await apiGet<ExportOptions>('/api/exports/options/');
    assertCurrent();
    const profile = await apiGet<UserProfile>('/api/profile/');
    assertCurrent();
    setData((current) => ({ ...current, exportOptions, profile }));
    assertCurrent();
  }

  async function deleteOrganization(organizationId: number) {
    const { apiGet, apiDelete, setData, assertCurrent } = getOperationRequests(operationGeneration);
    await apiDelete<void>(`/api/organizations/${organizationId}/`);
    assertCurrent();
    const exportOptions = await apiGet<ExportOptions>('/api/exports/options/');
    assertCurrent();
    const profile = await apiGet<UserProfile>('/api/profile/');
    assertCurrent();
    setData((current) => ({ ...current, exportOptions, profile }));
    assertCurrent();
  }

  async function createBoxTransfer(payload: BoxTransferPayload) {
    const { apiPost, assertCurrent } = getOperationRequests(operationGeneration);
    const result = await apiPost<BoxTransferResult>('/api/box-transfers/', payload);
    assertCurrent();
    return result;
  }

  function handleAuthenticated() {
    const nextPath = new URLSearchParams(window.location.search).get('next');
    // Access is checked by the existing guards after profile bootstrap. Recognize
    // the destination now without treating an unknown parser fallback as home.
    const destination = nextPath && isRecognizedAppPath(nextPath, true, true) ? nextPath : '/';

    resetNavigation(destination, null);
    setRoute(getCurrentRoute());
    setError(null);
    setIsLoginRoute(false);
  }

  if (passwordReset) {
    return (
      <PasswordResetPage
        uid={passwordReset.uid}
        token={passwordReset.token}
        t={t}
        onDone={() => {
          resetNavigation('/login', null);
          setPasswordReset(null);
          setIsLoginRoute(true);
        }}
      />
    );
  }

  if (isLoginRoute) {
    return <LoginPage onAuthenticated={handleAuthenticated} t={t} />;
  }

  if (needsOrganizationChoice && data.profile) {
    return (
      <OrganizationChoiceScreen
        isLoading={isLoading}
        organizations={selectableOrganizations}
        profile={data.profile}
        t={t}
        onSelect={(organizationId) => void chooseOrganization(organizationId)}
      />
    );
  }

  if (activeTab === 'admin' && !isDesktopApp) return null;

  // Routes that need every box wait for the list on their own, so the rest of
  // the application stays usable while it loads or if it fails.
  const isBoxCollectionLoading = !isBoxCollectionReady && boxCollection.status !== 'error';
  const requestBoxCollectionRetry = () => void requestBoxCollection({ retry: true });
  const boxCollectionError = boxCollection.status === 'error' ? (
    <section className="login-notice" role="alert">
      <h2>{t('pageLoadErrorTitle')}</h2>
      <p>{t('boxCollectionLoadError')}</p>
      <button className="secondary-button" type="button" onClick={requestBoxCollectionRetry}>
        {t('boxCollectionRetry')}
      </button>
    </section>
  ) : null;

  const brandIdentity = (
    <>
      <span className="brand-mark" aria-hidden="true">
        <img src="/jellyfish.svg" alt="" />
      </span>
      <div className="brand-text">
        <p className="eyebrow">Polypbase</p>
        <strong>{brandOrganizationName}</strong>
      </div>
    </>
  );

  return (
    <main
      className={`app-shell${isEffectiveCollapsed ? ' is-sidebar-collapsed' : ''}${isTabletLayout ? ' is-tablet-rail' : ''}`}
    >
      <aside className={isEffectiveCollapsed ? 'sidebar is-collapsed' : 'sidebar'}>
        <div className="brand-switcher">
          {selectableOrganizations.length > 1 ? (
            <button
              className="brand-block is-clickable"
              type="button"
              aria-expanded={isOrganizationMenuOpen}
              aria-haspopup="menu"
              onClick={() => setIsOrganizationMenuOpen((isOpen) => !isOpen)}
            >
              {brandIdentity}
            </button>
          ) : (
            <div className="brand-block">{brandIdentity}</div>
          )}

          {isOrganizationMenuOpen && selectableOrganizations.length > 1 ? (
            <div className="organization-menu" role="menu">
              <p className="organization-menu-title">{t('organizationMenuTitle')}</p>
              {selectableOrganizations.map((organization) => {
                const role = getMembershipRoleLabel(
                  data.profile,
                  organization.id,
                  t('roleResponsable'),
                );
                return (
                  <button
                    key={organization.id}
                    className={organization.id === activeOrganization?.id ? 'is-active' : ''}
                    type="button"
                    role="menuitem"
                    onClick={() => void chooseOrganization(organization.id)}
                  >
                    <span>
                      <strong>{organization.name}</strong>
                      {role ? <small>{role}</small> : null}
                    </span>
                    {organization.id === activeOrganization?.id ? <span aria-hidden="true">{t('profileDefaultOrganization')}</span> : null}
                  </button>
                );
              })}
            </div>
          ) : null}
        </div>

        {isTabletLayout ? (
          <button
            className="tablet-qr-action"
            type="button"
            aria-label={t('qrScannerTitle')}
            title={t('qrScannerTitle')}
            onClick={() => setIsTabletScannerOpen(true)}
          >
            <PolypbaseIcon name="qr-scan" size={22} />
          </button>
        ) : null}

        <nav className="tabbar" aria-label={t('mainNavigation')}>
          {availableTabs.map((tab) => {
            const label = t(tab);
            return (
              <button
                key={tab}
                className={tab === activeTab ? `tab tab-${tab} is-active` : `tab tab-${tab}`}
                type="button"
                aria-label={isNavLabelHidden ? label : undefined}
                title={isNavLabelHidden ? label : undefined}
                onClick={() => openTab(tab)}
              >
                {isDesktopApp || isTabletLayout ? (
                  <span className="tab-icon-slot" aria-hidden="true">
                    <PolypbaseIcon
                      name={tab === 'labels' && isTabletLayout ? 'label-qr' : TAB_ICONS[tab]}
                      size={isTabletLayout ? 22 : 19}
                      className="tab-icon"
                    />
                  </span>
                ) : null}
                <span className="tab-label">{label}</span>
              </button>
            );
          })}
        </nav>

        {isPhoneLayout ? (
          <PhoneBottomNavigation
            activeTab={activeTab}
            t={t}
            onOpenQr={() => setIsPhoneQrOpen(true)}
            onSelectTab={openTab}
          />
        ) : null}

        {isDesktopApp ? (
          <div className="sidebar-footer">
            <button
              className="sidebar-toggle"
              type="button"
              onClick={toggleSidebar}
              aria-label={isSidebarCollapsed ? t('sidebarExpand') : t('sidebarCollapse')}
              title={isSidebarCollapsed ? t('sidebarExpand') : t('sidebarCollapse')}
            >
              <PolypbaseIcon
                name={isSidebarCollapsed ? 'chevrons-right' : 'chevrons-left'}
                size={18}
                className="sidebar-toggle-icon"
              />
            </button>
          </div>
        ) : null}
      </aside>

      <section className="workspace">
        {!isBoxRoute && !isZoneRoute ? (
          <header className={activeTab === 'pilotage' ? 'page-heading pilotage-page-heading' : 'page-heading'}>
            <h1>{getTitle(activeTab, t)}</h1>
            {activeTab === 'pilotage' && !isPhoneLayout && canCreateBox ? (
              <button
                className="secondary-button button-icon-label pilotage-create-action"
                type="button"
                onClick={() => setIsCreateBoxOpen(true)}
              >
                <PolypbaseIcon name="plus" size={18} />
                {t('createBoxNew')}
              </button>
            ) : null}
          </header>
        ) : null}

        {refreshRecovery ? (
          <section className="login-notice" role="status">
            <p>{t('mutationRefreshFailed')}</p>
            <button className="secondary-button" type="button" disabled={isRecoveringRefresh} onClick={() => void recoverMutationRefresh()}>
              {t('reloadAction')}
            </button>
          </section>
        ) : null}

        {error ? (
          <div className="workspace-page">
            <ApplicationErrorNotice
              actionHref={error.requiresAuthentication
                ? `/login?next=${encodeURIComponent(`${window.location.pathname}${window.location.search}`)}`
                : `${window.location.pathname}${window.location.search}`}
              labels={{
                action: error.requiresAuthentication ? t('loginAction') : t('reloadAction'),
                title: error.requiresAuthentication ? t('loginRequired') : t('pageLoadErrorTitle'),
              }}
              message={error.message}
            />
          </div>
        ) : null}

        {!error && (
          <div className="workspace-page" key={workspacePageKey}>
            <Suspense
              fallback={(
                <PageLoader
                  label={t('pageLoading')}
                  variant={getRouteLoaderVariant(activeTab, isBoxRoute, isZoneRoute)}
                />
              )}
            >
            {activeTab === 'pilotage' && isBoxRoute && (
              <BoxPage
                key={`${activeOrganizationId}:${selectedBoxId}`}
                box={selectedBoxDetail ?? selectedBox}
                zones={data.zones}
                profile={data.profile}
                language={language}
                qrLabelSelection={qrLabelSelection}
                isLoading={isLoading || isBoxLoading || isBoxCodePending || isBoxDetailPending}
                onCreateMeasurement={createMeasurement}
                isOperationCurrent={() => operationGeneration === organizationRequestGenerationRef.current}
                onUpdateMeasurement={updateMeasurement}
                onRefreshMeasurementState={refreshBoxAfterMeasurement}
                onCreateSubculture={createSubculture}
                onMoveBox={moveBox}
                onDeactivateBox={deactivateBox}
                onReactivateBox={reactivateBox}
                onLoadLineageGraph={loadLineageGraph}
                measurementPrefill={measurementPrefill}
                onMeasurementPrefillConsumed={() => setMeasurementPrefill(null)}
                onOpenBox={openBox}
                onOpenZone={openZone}
                onAddQrLabel={addQrLabelToSelection}
                onBack={closeBoxPage}
                onOpenQrLabelSelection={openQrLabelSelection}
                confirmAction={confirmAction}
                t={t}
              />
            )}

            {activeTab === 'pilotage' && !isBoxRoute && (
              <PilotageView
                activeOrganizationId={activeOrganizationId}
                boxes={data.boxes}
                boxCollectionStatus={boxCollection.status}
                onRequestBoxCollection={requestBoxCollection}
                onRetryBoxCollection={requestBoxCollectionRetry}
                onResolveBoxCode={findBoxIdByCode}
                exportOptions={data.exportOptions}
                isLoading={isLoading}
                isCreateBoxOpen={isCreateBoxOpen}
                isPhoneLayout={isPhoneLayout}
                isOptionsLoading={isExportOptionsLoading}
                profile={data.profile}
                search={search}
                searchResults={filteredBoxes}
                recentBoxes={recentBoxes}
                onCreateBox={createBox}
                isOperationCurrent={() => operationGeneration === organizationRequestGenerationRef.current}
                onManageSpeciesCodes={isDesktopApp ? () => {
                  setIsCreateBoxOpen(false);
                  openAdminSection('references');
                } : undefined}
                onCreateBoxOpenChange={setIsCreateBoxOpen}
                onRequestOptions={() => setExportOptionsRequested(true)}
                confirmAction={confirmAction}
                onSearch={setSearch}
                onSelectBox={openBox}
                t={t}
              />
            )}

            {activeTab === 'overview' && (
              <OverviewView
                key={activeOrganizationId}
                loadHistory={loadOverviewHistory}
                boxes={data.overview}
                isLoading={isLoading || isOverviewLoading}
                language={language}
                onSelectBox={openBox}
                onOpenZone={openZone}
                t={t}
              />
            )}

            {activeTab === 'zones' && (
              route.zoneId != null ? (
                route.zoneHistory ? (
                  <ZoneMovementHistoryPage
                    direction={route.zoneHistoryDirection ?? 'arrival'}
                    isLoading={isLoading}
                    language={language}
                    zone={selectedZone}
                    onBack={() => closeZoneSubview(route.zoneId as number)}
                    onChangeDirection={(direction) => openZoneHistory(route.zoneId as number, direction)}
                    onOpenBox={openBox}
                    t={t}
                  />
                ) : boxCollectionError ? (
                  boxCollectionError
                ) : route.zoneBoxes ? (
                  <ZoneBoxesPage
                    boxes={data.boxes}
                    isLoading={isLoading || isBoxCollectionLoading}
                    language={language}
                    zone={selectedZone}
                    onBack={() => closeZoneSubview(route.zoneId as number)}
                    onOpenBox={openBox}
                    t={t}
                  />
                ) : (
                  <ZoneDetailPage
                    boxes={data.boxes}
                    isLoading={isLoading || isBoxCollectionLoading}
                    language={language}
                    zone={selectedZone}
                    canRecordManualTemperature={userCanWriteLabData(
                      data.profile,
                      selectedZone?.organization.id ?? -1,
                    )}
                    onBack={closeZonePage}
                    onOpenBox={openBox}
                    onOpenBoxes={openZoneBoxes}
                    onOpenHistory={openZoneHistory}
                    onRecordManualSalinity={recordManualSalinity}
                    onRefreshZoneSalinityCapability={refreshZoneSalinityCapability}
                    onRecordManualTemperature={recordManualTemperature}
                    onUpdateManualSalinity={updateManualSalinity}
                    t={t}
                  />
                )
              ) : boxCollectionError ?? (
                <ZonesView
                  boxes={data.boxes}
                  isLoading={isLoading || isBoxCollectionLoading}
                  zones={data.zones}
                  onOpenZone={openZone}
                  t={t}
                />
              )
            )}

            {activeTab === 'exports' && (
              <ExportsView
                isLoading={isLoading || isExportOptionsLoading}
                options={data.exportOptions}
                language={language}
              />
            )}

            {activeTab === 'admin' && isDesktopApp && (
              <AdminView
                activeOrganizationId={activeOrganizationId}
                activeSection={route.adminSection ?? 'accounts'}
                boxes={data.boxes}
                boxCollectionStatus={boxCollection.status}
                onRetryBoxCollection={requestBoxCollectionRetry}
                exportOptions={data.exportOptions}
                isLoading={isLoading}
                isOptionsLoading={isExportOptionsLoading}
                language={language}
                profile={data.profile}
                onResponsableChange={updateProfileMembershipResponsable}
                onSelectSection={openAdminSection}
                onRequestOptions={() => setExportOptionsRequested(true)}
                onCreateZone={createThermalZone}
                onUpdateZone={updateThermalZone}
                onCreateProbe={createProbe}
                onCreateOrganization={createOrganization}
                onUpdateOrganization={updateOrganization}
                onDeleteOrganization={deleteOrganization}
                onCreateTransfer={createBoxTransfer}
                onAssignBoxLocation={assignBoxInitialLocation}
                onOpenBox={openBox}
                onOpenZone={openZone}
                onBatchQualifyBoxes={qualifyBoxesBatch}
                onDeactivateBox={deactivateBox}
                onQualifyBox={qualifyBox}
                onReactivateBox={reactivateBox}
                onEditMeasurement={editMeasurementFromHistory}
                t={t}
                zones={data.zones}
              />
            )}

            {activeTab === 'labels' && (boxCollectionError ?? (
              <LabelsView
                boxes={data.boxes}
                isLoading={isLoading || isBoxCollectionLoading}
                labels={getLabelsViewLabels(t)}
                language={language}
                profile={data.profile}
                qrLabelSelection={qrLabelSelection}
                t={t}
                onOpenBox={openBox}
                onAddQrLabel={addQrLabelToSelection}
                onClearQrLabelSelection={clearQrLabelSelection}
                onRemoveQrLabel={removeQrLabelFromSelection}
              />
            ))}

            {activeTab === 'profile' && (
              <ProfileView
                isLoading={isLoading}
                labels={getProfileLabels(t)}
                language={language}
                profile={data.profile}
                activeOrganizationId={activeOrganizationId}
                canOpenAdmin={canUseAdmin && isDesktopApp}
                onSelectOrganization={(organizationId) => void chooseOrganization(organizationId)}
                onOpenAdmin={() => openTab('admin')}
                onOpenBox={openBox}
                onLogout={logoutCurrentUser}
                onUpdateLanguage={updateLanguage}
                t={t}
              />
            )}
            </Suspense>
          </div>
        )}
        {confirmActionModal}
      </section>
      {isTabletScannerOpen && isTabletLayout ? (
        <TabletQrScannerModal
          boxes={data.boxes}
          onResolveBoxCode={findBoxIdByCode}
          labels={{
            close: t('close'),

            found: t('qrScannerFound'),
            loading: t('qrScannerLoading'),
            permission: t('qrScannerPermission'),
            secureContext: t('qrScannerSecureContext'),
            start: t('qrScannerStart'),
            stop: t('qrScannerStop'),
            title: t('qrScannerTitle'),
            unsupported: t('qrScannerUnsupported'),
          }}
          onClose={() => setIsTabletScannerOpen(false)}
          onSelectBox={openScannedBox}
        />
      ) : null}

      {isPhoneLayout && isPhoneQrOpen ? (
        <QrSearchModal
          boxes={data.boxes}
          boxCollectionStatus={boxCollection.status}
          onRequestBoxCollection={requestBoxCollection}
          onRetryBoxCollection={requestBoxCollectionRetry}
          onResolveBoxCode={findBoxIdByCode}
          t={t}
          onClose={() => setIsPhoneQrOpen(false)}
          onSelectBox={(boxId) => {
            setIsPhoneQrOpen(false);
            openBox(boxId);
          }}
        />
      ) : null}
    </main>
  );
}

function PhoneBottomNavigation({
  activeTab,
  t,
  onOpenQr,
  onSelectTab,
}: {
  activeTab: TabId;
  t: TFunction;
  onOpenQr: () => void;
  onSelectTab: (tab: PhoneDestination) => void;
}) {
  return (
    <nav className="phone-bottom-nav" aria-label={t('mainNavigation')}>
      {PHONE_NAVIGATION_ITEMS.map((item) => {
        const label = t(item.labelKey);

        if (item.kind === 'action') {
          return (
            <button
              key={item.action}
              className="phone-nav-item phone-nav-qr"
              type="button"
              aria-label={t('searchOrScan')}
              title={label}
              onClick={onOpenQr}
            >
              <span className="phone-nav-icon" aria-hidden="true">
                <PolypbaseIcon name={item.icon} size={28} />
              </span>
            </button>
          );
        }

        const isActive = item.tab === activeTab;
        return (
          <button
            key={item.tab}
            className={isActive ? 'phone-nav-item is-active' : 'phone-nav-item'}
            type="button"
            aria-label={label}
            title={label}
            aria-current={isActive ? 'page' : undefined}
            onClick={() => onSelectTab(item.tab)}
          >
            <span className="phone-nav-icon" aria-hidden="true">
              <PolypbaseIcon name={item.icon} size={24} />
            </span>
          </button>
        );
      })}
    </nav>
  );
}

function QrSearchModal({
  boxes,
  boxCollectionStatus,
  onRequestBoxCollection,
  onRetryBoxCollection,
  onResolveBoxCode,
  t,
  onClose,
  onSelectBox,
}: {
  boxes: BoxItem[];
  boxCollectionStatus: BoxCollectionState['status'];
  onRequestBoxCollection: () => void;
  onRetryBoxCollection: () => void;
  onResolveBoxCode: (code: string) => Promise<number | null>;
  t: TFunction;
  onClose: () => void;
  onSelectBox: (boxId: number) => void;
}) {
  const dialogRef = useRef<HTMLElement | null>(null);
  const returnFocusRef = useRef<HTMLElement | null>(null);
  const [query, setQuery] = useState('');
  const [highlightedIndex, setHighlightedIndex] = useState(0);
  const isBoxCollectionReady = boxCollectionStatus === 'ready';
  const hasQuery = query.trim() !== '';
  const results = useMemo(
    () => (hasQuery && isBoxCollectionReady ? filterBoxes(boxes, query).slice(0, 5) : []),
    [boxes, hasQuery, isBoxCollectionReady, query],
  );

  // Scanning needs no list; typing a search does.
  useEffect(() => {
    if (hasQuery) onRequestBoxCollection();
  }, [hasQuery, onRequestBoxCollection]);

  useEffect(() => {
    returnFocusRef.current = document.activeElement instanceof HTMLElement
      ? document.activeElement
      : null;
    dialogRef.current?.querySelector<HTMLButtonElement>('.modal-close-button')?.focus();

    function handleKeyDown(event: globalThis.KeyboardEvent) {
      if (event.key === 'Escape') {
        event.preventDefault();
        onClose();
        return;
      }
      if (event.key !== 'Tab') return;

      const dialog = dialogRef.current;
      if (!dialog) return;
      const focusable = Array.from(dialog.querySelectorAll<HTMLElement>(
        'button:not(:disabled), input:not(:disabled), [tabindex]:not([tabindex="-1"])',
      ));
      if (!focusable.length) return;

      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      const active = document.activeElement;
      if (event.shiftKey && (active === first || !dialog.contains(active))) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && (active === last || !dialog.contains(active))) {
        event.preventDefault();
        first.focus();
      }
    }

    window.addEventListener('keydown', handleKeyDown);
    return () => {
      window.removeEventListener('keydown', handleKeyDown);
      returnFocusRef.current?.focus({ preventScroll: true });
    };
  }, [onClose]);

  function selectHighlightedResult() {
    const selected = results[highlightedIndex] ?? results[0];
    if (selected) {
      onSelectBox(selected.id);
      return;
    }
    // While the full list loads, submitting an exact box code still opens that box.
    if (hasQuery && !isBoxCollectionReady) {
      void onResolveBoxCode(query)
        .then((boxId) => {
          if (boxId != null) onSelectBox(boxId);
        })
        .catch(() => {
          // The search keeps its loading or retry state; nothing else to report.
        });
    }
  }

  function handleSearchKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (!results.length) return;
    if (event.key === 'ArrowDown') {
      event.preventDefault();
      setHighlightedIndex((current) => (current + 1) % results.length);
    } else if (event.key === 'ArrowUp') {
      event.preventDefault();
      setHighlightedIndex((current) => (current - 1 + results.length) % results.length);
    }
  }

  return (
    <ModalPortal>
      <div className="modal-backdrop qr-search-backdrop" role="presentation" onMouseDown={onClose}>
        <section
          ref={dialogRef}
          className="qr-search-modal"
          role="dialog"
          aria-modal="true"
          aria-labelledby="qr-search-title"
          onMouseDown={(event) => event.stopPropagation()}
        >
          <header className="qr-search-heading">
            <h2 id="qr-search-title">{t('searchOrScan')}</h2>
            <button className="modal-close-button" type="button" aria-label={t('close')} onClick={onClose}>
              <PolypbaseIcon name="close" size={18} />
            </button>
          </header>

          <div className="qr-search-content">
            <TabletQrScanner
              autoStart
              boxes={boxes}
              labels={{
                found: t('qrScannerFound'),
                loading: t('qrScannerLoading'),
                permission: t('qrScannerPermission'),
                secureContext: t('qrScannerSecureContext'),
                start: t('qrScannerStart'),
                stop: t('qrScannerStop'),
                unsupported: t('qrScannerUnsupported'),
              }}
              onResolveBoxCode={onResolveBoxCode}
              onSelectBox={onSelectBox}
            />

            <div className="qr-search-manual">
              <SearchField
                activeDescendant={results[highlightedIndex] ? `qr-search-result-${results[highlightedIndex].id}` : undefined}
                controls="qr-search-results"
                expanded={results.length > 0}
                labels={{ label: t('searchOrScan'), placeholder: t('searchPlaceholder') }}
                value={query}
                onChange={(value) => {
                  setQuery(value);
                  setHighlightedIndex(0);
                }}
                onKeyDown={handleSearchKeyDown}
                onSubmit={selectHighlightedResult}
              />

              {results.length > 0 ? (
                <div className="qr-search-results" id="qr-search-results" role="listbox">
                  <div className="qr-search-results-heading">
                    <span>{t('suggestions')}</span>
                    <span>{results.length}</span>
                  </div>
                  {results.map((box, index) => (
                    <button
                      key={box.id}
                      id={`qr-search-result-${box.id}`}
                      className={index === highlightedIndex ? 'qr-search-result is-selected' : 'qr-search-result'}
                      type="button"
                      role="option"
                      aria-selected={index === highlightedIndex}
                      onClick={() => onSelectBox(box.id)}
                    >
                      <span>
                        <strong>{box.global_code}</strong>
                        <small>{box.species.scientific_name}</small>
                      </span>
                      <small>{box.thermal_zone?.name ?? t('noZone')}</small>
                    </button>
                  ))}
                </div>
              ) : hasQuery && !isBoxCollectionReady ? (
                <BoxSearchStatus status={boxCollectionStatus} onRetry={onRetryBoxCollection} t={t} />
              ) : hasQuery ? (
                <p className="qr-search-empty">{t('searchNoResults')}</p>
              ) : null}
            </div>
          </div>
        </section>
      </div>
    </ModalPortal>
  );
}

function getRouteLoaderVariant(activeTab: TabId, isBoxRoute: boolean, isZoneRoute: boolean) {
  if (activeTab === 'pilotage') return isBoxRoute ? 'box' as const : 'pilotage' as const;
  if (activeTab === 'overview') return 'overview' as const;
  if (activeTab === 'zones') return isZoneRoute ? 'zone' as const : 'zones' as const;
  if (activeTab === 'exports') return 'exports' as const;
  if (activeTab === 'labels') return 'labels' as const;
  if (activeTab === 'profile') return 'profile' as const;
  return 'admin' as const;
}

function OrganizationChoiceScreen({
  isLoading,
  organizations,
  profile,
  t,
  onSelect,
}: {
  isLoading: boolean;
  organizations: Organization[];
  profile: UserProfile;
  t: TFunction;
  onSelect: (organizationId: number) => void;
}) {
  if (isLoading) {
    return <PageLoader variant="profile" label={t('organizationChoiceLoading')} />;
  }

  return (
    <main className="organization-choice-page">
      <section className="organization-choice-panel">
        <div className="organization-choice-brand">
          <span className="brand-mark" aria-hidden="true">
            <img src="/jellyfish.svg" alt="" />
          </span>
          <div>
            <p className="eyebrow">Polypbase</p>
            <h1>{t('organizationChoiceTitle')}</h1>
          </div>
        </div>

        <p className="organization-choice-intro">
          {t('organizationChoiceIntro')}
        </p>

        <div className="organization-choice-list">
          {organizations.map((organization) => {
            const roleLabel = getMembershipRoleLabel(
              profile,
              organization.id,
              t('roleResponsable'),
            ) ?? t('profileFullAccess');
            return (
              <button
                key={organization.id}
                className="organization-choice-card"
                type="button"
                onClick={() => onSelect(organization.id)}
              >
                <span>
                  <strong>{organization.name}</strong>
                  <small>{roleLabel}</small>
                </span>
                <span aria-hidden="true">{t('organizationChoiceOpen')}</span>
              </button>
            );
          })}
        </div>
      </section>
    </main>
  );
}

function BoxSearchStatus({
  status,
  onRetry,
  t,
}: {
  status: BoxCollectionState['status'];
  onRetry: () => void;
  t: TFunction;
}) {
  if (status === 'error') {
    return (
      <section className="suggestion-panel" role="alert">
        <p className="muted compact-text">{t('boxCollectionLoadError')}</p>
        <button className="secondary-button" type="button" onClick={onRetry}>
          {t('boxCollectionRetry')}
        </button>
      </section>
    );
  }

  return (
    <section className="suggestion-panel" role="status">
      <p className="muted compact-text">{t('loading')}</p>
    </section>
  );
}

function PilotageView({
  activeOrganizationId,
  boxes,
  boxCollectionStatus,
  onRequestBoxCollection,
  onRetryBoxCollection,
  onResolveBoxCode,
  exportOptions,
  isLoading,
  isCreateBoxOpen,
  isPhoneLayout,
  isOptionsLoading,
  profile,
  recentBoxes,
  search,
  searchResults,
  onCreateBox,
  isOperationCurrent,
  onCreateBoxOpenChange,
  onManageSpeciesCodes,
  onRequestOptions,
  confirmAction,
  t,
  onSearch,
  onSelectBox,
}: {
  activeOrganizationId: number | null;
  boxes: BoxItem[];
  boxCollectionStatus: BoxCollectionState['status'];
  onRequestBoxCollection: () => void;
  onRetryBoxCollection: () => void;
  onResolveBoxCode: (code: string) => Promise<number | null>;
  exportOptions: ExportOptions | null;
  isLoading: boolean;
  isCreateBoxOpen: boolean;
  isPhoneLayout: boolean;
  isOptionsLoading: boolean;
  profile: UserProfile | null;
  recentBoxes: BoxItem[];
  search: string;
  searchResults: BoxItem[];
  onCreateBox: (payload: BoxCreatePayload) => Promise<BoxDetail>;
  isOperationCurrent: () => boolean;
  onCreateBoxOpenChange: (isOpen: boolean) => void;
  onManageSpeciesCodes?: () => void;
  onRequestOptions: () => void;
  confirmAction: ConfirmAction;
  t: TFunction;
  onSearch: (value: string) => void;
  onSelectBox: (id: number) => void;
}) {
  const hasSearch = Boolean(search.trim());
  const isBoxCollectionReady = boxCollectionStatus === 'ready';
  const visibleSuggestions = hasSearch
    ? searchResults.slice(0, isPhoneLayout ? PHONE_RESULT_LIMIT : PILOTAGE_RESULT_LIMIT)
    : [];
  const [tabletLookupMode, setTabletLookupMode] = useState<'qr' | 'search'>('qr');
  const [highlightedSuggestionIndex, setHighlightedSuggestionIndex] = useState(0);
  const canCreateBox = userCanCreateBoxes(profile);

  function selectFirstSuggestion() {
    const selectedSuggestion = visibleSuggestions[highlightedSuggestionIndex] ?? visibleSuggestions[0];
    if (selectedSuggestion) {
      onSelectBox(selectedSuggestion.id);
      return;
    }
    // While the full list loads, submitting an exact box code still opens that box.
    if (hasSearch && !isBoxCollectionReady) {
      void onResolveBoxCode(search)
        .then((boxId) => {
          if (boxId != null) onSelectBox(boxId);
        })
        .catch(() => {
          // The search keeps its loading or retry state; nothing else to report.
        });
    }
  }

  function handleSearchChange(value: string) {
    setHighlightedSuggestionIndex(0);
    onSearch(value);
  }

  function highlightSuggestion(index: number) {
    setHighlightedSuggestionIndex(index);
    const prefix = isPhoneLayout ? 'box-suggestion' : 'box-search-result';
    document.getElementById(`${prefix}-${visibleSuggestions[index].id}`)?.scrollIntoView({ block: 'nearest', inline: 'nearest' });
  }

  function handleSearchKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === 'Escape') {
      event.preventDefault();
      handleSearchChange('');
      return;
    }
    if (!visibleSuggestions.length) return;

    if (event.key === 'ArrowDown') {
      event.preventDefault();
      highlightSuggestion((highlightedSuggestionIndex + 1) % visibleSuggestions.length);
    } else if (event.key === 'ArrowUp') {
      event.preventDefault();
      highlightSuggestion((highlightedSuggestionIndex - 1 + visibleSuggestions.length) % visibleSuggestions.length);
    }
  }

  const resultIdPrefix = isPhoneLayout ? 'box-suggestion' : 'box-search-result';
  const resultListId = isPhoneLayout ? 'box-suggestions' : 'box-search-results';
  const searchFieldProps = {
    activeDescendant: visibleSuggestions[highlightedSuggestionIndex]
      ? `${resultIdPrefix}-${visibleSuggestions[highlightedSuggestionIndex].id}`
      : undefined,
    controls: isPhoneLayout || hasSearch ? resultListId : undefined,
    expanded: hasSearch,
    labels: {
      label: t('searchOrScan'),
      placeholder: t('searchPlaceholder'),
    },
    value: search,
    onChange: handleSearchChange,
    onKeyDown: handleSearchKeyDown,
    onSubmit: selectFirstSuggestion,
  };

  if (isLoading) {
    return <PageLoader variant="pilotage" label={t('pilotageTitle')} />;
  }

  return (
    <section className="pilotage-flow">
      <div className="lookup-panel">
        {!isPhoneLayout ? (
          <div className="pilotage-search-surface">
            <SearchField
              {...searchFieldProps}
              labels={{
                label: t('searchOrScan'),
                placeholder: t('pilotageSearchPlaceholder'),
              }}
              clearLabel={t('searchClear')}
              variant="control-deck"
            />
            {hasSearch && !isBoxCollectionReady ? (
              <BoxSearchStatus status={boxCollectionStatus} onRetry={onRetryBoxCollection} t={t} />
            ) : hasSearch ? (
              <SuggestionList
                boxes={visibleSuggestions}
                listId={resultListId}
                resultIdPrefix={resultIdPrefix}
                selectedBoxId={visibleSuggestions[highlightedSuggestionIndex]?.id ?? null}
                totalCount={searchResults.length}
                heading={t('searchResults')}
                isPhoneLayout={false}
                onClear={() => handleSearchChange('')}
                onSelectBox={onSelectBox}
                t={t}
              />
            ) : null}
          </div>
        ) : (
          <>
            <section className={`phone-lookup-panel is-${tabletLookupMode}-mode`}>
              <div className="tablet-lookup-tabs" role="tablist" aria-label={t('searchOrScan')}>
                <button
                  className={tabletLookupMode === 'qr' ? 'is-active' : ''}
                  type="button"
                  role="tab"
                  aria-selected={tabletLookupMode === 'qr'}
                  onClick={() => setTabletLookupMode('qr')}
                >
                  {t('qrCode')}
                </button>
                <button
                  className={tabletLookupMode === 'search' ? 'is-active' : ''}
                  type="button"
                  role="tab"
                  aria-selected={tabletLookupMode === 'search'}
                  onClick={() => setTabletLookupMode('search')}
                >
                  {t('searchTab')}
                </button>
              </div>

              {tabletLookupMode === 'qr' ? (
                <TabletQrScanner
                  boxes={boxes}
                  labels={{
                    found: t('qrScannerFound'),
                    loading: t('qrScannerLoading'),
                    permission: t('qrScannerPermission'),
                    secureContext: t('qrScannerSecureContext'),
                    start: t('qrScannerStart'),
                    stop: t('qrScannerStop'),
                    unsupported: t('qrScannerUnsupported'),
                  }}
                  onResolveBoxCode={onResolveBoxCode}
                  onSelectBox={onSelectBox}
                />
              ) : (
                <div className="tablet-manual-search">
                  <SearchField {...searchFieldProps} />
                </div>
              )}
            </section>

            <div className="mobile-suggestion-slot">
              {tabletLookupMode === 'search' && hasSearch && !isBoxCollectionReady ? (
                <BoxSearchStatus status={boxCollectionStatus} onRetry={onRetryBoxCollection} t={t} />
              ) : tabletLookupMode === 'search' && hasSearch ? (
                <SuggestionList
                  boxes={visibleSuggestions}
                  listId={resultListId}
                  resultIdPrefix={resultIdPrefix}
                  selectedBoxId={visibleSuggestions[highlightedSuggestionIndex]?.id ?? null}
                  totalCount={visibleSuggestions.length}
                  heading={t('suggestions')}
                  isPhoneLayout
                  onClear={() => handleSearchChange('')}
                  onSelectBox={onSelectBox}
                  t={t}
                />
              ) : null}
            </div>
          </>
        )}

        {(!hasSearch || isPhoneLayout) ? (
          <RecentAccessList boxes={recentBoxes} isPhoneLayout={isPhoneLayout} onSelectBox={onSelectBox} t={t} />
        ) : null}

        {canCreateBox ? (
          <CreateBoxPanel
            key={activeOrganizationId ?? 'none'}
            boxes={boxes}
            isBoxCollectionReady={isBoxCollectionReady}
            onRequestBoxCollection={onRequestBoxCollection}
            exportOptions={exportOptions}
            isOpen={isPhoneLayout ? undefined : isCreateBoxOpen}
            isOptionsLoading={isOptionsLoading}
            presentation={isPhoneLayout ? 'inline' : 'modal'}
            profile={profile}
            t={t}
            onCreateBox={onCreateBox}
            isOperationCurrent={isOperationCurrent}
            onManageSpeciesCodes={onManageSpeciesCodes}
            onOpenChange={isPhoneLayout ? undefined : onCreateBoxOpenChange}
            onRequestOptions={onRequestOptions}
            confirmAction={confirmAction}
            onSelectBox={onSelectBox}
          />
        ) : null}
      </div>
    </section>
  );
}

function CreateBoxPanel({
  boxes,
  isBoxCollectionReady,
  onRequestBoxCollection,
  exportOptions,
  isOpen: controlledIsOpen,
  isOptionsLoading,
  presentation = 'inline',
  profile,
  confirmAction,
  onCreateBox,
  isOperationCurrent,
  onManageSpeciesCodes,
  onOpenChange,
  onRequestOptions,
  onSelectBox,
  t,
}: {
  boxes: BoxItem[];
  isBoxCollectionReady: boolean;
  onRequestBoxCollection: () => void;
  exportOptions: ExportOptions | null;
  isOpen?: boolean;
  isOptionsLoading: boolean;
  presentation?: 'inline' | 'modal';
  profile: UserProfile | null;
  confirmAction: ConfirmAction;
  onCreateBox: (payload: BoxCreatePayload) => Promise<BoxDetail>;
  isOperationCurrent: () => boolean;
  onManageSpeciesCodes?: () => void;
  onOpenChange?: (isOpen: boolean) => void;
  onRequestOptions: () => void;
  onSelectBox: (id: number) => void;
  t: TFunction;
}) {
  const [isInlineOpen, setIsInlineOpen] = useState(false);
  const isOpen = presentation === 'modal' ? Boolean(controlledIsOpen) : isInlineOpen;
  const dialogRef = useRef<HTMLElement | null>(null);
  const returnFocusRef = useRef<HTMLElement | null>(null);
  const isQuickStrainOpenRef = useRef(false);
  const operationLifetimeRef = useRef(true);
  useLayoutEffect(() => {
    operationLifetimeRef.current = true;
    return () => { operationLifetimeRef.current = false; };
  }, []);
  const isConfirmationOpenRef = useRef(false);
  const activeOrganization = profile?.active_organization ?? null;
  const organizationId = activeOrganization?.id ?? null;
  const [strainId, setStrainId] = useState<number | null>(null);
  const [zoneId, setZoneId] = useState<number | null>(null);
  const [globalCode, setGlobalCode] = useState('');
  const [boxNumber, setBoxNumber] = useState('');
  const [enteredOn, setEnteredOn] = useState(() => new Date().toISOString().slice(0, 10));
  const [notes, setNotes] = useState('');
  const [strainSearch, setStrainSearch] = useState('');
  const [createdStrains, setCreatedStrains] = useState<QuickCreatedStrain[]>([]);
  const [references, setReferences] = useState<TaxonomyReferences | null>(null);
  const [isReferencesLoading, setIsReferencesLoading] = useState(false);
  const [referencesError, setReferencesError] = useState<string | null>(null);
  const [isQuickStrainOpen, setIsQuickStrainOpen] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function setOpen(nextIsOpen: boolean) {
    if (presentation === 'modal') onOpenChange?.(nextIsOpen);
    else setIsInlineOpen(nextIsOpen);
  }

  useEffect(() => {
    if (!isOpen || organizationId == null) return;
    let isCurrent = true;
    setIsReferencesLoading(true);
    setReferencesError(null);
    apiGet<TaxonomyReferences>('/api/taxonomy/references/')
      .then((result) => {
        if (isCurrent) setReferences(result);
      })
      .catch((requestError) => {
        if (isCurrent) setReferencesError(getErrorMessage(requestError, t('taxonomyLoadError')));
      })
      .finally(() => {
        if (isCurrent) setIsReferencesLoading(false);
      });
    return () => { isCurrent = false; };
  }, [isOpen, organizationId, t]);

  const strains = useMemo(() => {
    const options = (references?.strains ?? []).map((strain) => ({
      id: strain.id,
      code: strain.code,
      species_id: strain.species,
      species_name: strain.species_scientific_name,
    }));
    return [...options, ...createdStrains.filter((created) =>
      !options.some((option) => option.id === created.id))];
  }, [createdStrains, references?.strains]);
  const normalizedStrainSearch = strainSearch.trim().toLocaleLowerCase('fr-FR');
  const filteredStrains = strains.filter((strain) => {
    if (!normalizedStrainSearch || strain.id === strainId) return true;
    return `${strain.species_name} ${strain.code}`.toLocaleLowerCase('fr-FR').includes(normalizedStrainSearch);
  });
  const selectedStrain = strains.find((strain) => strain.id === strainId) ?? null;
  const availableZones = (exportOptions?.zones ?? []).filter((zone) => zone.organization_id === organizationId);
  const selectedZone = availableZones.find((zone) => zone.id === zoneId) ?? null;
  const canSubmit = organizationId != null && references != null && !isReferencesLoading && !referencesError &&
    selectedStrain != null && zoneId != null && globalCode.trim() && boxNumber.trim();

  useEffect(() => {
    if (strainId != null && strains.some((strain) => strain.id === strainId)) return;
    setStrainId(strains[0]?.id ?? null);
  }, [strainId, strains]);

  // The next code is derived from every existing box, so it waits for the full list.
  useEffect(() => {
    if (isOpen) onRequestBoxCollection();
  }, [isOpen, onRequestBoxCollection]);

  useEffect(() => {
    if (organizationId == null || !selectedStrain || !isBoxCollectionReady) return;
    const suggestion = buildNextBoxCode(boxes, selectedStrain, organizationId);
    setGlobalCode((current) => current.trim() ? current : suggestion.globalCode);
    setBoxNumber((current) => current.trim() ? current : suggestion.boxNumber);
  }, [boxes, isBoxCollectionReady, organizationId, selectedStrain]);

  useEffect(() => {
    if (zoneId == null || availableZones.some((zone) => zone.id === zoneId)) return;
    setZoneId(null);
  }, [availableZones, zoneId]);

  useEffect(() => {
    isQuickStrainOpenRef.current = isQuickStrainOpen;
  }, [isQuickStrainOpen]);

  useEffect(() => {
    if (presentation === 'modal' && isOpen && !exportOptions) onRequestOptions();
  }, [exportOptions, isOpen, onRequestOptions, presentation]);

  useLayoutEffect(() => {
    if (presentation !== 'modal' || !isOpen) return;
    returnFocusRef.current = document.activeElement instanceof HTMLElement
      ? document.activeElement
      : null;
    dialogRef.current?.querySelector<HTMLButtonElement>('.modal-close-button')?.focus();
  }, [isOpen, presentation]);

  useEffect(() => {
    if (presentation !== 'modal' || !isOpen) return;

    function handleKeyDown(event: globalThis.KeyboardEvent) {
      if (isQuickStrainOpenRef.current || isConfirmationOpenRef.current) return;
      if (event.key === 'Escape') {
        event.preventDefault();
        setOpen(false);
        return;
      }
      if (event.key !== 'Tab') return;

      const dialog = dialogRef.current;
      if (!dialog) return;
      const focusable = Array.from(dialog.querySelectorAll<HTMLElement>(DIALOG_FOCUSABLE_SELECTOR));
      if (!focusable.length) return;

      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      const active = document.activeElement;
      if (event.shiftKey && (active === first || !dialog.contains(active))) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && (active === last || !dialog.contains(active))) {
        event.preventDefault();
        first.focus();
      }
    }

    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [isOpen, onOpenChange, presentation]);

  useEffect(() => {
    if (presentation !== 'modal' || !isOpen) return;
    const returnFocus = returnFocusRef.current;
    return () => {
      if (returnFocus?.isConnected) returnFocus.focus({ preventScroll: true });
    };
  }, [isOpen, presentation]);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (isSaving || !canSubmit || organizationId == null || strainId == null || zoneId == null) return;

    if (!boxCodeMatchesBoxNumber(globalCode, boxNumber)) {
      setError(t('createBoxNumberMismatch'));
      return;
    }

    let confirmed = false;
    isConfirmationOpenRef.current = true;
    try {
      confirmed = await confirmAction({
        title: t('confirmCreateBoxTitle'),
        message: t('confirmCreateBoxMessage'),
        confirmLabel: t('confirmCreateBoxAction'),
        cancelLabel: t('confirmCancel'),
        details: [
          { label: t('confirmDetailBox'), value: globalCode.trim() },
          { label: t('confirmDetailSpecies'), value: selectedStrain?.species_name },
          { label: t('confirmDetailStrain'), value: selectedStrain?.code },
          { label: t('confirmDetailOrganization'), value: activeOrganization?.name },
          { label: t('confirmDetailLocation'), value: selectedZone?.name ?? t('createBoxNoZone') },
        ],
      });
    } finally {
      isConfirmationOpenRef.current = false;
    }
    if (!confirmed || !operationLifetimeRef.current || !isOperationCurrent()) return;

    setIsSaving(true);
    setError(null);

    try {
      const created = await onCreateBox({
        strain: strainId,
        thermal_zone: zoneId,
        global_code: globalCode.trim(),
        local_code: '',
        box_number: boxNumber.trim(),
        entered_on: enteredOn,
        volume_liters: null,
        notes: notes.trim(),
      });
      if (!operationLifetimeRef.current || !isOperationCurrent()) return;
      setGlobalCode('');
      setBoxNumber('');
      setNotes('');
      onSelectBox(created.id);
      if (presentation === 'modal') {
        window.requestAnimationFrame(() => {
          document.querySelector<HTMLElement>('[data-box-page-focus-target]')?.focus({ preventScroll: true });
        });
      }
    } catch (requestError) {
      if (!operationLifetimeRef.current || !isOperationCurrent() || requestError instanceof ApiResourceCancelledError) return;
      if (requestError instanceof ApiError && requestError.status === 403) {
        setError(t('createBoxForbidden'));
      } else {
        setError(getErrorMessage(requestError));
      }
    } finally {
      if (operationLifetimeRef.current && isOperationCurrent()) setIsSaving(false);
    }
  }

  const panel = (
    <section className={presentation === 'modal' ? 'create-box-panel is-modal' : 'create-box-panel'}>
      {presentation === 'inline' ? (
        <button
          className="create-box-toggle"
          type="button"
          onClick={() => {
            if (!isOpen && !exportOptions) onRequestOptions();
            setOpen(!isOpen);
          }}
        >
          <span aria-hidden="true">
            <PolypbaseIcon name={isOpen ? 'close' : 'plus'} size={18} />
          </span>
          <strong>{isOpen ? t('createBoxClose') : t('createBoxOpen')}</strong>
        </button>
      ) : null}

      {isOpen ? (
        <form className="create-box-form" onSubmit={handleSubmit}>
          <div className="section-title">
            <h2>{t('createBoxTitle')}</h2>
          </div>

          {isOptionsLoading || isReferencesLoading ? <p className="muted compact-text">{t('loading')}</p> : null}
          {referencesError ? <p className="inline-error">{referencesError}</p> : null}
          {!isReferencesLoading && !referencesError && references && !strains.length ? <p className="muted compact-text">{t('createBoxNoOptions')}</p> : null}

          <div className="create-box-strain-field">
            <div className="create-box-field-heading">
              <span>{t('createBoxStrain')}</span>
              {userHasAdminRole(profile) ? (
                <button
                  className="create-box-reference-action"
                  type="button"
                  title={t('quickStrainTitle')}
                  onClick={() => setIsQuickStrainOpen(true)}
                >
                  <PolypbaseIcon name="plus" size={17} />
                  {t('quickStrainAdd')}
                </button>
              ) : null}
            </div>
            <label className="create-box-strain-search">
              <PolypbaseIcon name="search" size={16} />
              <span className="sr-only">{t('quickStrainSearch')}</span>
              <input
                type="search"
                value={strainSearch}
                placeholder={t('quickStrainSearch')}
                onChange={(event) => setStrainSearch(event.target.value)}
              />
            </label>
            <select
              aria-label={t('createBoxStrain')}
              value={strainId ?? ''}
              onChange={(event) => {
                setStrainId(Number(event.target.value));
                setGlobalCode('');
                setBoxNumber('');
              }}
            >
              {filteredStrains.map((strain) => (
                <option key={strain.id} value={strain.id}>
                  {strain.species_name} - {strain.code}
                </option>
              ))}
            </select>
          </div>

          <label className="create-box-location-field">
            <span>{t('createBoxZone')}</span>
            <select required value={zoneId ?? ''} onChange={(event) => setZoneId(event.target.value ? Number(event.target.value) : null)}>
              <option value="" disabled>{t('createBoxNoZone')}</option>
              {availableZones.map((zone) => (
                <option key={zone.id} value={zone.id}>
                  {zone.name}
                </option>
              ))}
            </select>
          </label>

          <label className="create-box-code-field">
            <span>{t('createBoxGlobalCode')}</span>
            <input required value={globalCode} onChange={(event) => setGlobalCode(event.target.value)} />
          </label>

          <label className="create-box-number-field">
            <span>{t('createBoxNumber')}</span>
            <input required value={boxNumber} onChange={(event) => setBoxNumber(event.target.value)} />
          </label>

          <label className="create-box-date-field">
            <span>{t('createBoxEnteredOn')}</span>
            <input required type="date" value={enteredOn} onChange={(event) => setEnteredOn(event.target.value)} />
          </label>

          <label className="create-box-wide">
            <span>{t('createBoxNotes')}</span>
            <textarea rows={2} value={notes} onChange={(event) => setNotes(event.target.value)} />
          </label>

          <button type="submit" disabled={isSaving || !canSubmit}>
            {isSaving ? t('saving') : t('createBoxSubmit')}
          </button>
          {error ? <p className="inline-error">{error}</p> : null}
        </form>
      ) : null}

      {isQuickStrainOpen && userHasAdminRole(profile) ? (
        <QuickStrainCreator
          t={t}
          onClose={() => setIsQuickStrainOpen(false)}
          onManageCodes={onManageSpeciesCodes}
          onCreated={(strain) => {
            setCreatedStrains((current) => [...current.filter((item) => item.id !== strain.id), strain]);
            setStrainId(strain.id);
            setStrainSearch(`${strain.species_name} ${strain.code}`);
            setGlobalCode('');
            setBoxNumber('');
            setIsQuickStrainOpen(false);
          }}
        />
      ) : null}
    </section>
  );

  if (presentation === 'modal') {
    if (!isOpen) return null;
    return (
      <ModalPortal>
        <div className="modal-backdrop create-box-backdrop" role="presentation" onMouseDown={() => setOpen(false)}>
          <section
            ref={dialogRef}
            className="create-box-modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="create-box-modal-title"
            onMouseDown={(event) => event.stopPropagation()}
          >
            <header className="modal-heading create-box-modal-heading">
              <h2 id="create-box-modal-title">{t('createBoxTitle')}</h2>
              <button className="modal-close-button" type="button" aria-label={t('close')} onClick={() => setOpen(false)}>
                <PolypbaseIcon name="close" size={18} />
              </button>
            </header>
            <div className="create-box-modal-content">{panel}</div>
          </section>
        </div>
      </ModalPortal>
    );
  }

  return panel;
}

function RecentAccessList({
  boxes,
  isPhoneLayout,
  onSelectBox,
  t,
}: {
  boxes: BoxItem[];
  isPhoneLayout: boolean;
  onSelectBox: (id: number) => void;
  t: TFunction;
}) {
  if (!boxes.length) {
    return <p className="muted compact-text">{t('noRecentScans')}</p>;
  }

  return (
    <section className="recent-panel" aria-label={t('recentAccessAriaLabel')}>
      <div className="section-title">
        <h2>{t('recentAccess')}</h2>
        <span>{t('scanSearch')}</span>
      </div>

      <div className="recent-strip">
        {boxes.map((box) => (
          <button
            key={box.id}
            type="button"
            onClick={() => onSelectBox(box.id)}
          >
            <span className="recent-box-heading">
              <strong>{box.global_code}</strong>
            </span>
            <small>{box.species.scientific_name}</small>
            <span className="recent-box-meta">
              <span>{box.thermal_zone?.name ?? t('noZone')}</span>
            </span>
            {!isPhoneLayout ? (
              <span className="recent-box-chevron" aria-hidden="true">
                <PolypbaseIcon name="chevron-right" size={16} />
              </span>
            ) : null}
          </button>
        ))}
      </div>
    </section>
  );
}

function SuggestionList({
  boxes,
  listId,
  resultIdPrefix,
  selectedBoxId,
  totalCount,
  heading,
  isPhoneLayout,
  onClear,
  onSelectBox,
  t,
}: {
  boxes: BoxItem[];
  listId: string;
  resultIdPrefix: string;
  selectedBoxId: number | null;
  totalCount: number;
  heading: string;
  isPhoneLayout: boolean;
  onClear?: () => void;
  onSelectBox: (id: number) => void;
  t: TFunction;
}) {
  return (
    <section className="suggestion-panel" aria-label={heading}>
      <div className="section-title">
        <h2>{heading}</h2>
        <span aria-live="polite">{totalCount}</span>
      </div>

      {boxes.length > 0 ? (
        <div className="suggestion-list" id={listId} role="listbox">
          {boxes.map((box) => (
            <button
              key={box.id}
              id={`${resultIdPrefix}-${box.id}`}
              className={selectedBoxId === box.id ? 'suggestion-row is-selected' : 'suggestion-row'}
              type="button"
              role="option"
              aria-selected={selectedBoxId === box.id}
              onClick={() => onSelectBox(box.id)}
            >
              {isPhoneLayout ? (
                <>
                  <span className="suggestion-identity">
                    <strong>{box.global_code}</strong>
                    <small>{box.species.scientific_name}</small>
                  </span>
                  <span className="suggestion-reading">
                    {box.latest_measurement ? (
                      <>
                        <strong>
                          {box.latest_measurement.polyp_count} {t('polyps').toLocaleLowerCase()},{' '}
                          {box.latest_measurement.ephyrae_count} {t('ephyrae').toLocaleLowerCase()}
                        </strong>
                        <small>{formatDisplayDate(box.latest_measurement.measured_on)}</small>
                      </>
                    ) : (
                      <small>{t('noMeasurementHistory')}</small>
                    )}
                  </span>
                  <span className="suggestion-location">
                    <strong>{box.thermal_zone?.name ?? t('noZone')}</strong>
                  </span>
                </>
              ) : (
                <>
                  <span className="suggestion-identity">
                    <strong>{box.global_code}</strong>
                  </span>
                  <span className="suggestion-context">
                    <strong>{box.species.scientific_name}</strong>
                  </span>
                  <span className="suggestion-reading">
                    {box.latest_measurement ? (
                      <>
                        <strong>
                          {box.latest_measurement.polyp_count} {t('polyps').toLocaleLowerCase()},{' '}
                          {box.latest_measurement.ephyrae_count} {t('ephyrae').toLocaleLowerCase()}
                        </strong>
                        <small>{formatDisplayDate(box.latest_measurement.measured_on)}</small>
                      </>
                    ) : (
                      <small>{t('noMeasurementHistory')}</small>
                    )}
                  </span>
                  <span className="suggestion-location">
                    <strong>{box.thermal_zone?.name ?? t('noZone')}</strong>
                  </span>
                </>
              )}
              <span className="suggestion-chevron" aria-hidden="true">
                <PolypbaseIcon name="chevron-right" size={17} />
              </span>
            </button>
          ))}
        </div>
      ) : (
        <div className="search-empty-state" id={listId} role="status">
          <p>{t('searchNoResults')}</p>
          {onClear ? (
            <button type="button" onClick={onClear}>{t('searchClear')}</button>
          ) : null}
        </div>
      )}
    </section>
  );
}

function BoxPage({
  box,
  zones,
  profile,
  language,
  qrLabelSelection,
  isLoading,
  onCreateMeasurement,
  isOperationCurrent,
  onUpdateMeasurement,
  onRefreshMeasurementState,
  onCreateSubculture,
  onMoveBox,
  onDeactivateBox,
  onReactivateBox,
  onLoadLineageGraph,
  measurementPrefill,
  onMeasurementPrefillConsumed,
  onOpenBox,
  onOpenZone,
  onAddQrLabel,
  onBack,
  onOpenQrLabelSelection,
  confirmAction,
  t,
}: {
  box: BoxItem | BoxDetail | null;
  zones: ThermalZone[];
  profile: UserProfile | null;
  language: Language;
  qrLabelSelection: QrLabelItem[];
  isLoading: boolean;
  onCreateMeasurement: (boxId: number, payload: MeasurementPayload) => Promise<BiologicalMeasurement>;
  isOperationCurrent: () => boolean;
  onUpdateMeasurement: (
    boxId: number,
    measurementId: number,
    payload: MeasurementPayload,
  ) => Promise<BiologicalMeasurement>;
  onRefreshMeasurementState: (boxId: number) => Promise<BoxDetail>;
  onCreateSubculture: (boxId: number, payload: SubculturePayload) => Promise<SubcultureResult>;
  onMoveBox: (boxId: number, payload: BoxMovePayload) => Promise<BoxDetail>;
  onDeactivateBox: (boxId: number, payload: BoxDeactivatePayload) => Promise<void>;
  onReactivateBox: (boxId: number, payload: BoxActivatePayload) => Promise<void>;
  onLoadLineageGraph: (boxId: number) => Promise<LineageGraph>;
  measurementPrefill: HistoryMeasurementPrefill | null;
  onMeasurementPrefillConsumed: () => void;
  onOpenBox: (boxId: number, globalCode: string) => void;
  onOpenZone: (zoneId: number) => void;
  onAddQrLabel: (label: QrLabelItem) => void;
  onBack: () => void;
  onOpenQrLabelSelection: () => void;
  confirmAction: ConfirmAction;
  t: TFunction;
}) {
  const operationLifetimeRef = useRef(true);
  useLayoutEffect(() => {
    operationLifetimeRef.current = true;
    return () => { operationLifetimeRef.current = false; };
  }, [box?.id]);
  const defaultSalinity = getDefaultMeasurementSalinity(box, zones);
  const [form, setForm] = useState(() => getInitialMeasurementForm(defaultSalinity));
  const [isSaving, setIsSaving] = useState(false);
  const isDesktopApp = useIsDesktopApp();
  const isPhoneLayout = useIsPhoneLayout();
  const isTabletLayout = !isDesktopApp && !isPhoneLayout;
  const [isHistoryOpen, setIsHistoryOpen] = useState(false);
  const [lineageGraph, setLineageGraph] = useState<LineageGraph | null>(null);
  const [isLineageGraphLoading, setIsLineageGraphLoading] = useState(false);
  const [lineageGraphError, setLineageGraphError] = useState<string | null>(null);
  const lineageRequestGenerationRef = useRef(0);
  const lineageRequestPendingRef = useRef(false);
  useEffect(() => () => {
    lineageRequestGenerationRef.current += 1;
    lineageRequestPendingRef.current = false;
  }, [box?.id]);
  const [isMoveOpen, setIsMoveOpen] = useState(false);
  const [isSavingMove, setIsSavingMove] = useState(false);
  const [isChangingBoxStatus, setIsChangingBoxStatus] = useState(false);
  const [lifecycleAction, setLifecycleAction] = useState<BoxLifecycleAction | null>(null);
  const [moveError, setMoveError] = useState<string | null>(null);
  const [statusError, setStatusError] = useState<string | null>(null);
  const [isSubcultureOpen, setIsSubcultureOpen] = useState(false);
  const [isSavingSubculture, setIsSavingSubculture] = useState(false);
  const [isQrLabelOpen, setIsQrLabelOpen] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [editingMeasurementId, setEditingMeasurementId] = useState<number | null>(null);
  const [isMeasurementEditorOpen, setIsMeasurementEditorOpen] = useState(false);
  // Set while the form holds values brought over from the history, so the save
  // button can say "correct" rather than "record": the technician is fixing an
  // existing measurement, not adding one.
  const [isCorrectingFromHistory, setIsCorrectingFromHistory] = useState(false);
  const [subcultureError, setSubcultureError] = useState<string | null>(null);
  const [subcultureSuccess, setSubcultureSuccess] = useState<SubcultureResult | null>(null);
  const [activeInsightTab, setActiveInsightTab] = useState<BoxInsightTab>('measurements');
  const [measurementReferenceDate, setMeasurementReferenceDate] = useState(getTodayDateValue);
  const measurements = box ? getMeasurementHistory(box) : [];
  const canWriteLabData = box
    ? userCanWriteLabData(profile, box.organization.id)
    : false;
  const canCreateMeasurement = box && 'can_create_measurement' in box
    ? box.can_create_measurement
    : false;
  const isBoxActive = box?.status === 'active';
  const weeklyMeasurement = findMeasurementForWeek(measurements, measurementReferenceDate);
  const measurementEditorMode = getMeasurementEditorMode({
    measurement: weeklyMeasurement,
    canCreateMeasurement,
  });
  const editingMeasurement = editingMeasurementId == null
    ? null
    : measurements.find((measurement) => measurement.id === editingMeasurementId) ?? null;
  const canShowMeasurementForm = measurementEditorMode === 'create'
    || (isMeasurementEditorOpen && Boolean(editingMeasurement?.can_edit));
  const isMeasurementFormLocked = editingMeasurementId != null
    && !editingMeasurement?.can_edit;
  // The draft is compared with the persisted measurement through the same
  // normalization the save payload applies, so an untouched form cannot be
  // saved again. This is frontend protection only: the server still decides
  // whether the measurement may be edited at all.
  const persistedMeasurementPayload = editingMeasurement
    ? buildMeasurementPayload(getMeasurementFormValues(editingMeasurement))
    : null;
  const draftMeasurementPayload = parsePositiveInteger(form.polypCount) != null
    && parsePositiveInteger(form.ephyraeCount) != null ? buildMeasurementPayload(form) : null;
  const isMeasurementDraftUnchanged = persistedMeasurementPayload != null && draftMeasurementPayload != null
    && isMeasurementPayloadUnchanged(persistedMeasurementPayload, draftMeasurementPayload);
  const showWeeklyMeasurementSummary = Boolean(weeklyMeasurement)
    && (!isMeasurementEditorOpen || !editingMeasurement?.can_edit);
  const isMeasurementEditorExpanded = Boolean(weeklyMeasurement)
    && isMeasurementEditorOpen
    && Boolean(editingMeasurement?.can_edit);

  useEffect(() => {
    const refreshDate = () => setMeasurementReferenceDate(getTodayDateValue());
    window.addEventListener('focus', refreshDate);
    const interval = window.setInterval(refreshDate, 60_000);
    return () => {
      window.removeEventListener('focus', refreshDate);
      window.clearInterval(interval);
    };
  }, []);

  useEffect(() => {
    if (!box || !weeklyMeasurement?.can_edit || !weeklyMeasurement.edit_deadline) {
      return;
    }
    const delay = Date.parse(weeklyMeasurement.edit_deadline) - Date.now() + 100;
    const timeout = window.setTimeout(
      () => void onRefreshMeasurementState(box.id).catch(() => {
        // A failed capability refresh must not produce an unhandled rejection.
      }),
      Math.max(0, delay),
    );
    return () => window.clearTimeout(timeout);
  }, [box?.id, weeklyMeasurement?.id, weeklyMeasurement?.can_edit, weeklyMeasurement?.edit_deadline]);

  useEffect(() => {
    setForm(getInitialMeasurementForm(defaultSalinity));
    setIsHistoryOpen(false);
    setLineageGraph(null);
    setIsLineageGraphLoading(false);
    setLineageGraphError(null);
    setIsMoveOpen(false);
    setIsSavingMove(false);
    setIsChangingBoxStatus(false);
    setLifecycleAction(null);
    setMoveError(null);
    setStatusError(null);
    setIsSubcultureOpen(false);
    setIsQrLabelOpen(false);
    setSaveError(null);
    setEditingMeasurementId(null);
    setIsMeasurementEditorOpen(false);
    setMeasurementReferenceDate(getTodayDateValue());
    setSubcultureError(null);
    setSubcultureSuccess(null);
    setActiveInsightTab('measurements');
    setIsCorrectingFromHistory(false);
  }, [box?.id]);

  useEffect(() => {
    if (isCorrectingFromHistory) return;

    if (!isMeasurementEditorOpen) {
      setEditingMeasurementId(null);
      if (measurementEditorMode === 'create') {
        setForm(getInitialMeasurementForm(defaultSalinity, measurementReferenceDate));
      }
      return;
    }

    if (weeklyMeasurement?.can_edit) {
      setForm(getMeasurementFormValues(weeklyMeasurement));
      setEditingMeasurementId(weeklyMeasurement.id);
      return;
    }

    setEditingMeasurementId(null);
    setIsMeasurementEditorOpen(false);
  }, [
    box?.id,
    defaultSalinity,
    isCorrectingFromHistory,
    isMeasurementEditorOpen,
    measurementEditorMode,
    measurementReferenceDate,
    weeklyMeasurement?.id,
    weeklyMeasurement?.can_edit,
  ]);

  useEffect(() => {
    if (!isMeasurementEditorOpen || editingMeasurementId == null) return;
    if (editingMeasurement?.can_edit) return;

    if (editingMeasurement) {
      setForm(getMeasurementFormValues(editingMeasurement));
    }
    setEditingMeasurementId(null);
    setIsCorrectingFromHistory(false);
    setIsMeasurementEditorOpen(false);
  }, [editingMeasurement?.id, editingMeasurement?.can_edit, editingMeasurementId, isMeasurementEditorOpen]);

  // The history sends the user here to correct a measurement: fill the form
  // with what was recorded. Declared after the reset above so it runs last and
  // its values survive the box change. Keeping the original date matters: the
  // API stores one measurement per box and week, so saving corrects that
  // measurement instead of adding a second one for the week.
  useEffect(() => {
    if (!measurementPrefill || !box || measurementPrefill.box_id !== box.id) return;
    // The journal carries the values, but only the box sheet's own history
    // carries the server-computed correction capability. Wait for it instead of
    // dropping the correction while the detail is still loading.
    if (!('biological_measurements' in box)) return;

    const target = measurements.find(
      (measurement) => measurement.id === measurementPrefill.id,
    );
    onMeasurementPrefillConsumed();
    if (!target?.can_edit) return;

    setMeasurementReferenceDate(target.measured_on);
    setForm(getMeasurementFormValues(target));
    setEditingMeasurementId(target.id);
    setIsCorrectingFromHistory(true);
    setIsMeasurementEditorOpen(true);
  }, [measurementPrefill, box, measurements]);

  // The zones can finish loading after the sheet is open, and the box can be
  // moved to another zone: seed the salinity once its control value is known.
  // Only an untouched field is filled, so this never overwrites a reading the
  // technician typed, nor the value loaded when correcting a past measurement.
  useEffect(() => {
    if (editingMeasurementId != null || !defaultSalinity) return;
    setForm((current) => (current.salinity ? current : { ...current, salinity: defaultSalinity }));
  }, [defaultSalinity, editingMeasurementId]);

  useEffect(() => {
    if (activeInsightTab !== 'lineage' || !box?.id || lineageGraph || lineageGraphError) return;
    void handleLoadLineageGraph();
  }, [activeInsightTab, box?.id, lineageGraph, lineageGraphError]);

  if (isLoading) {
    return (
      <PageLoader variant="box" label={t('boxSheet')} />
    );
  }

  if (!box) {
    return (
      <section className="box-page empty-box-state">
        <DetailBackButton label={t('back')} onBack={onBack} />
        <h2>{t('boxNotFound')}</h2>
        <p>{t('boxNotFoundText')}</p>
      </section>
    );
  }

  const lastComment = getLatestComment(measurements, box);
  const summaryComment = weeklyMeasurement?.notes?.trim() || lastComment;
  const qr = 'qr_image_url' in box
    ? { imageUrl: getBoxQrImageUrl(box), scanUrl: getBoxScanUrl(box) }
    : null;
  const lineage = getBoxLineage(box);
  const currentZone = getCurrentThermalZone(box, zones);
  const displayDate = getBoxDisplayDate(box, measurements);
  const statusPresentation = getBoxStatusPresentation(box.status, language);
  const canChangeBoxStatus = userCanArchiveBox(profile, box.organization.id);
  const canShowStatusButton = canChangeBoxStatus && ['active', 'inactive'].includes(box.status);
  type BoxAction = 'qr' | 'move' | 'subculture' | 'tracking';
  const boxActions: RowActionMenuItem<BoxAction>[] = [
    ...(qr && canWriteLabData ? [{ action: 'qr' as const, label: t('qrLabelTitle') }] : []),
    ...(canWriteLabData ? [
      { action: 'move' as const, label: t('moveAction') },
      ...(isBoxActive ? [{ action: 'subculture' as const, label: t('subcultureAction') }] : []),
    ] : []),
    ...(canShowStatusButton ? [{
      action: 'tracking' as const,
      label: isChangingBoxStatus ? t('saving') : t(isBoxActive ? 'boxArchiveAction' : 'boxActivateAction'),
      disabled: isChangingBoxStatus,
    }] : []),
  ];
  function dispatchBoxAction(action: BoxAction) {
    const item = boxActions.find((candidate) => candidate.action === action);
    if (!item || item.disabled) return;
    if (action === 'qr') setIsQrLabelOpen(true);
    else if (action === 'move') setIsMoveOpen(true);
    else if (action === 'subculture') {
      setSubcultureError(null);
      setIsSubcultureOpen(true);
    }
    else {
      setStatusError(null);
      setLifecycleAction(isBoxActive ? 'deactivate' : 'reactivate');
    }
  }

  async function saveMeasurement(): Promise<boolean> {
    if (!box || isSaving) return false;

    const targetMeasurement = editingMeasurementId == null
      ? null
      : measurements.find((measurement) => measurement.id === editingMeasurementId) ?? null;
    if (
      (targetMeasurement && !targetMeasurement.can_edit)
      || (!targetMeasurement && measurementEditorMode !== 'create')
    ) {
      return false;
    }

    if (editingMeasurementId != null && isMeasurementDraftUnchanged) return false;

    if (!form.polypCount.trim() || !form.ephyraeCount.trim()) {
      setSaveError(t('measurementCountsRequired'));
      return false;
    }

    if (parsePositiveInteger(form.polypCount) == null || parsePositiveInteger(form.ephyraeCount) == null) {
      setSaveError(t('measurementCountsInvalid'));
      return false;
    }

    setIsSaving(true);
    setSaveError(null);

    const payload = buildMeasurementPayload(form);

    try {
      if (editingMeasurementId != null) {
        await onUpdateMeasurement(box.id, editingMeasurementId, payload);
      } else {
        await onCreateMeasurement(box.id, payload);
      }
      if (!operationLifetimeRef.current || !isOperationCurrent()) return false;
      setEditingMeasurementId(null);
      setIsCorrectingFromHistory(false);
      setIsMeasurementEditorOpen(false);
      triggerHaptic([12, 28, 12]);
      return true;
    } catch (requestError) {
      if (!operationLifetimeRef.current || !isOperationCurrent() || requestError instanceof ApiResourceCancelledError) return false;
      setSaveError(getMeasurementSaveError(requestError, t));
      return false;
    } finally {
      if (operationLifetimeRef.current && isOperationCurrent()) setIsSaving(false);
    }
  }


  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!isDesktopApp) return;
    void saveMeasurement();
  }

  function openWeeklyMeasurementEditor() {
    if (!weeklyMeasurement?.can_edit) return;
    setForm(getMeasurementFormValues(weeklyMeasurement));
    setEditingMeasurementId(weeklyMeasurement.id);
    setIsCorrectingFromHistory(false);
    setSaveError(null);
    setIsMeasurementEditorOpen(true);
  }

  function cancelMeasurementEdit() {
    if (editingMeasurement) {
      setForm(getMeasurementFormValues(editingMeasurement));
    }
    setEditingMeasurementId(null);
    setIsCorrectingFromHistory(false);
    setSaveError(null);
    setIsMeasurementEditorOpen(false);
  }

  async function handleSubculture(payload: SubculturePayload) {
    if (!box || isSavingSubculture) return;
    const confirmed = await confirmAction({
      title: t('confirmSubcultureTitle'),
      message: t('confirmSubcultureMessage'),
      confirmLabel: t('confirmSubcultureAction'),
      cancelLabel: t('confirmCancel'),
      variant: 'warning',
      details: [
        { label: t('confirmDetailParentBox'), value: box.global_code },
        { label: t('confirmDetailSpecies'), value: box.species.scientific_name },
        { label: t('confirmDetailChildren'), value: payload.children.length },
      ],
    });
    if (!confirmed || !operationLifetimeRef.current || !isOperationCurrent()) return;

    setIsSavingSubculture(true);
    setSubcultureError(null);

    let result: SubcultureResult;
    try {
      result = await onCreateSubculture(box.id, payload);
      if (!operationLifetimeRef.current || !isOperationCurrent()) return;
      setSubcultureSuccess(result);
      setIsSubcultureOpen(false);
    } catch (requestError) {
      if (!operationLifetimeRef.current || !isOperationCurrent() || requestError instanceof ApiResourceCancelledError) return;
      setSubcultureError(getSubcultureSaveError(requestError, t));
      return;
    } finally {
      if (operationLifetimeRef.current && isOperationCurrent()) setIsSavingSubculture(false);
    }

    // Subculture is already committed; the optional lifecycle operation is separate.
    if (result.parent_polyp_count_after === 0 && result.allocated_polyp_count !== null
        && result.allocations.length > 0 && result.allocations.every((allocation) => allocation.allocated_polyps !== null)
        && canChangeBoxStatus) {
      const deactivate = await confirmAction({
        title: t('subcultureDeactivateParentTitle'),
        message: t('subcultureDeactivateParentMessage'),
        confirmLabel: t('boxArchiveAction'),
        cancelLabel: t('subcultureKeepParentActive'),
        details: [{ label: t('confirmDetailParentBox'), value: box.global_code }],
      });
      if (deactivate && operationLifetimeRef.current && isOperationCurrent()) {
        setStatusError(null);
        setLifecycleAction('deactivate');
      }
    }
  }

  async function handleMove(payload: BoxMovePayload) {
    if (!box || isSavingMove) return;
    const targetZone = zones.find((zone) => zone.id === payload.thermal_zone_id) ?? null;
    const confirmed = await confirmAction({
      title: t('confirmMoveTitle'),
      message: t('confirmMoveMessage'),
      confirmLabel: t('confirmMoveAction'),
      cancelLabel: t('confirmCancel'),
      details: [
        { label: t('confirmDetailBox'), value: box.global_code },
        { label: t('confirmDetailCurrentLocation'), value: currentZone?.name ?? '-' },
        { label: t('confirmDetailTargetLocation'), value: targetZone?.name ?? '-' },
      ],
    });
    if (!confirmed || !operationLifetimeRef.current || !isOperationCurrent()) return;

    setIsSavingMove(true);
    setMoveError(null);

    try {
      await onMoveBox(box.id, payload);
      if (!operationLifetimeRef.current || !isOperationCurrent()) return;
      setIsMoveOpen(false);
    } catch (requestError) {
      if (!operationLifetimeRef.current || !isOperationCurrent() || requestError instanceof ApiResourceCancelledError) return;
      setMoveError(getMoveSaveError(requestError, t));
    } finally {
      if (operationLifetimeRef.current && isOperationCurrent()) setIsSavingMove(false);
    }
  }

  async function handleLifecycleSubmit(submission: BoxLifecycleSubmission) {
    if (!box || isChangingBoxStatus) return;
    setIsChangingBoxStatus(true);
    setStatusError(null);

    try {
      if (submission.action === 'reactivate') {
        await onReactivateBox(box.id, submission.payload);
      } else if (submission.action === 'deactivate') {
        await onDeactivateBox(box.id, submission.payload);
      }
      if (!operationLifetimeRef.current || !isOperationCurrent()) return;
      setLifecycleAction(null);
    } catch (requestError) {
      if (!operationLifetimeRef.current || !isOperationCurrent() || requestError instanceof ApiResourceCancelledError) return;
      if (requestError instanceof ApiError && requestError.status === 403) {
        setStatusError(t(submission.action === 'reactivate' ? 'boxActivateForbidden' : 'boxArchiveForbidden'));
      } else {
        setStatusError(getErrorMessage(requestError));
      }
    } finally {
      if (operationLifetimeRef.current && isOperationCurrent()) setIsChangingBoxStatus(false);
    }
  }

  async function handleLoadLineageGraph() {
    if (!box || lineageRequestPendingRef.current) return;
    const generation = ++lineageRequestGenerationRef.current;
    const isCurrent = () => generation === lineageRequestGenerationRef.current && isOperationCurrent();
    lineageRequestPendingRef.current = true;
    setLineageGraph(null);
    setIsLineageGraphLoading(true);
    setLineageGraphError(null);

    try {
      const graph = await onLoadLineageGraph(box.id);
      if (isCurrent()) setLineageGraph(graph);
    } catch (requestError) {
      if (isCurrent() && !(requestError instanceof ApiResourceCancelledError)) {
        setLineageGraphError(getErrorMessage(requestError));
      }
    } finally {
      if (isCurrent()) {
        lineageRequestPendingRef.current = false;
        setIsLineageGraphLoading(false);
      }
    }
  }

  return (
    <section className={canWriteLabData ? 'box-page' : 'box-page is-read-only'}>
      {!isDesktopApp ? <DetailBackButton label={t('back')} onBack={onBack} /> : null}

      <header className={`entity-header entity-header--box box-sheet-hero is-status-${statusPresentation.tone}${isTabletLayout ? ' is-tablet' : isPhoneLayout ? ' is-phone' : ''}`}>
        <div className="entity-header__identity box-sheet-identity">
          <div>
            <p className="box-page-label">{t('boxSheet')}</p>
            <div className="box-code-line">
              <h2 data-box-page-focus-target tabIndex={-1}>{box.global_code}</h2>
            </div>
            <p className="box-species-name">{box.species.scientific_name}</p>
          </div>

          {isDesktopApp ? (
            <div className="box-small-facts">
              <InfoPill
                label={t(displayDate.labelKey)}
                value={displayDate.date ? formatDisplayDate(displayDate.date) : t('noDate')}
              />
            </div>
          ) : null}
        </div>

        <div className="box-header-tools">
          {!isPhoneLayout && qr && canWriteLabData ? (
            <button
              className="box-hero-qr"
              type="button"
              aria-label={`${t('qrLabelTitle')} ${box.global_code}`}
              title={qr.scanUrl}
              onClick={() => dispatchBoxAction('qr')}
            >
              <QrLabel
                altLabel={t('qrCode')}
                item={buildQrLabelItem(box, qr.imageUrl)}
                showMetadata={false}
                variant="trigger"
              />
            </button>
          ) : null}
          {isTabletLayout ? (
            <div className="box-tablet-actions">
              {boxActions.filter((item) => item.action !== 'qr').map((item) => (
                <button
                  key={item.action}
                  className={`icon-button box-compact-action${item.action === 'move' || item.action === 'subculture' ? ' box-compact-action--labeled' : ''}`}
                  type="button"
                  aria-label={item.label}
                  title={item.label}
                  disabled={item.disabled}
                  onClick={() => dispatchBoxAction(item.action)}
                >
                  {item.action === 'move' ? (
                    <>
                      <Route aria-hidden="true" size={20} />
                      <span>{item.label}</span>
                    </>
                  ) : item.action === 'subculture' ? (
                    <>
                      <GitFork className="box-subculture-glyph" aria-hidden="true" size={20} />
                      <span>{item.label}</span>
                    </>
                  )
                      : isBoxActive ? <CirclePause aria-hidden="true" size={36} />
                        : <CirclePlay aria-hidden="true" size={36} />}
                </button>
              ))}
            </div>
          ) : null}
          {isPhoneLayout && boxActions.length > 0 ? (
            <RowActionMenu actions={boxActions} onAction={dispatchBoxAction} ariaLabel={`${t('boxInventoryActions')} ${box.global_code}`} />
          ) : null}
        </div>

        <div className="entity-header__summary box-zone-summary">
          <div className={isDesktopApp ? 'box-summary-metadata' : 'box-summary-metadata is-compact'}>
            {!isDesktopApp ? (
              <InfoPill
                label={t(displayDate.labelKey)}
                value={displayDate.date ? formatDisplayDate(displayDate.date) : t('noDate')}
              />
            ) : null}
          {box.thermal_zone ? (
            <button
              className="info-pill is-strong box-zone-link"
              type="button"
              onClick={() => onOpenZone(box.thermal_zone!.id)}
            >
              <small>{t('zones')}</small>
              <strong>{box.thermal_zone.name}</strong>
            </button>
          ) : (
            <InfoPill label={t('zones')} value={t('noZone')} strong />
          )}
          </div>
          <InfoPill label={t('zoneSalinityShort')} value={formatSalinity(currentZone?.salinity_psu)} />
          {/* Salinity recorded for this box (the last measurement's PSU), shown
              right after the zone reference so both are read side by side. */}
          <InfoPill label={t('boxSalinityShort')} value={formatSalinity(box.latest_salinity_psu)} />
          <InfoPill label={t('temperatureShort')} value={formatTemperature(currentZone?.latest_temperature?.average_temperature_c)} />
        </div>

        {isDesktopApp ? (
          <div className="entity-header__actions box-action-stack">
            {boxActions.filter((item) => item.action !== 'qr').map((item) => (
              <button
                key={item.action}
                className={item.action === 'tracking'
                  ? isBoxActive ? 'archive-box-trigger' : 'activate-box-trigger'
                  : `${item.action}-trigger`}
                type="button"
                disabled={item.disabled}
                onClick={() => dispatchBoxAction(item.action)}
              >
                {item.action === 'tracking' ? (
                  <span className="button-icon-label">
                    {!isChangingBoxStatus ? (
                      <PolypbaseIcon name={isBoxActive ? 'archive' : 'restore'} size={17} />
                    ) : null}
                    {item.label}
                  </span>
                ) : item.label}
              </button>
            ))}
          </div>
        ) : null}
      </header>

      {statusError ? (
        <p className="inline-error box-action-feedback">{statusError}</p>
      ) : null}

      <div className={`box-page-grid${!isBoxActive ? ' is-inactive' : ''}${weeklyMeasurement ? ' has-measurement-module' : ''}`}>
        {(showWeeklyMeasurementSummary || (!weeklyMeasurement && isBoxActive)) ? (
        <section
          className={`last-reading-card${showWeeklyMeasurementSummary ? ' measurement-summary measurement-module' : ''}${showWeeklyMeasurementSummary && weeklyMeasurement?.can_edit ? ' has-edit-capability' : ''}`}
        >
          <div>
            <h2>{t('lastMeasurement')}</h2>
            <span>
              {weeklyMeasurement && showWeeklyMeasurementSummary
                ? formatDisplayDate(weeklyMeasurement.measured_on)
                : box.latest_measurement
                  ? formatDisplayDate(box.latest_measurement.measured_on)
                  : t('noDate')}
            </span>
          </div>
          <Metric
            label={t('polyps')}
            value={formatMeasurementCount(
              weeklyMeasurement && showWeeklyMeasurementSummary
                ? weeklyMeasurement.polyp_count
                : box.latest_measurement?.polyp_count,
            )}
          />
          <Metric
            label={t('ephyraeFull')}
            value={formatMeasurementCount(
              weeklyMeasurement && showWeeklyMeasurementSummary
                ? weeklyMeasurement.ephyrae_count
                : box.latest_measurement?.ephyrae_count,
            )}
          />

          <div className="last-reading-comment">
            <div className="last-reading-comment-header">
              <small>{t('lastComment')}</small>
              {showWeeklyMeasurementSummary && weeklyMeasurement && !weeklyMeasurement.can_edit ? (
                <p className="measurement-lock-note">
                  {weeklyMeasurement.edit_restriction === 'edit_window_expired'
                    ? t('weeklyMeasurementWindowExpired')
                    : t('weeklyMeasurementReadOnly')}
                </p>
              ) : null}
            </div>
            {showWeeklyMeasurementSummary ? (
              summaryComment ? <p>{summaryComment}</p> : null
            ) : (
              <p>{lastComment || t('noComment')}</p>
            )}
          </div>
          {showWeeklyMeasurementSummary && weeklyMeasurement?.can_edit ? (
            <button
              className="icon-button measurement-summary-edit-button"
              type="button"
              aria-label={t('modifyWeeklyMeasurement')}
              title={t('modifyWeeklyMeasurement')}
              onClick={openWeeklyMeasurementEditor}
            >
              <Pencil aria-hidden="true" size={18} />
            </button>
          ) : null}
        </section>
        ) : null}

        {canShowMeasurementForm ? (
          <section className={`box-section measurement-form-section${isMeasurementEditorExpanded ? ' measurement-module is-expanded' : ''}${isMeasurementFormLocked ? ' is-locked' : ''}`}>
            <form className="fake-form" onSubmit={handleSubmit}>
              <fieldset
                className="measurement-editor-fields"
                disabled={isMeasurementFormLocked}
                aria-describedby={isMeasurementFormLocked ? `measurement-lock-${box.id}` : undefined}
              >
              <div className="section-title">
                <div>
                  <h2>
                    {t(
                      editingMeasurementId != null
                        ? isCorrectingFromHistory
                          ? 'correctMeasurement'
                          : 'modifyWeeklyMeasurement'
                        : 'newMeasurement',
                    )}
                  </h2>
                  {isMeasurementFormLocked && weeklyMeasurement ? (
                    <p className="measurement-lock-note" id={`measurement-lock-${box.id}`}>
                      {weeklyMeasurement.edit_restriction === 'edit_window_expired'
                        ? t('weeklyMeasurementWindowExpired')
                        : t('weeklyMeasurementReadOnly')}
                    </p>
                  ) : null}
                </div>
                <span>{formatDisplayDate(form.measuredOn)}</span>
              </div>

              <div className="measurement-entry-grid">
                <label className="measurement-date-field">
                  {t('measurementDate')}
                  <input
                    disabled={editingMeasurementId != null}
                    required
                    type="date"
                    value={form.measuredOn}
                    onChange={(event) => setForm((current) => ({ ...current, measuredOn: event.target.value }))}
                  />
                </label>

                <div className="measurement-count-field measurement-polyp-field">
                  <label className="measurement-field-label" htmlFor="measurement-polyps">{t('polyps')}</label>
                  <div className="count-stepper">
                    <StepperButton
                      aria-label={`${t('polyps')} -1`}
                      onStep={() => setForm((current) => ({
                        ...current,
                        polypCount: decrementCountValue(current.polypCount),
                      }))}
                    >
                      <PolypbaseIcon name="minus" size={18} />
                    </StepperButton>
                    <input
                      min="0"
                      required
                      inputMode="numeric"
                      placeholder="0"
                      type="number"
                      id="measurement-polyps"
                      value={form.polypCount}
                      onChange={(event) => setForm((current) => ({ ...current, polypCount: event.target.value }))}
                    />
                    <StepperButton
                      aria-label={`${t('polyps')} +1`}
                      onStep={() => setForm((current) => ({
                        ...current,
                        polypCount: incrementCountValue(current.polypCount, 1),
                      }))}
                    >
                      <PolypbaseIcon name="plus" size={18} />
                    </StepperButton>
                  </div>
                  <QuickCountButtons
                    values={[50, 100]}
                    onAdd={(value) => setForm((current) => ({
                      ...current,
                      polypCount: incrementCountValue(current.polypCount, value),
                    }))}
                  />
                </div>

                <div className="measurement-count-field measurement-ephyrae-field">
                  <label className="measurement-field-label" htmlFor="measurement-ephyrae">{t('ephyraeFull')}</label>
                  <div className="count-stepper">
                    <StepperButton
                      aria-label={`${t('ephyraeFull')} -1`}
                      onStep={() => setForm((current) => ({
                        ...current,
                        ephyraeCount: decrementCountValue(current.ephyraeCount),
                      }))}
                    >
                      <PolypbaseIcon name="minus" size={18} />
                    </StepperButton>
                    <input
                      min="0"
                      required
                      inputMode="numeric"
                      placeholder="0"
                      type="number"
                      id="measurement-ephyrae"
                      value={form.ephyraeCount}
                      onChange={(event) => setForm((current) => ({ ...current, ephyraeCount: event.target.value }))}
                    />
                    <StepperButton
                      aria-label={`${t('ephyraeFull')} +1`}
                      onStep={() => setForm((current) => ({
                        ...current,
                        ephyraeCount: incrementCountValue(current.ephyraeCount, 1),
                      }))}
                    >
                      <PolypbaseIcon name="plus" size={18} />
                    </StepperButton>
                  </div>
                  <QuickCountButtons
                    values={[10, 25]}
                    onAdd={(value) => setForm((current) => ({
                      ...current,
                      ephyraeCount: incrementCountValue(current.ephyraeCount, value),
                    }))}
                  />
                </div>

                <div className="measurement-salinity-field">
                  <label className="measurement-field-label" htmlFor="measurement-salinity">{t('salinityFull')}</label>
                  <div className="count-stepper count-stepper-salinity">
                    <StepperButton
                      aria-label={`${t('salinityFull')} -${SALINITY_STEP.toLocaleString(language)}`}
                      onStep={() => setForm((current) => ({
                        ...current,
                        salinity: stepBiologicalSalinity(current.salinity, -SALINITY_STEP),
                      }))}
                    >
                      <PolypbaseIcon name="minus" size={18} />
                    </StepperButton>

                    <input
                      min="0"
                      inputMode="decimal"
                      step="0.01"
                      placeholder={String(SALINITY_STEP)}
                      type="number"
                      id="measurement-salinity"
                      value={form.salinity}
                      onChange={(event) => setForm((current) => ({ ...current, salinity: event.target.value }))}
                    />
                    <StepperButton
                      aria-label={`${t('salinityFull')} +${SALINITY_STEP.toLocaleString(language)}`}
                      onStep={() => setForm((current) => ({
                        ...current,
                        salinity: stepBiologicalSalinity(current.salinity, SALINITY_STEP),
                      }))}
                    >
                      <PolypbaseIcon name="plus" size={18} />
                    </StepperButton>
                  </div>
                  <QuickCountButtons
                    values={[1, 5]}
                    getAccessibleLabel={(value) => `${t('salinityFull')} +${value.toLocaleString(language)} PSU`}
                    onAdd={(value) => setForm((current) => ({
                      ...current,
                      salinity: stepBiologicalSalinity(current.salinity, value),
                    }))}
                  />
                </div>
              </div>

              <label className="notes-field">
                <span className="measurement-field-label">{t('observation')}</span>
                <textarea
                  placeholder={t('observationPlaceholder')}
                  rows={3}
                  value={form.notes}
                  onChange={(event) => setForm((current) => ({ ...current, notes: event.target.value }))}
                />
              </label>

              {saveError ? <p className="inline-error form-feedback">{saveError}</p> : null}

              <div className="measurement-actions-row">
                <MeasurementSaveButton
                  isDesktop={isDesktopApp}
                  isDisabled={isMeasurementFormLocked || isMeasurementDraftUnchanged}
                  isSaving={isSaving}
                  labels={{
                    hold: editingMeasurementId != null ? t('holdToUpdate') : t('holdToSave'),
                    save: editingMeasurementId != null
                      ? t('saveMeasurementEdit')
                      : t('saveMeasurement'),
                    saving: t('saving'),
                  }}
                  onSave={saveMeasurement}
                />
                {editingMeasurementId != null ? (
                  <button
                    className="secondary-button measurement-cancel-button"
                    type="button"
                    disabled={isSaving}
                    onClick={cancelMeasurementEdit}
                  >
                    {t('cancelEdit')}
                  </button>
                ) : null}
              </div>
              </fieldset>
            </form>
          </section>
        ) : null}

        {subcultureSuccess ? (
          <p className="subculture-success" role="status">
            <strong>{t('subcultureCompleted')}</strong>
            {typeof subcultureSuccess.allocated_polyp_count === 'number' && typeof subcultureSuccess.parent_polyp_count_after === 'number'
                && subcultureSuccess.allocations.length > 0 && subcultureSuccess.allocations.every((allocation) => allocation.allocated_polyps !== null) ? (
              <>{' '}{subcultureSuccess.parent_polyp_count_before} → {subcultureSuccess.parent_polyp_count_after} {t('polyps').toLocaleLowerCase()}</>
            ) : null}
            {' — '}{subcultureSuccess.children.map((child) => child.global_code).join(', ')}
          </p>
        ) : null}

        <section className="box-insights-section">
          <BoxInsights
            activeTab={activeInsightTab}
            graph={lineageGraph}
            graphError={lineageGraphError}
            isGraphLoading={isLineageGraphLoading}
            labels={getBoxInsightsLabels(t)}
            language={language}
            lineage={lineage}
            locations={'locations' in box ? box.locations : []}
            measurements={measurements}
            biologicalTimeline={'biological_timeline' in box ? box.biological_timeline : undefined}
            movements={getBoxMovements(box)}
            onLoadLineageGraph={handleLoadLineageGraph}
            onOpenHistory={() => setIsHistoryOpen(true)}
            onSelectBox={onOpenBox}
            onSelectTab={setActiveInsightTab}
          />
        </section>

        {isHistoryOpen ? (
          <MeasurementHistoryModal
            boxCode={box.global_code}
            labels={getBoxInsightsLabels(t)}
            language={language}
            measurements={measurements}
            biologicalTimeline={'biological_timeline' in box ? box.biological_timeline : undefined}
            onClose={() => setIsHistoryOpen(false)}
          />
        ) : null}

        {isMoveOpen ? (
          <MoveBoxModal
            box={box}
            zones={zones}
            language={language}
            isSaving={isSavingMove}
            error={moveError}
            onClose={() => setIsMoveOpen(false)}
            onSubmit={handleMove}
          />
        ) : null}

        {isSubcultureOpen ? (
          <SubcultureModal
            box={box}
            zones={zones}
            language={language}
            isSaving={isSavingSubculture}
            error={subcultureError}
            onClose={() => setIsSubcultureOpen(false)}
            onSubmit={handleSubculture}
          />
        ) : null}

        {lifecycleAction && (lifecycleAction === 'deactivate' || lifecycleAction === 'reactivate') ? (
          <BoxLifecycleModal
            action={lifecycleAction}
            box={box}
            error={statusError}
            isSaving={isChangingBoxStatus}
            onClose={() => {
              if (!isChangingBoxStatus) setLifecycleAction(null);
            }}
            onSubmit={handleLifecycleSubmit}
            t={t}
            zones={zones}
          />
        ) : null}

        {isQrLabelOpen && qr ? (
          <QrLabelModal
            box={box}
            labels={{
              addToSelection: t('qrLabelAddToSelection'),
              alreadySelected: t('qrLabelAlreadySelected'),
              close: t('close'),
              download: t('qrLabelDownload'),
              qrLabelPreparing: t('qrLabelPreparing'),
              qrLabelPopupBlocked: t('qrLabelPopupBlocked'),
              qrLabelQrUnavailable: t('qrLabelQrUnavailable'),
              qrLabelResourceUnavailable: t('qrLabelResourceUnavailable'),
              qrLabelImagePreparationFailed: t('qrLabelImagePreparationFailed'),
              qrLabelPreparationFailed: t('qrLabelPreparationFailed'),
              qrLabelRetry: t('qrLabelRetry'),
              print: t('print'),
              qrCode: t('qrCode'),
              selectionCount: t('qrLabelSelectionCount'),
              title: t('qrLabelTitle'),
              viewSelection: t('qrLabelViewSelection'),
            }}
            qrImageUrl={qr.imageUrl}
            selectedLabels={qrLabelSelection}
            onAddToSelection={onAddQrLabel}
            onClose={() => setIsQrLabelOpen(false)}
            onViewSelection={() => {
              setIsQrLabelOpen(false);
              onOpenQrLabelSelection();
            }}
          />
        ) : null}
      </div>
    </section>
  );
}

function StepperButton({
  'aria-label': ariaLabel,
  children,
  onStep,
}: {
  'aria-label': string;
  children: ReactNode;
  onStep: () => void;
}) {
  const delayRef = useRef<number | null>(null);
  const intervalRef = useRef<number | null>(null);
  // The pressed look belongs to this button instance alone, so pressing one
  // control can never mark another one pressed, including the same symbol in
  // another field. It clears as soon as this button is released.
  const [isPressed, setIsPressed] = useState(false);

  function clearRepeat() {
    if (delayRef.current != null) {
      window.clearTimeout(delayRef.current);
      delayRef.current = null;
    }

    if (intervalRef.current != null) {
      window.clearInterval(intervalRef.current);
      intervalRef.current = null;
    }

    setIsPressed(false);
  }

  useEffect(() => clearRepeat, []);

  function startRepeat(event: PointerEvent<HTMLButtonElement>) {
    event.preventDefault();
    // Preventing native pointer behavior must not leave focus on a previous control.
    event.currentTarget.focus({ preventScroll: true });
    clearRepeat();
    setIsPressed(true);
    onStep();

    delayRef.current = window.setTimeout(() => {
      intervalRef.current = window.setInterval(onStep, 95);
    }, 340);
  }

  function handleKeyboard(event: KeyboardEvent<HTMLButtonElement>) {
    if ((event.key === 'Enter' || event.key === ' ') && !event.repeat) {
      event.preventDefault();
      onStep();
    }
  }

  return (
    <button
      type="button"
      className={isPressed ? 'count-stepper-button is-pressed' : 'count-stepper-button'}
      aria-label={ariaLabel}
      onPointerDown={startRepeat}
      onPointerUp={clearRepeat}
      onPointerLeave={clearRepeat}
      onPointerCancel={clearRepeat}
      onBlur={clearRepeat}
      onKeyDown={handleKeyboard}
      onContextMenu={(event) => event.preventDefault()}
    >
      {children}
    </button>
  );
}

function InfoPill({ label, value, strong = false }: { label: string; value: string; strong?: boolean }) {
  return (
    <span className={strong ? 'info-pill is-strong' : 'info-pill'}>
      <small>{label}</small>
      <strong>{value}</strong>
    </span>
  );
}

function getBoxInsightsLabels(t: TFunction) {
  return {
    chartEmpty: t('chartEmpty'),
    chartTitle: t('chartTitle'),
    children: t('children'),
    close: t('close'),
    ephyraeFull: t('ephyraeFull'),
    events: t('events'),
    historyButton: t('historyButton'),
    historyAllYears: t('historyAllYears'),
    historyVisibleCount: (visible: number, total: number) => t('historyVisibleCount')
      .replace('{visible}', String(visible)).replace('{total}', String(total)),
    historyYearFilter: t('historyYearFilter'),
    historyEnteredBy: t('historyEnteredBy'),
    historyHideComment: t('historyHideComment'),
    historyObservation: t('historyObservation'),
    historyReadComment: t('historyReadComment'),
    historyShowMore: t('historyShowMore'),
    historyYear: t('historyYear'),
    lineageEmptyGraph: t('lineageEmptyGraph'),
    lineageLoading: t('lineageLoading'),
    lineageRetry: t('lineageRetry'),
    lineageTab: t('analysisTabLineage'),
    measurementHistory: t('measurementHistory'),
    measurementsTab: t('analysisTabMeasurements'),
    missingReading: t('chartMissingReading'),
    missingReadingRange: t('chartMissingReading'),
    movementEvent: t('movementEvent'),
    movedTo: t('movedTo'),
    movementHistoryTitle: t('movementHistoryTitle'),
    movementsTab: t('analysisTabMovements'),
    noComment: t('noComment'),
    noMeasurementHistory: t('noMeasurementHistory'),
    noMovementHistory: t('noMovementHistory'),
    oneMonth: t('oneMonth'),
    oneYear: t('oneYear'),
    parents: t('parents'),
    polyps: t('polyps'),
    salinityFull: t('salinityFull'),
    sixMonths: t('sixMonths'),
    subcultureEvent: t('subcultureEvent'),
    temperature: t('temperature'),
    temperatureNoData: t('temperatureNoData'),
    threeMonths: t('threeMonths'),
  };
}

function getProfileLabels(t: TFunction) {
  return {
    account: t('account'),
    logoutAction: t('logoutAction'),
    logoutError: t('logoutError'),
    profileEmail: t('profileEmail'),
    profileLanguage: t('profileLanguage'),
    profileAdminAction: t('profileAdminAction'),
    profileMemberships: t('profileMemberships'),
    profileNoEmail: t('profileNoEmail'),
    profileNoMembership: t('profileNoMembership'),
    profileAllOrganizationsAccess: t('profileAllOrganizationsAccess'),
    profilePreferences: t('profilePreferences'),
    profileActiveOrganization: t('profileActiveOrganization'),
    profileDefaultOrganization: t('profileDefaultOrganization'),
    profileFullAccess: t('profileFullAccess'),
    roleResponsable: t('roleResponsable'),
    roleDescAdmin: t('roleDescAdmin'),
    roleDescTechnician: t('roleDescTechnician'),
    roleDescViewer: t('roleDescViewer'),
    saving: t('saving'),
  };
}

function getLabelsViewLabels(t: TFunction) {
  return {
    allZones: t('zoneFilterAll'),
    noZone: t('noZone'),
    qrLabelAddResults: (count: number) => t('qrLabelAddResults').replace('{count}', String(count)),
    qrLabelAddResultsCompact: (count: number) => t('qrLabelAddResultsCompact').replace('{count}', String(count)),
    qrLabelClearSelection: t('qrLabelClearSelection'),
    qrLabelPreparing: t('qrLabelPreparing'),
    qrLabelPopupBlocked: t('qrLabelPopupBlocked'),
    qrLabelQrUnavailable: t('qrLabelQrUnavailable'),
    qrLabelResourceUnavailable: t('qrLabelResourceUnavailable'),
    qrLabelImagePreparationFailed: t('qrLabelImagePreparationFailed'),
    qrLabelPreparationFailed: t('qrLabelPreparationFailed'),
    qrLabelRetry: t('qrLabelRetry'),
    qrLabelNoEligibleBoxes: t('qrLabelNoEligibleBoxes'),
    qrLabelNoMatches: t('qrLabelNoMatches'),
    qrLabelPrintCount: (count: number) => t('qrLabelPrintCount').replace('{count}', String(count)),
    qrLabelSearchTitle: t('qrLabelSearchTitle'),
    qrLabelSelectedSingular: t('qrLabelSelectedSingular'),
    qrLabelSelectedPlural: t('qrLabelSelectedPlural'),
    pageTitle: t('labelsTitle'),
    qrLabelSearchPlaceholder: t('adminPrintLabelsSearchPlaceholder'),
    qrLabelSpeciesCount: (count: number) => t('qrLabelSpeciesCount').replace('{count}', String(count)),
    qrLabelSpeciesSelected: (count: number) => t('qrLabelSpeciesSelected').replace('{count}', String(count)),
    qrLabelSpeciesSelectedCompact: (count: number) => t('qrLabelSpeciesSelectedCompact').replace('{count}', String(count)),
    qrLabelSelectSpecies: (count: number, species: string) => t('qrLabelSelectSpecies').replace('{count}', String(count)).replace('{species}', species),
    qrLabelDeselectSpecies: (count: number, species: string) => t('qrLabelDeselectSpecies').replace('{count}', String(count)).replace('{species}', species),
    selectBox: t('boxInventoryBatchSelectBox'),
    zoneLabel: t('zoneLabel'),
  };
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <span className="metric">
      <small>{label}</small>
      <strong>{value}</strong>
    </span>
  );
}

function mergeBoxDetail(current: AppData, detail: BoxDetail): AppData {
  return {
    ...current,
    boxes: upsertBoxes(current.boxes, [detail]),
    boxDetails: {
      ...current.boxDetails,
      [detail.id]: detail,
    },
  };
}

function getInitialMeasurementForm(
  defaultSalinity = '',
  measuredOn = getTodayDateValue(),
) {
  return {
    measuredOn,
    polypCount: '',
    ephyraeCount: '',
    salinity: defaultSalinity,
    notes: '',
  };
}

/**
 * Control salinity of the box: the one maintained on its zone.
 *
 * A new measurement starts from it, since that is the environment the box is
 * known to sit in. Normalized without rounding so the API's "30.00"
 * reaches the field as "30" and the +/- buttons keep working from there.
 */
function getZoneSalinityValue(box: BoxItem | BoxDetail | null, zones: ThermalZone[]) {
  if (!box) return '';
  const salinity = getCurrentThermalZone(box, zones)?.salinity_psu;
  if (salinity === null || salinity === undefined || salinity === '') return '';
  return formatBiologicalSalinity(salinity);
}

/**
 * Salinity a new measurement starts from.
 *
 * Priority: the box's own recorded salinity, once a measurement has set one --
 * that is the value the technician last decided for this box. Otherwise the
 * zone's control salinity, and finally empty. Normalised so the API's "31.00"
 * reaches the field as "31" and the +/- buttons keep working from there.
 */
function getDefaultMeasurementSalinity(box: BoxItem | BoxDetail | null, zones: ThermalZone[]) {
  const boxSalinity = box?.latest_salinity_psu;
  if (boxSalinity !== null && boxSalinity !== undefined && boxSalinity !== '') {
    return formatBiologicalSalinity(boxSalinity);
  }
  return getZoneSalinityValue(box, zones);
}

function getTodayDateValue() {
  const today = new Date();
  today.setMinutes(today.getMinutes() - today.getTimezoneOffset());
  return today.toISOString().slice(0, 10);
}

function getMeasurementHistory(box: BoxItem | BoxDetail) {
  if ('biological_measurements' in box) {
    return box.biological_measurements;
  }

  return box.latest_measurement ? [box.latest_measurement] : [];
}

function upsertThermalZones(currentZones: ThermalZone[], updatedZones: ThermalZone[]) {
  const updatedById = new Map(updatedZones.map((zone) => [zone.id, zone]));
  const mergedZones = currentZones.map((zone) => updatedById.get(zone.id) ?? zone);
  const existingIds = new Set(currentZones.map((zone) => zone.id));
  return [
    ...mergedZones,
    ...updatedZones.filter((zone) => !existingIds.has(zone.id)),
  ];
}

function getLatestComment(measurements: BiologicalMeasurement[], box: BoxItem | BoxDetail) {
  const measurementWithComment = measurements.find((measurement) => measurement.notes?.trim());
  return measurementWithComment?.notes.trim() || box.latest_measurement?.notes?.trim();
}

function getBoxCreatedDate(box: BoxItem | BoxDetail) {
  if ('created_on' in box) {
    return box.created_on;
  }
  return box.entered_on;
}

function getFirstMeasurementDate(measurements: BiologicalMeasurement[]) {
  if (!measurements.length) return null;
  return measurements
    .map((measurement) => measurement.measured_on)
    .filter(Boolean)
    .sort((first, second) => first.localeCompare(second))[0] ?? null;
}

function getBoxDisplayDate(
  box: BoxItem | BoxDetail,
  measurements: BiologicalMeasurement[],
): { labelKey: TranslationKey; date: string | null } {
  const createdOn = getBoxCreatedDate(box);
  const firstMeasurementOn = getFirstMeasurementDate(measurements);

  if (firstMeasurementOn && (!createdOn || firstMeasurementOn < createdOn)) {
    return { labelKey: 'firstMeasurementOn', date: firstMeasurementOn };
  }

  return { labelKey: 'createdOn', date: createdOn };
}

// Builds the payload sent to the API. Kept in one place so the dirty-state
// check compares exactly what a save would send.
function buildMeasurementPayload(form: {
  measuredOn: string;
  polypCount: string;
  ephyraeCount: string;
  salinity: string;
  notes: string;
}): MeasurementPayload {
  const polypCount = parsePositiveInteger(form.polypCount);
  const ephyraeCount = parsePositiveInteger(form.ephyraeCount);
  if (polypCount == null || ephyraeCount == null) throw new Error('Invalid measurement counts.');
  return {
    measured_on: form.measuredOn,
    polyp_count: polypCount,
    ephyrae_count: ephyraeCount,
    salinity_psu: form.salinity.trim() || null,
    notes: form.notes.trim(),
  };
}

function parsePositiveInteger(value: string) {
  const match = /^(\d+(?:\.\d*)?|\.\d+)(?:e([+-]?\d+))?$/i.exec(value.trim());
  if (!match) return null;
  const [whole, fraction = ''] = match[1].split('.');
  const digits = `${whole}${fraction}`.replace(/^0+/, '');
  if (!digits) return 0;
  const exponent = Number(match[2] ?? '0');
  if (!Number.isSafeInteger(exponent)) return null;
  const scale = exponent - fraction.length;
  // Check decimal digits before conversion so rounding/underflow cannot turn a fraction into an integer or zero.
  if (digits.length + scale > 10 || digits.length + scale <= 0) return null;
  let integerText: string;
  if (scale < 0) {
    if (!/^0+$/.test(digits.slice(scale))) return null;
    integerText = digits.slice(0, scale);
  } else {
    integerText = digits + '0'.repeat(scale);
  }
  const parsedValue = Number(integerText);
  return parsedValue <= 2147483647 ? parsedValue : null;
}

function incrementCountValue(currentValue: string, increment: number) {
  const parsed = parsePositiveInteger(currentValue);
  if (parsed == null) return currentValue.trim() ? currentValue : String(increment);
  return String(Math.min(parsed + increment, 2147483647));
}

function decrementCountValue(currentValue: string) {
  const parsed = parsePositiveInteger(currentValue);
  if (parsed == null) return currentValue;
  return String(Math.max(parsed - 1, 0));
}

function getMeasurementSaveError(error: unknown, t: TFunction) {
  if (isMeasurementWeekConflict(error)) {
    return t('measurementWeekConflict');
  }
  if (isMeasurementEditWindowExpired(error)) {
    return t('weeklyMeasurementWindowExpired');
  }
  if (error instanceof ApiError && error.status === 403) {
    return t('measurementForbidden');
  }

  return getErrorMessage(error);
}

function getBoxLineage(box: BoxItem | BoxDetail): BoxLineage {
  if ('lineage' in box) {
    return box.lineage;
  }

  return { parents: [], children: [] };
}

function getBoxMovements(box: BoxItem | BoxDetail): BoxMovement[] {
  return 'movements' in box ? box.movements : [];
}

function getCurrentThermalZone(box: BoxItem | BoxDetail, zones: ThermalZone[]) {
  if (!box.thermal_zone) return null;
  return zones.find((zone) => zone.id === box.thermal_zone?.id) ?? null;
}

function getSubcultureSaveError(error: unknown, t: TFunction) {
  if (error instanceof ApiError && error.status === 403) {
    return t('subcultureForbidden');
  }
  if (error instanceof ApiError && error.status === 409 && error.data
      && typeof error.data === 'object' && 'code' in error.data
      && error.data.code === 'subculture_current_state_changed') {
    return t('subcultureStateChanged');
  }
  return getErrorMessage(error);
}

function getMoveSaveError(error: unknown, t: TFunction) {
  if (isBoxLocationChangedError(error)) {
    return t('moveLocationChanged');
  }
  if (error instanceof ApiError && error.status === 403) {
    return t('moveForbidden');
  }

  return getErrorMessage(error);
}

function isBoxLocationChangedError(error: unknown) {
  return error instanceof ApiError
    && error.status === 409
    && typeof error.data === 'object'
    && error.data !== null
    && 'code' in error.data
    && error.data.code === 'box_location_changed';
}

function formatTemperature(value: string | number | null | undefined) {
  if (value === null || value === undefined || value === '') return '-';
  const numericValue = typeof value === 'number' ? value : Number.parseFloat(value);
  return Number.isFinite(numericValue) ? `${numericValue.toFixed(1)}°C` : '-';
}

function formatTemperatureValue(value: string | number | null | undefined) {
  if (value === null || value === undefined || value === '') return '-';
  const numericValue = typeof value === 'number' ? value : Number.parseFloat(value);
  return Number.isFinite(numericValue) ? `${numericValue.toFixed(1)}°C` : '-';
}

function formatSalinity(value: string | number | null | undefined) {
  if (value === null || value === undefined || value === '') return '-';
  const numeric = typeof value === 'string' ? Number.parseFloat(value) : value;
  return Number.isNaN(numeric) ? '-' : numeric.toFixed(1);
}

function getLanguage(profile: UserProfile | null): Language {
  return resolveLanguage(
    profile?.interface_language
      ?? getStoredInterfaceLanguage()
      ?? window.navigator.language,
  );
}

function getSelectableOrganizations(profile: UserProfile | null) {
  if (!profile) return [];

  const organizations = profile.memberships.length > 0
    ? profile.memberships.map((membership) => membership.organization)
    : profile.organizations;

  return organizations.filter(
    (organization, index) =>
      organizations.findIndex((candidate) => candidate.id === organization.id) === index,
  );
}

function getOrganizationById(profile: UserProfile | null, organizationId: number | null) {
  if (organizationId == null) return null;
  return getSelectableOrganizations(profile).find((organization) => organization.id === organizationId) ?? null;
}

function resolveActiveOrganizationId(profile: UserProfile | null, preferredOrganizationId: number | null) {
  const organizations = getSelectableOrganizations(profile);
  if (preferredOrganizationId != null && organizations.some((organization) => organization.id === preferredOrganizationId)) {
    return preferredOrganizationId;
  }
  if (organizations.length === 1) return organizations[0].id;
  return null;
}

function setProfileActiveOrganization(profile: UserProfile, organizationId: number): UserProfile {
  const organization = getOrganizationById(profile, organizationId);
  return {
    ...profile,
    active_organization: organization ?? profile.active_organization,
  };
}

function getActiveOrganizationId(profile: UserProfile | null) {
  return profile?.active_organization?.id ?? null;
}

function getMembershipRole(profile: UserProfile | null, organizationId: number | null) {
  if (!profile || organizationId == null) return null;
  return profile.memberships.find((membership) => membership.organization.id === organizationId)?.role ?? null;
}

function getMembershipRoleLabel(
  profile: UserProfile | null,
  organizationId: number | null,
  responsableLabel: string,
) {
  if (!profile || organizationId == null) return null;
  const membership = profile.memberships.find((item) => item.organization.id === organizationId);
  return membership ? getAccountMemberRoleLabel(membership, responsableLabel) : null;
}

function getBrandOrganizationName(profile: UserProfile | null, t: TFunction) {
  if (!profile) return t('laboratoryTracking');

  if (profile.active_organization) return profile.active_organization.name;

  const organizations = getSelectableOrganizations(profile);
  if (organizations.length === 0) return t('laboratoryTracking');
  return organizations[0].name;
}

function userHasAdminRole(profile: UserProfile | null, activeOrganizationId: number | null = getActiveOrganizationId(profile)) {
  if (!profile) return false;
  if (profile.is_superuser) return true;
  return getMembershipRole(profile, activeOrganizationId) === 'admin';
}

function userCanCreateBoxes(profile: UserProfile | null) {
  if (!profile) return false;
  if (profile.is_superuser) return true;
  return ['admin', 'lab_technician'].includes(getMembershipRole(profile, getActiveOrganizationId(profile)) ?? '');
}

function buildNextBoxCode(
  boxes: BoxItem[],
  strain: QuickCreatedStrain,
  organizationId: number,
) {
  const matchingBoxes = boxes
    .filter((box) => box.organization.id === organizationId && box.strain.id === strain.id)
    .map((box) => {
      const match = box.global_code.match(/^.*\.(\d+).*$/);
      return {
        numberText: match?.[1] ?? '',
        number: match ? Number(match[1]) : Number.NaN,
      };
    })
    .filter((item) => Number.isFinite(item.number))
    .sort((first, second) => second.number - first.number);

  const template = matchingBoxes[0];
  if (template) {
    const nextNumber = template.number + 1;
    const width = Math.max(template.numberText.length, 3);
    const boxNumber = String(nextNumber).padStart(width, '0');
    return {
      boxNumber,
      globalCode: `${strain.code}.${boxNumber}`,
    };
  }

  const boxNumber = '001';
  return {
    boxNumber,
    globalCode: `${strain.code}.${boxNumber}`,
  };
}

function boxCodeMatchesBoxNumber(globalCode: string, boxNumber: string) {
  const codeNumber = extractBoxNumberFromCode(globalCode);
  if (!codeNumber) return true;
  return normalizeBoxNumber(codeNumber) === normalizeBoxNumber(boxNumber);
}

function extractBoxNumberFromCode(globalCode: string) {
  return globalCode.trim().match(/^.*\.(\d+).*$/)?.[1] ?? null;
}

function normalizeBoxNumber(value: string) {
  const normalized = value.trim();
  return /^\d+$/.test(normalized) ? String(Number.parseInt(normalized, 10)) : normalized;
}

function userCanWriteLabData(profile: UserProfile | null, organizationId: number) {
  if (!profile) return false;
  if (profile.is_superuser) return true;

  return profile.memberships.some(
    (membership) => membership.organization.id === organizationId
      && ['admin', 'lab_technician'].includes(membership.role),
  );
}

function userCanArchiveBox(profile: UserProfile | null, organizationId: number) {
  if (!profile) return false;
  if (profile.is_superuser) return true;

  return profile.memberships.some(
    (membership) => membership.organization.id === organizationId && membership.role === 'admin',
  );
}

function getTitle(tab: TabId, t: TFunction) {
  if (tab === 'pilotage') return t('pilotageTitle');
  if (tab === 'overview') return t('overviewTitle');
  if (tab === 'zones') return t('zonesTitle');
  if (tab === 'exports') return t('exportsTitle');
  if (tab === 'labels') return t('labelsTitle');
  if (tab === 'admin') return t('adminTitle');
  return t('profileTitle');
}

async function getApplicationError(error: unknown): Promise<ApplicationError> {
  const status = error instanceof ApiError ? error.status : null;
  let profileStatus: number | null = null;

  // DRF SessionAuthentication uses 403 both when the session is missing and
  // when an authenticated user is genuinely forbidden. Re-check the
  // organization-independent profile endpoint instead of interpreting every
  // 403 as logout.
  if (status === 403) {
    try {
      await apiGet<UserProfile>('/api/profile/', { skipOrganizationContext: true });
      profileStatus = 200;
    } catch (profileError) {
      profileStatus = profileError instanceof ApiError ? profileError.status : null;
    }
  }

  return {
    message: getErrorMessage(error),
    requiresAuthentication: requiresSignInRecovery(status, profileStatus),
  };
}

function getCurrentAppPath(): string {
  return `${window.location.pathname}${window.location.search}${window.location.hash}`;
}

function isRecognizedAppPath(path: string, isDesktopApp: boolean, canUseAdmin: boolean): boolean {
  // Keep this positive list aligned with getCurrentRoute, never its default home.
  if (!path.startsWith('/') || path.startsWith('//') || /[\\\s\u0000-\u001f\u007f]/.test(path)) return false;
  try {
    const url = new URL(path, 'https://in-app.invalid');
    if (`${url.pathname}${url.search}${url.hash}` !== path) return false;
    const pathname = url.pathname;
    const decoded = decodeURIComponent(pathname);
    // Match the encoded segment, not a decoded Box code containing slashes.
    const isBoxPath = /^\/boxes\/[^/]+\/?$/.test(pathname);
    if (/[\\\u0000-\u001f\u007f]/.test(decoded)
      || /(?:^|\/)\.{1,2}(?:\/|$)/.test(decoded)
      || (/%2f/i.test(pathname) && !isBoxPath)) return false;
    if (['/', '/zones', '/overview', '/labels', '/profile'].includes(pathname)) return true;
    if (pathname === '/exports') return isDesktopApp;
    if (isBoxPath) return true;
    const idMatch = pathname.match(/^\/(?:bac\/(\d+)|zones\/(\d+)(?:\/(?:boxes|history))?)\/?$/);
    if (idMatch) {
      const id = Number(idMatch[1] ?? idMatch[2]);
      return Number.isSafeInteger(id) && id > 0;
    }
    if (!isDesktopApp || !canUseAdmin) return false;
    return pathname === '/administration' || pathname === '/administration/'
      || Object.values(ADMIN_SECTION_PATHS).some((sectionPath) => (
        pathname === sectionPath || pathname === `${sectionPath}/`
      ));
  } catch {
    return false;
  }
}

function getCurrentRoute(): RouteState {
  const path = window.location.pathname;

  // Keep the QR handoff pending until the authenticated organization is ready.
  const scanMatch = path.match(/^\/bac\/(\d+)\/?$/);
  const scanBoxValue = scanMatch?.[1] ?? (path === '/' ? new URLSearchParams(window.location.search).get('scan_box') : null);
  if (scanBoxValue && /^\d+$/.test(scanBoxValue)) {
    const scanBoxId = Number(scanBoxValue);
    if (Number.isSafeInteger(scanBoxId) && scanBoxId > 0) {
      return { tab: 'pilotage', boxCode: null, boxId: null, scanBoxId };
    }
  }

  if (path.startsWith('/boxes/')) {
    return {
      tab: 'pilotage',
      boxCode: decodeURIComponent(path.replace('/boxes/', '').replace(/\/$/, '')),
      boxId: null,
    };
  }

  if (path === '/zones') {
    return { tab: 'zones', boxCode: null, boxId: null };
  }

  if (path === '/overview') {
    return { tab: 'overview', boxCode: null, boxId: null };
  }

  if (path === '/labels') {
    return { tab: 'labels', boxCode: null, boxId: null };
  }

  const zoneHistoryMatch = path.match(/^\/zones\/(\d+)\/history\/?$/);
  if (zoneHistoryMatch) {
    const requestedDirection = new URLSearchParams(window.location.search).get('direction');
    const zoneHistoryDirection = requestedDirection === 'departure' ? 'departure' : 'arrival';
    return {
      tab: 'zones',
      boxCode: null,
      boxId: null,
      zoneId: Number(zoneHistoryMatch[1]),
      zoneHistory: true,
      zoneHistoryDirection,
    };
  }

  const zoneBoxesMatch = path.match(/^\/zones\/(\d+)\/boxes\/?$/);
  if (zoneBoxesMatch) {
    return {
      tab: 'zones',
      boxCode: null,
      boxId: null,
      zoneId: Number(zoneBoxesMatch[1]),
      zoneBoxes: true,
    };
  }

  const zoneMatch = path.match(/^\/zones\/(\d+)\/?$/);
  if (zoneMatch) {
    return {
      tab: 'zones',
      boxCode: null,
      boxId: null,
      zoneId: Number(zoneMatch[1]),
    };
  }

  if (path === '/exports') {
    return { tab: 'exports', boxCode: null, boxId: null };
  }

  if (path === '/administration' || path === '/administration/') {
    return { tab: 'admin', boxCode: null, boxId: null, adminSection: 'accounts' };
  }

  const adminSection = Object.entries(ADMIN_SECTION_PATHS).find(([, sectionPath]) => (
    path === sectionPath || path === `${sectionPath}/`
  ));
  if (adminSection) {
    return {
      tab: 'admin',
      boxCode: null,
      boxId: null,
      adminSection: adminSection[0] as AdminSectionKey,
    };
  }

  if (path === '/profile') {
    return { tab: 'profile', boxCode: null, boxId: null };
  }

  return { tab: 'pilotage', boxCode: null, boxId: null };
}
