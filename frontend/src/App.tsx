import {
  lazy,
  Suspense,
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
  invalidateInFlightGet,
  setActiveOrganizationContext,
} from './api/client';

import {
  createTranslator,
  getStoredInterfaceLanguage,
  resolveLanguage,
  setStoredInterfaceLanguage,
  type Language,
  type Translator,
} from './i18n';
import type { AdminSectionKey } from './components/AdminView';
import type { EditableMeasurement } from './types';
import BoxPage from './components/BoxPage';

// A measurement handed over by the history so the box sheet can open with it
// already filled in.
type HistoryMeasurementPrefill = EditableMeasurement;
import { useConfirmAction } from './components/ConfirmActionModal';
import ApplicationErrorNotice from './components/ApplicationErrorNotice';
import PilotageView from './components/PilotageView';
import LoginPage from './components/LoginPage';
import PasswordResetPage from './components/PasswordResetPage';
import ModalPortal from './components/ModalPortal';
import PageLoader from './components/PageLoader';
import PolypbaseIcon, { type PolypbaseIconName } from './components/PolypbaseIcon';
import ProfileView from './components/ProfileView';
import QrSearchModal from './components/QrSearchModal';
import TabletQrScannerModal from './components/TabletQrScannerModal';
import { useIsDesktopApp } from './hooks/useIsDesktopApp';
import { useIsPhoneLayout } from './hooks/useIsPhoneLayout';
import { useIsTabletLayout } from './hooks/useIsTabletLayout';
import { useRecentBoxLimit } from './hooks/useRecentBoxLimit';
import type {
  BiologicalMeasurement,
  BoxActivatePayload,
  BoxCreatePayload,
  BoxCreationResolution,
  BoxDeactivatePayload,
  BoxDetail,
  BoxInitialLocationPayload,
  BoxInventoryBatchQualifyPayload,
  BoxInventoryBatchResult,
  BoxItem,
  BoxMovePayload,
  BoxMovementCorrectionPayload,
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
} from './types/admin';
import { getAccountMemberRoleLabel } from './utils/accountMembers';
import { userCanCreateBoxes, userCanWriteLabData, userHasAdminRole } from './utils/profilePermissions';
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
  isMeasurementEditWindowExpired,
  isMeasurementWeekConflict,
  type MeasurementPayloadValues as MeasurementPayload,
} from './utils/boxMeasurement';
import { getErrorMessage } from './utils/errors';
import {
  getPasswordResetRoute,
  isPublicAuthPath,
  requiresSignInRecovery,
  shouldRedirectToLogin,
} from './utils/authRouting';
import { createInAppHistory } from './utils/inAppHistory';
import { PHONE_NAVIGATION_ITEMS, type PhoneDestination } from './utils/phoneNavigation';
import { getLabelsViewLabels, getProfileLabels } from './utils/presentationLabels';
import type { QrLabelItem } from './utils/qrLabels';
import { isBoxLocationChangedError } from './utils/boxErrors';
import { isRouteRequestCurrent } from './utils/routeSafety';

const AdminView = lazy(() => import('./components/AdminView'));
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

class BoxDetailSupersededError extends Error {}

