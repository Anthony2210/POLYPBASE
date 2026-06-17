import { type FormEvent, useEffect, useMemo, useState } from 'react';

import { ApiError, apiGet, apiPatch, apiPost } from './api/client';
import { getBoxStatusPresentation } from './boxStatus';
import ExportsView from './components/ExportsView';
import LineageModal from './components/LineageModal';
import MoveBoxModal from './components/MoveBoxModal';
import SubcultureModal from './components/SubcultureModal';
import type {
  BiologicalMeasurement,
  BoxDetail,
  BoxItem,
  BoxLineage,
  BoxMovePayload,
  Dashboard,
  ExportOptions,
  LineageGraph,
  PaginatedResponse,
  SubculturePayload,
  SubcultureResult,
  ThermalZone,
  UserProfile,
} from './types';

type TabId = 'pilotage' | 'zones' | 'exports' | 'admin' | 'profile';
type BoxInsightTab = 'measurements' | 'observations' | 'lineage';

type AppData = {
  boxes: BoxItem[];
  boxDetails: Record<number, BoxDetail>;
  zones: ThermalZone[];
  dashboard: Dashboard | null;
  exportOptions: ExportOptions | null;
  profile: UserProfile | null;
};

type MeasurementPayload = {
  measured_on: string;
  polyp_count: number;
  ephyrae_count: number;
  notes: string;
};

type RouteState = {
  tab: TabId;
  boxCode: string | null;
  boxId: number | null;
};

const translations = {
  fr: {
    account: 'Compte',
    backToPilotage: 'Retour au pilotage',
    boxNotFound: 'Boîte introuvable',
    boxNotFoundText: 'Cette boîte n’existe pas dans les données chargées.',
    boxSheet: 'Fiche de la boîte',
    boxes: 'Boîtes',
    close: 'Fermer',
    ephyrae: 'Éphyr.',
    ephyraeFull: 'Éphyrules',
    exports: 'Exports',
    exportsTitle: 'Exporter les données',
    admin: 'Administration',
    adminTitle: 'Administration',
    adminOpenDjango: 'Ouvrir Django admin',
    adminUsers: 'Comptes et rôles',
    adminOrganizations: 'Structures partenaires',
    adminReferences: 'Espèces et souches',
    adminEnvironment: 'Zones et sondes',
    adminRights: 'Réservé admin',
    profileRoles: 'Rôles',
    historyButton: 'Voir relevés',
    analysisTabLineage: 'Parenté',
    analysisTabMeasurements: 'Relevés',
    analysisTabObservations: 'Observations',
    boxLocalCode: 'Code local',
    boxStrain: 'Souche',
    chartEmpty: 'Pas assez de relevés pour tracer une tendance.',
    chartTitle: 'Évolution des relevés',
    createdOn: 'Créée le',
    lastComment: 'Dernier commentaire',
    lastMeasurement: 'Dernier relevé',
    laboratoryTracking: 'Suivi laboratoire',
    lineageAction: 'Parenté',
    loginAction: 'Se connecter',
    loginRequired: 'Connexion requise',
    measurementDate: 'Date du relevé',
    measurementForbidden: 'Ce compte ne peut pas créer de relevé.',
    measurementHistory: 'Historique des relevés',
    measurementSaved: 'Relevé enregistré',
    moveAction: 'Déplacer',
    moveForbidden: 'Ce compte ne peut pas déplacer de boîte.',
    moveSaved: 'Déplacement enregistré',
    newMeasurement: 'Nouveau relevé',
    noComment: 'Aucun commentaire récent pour cette boîte.',
    noDate: 'aucune date',
    noMeasurementHistory: 'Aucun relevé pour cette boîte.',
    noRecentScans: 'Aucun scan récent pour l’instant.',
    noZone: 'Sans zone',
    observation: 'Observation',
    observationPlaceholder: 'Note rapide pour le laboratoire',
    pilotage: 'Pilotage',
    pilotageTitle: 'Pilotage labo',
    polyps: 'Polypes',
    parents: 'Parents',
    children: 'Enfants',
    probes: 'Sondes',
    profile: 'Profil',
    profileTitle: 'Mon profil',
    prototype: 'prototype',
    qrCode: 'QR code',
    qrDownload: 'Télécharger (SVG)',
    qrScanHint: 'Scannez pour ouvrir cette fiche',
    recentAccess: 'Derniers accès',
    saveMeasurement: 'Enregistrer le relevé',
    saving: 'Enregistrement...',
    scanSearch: 'scan / recherche',
    searchOrScan: 'Recherche ou scan',
    searchPlaceholder: 'Code boîte, espèce, souche',
    suggestions: 'Suggestions',
    subcultureAction: 'Repiquer',
    subcultureForbidden: 'Ce compte ne peut pas créer de repiquage.',
    subcultureSaved: 'Repiquage enregistré',
    temperatureShort: 'Temp.',
    targetTemperature: 'Consigne',
    salinityShort: 'Sal.',
    zones: 'Zones',
    zonesTitle: 'Zones thermiques',
  },
  en: {
    account: 'Account',
    backToPilotage: 'Back to pilotage',
    boxNotFound: 'Box not found',
    boxNotFoundText: 'This box does not exist in the loaded data.',
    boxSheet: 'Box sheet',
    boxes: 'Boxes',
    close: 'Close',
    ephyrae: 'Ephyrae',
    ephyraeFull: 'Ephyrae',
    exports: 'Exports',
    exportsTitle: 'Export data',
    admin: 'Administration',
    adminTitle: 'Administration',
    adminOpenDjango: 'Open Django admin',
    adminUsers: 'Accounts and roles',
    adminOrganizations: 'Partner organizations',
    adminReferences: 'Species and strains',
    adminEnvironment: 'Zones and probes',
    adminRights: 'Admin only',
    profileRoles: 'Roles',
    historyButton: 'View records',
    analysisTabLineage: 'Lineage',
    analysisTabMeasurements: 'Measurements',
    analysisTabObservations: 'Observations',
    boxLocalCode: 'Local code',
    boxStrain: 'Strain',
    chartEmpty: 'Not enough measurements to draw a trend.',
    chartTitle: 'Measurement trend',
    createdOn: 'Created on',
    lastComment: 'Last comment',
    lastMeasurement: 'Last measurement',
    laboratoryTracking: 'Lab tracking',
    lineageAction: 'Lineage',
    loginAction: 'Sign in',
    loginRequired: 'Sign-in required',
    measurementDate: 'Measurement date',
    measurementForbidden: 'This account cannot create measurements.',
    measurementHistory: 'Measurement history',
    measurementSaved: 'Measurement saved',
    moveAction: 'Move',
    moveForbidden: 'This account cannot move boxes.',
    moveSaved: 'Movement saved',
    newMeasurement: 'New measurement',
    noComment: 'No recent comment for this box.',
    noDate: 'no date',
    noMeasurementHistory: 'No measurement for this box.',
    noRecentScans: 'No recent scan yet.',
    noZone: 'No zone',
    observation: 'Observation',
    observationPlaceholder: 'Quick lab note',
    pilotage: 'Pilotage',
    pilotageTitle: 'Lab pilotage',
    polyps: 'Polyps',
    parents: 'Parents',
    children: 'Children',
    probes: 'Probes',
    profile: 'Profile',
    profileTitle: 'My profile',
    prototype: 'prototype',
    qrCode: 'QR code',
    qrDownload: 'Download (SVG)',
    qrScanHint: 'Scan to open this sheet',
    recentAccess: 'Recent access',
    saveMeasurement: 'Save measurement',
    saving: 'Saving...',
    scanSearch: 'scan / search',
    searchOrScan: 'Search or scan',
    searchPlaceholder: 'Box code, species, strain',
    suggestions: 'Suggestions',
    subcultureAction: 'Subculture',
    subcultureForbidden: 'This account cannot create subculture events.',
    subcultureSaved: 'Subculture created',
    temperatureShort: 'Temp.',
    targetTemperature: 'Target',
    salinityShort: 'Sal.',
    zones: 'Zones',
    zonesTitle: 'Thermal zones',
  },
};

