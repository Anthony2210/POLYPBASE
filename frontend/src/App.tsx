import { useEffect, useMemo, useState } from 'react';

import { ApiError, apiGet, apiPatch } from './api/client';
import type {
  BoxItem,
  Dashboard,
  PaginatedResponse,
  ThermalZone,
  UserProfile,
} from './types';

type TabId = 'pilotage' | 'zones' | 'profile';

type AppData = {
  boxes: BoxItem[];
  zones: ThermalZone[];
  dashboard: Dashboard | null;
  profile: UserProfile | null;
};

type RouteState = {
  tab: TabId;
  boxCode: string | null;
};

const translations = {
  fr: {
    account: 'Compte',
    backToPilotage: 'Retour au pilotage',
    boxNotFound: 'Boîte introuvable',
    boxNotFoundText: 'Cette boîte n’existe pas dans les données chargées.',
    boxSheet: 'Fiche de la boîte',
    boxes: 'Boîtes',
    ephyrae: 'Éphyr.',
    lastComment: 'Dernier commentaire',
    lastMeasurement: 'Dernier relevé',
    laboratoryTracking: 'Suivi laboratoire',
    loginAction: 'Se connecter',
    loginRequired: 'Connexion requise',
    newMeasurement: 'Nouveau relevé',
    noComment: 'Aucun commentaire récent pour cette boîte.',
    noDate: 'aucune date',
    noRecentScans: 'Aucun scan récent pour l’instant.',
    noZone: 'Sans zone',
    observation: 'Observation',
    observationPlaceholder: 'Note rapide pour le laboratoire',
    pilotage: 'Pilotage',
    pilotageTitle: 'Pilotage labo',
    polyps: 'Polypes',
    probes: 'Sondes',
    profile: 'Profil',
    profileTitle: 'Mon profil',
    prototype: 'prototype',
    recentAccess: 'Derniers accès',
    saveLater: 'Enregistrer plus tard',
    scanSearch: 'scan / recherche',
    searchOrScan: 'Recherche ou scan',
    searchPlaceholder: 'Code boîte, espèce, souche',
    suggestions: 'Suggestions',
    temperatureShort: 'Temp.',
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
    ephyrae: 'Ephyrae',
    lastComment: 'Last comment',
    lastMeasurement: 'Last measurement',
    laboratoryTracking: 'Lab tracking',
    loginAction: 'Sign in',
    loginRequired: 'Sign-in required',
    newMeasurement: 'New measurement',
    noComment: 'No recent comment for this box.',
    noDate: 'no date',
    noRecentScans: 'No recent scan yet.',
    noZone: 'No zone',
    observation: 'Observation',
    observationPlaceholder: 'Quick lab note',
    pilotage: 'Pilotage',
    pilotageTitle: 'Lab pilotage',
    polyps: 'Polyps',
    probes: 'Probes',
    profile: 'Profile',
    profileTitle: 'My profile',
    prototype: 'prototype',
    recentAccess: 'Recent access',
    saveLater: 'Save later',
    scanSearch: 'scan / search',
    searchOrScan: 'Search or scan',
    searchPlaceholder: 'Box code, species, strain',
    suggestions: 'Suggestions',
    temperatureShort: 'Temp.',
    salinityShort: 'Sal.',
    zones: 'Zones',
    zonesTitle: 'Thermal zones',
  },
};

type Language = keyof typeof translations;
type TranslationKey = keyof typeof translations.fr;
type TFunction = (key: TranslationKey) => string;

const tabs: TabId[] = ['pilotage', 'zones', 'profile'];

