import {
  lazy,
  Suspense,
  type FormEvent,
  type KeyboardEvent,
  type PointerEvent,
  type ReactNode,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from 'react';

import {
  ApiError,
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
import BiologicalTrendChart from './components/BiologicalTrendChart';
import BoxLifecycleModal, {
  type BoxLifecycleAction,
  type BoxLifecycleSubmission,
} from './components/BoxLifecycleModal';
import ChartWindowControls from './components/ChartWindowControls';
import { buildChartWindow } from './utils/chartWindow';

// A measurement handed over by the history so the box sheet can open with it
// already filled in.
type HistoryMeasurementPrefill = EditableMeasurement;
import type { BoxInsightTab } from './components/BoxInsights';
import { useConfirmAction, type ConfirmActionOptions } from './components/ConfirmActionModal';
import LoginPage from './components/LoginPage';
import PasswordResetPage from './components/PasswordResetPage';
import LoginNotice from './components/LoginNotice';
import MeasurementSaveButton from './components/MeasurementSaveButton';
import ModalPortal from './components/ModalPortal';
import MoveBoxModal from './components/MoveBoxModal';
import PageLoader from './components/PageLoader';
import PolypbaseIcon from './components/PolypbaseIcon';
import ProfileView from './components/ProfileView';
import QuickCountButtons from './components/QuickCountButtons';
import QuickStrainCreator, { type QuickCreatedStrain } from './components/QuickStrainCreator';
import QrLabel from './components/QrLabel';
import QrLabelModal from './components/QrLabelModal';
import SearchField from './components/SearchField';
import SubcultureModal from './components/SubcultureModal';
import TabletQrScanner from './components/TabletQrScanner';
import { useIsDesktopApp } from './hooks/useIsDesktopApp';
import type {
  BiologicalMeasurement,
  BoxActivatePayload,
  BoxAlert,
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
  ExportOptions,
  LineageGraph,
  Organization,
  OverviewBox,
  OverviewMeasurementPoint,
  OverviewResponse,
  PaginatedResponse,
  Probe,
  SubculturePayload,
  SubcultureResult,
  ThermalZone,
  UserProfile,
} from './types';
import type {
  BoxTransferPayload,
  BoxTransferResult,
  ManualTemperaturePayload,
  OrganizationPayload,
  ProbePayload,
  ThermalZonePayload,
} from './types/admin';
import { upsertBoxes } from './utils/boxCollection';
import { formatDisplayDate } from './utils/dateFormat';
import { getErrorMessage } from './utils/errors';
import {
  decrementDecimalValue,
  formatDecimalValue,
  incrementDecimalValue,
  parsePositiveDecimal,
} from './utils/stepValue';
import { triggerHaptic } from './utils/haptics';
import { buildQrLabelItem, getBoxQrImageUrl, getBoxScanUrl, type QrLabelItem } from './utils/qrLabels';

const AdminView = lazy(() => import('./components/AdminView'));
const BoxInsights = lazy(() => import('./components/BoxInsights'));
const MeasurementHistoryModal = lazy(() =>
  import('./components/BoxInsights').then((module) => ({ default: module.MeasurementHistoryModal })),
);
const ExportsView = lazy(() => import('./components/ExportsView'));
const LabelsView = lazy(() => import('./components/LabelsView'));
const ZoneDetailPage = lazy(() =>
  import('./components/ZonesView').then((module) => ({ default: module.ZoneDetailPage })),
);
const ZoneBoxesPage = lazy(() =>
  import('./components/ZonesView').then((module) => ({ default: module.ZoneBoxesPage })),
);
const ZonesView = lazy(() =>
  import('./components/ZonesView').then((module) => ({ default: module.ZonesView })),
);

// Boxes are filtered client-side, so the whole collection must be loaded.
// Kept well above the current box count to leave room for growth.
const BOX_LIST_LIMIT = 1000;

// Salinity (PSU) is read off a refractometer and lands on round values, so the
// +/- buttons move by 5 rather than by decimals. The field starts on the control
// salinity of the box's zone -- the environment it is known to sit in -- and the
// technician overrides it when the refractometer disagrees. It stays empty while
// the zone has no salinity set, rather than storing a value nobody measured.
const SALINITY_STEP = 5;

type TabId = 'pilotage' | 'overview' | 'zones' | 'exports' | 'labels' | 'admin' | 'profile';

type AppData = {
  boxes: BoxItem[];
  boxDetails: Record<number, BoxDetail>;
  zones: ThermalZone[];
  dashboard: Dashboard | null;
  overview: OverviewBox[] | null;
  exportOptions: ExportOptions | null;
  profile: UserProfile | null;
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
  zoneId?: number | null;
  zoneBoxes?: boolean;
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
  const [passwordReset, setPasswordReset] = useState(() => getPasswordResetRoute());
  const [search, setSearch] = useState('');
  const [recentBoxIds, setRecentBoxIds] = useState<number[]>([]);
  const [qrLabelSelection, setQrLabelSelection] = useState<QrLabelItem[]>([]);
  // Values carried over when the history sends the user to correct a
  // measurement; consumed once by the box sheet, then cleared.
  const [measurementPrefill, setMeasurementPrefill] = useState<HistoryMeasurementPrefill | null>(null);
  const [activeOrganizationId, setActiveOrganizationId] = useState<number | null>(() => getStoredActiveOrganizationId());
  const [needsOrganizationChoice, setNeedsOrganizationChoice] = useState(false);
  const [isOrganizationMenuOpen, setIsOrganizationMenuOpen] = useState(false);
  const lastRecordedBoxIdRef = useRef<number | null>(null);
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
  const [exportOptionsRequested, setExportOptionsRequested] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const activeTab = route.tab;
  const isBoxRoute = route.boxCode != null || route.boxId != null;
  const isZoneRoute = activeTab === 'zones' && route.zoneId != null;
  const language = getLanguage(data.profile);
  const t = useMemo(() => createTranslator(language), [language]);
  const { confirmAction, confirmActionModal } = useConfirmAction();
  const isDesktopApp = useIsDesktopApp();
  const hasAdminRole = userHasAdminRole(data.profile, activeOrganizationId);

  useEffect(() => {
    document.documentElement.lang = language;
  }, [language]);
  const canUseAdmin = hasAdminRole;
  const isExportOptionsLoading = (
    activeTab === 'exports' || exportOptionsRequested
  ) && data.exportOptions === null;
  const isOverviewLoading = activeTab === 'overview' && data.overview === null;
  const workspacePageKey = `${activeTab}-${route.boxCode ?? route.boxId ?? 'list'}-${route.zoneId ?? 'list'}-${route.zoneBoxes ? 'boxes' : 'detail'}-${route.adminSection ?? 'default'}`;
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

  async function fetchScopedData(profile: UserProfile, organizationId: number) {
    setActiveOrganizationContext(organizationId);
    const scopedProfile = setProfileActiveOrganization(profile, organizationId);
    const [boxes, zones, dashboard] = await Promise.all([
      apiGet<PaginatedResponse<BoxItem>>(`/api/boxes/?limit=${BOX_LIST_LIMIT}`),
      apiGet<PaginatedResponse<ThermalZone>>('/api/thermal-zones/?limit=80'),
      apiGet<Dashboard>('/api/dashboard/'),
    ]);

    return {
      boxes: boxes.results,
      boxDetails: {},
      zones: zones.results,
      dashboard,
      overview: null,
      exportOptions: null,
      profile: scopedProfile,
    };
  }

  async function chooseOrganization(organizationId: number) {
    if (!data.profile) return;

    if (!needsOrganizationChoice && organizationId === activeOrganizationId) {
      setIsOrganizationMenuOpen(false);
      return;
    }

    setIsOrganizationMenuOpen(false);
    setNeedsOrganizationChoice(false);
    setActiveOrganizationId(organizationId);
    setIsLoading(true);
    setError(null);
    setSearch('');
    setRecentBoxIds([]);

    if (isBoxRoute || isZoneRoute) {
      navigateTo({ tab: activeTab, boxCode: null, boxId: null, zoneId: null }, activeTab === 'zones' ? '/zones' : '/');
    }

    try {
      const nextData = await fetchScopedData(data.profile, organizationId);
      setData(nextData);
      setRecentBoxIds(buildRecentBoxIds(nextData.boxes, nextData.dashboard));
    } catch (requestError) {
      setError(getErrorMessage(requestError));
    } finally {
      setIsLoading(false);
    }
  }

  useEffect(() => {
    function syncRoute() {
      setRoute(getCurrentRoute());
      setIsLoginRoute(window.location.pathname === '/login');
    }

    window.addEventListener('popstate', syncRoute);
    return () => window.removeEventListener('popstate', syncRoute);
  }, []);

  useEffect(() => {
    if (isLoginRoute) {
      setError(null);
      setIsLoading(false);
      return;
    }

    let isActive = true;

    async function loadData() {
      try {
        setIsLoading(true);
        setError(null);

        const profile = await apiGet<UserProfile>('/api/profile/', { skipOrganizationContext: true });

        if (!isActive) return;

        setStoredInterfaceLanguage(profile.interface_language);

        const organizations = getSelectableOrganizations(profile);
        const preferredOrganizationId = activeOrganizationId ?? getStoredActiveOrganizationId();
        const resolvedOrganizationId = resolveActiveOrganizationId(profile, preferredOrganizationId);

        if (organizations.length > 1 && resolvedOrganizationId == null) {
          setActiveOrganizationContext(null);
          setActiveOrganizationId(null);
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

        setActiveOrganizationId(resolvedOrganizationId);
        setNeedsOrganizationChoice(false);

        const nextData = await fetchScopedData(profile, resolvedOrganizationId);
        if (!isActive) return;

        setData(nextData);
        setRecentBoxIds(buildRecentBoxIds(nextData.boxes, nextData.dashboard));
      } catch (requestError) {
        if (!isActive) return;

        if (requestError instanceof ApiError && [401, 403].includes(requestError.status)) {
          const requestedPath = `${window.location.pathname}${window.location.search}`;
          const loginPath = `/login?next=${encodeURIComponent(requestedPath)}`;
          window.history.replaceState(null, '', loginPath);
          setIsLoginRoute(true);
          setError(null);
          return;
        }

        setError(getErrorMessage(requestError));
      } finally {
        if (isActive) {
          setIsLoading(false);
        }
      }
    }

    loadData();

    return () => {
      isActive = false;
    };
  }, [isLoginRoute]);

  useEffect(() => {
    if (isLoginRoute || needsOrganizationChoice || activeOrganizationId == null || activeTab !== 'overview' || data.overview !== null) return;

    let isActive = true;

    async function loadOverview() {
      try {
        const overview = await apiGet<OverviewResponse>('/api/overview/active-boxes/?months=6');
        if (!isActive) return;
        setData((current) => ({ ...current, overview: overview.results }));
      } catch (requestError) {
        if (!isActive) return;
        setError(getErrorMessage(requestError));
      }
    }

    loadOverview();

    return () => {
      isActive = false;
    };
  }, [activeOrganizationId, activeTab, data.overview, isLoginRoute, needsOrganizationChoice]);

  const boxSearchIndex = useMemo(() => data.boxes.map((box) => ({
    box,
    searchableFields: [
      box.global_code,
      box.local_code,
      box.box_number,
      box.species.scientific_name,
      box.strain.code,
      box.thermal_zone?.name ?? '',
    ].map((field) => field.toLowerCase()),
  })), [data.boxes]);

  const filteredBoxes = useMemo(() => {
    const value = search.trim().toLowerCase();
    if (!value) return data.boxes;

    return boxSearchIndex
      .filter((entry) => entry.searchableFields.some((field) => field.includes(value)))
      .map((entry) => entry.box);
  }, [boxSearchIndex, data.boxes, search]);

  const selectedBoxId = useMemo(() => {
    if (route.boxId != null) return route.boxId;
    if (route.boxCode) {
      return data.boxes.find((box) => box.global_code === route.boxCode)?.id ?? null;
    }
    return null;
  }, [data.boxes, route.boxCode, route.boxId]);

  const selectedBox = useMemo(() => {
    if (selectedBoxId == null) return null;
    return data.boxes.find((box) => box.id === selectedBoxId) ?? null;
  }, [data.boxes, selectedBoxId]);
  const selectedZone = useMemo(() => {
    if (route.zoneId == null) return null;
    return data.zones.find((zone) => zone.id === route.zoneId) ?? null;
  }, [data.zones, route.zoneId]);

  const selectedBoxDetail = selectedBoxId != null ? data.boxDetails[selectedBoxId] ?? null : null;

  useEffect(() => {
    let isActive = true;

    async function loadBoxDetail(boxId: number) {
      try {
        setIsBoxLoading(true);
        const detail = await apiGet<BoxDetail>(`/api/boxes/${boxId}/`);
        if (!isActive) return;
        setData((current) => mergeBoxDetail(current, detail));
      } catch (requestError) {
        if (!isActive) return;
        setError(getErrorMessage(requestError));
      } finally {
        if (isActive) setIsBoxLoading(false);
      }
    }

    if (selectedBoxId != null && !selectedBoxDetail) {
      loadBoxDetail(selectedBoxId);
    }

    return () => {
      isActive = false;
    };
  }, [selectedBoxId, Boolean(selectedBoxDetail)]);

  useEffect(() => {
    if (selectedBoxId == null) {
      lastRecordedBoxIdRef.current = null;
      return;
    }

    setRecentBoxIds((currentIds) => [
      selectedBoxId,
      ...currentIds.filter((currentId) => currentId !== selectedBoxId),
    ].slice(0, 5));

    if (lastRecordedBoxIdRef.current === selectedBoxId) return;
    lastRecordedBoxIdRef.current = selectedBoxId;

    // Access tracking must never prevent someone from opening a box.
    void apiPost<void>(`/api/boxes/${selectedBoxId}/access/`, {}).catch(() => {
      if (lastRecordedBoxIdRef.current === selectedBoxId) {
        lastRecordedBoxIdRef.current = null;
      }
    });
  }, [selectedBoxId]);

  const recentBoxes = useMemo(() => {
    return recentBoxIds
      .map((boxId) => data.boxes.find((box) => box.id === boxId))
      .filter((box): box is BoxItem => Boolean(box))
      .slice(0, 5);
  }, [data.boxes, recentBoxIds]);

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
    const box = data.boxes.find((item) => item.id === boxId);
    if (box) {
      setSearch(box.global_code);
      navigateTo({ tab: 'pilotage', boxCode: box.global_code, boxId: null }, `/boxes/${encodeURIComponent(box.global_code)}`);
      return;
    }

    if (fallbackCode) {
      setSearch(fallbackCode);
      navigateTo({ tab: 'pilotage', boxCode: fallbackCode, boxId: null }, `/boxes/${encodeURIComponent(fallbackCode)}`);
    }

    setIsBoxLoading(true);
    void apiGet<BoxDetail>(`/api/boxes/${boxId}/`)
      .then((detail) => {
        setData((current) => mergeBoxDetail(current, detail));
        setSearch(detail.global_code);
        navigateTo({ tab: 'pilotage', boxCode: detail.global_code, boxId: null }, `/boxes/${encodeURIComponent(detail.global_code)}`);
      })
      .catch((requestError) => setError(getErrorMessage(requestError)))
      .finally(() => setIsBoxLoading(false));
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

  function openTab(tab: TabId) {
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

  useLayoutEffect(() => {
    if (activeTab === 'admin' && !isDesktopApp) {
      replaceRoute({ tab: 'pilotage', boxCode: null, boxId: null }, '/');
      return;
    }
    if (isLoading || !data.profile) return;
    if (
      availableTabs.includes(activeTab)
      || (activeTab === 'admin' && canUseAdmin && isDesktopApp)
    ) return;

    navigateTo({ tab: 'pilotage', boxCode: null, boxId: null }, '/');
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

    async function loadExportOptions() {
      try {
        const exportOptions = await apiGet<ExportOptions>('/api/exports/options/');
        if (!isActive) return;
        setData((current) => ({ ...current, exportOptions }));
        setExportOptionsRequested(false);
      } catch (requestError) {
        if (isActive) {
          setError(getErrorMessage(requestError));
          setExportOptionsRequested(false);
        }
      }
    }

    void loadExportOptions();

    return () => {
      isActive = false;
    };
  }, [activeOrganizationId, activeTab, data.exportOptions, exportOptionsRequested, isLoginRoute, needsOrganizationChoice]);

  function closeBoxPage() {
    navigateTo({ tab: 'pilotage', boxCode: null, boxId: null }, '/');
  }

  function closeZonePage() {
    navigateTo({ tab: 'zones', boxCode: null, boxId: null, zoneId: null }, '/zones');
  }

  function navigateTo(nextRoute: RouteState, path: string) {
    window.history.pushState(null, '', path);
    setRoute(nextRoute);
  }

  function replaceRoute(nextRoute: RouteState, path: string) {
    window.history.replaceState(null, '', path);
    setRoute(nextRoute);
  }

  async function updateLanguage(language: string) {
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

      setStoredInterfaceLanguage(profile.interface_language);
      setData((current) => ({
        ...current,
        profile,
      }));
    } catch (requestError) {
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
    window.history.replaceState(null, '', '/login');
    setRoute(getCurrentRoute());
    setError(null);
    setIsLoginRoute(true);
  }

  async function createMeasurement(boxId: number, payload: MeasurementPayload) {
    const created = await apiPost<BiologicalMeasurement>(`/api/boxes/${boxId}/measurements/`, payload);
    const detail = await apiGet<BoxDetail>(`/api/boxes/${boxId}/`);

    setData((current) => ({
      ...mergeBoxDetail(current, detail),
      overview: null,
    }));
    return created;
  }

  async function createBox(payload: BoxCreatePayload) {
    const detail = await apiPost<BoxDetail>('/api/boxes/', payload);

    setData((current) => ({
      ...mergeBoxDetail(current, detail),
      boxes: upsertBoxes(current.boxes, [detail]),
      overview: null,
      exportOptions: null,
    }));
    return detail;
  }

  async function updateMeasurement(boxId: number, measurementId: number, payload: MeasurementPayload) {
    const updated = await apiPatch<BiologicalMeasurement>(
      `/api/boxes/${boxId}/measurements/${measurementId}/`,
      payload,
    );
    const detail = await apiGet<BoxDetail>(`/api/boxes/${boxId}/`);

    setData((current) => ({
      ...mergeBoxDetail(current, detail),
      overview: null,
    }));
    return updated;
  }

  async function createSubculture(boxId: number, payload: SubculturePayload) {
    const result = await apiPost<SubcultureResult>(`/api/boxes/${boxId}/subcultures/`, payload);
    const detail = await apiGet<BoxDetail>(`/api/boxes/${boxId}/`);

    setData((current) => ({
      ...mergeBoxDetail(current, detail),
      boxes: upsertBoxes(current.boxes, [detail, ...result.children]),
      overview: null,
      exportOptions: null,
    }));
  }

  async function moveBox(boxId: number, payload: BoxMovePayload) {
    try {
      const detail = await apiPost<BoxDetail>(`/api/boxes/${boxId}/move/`, payload);
      const zones = await apiGet<PaginatedResponse<ThermalZone>>('/api/thermal-zones/?limit=80');

      setData((current) => ({
        ...mergeBoxDetail(current, detail),
        zones: zones.results,
        overview: null,
        exportOptions: null,
      }));
    } catch (requestError) {
      if (isBoxLocationChangedError(requestError)) {
        try {
          const [detail, zones] = await Promise.all([
            apiGet<BoxDetail>(`/api/boxes/${boxId}/`),
            apiGet<PaginatedResponse<ThermalZone>>('/api/thermal-zones/?limit=80'),
          ]);
          setData((current) => ({
            ...mergeBoxDetail(current, detail),
            zones: zones.results,
            overview: null,
            exportOptions: null,
          }));
        } catch {
          // Keep the original conflict visible if the targeted refresh also fails.
        }
      }
      throw requestError;
    }
  }

  async function deactivateBox(boxId: number, payload: BoxDeactivatePayload) {
    const detail = await apiPost<BoxDetail>(`/api/boxes/${boxId}/deactivate/`, payload);

    setData((current) => ({
      ...mergeBoxDetail(current, detail),
      boxes: upsertBoxes(current.boxes, [detail]),
      overview: null,
      exportOptions: null,
    }));
  }

  async function reactivateBox(boxId: number, payload: BoxActivatePayload) {
    const detail = await apiPost<BoxDetail>(`/api/boxes/${boxId}/activate/`, payload);

    setData((current) => ({
      ...mergeBoxDetail(current, detail),
      boxes: upsertBoxes(current.boxes, [detail]),
      overview: null,
      exportOptions: null,
    }));
  }

  async function qualifyBox(boxId: number, payload: BoxQualifyPayload) {
    const detail = await apiPost<BoxDetail>(`/api/boxes/${boxId}/qualify/`, payload);

    setData((current) => ({
      ...mergeBoxDetail(current, detail),
      boxes: upsertBoxes(current.boxes, [detail]),
      overview: null,
      exportOptions: null,
    }));
  }

  async function assignBoxInitialLocation(boxId: number, payload: BoxInitialLocationPayload) {
    const detail = await apiPost<BoxDetail>(
      `/api/admin/box-inventory/${boxId}/assign-location/`,
      payload,
    );
    const zones = await apiGet<PaginatedResponse<ThermalZone>>('/api/thermal-zones/?limit=80');

    setData((current) => ({
      ...mergeBoxDetail(current, detail),
      boxes: upsertBoxes(current.boxes, [detail]),
      zones: zones.results,
      overview: null,
      exportOptions: null,
    }));
  }

  async function qualifyBoxesBatch(payload: BoxInventoryBatchQualifyPayload) {
    const result = await apiPost<BoxInventoryBatchResult>(
      '/api/admin/box-inventory/batch-qualify/',
      payload,
    );
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
    return result;
  }

  async function resolveAlert(boxId: number, alertId: number) {
    await apiPost<{ id: number; resolved: boolean }>(`/api/alerts/${alertId}/resolve/`, {});
    const detail = await apiGet<BoxDetail>(`/api/boxes/${boxId}/`);
    setData((current) => ({
      ...mergeBoxDetail(current, detail),
      dashboard: null,
    }));
  }

  async function loadLineageGraph(boxId: number) {
    return apiGet<LineageGraph>(`/api/boxes/${boxId}/lineage/`);
  }

  async function createThermalZone(payload: ThermalZonePayload) {
    await apiPost<ThermalZone>('/api/thermal-zones/', payload);
    const zones = await apiGet<PaginatedResponse<ThermalZone>>('/api/thermal-zones/?limit=80');
    setData((current) => ({ ...current, zones: zones.results }));
  }

  async function updateThermalZone(zoneId: number, payload: ThermalZonePayload) {
    await apiPatch<ThermalZone>(`/api/thermal-zones/${zoneId}/`, payload);
    const zones = await apiGet<PaginatedResponse<ThermalZone>>('/api/thermal-zones/?limit=80');
    setData((current) => ({ ...current, zones: zones.results }));
  }

  async function recordManualTemperature(zoneId: number, payload: ManualTemperaturePayload) {
    const zone = await apiPost<ThermalZone>(`/api/thermal-zones/${zoneId}/temperature/`, payload);
    setData((current) => ({
      ...current,
      zones: upsertThermalZones(current.zones, [zone]),
      overview: null,
      exportOptions: null,
    }));
    return zone;
  }

  async function createProbe(payload: ProbePayload) {
    await apiPost<Probe>('/api/probes/', payload);
    // Probes are nested inside the zone payload, so refresh the zones list.
    const zones = await apiGet<PaginatedResponse<ThermalZone>>('/api/thermal-zones/?limit=80');
    setData((current) => ({ ...current, zones: zones.results }));
  }

  async function createOrganization(payload: OrganizationPayload) {
    await apiPost<Organization>('/api/organizations/', payload);
    // Refresh linked lists so the new organization is usable immediately.
    const exportOptions = await apiGet<ExportOptions>('/api/exports/options/');
    const profile = await apiGet<UserProfile>('/api/profile/');
    setData((current) => ({ ...current, exportOptions, profile }));
  }

  async function updateOrganization(organizationId: number, payload: OrganizationPayload) {
    await apiPatch<Organization>(`/api/organizations/${organizationId}/`, payload);
    const exportOptions = await apiGet<ExportOptions>('/api/exports/options/');
    const profile = await apiGet<UserProfile>('/api/profile/');
    setData((current) => ({ ...current, exportOptions, profile }));
  }

  async function deleteOrganization(organizationId: number) {
    await apiDelete<void>(`/api/organizations/${organizationId}/`);
    const exportOptions = await apiGet<ExportOptions>('/api/exports/options/');
    const profile = await apiGet<UserProfile>('/api/profile/');
    setData((current) => ({ ...current, exportOptions, profile }));
  }

  async function createBoxTransfer(payload: BoxTransferPayload) {
    return apiPost<BoxTransferResult>('/api/box-transfers/', payload);
  }

  function handleAuthenticated() {
    const nextPath = new URLSearchParams(window.location.search).get('next');
    const destination = nextPath?.startsWith('/') && !nextPath.startsWith('//')
      ? nextPath
      : '/';

    window.history.replaceState(null, '', destination);
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
          window.history.replaceState(null, '', '/login');
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

  const brandIdentity = (
    <>
      <span className="brand-mark" aria-hidden="true">
        <img src="/jellyfish.svg" alt="" />
      </span>
      <div>
        <p className="eyebrow">Polypbase</p>
        <strong>{brandOrganizationName}</strong>
      </div>
    </>
  );

  return (
    <main className="app-shell">
      <aside className="sidebar">
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
                const role = getMembershipRoleLabel(data.profile, organization.id);
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

        <nav className="tabbar" aria-label={t('mainNavigation')}>
          {availableTabs.map((tab) => (
            <button
              key={tab}
              className={tab === activeTab ? `tab tab-${tab} is-active` : `tab tab-${tab}`}
              type="button"
              onClick={() => openTab(tab)}
            >
              {t(tab)}
            </button>
          ))}
        </nav>
      </aside>

      <section className="workspace">
        {!isBoxRoute && !isZoneRoute ? (
          <header className="page-heading">
            <h1>{getTitle(activeTab, t)}</h1>
          </header>
        ) : null}

        {error ? (
          <LoginNotice
            labels={{
              action: t('loginAction'),
              title: t('loginRequired'),
            }}
            message={error}
          />
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
                box={selectedBoxDetail ?? selectedBox}
                boxes={data.boxes}
                zones={data.zones}
                profile={data.profile}
                language={language}
                qrLabelSelection={qrLabelSelection}
                isLoading={isLoading || isBoxLoading}
                onCreateMeasurement={createMeasurement}
                onUpdateMeasurement={updateMeasurement}
                onCreateSubculture={createSubculture}
                onMoveBox={moveBox}
                onDeactivateBox={deactivateBox}
                onReactivateBox={reactivateBox}
                onResolveAlert={resolveAlert}
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
                boxes={data.boxes}
                exportOptions={data.exportOptions}
                isLoading={isLoading}
                isOptionsLoading={isExportOptionsLoading}
                profile={data.profile}
                search={search}
                suggestions={filteredBoxes.slice(0, 5)}
                recentBoxes={recentBoxes}
                onCreateBox={createBox}
                onRequestOptions={() => setExportOptionsRequested(true)}
                confirmAction={confirmAction}
                onSearch={setSearch}
                onSelectBox={openBox}
                t={t}
              />
            )}

            {activeTab === 'overview' && (
              <OverviewView
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
                route.zoneBoxes ? (
                  <ZoneBoxesPage
                    boxes={data.boxes}
                    isLoading={isLoading}
                    language={language}
                    zone={selectedZone}
                    onBack={() => openZone(route.zoneId as number)}
                    onOpenBox={openBox}
                    t={t}
                  />
                ) : (
                  <ZoneDetailPage
                    boxes={data.boxes}
                    isLoading={isLoading}
                    zone={selectedZone}
                    canRecordManualTemperature={userCanWriteLabData(
                      data.profile,
                      selectedZone?.organization.id ?? -1,
                    )}
                    onBack={closeZonePage}
                    onOpenBoxes={openZoneBoxes}
                    onRecordManualTemperature={recordManualTemperature}
                    onOpenBox={openBox}
                    t={t}
                  />
                )
              ) : (
                <ZonesView
                  boxes={data.boxes}
                  isLoading={isLoading}
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
                activeSection={route.adminSection ?? 'accounts'}
                boxes={data.boxes}
                exportOptions={data.exportOptions}
                isLoading={isLoading}
                isOptionsLoading={isExportOptionsLoading}
                language={language}
                profile={data.profile}
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

            {activeTab === 'labels' && (
              <LabelsView
                boxes={data.boxes}
                isLoading={isLoading}
                labels={getLabelsViewLabels(t)}
                profile={data.profile}
                qrLabelSelection={qrLabelSelection}
                onAddQrLabel={addQrLabelToSelection}
                onClearQrLabelSelection={clearQrLabelSelection}
                onRemoveQrLabel={removeQrLabelFromSelection}
              />
            )}

            {activeTab === 'profile' && (
              <ProfileView
                isLoading={isLoading}
                labels={getProfileLabels(t)}
                profile={data.profile}
                activeOrganizationId={activeOrganizationId}
                canOpenAdmin={canUseAdmin && isDesktopApp}
                onSelectOrganization={(organizationId) => void chooseOrganization(organizationId)}
                onOpenAdmin={() => openTab('admin')}
                onOpenLabels={() => openTab('labels')}
                onLogout={logoutCurrentUser}
                onUpdateLanguage={updateLanguage}
              />
            )}
            </Suspense>
          </div>
        )}
        {confirmActionModal}
      </section>
    </main>
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
            const roleLabel = getMembershipRoleLabel(profile, organization.id) ?? t('profileFullAccess');
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

function PilotageView({
  boxes,
  exportOptions,
  isLoading,
  isOptionsLoading,
  profile,
  recentBoxes,
  search,
  suggestions,
  onCreateBox,
  onRequestOptions,
  confirmAction,
  t,
  onSearch,
  onSelectBox,
}: {
  boxes: BoxItem[];
  exportOptions: ExportOptions | null;
  isLoading: boolean;
  isOptionsLoading: boolean;
  profile: UserProfile | null;
  recentBoxes: BoxItem[];
  search: string;
  suggestions: BoxItem[];
  onCreateBox: (payload: BoxCreatePayload) => Promise<BoxDetail>;
  onRequestOptions: () => void;
  confirmAction: ConfirmAction;
  t: TFunction;
  onSearch: (value: string) => void;
  onSelectBox: (id: number) => void;
}) {
  const visibleSuggestions = search.trim() ? suggestions : [];
  const [tabletLookupMode, setTabletLookupMode] = useState<'qr' | 'search'>('qr');
  const [highlightedSuggestionIndex, setHighlightedSuggestionIndex] = useState(0);
  const canCreateBox = userCanCreateBoxes(profile);

  function selectFirstSuggestion() {
    const selectedSuggestion = visibleSuggestions[highlightedSuggestionIndex] ?? visibleSuggestions[0];
    if (selectedSuggestion) {
      onSelectBox(selectedSuggestion.id);
      onSearch(selectedSuggestion.global_code);
    }
  }

  function handleSearchChange(value: string) {
    setHighlightedSuggestionIndex(0);
    onSearch(value);
  }

  function handleSearchKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (!visibleSuggestions.length) return;

    if (event.key === 'ArrowDown') {
      event.preventDefault();
      setHighlightedSuggestionIndex((current) => (current + 1) % visibleSuggestions.length);
    } else if (event.key === 'ArrowUp') {
      event.preventDefault();
      setHighlightedSuggestionIndex((current) => (current - 1 + visibleSuggestions.length) % visibleSuggestions.length);
    } else if (event.key === 'Escape') {
      event.preventDefault();
      handleSearchChange('');
    }
  }

  const searchFieldProps = {
    activeDescendant: visibleSuggestions[highlightedSuggestionIndex]
      ? `box-suggestion-${visibleSuggestions[highlightedSuggestionIndex].id}`
      : undefined,
    controls: 'box-suggestions',
    expanded: visibleSuggestions.length > 0,
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
        <div className="desktop-search-panel">
          <SearchField {...searchFieldProps} />
        </div>

        <section className={`tablet-lookup-panel is-${tabletLookupMode}-mode`}>
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
                permission: t('qrScannerPermission'),
                secureContext: t('qrScannerSecureContext'),
                start: t('qrScannerStart'),
                stop: t('qrScannerStop'),
                unsupported: t('qrScannerUnsupported'),
              }}
              onSelectBox={onSelectBox}
            />
          ) : (
            <div className="tablet-manual-search">
              <SearchField {...searchFieldProps} />
            </div>
          )}
        </section>

        <div className="mobile-suggestion-slot">
          {tabletLookupMode === 'search' && visibleSuggestions.length > 0 ? (
            <SuggestionList
              boxes={visibleSuggestions}
              selectedBoxId={visibleSuggestions[highlightedSuggestionIndex]?.id ?? null}
              onSelectBox={onSelectBox}
              t={t}
            />
          ) : null}
        </div>

        <div className="desktop-suggestion-slot">
          {!isLoading && visibleSuggestions.length > 0 ? (
            <SuggestionList
              boxes={visibleSuggestions}
              selectedBoxId={visibleSuggestions[highlightedSuggestionIndex]?.id ?? null}
              onSelectBox={onSelectBox}
              t={t}
            />
          ) : null}
        </div>

        <RecentAccessList boxes={recentBoxes} onSelectBox={onSelectBox} t={t} />

        {canCreateBox ? (
          <CreateBoxPanel
            boxes={boxes}
            exportOptions={exportOptions}
            isOptionsLoading={isOptionsLoading}
            profile={profile}
            t={t}
            onCreateBox={onCreateBox}
            onRequestOptions={onRequestOptions}
            confirmAction={confirmAction}
            onSelectBox={onSelectBox}
            onSearch={onSearch}
          />
        ) : null}
      </div>

      <JellyfishPattern />
    </section>
  );
}

function CreateBoxPanel({
  boxes,
  exportOptions,
  isOptionsLoading,
  profile,
  confirmAction,
  onCreateBox,
  onRequestOptions,
  onSearch,
  onSelectBox,
  t,
}: {
  boxes: BoxItem[];
  exportOptions: ExportOptions | null;
  isOptionsLoading: boolean;
  profile: UserProfile | null;
  confirmAction: ConfirmAction;
  onCreateBox: (payload: BoxCreatePayload) => Promise<BoxDetail>;
  onRequestOptions: () => void;
  onSearch: (value: string) => void;
  onSelectBox: (id: number) => void;
  t: TFunction;
}) {
  const [isOpen, setIsOpen] = useState(false);
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
  const [isQuickStrainOpen, setIsQuickStrainOpen] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const strains = useMemo(() => {
    const options = exportOptions?.strains ?? [];
    return [...options, ...createdStrains.filter((created) =>
      !options.some((option) => option.id === created.id))];
  }, [createdStrains, exportOptions?.strains]);
  const normalizedStrainSearch = strainSearch.trim().toLocaleLowerCase('fr-FR');
  const filteredStrains = strains.filter((strain) => {
    if (!normalizedStrainSearch || strain.id === strainId) return true;
    return `${strain.species_name} ${strain.code}`.toLocaleLowerCase('fr-FR').includes(normalizedStrainSearch);
  });
  const selectedStrain = strains.find((strain) => strain.id === strainId) ?? null;
  const availableZones = (exportOptions?.zones ?? []).filter((zone) => zone.organization_id === organizationId);
  const selectedZone = availableZones.find((zone) => zone.id === zoneId) ?? null;
  const canSubmit = organizationId != null && strainId != null && zoneId != null && globalCode.trim() && boxNumber.trim();

  useEffect(() => {
    if (!strains.length || strainId != null) return;
    setStrainId(strains[0].id);
  }, [strainId, strains]);

  useEffect(() => {
    if (organizationId == null || !selectedStrain) return;
    const suggestion = buildNextBoxCode(boxes, selectedStrain, organizationId);
    setGlobalCode((current) => current.trim() ? current : suggestion.globalCode);
    setBoxNumber((current) => current.trim() ? current : suggestion.boxNumber);
  }, [boxes, organizationId, selectedStrain]);

  useEffect(() => {
    if (zoneId == null || availableZones.some((zone) => zone.id === zoneId)) return;
    setZoneId(null);
  }, [availableZones, zoneId]);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (isSaving || !canSubmit || organizationId == null || strainId == null || zoneId == null) return;

    if (!boxCodeMatchesBoxNumber(globalCode, boxNumber)) {
      setMessage(null);
      setError(t('createBoxNumberMismatch'));
      return;
    }

    const confirmed = await confirmAction({
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
    if (!confirmed) return;

    setIsSaving(true);
    setMessage(null);
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
      setMessage(t('createBoxSaved'));
      setGlobalCode('');
      setBoxNumber('');
      setNotes('');
      onSearch(created.global_code);
      onSelectBox(created.id);
    } catch (requestError) {
      if (requestError instanceof ApiError && requestError.status === 403) {
        setError(t('createBoxForbidden'));
      } else {
        setError(getErrorMessage(requestError));
      }
    } finally {
      setIsSaving(false);
    }
  }

  return (
    <section className="create-box-panel">
      <button
        className="create-box-toggle"
        type="button"
        onClick={() => {
          if (!isOpen && !exportOptions) onRequestOptions();
          setIsOpen((current) => !current);
        }}
      >
        <span aria-hidden="true">
          <PolypbaseIcon name={isOpen ? 'close' : 'plus'} size={18} />
        </span>
        <strong>{isOpen ? t('createBoxClose') : t('createBoxOpen')}</strong>
      </button>

      {isOpen ? (
        <form className="create-box-form" onSubmit={handleSubmit}>
          <div className="section-title">
            <h2>{t('createBoxTitle')}</h2>
          </div>

          {isOptionsLoading ? <p className="muted compact-text">{t('loading')}</p> : null}
          {!isOptionsLoading && !strains.length ? <p className="muted compact-text">{t('createBoxNoOptions')}</p> : null}

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
          {message ? <p className="inline-success">{message}</p> : null}
          {error ? <p className="inline-error">{error}</p> : null}
        </form>
      ) : null}

      {isQuickStrainOpen ? (
        <QuickStrainCreator
          t={t}
          onClose={() => setIsQuickStrainOpen(false)}
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
}

function RecentAccessList({
  boxes,
  onSelectBox,
  t,
}: {
  boxes: BoxItem[];
  onSelectBox: (id: number) => void;
  t: TFunction;
}) {
  if (!boxes.length) {
    return <p className="muted compact-text">{t('noRecentScans')}</p>;
  }

  return (
    <section className="recent-panel" aria-label="Derniers scans et recherches">
      <div className="section-title">
        <h2>{t('recentAccess')}</h2>
        <span>{t('scanSearch')}</span>
      </div>

      <div className="recent-strip">
        {boxes.map((box) => (
          <button
            key={box.id}
            className={box.active_alert_count > 0 ? 'has-alerts' : ''}
            type="button"
            onClick={() => onSelectBox(box.id)}
          >
            <span className="recent-box-heading">
              <strong>{box.global_code}</strong>
              {box.active_alert_count > 0 ? (
                <span className="recent-alert-count" aria-label={`${box.active_alert_count} ${t('activeAlerts')}`}>
                  {box.active_alert_count}
                </span>
              ) : null}
            </span>
            <small>{box.species.scientific_name}</small>
            <span className="recent-box-meta">
              <span>{box.thermal_zone?.name ?? t('noZone')}</span>
            </span>
          </button>
        ))}
      </div>
    </section>
  );
}

function OverviewView({
  boxes,
  isLoading,
  language,
  onSelectBox,
  onOpenZone,
  t,
}: {
  boxes: OverviewBox[] | null;
  isLoading: boolean;
  language: Language;
  onSelectBox: (id: number) => void;
  onOpenZone: (zoneId: number) => void;
  t: TFunction;
}) {
  const [speciesFilter, setSpeciesFilter] = useState('');
  const [zoneFilter, setZoneFilter] = useState('');
  const [sortOrder, setSortOrder] = useState<OverviewSortOrder>('oldest');
  const [focusFilter, setFocusFilter] = useState<OverviewFocusFilter>('all');
  const [query, setQuery] = useState('');
  const [visibleCount, setVisibleCount] = useState(12);
  const overviewBoxes = boxes ?? [];
  const noZoneLabel = t('noZone');
  const trackedEntries = useMemo<OverviewEntry[]>(
    () => overviewBoxes
      .map((box) => {
        const latest = getLastItem(box.measurements);
        const daysSince = latest ? getDaysSinceDate(latest.date) : null;
        const status = getWeeklyStatus(daysSince);
        const zoneName = box.thermal_zone?.name ?? noZoneLabel;

        return {
          box,
          latest,
          daysSince,
          status,
          zoneName,
          searchText: [box.global_code, box.species_name, box.strain_code, zoneName]
            .join(' ')
            .toLocaleLowerCase(),
        };
      }),
    [noZoneLabel, overviewBoxes],
  );
  const speciesOptions = useMemo(
    () => Array.from(new Set(trackedEntries.map((entry) => entry.box.species_name))).sort(),
    [trackedEntries],
  );
  const normalizedQuery = query.trim().toLocaleLowerCase();
  const filteredEntries = useMemo(
    () => trackedEntries.filter((entry) => {
      if (focusFilter === 'done' && entry.status !== 'ok') return false;
      if (focusFilter === 'due' && entry.status !== 'due') return false;
      if (focusFilter === 'soon' && entry.status !== 'soon') return false;
      if (speciesFilter && entry.box.species_name !== speciesFilter) return false;
      if (zoneFilter && entry.zoneName !== zoneFilter) return false;
      return !normalizedQuery || entry.searchText.includes(normalizedQuery);
    }).sort((first, second) => sortOverviewEntries(first, second, sortOrder)),
    [focusFilter, normalizedQuery, sortOrder, speciesFilter, trackedEntries, zoneFilter],
  );
  const visibleEntries = filteredEntries.slice(0, visibleCount);
  const doneCount = trackedEntries.filter((entry) => entry.status === 'ok').length;
  const dueCount = trackedEntries.filter((entry) => entry.status === 'due').length;
  const soonCount = trackedEntries.filter((entry) => entry.status === 'soon').length;
  const zoneSummaries = useMemo(() => buildOverviewZoneSummaries(trackedEntries), [trackedEntries]);
  const toggleFocusFilter = (targetFilter: Exclude<OverviewFocusFilter, 'all'>) => {
    setFocusFilter((currentFilter) => (currentFilter === targetFilter ? 'all' : targetFilter));
  };
  const toggleZoneFilter = (targetZoneName: string) => {
    setZoneFilter((currentZoneName) => (currentZoneName === targetZoneName ? '' : targetZoneName));
  };
  const hasCustomizedOverview = Boolean(
    focusFilter !== 'all'
    || speciesFilter
    || zoneFilter
    || normalizedQuery
    || sortOrder !== 'oldest',
  );
  const resetOverview = () => {
    setFocusFilter('all');
    setSpeciesFilter('');
    setZoneFilter('');
    setQuery('');
    setSortOrder('oldest');
  };

  useEffect(() => {
    setVisibleCount(12);
  }, [focusFilter, normalizedQuery, sortOrder, speciesFilter, zoneFilter]);

  if (isLoading) {
    return <PageLoader variant="overview" label={t('overviewTitle')} />;
  }

  return (
    <section className="overview-page">
      <header className="overview-intro overview-intro-priority">
        <div className="overview-summary-actions" aria-label={t('overviewFilters')}>
          <button
            type="button"
            aria-pressed={focusFilter === 'done'}
            aria-label={`${t('overviewRecordedBoxes')} : ${doneCount} ${t('boxes')}`}
            className={focusFilter === 'done' ? 'is-active is-done' : 'is-done'}
            onClick={() => toggleFocusFilter('done')}
          >
            <span>{t('overviewRecordedBoxes')}</span>
            <strong>{doneCount}</strong>
          </button>
          <button
            type="button"
            aria-pressed={focusFilter === 'soon'}
            aria-label={`${t('weeklyDueSoon')} : ${soonCount} ${t('boxes')}`}
            className={focusFilter === 'soon' ? 'is-active is-soon' : 'is-soon'}
            onClick={() => toggleFocusFilter('soon')}
          >
            <span>{t('weeklyDueSoon')}</span>
            <strong>{soonCount}</strong>
          </button>
          <button
            type="button"
            aria-pressed={focusFilter === 'due'}
            aria-label={`${t('weeklyDueNow')} : ${dueCount} ${t('boxes')}`}
            className={focusFilter === 'due' ? 'is-active is-due' : 'is-due'}
            onClick={() => toggleFocusFilter('due')}
          >
            <span>{t('weeklyDueNow')}</span>
            <strong>{dueCount}</strong>
          </button>
        </div>
      </header>

      {zoneSummaries.length ? (
        <section className="overview-zone-progress" aria-label={t('overviewByZone')}>
          <header>
            <h2>{t('overviewByZone')}</h2>
          </header>
          <div
            className="overview-zone-progress-list"
            data-layout={zoneSummaries.length > 5 ? 'many' : zoneSummaries.length}
          >
            {zoneSummaries.map((summary) => {
              const doneRatio = summary.done / Math.max(1, summary.total);
              const isZoneActive = zoneFilter === summary.zoneName;

              return (
                <button
                  type="button"
                  key={summary.zoneName}
                  aria-pressed={isZoneActive}
                  className={`overview-zone-progress-card ${summary.due ? 'is-due' : 'is-ok'} ${isZoneActive ? 'is-active' : ''}`}
                  onClick={() => toggleZoneFilter(summary.zoneName)}
                >
                  <span className="overview-zone-progress-copy">
                    <strong>{summary.zoneName}</strong>
                    <small>
                      {summary.due
                        ? `${summary.due} ${t('overviewZoneRemaining')}`
                        : t('overviewZoneUpToDate')}
                    </small>
                  </span>
                  <em className="overview-zone-progress-count">
                    <strong>{summary.done}</strong>
                    <span>/{summary.total}</span>
                  </em>
                  <i aria-hidden="true">
                    <b style={{ width: `${Math.round(doneRatio * 100)}%` }} />
                  </i>
                </button>
              );
            })}
          </div>
        </section>
      ) : null}

      <section className="overview-filters overview-filters-priority" aria-label={t('overviewFilters')}>
        <header className="overview-filters-header">
          <div>
            <strong>{t('overviewRefineList')}</strong>
            <span>
              <b>{visibleEntries.length}</b>/{filteredEntries.length} {t('overviewShowing')}
            </span>
          </div>
          {hasCustomizedOverview ? (
            <button type="button" onClick={resetOverview}>
              <span className="button-icon-label">
                <PolypbaseIcon name="reset-filter" size={15} />
                {t('overviewResetFilters')}
              </span>
            </button>
          ) : null}
        </header>
        <div className="overview-filter-fields">
          <label>
            <span>{t('overviewSearch')}</span>
            <input
              type="search"
              placeholder={t('overviewSearchPlaceholder')}
              value={query}
              onChange={(event) => setQuery(event.target.value)}
            />
          </label>
          <label>
            <span>{t('speciesLabel')}</span>
            <select value={speciesFilter} onChange={(event) => setSpeciesFilter(event.target.value)}>
              <option value="">{t('overviewFilterAllSpecies')}</option>
              {speciesOptions.map((speciesName) => (
                <option key={speciesName} value={speciesName}>{speciesName}</option>
              ))}
            </select>
          </label>
          <div className="overview-sort-control">
            <span>{t('overviewSort')}</span>
            <div className="overview-sort-buttons">
              <button
                type="button"
                className={sortOrder === 'oldest' ? 'is-active' : ''}
                onClick={() => setSortOrder('oldest')}
              >
                {t('overviewSortOldest')}
              </button>
              <button
                type="button"
                className={sortOrder === 'newest' ? 'is-active' : ''}
                onClick={() => setSortOrder('newest')}
              >
                {t('overviewSortNewest')}
              </button>
            </div>
          </div>
        </div>
      </section>

      {trackedEntries.length > 0 && filteredEntries.length > 0 ? (
        <div className="overview-list">
          <div className="overview-box-list">
            {visibleEntries.map((entry) => (
              <article
                className={`overview-box-summary overview-box-summary-priority is-${entry.status}`}
                key={entry.box.id}
              >
                <header>
                  <div
                    className="overview-reading-age"
                    aria-label={`${entry.daysSince == null ? t('weeklyNoRecentReading') : t('weeklyLastReading')} ${entry.daysSince ?? ''}`.trim()}
                  >
                    <strong>
                      {entry.daysSince == null
                        ? '—'
                        : `${entry.daysSince} ${t(entry.daysSince === 1 ? 'weeklyDay' : 'weeklyDays')}`}
                    </strong>
                  </div>
                  <button
                    type="button"
                    className="overview-box-identity"
                    onClick={() => onSelectBox(entry.box.id)}
                  >
                    <strong>{entry.box.global_code}</strong>
                    <span>{entry.box.species_name}</span>
                  </button>
                  <div className="overview-zone-context">
                    {entry.box.thermal_zone ? (
                      <button
                        type="button"
                        className="overview-zone-button"
                        onClick={() => onOpenZone(entry.box.thermal_zone!.id)}
                      >
                        {entry.zoneName}
                      </button>
                    ) : (
                      <small className="overview-zone-label">{entry.zoneName}</small>
                    )}
                  </div>
                </header>

                <OverviewMiniChart box={entry.box} language={language} t={t} />
              </article>
            ))}
          </div>
          {visibleEntries.length < filteredEntries.length ? (
            <button
              type="button"
              className="overview-show-more"
              onClick={() => setVisibleCount((count) => count + 12)}
            >
              {t('overviewShowMore')}
            </button>
          ) : null}
        </div>
      ) : (
        <p className="muted compact-text">
          {trackedEntries.length ? t('overviewEmpty') : t('weeklyNoActiveBoxes')}
        </p>
      )}
    </section>
  );
}

type WeeklyStatus = 'due' | 'soon' | 'ok';
type OverviewSortOrder = 'oldest' | 'newest';
type OverviewFocusFilter = 'all' | 'done' | 'due' | 'soon';
type OverviewEntry = {
  box: OverviewBox;
  latest: OverviewMeasurementPoint | undefined;
  daysSince: number | null;
  status: WeeklyStatus;
  zoneName: string;
  searchText: string;
};

type OverviewZoneSummary = {
  zoneName: string;
  total: number;
  done: number;
  due: number;
  soon: number;
};

function buildOverviewZoneSummaries(entries: OverviewEntry[]) {
  const summaries = new Map<string, OverviewZoneSummary>();

  entries.forEach((entry) => {
    const currentSummary = summaries.get(entry.zoneName) ?? {
      zoneName: entry.zoneName,
      total: 0,
      done: 0,
      due: 0,
      soon: 0,
    };

    currentSummary.total += 1;

    if (entry.status === 'due') {
      currentSummary.due += 1;
    } else if (entry.status === 'soon') {
      currentSummary.soon += 1;
    } else {
      currentSummary.done += 1;
    }

    summaries.set(entry.zoneName, currentSummary);
  });

  return Array.from(summaries.values()).sort((first, second) => (
    getOverviewZoneTemperature(first.zoneName) - getOverviewZoneTemperature(second.zoneName)
    || first.zoneName.localeCompare(second.zoneName)
  ));
}

function getOverviewZoneTemperature(zoneName: string) {
  const temperatureMatch = zoneName.match(/-?\d+(?:[.,]\d+)?/);
  if (!temperatureMatch) return Number.POSITIVE_INFINITY;

  const temperature = Number.parseFloat(temperatureMatch[0].replace(',', '.'));
  return Number.isFinite(temperature) ? temperature : Number.POSITIVE_INFINITY;
}

function sortOverviewEntries(first: OverviewEntry, second: OverviewEntry, order: OverviewSortOrder) {
  const firstDays = first.daysSince ?? 9999;
  const secondDays = second.daysSince ?? 9999;
  const dayDiff = order === 'oldest' ? secondDays - firstDays : firstDays - secondDays;
  return dayDiff || first.box.global_code.localeCompare(second.box.global_code);
}

function getWeeklyStatus(daysSince: number | null): WeeklyStatus {
  if (daysSince === null || daysSince >= 7) return 'due';
  if (daysSince >= 5) return 'soon';
  return 'ok';
}

function getDaysSinceDate(date: string) {
  const parsedDate = new Date(`${date}T00:00:00`);
  if (Number.isNaN(parsedDate.getTime())) return null;
  const today = new Date();
  const todayStart = new Date(today.getFullYear(), today.getMonth(), today.getDate());
  return Math.max(0, Math.floor((todayStart.getTime() - parsedDate.getTime()) / 86_400_000));
}

function OverviewMiniChart({
  box,
  language,
  t,
}: {
  box: OverviewBox;
  language: Language;
  t: TFunction;
}) {
  const [windowOffset, setWindowOffset] = useState(0);
  const orderedMeasurements = useMemo(
    () => [...box.measurements].sort((left, right) => left.date.localeCompare(right.date)),
    [box.measurements],
  );
  const sourceDates = useMemo(
    () => [
      ...orderedMeasurements.map((measurement) => measurement.date),
      ...(box.locations ?? []).flatMap((location) => [location.starts_at, location.ends_at]),
    ],
    [box.locations, orderedMeasurements],
  );
  const chartWindow = useMemo(
    () => buildChartWindow(sourceDates, windowOffset, 3),
    [sourceDates, windowOffset],
  );

  useEffect(() => {
    if (windowOffset !== chartWindow.offset) setWindowOffset(chartWindow.offset);
  }, [chartWindow.offset, windowOffset]);

  useEffect(() => setWindowOffset(0), [box.id]);

  const latestDate = orderedMeasurements[orderedMeasurements.length - 1]?.date;
  const canMoveOverviewWindow = (months: number) => {
    const targetOffset = Math.max(
      0,
      Math.min(chartWindow.maxOffset, chartWindow.offset + months),
    );
    if (targetOffset === chartWindow.offset) return false;

    const targetWindow = buildChartWindow(sourceDates, targetOffset, 3);
    return orderedMeasurements.some((measurement) => (
      measurement.date >= targetWindow.startDate && measurement.date <= targetWindow.endDate
    ));
  };

  if (!latestDate) {
    return (
      <div className="overview-chart overview-chart-empty">
        <strong>{t('overviewChartTitle')}</strong>
        <span>{t('overviewNoHistory')}</span>
      </div>
    );
  }

  const locations = (box.locations?.length
    ? box.locations.map((location) => ({
      id: location.id,
      name: location.thermal_zone.name,
      startsAt: location.starts_at,
      endsAt: location.ends_at,
      endDateUnknown: location.end_date_unknown,
    }))
    : box.thermal_zone
      ? [{
        id: `current-${box.thermal_zone.id}`,
        name: box.thermal_zone.name,
        startsAt: chartWindow.startDate,
        endsAt: chartWindow.endDate,
      }]
      : []
  );

  return (
    <div className="overview-mini-chart">
      <ChartWindowControls
        canMove={canMoveOverviewWindow}
        compact
        endDate={chartWindow.endDate}
        hasNewerWindow={chartWindow.hasNewerWindow}
        hasOlderWindow={chartWindow.hasOlderWindow}
        language={language}
        longStep={3}
        onMove={(months) => {
          if (!canMoveOverviewWindow(months)) return;
          setWindowOffset(Math.max(
            0,
            Math.min(chartWindow.maxOffset, chartWindow.offset + months),
          ));
        }}
        startDate={chartWindow.startDate}
        windowMonths={3}
      />
      <BiologicalTrendChart
        compact
        detailDisplay="inline"
        startDate={chartWindow.startDate}
        endDate={chartWindow.endDate}
        measurements={orderedMeasurements.map((point) => ({
          id: point.date,
          date: point.date,
          polypCount: point.polyp_count,
          ephyraeCount: point.ephyrae_count,
          salinity: point.salinity_psu,
        }))}
        locations={locations}
        selectionScope={box.id}
        labels={{
          chartTitle: t('overviewChartTitle'),
          empty: t('overviewNoHistory'),
          ephyrae: t('ephyraeFull'),
          location: language === 'fr' ? 'Emplacement' : 'Location',
          missingReading: t('chartMissingReading'),
          movement: t('movementEvent'),
          polyps: t('polyps'),
          salinity: 'PSU',
          selectedReading: language === 'fr' ? 'Relevé sélectionné' : 'Selected reading',
        }}
      />
    </div>
  );
}

function getLastItem<T>(items: T[]): T | undefined {
  return items.length ? items[items.length - 1] : undefined;
}

function SuggestionList({
  boxes,
  selectedBoxId,
  onSelectBox,
  t,
}: {
  boxes: BoxItem[];
  selectedBoxId: number | null;
  onSelectBox: (id: number) => void;
  t: TFunction;
}) {
  return (
    <section className="suggestion-panel" aria-label="Suggestions de boîtes">
      <div className="section-title">
        <h2>{t('suggestions')}</h2>
        <span>{boxes.length}</span>
      </div>

      <div className="suggestion-list" id="box-suggestions" role="listbox">
        {boxes.map((box) => (
          <button
            key={box.id}
            id={`box-suggestion-${box.id}`}
            className={selectedBoxId === box.id ? 'suggestion-row is-selected' : 'suggestion-row'}
            type="button"
            role="option"
            aria-selected={selectedBoxId === box.id}
            onClick={() => onSelectBox(box.id)}
          >
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
              {box.active_alert_count > 0 ? (
                <small className="suggestion-alert-count">
                  {box.active_alert_count} {t('activeAlerts')}
                </small>
              ) : null}
            </span>
            <span className="suggestion-chevron" aria-hidden="true">›</span>
          </button>
        ))}
      </div>
    </section>
  );
}

const jellyfishPatternItems = [
  { left: '4%', top: '8%', width: 24, opacity: 0.07, rotation: -8 },
  { left: '23%', top: '2%', width: 34, opacity: 0.08, rotation: 4 },
  { left: '48%', top: '9%', width: 27, opacity: 0.06, rotation: -2 },
  { left: '76%', top: '4%', width: 38, opacity: 0.08, rotation: 7 },
  { left: '11%', top: '24%', width: 42, opacity: 0.09, rotation: 3 },
  { left: '36%', top: '21%', width: 28, opacity: 0.06, rotation: -7 },
  { left: '61%', top: '27%', width: 34, opacity: 0.075, rotation: 5 },
  { left: '86%', top: '22%', width: 25, opacity: 0.055, rotation: -4 },
  { left: '2%', top: '47%', width: 31, opacity: 0.06, rotation: 6 },
  { left: '22%', top: '43%', width: 25, opacity: 0.055, rotation: -5 },
  { left: '45%', top: '49%', width: 48, opacity: 0.095, rotation: 2 },
  { left: '70%', top: '44%', width: 31, opacity: 0.065, rotation: -8 },
  { left: '92%', top: '50%', width: 39, opacity: 0.075, rotation: 6 },
  { left: '9%', top: '70%', width: 29, opacity: 0.055, rotation: -2 },
  { left: '31%', top: '66%', width: 37, opacity: 0.08, rotation: 8 },
  { left: '58%', top: '73%', width: 26, opacity: 0.055, rotation: -6 },
  { left: '81%', top: '69%', width: 44, opacity: 0.09, rotation: 3 },
  { left: '15%', top: '88%', width: 41, opacity: 0.075, rotation: 5 },
  { left: '52%', top: '91%', width: 30, opacity: 0.055, rotation: -4 },
  { left: '74%', top: '88%', width: 28, opacity: 0.055, rotation: 7 },
];

function JellyfishPattern() {
  return (
    <div className="jellyfish-pattern" aria-hidden="true">
      {jellyfishPatternItems.map((item, index) => (
        <img
          key={index}
          src="/jellyfish.svg"
          alt=""
          style={{
            left: item.left,
            top: item.top,
            width: `${item.width}px`,
            opacity: item.opacity,
            transform: `rotate(${item.rotation}deg)`,
          }}
        />
      ))}
    </div>
  );
}

function BoxPage({
  box,
  boxes,
  zones,
  profile,
  language,
  qrLabelSelection,
  isLoading,
  onCreateMeasurement,
  onUpdateMeasurement,
  onCreateSubculture,
  onMoveBox,
  onDeactivateBox,
  onReactivateBox,
  onResolveAlert,
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
  boxes: BoxItem[];
  zones: ThermalZone[];
  profile: UserProfile | null;
  language: Language;
  qrLabelSelection: QrLabelItem[];
  isLoading: boolean;
  onCreateMeasurement: (boxId: number, payload: MeasurementPayload) => Promise<BiologicalMeasurement>;
  onUpdateMeasurement: (
    boxId: number,
    measurementId: number,
    payload: MeasurementPayload,
  ) => Promise<BiologicalMeasurement>;
  onCreateSubculture: (boxId: number, payload: SubculturePayload) => Promise<void>;
  onMoveBox: (boxId: number, payload: BoxMovePayload) => Promise<void>;
  onDeactivateBox: (boxId: number, payload: BoxDeactivatePayload) => Promise<void>;
  onReactivateBox: (boxId: number, payload: BoxActivatePayload) => Promise<void>;
  onResolveAlert: (boxId: number, alertId: number) => Promise<void>;
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
  const defaultSalinity = getDefaultMeasurementSalinity(box, zones);
  const [form, setForm] = useState(() => getInitialMeasurementForm(defaultSalinity));
  const [isSaving, setIsSaving] = useState(false);
  const isDesktopApp = useIsDesktopApp();
  const [isHistoryOpen, setIsHistoryOpen] = useState(false);
  const [lineageGraph, setLineageGraph] = useState<LineageGraph | null>(null);
  const [isLineageGraphLoading, setIsLineageGraphLoading] = useState(false);
  const [lineageGraphError, setLineageGraphError] = useState<string | null>(null);
  const [isMoveOpen, setIsMoveOpen] = useState(false);
  const [isSavingMove, setIsSavingMove] = useState(false);
  const [isChangingBoxStatus, setIsChangingBoxStatus] = useState(false);
  const [lifecycleAction, setLifecycleAction] = useState<BoxLifecycleAction | null>(null);
  const [moveError, setMoveError] = useState<string | null>(null);
  const [moveMessage, setMoveMessage] = useState<string | null>(null);
  const [statusError, setStatusError] = useState<string | null>(null);
  const [statusMessage, setStatusMessage] = useState<string | null>(null);
  const [isSubcultureOpen, setIsSubcultureOpen] = useState(false);
  const [isSavingSubculture, setIsSavingSubculture] = useState(false);
  const [isQrLabelOpen, setIsQrLabelOpen] = useState(false);
  const [isChecksOpen, setIsChecksOpen] = useState(false);
  const [resolvingAlertId, setResolvingAlertId] = useState<number | null>(null);
  const [alertResolveError, setAlertResolveError] = useState<string | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [saveMessage, setSaveMessage] = useState<string | null>(null);
  // Measurement saved during this visit (enables the "Modifier" button) and the
  // one currently being edited (null = creating a new measurement).
  const [lastSavedMeasurementId, setLastSavedMeasurementId] = useState<number | null>(null);
  const [editingMeasurementId, setEditingMeasurementId] = useState<number | null>(null);
  // Set while the form holds values brought over from the history, so the save
  // button can say "correct" rather than "record": the technician is fixing an
  // existing measurement, not adding one.
  const [isCorrectingFromHistory, setIsCorrectingFromHistory] = useState(false);
  const [subcultureError, setSubcultureError] = useState<string | null>(null);
  const [subcultureMessage, setSubcultureMessage] = useState<string | null>(null);
  const [activeInsightTab, setActiveInsightTab] = useState<BoxInsightTab>('measurements');

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
    setMoveMessage(null);
    setStatusError(null);
    setStatusMessage(null);
    setIsSubcultureOpen(false);
    setIsQrLabelOpen(false);
    setIsChecksOpen(false);
    setSaveError(null);
    setSaveMessage(null);
    setLastSavedMeasurementId(null);
    setEditingMeasurementId(null);
    setSubcultureError(null);
    setSubcultureMessage(null);
    setActiveInsightTab('measurements');
    setIsCorrectingFromHistory(false);
  }, [box?.id]);

  // The history sends the user here to correct a measurement: fill the form
  // with what was recorded. Declared after the reset above so it runs last and
  // its values survive the box change. Keeping the original date matters: the
  // API stores one measurement per box and date, so saving overwrites that
  // measurement instead of adding a second one.
  useEffect(() => {
    if (!measurementPrefill || !box || measurementPrefill.box_id !== box.id) return;

    setForm({
      measuredOn: measurementPrefill.measured_on,
      polypCount: String(measurementPrefill.polyp_count),
      ephyraeCount: String(measurementPrefill.ephyrae_count),
      salinity: measurementPrefill.salinity_psu,
      notes: measurementPrefill.notes,
    });
    setIsCorrectingFromHistory(true);
    onMeasurementPrefillConsumed();
  }, [measurementPrefill, box?.id]);

  // The zones can finish loading after the sheet is open, and the box can be
  // moved to another zone: seed the salinity once its control value is known.
  // Only an untouched field is filled, so this never overwrites a reading the
  // technician typed, nor the value loaded when correcting a past measurement.
  useEffect(() => {
    if (editingMeasurementId != null || !defaultSalinity) return;
    setForm((current) => (current.salinity ? current : { ...current, salinity: defaultSalinity }));
  }, [defaultSalinity, editingMeasurementId]);

  useEffect(() => {
    if (activeInsightTab !== 'lineage' || !box?.id || lineageGraph || isLineageGraphLoading) {
      return;
    }

    let ignoreResult = false;
    setIsLineageGraphLoading(true);
    setLineageGraphError(null);

    void onLoadLineageGraph(box.id)
      .then((graph) => {
        if (!ignoreResult) {
          setLineageGraph(graph);
        }
      })
      .catch((requestError) => {
        if (!ignoreResult) {
          setLineageGraphError(getErrorMessage(requestError));
        }
      })
      .finally(() => {
        if (!ignoreResult) {
          setIsLineageGraphLoading(false);
        }
      });

    return () => {
      ignoreResult = true;
    };
  }, [activeInsightTab, box?.id, lineageGraph]);

  if (isLoading) {
    return (
      <PageLoader variant="box" label={t('boxSheet')} />
    );
  }

  if (!box) {
    return (
      <section className="box-page empty-box-state">
        <button className="text-button" type="button" onClick={onBack}>
          {t('backToPilotage')}
        </button>
        <h2>{t('boxNotFound')}</h2>
        <p>{t('boxNotFoundText')}</p>
      </section>
    );
  }

  const measurements = getMeasurementHistory(box);
  const lastComment = getLatestComment(measurements, box);
  const sortedMeasurements = [...measurements].sort(
    (first, second) =>
      new Date(second.measured_on).getTime() - new Date(first.measured_on).getTime(),
  );

  const latestMeasurement = sortedMeasurements[0];
  const previousMeasurement = sortedMeasurements[1];

  const polypDropDetected =
    latestMeasurement &&
    previousMeasurement &&
    latestMeasurement.polyp_count < previousMeasurement.polyp_count;

  const polypDropCount = polypDropDetected
    ? previousMeasurement.polyp_count - latestMeasurement.polyp_count
    : 0;

  const activeAlerts = 'active_alerts' in box ? box.active_alerts : [];
  const hasPolypDropAlert = activeAlerts.some((alert) => {
    const message = alert.message.toLocaleLowerCase('fr-FR');
    return alert.alert_type === 'biological' && message.includes('polype');
  });
  const showLocalPolypDrop = Boolean(polypDropDetected) && !hasPolypDropAlert;
  const checkCount = activeAlerts.length + Number(showLocalPolypDrop);

  const qr = 'qr_image_url' in box
    ? { imageUrl: getBoxQrImageUrl(box), scanUrl: getBoxScanUrl(box) }
    : null;
  const lineage = getBoxLineage(box);
  const currentZone = getCurrentThermalZone(box, zones);
  const displayDate = getBoxDisplayDate(box, measurements);
  const statusPresentation = getBoxStatusPresentation(box.status, language);
  const canWriteLabData = userCanWriteLabData(profile, box.organization.id);
  const canChangeBoxStatus = userCanArchiveBox(profile, box.organization.id);
  const isBoxActive = box.status === 'active';
  const canShowStatusButton = canChangeBoxStatus && ['active', 'inactive'].includes(box.status);

  async function saveMeasurement(): Promise<boolean> {
    if (!box || isSaving) return false;

    if (!form.polypCount.trim() || !form.ephyraeCount.trim()) {
      setSaveMessage(null);
      setSaveError(t('measurementCountsRequired'));
      return false;
    }

    setIsSaving(true);
    setSaveError(null);
    setSaveMessage(null);

    const payload: MeasurementPayload = {
      measured_on: form.measuredOn,
      polyp_count: parsePositiveInteger(form.polypCount),
      ephyrae_count: parsePositiveInteger(form.ephyraeCount),
      salinity_psu: form.salinity.trim() || null,
      notes: form.notes.trim(),
    };

    try {
      if (editingMeasurementId != null) {
        await onUpdateMeasurement(box.id, editingMeasurementId, payload);
        setLastSavedMeasurementId(editingMeasurementId);
        setEditingMeasurementId(null);
        setSaveMessage(t('measurementUpdated'));
      } else {
        const created = await onCreateMeasurement(box.id, payload);
        setLastSavedMeasurementId(created.id);
        setSaveMessage(t('measurementSaved'));
      }
      setForm(getInitialMeasurementForm(defaultSalinity));
      // The correction is done: the emptied form is a new measurement again.
      setIsCorrectingFromHistory(false);
      triggerHaptic([12, 28, 12]);
      return true;
    } catch (requestError) {
      setSaveError(getMeasurementSaveError(requestError, t));
      return false;
    } finally {
      setIsSaving(false);
    }
  }

  // Load the just-saved measurement back into the form to correct it (mobile).
  function startEditingLastMeasurement() {
    if (lastSavedMeasurementId == null) return;
    const target = measurements.find((measurement) => measurement.id === lastSavedMeasurementId);
    if (!target) return;
    setForm({
      measuredOn: target.measured_on,
      polypCount: String(target.polyp_count),
      ephyraeCount: String(target.ephyrae_count),
      salinity: target.salinity_psu ?? '',
      notes: target.notes ?? '',
    });
    setEditingMeasurementId(lastSavedMeasurementId);
    setSaveError(null);
    setSaveMessage(null);
  }

  function cancelEditingMeasurement() {
    setEditingMeasurementId(null);
    setForm(getInitialMeasurementForm(defaultSalinity));
    setSaveError(null);
    setSaveMessage(null);
  }

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!isDesktopApp) return;
    void saveMeasurement();
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
    if (!confirmed) return;

    setIsSavingSubculture(true);
    setSubcultureError(null);
    setSubcultureMessage(null);

    try {
      await onCreateSubculture(box.id, payload);
      setIsSubcultureOpen(false);
      setSubcultureMessage(t('subcultureSaved'));
    } catch (requestError) {
      setSubcultureError(getSubcultureSaveError(requestError, t));
    } finally {
      setIsSavingSubculture(false);
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
    if (!confirmed) return;

    setIsSavingMove(true);
    setMoveError(null);
    setMoveMessage(null);

    try {
      await onMoveBox(box.id, payload);
      setIsMoveOpen(false);
      setMoveMessage(t('moveSaved'));
    } catch (requestError) {
      setMoveError(getMoveSaveError(requestError, t));
    } finally {
      setIsSavingMove(false);
    }
  }

  async function handleLifecycleSubmit(submission: BoxLifecycleSubmission) {
    if (!box || isChangingBoxStatus) return;
    setIsChangingBoxStatus(true);
    setStatusError(null);
    setStatusMessage(null);

    try {
      if (submission.action === 'reactivate') {
        await onReactivateBox(box.id, submission.payload);
        setStatusMessage(t('boxActivated'));
      } else if (submission.action === 'deactivate') {
        await onDeactivateBox(box.id, submission.payload);
        setStatusMessage(t('boxArchived'));
      }
      setLifecycleAction(null);
    } catch (requestError) {
      if (requestError instanceof ApiError && requestError.status === 403) {
        setStatusError(t(submission.action === 'reactivate' ? 'boxActivateForbidden' : 'boxArchiveForbidden'));
      } else {
        setStatusError(getErrorMessage(requestError));
      }
    } finally {
      setIsChangingBoxStatus(false);
    }
  }

  async function handleResolveAlert(alert: BoxAlert) {
    if (!box || resolvingAlertId !== null) return;
    const confirmed = await confirmAction({
      title: t('alertResolveConfirmTitle'),
      message: t('alertResolveConfirmMessage'),
      confirmLabel: t('alertResolveAction'),
      cancelLabel: t('confirmCancel'),
      details: [{ label: t('boxChecksButton'), value: alert.message }],
    });
    if (!confirmed) return;
    setResolvingAlertId(alert.id);
    setAlertResolveError(null);
    try {
      await onResolveAlert(box.id, alert.id);
    } catch (requestError) {
      setAlertResolveError(getErrorMessage(requestError) || t('alertResolveError'));
    } finally {
      setResolvingAlertId(null);
    }
  }

  async function handleLoadLineageGraph() {
    if (!box) return;

    setLineageGraph(null);
    setIsLineageGraphLoading(true);
    setLineageGraphError(null);

    try {
      setLineageGraph(await onLoadLineageGraph(box.id));
    } catch (requestError) {
      setLineageGraphError(getErrorMessage(requestError));
    } finally {
      setIsLineageGraphLoading(false);
    }
  }

  return (
    <section className={canWriteLabData ? 'box-page' : 'box-page is-read-only'}>
      <button className="text-button" type="button" onClick={onBack}>
        {t('backToPilotage')}
      </button>

      <header className={`entity-header entity-header--box box-sheet-hero is-status-${statusPresentation.tone}`}>
        <div className="entity-header__identity box-sheet-identity">
          <div>
            <p className="box-page-label">{t('boxSheet')}</p>
            <div className="box-code-line">
              <h2>{box.global_code}</h2>
            </div>
            <p className="box-species-name">{box.species.scientific_name}</p>
          </div>

          <div className="box-small-facts">
            <InfoPill
              label={t(displayDate.labelKey)}
              value={displayDate.date ? formatDisplayDate(displayDate.date) : t('noDate')}
            />
          </div>
        </div>

        <div className="box-header-tools">
          {qr && canWriteLabData ? (
            <button
              className="box-hero-qr"
              type="button"
              title={qr.scanUrl}
              onClick={() => setIsQrLabelOpen(true)}
            >
              <QrLabel
                altLabel={t('qrCode')}
                item={buildQrLabelItem(box, qr.imageUrl)}
                showMetadata={false}
                variant="trigger"
              />
            </button>
          ) : null}

          <button
            className={checkCount > 0 ? 'entity-header__alert box-alert-trigger' : 'entity-header__alert box-alert-trigger is-empty'}
            type="button"
            aria-label={`${t('boxChecksButton')} (${checkCount})`}
            title={`${t('boxChecksButton')} (${checkCount})`}
            onClick={() => setIsChecksOpen(true)}
          >
            <BellIcon />
            <strong>{checkCount}</strong>
          </button>
        </div>

        <div className="entity-header__summary box-zone-summary">
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
          <InfoPill label={t('zoneSalinityShort')} value={formatSalinity(currentZone?.salinity_psu)} />
          {/* Salinity recorded for this box (the last measurement's PSU), shown
              right after the zone reference so both are read side by side. */}
          <InfoPill label={t('boxSalinityShort')} value={formatSalinity(box.latest_salinity_psu)} />
          <InfoPill label={t('temperatureShort')} value={formatTemperature(currentZone?.latest_temperature?.average_temperature_c)} />
        </div>

        <div className="entity-header__actions box-action-stack">
          {canWriteLabData ? (
            <>
              <button className="move-trigger" type="button" onClick={() => setIsMoveOpen(true)}>
                {t('moveAction')}
              </button>
              <button className="subculture-trigger" type="button" onClick={() => setIsSubcultureOpen(true)}>
                {t('subcultureAction')}
              </button>
            </>
          ) : null}

          {canShowStatusButton ? (
            <button
              className={isBoxActive ? 'archive-box-trigger' : 'activate-box-trigger'}
              type="button"
              disabled={isChangingBoxStatus}
              onClick={() => {
                setStatusError(null);
                setStatusMessage(null);
                setLifecycleAction(isBoxActive ? 'deactivate' : 'reactivate');
              }}
            >
              <span className="button-icon-label">
                {!isChangingBoxStatus ? (
                  <PolypbaseIcon name={isBoxActive ? 'archive' : 'restore'} size={17} />
                ) : null}
                {isChangingBoxStatus ? t('saving') : t(isBoxActive ? 'boxArchiveAction' : 'boxActivateAction')}
              </span>
            </button>
          ) : null}
        </div>
      </header>

      {subcultureMessage ? (
        <p className="inline-success box-action-feedback">{subcultureMessage}</p>
      ) : null}
      {moveMessage ? (
        <p className="inline-success box-action-feedback">{moveMessage}</p>
      ) : null}
      {statusMessage ? (
        <p className="inline-success box-action-feedback">{statusMessage}</p>
      ) : null}
      {statusError ? (
        <p className="inline-error box-action-feedback">{statusError}</p>
      ) : null}

      <div className={`box-page-grid${!isBoxActive ? ' is-inactive' : ''}`}>
        {isBoxActive ? (
        <section className={saveMessage ? 'last-reading-card is-fresh' : 'last-reading-card'}>
          <div>
            <h2>{t('lastMeasurement')}</h2>
            <span>{box.latest_measurement ? formatDisplayDate(box.latest_measurement.measured_on) : t('noDate')}</span>
          </div>
          <Metric label={t('polyps')} value={String(box.latest_measurement?.polyp_count ?? '-')} />
          <Metric label={t('ephyraeFull')} value={String(box.latest_measurement?.ephyrae_count ?? '-')} />

          <div className="last-reading-comment">
            <small>{t('lastComment')}</small>
            <p>{lastComment || t('noComment')}</p>
          </div>
        </section>
        ) : null}

        {isBoxActive && canWriteLabData ? (
          <section className="box-section measurement-form-section">
            <form className="fake-form" onSubmit={handleSubmit}>
              <div className="section-title">
                <h2>{t('newMeasurement')}</h2>
                <span>{formatDisplayDate(form.measuredOn)}</span>
              </div>

              <div className="measurement-entry-grid">
                <label className="measurement-date-field">
                  {t('measurementDate')}
                  <input
                    required
                    type="date"
                    value={form.measuredOn}
                    onChange={(event) => setForm((current) => ({ ...current, measuredOn: event.target.value }))}
                  />
                </label>

                <label className="measurement-count-field measurement-polyp-field">
                  <span className="measurement-field-label">{t('polyps')}</span>
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
                </label>

                <label className="measurement-count-field measurement-ephyrae-field">
                  <span className="measurement-field-label">{t('ephyraeFull')}</span>
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
                </label>

                <label className="measurement-salinity-field">
                  <span className="measurement-field-label">{t('salinityFull')}</span>
                  <div className="count-stepper count-stepper-salinity">
                    <StepperButton
                      aria-label={`${t('salinityFull')} -${SALINITY_STEP}`}
                      onStep={() => setForm((current) => ({
                        ...current,
                        salinity: decrementDecimalValue(current.salinity, SALINITY_STEP),
                      }))}
                    >
                      <PolypbaseIcon name="minus" size={18} />
                    </StepperButton>
                    {/* No step attribute: browsers reject off-step values, and
                        the field must accept whatever the refractometer reads
                        (32 with a zone control at 30). The buttons step by 5. */}
                    <input
                      min="0"
                      inputMode="decimal"
                      placeholder={String(SALINITY_STEP)}
                      type="number"
                      value={form.salinity}
                      onChange={(event) => setForm((current) => ({ ...current, salinity: event.target.value }))}
                    />
                    <StepperButton
                      aria-label={`${t('salinityFull')} +${SALINITY_STEP}`}
                      onStep={() => setForm((current) => ({
                        ...current,
                        salinity: incrementDecimalValue(current.salinity, SALINITY_STEP),
                      }))}
                    >
                      <PolypbaseIcon name="plus" size={18} />
                    </StepperButton>
                  </div>
                </label>
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
                  isSaving={isSaving}
                  isSuccess={Boolean(saveMessage)}
                  labels={{
                    hold: editingMeasurementId != null ? t('holdToUpdate') : t('holdToSave'),
                    save: isCorrectingFromHistory
                      ? t('correctMeasurement')
                      : editingMeasurementId != null
                        ? t('saveMeasurementEdit')
                        : t('saveMeasurement'),
                    saved: saveMessage || t('measurementSaved'),
                    saving: t('saving'),
                  }}
                  onSave={saveMeasurement}
                />

                {lastSavedMeasurementId != null || editingMeasurementId != null ? (
                  <div className={editingMeasurementId != null ? 'measurement-edit-actions is-editing' : 'measurement-edit-actions'}>
                    {editingMeasurementId != null ? (
                      <>
                        <span className="measurement-edit-hint">{t('measurementEditing')}</span>
                        <button type="button" className="measurement-edit-cancel" onClick={cancelEditingMeasurement}>
                          {t('cancelEdit')}
                        </button>
                      </>
                    ) : (
                      <>
                        <span className="measurement-edit-hint">{t('editLastMeasurementHelp')}</span>
                        <button
                          type="button"
                          className="measurement-edit-button"
                          onClick={startEditingLastMeasurement}
                        >
                          <span className="button-icon-label">
                            <PolypbaseIcon name="edit" size={16} />
                            {t('editLastMeasurement')}
                          </span>
                        </button>
                      </>
                    )}
                  </div>
                ) : null}
              </div>
            </form>
          </section>
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
            measurements={measurements}
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
            existingBoxes={boxes}
            zones={zones}
            language={language}
            isSaving={isSavingSubculture}
            error={subcultureError}
            onClose={() => setIsSubcultureOpen(false)}
            onSubmit={handleSubculture}
          />
        ) : null}

        {isChecksOpen ? (
          <BoxChecksModal
            activeAlerts={activeAlerts}
            polypDropCount={polypDropCount}
            polypDropDetected={showLocalPolypDrop}
            canResolve={canWriteLabData}
            resolvingAlertId={resolvingAlertId}
            resolveError={alertResolveError}
            t={t}
            onClose={() => setIsChecksOpen(false)}
            onResolve={handleResolveAlert}
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
              help: t('qrLabelHelp'),
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

function BellIcon() {
  return (
    <svg className="bell-icon" viewBox="0 0 24 24" aria-hidden="true" focusable="false">
      <path
        d="M12 21a2.6 2.6 0 0 0 2.45-1.75h-4.9A2.6 2.6 0 0 0 12 21Z"
        fill="currentColor"
      />
      <path
        d="M18 10.15c0-3.05-1.66-5.18-4.22-5.88A1.84 1.84 0 0 0 12 3a1.84 1.84 0 0 0-1.78 1.27C7.66 4.97 6 7.1 6 10.15v2.45c0 1.1-.43 2.14-1.2 2.92l-.52.52a.9.9 0 0 0 .64 1.54h14.16a.9.9 0 0 0 .64-1.54l-.52-.52A4.13 4.13 0 0 1 18 12.6v-2.45Z"
        fill="none"
        stroke="currentColor"
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth="1.8"
      />
    </svg>
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

  function clearRepeat() {
    if (delayRef.current != null) {
      window.clearTimeout(delayRef.current);
      delayRef.current = null;
    }

    if (intervalRef.current != null) {
      window.clearInterval(intervalRef.current);
      intervalRef.current = null;
    }
  }

  useEffect(() => clearRepeat, []);

  function startRepeat(event: PointerEvent<HTMLButtonElement>) {
    event.preventDefault();
    clearRepeat();
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
      className="count-stepper-button"
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

function BoxChecksModal({
  activeAlerts,
  polypDropCount,
  polypDropDetected,
  canResolve,
  resolvingAlertId,
  resolveError,
  t,
  onClose,
  onResolve,
}: {
  activeAlerts: BoxAlert[];
  polypDropCount: number;
  polypDropDetected: boolean;
  canResolve: boolean;
  resolvingAlertId: number | null;
  resolveError: string | null;
  t: TFunction;
  onClose: () => void;
  onResolve: (alert: BoxAlert) => Promise<void>;
}) {
  const hasAlerts = activeAlerts.length > 0 || polypDropDetected;

  return (
    <ModalPortal>
      <div className="modal-backdrop" role="presentation" onMouseDown={onClose}>
      <section
        className="box-checks-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="box-checks-title"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <header className="box-checks-heading">
          <div>
            <h2 id="box-checks-title">{t('boxChecksTitle')}</h2>
          </div>
          <button type="button" aria-label={t('close')} onClick={onClose}>
            <PolypbaseIcon name="close" size={19} />
          </button>
        </header>

        <div className="box-checks-list">
          {activeAlerts.map((alert) => (
            <article className={`box-check-item is-${getAlertTone(alert.level)}`} key={alert.id}>
              <span className="check-severity">
                {getAlertLevelLabel(alert.level, t)}
              </span>
              <div>
                <small>{formatDisplayDate(alert.created_at)}</small>
                <strong>{getAlertTypeLabel(alert.alert_type, t)}</strong>
                <p>{alert.message}</p>
                {canResolve && alert.alert_type !== 'biological' ? (
                  <button
                    className="alert-resolve-button"
                    type="button"
                    disabled={resolvingAlertId !== null}
                    onClick={() => void onResolve(alert)}
                  >
                    {resolvingAlertId === alert.id ? t('saving') : t('alertResolveAction')}
                  </button>
                ) : null}
              </div>
            </article>
          ))}

          {resolveError ? <p className="inline-error">{resolveError}</p> : null}

          {polypDropDetected ? (
            <article className="box-check-item is-medium">
              <span className="check-severity">{t('checkImportanceMedium')}</span>
              <div>
                <small>{t('detectedSignal')}</small>
                <strong>{t('polypDropAdviceTitle')}</strong>
                <p>{polypDropCount} {t('polypDropAdviceText')}</p>
              </div>
              <div>
                <small>{t('suggestedAction')}</small>
                <p>{t('polypDropAdviceAction')}</p>
              </div>
            </article>
          ) : null}

          {!hasAlerts ? (
            <article className="box-check-empty">
              <span className="check-empty-icon">
                <BellIcon />
              </span>
              <div>
                <strong>{t('boxChecksEmptyTitle')}</strong>
                <p>{t('boxChecksEmptyText')}</p>
              </div>
            </article>
          ) : null}

        </div>
        </section>
      </div>
    </ModalPortal>
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
    historyCountLabel: t('historyCountLabel'),
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

function getAlertTone(level: string) {
  if (level === 'critical') return 'high';
  if (level === 'warning') return 'medium';
  return 'low';
}

function getAlertLevelLabel(level: string, t: TFunction) {
  if (level === 'critical') return t('checkImportanceHigh');
  if (level === 'warning') return t('checkImportanceMedium');
  return t('checkImportanceInfo');
}

function getAlertTypeLabel(alertType: string, t: TFunction) {
  if (alertType === 'temperature') return t('temperature');
  if (alertType === 'salinity') return t('salinityFull');
  if (alertType === 'subculture') return t('subcultureEvent');
  return t('detectedSignal');
}

function getProfileLabels(t: TFunction) {
  return {
    account: t('account'),
    logoutAction: t('logoutAction'),
    logoutError: t('logoutError'),
    profileEmail: t('profileEmail'),
    profileLanguage: t('profileLanguage'),
    profileAdminAction: t('profileAdminAction'),
    profileAdminTitle: t('profileAdminTitle'),
    profileAdminText: t('profileAdminText'),
    profileMemberships: t('profileMemberships'),
    profileNoEmail: t('profileNoEmail'),
    profileNoMembership: t('profileNoMembership'),
    profileAllOrganizationsAccess: t('profileAllOrganizationsAccess'),
    profileLabelsMobileText: t('profileLabelsMobileText'),
    profilePreferences: t('profilePreferences'),
    profileActiveOrganization: t('profileActiveOrganization'),
    profileActiveOrganizationHelp: t('profileActiveOrganizationHelp'),
    profileDefaultOrganization: t('profileDefaultOrganization'),
    profileFullAccess: t('profileFullAccess'),
    roleDescAdmin: t('roleDescAdmin'),
    roleDescTechnician: t('roleDescTechnician'),
    roleDescViewer: t('roleDescViewer'),
    saving: t('saving'),
    labelsTitle: t('labelsTitle'),
  };
}

function getLabelsViewLabels(t: TFunction) {
  return {
    allZones: t('zoneFilterAll'),
    noZone: t('noZone'),
    qrLabelAddToSelection: t('qrLabelAddToSelection'),
    qrLabelClearSelection: t('qrLabelClearSelection'),
    qrLabelNoEligibleBoxes: t('qrLabelNoEligibleBoxes'),
    qrLabelPage: t('qrLabelPage'),
    qrLabelPerPage: t('qrLabelPerPage'),
    qrLabelPreview: t('qrLabelPreview'),
    qrLabelPrintSelection: t('qrLabelPrintSelection'),
    qrLabelSearchTitle: t('qrLabelSearchTitle'),
    qrLabelSelectionEmpty: t('qrLabelSelectionEmpty'),
    qrLabelSelectionFilter: t('qrLabelSelectionFilter'),
    qrLabelSelectionHelp: t('qrLabelSelectionHelp'),
    qrLabelSelectionSearch: t('qrLabelSelectionSearch'),
    qrLabelSelectionTitle: t('qrLabelSelectionTitle'),
    qrLabelSearchPlaceholder: t('adminPrintLabelsSearchPlaceholder'),
    qrLabelSettingsTitle: t('qrLabelSettingsTitle'),
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

function getInitialMeasurementForm(defaultSalinity = '') {
  return {
    measuredOn: getTodayDateValue(),
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
 * known to sit in. Normalised through formatDecimalValue so the API's "30.00"
 * reaches the field as "30" and the +/- buttons keep working from there.
 */
function getZoneSalinityValue(box: BoxItem | BoxDetail | null, zones: ThermalZone[]) {
  if (!box) return '';
  const salinity = getCurrentThermalZone(box, zones)?.salinity_psu;
  if (salinity === null || salinity === undefined || salinity === '') return '';
  return formatDecimalValue(parsePositiveDecimal(salinity));
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
    return formatDecimalValue(parsePositiveDecimal(boxSalinity));
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

function parsePositiveInteger(value: string) {
  const parsedValue = Number.parseInt(value, 10);
  return Number.isFinite(parsedValue) && parsedValue >= 0 ? parsedValue : 0;
}

function incrementCountValue(currentValue: string, increment: number) {
  return String(parsePositiveInteger(currentValue) + increment);
}

function decrementCountValue(currentValue: string) {
  return String(Math.max(parsePositiveInteger(currentValue) - 1, 0));
}

function getMeasurementSaveError(error: unknown, t: TFunction) {
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

function buildRecentBoxIds(boxes: BoxItem[], dashboard: Dashboard) {
  const idsFromAccesses = dashboard.recent_accesses
    .map((access) => boxes.find((box) => box.global_code === access.object_id)?.id)
    .filter((boxId): boxId is number => Boolean(boxId));

  return uniqueNumbers(idsFromAccesses).slice(0, 5);
}

function uniqueNumbers(values: number[]) {
  return values.filter((value, index) => values.indexOf(value) === index);
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

function getMembershipRoleLabel(profile: UserProfile | null, organizationId: number | null) {
  if (!profile || organizationId == null) return null;
  return profile.memberships.find((membership) => membership.organization.id === organizationId)?.role_label ?? null;
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
  strain: ExportOptions['strains'][number],
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

/**
 * Reads /reset-password/<uid>/<token>, the address carried by the email sent by
 * the "forgot password" flow. Returns null on any other page. The two parts are
 * handed to the API untouched; it is what decides whether they are still valid.
 */
function getPasswordResetRoute(): { uid: string; token: string } | null {
  const segments = window.location.pathname.split('/').filter(Boolean);
  if (segments.length !== 3 || segments[0] !== 'reset-password') return null;

  const [, uid, token] = segments;
  if (!uid || !token) return null;

  return { uid: decodeURIComponent(uid), token: decodeURIComponent(token) };
}

function getCurrentRoute(): RouteState {
  const path = window.location.pathname;

  // Stable QR scan target: /bac/<id> opens the box sheet directly.
  const scanMatch = path.match(/^\/bac\/(\d+)\/?$/);
  if (scanMatch) {
    return { tab: 'pilotage', boxCode: null, boxId: Number(scanMatch[1]) };
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