type Language = keyof typeof translations;
type TranslationKey = keyof typeof translations.fr;
type TFunction = (key: TranslationKey) => string;

const labTabs: TabId[] = ['pilotage', 'zones', 'profile'];
const desktopTabs: TabId[] = ['pilotage', 'zones', 'exports', 'profile'];

export default function App() {
  const [route, setRoute] = useState<RouteState>(() => getCurrentRoute());
  const [search, setSearch] = useState('');
  const [recentBoxIds, setRecentBoxIds] = useState<number[]>([]);
  const [data, setData] = useState<AppData>({
    boxes: [],
    boxDetails: {},
    zones: [],
    dashboard: null,
    exportOptions: null,
    profile: null,
  });
  const [isLoading, setIsLoading] = useState(true);
  const [isBoxLoading, setIsBoxLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const activeTab = route.tab;
  const isBoxRoute = route.boxCode != null || route.boxId != null;
  const language = getLanguage(data.profile);
  const t: TFunction = (key) => translations[language][key];
  const isDesktopApp = useIsDesktopApp();
  const canUseAdmin = userHasAdminRole(data.profile);
  const availableTabs = useMemo(() => {
    if (!isDesktopApp) return labTabs;
    return canUseAdmin
      ? [...desktopTabs.slice(0, -1), 'admin', 'profile']
      : desktopTabs;
  }, [canUseAdmin, isDesktopApp]);

  useEffect(() => {
    function syncRoute() {
      setRoute(getCurrentRoute());
    }

    window.addEventListener('popstate', syncRoute);
    return () => window.removeEventListener('popstate', syncRoute);
  }, []);

  useEffect(() => {
    let isActive = true;

    async function loadData() {
      try {
        setIsLoading(true);
        setError(null);

        const [boxes, zones, dashboard, exportOptions, profile] = await Promise.all([
          apiGet<PaginatedResponse<BoxItem>>('/api/boxes/?limit=80'),
          apiGet<PaginatedResponse<ThermalZone>>('/api/thermal-zones/?limit=80'),
          apiGet<Dashboard>('/api/dashboard/'),
          apiGet<ExportOptions>('/api/exports/options/'),
          apiGet<UserProfile>('/api/profile/'),
        ]);

        if (!isActive) return;

        setData({
          boxes: boxes.results,
          boxDetails: {},
          zones: zones.results,
          dashboard,
          exportOptions,
          profile,
        });
        setRecentBoxIds((currentIds) => {
          if (currentIds.length) return currentIds;
          return buildRecentBoxIds(boxes.results, dashboard);
        });
      } catch (requestError) {
        if (!isActive) return;
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
  }, []);

  const filteredBoxes = useMemo(() => {
    const value = search.trim().toLowerCase();
    if (!value) return data.boxes;

    return data.boxes.filter((box) => {
      return [
        box.global_code,
        box.local_code,
        box.box_number,
        box.species.scientific_name,
        box.strain.code,
        box.thermal_zone?.name ?? '',
      ].some((field) => field.toLowerCase().includes(value));
    });
  }, [data.boxes, search]);

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

  const recentBoxes = useMemo(() => {
    return recentBoxIds
      .map((boxId) => data.boxes.find((box) => box.id === boxId))
      .filter((box): box is BoxItem => Boolean(box))
      .slice(0, 5);
  }, [data.boxes, recentBoxIds]);

  function openBox(boxId: number, fallbackCode?: string) {
    const box = data.boxes.find((item) => item.id === boxId);
    const globalCode = box?.global_code ?? fallbackCode;
    if (!globalCode) return;

    if (box) {
      setRecentBoxIds((currentIds) => [
        boxId,
        ...currentIds.filter((currentId) => currentId !== boxId),
      ].slice(0, 5));
    } else {
      void apiGet<BoxDetail>(`/api/boxes/${boxId}/`)
        .then((detail) => {
          setData((current) => ({
            ...current,
            boxes: current.boxes.some((item) => item.id === detail.id)
              ? current.boxes
              : [...current.boxes, detail],
            boxDetails: {
              ...current.boxDetails,
              [detail.id]: detail,
            },
          }));
        })
        .catch((requestError) => setError(getErrorMessage(requestError)));
    }
    setSearch(globalCode);
    navigateTo({ tab: 'pilotage', boxCode: globalCode, boxId: null }, `/boxes/${encodeURIComponent(globalCode)}`);
  }

  function openTab(tab: TabId) {
    const paths: Record<TabId, string> = {
      pilotage: '/',
      zones: '/zones',
      exports: '/exports',
      admin: '/administration',
      profile: '/profile',
    };
    navigateTo({ tab, boxCode: null, boxId: null }, paths[tab]);
  }

  useEffect(() => {
    const shouldWaitForProfile = activeTab === 'admin' && isLoading && !data.profile;
    if (shouldWaitForProfile) return;
    if (availableTabs.includes(activeTab)) return;

    navigateTo({ tab: 'pilotage', boxCode: null }, '/');
  }, [activeTab, availableTabs, data.profile, isLoading]);

  function closeBoxPage() {
    navigateTo({ tab: 'pilotage', boxCode: null, boxId: null }, '/');
  }

  function navigateTo(nextRoute: RouteState, path: string) {
    window.history.pushState(null, '', path);
    setRoute(nextRoute);
  }

  async function updateLanguage(language: string) {
    const profile = await apiPatch<UserProfile>('/api/profile/', {
      interface_language: language,
    });

    setData((current) => ({
      ...current,
      profile,
    }));
  }

  async function createMeasurement(boxId: number, payload: MeasurementPayload) {
    await apiPost<BiologicalMeasurement>(`/api/boxes/${boxId}/measurements/`, payload);
    const detail = await apiGet<BoxDetail>(`/api/boxes/${boxId}/`);

    setData((current) => mergeBoxDetail(current, detail));
  }

  async function createSubculture(boxId: number, payload: SubculturePayload) {
    await apiPost<SubcultureResult>(`/api/boxes/${boxId}/subcultures/`, payload);
    const [boxes, detail, dashboard, exportOptions] = await Promise.all([
      apiGet<PaginatedResponse<BoxItem>>('/api/boxes/?limit=80'),
      apiGet<BoxDetail>(`/api/boxes/${boxId}/`),
      apiGet<Dashboard>('/api/dashboard/'),
      apiGet<ExportOptions>('/api/exports/options/'),
    ]);

    setData((current) => ({
      ...mergeBoxDetail(current, detail),
      boxes: boxes.results,
      dashboard,
      exportOptions,
    }));
  }

  async function moveBox(boxId: number, payload: BoxMovePayload) {
    const detail = await apiPost<BoxDetail>(`/api/boxes/${boxId}/move/`, payload);
    const [boxes, zones, dashboard, exportOptions] = await Promise.all([
      apiGet<PaginatedResponse<BoxItem>>('/api/boxes/?limit=80'),
      apiGet<PaginatedResponse<ThermalZone>>('/api/thermal-zones/?limit=80'),
      apiGet<Dashboard>('/api/dashboard/'),
      apiGet<ExportOptions>('/api/exports/options/'),
    ]);

    setData((current) => ({
      ...mergeBoxDetail(current, detail),
      boxes: boxes.results,
      zones: zones.results,
      dashboard,
      exportOptions,
    }));
  }

  async function loadLineageGraph(boxId: number) {
    return apiGet<LineageGraph>(`/api/boxes/${boxId}/lineage/`);
  }

  return (
    <main className="app-shell">
      <aside className="sidebar">
        <div className="brand-block">
          <span className="brand-mark">P</span>
          <div>
            <p className="eyebrow">Polypbase</p>
            <strong>{t('laboratoryTracking')}</strong>
          </div>
        </div>

        <nav className="tabbar" aria-label="Navigation principale">
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
        {!isBoxRoute ? (
          <header className="page-heading">
            <h1>{getTitle(activeTab, t)}</h1>
          </header>
        ) : null}

        {error ? <LoginNotice message={error} t={t} /> : null}

        {!error && (
          <>
            {activeTab === 'pilotage' && isBoxRoute && (
              <BoxPage
                box={selectedBoxDetail ?? selectedBox}
                boxes={data.boxes}
                zones={data.zones}
                language={language}
                isLoading={isLoading || isBoxLoading}
                onCreateMeasurement={createMeasurement}
                onCreateSubculture={createSubculture}
                onMoveBox={moveBox}
                onLoadLineageGraph={loadLineageGraph}
                onOpenBox={openBox}
                onBack={closeBoxPage}
                t={t}
              />
            )}

            {activeTab === 'pilotage' && !isBoxRoute && (
              <PilotageView
                isLoading={isLoading}
                search={search}
                suggestions={filteredBoxes.slice(0, 5)}
                recentBoxes={recentBoxes}
                onSearch={setSearch}
                onSelectBox={openBox}
                t={t}
              />
            )}

            {activeTab === 'zones' && <ZonesView isLoading={isLoading} zones={data.zones} t={t} />}

            {activeTab === 'exports' && (
              <ExportsView
                isLoading={isLoading}
                options={data.exportOptions}
                language={language}
              />
            )}

            {activeTab === 'admin' && (
              <AdminView
                isLoading={isLoading}
                profile={data.profile}
                t={t}
              />
            )}

            {activeTab === 'profile' && (
              <ProfileView
                isLoading={isLoading}
                profile={data.profile}
                onUpdateLanguage={updateLanguage}
                t={t}
              />
            )}
          </>
        )}
      </section>
    </main>
  );
}

function PilotageView({
  isLoading,
  recentBoxes,
  search,
  suggestions,
  t,
  onSearch,
  onSelectBox,
}: {
  isLoading: boolean;
  recentBoxes: BoxItem[];
  search: string;
  suggestions: BoxItem[];
  t: TFunction;
  onSearch: (value: string) => void;
  onSelectBox: (id: number) => void;
}) {
  const visibleSuggestions = search.trim() ? suggestions : [];

  function selectFirstSuggestion() {
    if (visibleSuggestions[0]) {
      onSelectBox(visibleSuggestions[0].id);
      onSearch(visibleSuggestions[0].global_code);
    }
  }

  return (
    <section className="pilotage-flow">
      <div className="lookup-panel">
        <SearchField value={search} onChange={onSearch} onSubmit={selectFirstSuggestion} t={t} />

        {isLoading ? <SkeletonRows count={3} /> : null}

        {!isLoading && <RecentAccessList boxes={recentBoxes} onSelectBox={onSelectBox} t={t} />}

        {!isLoading && visibleSuggestions.length > 0 ? (
          <SuggestionList
            boxes={visibleSuggestions}
            selectedBoxId={null}
            onSelectBox={onSelectBox}
            t={t}
          />
        ) : null}
      </div>

      <JellyfishPattern />
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
          <button key={box.id} type="button" onClick={() => onSelectBox(box.id)}>
            <strong>{box.global_code}</strong>
            <small>{box.species.genus_species_code || box.strain.code}</small>
          </button>
        ))}
      </div>
    </section>
  );
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

      <div className="suggestion-list">
        {boxes.map((box) => (
          <button
            key={box.id}
            className={selectedBoxId === box.id ? 'suggestion-row is-selected' : 'suggestion-row'}
            type="button"
            onClick={() => onSelectBox(box.id)}
          >
            <span>
              <strong>{box.global_code}</strong>
              <small>{box.species.scientific_name}</small>
            </span>
            <span>{box.thermal_zone?.name ?? t('noZone')}</span>
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
  language,
  isLoading,
  onCreateMeasurement,
  onCreateSubculture,
  onMoveBox,
  onLoadLineageGraph,
  onOpenBox,
  onBack,
  t,
}: {
  box: BoxItem | BoxDetail | null;
  boxes: BoxItem[];
  zones: ThermalZone[];
  language: Language;
  isLoading: boolean;
  onCreateMeasurement: (boxId: number, payload: MeasurementPayload) => Promise<void>;
  onCreateSubculture: (boxId: number, payload: SubculturePayload) => Promise<void>;
  onMoveBox: (boxId: number, payload: BoxMovePayload) => Promise<void>;
  onLoadLineageGraph: (boxId: number) => Promise<LineageGraph>;
  onOpenBox: (boxId: number, globalCode: string) => void;
  onBack: () => void;
  t: TFunction;
}) {
  const [form, setForm] = useState(() => getInitialMeasurementForm());
  const [isSaving, setIsSaving] = useState(false);
  const [isHistoryOpen, setIsHistoryOpen] = useState(false);
  const [isLineageOpen, setIsLineageOpen] = useState(false);
  const [lineageGraph, setLineageGraph] = useState<LineageGraph | null>(null);
  const [isLineageGraphLoading, setIsLineageGraphLoading] = useState(false);
  const [lineageGraphError, setLineageGraphError] = useState<string | null>(null);
  const [isMoveOpen, setIsMoveOpen] = useState(false);
  const [isSavingMove, setIsSavingMove] = useState(false);
  const [moveError, setMoveError] = useState<string | null>(null);
  const [moveMessage, setMoveMessage] = useState<string | null>(null);
  const [isSubcultureOpen, setIsSubcultureOpen] = useState(false);
  const [isSavingSubculture, setIsSavingSubculture] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [saveMessage, setSaveMessage] = useState<string | null>(null);
  const [subcultureError, setSubcultureError] = useState<string | null>(null);
  const [subcultureMessage, setSubcultureMessage] = useState<string | null>(null);
  const [activeInsightTab, setActiveInsightTab] = useState<BoxInsightTab>('measurements');

  useEffect(() => {
    setForm(getInitialMeasurementForm());
    setIsHistoryOpen(false);
    setIsLineageOpen(false);
    setLineageGraph(null);
    setIsLineageGraphLoading(false);
    setLineageGraphError(null);
    setIsMoveOpen(false);
    setIsSavingMove(false);
    setMoveError(null);
    setMoveMessage(null);
    setIsSubcultureOpen(false);
    setSaveError(null);
    setSaveMessage(null);
    setSubcultureError(null);
    setSubcultureMessage(null);
    setActiveInsightTab('measurements');
  }, [box?.id]);

  if (isLoading) {
    return (
      <section className="box-page">
        <SkeletonRows count={4} />
      </section>
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
  const qr = 'qr_image_url' in box ? { imageUrl: box.qr_image_url, scanUrl: box.scan_url } : null;
  const lineage = getBoxLineage(box);
  const lineageCount = lineage.parents.length + lineage.children.length;
  const currentZone = getCurrentThermalZone(box, zones);
  const createdOn = getBoxCreatedDate(box);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!box || isSaving) return;

    setIsSaving(true);
    setSaveError(null);
    setSaveMessage(null);

    try {
      await onCreateMeasurement(box.id, {
        measured_on: form.measuredOn,
        polyp_count: parsePositiveInteger(form.polypCount),
        ephyrae_count: parsePositiveInteger(form.ephyraeCount),
        notes: form.notes.trim(),
      });
      setForm(getInitialMeasurementForm());
      setSaveMessage(t('measurementSaved'));
    } catch (requestError) {
      setSaveError(getMeasurementSaveError(requestError, t));
    } finally {
      setIsSaving(false);
    }
  }

  async function handleSubculture(payload: SubculturePayload) {
    if (!box || isSavingSubculture) return;

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

  async function handleOpenLineage() {
    if (!box) return;

    setIsLineageOpen(true);
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
    <section className="box-page">
      <button className="text-button" type="button" onClick={onBack}>
        {t('backToPilotage')}
      </button>

      <header className="box-sheet-hero">
        <div className="box-sheet-identity">
          <p className="box-page-label">{t('boxSheet')}</p>
          <div className="box-code-line">
            <h2>{box.global_code}</h2>
            <span
              className={`box-life-status is-${
                getBoxStatusPresentation(box.status, language).tone
              }`}
            >
              {getBoxStatusPresentation(box.status, language).label}
            </span>
          </div>
          <p>{box.species.scientific_name}</p>
          <div className="box-small-facts">
            <InfoPill label={t('boxLocalCode')} value={box.local_code || '-'} />
            <InfoPill label={t('boxStrain')} value={box.strain.code} />
            <InfoPill label={t('createdOn')} value={createdOn ? formatDisplayDate(createdOn) : t('noDate')} />
          </div>
        </div>

        <div className="box-zone-summary">
          <InfoPill label={t('zones')} value={box.thermal_zone?.name ?? t('noZone')} strong />
          <InfoPill
            label={t('targetTemperature')}
            value={formatTemperatureValue(currentZone?.target_temperature_c ?? box.thermal_zone?.target_temperature_c)}
          />
          <InfoPill label={t('temperatureShort')} value={formatTemperature(currentZone?.latest_temperature?.average_temperature_c)} />
          <InfoPill label={t('salinityShort')} value={formatSalinity(currentZone?.latest_salinity?.salinity_psu)} />
        </div>

        <div className="box-action-stack">
          {qr ? (
            <a className="box-hero-qr" href={qr.imageUrl} download={`bac-${box.id}.svg`}>
              <img src={qr.imageUrl} alt={`${t('qrCode')} ${box.global_code}`} width={74} height={74} />
              <span>{t('qrCode')}</span>
            </a>
          ) : null}
          <button className="lineage-trigger" type="button" onClick={handleOpenLineage}>
            <span>{t('lineageAction')}</span>
            <strong>{lineageCount}</strong>
          </button>
          <button className="history-trigger" type="button" onClick={() => setIsHistoryOpen(true)}>
            <span>{t('historyButton')}</span>
            <strong>{measurements.length}</strong>
          </button>
          <button className="move-trigger" type="button" onClick={() => setIsMoveOpen(true)}>
            {t('moveAction')}
          </button>
          <button className="subculture-trigger" type="button" onClick={() => setIsSubcultureOpen(true)}>
            {t('subcultureAction')}
          </button>
        </div>
      </header>

      {subcultureMessage ? (
        <p className="inline-success box-action-feedback">{subcultureMessage}</p>
      ) : null}
      {moveMessage ? (
        <p className="inline-success box-action-feedback">{moveMessage}</p>
      ) : null}

      <div className="box-page-grid">
        <section className="last-reading-card">
          <div>
            <h2>{t('lastMeasurement')}</h2>
            <span>{box.latest_measurement ? formatDisplayDate(box.latest_measurement.measured_on) : t('noDate')}</span>
          </div>
          <Metric label={t('polyps')} value={String(box.latest_measurement?.polyp_count ?? '-')} />
          <Metric label={t('ephyraeFull')} value={String(box.latest_measurement?.ephyrae_count ?? '-')} />
        </section>

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

              <label>
                {t('polyps')}
                <input
                  min="0"
                  required
                  inputMode="numeric"
                  placeholder="0"
                  type="number"
                  value={form.polypCount}
                  onChange={(event) => setForm((current) => ({ ...current, polypCount: event.target.value }))}
                />
                <QuickCountButtons
                  values={[50, 100]}
                  onAdd={(value) => setForm((current) => ({
                    ...current,
                    polypCount: incrementCountValue(current.polypCount, value),
                  }))}
                />
              </label>

              <label>
                {t('ephyraeFull')}
                <input
                  min="0"
                  required
                  inputMode="numeric"
                  placeholder="0"
                  type="number"
                  value={form.ephyraeCount}
                  onChange={(event) => setForm((current) => ({ ...current, ephyraeCount: event.target.value }))}
                />
                <QuickCountButtons
                  values={[10, 25]}
                  onAdd={(value) => setForm((current) => ({
                    ...current,
                    ephyraeCount: incrementCountValue(current.ephyraeCount, value),
                  }))}
                />
              </label>
            </div>

            <div className="last-comment">
              <span>{t('lastComment')}</span>
              <p>{lastComment || t('noComment')}</p>
            </div>

            <label className="notes-field">
              {t('observation')}
              <textarea
                placeholder={t('observationPlaceholder')}
                rows={3}
                value={form.notes}
                onChange={(event) => setForm((current) => ({ ...current, notes: event.target.value }))}
              />
            </label>

            {saveError ? <p className="inline-error form-feedback">{saveError}</p> : null}
            {saveMessage ? <p className="inline-success form-feedback">{saveMessage}</p> : null}

            <button type="submit" disabled={isSaving}>
              {isSaving ? t('saving') : t('saveMeasurement')}
            </button>
          </form>
        </section>

        <section className="box-insights-section">
          <BoxInsights
            activeTab={activeInsightTab}
            lineage={lineage}
            measurements={measurements}
            onOpenHistory={() => setIsHistoryOpen(true)}
            onOpenLineage={handleOpenLineage}
            onSelectTab={setActiveInsightTab}
            t={t}
          />
        </section>

        {qr ? (
          <section className="box-section box-qr-section">
            <div className="section-title">
              <h2>{t('qrCode')}</h2>
              <span>{qr.scanUrl}</span>
            </div>
            <div className="box-qr">
              <p>{t('qrScanHint')}</p>
              <a className="qr-download" href={qr.imageUrl} download={`bac-${box.id}.svg`}>
                {t('qrDownload')}
              </a>
            </div>
          </section>
        ) : null}

        {isHistoryOpen ? (
          <MeasurementHistoryModal
            measurements={measurements}
            onClose={() => setIsHistoryOpen(false)}
            t={t}
          />
        ) : null}

        {isLineageOpen ? (
          <LineageModal
            lineage={lineage}
            graph={lineageGraph}
            isGraphLoading={isLineageGraphLoading}
            graphError={lineageGraphError}
            language={language}
            onClose={() => setIsLineageOpen(false)}
            onSelectBox={(boxId, globalCode) => {
              setIsLineageOpen(false);
              onOpenBox(boxId, globalCode);
            }}
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
      </div>
    </section>
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

function BoxInsights({
  activeTab,
  lineage,
  measurements,
  onOpenHistory,
  onOpenLineage,
  onSelectTab,
  t,
}: {
  activeTab: BoxInsightTab;
  lineage: BoxLineage;
  measurements: BiologicalMeasurement[];
  onOpenHistory: () => void;
  onOpenLineage: () => void;
  onSelectTab: (tab: BoxInsightTab) => void;
  t: TFunction;
}) {
  const tabs: Array<{ id: BoxInsightTab; label: string }> = [
    { id: 'measurements', label: t('analysisTabMeasurements') },
    { id: 'observations', label: t('analysisTabObservations') },
    { id: 'lineage', label: t('analysisTabLineage') },
  ];

  return (
    <div className="box-insights">
      <div className="insight-tabs" role="tablist" aria-label={t('chartTitle')}>
        {tabs.map((tab) => (
          <button
            key={tab.id}
            className={activeTab === tab.id ? 'is-active' : ''}
            role="tab"
            type="button"
            aria-selected={activeTab === tab.id}
            onClick={() => onSelectTab(tab.id)}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {activeTab === 'measurements' ? (
        <div className="insight-panel">
          <div className="insight-heading">
            <h2>{t('chartTitle')}</h2>
            <button type="button" onClick={onOpenHistory}>{t('historyButton')}</button>
          </div>
          <MeasurementTrendChart measurements={measurements} t={t} />
        </div>
      ) : null}

      {activeTab === 'observations' ? (
        <div className="insight-panel">
          <div className="insight-heading">
            <h2>{t('analysisTabObservations')}</h2>
            <button type="button" onClick={onOpenHistory}>{t('historyButton')}</button>
          </div>
          <ObservationPreview measurements={measurements} t={t} />
        </div>
      ) : null}

      {activeTab === 'lineage' ? (
        <div className="insight-panel">
          <div className="insight-heading">
            <h2>{t('analysisTabLineage')}</h2>
            <button type="button" onClick={onOpenLineage}>{t('lineageAction')}</button>
          </div>
          <div className="lineage-preview">
            <Metric label={t('analysisTabLineage')} value={String(lineage.parents.length + lineage.children.length)} />
            <Metric label={t('parents')} value={String(lineage.parents.length)} />
            <Metric label={t('children')} value={String(lineage.children.length)} />
          </div>
        </div>
      ) : null}
    </div>
  );
}

function MeasurementTrendChart({
  measurements,
  t,
}: {
  measurements: BiologicalMeasurement[];
  t: TFunction;
}) {
  const chartMeasurements = [...measurements]
    .sort((left, right) => left.measured_on.localeCompare(right.measured_on))
    .slice(-12);

  if (chartMeasurements.length < 2) {
    return <p className="muted compact-text chart-empty">{t('chartEmpty')}</p>;
  }

  const width = 720;
  const height = 250;
  const padding = { top: 20, right: 28, bottom: 38, left: 42 };
  const maxValue = Math.max(
    1,
    ...chartMeasurements.flatMap((measurement) => [
      measurement.polyp_count,
      measurement.ephyrae_count,
    ]),
  );
  const xStep = (width - padding.left - padding.right) / (chartMeasurements.length - 1);
  const yScale = (value: number) => (
    height - padding.bottom - (value / maxValue) * (height - padding.top - padding.bottom)
  );
  const toPoints = (selector: (measurement: BiologicalMeasurement) => number) => (
    chartMeasurements
      .map((measurement, index) => `${padding.left + index * xStep},${yScale(selector(measurement))}`)
      .join(' ')
  );
  const firstDate = chartMeasurements[0].measured_on;
  const lastDate = chartMeasurements[chartMeasurements.length - 1].measured_on;

  return (
    <div className="measurement-chart" aria-label={t('chartTitle')}>
      <svg viewBox={`0 0 ${width} ${height}`} role="img">
        <line className="chart-axis" x1={padding.left} y1={height - padding.bottom} x2={width - padding.right} y2={height - padding.bottom} />
        <line className="chart-axis" x1={padding.left} y1={padding.top} x2={padding.left} y2={height - padding.bottom} />
        {[0.25, 0.5, 0.75].map((ratio) => {
          const y = padding.top + ratio * (height - padding.top - padding.bottom);
          return <line key={ratio} className="chart-grid-line" x1={padding.left} y1={y} x2={width - padding.right} y2={y} />;
        })}
        <polyline className="chart-line is-polyps" points={toPoints((measurement) => measurement.polyp_count)} />
        <polyline className="chart-line is-ephyrae" points={toPoints((measurement) => measurement.ephyrae_count)} />
        {chartMeasurements.map((measurement, index) => (
          <g key={measurement.id}>
            <circle className="chart-dot is-polyps" cx={padding.left + index * xStep} cy={yScale(measurement.polyp_count)} r="3.5" />
            <circle className="chart-dot is-ephyrae" cx={padding.left + index * xStep} cy={yScale(measurement.ephyrae_count)} r="3.5" />
          </g>
        ))}
        <text className="chart-label" x={padding.left} y={height - 12}>{formatDisplayDate(firstDate)}</text>
        <text className="chart-label is-end" x={width - padding.right} y={height - 12}>{formatDisplayDate(lastDate)}</text>
        <text className="chart-y-label" x={padding.left - 8} y={padding.top + 4}>{maxValue}</text>
        <text className="chart-y-label" x={padding.left - 8} y={height - padding.bottom + 4}>0</text>
      </svg>

      <div className="chart-legend">
        <span className="is-polyps">{t('polyps')}</span>
        <span className="is-ephyrae">{t('ephyraeFull')}</span>
      </div>
    </div>
  );
}

function ObservationPreview({
  measurements,
  t,
}: {
  measurements: BiologicalMeasurement[];
  t: TFunction;
}) {
  const observations = measurements
    .filter((measurement) => measurement.notes?.trim())
    .slice(0, 4);

  if (!observations.length) {
    return <p className="muted compact-text">{t('noComment')}</p>;
  }

  return (
    <div className="observation-preview">
      {observations.map((measurement) => (
        <article key={measurement.id}>
          <strong>{formatDisplayDate(measurement.measured_on)}</strong>
          <p>{measurement.notes}</p>
        </article>
      ))}
    </div>
  );
}

function MeasurementHistoryList({
  measurements,
  t,
}: {
  measurements: BiologicalMeasurement[];
  t: TFunction;
}) {
  return (
    <div className="measurement-history">
      {!measurements.length ? <p className="muted compact-text">{t('noMeasurementHistory')}</p> : null}

      {measurements.map((measurement) => (
        <article key={measurement.id} className="measurement-row">
          <div>
            <strong>{formatDisplayDate(measurement.measured_on)}</strong>
            <small>{measurement.user ?? '-'}</small>
          </div>
          <span>
            <strong>{measurement.polyp_count}</strong>
            <small>{t('polyps')}</small>
          </span>
          <span>
            <strong>{measurement.ephyrae_count}</strong>
            <small>{t('ephyraeFull')}</small>
          </span>
          <p>{measurement.notes?.trim() || t('noComment')}</p>
        </article>
      ))}
    </div>
  );
}

function MeasurementHistoryModal({
  measurements,
  onClose,
  t,
}: {
  measurements: BiologicalMeasurement[];
  onClose: () => void;
  t: TFunction;
}) {
  return (
    <div className="modal-backdrop" role="presentation" onClick={onClose}>
      <section
        className="history-modal"
        role="dialog"
        aria-modal="true"
        aria-label={t('measurementHistory')}
        onClick={(event) => event.stopPropagation()}
      >
        <div className="modal-heading">
          <div>
            <h2>{t('measurementHistory')}</h2>
            <span>{measurements.length}</span>
          </div>
          <button type="button" onClick={onClose}>
            {t('close')}
          </button>
        </div>

        <MeasurementHistoryList measurements={measurements} t={t} />
      </section>
    </div>
  );
}

function ZonesView({ isLoading, zones, t }: { isLoading: boolean; zones: ThermalZone[]; t: TFunction }) {
  return (
    <section className="single-panel">
      {isLoading ? (
        <SkeletonRows count={5} />
      ) : (
        <div className="zone-list">
          {zones.map((zone) => (
            <article className="zone-row" key={zone.id}>
              <div>
                <strong>{zone.name}</strong>
                <small>{zone.organization.name}</small>
              </div>
              <Metric label={t('temperatureShort')} value={formatTemperature(zone.latest_temperature?.average_temperature_c)} />
              <Metric label={t('salinityShort')} value={formatSalinity(zone.latest_salinity?.salinity_psu)} />
              <Metric label={t('probes')} value={String(zone.probes.length)} />
              <Metric label={t('boxes')} value={String(zone.box_count)} />
            </article>
          ))}
        </div>
      )}
    </section>
  );
}

function ProfileView({
  isLoading,
  profile,
  onUpdateLanguage,
  t,
}: {
  isLoading: boolean;
  profile: UserProfile | null;
  onUpdateLanguage: (language: string) => Promise<void>;
  t: TFunction;
}) {
  const [isSaving, setIsSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  async function handleLanguage(language: string) {
    setIsSaving(true);
    setSaveError(null);
    try {
      await onUpdateLanguage(language);
    } catch (requestError) {
      setSaveError(getErrorMessage(requestError));
    } finally {
      setIsSaving(false);
    }
  }

  if (isLoading) {
    return (
      <section className="single-panel">
        <SkeletonRows count={3} />
      </section>
    );
  }

  if (!profile) return null;

  return (
    <section className="profile-panel">
      <div>
        <p className="eyebrow">{t('account')}</p>
        <h2>{profile.username}</h2>
        <p>{profile.organizations.map((organization) => organization.name).join(', ')}</p>
      </div>

      {profile.memberships.length ? (
        <div className="profile-roles" aria-label={t('profileRoles')}>
          {profile.memberships.map((membership) => (
            <span key={`${membership.organization.id}-${membership.role}`}>
              <strong>{membership.organization.name}</strong>
              {membership.role_label}
            </span>
          ))}
        </div>
      ) : null}

      <div className="language-switch">
        {profile.available_languages.map((language) => (
          <button
            key={language.code}
            className={profile.interface_language === language.code ? 'pill is-active' : 'pill'}
            type="button"
            disabled={isSaving}
            onClick={() => handleLanguage(language.code)}
          >
            {language.label}
          </button>
        ))}
      </div>

      {saveError ? <p className="inline-error">{saveError}</p> : null}
    </section>
  );
}

function AdminView({
  isLoading,
  profile,
  t,
}: {
  isLoading: boolean;
  profile: UserProfile | null;
  t: TFunction;
}) {
  if (isLoading) {
    return (
      <section className="single-panel">
        <SkeletonRows count={4} />
      </section>
    );
  }

  if (!profile || !userHasAdminRole(profile)) return null;

  const adminItems = [
    t('adminUsers'),
    t('adminOrganizations'),
    t('adminReferences'),
    t('adminEnvironment'),
  ];

  return (
    <section className="admin-panel">
      <div className="admin-grid">
        {adminItems.map((item) => (
          <article key={item} className="admin-item">
            <span>{item}</span>
            <strong>{t('adminRights')}</strong>
          </article>
        ))}
      </div>

      <a className="admin-link" href="/admin/">
        {t('adminOpenDjango')}
      </a>
    </section>
  );
}

function SearchField({
  value,
  onChange,
  onSubmit,
  t,
}: {
  value: string;
  onChange: (value: string) => void;
  onSubmit?: () => void;
  t: TFunction;
}) {
  return (
    <form
      className="search-field"
      onSubmit={(event) => {
        event.preventDefault();
        onSubmit?.();
      }}
    >
      <label>
        <span>{t('searchOrScan')}</span>
        <input
          value={value}
          placeholder={t('searchPlaceholder')}
          type="search"
          onChange={(event) => onChange(event.target.value)}
        />
      </label>
    </form>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <span className="metric">
      <small>{label}</small>
      <strong>{value}</strong>
    </span>
  );
}

function QuickCountButtons({ values, onAdd }: { values: number[]; onAdd: (value: number) => void }) {
  return (
    <span className="quick-counts">
      {values.map((value) => (
        <button key={value} type="button" onClick={() => onAdd(value)}>
          +{value}
        </button>
      ))}
    </span>
  );
}

function LoginNotice({ message, t }: { message: string; t: TFunction }) {
  return (
    <section className="login-notice">
      <h2>{t('loginRequired')}</h2>
      <p>{message}</p>
      <a href="http://127.0.0.1:8000/accounts/login/">{t('loginAction')}</a>
    </section>
  );
}

function SkeletonRows({ count }: { count: number }) {
  return (
    <div className="skeleton-stack">
      {Array.from({ length: count }, (_, index) => (
        <span className="skeleton-row" key={index} />
      ))}
    </div>
  );
}

function mergeBoxDetail(current: AppData, detail: BoxDetail): AppData {
  return {
    ...current,
    boxes: current.boxes.map((box) => (box.id === detail.id ? detail : box)),
    boxDetails: {
      ...current.boxDetails,
      [detail.id]: detail,
    },
  };
}

function getInitialMeasurementForm() {
  return {
    measuredOn: getTodayDateValue(),
    polypCount: '',
    ephyraeCount: '',
    notes: '',
  };
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

function parsePositiveInteger(value: string) {
  const parsedValue = Number.parseInt(value, 10);
  return Number.isFinite(parsedValue) && parsedValue >= 0 ? parsedValue : 0;
}

function incrementCountValue(currentValue: string, increment: number) {
  return String(parsePositiveInteger(currentValue) + increment);
}

function formatDisplayDate(value: string) {
  return new Intl.DateTimeFormat('fr-FR', {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
  }).format(new Date(`${value}T00:00:00`));
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
  if (error instanceof ApiError && error.status === 403) {
    return t('moveForbidden');
  }

  return getErrorMessage(error);
}

function formatTemperature(value: number | undefined) {
  return value === undefined ? '-' : `${value.toFixed(1)}°C`;
}

function formatTemperatureValue(value: string | number | null | undefined) {
  if (value === null || value === undefined || value === '') return '-';
  const numericValue = typeof value === 'number' ? value : Number.parseFloat(value);
  return Number.isFinite(numericValue) ? `${numericValue.toFixed(1)}°C` : '-';
}

function formatSalinity(value: number | undefined) {
  return value === undefined ? '-' : `${value.toFixed(1)}`;
}

function buildRecentBoxIds(boxes: BoxItem[], dashboard: Dashboard) {
  const idsFromScans = dashboard.latest_scans
    .map((scan) => boxes.find((box) => box.global_code === scan.object_id)?.id)
    .filter((boxId): boxId is number => Boolean(boxId));

  return uniqueNumbers([...idsFromScans, ...boxes.slice(0, 5).map((box) => box.id)]).slice(0, 5);
}

function uniqueNumbers(values: number[]) {
  return values.filter((value, index) => values.indexOf(value) === index);
}

function getLanguage(profile: UserProfile | null): Language {
  return profile?.interface_language === 'en' ? 'en' : 'fr';
}

function useIsDesktopApp() {
  const [isDesktop, setIsDesktop] = useState(() => getIsDesktopApp());

  useEffect(() => {
    const media = window.matchMedia('(min-width: 1181px), (min-width: 841px) and (min-height: 761px)');

    function syncDesktopState() {
      setIsDesktop(media.matches);
    }

    syncDesktopState();
    media.addEventListener('change', syncDesktopState);
    return () => media.removeEventListener('change', syncDesktopState);
  }, []);

  return isDesktop;
}

function getIsDesktopApp() {
  return window.matchMedia('(min-width: 1181px), (min-width: 841px) and (min-height: 761px)').matches;
}

function userHasAdminRole(profile: UserProfile | null) {
  if (!profile) return false;
  if (profile.is_superuser) return true;
  return profile.memberships.some((membership) => membership.role === 'admin');
}

function getTitle(tab: TabId, t: TFunction) {
  if (tab === 'pilotage') return t('pilotageTitle');
  if (tab === 'zones') return t('zonesTitle');
  if (tab === 'exports') return t('exportsTitle');
  if (tab === 'admin') return t('adminTitle');
  return t('profileTitle');
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

  if (path === '/exports') {
    return { tab: 'exports', boxCode: null, boxId: null };
  }

  if (path === '/administration') {
    return { tab: 'admin', boxCode: null };
  }

  if (path === '/profile') {
    return { tab: 'profile', boxCode: null, boxId: null };
  }

  return { tab: 'pilotage', boxCode: null, boxId: null };
}

function getErrorMessage(error: unknown) {
  if (error instanceof ApiError) {
    if (error.status === 401 || error.status === 403) {
      return 'Connecte-toi avec un compte demo pour voir les données.';
    }
    return error.message;
  }

  return 'Impossible de joindre l’API Django.';
}