export default function App() {
  const [route, setRoute] = useState<RouteState>(() => getCurrentRoute());
  const [search, setSearch] = useState('');
  const [recentBoxIds, setRecentBoxIds] = useState<number[]>([]);
  const [data, setData] = useState<AppData>({
    boxes: [],
    zones: [],
    dashboard: null,
    profile: null,
  });
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const activeTab = route.tab;
  const language = getLanguage(data.profile);
  const t: TFunction = (key) => translations[language][key];

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

        const [boxes, zones, dashboard, profile] = await Promise.all([
          apiGet<PaginatedResponse<BoxItem>>('/api/boxes/?limit=80'),
          apiGet<PaginatedResponse<ThermalZone>>('/api/thermal-zones/?limit=80'),
          apiGet<Dashboard>('/api/dashboard/'),
          apiGet<UserProfile>('/api/profile/'),
        ]);

        if (!isActive) return;

        setData({
          boxes: boxes.results,
          zones: zones.results,
          dashboard,
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

  const selectedBox = useMemo(() => {
    if (!route.boxCode) return null;
    return data.boxes.find((box) => box.global_code === route.boxCode) ?? null;
  }, [data.boxes, route.boxCode]);

  const recentBoxes = useMemo(() => {
    return recentBoxIds
      .map((boxId) => data.boxes.find((box) => box.id === boxId))
      .filter((box): box is BoxItem => Boolean(box))
      .slice(0, 5);
  }, [data.boxes, recentBoxIds]);

  function openBox(boxId: number) {
    const box = data.boxes.find((item) => item.id === boxId);
    if (!box) return;

    setRecentBoxIds((currentIds) => [
      boxId,
      ...currentIds.filter((currentId) => currentId !== boxId),
    ].slice(0, 5));
    setSearch(box.global_code);
    navigateTo({ tab: 'pilotage', boxCode: box.global_code }, `/boxes/${encodeURIComponent(box.global_code)}`);
  }

  function openTab(tab: TabId) {
    const paths: Record<TabId, string> = {
      pilotage: '/',
      zones: '/zones',
      profile: '/profile',
    };
    navigateTo({ tab, boxCode: null }, paths[tab]);
  }

  function closeBoxPage() {
    navigateTo({ tab: 'pilotage', boxCode: null }, '/');
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
          {tabs.map((tab) => (
            <button
              key={tab}
              className={tab === activeTab ? 'tab is-active' : 'tab'}
              type="button"
              onClick={() => openTab(tab)}
            >
              {t(tab)}
            </button>
          ))}
        </nav>
      </aside>

      <section className="workspace">
        {!route.boxCode ? (
          <header className="page-heading">
            <h1>{getTitle(activeTab, t)}</h1>
          </header>
        ) : null}

        {error ? <LoginNotice message={error} t={t} /> : null}

        {!error && (
          <>
            {activeTab === 'pilotage' && route.boxCode && (
              <BoxPage
                box={selectedBox}
                isLoading={isLoading}
                onBack={closeBoxPage}
                t={t}
              />
            )}

            {activeTab === 'pilotage' && !route.boxCode && (
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
  isLoading,
  onBack,
  t,
}: {
  box: BoxItem | null;
  isLoading: boolean;
  onBack: () => void;
  t: TFunction;
}) {
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

  const lastComment = box.latest_measurement?.notes?.trim();

  return (
    <section className="box-page">
      <button className="text-button" type="button" onClick={onBack}>
        {t('backToPilotage')}
      </button>

      <p className="box-page-label">{t('boxSheet')}</p>

      <header className="box-page-heading">
        <div>
          <div className="box-code-line">
            <h2>{box.global_code}</h2>
            <span>{formatStatus(box.status)}</span>
          </div>
          <p>{box.species.scientific_name}</p>
        </div>
        <div className="box-meta">
          <span>{box.thermal_zone?.name ?? t('noZone')}</span>
          <small>{box.organization.name}</small>
        </div>
      </header>

      <div className="box-page-grid">
        <section className="box-section">
          <div className="section-title">
            <h2>{t('lastMeasurement')}</h2>
            <span>{box.latest_measurement?.measured_on ?? t('noDate')}</span>
          </div>

          <div className="metric-grid compact two-columns">
            <Metric label={t('polyps')} value={String(box.latest_measurement?.polyp_count ?? '-')} />
            <Metric label={t('ephyrae')} value={String(box.latest_measurement?.ephyrae_count ?? '-')} />
          </div>
        </section>

        <section className="box-section">
          <form className="fake-form" onSubmit={(event) => event.preventDefault()}>
            <div className="section-title">
              <h2>{t('newMeasurement')}</h2>
              <span>{t('prototype')}</span>
            </div>

            <div className="form-grid two-columns">
              <label>
                {t('polyps')}
                <input inputMode="numeric" placeholder="0" type="number" />
              </label>
              <label>
                {t('ephyrae')}
                <input inputMode="numeric" placeholder="0" type="number" />
              </label>
            </div>

            <div className="last-comment">
              <span>{t('lastComment')}</span>
              <p>{lastComment || t('noComment')}</p>
            </div>

            <label className="notes-field">
              {t('observation')}
              <textarea placeholder={t('observationPlaceholder')} rows={3} />
            </label>

            <button type="submit">{t('saveLater')}</button>
          </form>
        </section>
      </div>
    </section>
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

function formatTemperature(value: number | undefined) {
  return value === undefined ? '-' : `${value.toFixed(1)}°C`;
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

function getTitle(tab: TabId, t: TFunction) {
  if (tab === 'pilotage') return t('pilotageTitle');
  if (tab === 'zones') return t('zonesTitle');
  return t('profileTitle');
}

function formatStatus(status: string) {
  return status.replaceAll('_', ' ');
}

function getCurrentRoute(): RouteState {
  const path = window.location.pathname;

  if (path.startsWith('/boxes/')) {
    return {
      tab: 'pilotage',
      boxCode: decodeURIComponent(path.replace('/boxes/', '').replace(/\/$/, '')),
    };
  }

  if (path === '/zones') {
    return { tab: 'zones', boxCode: null };
  }

  if (path === '/profile') {
    return { tab: 'profile', boxCode: null };
  }

  return { tab: 'pilotage', boxCode: null };
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