type MutationRefreshRecovery = (() => Promise<void>) & { isFresh?: () => boolean };

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
  const boxDetailRevisionsRef = useRef(new Map<string, number>());
  const zoneCollectionRevisionRef = useRef(0);
  const zoneCollectionReadRef = useRef(0);
  const zoneRevisionsRef = useRef(new Map<string, number>());
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
  const [refreshRecovery, setRefreshRecovery] = useState<MutationRefreshRecovery | null>(null);
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

  function invalidateBoxDetail(boxId: number, generation = organizationRequestGenerationRef.current) {
    const key = `${generation}:${boxId}`;
    // Allocate client boundary tokens within this organization lifetime, not server commit order.
    const sequenceKey = `${generation}:sequence`;
    const revision = (boxDetailRevisionsRef.current.get(sequenceKey) ?? 0) + 1;
    boxDetailRevisionsRef.current.set(sequenceKey, revision);
    boxDetailRevisionsRef.current.set(key, revision);
    invalidateInFlightGet(`/api/boxes/${boxId}/`, activeOrganizationId);
    setRefreshRecovery((current) => current?.isFresh && !current.isFresh() ? null : current);
    return revision;
  }

  function beginBoxMutation(affectsZones = false) {
    const generation = operationGeneration;
    // A scalar watermark also covers IDs learned from creation/subculture responses.
    const startedRevision = boxDetailRevisionsRef.current.get(`${generation}:sequence`) ?? 0;
    return (boxId: number) => {
      const key = `${generation}:${boxId}`;
      const safe = (boxDetailRevisionsRef.current.get(key) ?? 0) <= startedRevision;
      // Success invalidates reads even when response ordering cannot establish freshness.
      const revision = invalidateBoxDetail(boxId, generation);
      if (affectsZones) zoneCollectionRevisionRef.current += 1;
      const { setData, assertCurrent } = getOperationRequests(generation);
      const isCurrent = () => generation === organizationRequestGenerationRef.current
        && revision === boxDetailRevisionsRef.current.get(key);
      return {
        isCurrent,
        setData: (update: (current: AppData) => AppData) => setData((current) => (
          safe && isCurrent() ? update(current) : current
        )),
        reconcile: (afterRead?: () => Promise<unknown>) => safe && !afterRead ? Promise.resolve() : refreshAfterMutation(
          async () => {
            if (!safe && isCurrent()) {
              try {
                await refreshBoxAfterMeasurement(boxId);
              } catch (requestError) {
                if (!(requestError instanceof BoxDetailSupersededError) && isCurrent()) throw requestError;
              }
            }
            if (afterRead) await afterRead();
          }, assertCurrent, afterRead
            // Side-resource recovery remains needed even after this Box is superseded.
            ? () => generation === organizationRequestGenerationRef.current
            : isCurrent,
        ),
      };
    };
  }

  function getOperationRequests(generation = organizationRequestGenerationRef.current) {
    const detailReads = new Map<string, number>();
    const areDetailsCurrent = (reads = detailReads) => [...reads].every(
      ([key, revision]) => revision === (boxDetailRevisionsRef.current.get(key) ?? 0),
    );
    const assertCurrent = () => {
      if (generation !== organizationRequestGenerationRef.current) {
        throw new ApiResourceCancelledError();
      }
      if (!areDetailsCurrent()) throw new BoxDetailSupersededError();
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
      apiGet: <T,>(path: string) => {
        const match = /^\/api\/boxes\/(\d+)\/$/.exec(path);
        if (match) {
          const key = `${generation}:${Number(match[1])}`;
          detailReads.set(key, boxDetailRevisionsRef.current.get(key) ?? 0);
        }
        // Zone snapshots must not join pages or histories captured before a boundary.
        return run(() => apiGet<T>(path, path.startsWith('/api/thermal-zones/') ? { cache: 'no-store' } : {}));
      },
      apiPost: <T,>(path: string, payload: unknown) => run(() => apiPost<T>(path, payload)),
      apiPatch: <T,>(path: string, payload: unknown) => run(() => apiPatch<T>(path, payload)),
      apiDelete: <T,>(path: string) => run(() => apiDelete<T>(path)),
      assertCurrent,
      isCurrent: () => generation === organizationRequestGenerationRef.current && areDetailsCurrent(),
      setData: (update: (current: AppData) => AppData) => {
        // React may apply this updater after another mutation has committed.
        const reads = new Map(detailReads);
        setData((current) => (
          generation === organizationRequestGenerationRef.current && areDetailsCurrent(reads)
            ? update(current) : current
        ));
      },
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

  function publishZoneUpdate(zoneId: number) {
    // Client response boundaries, not server transaction commit ordering.
    zoneCollectionRevisionRef.current += 1;
    const key = String(zoneId);
    const revision = (zoneRevisionsRef.current.get(key) ?? 0) + 1;
    zoneRevisionsRef.current.set(key, revision);
    const { setData } = getOperationRequests(operationGeneration);
    return (update: (current: AppData) => AppData) => setData((current) => (
      zoneRevisionsRef.current.get(key) === revision ? update(current) : current
    ));
  }

  function beginZoneMutation(zoneId: number) {
    const generation = operationGeneration;
    const key = String(zoneId);
    const startedRevision = zoneRevisionsRef.current.get(key) ?? 0;
    const capabilityKey = `capability:${zoneId}`;
    const startedCapability = zoneRevisionsRef.current.get(capabilityKey) ?? 0;
    const { assertCurrent } = getOperationRequests(operationGeneration);
    return () => {
      assertCurrent();
      // Overlapping full snapshots cannot establish server commit order, even for different fields.
      const safe = startedRevision === (zoneRevisionsRef.current.get(key) ?? 0)
        && startedCapability === (zoneRevisionsRef.current.get(capabilityKey) ?? 0);
      const publish = publishZoneUpdate(zoneId);
      const revision = zoneRevisionsRef.current.get(key);
      const isFresh = () => generation === organizationRequestGenerationRef.current
        && revision === zoneRevisionsRef.current.get(key);
      return {
        setData: (update: (current: AppData) => AppData) => publish((current) => safe ? update(current) : current),
        reconcile: () => safe ? Promise.resolve() : refreshAfterMutation(async () => {
          const collectionRead = zoneCollectionReadRef.current;
          const capability = zoneRevisionsRef.current.get(capabilityKey) ?? 0;
          const isReadCurrent = () => collectionRead === zoneCollectionReadRef.current
            && capability === (zoneRevisionsRef.current.get(capabilityKey) ?? 0);
          // Detail GET uses the write serializer. Find this zone's complete summary instead.
          const zones = await fetchAllPages<ThermalZone>(
            '/api/thermal-zones/?limit=80', (items) => items.some((zone) => zone.id === zoneId),
          );
          assertCurrent();
          if (!isFresh() || !isReadCurrent()) return;
          const zone = zones.find((item) => item.id === zoneId);
          if (!zone) throw new Error('Zone summary not found.');
          publish((current) => isReadCurrent() ? {
            ...current, zones: upsertThermalZones(current.zones, [zone]),
          } : current);
        }, assertCurrent, isFresh),
      };
    };
  }

  async function readZoneCollection(path = '/api/thermal-zones/?limit=80') {
    const generation = organizationRequestGenerationRef.current;
    const revision = ++zoneCollectionRevisionRef.current;
    zoneCollectionReadRef.current += 1;
    const { assertCurrent } = getOperationRequests(generation);
    const isCurrent = () => generation === organizationRequestGenerationRef.current
      && revision === zoneCollectionRevisionRef.current;
    try {
      const zones = await fetchAllPages<ThermalZone>(path);
      return { zones, isCurrent };
    } catch (requestError) {
      assertCurrent();
      if (isCurrent()) throw requestError;
      // Superseded collection work is a safe no-op, not a recovery failure.
      return { zones: [] as ThermalZone[], isCurrent };
    }
  }

  async function refreshZones(path = '/api/thermal-zones/?limit=80') {
    const { setData, assertCurrent } = getOperationRequests(operationGeneration);
    assertCurrent();
    const read = await readZoneCollection(path);
    setData((current) => read.isCurrent() ? { ...current, zones: read.zones } : current);
  }

  async function fetchScopedData(profile: UserProfile, organizationId: number) {
    setActiveOrganizationContext(organizationId);
    const scopedProfile = setProfileActiveOrganization(profile, organizationId);
    // The complete Box list is not part of the bootstrap: see requestBoxCollection.
    const [zoneRead, dashboard] = await Promise.all([
      readZoneCollection(),
      apiGet<Dashboard>('/api/dashboard/'),
    ]);

    return {
      boxes: [] as BoxItem[],
      boxDetails: {},
      zones: zoneRead.zones,
      zoneRead,
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
      || item.previous_global_code?.toLowerCase() === needle
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
    boxDetailRevisionsRef.current.clear();
    zoneRevisionsRef.current.clear();
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
      const { zoneRead, ...nextData } = await fetchScopedData(data.profile, organizationId);
      if (requestGeneration !== organizationRequestGenerationRef.current) return;
      setData((current) => requestGeneration === organizationRequestGenerationRef.current
        ? { ...nextData, zones: zoneRead.isCurrent() ? nextData.zones : current.zones } : current);
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
    boxDetailRevisionsRef.current.clear();
    zoneRevisionsRef.current.clear();
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

        const { zoneRead, ...nextData } = await fetchScopedData(profile, resolvedOrganizationId);
        if (!isCurrentRequest()) return;

        setData((current) => isCurrentRequest()
          ? { ...nextData, zones: zoneRead.isCurrent() ? nextData.zones : current.zones } : current);
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
    const scanBoxId = route.scanBoxId;

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
        const result = await apiPost<{ id: number; global_code: string }>(`/api/boxes/${scanBoxId}/scan/`, {});
        if (!isCurrentRequest()) return;
        replaceRoute(
          { tab: 'pilotage', boxCode: result.global_code, boxId: result.id },
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
        const overview = await apiGet<OverviewResponse>('/api/overview/active-boxes/?months=3&history=full');
        if (!isCurrentRequest()) return;
        setData((current) => ({ ...current, overview: overview.results.map((box) => ({
                  ...box, history_start_date: overview.history_start_date, history_end_date: overview.history_end_date,
                  history_complete: overview.complete_history === true,
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
    const navigationGeneration = navigationGenerationRef.current;
    const isCurrentRequest = () => isActive && requestGeneration === organizationRequestGenerationRef.current
      && navigationGeneration === navigationGenerationRef.current;

    async function resolveBoxCode() {
      try {
        const box = await findBoxBySearch(code, (item) => item.global_code === code || item.previous_global_code === code);
        if (!isCurrentRequest()) return;
        setResolvedBoxCode({ organizationId, code, boxId: box?.id ?? null });
        if (box && box.global_code !== code) {
          replaceRoute(
            { tab: 'pilotage', boxCode: box.global_code, boxId: box.id },
            `/boxes/${encodeURIComponent(box.global_code)}`,
          );
        }
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
      const { apiGet, setData, isCurrent: isDetailCurrent } = getOperationRequests(requestGeneration);
      try {
        setIsBoxLoading(true);
        const detail = await apiGet<BoxDetail>(`/api/boxes/${boxId}/`);
        if (!isCurrentRequest()) return;
        setData((current) => mergeBoxDetail(current, detail));
      } catch (requestError) {
        if (!isCurrentRequest() || !isDetailCurrent()) return;
        const applicationError = await getApplicationError(requestError);
        if (!isCurrentRequest() || !isDetailCurrent()) return;
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
    missingIds.forEach((boxId) => {
      // Keep each publication independent: one changed Box must not discard its siblings.
      const { apiGet, setData } = getOperationRequests();
      void apiGet<BoxDetail>(`/api/boxes/${boxId}/`)
        .then((detail) => {
          setData((current) => ({ ...current, boxes: upsertBoxes(current.boxes, [detail]) }));
        })
        .catch(() => {
          // A failed recent box is only left out; it is retried on a later visit.
          requestedIds.delete(boxId);
        });
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

  function openBox(boxId: number, _fallbackCode?: string) {
    const organizationGeneration = organizationRequestGenerationRef.current;
    const requestGeneration = ++openBoxRequestGenerationRef.current;
    // Cached codes can belong to a retired identity. Resolve before selecting action targets.
    const navigationGeneration = navigationGenerationRef.current;
    const { apiGet, setData, isCurrent: isDetailCurrent } = getOperationRequests(organizationGeneration);
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
        navigateTo({ tab: 'pilotage', boxCode: detail.global_code, boxId: detail.id }, `/boxes/${encodeURIComponent(detail.global_code)}`);
      })
      .catch(async (requestError) => {
        if (!isDetailCurrent()) return;
        if (!isRouteRequestCurrent(
          requestGeneration,
          openBoxRequestGenerationRef.current,
          navigationGeneration,
          navigationGenerationRef.current,
        )) return;
        const applicationError = await getApplicationError(requestError);
        if (isDetailCurrent() && isRouteRequestCurrent(
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
    // A same-path noop can still refresh the resolved box identity.
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
    setActiveOrganizationContext(null);
    organizationRequestGenerationRef.current += 1;
    boxDetailRevisionsRef.current.clear();
    zoneRevisionsRef.current.clear();
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

  async function refreshAfterMutation(
    refresh: () => Promise<unknown>, assertCurrent: () => void, isFresh = () => true,
  ) {
    const generation = organizationRequestGenerationRef.current;
    assertCurrent();
    if (!isFresh()) return;
    try {
      await refresh();
      assertCurrent();
    } catch (requestError) {
      assertCurrent();
      // A later committed Box mutation owns the refresh now; this is not a read failure.
      if (requestError instanceof BoxDetailSupersededError || !isFresh()) return;
      setRefreshRecovery((current) => {
        if (generation !== organizationRequestGenerationRef.current || !isFresh()) return current;
        const recovery = async () => {
          assertCurrent();
          if (isFresh()) {
            try {
              await refresh();
              assertCurrent();
            } catch (requestError) {
              assertCurrent();
              if (!(requestError instanceof BoxDetailSupersededError) && isFresh()) throw requestError;
            }
          }
          setRefreshRecovery((pending) => pending === recovery ? null : pending);
        };
        recovery.isFresh = isFresh;
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

  function applyMeasurementResult(
    boxId: number, measurement: BiologicalMeasurement,
    setData: (update: (current: AppData) => AppData) => void,
  ) {
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

  async function correctMovement(boxId: number, movementId: number, payload: BoxMovementCorrectionPayload) {
    const { apiPatch, assertCurrent } = getOperationRequests(operationGeneration);
    const commit = beginBoxMutation(true);
    const detail = await apiPatch<BoxDetail>(`/api/boxes/${boxId}/movements/${movementId}/`, payload);
    assertCurrent();
    const publication = commit(boxId);
    // The component owns the subsequent guarded detail and movement refresh.
    publication.setData((current) => ({
      ...mergeBoxDetail(current, detail),
      overview: null,
      exportOptions: null,
    }));
  }

  async function refreshMovementState(boxId: number) {
    const { apiGet, setData, assertCurrent } = getOperationRequests(operationGeneration);
    const [detail] = await Promise.all([
      apiGet<BoxDetail>(`/api/boxes/${boxId}/`),
      refreshZones('/api/thermal-zones/'),
    ]);
    assertCurrent();
    setData((current) => ({
      ...mergeBoxDetail(current, detail),
      overview: null,
      exportOptions: null,
    }));
    return detail;
  }

  async function createMeasurement(boxId: number, payload: MeasurementPayload) {
    const { apiPost, assertCurrent } = getOperationRequests(operationGeneration);
    const commit = beginBoxMutation();
    try {
      const created = await apiPost<BiologicalMeasurement>(`/api/boxes/${boxId}/measurements/`, payload);
      assertCurrent();
      const publication = commit(boxId);
      applyMeasurementResult(boxId, created, publication.setData);
      await refreshAfterMutation(() => refreshBoxAfterMeasurement(boxId), assertCurrent, publication.isCurrent);
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
    const { apiPost, assertCurrent } = getOperationRequests(operationGeneration);
    const commit = beginBoxMutation(true);
    const detail = await apiPost<BoxDetail>('/api/boxes/automatic/', payload);
    assertCurrent();
    const publication = commit(detail.id);

    publication.setData((current) => ({
      ...mergeBoxDetail(current, detail),
      boxes: upsertBoxes(current.boxes, [detail]),
      overview: null,
      exportOptions: null,
    }));
    await publication.reconcile();
    assertCurrent();
    return detail;
  }

  async function resolveBoxCreation(requestId: string): Promise<BoxCreationResolution> {
    const { apiPost, assertCurrent } = getOperationRequests(operationGeneration);
    const commit = beginBoxMutation(true);
    const result = await apiPost<BoxCreationResolution>('/api/boxes/automatic/resolve/', { request_id: requestId });
    assertCurrent();
    if (result.status === 'created') {
      const detail = result.box;
      const publication = commit(detail.id);
      publication.setData((current) => ({
        ...mergeBoxDetail(current, detail),
        boxes: upsertBoxes(current.boxes, [detail]),
        overview: null,
        exportOptions: null,
      }));
      await publication.reconcile();
      assertCurrent();
    }
    return result;
  }

  async function updateMeasurement(boxId: number, measurementId: number, payload: MeasurementPayload) {
    const { apiPatch, assertCurrent } = getOperationRequests(operationGeneration);
    const commit = beginBoxMutation();
    try {
      const updated = await apiPatch<BiologicalMeasurement>(
        `/api/boxes/${boxId}/measurements/${measurementId}/`,
        payload,
      );
      assertCurrent();
      const publication = commit(boxId);
      applyMeasurementResult(boxId, updated, publication.setData);
      await refreshAfterMutation(() => refreshBoxAfterMeasurement(boxId), assertCurrent, publication.isCurrent);
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
    const { apiPost, setData, assertCurrent } = getOperationRequests(operationGeneration);
    const commit = beginBoxMutation(true);
    const conflictRevision = boxDetailRevisionsRef.current.get(`${operationGeneration}:${boxId}`) ?? 0;
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
            return detail && conflictRevision === (boxDetailRevisionsRef.current.get(`${operationGeneration}:${boxId}`) ?? 0)
              ? mergeBoxDetail(current, { ...detail, current_polyp_state: state }) : current;
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
    const publication = commit(boxId);
    const children = result.children.map((child) => ({ child, publication: commit(child.id) }));
    publication.setData((current) => {
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
        overview: null,
        exportOptions: null,
      };
    });
    children.forEach(({ child, publication: childPublication }) => {
      childPublication.setData((current) => ({ ...current, boxes: upsertBoxes(current.boxes, [child]) }));
    });
    await Promise.all([
      refreshAfterMutation(() => refreshBoxAfterMeasurement(boxId), assertCurrent, publication.isCurrent),
      ...children.map(({ publication: childPublication }) => childPublication.reconcile()),
    ]);
    assertCurrent();
    return result;
  }

  async function moveBox(boxId: number, payload: BoxMovePayload) {
    const { apiGet, apiPost, setData, assertCurrent } = getOperationRequests(operationGeneration);
    const commit = beginBoxMutation(true);
    try {
      const detail = await apiPost<BoxDetail>(`/api/boxes/${boxId}/move/`, payload);
      assertCurrent();
      const publication = commit(boxId);
      publication.setData((current) => ({
        ...mergeBoxDetail(current, detail),
        overview: null,
        exportOptions: null,
      }));
      await publication.reconcile(async () => {
        assertCurrent();
        await refreshZones();
        assertCurrent();
      });
      assertCurrent();
      return detail;
    } catch (requestError) {
      if (isBoxLocationChangedError(requestError)) {
        try {
          const [detail] = await Promise.all([
            apiGet<BoxDetail>(`/api/boxes/${boxId}/`),
            refreshZones(),
          ]);
          assertCurrent();
          setData((current) => ({
            ...mergeBoxDetail(current, detail),
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
    const { apiPost, assertCurrent } = getOperationRequests(operationGeneration);
    const commit = beginBoxMutation(true);
    const detail = await apiPost<BoxDetail>(`/api/boxes/${boxId}/deactivate/`, payload);
    assertCurrent();
    const publication = commit(boxId);

    publication.setData((current) => ({
      ...mergeBoxDetail(current, detail),
      boxes: upsertBoxes(current.boxes, [detail]),
      overview: null,
      exportOptions: null,
    }));
    await publication.reconcile();
    assertCurrent();
  }

  async function reactivateBox(boxId: number, payload: BoxActivatePayload) {
    const { apiPost, assertCurrent } = getOperationRequests(operationGeneration);
    const commit = beginBoxMutation(true);
    const detail = await apiPost<BoxDetail>(`/api/boxes/${boxId}/activate/`, payload);
    assertCurrent();
    const publication = commit(boxId);

    publication.setData((current) => ({
      ...mergeBoxDetail(current, detail),
      boxes: upsertBoxes(current.boxes, [detail]),
      overview: null,
      exportOptions: null,
    }));
    await publication.reconcile();
    assertCurrent();
  }

  async function qualifyBox(boxId: number, payload: BoxQualifyPayload) {
    const { apiPost, assertCurrent } = getOperationRequests(operationGeneration);
    const commit = beginBoxMutation(true);
    const detail = await apiPost<BoxDetail>(`/api/boxes/${boxId}/qualify/`, payload);
    assertCurrent();
    const publication = commit(boxId);

    publication.setData((current) => ({
      ...mergeBoxDetail(current, detail),
      boxes: upsertBoxes(current.boxes, [detail]),
      overview: null,
      exportOptions: null,
    }));
    await publication.reconcile();
    assertCurrent();
  }

  async function assignBoxInitialLocation(boxId: number, payload: BoxInitialLocationPayload) {
    const { apiPost, assertCurrent } = getOperationRequests(operationGeneration);
    const commit = beginBoxMutation(true);
    const detail = await apiPost<BoxDetail>(
      `/api/admin/box-inventory/${boxId}/assign-location/`,
      payload,
    );
    assertCurrent();
    const publication = commit(boxId);
    publication.setData((current) => ({
      ...mergeBoxDetail(current, detail),
      boxes: upsertBoxes(current.boxes, [detail]),
      overview: null,
      exportOptions: null,
    }));
    await publication.reconcile(async () => {
      assertCurrent();
      await refreshZones();
      assertCurrent();
    });
    assertCurrent();
  }

  async function qualifyBoxesBatch(payload: BoxInventoryBatchQualifyPayload) {
    const { apiPost, assertCurrent } = getOperationRequests(operationGeneration);
    const commit = beginBoxMutation(true);
    const result = await apiPost<BoxInventoryBatchResult>(
      '/api/admin/box-inventory/batch-qualify/',
      payload,
    );
    assertCurrent();
    const publications = result.successes.map((item) => {
      const publication = commit(item.box_id);
      publication.setData((current) => {
        const project = <T extends BoxItem>(box: T): T => ({
          ...box, status: item.status,
          thermal_zone: item.status === 'inactive' ? null : box.thermal_zone,
        });
        const detail = current.boxDetails[item.box_id];
        return {
          ...current,
          boxes: current.boxes.map((box) => box.id === item.box_id ? project(box) : box),
          boxDetails: detail ? { ...current.boxDetails, [item.box_id]: project(detail) } : current.boxDetails,
          overview: null,
          exportOptions: null,
        };
      });
      return publication;
    });
    await Promise.all(publications.map((publication) => publication.reconcile()));
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
    const { apiPost, assertCurrent } = getOperationRequests(operationGeneration);
    await apiPost<ThermalZone>('/api/thermal-zones/', payload);
    assertCurrent();
    zoneCollectionRevisionRef.current += 1;
    await refreshAfterMutation(async () => {
      assertCurrent();
      await refreshZones();
      assertCurrent();
    }, assertCurrent);
    assertCurrent();
  }

  async function updateThermalZone(zoneId: number, payload: ThermalZonePayload) {
    const { apiPatch, assertCurrent } = getOperationRequests(operationGeneration);
    const commit = beginZoneMutation(zoneId);
    await apiPatch<ThermalZone>(`/api/thermal-zones/${zoneId}/`, payload);
    assertCurrent();
    commit();
    await refreshAfterMutation(async () => {
      assertCurrent();
      await refreshZones();
      assertCurrent();
    }, assertCurrent);
    assertCurrent();
  }

  async function recordManualTemperature(zoneId: number, payload: ManualTemperaturePayload) {
    const { apiPost, setData, assertCurrent } = getOperationRequests(operationGeneration);
    const commit = beginZoneMutation(zoneId);
    const zone = await apiPost<ThermalZone>(`/api/thermal-zones/${zoneId}/temperature/`, payload);
    assertCurrent();
    const publication = commit();
    publication.setData((current) => ({
      ...current,
      zones: upsertThermalZones(current.zones, [zone]),
    }));
    // The write succeeded even if its full zone snapshot needs reconciliation.
    setData((current) => ({ ...current, overview: null, exportOptions: null }));
    await publication.reconcile();
    assertCurrent();
    return zone;
  }

  async function refreshZoneSalinityCapability(zoneId: number) {
    const { apiGet, setData, assertCurrent } = getOperationRequests(operationGeneration);
    const key = String(zoneId);
    const revision = zoneRevisionsRef.current.get(key) ?? 0;
    const readKey = `salinity:${zoneId}`;
    const readRevision = (zoneRevisionsRef.current.get(readKey) ?? 0) + 1;
    zoneRevisionsRef.current.set(readKey, readRevision);
    const collectionRead = zoneCollectionReadRef.current;
    const isCurrent = () => revision === (zoneRevisionsRef.current.get(key) ?? 0)
      && readRevision === zoneRevisionsRef.current.get(readKey)
      && collectionRead === zoneCollectionReadRef.current;
    const readings = await apiGet<ZoneSalinityMeasurement[]>(
      `/api/thermal-zones/${zoneId}/salinity/history/`,
    );
    assertCurrent();
    if (!isCurrent()) return;
    // A partial salinity projection must not cancel an independent queued zone payload.
    const capabilityKey = `capability:${zoneId}`;
    zoneRevisionsRef.current.set(capabilityKey, (zoneRevisionsRef.current.get(capabilityKey) ?? 0) + 1);
    zoneCollectionRevisionRef.current += 1;
    setData((current) => isCurrent() ? {
      ...current,
      zones: current.zones.map((zone) => zone.id === zoneId
        ? { ...zone, latest_salinity: readings[0] ?? null }
        : zone),
    } : current);
    assertCurrent();
  }

  async function recordManualSalinity(zoneId: number, payload: ManualSalinityPayload) {
    const { apiPost, assertCurrent } = getOperationRequests(operationGeneration);
    const commit = beginZoneMutation(zoneId);
    const zone = await apiPost<ThermalZone>(`/api/thermal-zones/${zoneId}/salinity/`, payload);
    assertCurrent();
    const publication = commit();
    publication.setData((current) => ({
      ...current,
      zones: upsertThermalZones(current.zones, [zone]),
    }));
    await publication.reconcile();
    assertCurrent();
    return zone;
  }

  async function updateManualSalinity(
    zoneId: number,
    measurementId: number,
    payload: ManualSalinityUpdatePayload,
  ) {
    const { apiPatch, assertCurrent } = getOperationRequests(operationGeneration);
    const commit = beginZoneMutation(zoneId);
    const zone = await apiPatch<ThermalZone>(
      `/api/thermal-zones/${zoneId}/salinity/${measurementId}/`,
      payload,
    );
    assertCurrent();
    const publication = commit();
    publication.setData((current) => ({
      ...current,
      zones: upsertThermalZones(current.zones, [zone]),
    }));
    await publication.reconcile();
    assertCurrent();
    return zone;
  }

  async function createProbe(payload: ProbePayload) {
    const { apiPost, assertCurrent } = getOperationRequests(operationGeneration);
    await apiPost<Probe>('/api/probes/', payload);
    assertCurrent();
    // Probes are nested inside the zone payload, so refresh the zones list.
    zoneCollectionRevisionRef.current += 1;
    await refreshAfterMutation(async () => {
      assertCurrent();
      await refreshZones();
      assertCurrent();
    }, assertCurrent);
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
                onCorrectMovement={correctMovement}
                onRefreshMovementState={refreshMovementState}
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
                onResolveBoxCreation={resolveBoxCreation}
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

function upsertThermalZones(currentZones: ThermalZone[], updatedZones: ThermalZone[]) {
  const updatedById = new Map(updatedZones.map((zone) => [zone.id, zone]));
  const mergedZones = currentZones.map((zone) => updatedById.get(zone.id) ?? zone);
  const existingIds = new Set(currentZones.map((zone) => zone.id));
  return [
    ...mergedZones,
    ...updatedZones.filter((zone) => !existingIds.has(zone.id)),
  ];
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
