import {
  type CSSProperties,
  type FormEvent,
  type PointerEvent as ReactPointerEvent,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react';

import { ApiError, apiGet, apiPatch, apiPost } from './api/client';
import { getBoxStatusPresentation } from './boxStatus';
import ExportsView from './components/ExportsView';
import InteractiveLineageGraph from './components/InteractiveLineageGraph';
import MoveBoxModal from './components/MoveBoxModal';
import SubcultureModal from './components/SubcultureModal';
import type {
  AccountMember,
  AccountMembers,
  BiologicalMeasurement,
  BoxDetail,
  BoxItem,
  BoxLineage,
  BoxMovement,
  BoxMovePayload,
  Dashboard,
  ExportOptions,
  LineageGraph,
  MembershipRole,
  NewMemberPayload,
  PaginatedResponse,
  SubculturePayload,
  SubcultureResult,
  ThermalZone,
  UserProfile,
} from './types';

type TabId = 'pilotage' | 'zones' | 'exports' | 'admin' | 'profile';
type BoxInsightTab = 'measurements' | 'movements' | 'lineage';

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
  zoneId?: number | null;
};

type QrLabelItem = {
  id: number;
  globalCode: string;
  speciesName: string;
  qrImageUrl: string;
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
    adminSubtitle: 'Gestion disponible sur ordinateur',
    adminDesktopOnly: 'Les fonctionnalités admin sont pensées pour la version ordinateur.',
    adminAccountsTitle: 'Gestion des comptes',
    adminAccountsText: 'Créer les accès, vérifier les rôles et préparer les modifications.',
    adminNewUser: '+ Nouvel utilisateur',
    adminRoleAdmin: 'Administrateurs',
    adminRoleTechnician: 'Techniciens',
    adminRoleViewer: 'Lecteurs',
    adminUserColumn: 'Utilisateur',
    adminRoleColumn: 'Rôle',
    adminEmailColumn: 'Email',
    adminLastLoginColumn: 'Dernière connexion',
    adminActionsColumn: 'Actions',
    adminCurrentSession: 'Session active',
    adminChangeRole: 'Modifier rôle',
    adminRemoveAccess: 'Supprimer accès',
    adminNotConnected: 'API à connecter',
    adminZonesProbesTitle: 'Zones thermiques et sondes',
    adminZonesProbesText: 'Créer une armoire ou une étuve, puis y associer une ou plusieurs sondes.',
    adminZoneName: 'Nom de la zone',
    adminZoneType: 'Type de zone',
    adminZoneTypeCabinet: 'Armoire',
    adminZoneTypeIncubator: 'Étuve',
    adminTargetTemperature: 'Température consigne',
    adminCreateZone: 'Créer la zone',
    adminProbeCode: 'Code sonde',
    adminProbeType: 'Type de sonde',
    adminProbeZone: 'Zone associée',
    adminProbeApiUrl: 'URL API',
    adminAddProbe: 'Ajouter la sonde',
    adminOrganizationsTitle: 'Nouvelles institutions',
    adminOrganizationsText: 'Préparer l’ajout d’un aquarium partenaire avec ses informations de contact.',
    adminCountry: 'Pays',
    adminCity: 'Ville',
    adminOrganizationName: 'Nom de l’institution',
    adminContactName: 'Personne contact',
    adminPostalAddress: 'Adresse postale',
    adminContactEmail: 'Email',
    adminContactPhone: 'Téléphone',
    adminAddOrganization: 'Ajouter l’institution',
    adminExistingOrganizations: 'Structures connues',
    adminTransferTitle: 'Transfert entre structures',
    adminTransferText: 'Préparer le transfert d’une boîte vers un autre aquarium sans perdre l’historique.',
    adminTransferBox: 'Boîte à transférer',
    adminTransferTarget: 'Institution destinataire',
    adminTransferPolyps: 'Nombre de polypes transmis',
    adminKeepTransferDate: 'Conserver la date du transfert',
    adminPrepareTransfer: 'Préparer le transfert',
    adminDjangoHint: 'Les actions sensibles restent accessibles dans Django admin tant que les API dédiées ne sont pas créées.',
    adminPrintLabelsAction: 'Imprimer les étiquettes',
    adminPrintLabelsClear: 'Tout décocher',
    adminPrintLabelsHelp: 'Sélectionnez les boîtes à imprimer sur une même feuille.',
    adminPrintLabelsSelectAll: 'Tout sélectionner',
    adminPrintLabelsTitle: 'Étiquettes des boîtes',
    profileRoles: 'Rôles',
    profileEmail: 'Email',
    profileNoEmail: 'Non renseigné',
    profileSuperuser: 'Super-administrateur',
    profileMemberships: 'Structures et rôles',
    profileNoMembership: 'Aucune structure rattachée à ce compte.',
    profilePreferences: 'Préférences',
    profileLanguage: 'Langue de l’interface',
    roleDescAdmin: 'Accès complet : laboratoire, exports et administration.',
    roleDescTechnician: 'Saisie et suivi du laboratoire, sans administration.',
    roleDescViewer: 'Consultation seule des données.',
    manageAccountsTitle: 'Gestion des comptes',
    manageAccountsSubtitle: 'Ajoutez des accès et ajustez les rôles dans vos structures.',
    manageAddTitle: 'Nouvel accès',
    manageColUser: 'Utilisateur',
    manageColOrganization: 'Structure',
    manageColRole: 'Rôle',
    manageColStatus: 'Statut',
    manageColLastLogin: 'Dernière connexion',
    manageStatusActive: 'Actif',
    manageStatusInactive: 'Désactivé',
    manageStatusSelf: 'Vous',
    manageNeverConnected: 'Jamais connecté',
    manageNoMembers: 'Aucun compte à afficher pour vos structures.',
    manageFieldUsername: 'Identifiant',
    manageFieldFirstName: 'Prénom',
    manageFieldLastName: 'Nom',
    manageFieldEmail: 'Email',
    manageFieldPassword: 'Mot de passe initial',
    manageFieldOrganization: 'Structure',
    manageFieldRole: 'Rôle',
    managePasswordHint: 'Requis uniquement pour un nouvel identifiant (8 caractères min.).',
    manageAddAction: 'Ajouter l’accès',
    manageAdding: 'Ajout...',
    manageMemberAdded: 'Accès enregistré.',
    manageRoleUpdated: 'Rôle mis à jour.',
    manageDeactivate: 'Désactiver',
    manageReactivate: 'Réactiver',
    historyButton: 'Voir relevés',
    analysisTabLineage: 'Parenté',
    analysisTabMeasurements: 'Relevés',
    analysisTabMovements: 'Mouvements',
    boxLocalCode: 'Code local',
    boxStrain: 'Souche',
    chartEmpty: 'Pas assez de relevés pour tracer une tendance.',
    chartTitle: 'Évolution des relevés',
    createdOn: 'Créée le',
    lastComment: 'Dernier commentaire',
    lastMeasurement: 'Dernier relevé',
    laboratoryTracking: 'Suivi laboratoire',
    lineageAction: 'Parenté',
    lineageEmptyGraph: 'Le graphique de parenté sera affiché ici.',
    lineageLoading: 'Chargement de la parenté...',
    lineageRetry: 'Recharger',
    loginAction: 'Se connecter',
    loginRequired: 'Connexion requise',
    measurementDate: 'Date du relevé',
    measurementForbidden: 'Ce compte ne peut pas créer de relevé.',
    measurementHistory: 'Historique des relevés',
    measurementSaved: 'Relevé enregistré',
    moveAction: 'Déplacer',
    moveForbidden: 'Ce compte ne peut pas déplacer de boîte.',
    moveSaved: 'Déplacement enregistré',
    movementHistoryTitle: 'Historique des emplacements',
    noMovementHistory: 'Aucun déplacement enregistré pour cette boîte.',
    movedTo: 'Déplacée vers',
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
    print: 'Imprimer',
    prototype: 'prototype',
    qrCode: 'QR code',
    qrLabelDownload: 'Télécharger',
    qrLabelHelp: 'Étiquette prête à imprimer et coller sur la boîte.',
    qrLabelTitle: 'Étiquette QR code',
    qrDownload: 'Télécharger',
    qrScanHint: 'Scannez pour ouvrir cette fiche',
    qrScannerFound: 'Boîte détectée',
    qrScannerPermission: 'Impossible d’ouvrir la caméra.',
    qrScannerStart: 'Scanner',
    qrScannerStop: 'Arrêter',
    qrScannerText: 'Scannez le QR code d’une boîte pour ouvrir directement sa fiche.',
    qrScannerTitle: 'Scan QR code',
    qrScannerUnsupported: 'Scanner indisponible sur ce navigateur.',
    recentAccess: 'Derniers accès',
    holdToSave: 'Maintenir pour enregistrer',
    saveMeasurement: 'Enregistrer le relevé',
    saving: 'Enregistrement...',
    scanSearch: 'scan / recherche',
    searchOrScan: 'Recherche ou scan',
    searchPlaceholder: 'Code boîte, espèce, souche',
    searchTab: 'Recherche',
    suggestions: 'Suggestions',
    subcultureAction: 'Repiquer',
    subcultureForbidden: 'Ce compte ne peut pas créer de repiquage.',
    subcultureSaved: 'Repiquage enregistré',
    temperatureShort: 'Temp.',
    targetTemperature: 'Consigne',
    salinityShort: 'Sal.',
    aliveBoxes: 'Vivantes',
    backToZones: 'Retour aux zones',
    boxAttention: 'À surveiller',
    boxesHealthy: 'Sans alerte',
    deadBoxes: 'Mortes',
    emptyZone: 'Aucune boîte dans cette zone.',
    latestCounts: 'Derniers comptages',
    latestReadingDate: 'Dernière mesure',
    maxTemperature: 'Max.',
    measuredTemperature: 'Température relevée',
    minTemperature: 'Min.',
    noZoneChart: 'Pas assez de relevés récents pour tracer un graphique.',
    openBox: 'Ouvrir',
    problemSummary: 'Surveillance',
    recentMeasurementMissing: 'Sans relevé',
    temperatureControl: 'Contrôle thermique',
    temperatureGap: 'Écart',
    temperatureMissing: 'Aucune température relevée',
    temperatureOk: 'Proche de la consigne',
    temperatureWatch: 'Écart à surveiller',
    zoneSheet: 'Fiche zone thermique',
    zoneBoxesTitle: 'Boîtes dans la zone',
    zoneProbesTitle: 'Sondes associées',
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
    adminSubtitle: 'Desktop administration',
    adminDesktopOnly: 'Administration features are designed for the desktop version.',
    adminAccountsTitle: 'Account management',
    adminAccountsText: 'Create access, check roles and prepare account changes.',
    adminNewUser: '+ New user',
    adminRoleAdmin: 'Administrators',
    adminRoleTechnician: 'Technicians',
    adminRoleViewer: 'Viewers',
    adminUserColumn: 'User',
    adminRoleColumn: 'Role',
    adminEmailColumn: 'Email',
    adminLastLoginColumn: 'Last login',
    adminActionsColumn: 'Actions',
    adminCurrentSession: 'Active session',
    adminChangeRole: 'Change role',
    adminRemoveAccess: 'Remove access',
    adminNotConnected: 'API to connect',
    adminZonesProbesTitle: 'Thermal zones and probes',
    adminZonesProbesText: 'Create a cabinet or incubator, then link one or several probes to it.',
    adminZoneName: 'Zone name',
    adminZoneType: 'Zone type',
    adminZoneTypeCabinet: 'Cabinet',
    adminZoneTypeIncubator: 'Incubator',
    adminTargetTemperature: 'Target temperature',
    adminCreateZone: 'Create zone',
    adminProbeCode: 'Probe code',
    adminProbeType: 'Probe type',
    adminProbeZone: 'Linked zone',
    adminProbeApiUrl: 'API URL',
    adminAddProbe: 'Add probe',
    adminOrganizationsTitle: 'New institutions',
    adminOrganizationsText: 'Prepare a partner aquarium with its contact information.',
    adminCountry: 'Country',
    adminCity: 'City',
    adminOrganizationName: 'Institution name',
    adminContactName: 'Contact person',
    adminPostalAddress: 'Postal address',
    adminContactEmail: 'Email',
    adminContactPhone: 'Phone',
    adminAddOrganization: 'Add institution',
    adminExistingOrganizations: 'Known organizations',
    adminTransferTitle: 'Transfer between organizations',
    adminTransferText: 'Prepare a box transfer to another aquarium without losing history.',
    adminTransferBox: 'Box to transfer',
    adminTransferTarget: 'Target institution',
    adminTransferPolyps: 'Transferred polyps',
    adminKeepTransferDate: 'Keep transfer date',
    adminPrepareTransfer: 'Prepare transfer',
    adminDjangoHint: 'Sensitive actions remain available in Django admin until dedicated APIs are created.',
    adminPrintLabelsAction: 'Print labels',
    adminPrintLabelsClear: 'Clear all',
    adminPrintLabelsHelp: 'Select the boxes to print on the same sheet.',
    adminPrintLabelsSelectAll: 'Select all',
    adminPrintLabelsTitle: 'Box labels',
    profileRoles: 'Roles',
    profileEmail: 'Email',
    profileNoEmail: 'Not provided',
    profileSuperuser: 'Superuser',
    profileMemberships: 'Organizations and roles',
    profileNoMembership: 'No organization linked to this account.',
    profilePreferences: 'Preferences',
    profileLanguage: 'Interface language',
    roleDescAdmin: 'Full access: lab, exports and administration.',
    roleDescTechnician: 'Lab data entry and tracking, no administration.',
    roleDescViewer: 'Read-only access to the data.',
    manageAccountsTitle: 'Account management',
    manageAccountsSubtitle: 'Add access and adjust roles within your organizations.',
    manageAddTitle: 'New access',
    manageColUser: 'User',
    manageColOrganization: 'Organization',
    manageColRole: 'Role',
    manageColStatus: 'Status',
    manageColLastLogin: 'Last login',
    manageStatusActive: 'Active',
    manageStatusInactive: 'Disabled',
    manageStatusSelf: 'You',
    manageNeverConnected: 'Never connected',
    manageNoMembers: 'No account to display for your organizations.',
    manageFieldUsername: 'Username',
    manageFieldFirstName: 'First name',
    manageFieldLastName: 'Last name',
    manageFieldEmail: 'Email',
    manageFieldPassword: 'Initial password',
    manageFieldOrganization: 'Organization',
    manageFieldRole: 'Role',
    managePasswordHint: 'Only required for a new username (min. 8 characters).',
    manageAddAction: 'Add access',
    manageAdding: 'Adding...',
    manageMemberAdded: 'Access saved.',
    manageRoleUpdated: 'Role updated.',
    manageDeactivate: 'Disable',
    manageReactivate: 'Re-enable',
    historyButton: 'View records',
    analysisTabLineage: 'Lineage',
    analysisTabMeasurements: 'Measurements',
    analysisTabMovements: 'Moves',
    boxLocalCode: 'Local code',
    boxStrain: 'Strain',
    chartEmpty: 'Not enough measurements to draw a trend.',
    chartTitle: 'Measurement trend',
    createdOn: 'Created on',
    lastComment: 'Last comment',
    lastMeasurement: 'Last measurement',
    laboratoryTracking: 'Lab tracking',
    lineageAction: 'Lineage',
    lineageEmptyGraph: 'The lineage graph will be shown here.',
    lineageLoading: 'Loading lineage...',
    lineageRetry: 'Reload',
    loginAction: 'Sign in',
    loginRequired: 'Sign-in required',
    measurementDate: 'Measurement date',
    measurementForbidden: 'This account cannot create measurements.',
    measurementHistory: 'Measurement history',
    measurementSaved: 'Measurement saved',
    moveAction: 'Move',
    moveForbidden: 'This account cannot move boxes.',
    moveSaved: 'Movement saved',
    movementHistoryTitle: 'Location history',
    noMovementHistory: 'No movement recorded for this box.',
    movedTo: 'Moved to',
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
    print: 'Print',
    prototype: 'prototype',
    qrCode: 'QR code',
    qrLabelDownload: 'Download',
    qrLabelHelp: 'Label ready to print and attach to the box.',
    qrLabelTitle: 'QR code label',
    qrDownload: 'Download',
    qrScanHint: 'Scan to open this sheet',
    qrScannerFound: 'Box detected',
    qrScannerPermission: 'Unable to open the camera.',
    qrScannerStart: 'Scan',
    qrScannerStop: 'Stop',
    qrScannerText: 'Scan a box QR code to open its sheet directly.',
    qrScannerTitle: 'QR code scan',
    qrScannerUnsupported: 'Scanner unavailable in this browser.',
    recentAccess: 'Recent access',
    holdToSave: 'Hold to save',
    saveMeasurement: 'Save measurement',
    saving: 'Saving...',
    scanSearch: 'scan / search',
    searchOrScan: 'Search or scan',
    searchPlaceholder: 'Box code, species, strain',
    searchTab: 'Search',
    suggestions: 'Suggestions',
    subcultureAction: 'Subculture',
    subcultureForbidden: 'This account cannot create subculture events.',
    subcultureSaved: 'Subculture created',
    temperatureShort: 'Temp.',
    targetTemperature: 'Target',
    salinityShort: 'Sal.',
    aliveBoxes: 'Alive',
    backToZones: 'Back to zones',
    boxAttention: 'Needs attention',
    boxesHealthy: 'No alert',
    deadBoxes: 'Dead',
    emptyZone: 'No box in this zone.',
    latestCounts: 'Latest counts',
    latestReadingDate: 'Latest reading',
    maxTemperature: 'Max.',
    measuredTemperature: 'Measured temperature',
    minTemperature: 'Min.',
    noZoneChart: 'Not enough recent measurements to draw a chart.',
    openBox: 'Open',
    problemSummary: 'Monitoring',
    recentMeasurementMissing: 'No measurement',
    temperatureControl: 'Thermal control',
    temperatureGap: 'Gap',
    temperatureMissing: 'No temperature reading',
    temperatureOk: 'Close to target',
    temperatureWatch: 'Gap to watch',
    zoneSheet: 'Thermal zone sheet',
    zoneBoxesTitle: 'Boxes in this zone',
    zoneProbesTitle: 'Linked probes',
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
  const isZoneRoute = activeTab === 'zones' && route.zoneId != null;
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

  function openZone(zoneId: number) {
    navigateTo({ tab: 'zones', boxCode: null, boxId: null, zoneId }, `/zones/${zoneId}`);
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

  function closeZonePage() {
    navigateTo({ tab: 'zones', boxCode: null, boxId: null, zoneId: null }, '/zones');
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
        {!isBoxRoute && !isZoneRoute ? (
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
                onOpenZone={openZone}
                onBack={closeBoxPage}
                t={t}
              />
            )}

            {activeTab === 'pilotage' && !isBoxRoute && (
              <PilotageView
                boxes={data.boxes}
                isLoading={isLoading}
                search={search}
                suggestions={filteredBoxes.slice(0, 5)}
                recentBoxes={recentBoxes}
                onSearch={setSearch}
                onSelectBox={openBox}
                t={t}
              />
            )}

            {activeTab === 'zones' && (
              route.zoneId != null ? (
                <ZoneDetailPage
                  boxes={data.boxes}
                  isLoading={isLoading}
                  language={language}
                  zone={data.zones.find((zone) => zone.id === route.zoneId) ?? null}
                  onBack={closeZonePage}
                  onOpenBox={openBox}
                  t={t}
                />
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
                isLoading={isLoading}
                options={data.exportOptions}
                language={language}
              />
            )}

            {activeTab === 'admin' && (
              <AdminView
                boxes={data.boxes}
                exportOptions={data.exportOptions}
                isLoading={isLoading}
                profile={data.profile}
                t={t}
                zones={data.zones}
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
  boxes,
  isLoading,
  recentBoxes,
  search,
  suggestions,
  t,
  onSearch,
  onSelectBox,
}: {
  boxes: BoxItem[];
  isLoading: boolean;
  recentBoxes: BoxItem[];
  search: string;
  suggestions: BoxItem[];
  t: TFunction;
  onSearch: (value: string) => void;
  onSelectBox: (id: number) => void;
}) {
  const visibleSuggestions = search.trim() ? suggestions : [];
  const [tabletLookupMode, setTabletLookupMode] = useState<'qr' | 'search'>('qr');

  function selectFirstSuggestion() {
    if (visibleSuggestions[0]) {
      onSelectBox(visibleSuggestions[0].id);
      onSearch(visibleSuggestions[0].global_code);
    }
  }

  return (
    <section className="pilotage-flow">
      <div className="lookup-panel">
        <div className="desktop-search-panel">
          <SearchField value={search} onChange={onSearch} onSubmit={selectFirstSuggestion} t={t} />
        </div>

        <section className="tablet-lookup-panel">
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
            <TabletQrScanner boxes={boxes} onSelectBox={onSelectBox} t={t} />
          ) : (
            <div className="tablet-manual-search">
              <SearchField value={search} onChange={onSearch} onSubmit={selectFirstSuggestion} t={t} />
            </div>
          )}
        </section>

        <div className="mobile-suggestion-slot">
          {tabletLookupMode === 'search' && visibleSuggestions.length > 0 ? (
            <SuggestionList
              boxes={visibleSuggestions}
              selectedBoxId={null}
              onSelectBox={onSelectBox}
              t={t}
            />
          ) : null}
        </div>

        <div className="desktop-suggestion-slot">
          {!isLoading && visibleSuggestions.length > 0 ? (
            <SuggestionList
              boxes={visibleSuggestions}
              selectedBoxId={null}
              onSelectBox={onSelectBox}
              t={t}
            />
          ) : null}
        </div>

        <div className="desktop-only-loading">
          {isLoading ? <SkeletonRows count={3} /> : null}
        </div>

        <div className="tablet-only-loading">
          {isLoading ? <SkeletonRows count={2} /> : null}
        </div>

        {!isLoading && <RecentAccessList boxes={recentBoxes} onSelectBox={onSelectBox} t={t} />}
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
  onOpenZone,
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
  onOpenZone: (zoneId: number) => void;
  onBack: () => void;
  t: TFunction;
}) {
  const [form, setForm] = useState(() => getInitialMeasurementForm());
  const [isSaving, setIsSaving] = useState(false);
  const isDesktopApp = useIsDesktopApp();
  const [isHistoryOpen, setIsHistoryOpen] = useState(false);
  const [lineageGraph, setLineageGraph] = useState<LineageGraph | null>(null);
  const [isLineageGraphLoading, setIsLineageGraphLoading] = useState(false);
  const [lineageGraphError, setLineageGraphError] = useState<string | null>(null);
  const [isMoveOpen, setIsMoveOpen] = useState(false);
  const [isSavingMove, setIsSavingMove] = useState(false);
  const [moveError, setMoveError] = useState<string | null>(null);
  const [moveMessage, setMoveMessage] = useState<string | null>(null);
  const [isSubcultureOpen, setIsSubcultureOpen] = useState(false);
  const [isSavingSubculture, setIsSavingSubculture] = useState(false);
  const [isQrLabelOpen, setIsQrLabelOpen] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [saveMessage, setSaveMessage] = useState<string | null>(null);
  const [subcultureError, setSubcultureError] = useState<string | null>(null);
  const [subcultureMessage, setSubcultureMessage] = useState<string | null>(null);
  const [activeInsightTab, setActiveInsightTab] = useState<BoxInsightTab>('measurements');

  useEffect(() => {
    setForm(getInitialMeasurementForm());
    setIsHistoryOpen(false);
    setLineageGraph(null);
    setIsLineageGraphLoading(false);
    setLineageGraphError(null);
    setIsMoveOpen(false);
    setIsSavingMove(false);
    setMoveError(null);
    setMoveMessage(null);
    setIsSubcultureOpen(false);
    setIsQrLabelOpen(false);
    setSaveError(null);
    setSaveMessage(null);
    setSubcultureError(null);
    setSubcultureMessage(null);
    setActiveInsightTab('measurements');
  }, [box?.id]);

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
  const currentZone = getCurrentThermalZone(box, zones);
  const createdOn = getBoxCreatedDate(box);
  const statusPresentation = getBoxStatusPresentation(box.status, language);

  async function saveMeasurement(): Promise<boolean> {
    if (!box || isSaving) return false;

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
      triggerHaptic([12, 28, 12]);
      return true;
    } catch (requestError) {
      setSaveError(getMeasurementSaveError(requestError, t));
      return false;
    } finally {
      setIsSaving(false);
    }
  }

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!isDesktopApp) return;
    void saveMeasurement();
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
    <section className="box-page">
      <button className="text-button" type="button" onClick={onBack}>
        {t('backToPilotage')}
      </button>

      <header className={`box-sheet-hero is-status-${statusPresentation.tone}`}>
        <div className="box-sheet-identity">
          <div className="box-identity-heading">
            <div>
              <p className="box-page-label">{t('boxSheet')}</p>
              <div className="box-code-line">
                <h2>{box.global_code}</h2>
                <span className={`box-life-status is-${statusPresentation.tone}`}>
                  {statusPresentation.label}
                </span>
              </div>
              <p>{box.species.scientific_name}</p>
            </div>

            {qr ? (
              <button
                className="box-hero-qr"
                type="button"
                title={qr.scanUrl}
                onClick={() => setIsQrLabelOpen(true)}
              >
                <img src={qr.imageUrl} alt={`${t('qrCode')} ${box.global_code}`} width={58} height={58} />
                <span>{t('qrCode')}</span>
              </button>
            ) : null}
          </div>

          <div className="box-small-facts">
            <InfoPill label={t('boxLocalCode')} value={box.local_code || '-'} />
            <InfoPill label={t('boxStrain')} value={box.strain.code} />
            <InfoPill label={t('createdOn')} value={createdOn ? formatDisplayDate(createdOn) : t('noDate')} />
          </div>
        </div>

        <div className="box-zone-summary">
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
          <InfoPill
            label={t('targetTemperature')}
            value={formatTemperatureValue(currentZone?.target_temperature_c ?? box.thermal_zone?.target_temperature_c)}
          />
          <InfoPill label={t('temperatureShort')} value={formatTemperature(currentZone?.latest_temperature?.average_temperature_c)} />
          <InfoPill label={t('salinityShort')} value={formatSalinity(currentZone?.latest_salinity?.salinity_psu)} />
        </div>

        <div className="box-action-stack">
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
        <section className={saveMessage ? 'last-reading-card is-fresh' : 'last-reading-card'}>
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

            <MeasurementSaveButton
              isDesktop={isDesktopApp}
              isSaving={isSaving}
              isSuccess={Boolean(saveMessage)}
              onSave={saveMeasurement}
              t={t}
            />
          </form>
        </section>

        <section className="box-insights-section">
          <BoxInsights
            activeTab={activeInsightTab}
            graph={lineageGraph}
            graphError={lineageGraphError}
            isGraphLoading={isLineageGraphLoading}
            language={language}
            lineage={lineage}
            measurements={measurements}
            movements={getBoxMovements(box)}
            onLoadLineageGraph={handleLoadLineageGraph}
            onOpenHistory={() => setIsHistoryOpen(true)}
            onSelectBox={onOpenBox}
            onSelectTab={setActiveInsightTab}
            t={t}
          />
        </section>

        {isHistoryOpen ? (
          <MeasurementHistoryModal
            measurements={measurements}
            onClose={() => setIsHistoryOpen(false)}
            t={t}
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

        {isQrLabelOpen && qr ? (
          <QrLabelModal
            box={box}
            qrImageUrl={qr.imageUrl}
            onClose={() => setIsQrLabelOpen(false)}
            t={t}
          />
        ) : null}
      </div>
    </section>
  );
}

function QrLabelModal({
  box,
  qrImageUrl,
  onClose,
  t,
}: {
  box: BoxItem | BoxDetail;
  qrImageUrl: string;
  onClose: () => void;
  t: TFunction;
}) {
  const label = buildQrLabelItem(box, qrImageUrl);

  return (
    <div className="modal-backdrop qr-print-backdrop" role="presentation" onClick={onClose}>
      <section
        className="qr-label-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="qr-label-title"
        onClick={(event) => event.stopPropagation()}
      >
        <header className="modal-heading qr-label-modal-heading">
          <div>
            <h2 id="qr-label-title">{t('qrLabelTitle')}</h2>
            <span>{t('qrLabelHelp')}</span>
          </div>
          <button type="button" aria-label={t('close')} onClick={onClose}>
            ×
          </button>
        </header>

        <div className="qr-label-print-sheet">
      <div className="qr-label-main">
        <strong>{label.globalCode}</strong>
        <span>{label.speciesName}</span>
      </div>

          <div className="qr-label-code">
            <img src={label.qrImageUrl} alt={`${t('qrCode')} ${label.globalCode}`} />
            <strong>{t('qrCode')}</strong>
          </div>
        </div>

        <footer className="qr-label-modal-actions">
          <button type="button" className="is-secondary" onClick={() => void downloadQrLabel(label)}>
            {t('qrLabelDownload')}
          </button>
          <button type="button" onClick={() => printQrLabels([label])}>
            {t('print')}
          </button>
        </footer>
      </section>
    </div>
  );
}

function buildQrLabelItem(box: BoxItem | BoxDetail, qrImageUrl?: string): QrLabelItem {
  return {
    id: box.id,
    globalCode: box.global_code,
    speciesName: box.species.scientific_name,
    qrImageUrl: getBoxQrImageUrl(box, qrImageUrl),
  };
}

function getBoxQrImageUrl(box: BoxItem | BoxDetail, explicitUrl?: string) {
  if (explicitUrl) return explicitUrl;
  if ('qr_image_url' in box && box.qr_image_url) return box.qr_image_url;
  return `/boites/${box.id}/qr.svg`;
}

function printQrLabels(labels: QrLabelItem[]) {
  if (!labels.length) return;

  const printWindow = window.open('', '_blank', 'width=980,height=720');
  if (!printWindow) return;

  printWindow.document.write(buildQrPrintDocument(labels));
  printWindow.document.close();

  window.setTimeout(() => {
    printWindow.focus();
    printWindow.print();
  }, 350);
}

async function downloadQrLabel(label: QrLabelItem) {
  const qrDataUrl = await getQrDataUrl(label.qrImageUrl);
  const svg = buildQrLabelSvg(label, qrDataUrl);
  downloadTextFile(svg, `${label.globalCode}_etiquette.svg`, 'image/svg+xml;charset=utf-8');
}

async function getQrDataUrl(qrImageUrl: string) {
  try {
    const response = await fetch(qrImageUrl, { credentials: 'include' });
    if (!response.ok) throw new Error('QR unavailable');
    const svgText = await response.text();
    return `data:image/svg+xml;base64,${window.btoa(unescape(encodeURIComponent(svgText)))}`;
  } catch {
    return new URL(qrImageUrl, window.location.origin).href;
  }
}

function buildQrPrintDocument(labels: QrLabelItem[]) {
  const labelMarkup = labels.map(renderPrintableQrLabel).join('');

  return `<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>Étiquettes Polypbase</title>
<style>
  @page { size: A4; margin: 10mm; }
  * { box-sizing: border-box; }
  body { margin: 0; color: #000; font-family: Arial, sans-serif; }
  .sheet { display: grid; grid-template-columns: repeat(2, 92mm); gap: 7mm; align-items: start; }
  .label { display: grid; grid-template-columns: 1fr 30mm; gap: 5mm; min-height: 38mm; padding: 4.5mm; border: 0.35mm solid #000; border-radius: 2mm; break-inside: avoid; page-break-inside: avoid; }
  .label-main { display: grid; align-content: center; gap: 1.2mm; min-width: 0; }
  .label-code { display: block; width: 100%; font-size: 19pt; font-style: italic; font-weight: 900; line-height: 0.95; overflow-wrap: anywhere; }
  .label-species { font-size: 8.5pt; }
  .label-qr { display: grid; justify-items: center; gap: 1mm; font-size: 7pt; font-weight: 800; text-align: center; }
  .label-qr img { width: 27mm; height: 27mm; image-rendering: pixelated; }
</style>
</head>
<body>
  <main class="sheet">${labelMarkup}</main>
</body>
</html>`;
}

function renderPrintableQrLabel(label: QrLabelItem) {
  return `<section class="label">
  <div class="label-main">
    <strong class="label-code">${escapeHtml(label.globalCode)}</strong>
    <span class="label-species">${escapeHtml(label.speciesName)}</span>
  </div>
  <div class="label-qr">
    <img src="${escapeAttribute(new URL(label.qrImageUrl, window.location.origin).href)}" alt="">
    <span>QR code</span>
  </div>
</section>`;
}

function buildQrLabelSvg(label: QrLabelItem, qrImageUrl: string) {
  const width = 720;
  const height = 300;
  const code = escapeXml(label.globalCode);
  const species = escapeXml(label.speciesName);
  const image = escapeXml(qrImageUrl);

  return `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}" viewBox="0 0 ${width} ${height}">
  <rect x="8" y="8" width="704" height="284" rx="18" fill="#fff" stroke="#000" stroke-width="3"/>
  <text x="34" y="92" font-family="Arial, sans-serif" font-size="56" font-style="italic" font-weight="900" fill="#000">${code}</text>
  <text x="36" y="136" font-family="Arial, sans-serif" font-size="25" fill="#000">${species}</text>
  <rect x="514" y="40" width="154" height="184" rx="14" fill="#fff" stroke="#000" stroke-width="2"/>
  <image href="${image}" x="534" y="58" width="114" height="114"/>
  <text x="591" y="204" text-anchor="middle" font-family="Arial, sans-serif" font-size="18" font-weight="800" fill="#000">QR code</text>
</svg>`;
}

function downloadTextFile(content: string, fileName: string, type: string) {
  const blob = new Blob([content], { type });
  const objectUrl = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = objectUrl;
  link.download = fileName;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(objectUrl);
}

function escapeHtml(value: string) {
  return value
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
}

function escapeXml(value: string) {
  return escapeHtml(value).replace(/"/g, '&quot;').replace(/'/g, '&apos;');
}

function escapeAttribute(value: string) {
  return escapeXml(value);
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
  graph,
  graphError,
  isGraphLoading,
  language,
  lineage,
  measurements,
  movements,
  onLoadLineageGraph,
  onOpenHistory,
  onSelectBox,
  onSelectTab,
  t,
}: {
  activeTab: BoxInsightTab;
  graph: LineageGraph | null;
  graphError: string | null;
  isGraphLoading: boolean;
  language: Language;
  lineage: BoxLineage;
  measurements: BiologicalMeasurement[];
  movements: BoxMovement[];
  onLoadLineageGraph: () => void;
  onOpenHistory: () => void;
  onSelectBox: (boxId: number, globalCode: string) => void;
  onSelectTab: (tab: BoxInsightTab) => void;
  t: TFunction;
}) {
  const tabs: Array<{ id: BoxInsightTab; label: string }> = [
    { id: 'measurements', label: t('analysisTabMeasurements') },
    { id: 'movements', label: t('analysisTabMovements') },
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

      {activeTab === 'movements' ? (
        <div className="insight-panel">
          <div className="insight-heading">
            <h2>{t('movementHistoryTitle')}</h2>
          </div>
          <MovementTimeline movements={movements} t={t} />
        </div>
      ) : null}

      {activeTab === 'lineage' ? (
        <div className="insight-panel">
          <div className="insight-heading">
            <h2>{t('analysisTabLineage')}</h2>
          </div>
          {isGraphLoading ? <p className="lineage-inline-status">{t('lineageLoading')}</p> : null}
          {graphError ? (
            <div className="lineage-inline-status is-error">
              <p>{graphError}</p>
              <button type="button" onClick={onLoadLineageGraph}>{t('lineageRetry')}</button>
            </div>
          ) : null}
          {graph ? (
            <InteractiveLineageGraph
              graph={graph}
              language={language}
              onSelectBox={onSelectBox}
            />
          ) : null}
          {!graph && !isGraphLoading && !graphError ? (
            <div className="lineage-preview">
              <Metric label={t('parents')} value={String(lineage.parents.length)} />
              <Metric label={t('children')} value={String(lineage.children.length)} />
              <p>{t('lineageEmptyGraph')}</p>
            </div>
          ) : null}
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
  const [hoveredIndex, setHoveredIndex] = useState<number | null>(null);
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
  const hoveredMeasurement = hoveredIndex != null ? chartMeasurements[hoveredIndex] : null;
  const hoverX = hoveredIndex != null ? padding.left + hoveredIndex * xStep : null;
  const hoverTop = hoveredMeasurement
    ? Math.min(yScale(hoveredMeasurement.polyp_count), yScale(hoveredMeasurement.ephyrae_count))
    : null;

  return (
    <div className="measurement-chart" aria-label={t('chartTitle')} onPointerLeave={() => setHoveredIndex(null)}>
      <svg viewBox={`0 0 ${width} ${height}`} role="img">
        <line className="chart-axis" x1={padding.left} y1={height - padding.bottom} x2={width - padding.right} y2={height - padding.bottom} />
        <line className="chart-axis" x1={padding.left} y1={padding.top} x2={padding.left} y2={height - padding.bottom} />
        {[0.25, 0.5, 0.75].map((ratio) => {
          const y = padding.top + ratio * (height - padding.top - padding.bottom);
          return <line key={ratio} className="chart-grid-line" x1={padding.left} y1={y} x2={width - padding.right} y2={y} />;
        })}
        <polyline className="chart-line is-polyps" points={toPoints((measurement) => measurement.polyp_count)} />
        <polyline className="chart-line is-ephyrae" points={toPoints((measurement) => measurement.ephyrae_count)} />
        {hoveredMeasurement && hoverX != null ? (
          <line
            className="chart-hover-line"
            x1={hoverX}
            y1={padding.top}
            x2={hoverX}
            y2={height - padding.bottom}
          />
        ) : null}
        {chartMeasurements.map((measurement, index) => (
          <g key={measurement.id}>
            <circle
              className={hoveredIndex === index ? 'chart-dot is-polyps is-active' : 'chart-dot is-polyps'}
              cx={padding.left + index * xStep}
              cy={yScale(measurement.polyp_count)}
              r={hoveredIndex === index ? '5' : '3.5'}
            />
            <circle
              className={hoveredIndex === index ? 'chart-dot is-ephyrae is-active' : 'chart-dot is-ephyrae'}
              cx={padding.left + index * xStep}
              cy={yScale(measurement.ephyrae_count)}
              r={hoveredIndex === index ? '5' : '3.5'}
            />
          </g>
        ))}
        {chartMeasurements.map((measurement, index) => {
          const x = padding.left + index * xStep;
          const hitWidth = Math.max(26, xStep * 0.82);

          return (
            <rect
              key={`hit-${measurement.id}`}
              className="chart-hit-area"
              x={x - hitWidth / 2}
              y={padding.top}
              width={hitWidth}
              height={height - padding.top - padding.bottom}
              tabIndex={0}
              onBlur={() => setHoveredIndex(null)}
              onFocus={() => setHoveredIndex(index)}
              onPointerEnter={() => setHoveredIndex(index)}
            />
          );
        })}
        <text className="chart-label" x={padding.left} y={height - 12}>{formatDisplayDate(firstDate)}</text>
        <text className="chart-label is-end" x={width - padding.right} y={height - 12}>{formatDisplayDate(lastDate)}</text>
        <text className="chart-y-label" x={padding.left - 8} y={padding.top + 4}>{maxValue}</text>
        <text className="chart-y-label" x={padding.left - 8} y={height - padding.bottom + 4}>0</text>
      </svg>

      {hoveredMeasurement && hoverX != null && hoverTop != null ? (
        <div
          className="chart-tooltip"
          style={{
            left: `${(hoverX / width) * 100}%`,
            top: `${Math.max(8, (hoverTop / height) * 100 - 8)}%`,
          }}
        >
          <strong>{formatDisplayDate(hoveredMeasurement.measured_on)}</strong>
          <span>{t('polyps')} : {hoveredMeasurement.polyp_count}</span>
          <span>{t('ephyraeFull')} : {hoveredMeasurement.ephyrae_count}</span>
        </div>
      ) : null}

      <div className="chart-legend">
        <span className="is-polyps">{t('polyps')}</span>
        <span className="is-ephyrae">{t('ephyraeFull')}</span>
      </div>
    </div>
  );
}

function MovementTimeline({
  movements,
  t,
}: {
  movements: BoxMovement[];
  t: TFunction;
}) {
  const sortedMovements = [...movements]
    .sort((left, right) => right.moved_at.localeCompare(left.moved_at));

  if (!sortedMovements.length) {
    return <p className="muted compact-text movement-empty">{t('noMovementHistory')}</p>;
  }

  return (
    <div className="movement-timeline">
      {sortedMovements.map((movement) => (
        <article key={movement.id}>
          <time>{formatDisplayDate(movement.moved_at)}</time>
          <div>
            <strong>
              {movement.from_thermal_zone
                ? `${movement.from_thermal_zone.name} → ${movement.to_thermal_zone.name}`
                : `${t('movedTo')} ${movement.to_thermal_zone.name}`}
            </strong>
            {movement.user ? <small>{movement.user}</small> : null}
            {movement.notes ? <p>{movement.notes}</p> : null}
          </div>
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

function ZonesView({
  boxes,
  isLoading,
  zones,
  onOpenZone,
  t,
}: {
  boxes: BoxItem[];
  isLoading: boolean;
  zones: ThermalZone[];
  onOpenZone: (id: number) => void;
  t: TFunction;
}) {
  return (
    <section className="single-panel">
      {isLoading ? (
        <SkeletonRows count={5} />
      ) : (
        <div className="zone-overview-grid">
          {zones.map((zone) => {
            const zoneBoxes = boxes.filter((box) => box.thermal_zone?.id === zone.id);
            const zoneAliveCount = zoneBoxes.filter((box) => box.status === 'active').length;

            return (
              <button
                className="zone-card"
                key={zone.id}
                type="button"
                onClick={() => onOpenZone(zone.id)}
              >
                <span className="zone-card-heading">
                  <span>
                    <strong>{zone.name}</strong>
                    <small>{zone.organization.name}</small>
                  </span>
                  <span className={zone.is_active ? 'zone-state is-active' : 'zone-state'}>
                    {zone.zone_type}
                  </span>
                </span>
                <span className="zone-card-metrics">
                  <Metric label={t('temperatureShort')} value={formatTemperature(zone.latest_temperature?.average_temperature_c)} />
                  <Metric label={t('salinityShort')} value={formatSalinity(zone.latest_salinity?.salinity_psu)} />
                  <Metric label={t('aliveBoxes')} value={String(zoneAliveCount)} />
                  <Metric label={t('boxes')} value={String(zone.box_count)} />
                </span>
              </button>
            );
          })}
        </div>
      )}
    </section>
  );
}

function ZoneDetailPage({
  boxes,
  isLoading,
  language,
  zone,
  onBack,
  onOpenBox,
  t,
}: {
  boxes: BoxItem[];
  isLoading: boolean;
  language: Language;
  zone: ThermalZone | null;
  onBack: () => void;
  onOpenBox: (id: number) => void;
  t: TFunction;
}) {
  if (isLoading) {
    return (
      <section className="zone-page">
        <SkeletonRows count={4} />
      </section>
    );
  }

  if (!zone) {
    return (
      <section className="zone-page">
        <button className="text-button" type="button" onClick={onBack}>{t('backToZones')}</button>
        <p className="muted compact-text">{t('noZone')}</p>
      </section>
    );
  }

  const zoneBoxes = boxes.filter((box) => box.thermal_zone?.id === zone.id);

  return (
    <section className="zone-page">
      <button className="text-button zone-back-button" type="button" onClick={onBack}>
        {t('backToZones')}
      </button>

      <header className="zone-sheet-hero">
        <div>
          <p className="box-page-label">{t('zoneSheet')}</p>
          <h2>{zone.name}</h2>
          <span>{zone.organization.name}</span>
        </div>
        <span className={zone.is_active ? 'zone-state is-active' : 'zone-state'}>
          {zone.zone_type}
        </span>
      </header>

      <TemperatureControlPanel zone={zone} t={t} />

      <div className="zone-page-grid">
        <section className="zone-page-section zone-boxes-section">
          <div className="section-title">
            <h2>{t('zoneBoxesTitle')}</h2>
            <span>{zoneBoxes.length}</span>
          </div>
          {zoneBoxes.length ? (
            <div className="zone-box-list">
              {zoneBoxes.map((box) => {
                const status = getBoxStatusPresentation(box.status, language);

                return (
                  <button
                    className={`zone-box-row is-${status.tone}`}
                    key={box.id}
                    type="button"
                    onClick={() => onOpenBox(box.id)}
                  >
                    <span className="zone-box-main">
                      <strong>{box.global_code}</strong>
                      <small>{box.species.scientific_name}</small>
                    </span>
                    <span className="zone-box-reading">
                      {box.latest_measurement ? (
                        <>
                          <strong>
                            {box.latest_measurement.polyp_count} {t('polyps')} / {box.latest_measurement.ephyrae_count} {t('ephyrae')}
                          </strong>
                          <small>{formatDisplayDate(box.latest_measurement.measured_on)}</small>
                        </>
                      ) : (
                        <strong>{t('recentMeasurementMissing')}</strong>
                      )}
                    </span>
                    {box.active_alert_count > 0 ? (
                      <span className="zone-alert-pill">{box.active_alert_count}</span>
                    ) : null}
                    <span className={`box-life-status is-${status.tone}`}>
                      {status.label}
                    </span>
                  </button>
                );
              })}
            </div>
          ) : (
            <p className="muted compact-text">{t('emptyZone')}</p>
          )}
        </section>

        <div className="zone-secondary-stack">
          <section className="zone-page-section zone-chart-section">
            <div className="section-title">
              <h2>{t('latestCounts')}</h2>
              <span>{zoneBoxes.length}</span>
            </div>
            <ZoneLatestCountsChart boxes={zoneBoxes} t={t} />
          </section>

          <section className="zone-page-section">
            <div className="section-title">
              <h2>{t('zoneProbesTitle')}</h2>
              <span>{zone.probes.length}</span>
            </div>
            <div className="probe-list">
              {zone.probes.length ? zone.probes.map((probe) => (
                <p key={probe.id}>
                  <strong>{probe.code}</strong>
                  <span>{probe.probe_type}</span>
                </p>
              )) : <p className="muted compact-text">-</p>}
            </div>
          </section>
        </div>
      </div>
    </section>
  );
}

function TemperatureControlPanel({ zone, t }: { zone: ThermalZone; t: TFunction }) {
  const targetTemperature = parseTemperatureNumber(zone.target_temperature_c);
  const measuredTemperature = zone.latest_temperature?.average_temperature_c ?? null;
  const minTemperature = zone.latest_temperature?.min_temperature_c ?? null;
  const maxTemperature = zone.latest_temperature?.max_temperature_c ?? null;
  const hasTemperature = measuredTemperature !== null && targetTemperature !== null;
  const delta = hasTemperature ? measuredTemperature - targetTemperature : null;
  const absoluteDelta = delta === null ? null : Math.abs(delta);
  const statusClass = absoluteDelta === null
    ? 'is-missing'
    : absoluteDelta <= 0.5
      ? 'is-ok'
      : 'is-watch';
  const measuredLeft = hasTemperature ? getTemperatureMarkerPosition(measuredTemperature, targetTemperature) : 50;
  const minLeft = hasTemperature && minTemperature !== null
    ? getTemperatureMarkerPosition(minTemperature, targetTemperature)
    : null;
  const maxLeft = hasTemperature && maxTemperature !== null
    ? getTemperatureMarkerPosition(maxTemperature, targetTemperature)
    : null;
  const rangeStart = minLeft !== null && maxLeft !== null
    ? Math.min(minLeft, maxLeft)
    : measuredLeft;
  const rangeWidth = minLeft !== null && maxLeft !== null
    ? Math.max(Math.abs(maxLeft - minLeft), 0.6)
    : 0.6;
  const [isGaugeReady, setIsGaugeReady] = useState(false);

  useEffect(() => {
    setIsGaugeReady(false);
    const animationFrame = window.requestAnimationFrame(() => setIsGaugeReady(true));
    return () => window.cancelAnimationFrame(animationFrame);
  }, [zone.id, targetTemperature, measuredTemperature, minTemperature, maxTemperature]);

  const gaugeStyle = {
    '--temperature-current-position': `${measuredLeft}%`,
    '--temperature-range-start': `${rangeStart}%`,
    '--temperature-range-width': `${rangeWidth}%`,
  } as CSSProperties;

  return (
    <section className={`zone-temperature-panel ${statusClass}`}>
      <div className="zone-temperature-heading">
        <div>
          <h2>{t('temperatureControl')}</h2>
          <p>{zone.latest_temperature ? formatDisplayDate(zone.latest_temperature.date) : t('temperatureMissing')}</p>
        </div>
      </div>

      <div
        className={isGaugeReady ? 'temperature-gauge is-ready' : 'temperature-gauge'}
        style={gaugeStyle}
        aria-label={t('temperatureControl')}
      >
        <span className="temperature-gauge-safe-band" aria-hidden="true" />
        <span className="temperature-gauge-track" aria-hidden="true" />
        {hasTemperature ? <span className="temperature-gauge-range" aria-hidden="true" /> : null}
        {targetTemperature !== null ? (
          <span className="temperature-gauge-target" aria-hidden="true">
            <span>{formatTemperature(targetTemperature)}</span>
          </span>
        ) : null}
        {minLeft !== null ? <span className="temperature-gauge-cap is-min" style={{ left: `${minLeft}%` }} aria-hidden="true" /> : null}
        {maxLeft !== null ? <span className="temperature-gauge-cap is-max" style={{ left: `${maxLeft}%` }} aria-hidden="true" /> : null}
        {hasTemperature ? (
          <span className="temperature-gauge-current">
            {measuredTemperature === null ? '-' : formatTemperature(measuredTemperature)}
          </span>
        ) : null}
      </div>

      <div className="temperature-scale-labels">
        <span>{targetTemperature === null ? '-' : formatTemperature(targetTemperature - 3)}</span>
        <strong>{t('targetTemperature')}</strong>
        <span>{targetTemperature === null ? '-' : formatTemperature(targetTemperature + 3)}</span>
      </div>

      <div className="temperature-details-grid">
        <Metric label={t('targetTemperature')} value={formatTemperatureValue(zone.target_temperature_c)} />
        <Metric label={t('measuredTemperature')} value={formatTemperature(measuredTemperature ?? undefined)} />
        <Metric label={t('minTemperature')} value={formatTemperature(minTemperature ?? undefined)} />
        <Metric label={t('maxTemperature')} value={formatTemperature(maxTemperature ?? undefined)} />
      </div>
    </section>
  );
}

function ZoneLatestCountsChart({ boxes, t }: { boxes: BoxItem[]; t: TFunction }) {
  const measuredBoxes = boxes
    .filter((box) => box.latest_measurement)
    .slice(0, 8);

  if (!measuredBoxes.length) {
    return <p className="muted compact-text chart-empty">{t('noZoneChart')}</p>;
  }

  const maxValue = Math.max(
    1,
    ...measuredBoxes.flatMap((box) => [
      box.latest_measurement?.polyp_count ?? 0,
      box.latest_measurement?.ephyrae_count ?? 0,
    ]),
  );

  return (
    <div className="zone-count-chart">
      {measuredBoxes.map((box) => {
        const measurement = box.latest_measurement;
        if (!measurement) return null;
        const polypWidth = `${Math.max(2, (measurement.polyp_count / maxValue) * 100)}%`;
        const ephyraeWidth = `${Math.max(2, (measurement.ephyrae_count / maxValue) * 100)}%`;

        return (
          <div className="zone-count-row" key={box.id}>
            <div>
              <strong>{box.global_code}</strong>
              <small>{formatDisplayDate(measurement.measured_on)}</small>
            </div>
            <div className="zone-count-bars">
              <span className="zone-count-bar is-polyps" style={{ width: polypWidth }}>
                {measurement.polyp_count}
              </span>
              <span className="zone-count-bar is-ephyrae" style={{ width: ephyraeWidth }}>
                {measurement.ephyrae_count}
              </span>
            </div>
          </div>
        );
      })}
      <div className="chart-legend">
        <span className="is-polyps">{t('polyps')}</span>
        <span className="is-ephyrae">{t('ephyraeFull')}</span>
      </div>
    </div>
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

  const fullName = [profile.first_name, profile.last_name].filter(Boolean).join(' ') || profile.username;
  const initials = getProfileInitials(profile);
  const highestRoleLabel = getHighestRoleLabel(profile);

  return (
    <section className="profile-page">
      <header className="profile-identity-card">
        <div className="profile-avatar" aria-hidden="true">
          {initials}
        </div>
        <div className="profile-identity-main">
          <p className="eyebrow">{t('account')}</p>
          <h2>{fullName}</h2>
          <p className="profile-username">@{profile.username}</p>
          <div className="profile-identity-meta">
            <span className="profile-meta-item">
              <small>{t('profileEmail')}</small>
              {profile.email || t('profileNoEmail')}
            </span>
            {highestRoleLabel ? <span className="profile-badge">{highestRoleLabel}</span> : null}
            {profile.is_superuser ? (
              <span className="profile-badge is-super">{t('profileSuperuser')}</span>
            ) : null}
          </div>
        </div>
      </header>

      <section className="profile-block">
        <div className="section-title">
          <h2>{t('profileMemberships')}</h2>
          <span>{profile.memberships.length}</span>
        </div>

        {profile.memberships.length ? (
          <div className="profile-membership-list">
            {profile.memberships.map((membership) => (
              <article
                key={`${membership.organization.id}-${membership.role}`}
                className="profile-membership-card"
              >
                <div className="profile-membership-head">
                  <strong>{membership.organization.name}</strong>
                  <span className={`profile-role-tag is-${membership.role}`}>
                    {membership.role_label}
                  </span>
                </div>
                <p>{t(getRoleDescriptionKey(membership.role))}</p>
              </article>
            ))}
          </div>
        ) : (
          <p className="muted compact-text">{t('profileNoMembership')}</p>
        )}
      </section>

      <section className="profile-block">
        <div className="section-title">
          <h2>{t('profilePreferences')}</h2>
        </div>
        <p className="muted compact-text">{t('profileLanguage')}</p>

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
    </section>
  );
}

const emptyMemberForm = {
  username: '',
  first_name: '',
  last_name: '',
  email: '',
  password: '',
};

function AccountManagementSection({ t }: { t: TFunction }) {
  const [data, setData] = useState<AccountMembers | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [form, setForm] = useState(emptyMemberForm);
  const [organizationId, setOrganizationId] = useState<number | null>(null);
  const [role, setRole] = useState<MembershipRole>('viewer');
  const [isAdding, setIsAdding] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [rowBusyId, setRowBusyId] = useState<number | null>(null);

  useEffect(() => {
    let isActive = true;

    async function loadMembers() {
      try {
        setIsLoading(true);
        const response = await apiGet<AccountMembers>('/api/accounts/members/');
        if (!isActive) return;
        setData(response);
        setOrganizationId(response.manageable_organizations[0]?.id ?? null);
      } catch (requestError) {
        if (!isActive) return;
        setLoadError(getErrorMessage(requestError));
      } finally {
        if (isActive) setIsLoading(false);
      }
    }

    loadMembers();
    return () => {
      isActive = false;
    };
  }, []);

  function upsertMember(updated: AccountMember) {
    setData((current) => {
      if (!current) return current;
      const exists = current.members.some((member) => member.membership_id === updated.membership_id);
      const members = exists
        ? current.members.map((member) =>
            member.membership_id === updated.membership_id ? updated : member,
          )
        : [...current.members, updated];
      members.sort(
        (a, b) =>
          a.organization.name.localeCompare(b.organization.name) ||
          a.username.localeCompare(b.username),
      );
      return { ...current, members };
    });
  }

  async function handleAddMember(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (isAdding || organizationId == null) return;

    setIsAdding(true);
    setFormError(null);
    setMessage(null);

    const payload: NewMemberPayload = {
      ...form,
      username: form.username.trim(),
      organization_id: organizationId,
      role,
    };

    try {
      const member = await apiPost<AccountMember>('/api/accounts/members/', payload);
      upsertMember(member);
      setForm(emptyMemberForm);
      setRole('viewer');
      setMessage(t('manageMemberAdded'));
    } catch (requestError) {
      setFormError(getErrorMessage(requestError));
    } finally {
      setIsAdding(false);
    }
  }

  async function handleRoleChange(member: AccountMember, nextRole: MembershipRole) {
    if (nextRole === member.role) return;
    setRowBusyId(member.membership_id);
    setMessage(null);
    setLoadError(null);
    try {
      const updated = await apiPatch<AccountMember>(
        `/api/accounts/members/${member.membership_id}/`,
        { role: nextRole },
      );
      upsertMember(updated);
      setMessage(t('manageRoleUpdated'));
    } catch (requestError) {
      setLoadError(getErrorMessage(requestError));
    } finally {
      setRowBusyId(null);
    }
  }

  async function handleToggleActive(member: AccountMember) {
    setRowBusyId(member.membership_id);
    setMessage(null);
    setLoadError(null);
    try {
      const updated = await apiPatch<AccountMember>(
        `/api/accounts/members/${member.membership_id}/`,
        { is_active: !member.is_active },
      );
      upsertMember(updated);
    } catch (requestError) {
      setLoadError(getErrorMessage(requestError));
    } finally {
      setRowBusyId(null);
    }
  }

  if (isLoading) {
    return (
      <section className="profile-block">
        <div className="section-title">
          <h2>{t('manageAccountsTitle')}</h2>
        </div>
        <SkeletonRows count={3} />
      </section>
    );
  }

  if (loadError && !data) {
    return (
      <section className="profile-block">
        <div className="section-title">
          <h2>{t('manageAccountsTitle')}</h2>
        </div>
        <p className="inline-error">{loadError}</p>
      </section>
    );
  }

  if (!data) return null;

  const organizations = data.manageable_organizations;
  const roles = data.roles;

  return (
    <section className="admin-section account-management">
      <div className="admin-section-heading account-management-heading">
        <div>
          <h2>{t('manageAccountsTitle')}</h2>
          <p>{t('manageAccountsSubtitle')}</p>
        </div>
        <span className="account-count">{data.members.length}</span>
      </div>

      <form className="member-add-form" onSubmit={handleAddMember}>
        <p className="member-add-title">{t('manageAddTitle')}</p>
        <div className="member-add-grid">
          <label>
            {t('manageFieldUsername')}
            <input
              required
              value={form.username}
              onChange={(event) => setForm((current) => ({ ...current, username: event.target.value }))}
            />
          </label>
          <label>
            {t('manageFieldFirstName')}
            <input
              value={form.first_name}
              onChange={(event) => setForm((current) => ({ ...current, first_name: event.target.value }))}
            />
          </label>
          <label>
            {t('manageFieldLastName')}
            <input
              value={form.last_name}
              onChange={(event) => setForm((current) => ({ ...current, last_name: event.target.value }))}
            />
          </label>
          <label>
            {t('manageFieldEmail')}
            <input
              type="email"
              value={form.email}
              onChange={(event) => setForm((current) => ({ ...current, email: event.target.value }))}
            />
          </label>
          <label className="member-add-password">
            {t('manageFieldPassword')}
            <input
              type="password"
              autoComplete="new-password"
              value={form.password}
              onChange={(event) => setForm((current) => ({ ...current, password: event.target.value }))}
            />
            <small>{t('managePasswordHint')}</small>
          </label>
          {organizations.length > 1 ? (
            <label>
              {t('manageFieldOrganization')}
              <select
                value={organizationId ?? ''}
                onChange={(event) => setOrganizationId(Number(event.target.value))}
              >
                {organizations.map((organization) => (
                  <option key={organization.id} value={organization.id}>
                    {organization.name}
                  </option>
                ))}
              </select>
            </label>
          ) : null}
          <label>
            {t('manageFieldRole')}
            <select value={role} onChange={(event) => setRole(event.target.value as MembershipRole)}>
              {roles.map((roleOption) => (
                <option key={roleOption.value} value={roleOption.value}>
                  {roleOption.label}
                </option>
              ))}
            </select>
          </label>
        </div>

        {formError ? <p className="inline-error">{formError}</p> : null}

        <button type="submit" disabled={isAdding || organizationId == null}>
          {isAdding ? t('manageAdding') : t('manageAddAction')}
        </button>
      </form>

      {message ? <p className="inline-success">{message}</p> : null}
      {loadError && data ? <p className="inline-error">{loadError}</p> : null}

      {data.members.length ? (
        <div className="member-table">
          <div className="member-table-head">
            <span>{t('manageColUser')}</span>
            <span>{t('manageColOrganization')}</span>
            <span>{t('manageColRole')}</span>
            <span>{t('manageColLastLogin')}</span>
            <span>{t('manageColStatus')}</span>
          </div>
          {data.members.map((member) => (
            <div
              key={member.membership_id}
              className={member.is_active ? 'member-table-row' : 'member-table-row is-inactive'}
            >
              <span className="member-identity">
                <strong>{member.full_name}</strong>
                <small>
                  @{member.username}
                  {member.email ? ` · ${member.email}` : ''}
                </small>
              </span>
              <span>{member.organization.name}</span>
              <span>
                <select
                  value={member.role}
                  disabled={member.is_self || rowBusyId === member.membership_id}
                  onChange={(event) =>
                    handleRoleChange(member, event.target.value as MembershipRole)
                  }
                >
                  {roles.map((roleOption) => (
                    <option key={roleOption.value} value={roleOption.value}>
                      {roleOption.label}
                    </option>
                  ))}
                </select>
              </span>
              <span className="member-last-login">
                {member.last_login ? formatDisplayDate(member.last_login) : t('manageNeverConnected')}
              </span>
              <span className="member-status">
                {member.is_self ? (
                  <em className="member-self-tag">{t('manageStatusSelf')}</em>
                ) : (
                  <button
                    type="button"
                    className="member-toggle"
                    disabled={rowBusyId === member.membership_id}
                    onClick={() => handleToggleActive(member)}
                  >
                    {member.is_active ? t('manageDeactivate') : t('manageReactivate')}
                  </button>
                )}
                <span className={member.is_active ? 'member-state is-on' : 'member-state is-off'}>
                  {member.is_active ? t('manageStatusActive') : t('manageStatusInactive')}
                </span>
              </span>
            </div>
          ))}
        </div>
      ) : (
        <p className="muted compact-text">{t('manageNoMembers')}</p>
      )}
    </section>
  );
}

function getRoleDescriptionKey(role: UserProfile['memberships'][number]['role']): TranslationKey {
  switch (role) {
    case 'admin':
      return 'roleDescAdmin';
    case 'lab_technician':
      return 'roleDescTechnician';
    default:
      return 'roleDescViewer';
  }
}

function getHighestRoleLabel(profile: UserProfile): string | null {
  const priority: Record<string, number> = { admin: 3, lab_technician: 2, viewer: 1 };
  const ranked = [...profile.memberships].sort(
    (a, b) => (priority[b.role] ?? 0) - (priority[a.role] ?? 0),
  );
  return ranked[0]?.role_label ?? null;
}

function getProfileInitials(profile: UserProfile): string {
  const fromName = [profile.first_name, profile.last_name]
    .filter(Boolean)
    .map((part) => part.charAt(0))
    .join('');
  const initials = fromName || profile.username.slice(0, 2);
  return initials.toUpperCase();
}

function AdminView({
  boxes,
  exportOptions,
  isLoading,
  profile,
  t,
  zones,
}: {
  boxes: BoxItem[];
  exportOptions: ExportOptions | null;
  isLoading: boolean;
  profile: UserProfile | null;
  t: TFunction;
  zones: ThermalZone[];
}) {
  const [selectedLabelBoxIds, setSelectedLabelBoxIds] = useState<number[]>([]);

  if (isLoading) {
    return (
      <section className="single-panel">
        <SkeletonRows count={4} />
      </section>
    );
  }

  if (!profile || !userHasAdminRole(profile)) return null;

  const organizations = exportOptions?.organizations ?? profile.organizations;
  const activeBoxes = boxes.filter((box) => box.status === 'active');
  const selectedLabelBoxes = boxes.filter((box) => selectedLabelBoxIds.includes(box.id));
  const zoneChoices = zones.filter((zone) => zone.is_active);

  function toggleLabelBox(boxId: number) {
    setSelectedLabelBoxIds((current) => (
      current.includes(boxId)
        ? current.filter((id) => id !== boxId)
        : [...current, boxId]
    ));
  }

  function printSelectedLabels() {
    printQrLabels(selectedLabelBoxes.map((box) => buildQrLabelItem(box)));
  }

  return (
    <section className="admin-panel">
      <AccountManagementSection t={t} />

      <section className="admin-section admin-label-section">
        <div className="admin-section-heading">
          <div>
            <h2>{t('adminPrintLabelsTitle')}</h2>
            <p>{t('adminPrintLabelsHelp')}</p>
          </div>
          <div className="admin-label-actions">
            <button type="button" onClick={() => setSelectedLabelBoxIds(boxes.map((box) => box.id))}>
              {t('adminPrintLabelsSelectAll')}
            </button>
            <button type="button" onClick={() => setSelectedLabelBoxIds([])}>
              {t('adminPrintLabelsClear')}
            </button>
          </div>
        </div>

        <div className="admin-label-selector">
          {boxes.map((box) => (
            <label key={box.id}>
              <input
                type="checkbox"
                checked={selectedLabelBoxIds.includes(box.id)}
                onChange={() => toggleLabelBox(box.id)}
              />
              <span>
                <strong>{box.global_code}</strong>
                <small>{box.species.scientific_name}</small>
              </span>
              <em>{box.thermal_zone?.name ?? t('noZone')}</em>
            </label>
          ))}
        </div>

        <button
          className="admin-print-labels-button"
          type="button"
          disabled={!selectedLabelBoxes.length}
          onClick={printSelectedLabels}
        >
          {t('adminPrintLabelsAction')} ({selectedLabelBoxes.length})
        </button>
      </section>

      <div className="admin-two-columns">
        <section className="admin-section">
          <div className="admin-section-heading">
            <div>
              <h2>{t('adminZonesProbesTitle')}</h2>
              <p>{t('adminZonesProbesText')}</p>
            </div>
          </div>

          <div className="admin-form-grid">
            <form className="admin-form" onSubmit={(event) => event.preventDefault()}>
              <label>
                <span>{t('adminZoneName')}</span>
                <input placeholder="Étuve 13" type="text" />
              </label>
              <label>
                <span>{t('adminZoneType')}</span>
                <select defaultValue="cabinet">
                  <option value="cabinet">{t('adminZoneTypeCabinet')}</option>
                  <option value="incubator">{t('adminZoneTypeIncubator')}</option>
                </select>
              </label>
              <label>
                <span>{t('adminTargetTemperature')}</span>
                <input placeholder="15.0" type="number" />
              </label>
              <button type="submit" disabled>{t('adminCreateZone')}</button>
            </form>

            <form className="admin-form" onSubmit={(event) => event.preventDefault()}>
              <label>
                <span>{t('adminProbeCode')}</span>
                <input placeholder="SONDE-15-01" type="text" />
              </label>
              <label>
                <span>{t('adminProbeType')}</span>
                <input placeholder="température" type="text" />
              </label>
              <label>
                <span>{t('adminProbeZone')}</span>
                <select defaultValue="">
                  <option value="" disabled>{t('noZone')}</option>
                  {zoneChoices.map((zone) => (
                    <option key={zone.id} value={zone.id}>{zone.name}</option>
                  ))}
                </select>
              </label>
              <label>
                <span>{t('adminProbeApiUrl')}</span>
                <input placeholder="https://..." type="url" />
              </label>
              <button type="submit" disabled>{t('adminAddProbe')}</button>
            </form>
          </div>
        </section>

        <section className="admin-section">
          <div className="admin-section-heading">
            <div>
              <h2>{t('adminOrganizationsTitle')}</h2>
              <p>{t('adminOrganizationsText')}</p>
            </div>
          </div>

          <form className="admin-form admin-organization-form" onSubmit={(event) => event.preventDefault()}>
            <label>
              <span>{t('adminOrganizationName')}</span>
              <input type="text" />
            </label>
            <label>
              <span>{t('adminCountry')}</span>
              <input type="text" />
            </label>
            <label>
              <span>{t('adminCity')}</span>
              <input type="text" />
            </label>
            <label>
              <span>{t('adminContactName')}</span>
              <input type="text" />
            </label>
            <label>
              <span>{t('adminContactEmail')}</span>
              <input type="email" />
            </label>
            <label>
              <span>{t('adminContactPhone')}</span>
              <input type="tel" />
            </label>
            <label className="admin-wide-field">
              <span>{t('adminPostalAddress')}</span>
              <textarea rows={3} />
            </label>
            <button type="submit" disabled>{t('adminAddOrganization')}</button>
          </form>

          <div className="admin-inline-list">
            <strong>{t('adminExistingOrganizations')}</strong>
            <div>
              {organizations.slice(0, 4).map((organization) => (
                <span key={organization.id}>{organization.name}</span>
              ))}
            </div>
          </div>
        </section>
      </div>

      <section className="admin-section admin-transfer-section">
        <div className="admin-section-heading">
          <div>
            <h2>{t('adminTransferTitle')}</h2>
            <p>{t('adminTransferText')}</p>
          </div>
        </div>

        <form className="admin-transfer-form" onSubmit={(event) => event.preventDefault()}>
          <label>
            <span>{t('adminTransferBox')}</span>
            <select defaultValue="">
              <option value="" disabled>{t('boxes')}</option>
              {activeBoxes.slice(0, 12).map((box) => (
                <option key={box.id} value={box.id}>{box.global_code}</option>
              ))}
            </select>
          </label>
          <label>
            <span>{t('adminTransferTarget')}</span>
            <select defaultValue="">
              <option value="" disabled>{t('adminOrganizations')}</option>
              {organizations.map((organization) => (
                <option key={organization.id} value={organization.id}>{organization.name}</option>
              ))}
            </select>
          </label>
          <label>
            <span>{t('adminTransferPolyps')}</span>
            <input min="0" placeholder="0" type="number" />
          </label>
          <label className="admin-checkbox">
            <input defaultChecked type="checkbox" />
            <span>{t('adminKeepTransferDate')}</span>
          </label>
          <button type="submit" disabled>{t('adminPrepareTransfer')}</button>
        </form>
      </section>

      <p className="admin-api-note">
        {t('adminDjangoHint')} <strong>{t('adminNotConnected')}</strong>
        <a href="/admin/">{t('adminOpenDjango')}</a>
      </p>
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

function TabletQrScanner({
  boxes,
  onSelectBox,
  t,
}: {
  boxes: BoxItem[];
  onSelectBox: (id: number) => void;
  t: TFunction;
}) {
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const intervalRef = useRef<number | null>(null);
  const [isScanning, setIsScanning] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => {
    if (!isScanning) {
      stopQrScanner(streamRef, intervalRef);
      return;
    }

    let isCancelled = false;

    async function startScanner() {
      const Detector = (window as unknown as {
        BarcodeDetector?: new (options?: { formats?: string[] }) => {
          detect: (source: HTMLVideoElement) => Promise<Array<{ rawValue: string }>>;
        };
      }).BarcodeDetector;

      if (!Detector || !navigator.mediaDevices?.getUserMedia) {
        setMessage(t('qrScannerUnsupported'));
        setIsScanning(false);
        return;
      }

      try {
        const stream = await navigator.mediaDevices.getUserMedia({
          video: { facingMode: { ideal: 'environment' } },
          audio: false,
        });
        if (isCancelled) {
          stream.getTracks().forEach((track) => track.stop());
          return;
        }

        streamRef.current = stream;
        if (videoRef.current) {
          videoRef.current.srcObject = stream;
          await videoRef.current.play();
        }

        const detector = new Detector({ formats: ['qr_code'] });
        intervalRef.current = window.setInterval(async () => {
          const video = videoRef.current;
          if (!video || video.readyState < 2) return;

          try {
            const codes = await detector.detect(video);
            const scannedValue = codes[0]?.rawValue;
            const scannedBoxId = scannedValue ? getBoxIdFromQrValue(scannedValue, boxes) : null;

            if (scannedBoxId != null) {
              triggerHaptic([10, 34, 12]);
              setMessage(t('qrScannerFound'));
              setIsScanning(false);
              onSelectBox(scannedBoxId);
            }
          } catch {
            // Some browsers throw while the video frame is not ready yet.
          }
        }, 650);
      } catch {
        setMessage(t('qrScannerPermission'));
        setIsScanning(false);
      }
    }

    void startScanner();

    return () => {
      isCancelled = true;
      stopQrScanner(streamRef, intervalRef);
    };
  }, [isScanning, boxes, onSelectBox, t]);

  return (
    <section className={isScanning ? 'tablet-scanner-panel is-scanning' : 'tablet-scanner-panel'}>
      <button
        className="scanner-preview"
        type="button"
        aria-label={isScanning ? t('qrScannerStop') : t('qrScannerStart')}
        onClick={() => {
          setMessage(null);
          setIsScanning((current) => !current);
        }}
      >
        {isScanning ? (
          <>
            <video ref={videoRef} muted playsInline />
            <span className="scanner-live-label">{t('qrScannerStop')}</span>
          </>
        ) : (
          <span className="scanner-placeholder">
            <span className="scanner-frame" aria-hidden="true">
              <span className="scanner-corner is-top-left" />
              <span className="scanner-corner is-top-right" />
              <span className="scanner-corner is-bottom-left" />
              <span className="scanner-corner is-bottom-right" />
              <span className="scanner-dash is-left" />
              <span className="scanner-dash is-right" />
            </span>
          </span>
        )}
      </button>

      {message ? <p className="scanner-status" aria-live="polite">{message}</p> : null}
    </section>
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
  const [lastPressed, setLastPressed] = useState<number | null>(null);
  const resetTimerRef = useRef<number | null>(null);

  useEffect(() => () => {
    if (resetTimerRef.current != null) {
      window.clearTimeout(resetTimerRef.current);
    }
  }, []);

  function handleAdd(value: number) {
    triggerHaptic(8);
    onAdd(value);
    setLastPressed(value);
    if (resetTimerRef.current != null) {
      window.clearTimeout(resetTimerRef.current);
    }
    resetTimerRef.current = window.setTimeout(() => setLastPressed(null), 180);
  }

  return (
    <span className="quick-counts">
      {values.map((value) => (
        <button
          key={value}
          className={lastPressed === value ? 'is-pressed' : ''}
          type="button"
          onClick={() => handleAdd(value)}
        >
          +{value}
        </button>
      ))}
    </span>
  );
}

function MeasurementSaveButton({
  isDesktop,
  isSaving,
  isSuccess,
  onSave,
  t,
}: {
  isDesktop: boolean;
  isSaving: boolean;
  isSuccess: boolean;
  onSave: () => Promise<boolean>;
  t: TFunction;
}) {
  const holdDuration = 950;
  const frameRef = useRef<number | null>(null);
  const holdStartRef = useRef<number | null>(null);
  const [isHolding, setIsHolding] = useState(false);
  const [holdProgress, setHoldProgress] = useState(0);

  useEffect(() => () => {
    if (frameRef.current != null) {
      window.cancelAnimationFrame(frameRef.current);
    }
  }, []);

  function cancelHold() {
    if (frameRef.current != null) {
      window.cancelAnimationFrame(frameRef.current);
      frameRef.current = null;
    }
    holdStartRef.current = null;
    setIsHolding(false);
    setHoldProgress(0);
  }

  function completeHold() {
    frameRef.current = null;
    holdStartRef.current = null;
    setHoldProgress(1);
    setIsHolding(false);
    triggerHaptic(10);
    void onSave();
  }

  function updateHoldProgress(timestamp: number) {
    if (holdStartRef.current == null) return;

    const progress = Math.min((timestamp - holdStartRef.current) / holdDuration, 1);
    setHoldProgress(progress);

    if (progress >= 1) {
      completeHold();
      return;
    }

    frameRef.current = window.requestAnimationFrame(updateHoldProgress);
  }

  function startHold(event: ReactPointerEvent<HTMLButtonElement>) {
    if (isSaving) return;

    event.currentTarget.setPointerCapture?.(event.pointerId);
    holdStartRef.current = performance.now();
    setIsHolding(true);
    setHoldProgress(0);
    frameRef.current = window.requestAnimationFrame(updateHoldProgress);
  }

  if (isDesktop) {
    return (
      <button className={isSuccess ? 'measurement-save-button is-success' : 'measurement-save-button'} type="submit" disabled={isSaving}>
        <span>{isSaving ? t('saving') : isSuccess ? t('measurementSaved') : t('saveMeasurement')}</span>
      </button>
    );
  }

  const buttonClass = [
    'measurement-save-button',
    'is-hold-action',
    isHolding ? 'is-holding' : '',
    isSuccess ? 'is-success' : '',
  ].filter(Boolean).join(' ');

  return (
    <button
      className={buttonClass}
      type="button"
      disabled={isSaving}
      title={t('holdToSave')}
      aria-label={t('holdToSave')}
      style={{ '--hold-progress': `${holdProgress * 360}deg` } as CSSProperties}
      onPointerDown={startHold}
      onPointerUp={cancelHold}
      onPointerCancel={cancelHold}
      onPointerLeave={cancelHold}
      onKeyDown={(event) => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault();
          void onSave();
        }
      }}
    >
      <span className="hold-save-progress" aria-hidden="true" />
      <span>{isSaving ? t('saving') : isSuccess ? t('measurementSaved') : t('saveMeasurement')}</span>
    </button>
  );
}

function stopQrScanner(
  streamRef: { current: MediaStream | null },
  intervalRef: { current: number | null },
) {
  if (intervalRef.current != null) {
    window.clearInterval(intervalRef.current);
    intervalRef.current = null;
  }

  streamRef.current?.getTracks().forEach((track) => track.stop());
  streamRef.current = null;
}

function getBoxIdFromQrValue(value: string, boxes: BoxItem[]) {
  const trimmedValue = value.trim();
  const routeMatch = trimmedValue.match(/\/bac\/(\d+)\/?/) ?? trimmedValue.match(/\/boxes\/([^/?#]+)\/?/);

  if (routeMatch?.[1]) {
    const routeValue = decodeURIComponent(routeMatch[1]);
    const routeId = Number(routeValue);
    if (Number.isInteger(routeId)) return routeId;

    const routeBox = boxes.find((box) => box.global_code.toLowerCase() === routeValue.toLowerCase());
    if (routeBox) return routeBox.id;
  }

  const normalizedValue = trimmedValue.toLowerCase();
  const directBox = boxes.find((box) => (
    box.global_code.toLowerCase() === normalizedValue ||
    box.local_code.toLowerCase() === normalizedValue
  ));

  return directBox?.id ?? null;
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

function triggerHaptic(pattern: number | number[]) {
  if (typeof navigator === 'undefined' || typeof navigator.vibrate !== 'function') return;

  try {
    navigator.vibrate(pattern);
  } catch {
    // Haptic feedback is optional and unsupported by several browsers.
  }
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

function parseTemperatureNumber(value: string | number | null | undefined) {
  if (value === null || value === undefined || value === '') return null;
  const numericValue = typeof value === 'number' ? value : Number.parseFloat(value);
  return Number.isFinite(numericValue) ? numericValue : null;
}

function getTemperatureMarkerPosition(value: number, target: number) {
  const relativePosition = 50 + ((value - target) / 3) * 50;
  return Math.min(100, Math.max(0, relativePosition));
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
    const media = window.matchMedia('(min-width: 1024px) and (hover: hover) and (pointer: fine)');

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
  return window.matchMedia('(min-width: 1024px) and (hover: hover) and (pointer: fine)').matches;
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
