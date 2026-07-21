import {
  type FormEvent,
  type KeyboardEvent,
  type PointerEvent,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react';

import { ApiError, apiDelete, apiGet, apiPatch, apiPost } from './api/client';
import { getBoxStatusPresentation } from './boxStatus';
import AdminView from './components/AdminView';
import BoxInsights, { MeasurementHistoryModal, type BoxInsightTab } from './components/BoxInsights';
import { useConfirmAction, type ConfirmActionOptions } from './components/ConfirmActionModal';
import ExportsView from './components/ExportsView';
import LabelsView from './components/LabelsView';
import LoginPage from './components/LoginPage';
import LoginNotice from './components/LoginNotice';
import MeasurementSaveButton from './components/MeasurementSaveButton';
import MoveBoxModal from './components/MoveBoxModal';
import PageLoader from './components/PageLoader';
import ProfileView from './components/ProfileView';
import QuickCountButtons from './components/QuickCountButtons';
import QrLabelModal from './components/QrLabelModal';
import SearchField from './components/SearchField';
import SubcultureModal from './components/SubcultureModal';
import TabletQrScanner from './components/TabletQrScanner';
import { ZoneDetailPage, ZonesView } from './components/ZonesView';
import { useIsDesktopApp } from './hooks/useIsDesktopApp';
import type {
  BiologicalMeasurement,
  BoxAlert,
  BoxCreatePayload,
  BoxDetail,
  BoxItem,
  BoxLineage,
  BoxMovement,
  BoxMovePayload,
  Dashboard,
  ExportOptions,
  LineageGraph,
  Organization,
  OverviewBox,
  OverviewMeasurementPoint,
  OverviewResponse,
  OverviewTemperaturePoint,
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
import { formatDisplayDate, formatIsoWeekDateLabel } from './utils/dateFormat';
import { getErrorMessage } from './utils/errors';
import {
  decrementDecimalValue,
  formatDecimalValue,
  incrementDecimalValue,
  parsePositiveDecimal,
} from './utils/stepValue';
import { triggerHaptic } from './utils/haptics';
import { getBoxQrImageUrl, getBoxScanUrl, printQrLabels, type QrLabelItem } from './utils/qrLabels';

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
};

const translations = {
  fr: {
    account: 'Compte',
    loading: 'Chargement...',
    backToPilotage: 'Retour au suivi',
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
    adminEnvironment: 'Emplacements et sondes',
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
    adminAuditTitle: 'Historique global',
    adminAuditText: 'Dernières actions enregistrées dans les structures que vous administrez.',
    adminAuditEmpty: 'Aucune action enregistrée.',
    adminAuditDate: 'Date',
    adminAuditUser: 'Utilisateur',
    adminAuditAction: 'Action',
    adminAuditObject: 'Objet',
    adminAuditShow: 'Afficher',
    adminAuditHide: 'Masquer',
    adminAuditShowAll: 'Tout afficher',
    adminAuditShowLess: 'Réduire',
    confirmCancel: 'Annuler',
    confirmCreateBoxTitle: 'Créer cette boîte',
    confirmCreateBoxMessage: 'Vérifiez les informations avant d’ajouter la boîte au suivi.',
    confirmCreateBoxAction: 'Créer la boîte',
    confirmSubcultureTitle: 'Enregistrer ce repiquage',
    confirmSubcultureMessage: 'Les nouvelles boîtes seront reliées à la boîte parent.',
    confirmSubcultureAction: 'Enregistrer le repiquage',
    confirmMoveTitle: 'Transférer cette boîte',
    confirmMoveMessage: 'Le nouvel emplacement sera ajouté à l’historique de la boîte.',
    confirmMoveAction: 'Transférer',
    confirmArchiveBoxTitle: 'Désactiver le suivi',
    confirmArchiveBoxMessage: 'L’historique sera conservé et la boîte ne sera plus affichée comme vivante.',
    confirmArchiveBoxAction: 'Désactiver',
    confirmActivateBoxTitle: 'Réactiver le suivi',
    confirmActivateBoxMessage: 'La boîte sera de nouveau affichée dans les suivis actifs.',
    confirmActivateBoxAction: 'Réactiver',
    confirmDeleteOrganizationTitle: 'Supprimer cette structure',
    confirmDeleteOrganizationMessage: 'Vérifiez que cette structure ne doit plus être conservée dans Polypbase.',
    confirmDeleteOrganizationAction: 'Supprimer',
    confirmDetailBox: 'Boîte',
    confirmDetailSpecies: 'Espèce',
    confirmDetailStrain: 'Souche',
    confirmDetailLocation: 'Emplacement',
    confirmDetailCurrentLocation: 'Emplacement actuel',
    confirmDetailTargetLocation: 'Nouvel emplacement',
    confirmDetailParentBox: 'Boîte parent',
    confirmDetailChildren: 'Boîtes enfants',
    confirmDetailOrganization: 'Structure',
    adminFlowLabel: 'Parcours administrateur',
    boxArchiveAction: 'Désactiver le suivi',
    boxArchiveConfirm: 'Désactiver le suivi de cette boîte ? Son historique restera conservé.',
    boxArchived: 'Suivi de la boîte désactivé.',
    boxArchiveForbidden: 'Seul un administrateur peut désactiver le suivi de cette boîte.',
    boxActivateAction: 'Réactiver le suivi',
    boxActivateConfirm: 'Réactiver le suivi de cette boîte ?',
    boxActivated: 'Suivi de la boîte réactivé.',
    boxActivateForbidden: 'Seul un administrateur peut réactiver le suivi de cette boîte.',
    moveConfirm: 'Confirmer le transfert de cette boîte ?',
    subcultureConfirm: 'Confirmer la création de ce repiquage ?',
    createBoxTitle: 'Créer une boîte',
    createBoxText: 'Ajouter une nouvelle boîte sans passer par le repiquage.',
    createBoxOpen: 'Créer une boîte',
    createBoxClose: 'Fermer',
    createBoxOrganization: 'Structure',
    createBoxStrain: 'Souche',
    createBoxZone: 'Emplacement',
    createBoxNoZone: 'Sans emplacement',
    createBoxGlobalCode: 'Code boîte',
    createBoxNumber: 'Numéro',
    createBoxEnteredOn: 'Date d’entrée',
    createBoxNotes: 'Note',
    createBoxSubmit: 'Créer',
    createBoxSaved: 'Boîte créée.',
    createBoxNoOptions: 'Aucune souche disponible pour créer une boîte.',
    createBoxForbidden: 'Ce compte ne peut pas créer de boîte.',
    createBoxNumberMismatch: 'Le numéro doit correspondre au numéro présent dans le code boîte.',
    createBoxConfirm: 'Confirmer la création de cette boîte ?',
    adminCurrentSession: 'Session active',
    adminChangeRole: 'Modifier rôle',
    adminRemoveAccess: 'Supprimer accès',
    adminNotConnected: 'API à connecter',
    adminZonesProbesTitle: 'Emplacements thermiques et sondes',
    adminZonesProbesText: 'Créer un emplacement ou une étuve, puis y associer une ou plusieurs sondes.',
    adminZoneName: 'Nom de l’emplacement',
    adminZoneType: 'Type d’emplacement',
    adminZoneTypeCabinet: 'Emplacement',
    adminZoneTypeIncubator: 'Étuve',
    adminTargetTemperature: 'Température consigne',
    adminZoneCapacity: 'Capacité',
    adminZoneSalinity: 'Salinité (PSU)',
    adminZoneOrganization: 'Structure',
    adminCreateZone: 'Créer l’emplacement',
    adminZoneCreated: 'Emplacement créé.',
    adminZoneUpdated: 'Emplacement modifié.',
    adminSaveZone: 'Enregistrer',
    adminZoneNoOrganization: 'Aucune structure que vous administrez.',
    adminProbeLocation: 'Emplacement',
    adminProbeCreated: 'Sonde ajoutée.',
    adminProbeNoZone: 'Aucun emplacement que vous administrez.',
    manualTemperatureTitle: 'Température manuelle',
    manualTemperatureDate: 'Date',
    manualTemperatureValue: 'Température mesurée (°C)',
    manualTemperatureSave: 'Enregistrer la température',
    manualTemperatureSaved: 'Température enregistrée.',
    manualTemperatureForbidden: 'Ce compte ne peut pas saisir de température pour cet emplacement.',
    adminOrganizationCreated: 'Structure créée.',
    adminOrganizationDeleted: 'Structure supprimée.',
    adminOrganizationUpdated: 'Structure modifiée.',
    adminEditOrganization: 'Modifier',
    adminDeleteOrganization: 'Supprimer',
    adminCancelEdit: 'Annuler',
    adminSaveOrganization: 'Enregistrer',
    adminConfirmDeleteOrganization: 'Supprimer cette structure ?',
    adminInvalidCountry: 'Choisissez un pays dans la liste.',
    adminInvalidCityCountry: 'Cette ville ne correspond pas au pays choisi.',
    adminTransferNotes: 'Notes',
    adminTransferCreated: 'Transfert enregistré.',
    adminTransferNoBox: 'Aucune boîte que vous administrez.',
    adminProbeCode: 'Code sonde',
    adminProbeType: 'Type de sonde',
    adminProbeZone: 'Emplacement associé',
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
    adminTransferBoxSearchPlaceholder: 'Code, espèce ou souche',
    adminTransferBoxSearchEmpty: 'Aucune boîte trouvée avec cette recherche.',
    adminTransferTarget: 'Institution destinataire',
    adminTransferPolyps: 'Nombre de polypes transmis',
    adminKeepTransferDate: 'Conserver la date du transfert',
    adminPrepareTransfer: 'Préparer le transfert',
    adminTransferPackageTitle: 'Documents de transfert prêts',
    adminTransferDownloadData: 'Télécharger les données',
    adminTransferPrintLabel: 'Imprimer l’étiquette QR',
    adminDjangoHint: 'Les actions sensibles restent accessibles dans Django admin tant que les API dédiées ne sont pas créées.',
    adminPrintLabelsAction: 'Imprimer les étiquettes',
    adminPrintLabelsClear: 'Tout décocher',
    adminPrintLabelsHelp: 'Sélectionnez les boîtes à imprimer sur une même feuille.',
    adminPrintLabelsSearch: 'Rechercher une boîte',
    adminPrintLabelsSearchPlaceholder: 'Code, espèce ou souche',
    adminPrintLabelsSelectAll: 'Tout sélectionner',
    adminPrintLabelsTitle: 'Étiquettes des boîtes',
    profileRoles: 'Rôles',
    profileEmail: 'Email',
    profileNoEmail: 'Non renseigné',
    profileMemberships: 'Structures et rôles',
    profileNoMembership: 'Aucune structure rattachée à ce compte.',
    profileAllOrganizationsAccess: 'Accès à toutes les structures.',
    profilePreferences: 'Préférences',
    profileLanguage: 'Langue de l’interface',
    profileAdminTitle: 'Espace administrateur',
    profileAdminText: 'Gérer les comptes, les emplacements, les sondes et les échanges entre structures.',
    roleDescAdmin: 'Accès complet : laboratoire, exports et administration.',
    roleDescTechnician: 'Saisie et suivi du laboratoire, sans administration.',
    roleDescViewer: 'Consultation seule des données.',
    manageAccountsTitle: 'Gestion des comptes',
    manageAccountsSubtitle: 'Créer les accès, ajuster les rôles et couper rapidement un compte si nécessaire.',
    manageAddTitle: 'Nouvel accès',
    manageActiveAccounts: 'Comptes actifs',
    manageAdminAccounts: 'Admins',
    manageTechnicianAccounts: 'Techniciens',
    manageViewerAccounts: 'Lecteurs',
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
    manageTemporaryPasswordTitle: 'Mot de passe temporaire',
    manageTemporaryPasswordText: 'Polypbase génère un mot de passe aléatoire et l’envoie à l’adresse indiquée.',
    managePasswordHint: 'L’utilisateur pourra ensuite choisir un mot de passe personnel.',
    manageAddAction: 'Ajouter l’accès',
    manageAdding: 'Ajout...',
    manageMemberAdded: 'Accès enregistré. Le mot de passe temporaire est envoyé par email.',
    manageRoleUpdated: 'Rôle mis à jour.',
    manageDeactivate: 'Désactiver',
    manageReactivate: 'Réactiver',
    historyButton: 'Voir relevés',
    analysisTabLineage: 'Parenté',
    analysisTabMeasurements: 'Relevés',
    analysisTabMovements: 'Mouvements',
    boxLocalCode: 'Code local',
    boxStrain: 'Souche',
    boxAttentionTitle: 'Suivi prioritaire',
    boxChecksButton: 'Alertes',
    boxChecksEmptyText: 'Aucun signal particulier sur les derniers relevés.',
    boxChecksEmptyTitle: 'Aucune alerte active',
    boxChecksIntro: 'Signaux détectés à partir des derniers relevés.',
    boxChecksTitle: 'Alertes de la boîte',
    boxAlertBanner: 'Attention : une alerte a été détectée pour cette boîte.',
    boxAlertBannerAction: 'Voir le détail',
    chartEmpty: 'Pas assez de relevés pour tracer une tendance.',
    chartMissingReading: 'Pas de relevé',
    chartTitle: 'Évolution des relevés',
    createdOn: 'Créée le',
    firstMeasurementOn: 'Premier relevé',
    events: 'Événements',
    lastComment: 'Dernier commentaire',
    lastMeasurement: 'Dernier relevé',
    laboratoryTracking: 'Suivi laboratoire',
    lineageAction: 'Parenté',
    lineageEmptyGraph: 'Le graphique de parenté sera affiché ici.',
    lineageLoading: 'Chargement de la parenté...',
    lineageRetry: 'Recharger',
    loginAction: 'Se connecter',
    loginRequired: 'Connexion requise',
    logoutAction: 'Se d\u00e9connecter',
    logoutError: 'D\u00e9connexion impossible pour le moment.',
    measurementDate: 'Date du relevé',
    measurementCountsRequired: 'Polypes et éphyrules sont obligatoires.',
    measurementForbidden: 'Ce compte ne peut pas créer de relevé.',
    measurementHistory: 'Historique des relevés',
    measurementSaved: 'Relevé enregistré',
    measurementUpdated: 'Relevé modifié',
    editLastMeasurement: 'Modifier le dernier relevé',
    editLastMeasurementHelp: 'Reprendre le dernier relevé dans le formulaire pour ajustement.',
    cancelEdit: 'Annuler la modification',
    holdToUpdate: 'Maintenir pour modifier',
    measurementEditing: 'Dernier relevé chargé pour modification',
    saveMeasurementEdit: 'Enregistrer l’ajustement',
    moveAction: 'Transférer',
    moveForbidden: 'Ce compte ne peut pas transférer de boîte.',
    moveSaved: 'Transfert enregistré',
    movementHistoryTitle: 'Historique des emplacements',
    noMovementHistory: 'Aucun déplacement enregistré pour cette boîte.',
    movedTo: 'Transférée vers',
    movementEvent: 'Transfert',
    newMeasurement: 'Nouveau relevé',
    noComment: 'Aucun commentaire récent pour cette boîte.',
    noDate: 'aucune date',
    noMeasurementHistory: 'Aucun relevé pour cette boîte.',
    noRecentScans: 'Aucun scan récent pour l’instant.',
    noZone: 'Sans emplacement',
    observation: 'Observation',
    observationPlaceholder: 'Note rapide pour le laboratoire',
    overview: 'Vue d’ensemble',
    overviewTitle: 'Vue d’ensemble',
    overviewSubtitle: 'Boîtes suivies dans l’application, avec tendance sur les 6 derniers mois.',
    overviewActiveBoxes: 'boîtes vivantes',
    overviewBoxColumn: 'Boîte',
    overviewLatestReading: 'Dernier relevé',
    overviewLocationColumn: 'Emplacement',
    overviewNoMeasurement: 'Sans relevé',
    overviewOpenBox: 'Ouvrir la fiche',
    overviewTemperature: 'Température emplacement',
    overviewChartTitle: '6 derniers mois',
    overviewShowChart: 'Voir tendance',
    overviewHideChart: 'Masquer tendance',
    overviewNoHistory: 'Pas assez de données pour tracer la tendance.',
    overviewEmpty: 'Aucune boîte vivante à afficher.',
    overviewFilters: 'Filtres',
    overviewSort: 'Tri',
    overviewSortOldest: 'Plus ancien relevé',
    overviewSortNewest: 'Plus récent relevé',
    overviewFilterAllSpecies: 'Toutes les espèces',
    overviewFilterAllZones: 'Tous les emplacements',
    overviewSearch: 'Rechercher',
    overviewSearchPlaceholder: 'Code, espèce ou emplacement',
    overviewFilteredBoxes: 'boîtes affichées',
    overviewNoZoneMetric: 'sans emplacement',
    overviewWithEphyrae: 'avec éphyrules',
    overviewPriorityBoxes: 'à vérifier',
    overviewTrackedBoxes: 'suivies dans l’app',
    overviewRecordedBoxes: 'déjà relevées',
    overviewFilterHint: 'filtrer',
    overviewClearFilter: 'retirer',
    overviewByZone: 'Par emplacement',
    overviewZoneDone: 'déjà faits',
    overviewZoneRemaining: 'reste à relever',
    overviewZoneUpToDate: 'à jour',
    overviewShowMore: 'Afficher plus',
    overviewShowing: 'affichées',
    weeklyDueNow: 'à relever',
    weeklyDueSoon: 'bientôt',
    weeklyUpToDate: 'à jour',
    weeklyNoRecentReading: 'Aucun relevé récent',
    weeklyLastReading: 'Dernier relevé',
    weeklyNoActiveBoxes: 'Aucune boîte active suivie dans l’application.',
    weeklyDayShort: 'j',
    labels: 'Étiquettes',
    labelsTitle: 'Étiquettes',
    pilotage: 'Suivi',
    pilotageTitle: 'Suivi labo',
    polyps: 'Polypes',
    speciesLabel: 'Espèce',
    parents: 'Parents',
    children: 'Enfants',
    probes: 'Sondes',
    profile: 'Profil',
    profileTitle: 'Mon profil',
    print: 'Imprimer',
    prototype: 'prototype',
    qrCode: 'QR code',
    qrLabelAddToSelection: 'Ajouter à la sélection',
    qrLabelAlreadySelected: 'Déjà dans la sélection',
    qrLabelClearSelection: 'Vider',
    qrLabelDownload: 'Télécharger',
    qrLabelHelp: 'Étiquette prête à imprimer et coller sur la boîte.',
    qrLabelPrintSelection: 'Imprimer la sélection',
    qrLabelSelectionCount: 'étiquette(s) sélectionnée(s)',
    qrLabelSelectionEmpty: 'Aucune étiquette sélectionnée.',
    qrLabelSelectionHelp: 'Ajoutez des boîtes depuis leur fiche ou depuis la recherche ci-dessous, puis imprimez-les sur une même feuille.',
    qrLabelSelectionSearch: 'Ajouter une boîte',
    qrLabelSelectionTitle: 'Sélection d’étiquettes',
    qrLabelTitle: 'Étiquette QR code',
    qrLabelViewSelection: 'Voir la sélection',
    qrDownload: 'Télécharger',
    qrScanHint: 'Scannez pour ouvrir cette fiche',
    qrScannerFound: 'Boîte détectée',
    qrScannerPermission: 'Impossible d’ouvrir la caméra.',
    qrScannerSecureContext: 'Le scan caméra sur téléphone nécessite une adresse HTTPS.',
    qrScannerStart: 'Scanner',
    qrScannerStop: 'Arrêter',
    qrScannerText: 'Scannez le QR code d’une boîte pour ouvrir directement sa fiche.',
    qrScannerTitle: 'Scan QR code',
    qrScannerUnsupported: 'Scanner indisponible sur ce navigateur.',
    recentAccess: 'Derniers accès',
    holdToSave: 'Maintenir pour enregistrer',
    saveMeasurement: 'Enregistrer le relevé',
    saving: 'Enregistrement...',
    scanSearch: 'compte actuel',
    searchOrScan: 'Recherche ou scan',
    searchPlaceholder: 'Code boîte, espèce, souche',
    searchTab: 'Recherche',
    suggestions: 'Suggestions',
    subcultureAction: 'Repiquer',
    subcultureEvent: 'Repiquage',
    subcultureForbidden: 'Ce compte ne peut pas créer de repiquage.',
    subcultureSaved: 'Repiquage enregistré',
    polypDropAdviceText: 'polypes de moins que le relevé précédent. Vérifier la boîte avant la prochaine saisie.',
    polypDropAdviceAction: 'Contrôler la boîte au prochain passage.',
    polypDropAdviceTitle: 'Baisse de polypes',
    checkImportanceHigh: 'Important',
    checkImportanceInfo: 'Information',
    checkImportanceMedium: 'À surveiller',
    detectedSignal: 'Signal détecté',
    suggestedAction: 'Action proposée',
    temperatureShort: 'Temp.',
    targetTemperature: 'Consigne',
    salinityShort: 'Sal.',
    // Two salinities coexist on a box sheet: the zone reference and the one
    // actually measured for this box. They must never be confused.
    zoneSalinityShort: 'Sal. armoire',
    boxSalinityShort: 'Sal. boîte',
    salinityFull: 'Salinité du relevé (PSU)',
    temperature: 'Température',
    temperatureNoData: 'Aucune température disponible sur cette période.',
    oneMonth: '1 mois',
    threeMonths: '3 mois',
    sixMonths: '6 mois',
    oneYear: '1 an',
    allPeriod: 'Tout',
    aliveBoxes: 'Vivantes',
    backToZones: 'Retour aux emplacements',
    boxAttention: 'À surveiller',
    boxesHealthy: 'Sans alerte',
    zoneActivityTitle: 'Activité récente',
    zoneAttentionTitle: 'À vérifier',
    zoneFilterAll: 'Toutes',
    zoneFilterAttention: 'À vérifier',
    zoneFilterLiving: 'Vivantes',
    zoneOverviewAttentionDetails: 'Consultez les emplacements qui demandent une vérification.',
    zoneOverviewAttentionTitle: 'À vérifier',
    zoneOverviewHeading: 'Zones et étuves suivies',
    zoneOverviewNoProbe: 'Aucune sonde',
    zoneOverviewSortLocation: 'Rangement',
    zoneOverviewSortTemperature: 'Température',
    zoneOverviewSortTemperatureAsc: 'Plus froides',
    zoneOverviewSortTemperatureDesc: 'Plus chaudes',
    zoneOverviewThermalGap: 'Écart thermique',
    zoneOverviewMissingMeasurements: 'relevé(s) manquant(s)',
    zoneAddAction: 'Ajouter un emplacement',
    zoneAddTitle: 'Nouvel emplacement',
    zoneAddProbeAction: 'Ajouter une sonde',
    zoneAddProbeTitle: 'Nouvelle sonde',
    zoneEditCapacityAction: 'Modifier capacité',
    zoneEditTitle: 'Paramètres de l’emplacement',
    zoneTarget: 'Consigne',
    zoneCapacity: 'Capacité',
    zoneSalinity: 'Salinité',
    zoneSalinityMissing: 'Salinité manquante',
    zoneOccupancy: 'Capacité',
    zoneNoAttention: 'Aucune action à prévoir dans cet emplacement.',
    zoneNoRecentActivity: 'Aucun relevé récent dans cet emplacement.',
    zoneSummaryAlive: 'Vivantes',
    zoneSummaryAttention: 'À vérifier',
    deadBoxes: 'Mortes',
    emptyZone: 'Aucune boîte dans cet emplacement.',
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
    temperatureManualReading: 'Mesure ponctuelle',
    temperatureContinuousReading: 'Sonde continue',
    temperatureOk: 'Proche de la consigne',
    temperatureSamples: 'mesures',
    temperatureWatch: 'Écart à surveiller',
    zoneSheet: 'Fiche emplacement thermique',
    zoneBoxesTitle: 'Boîtes dans l’emplacement',
    zoneProbesTitle: 'Sondes associées',
    zones: 'Emplacements',
    zonesTitle: 'Emplacements thermiques',
  },
  en: {
    account: 'Account',
    loading: 'Loading...',
    backToPilotage: 'Back to tracking',
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
    adminAuditTitle: 'Global history',
    adminAuditText: 'Latest actions recorded in the organizations you administer.',
    adminAuditEmpty: 'No action recorded.',
    adminAuditDate: 'Date',
    adminAuditUser: 'User',
    adminAuditAction: 'Action',
    adminAuditObject: 'Object',
    adminAuditShow: 'Show',
    adminAuditHide: 'Hide',
    adminAuditShowAll: 'Show all',
    adminAuditShowLess: 'Collapse',
    confirmCancel: 'Cancel',
    confirmCreateBoxTitle: 'Create this box',
    confirmCreateBoxMessage: 'Check the information before adding the box to tracking.',
    confirmCreateBoxAction: 'Create box',
    confirmSubcultureTitle: 'Save this subculture',
    confirmSubcultureMessage: 'The new boxes will be linked to the parent box.',
    confirmSubcultureAction: 'Save subculture',
    confirmMoveTitle: 'Move this box',
    confirmMoveMessage: 'The new location will be added to the box history.',
    confirmMoveAction: 'Move',
    confirmArchiveBoxTitle: 'Disable tracking',
    confirmArchiveBoxMessage: 'The history will be kept and the box will no longer be shown as living.',
    confirmArchiveBoxAction: 'Disable',
    confirmActivateBoxTitle: 'Enable tracking',
    confirmActivateBoxMessage: 'The box will be shown again in active tracking.',
    confirmActivateBoxAction: 'Enable',
    confirmDeleteOrganizationTitle: 'Delete this organization',
    confirmDeleteOrganizationMessage: 'Check that this organization should no longer be kept in Polypbase.',
    confirmDeleteOrganizationAction: 'Delete',
    confirmDetailBox: 'Box',
    confirmDetailSpecies: 'Species',
    confirmDetailStrain: 'Strain',
    confirmDetailLocation: 'Location',
    confirmDetailCurrentLocation: 'Current location',
    confirmDetailTargetLocation: 'New location',
    confirmDetailParentBox: 'Parent box',
    confirmDetailChildren: 'Child boxes',
    confirmDetailOrganization: 'Organization',
    adminFlowLabel: 'Administration flow',
    boxArchiveAction: 'Disable tracking',
    boxArchiveConfirm: 'Disable tracking for this box? Its history will be kept.',
    boxArchived: 'Box tracking disabled.',
    boxArchiveForbidden: 'Only an administrator can disable tracking for this box.',
    boxActivateAction: 'Enable tracking',
    boxActivateConfirm: 'Enable tracking for this box?',
    boxActivated: 'Box tracking enabled.',
    boxActivateForbidden: 'Only an administrator can enable tracking for this box.',
    moveConfirm: 'Confirm moving this box?',
    subcultureConfirm: 'Confirm creating this subculture?',
    createBoxTitle: 'Create a box',
    createBoxText: 'Add a new box without going through subculture.',
    createBoxOpen: 'Create a box',
    createBoxClose: 'Close',
    createBoxOrganization: 'Organization',
    createBoxStrain: 'Strain',
    createBoxZone: 'Location',
    createBoxNoZone: 'No location',
    createBoxGlobalCode: 'Box code',
    createBoxNumber: 'Number',
    createBoxEnteredOn: 'Entry date',
    createBoxNotes: 'Note',
    createBoxSubmit: 'Create',
    createBoxSaved: 'Box created.',
    createBoxNoOptions: 'No strain available to create a box.',
    createBoxForbidden: 'This account cannot create boxes.',
    createBoxNumberMismatch: 'The number must match the number used in the box code.',
    createBoxConfirm: 'Confirm creating this box?',
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
    adminZoneCapacity: 'Capacity',
    adminZoneSalinity: 'Salinity (PSU)',
    adminZoneOrganization: 'Organization',
    adminCreateZone: 'Create zone',
    adminZoneCreated: 'Zone created.',
    adminZoneUpdated: 'Zone updated.',
    adminSaveZone: 'Save',
    adminZoneNoOrganization: 'No organization you administer.',
    adminProbeLocation: 'Location',
    adminProbeCreated: 'Probe added.',
    adminProbeNoZone: 'No zone you administer.',
    manualTemperatureTitle: 'Manual temperature',
    manualTemperatureDate: 'Date',
    manualTemperatureValue: 'Measured temperature (°C)',
    manualTemperatureSave: 'Save temperature',
    manualTemperatureSaved: 'Temperature saved.',
    manualTemperatureForbidden: 'This account cannot record temperature for this location.',
    adminOrganizationCreated: 'Organization created.',
    adminOrganizationDeleted: 'Organization deleted.',
    adminOrganizationUpdated: 'Organization updated.',
    adminEditOrganization: 'Edit',
    adminDeleteOrganization: 'Delete',
    adminCancelEdit: 'Cancel',
    adminSaveOrganization: 'Save',
    adminConfirmDeleteOrganization: 'Delete this organization?',
    adminInvalidCountry: 'Choose a country from the list.',
    adminInvalidCityCountry: 'This city does not match the selected country.',
    adminTransferNotes: 'Notes',
    adminTransferCreated: 'Transfer recorded.',
    adminTransferNoBox: 'No box you administer.',
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
    adminTransferBoxSearchPlaceholder: 'Code, species or strain',
    adminTransferBoxSearchEmpty: 'No box found for this search.',
    adminTransferTarget: 'Target institution',
    adminTransferPolyps: 'Transferred polyps',
    adminKeepTransferDate: 'Keep transfer date',
    adminPrepareTransfer: 'Prepare transfer',
    adminTransferPackageTitle: 'Transfer documents ready',
    adminTransferDownloadData: 'Download data',
    adminTransferPrintLabel: 'Print QR label',
    adminDjangoHint: 'Sensitive actions remain available in Django admin until dedicated APIs are created.',
    adminPrintLabelsAction: 'Print labels',
    adminPrintLabelsClear: 'Clear all',
    adminPrintLabelsHelp: 'Select the boxes to print on the same sheet.',
    adminPrintLabelsSearch: 'Search for a box',
    adminPrintLabelsSearchPlaceholder: 'Code, species or strain',
    adminPrintLabelsSelectAll: 'Select all',
    adminPrintLabelsTitle: 'Box labels',
    profileRoles: 'Roles',
    profileEmail: 'Email',
    profileNoEmail: 'Not provided',
    profileMemberships: 'Organizations and roles',
    profileNoMembership: 'No organization linked to this account.',
    profileAllOrganizationsAccess: 'Access to every organization.',
    profilePreferences: 'Preferences',
    profileLanguage: 'Interface language',
    profileAdminTitle: 'Administration area',
    profileAdminText: 'Manage accounts, locations, probes and exchanges between organizations.',
    roleDescAdmin: 'Full access: lab, exports and administration.',
    roleDescTechnician: 'Lab data entry and tracking, no administration.',
    roleDescViewer: 'Read-only access to the data.',
    manageAccountsTitle: 'Account management',
    manageAccountsSubtitle: 'Create access, adjust roles and quickly disable an account when needed.',
    manageAddTitle: 'New access',
    manageActiveAccounts: 'Active accounts',
    manageAdminAccounts: 'Admins',
    manageTechnicianAccounts: 'Technicians',
    manageViewerAccounts: 'Viewers',
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
    manageTemporaryPasswordTitle: 'Temporary password',
    manageTemporaryPasswordText: 'Polypbase generates a random password and sends it to the provided email address.',
    managePasswordHint: 'The user can then choose a personal password.',
    manageAddAction: 'Add access',
    manageAdding: 'Adding...',
    manageMemberAdded: 'Access saved. The temporary password is sent by email.',
    manageRoleUpdated: 'Role updated.',
    manageDeactivate: 'Disable',
    manageReactivate: 'Re-enable',
    historyButton: 'View records',
    analysisTabLineage: 'Lineage',
    analysisTabMeasurements: 'Measurements',
    analysisTabMovements: 'Moves',
    boxLocalCode: 'Local code',
    boxStrain: 'Strain',
    boxAttentionTitle: 'Priority follow-up',
    boxChecksButton: 'Alerts',
    boxChecksEmptyText: 'No specific signal in the latest measurements.',
    boxChecksEmptyTitle: 'No active alert',
    boxChecksIntro: 'Signals detected from the latest measurements.',
    boxChecksTitle: 'Box alerts',
    boxAlertBanner: 'Warning: an alert has been detected for this box.',
    boxAlertBannerAction: 'View details',
    chartEmpty: 'Not enough measurements to draw a trend.',
    chartMissingReading: 'No reading',
    chartTitle: 'Measurement trend',
    createdOn: 'Created on',
    firstMeasurementOn: 'First reading',
    events: 'Events',
    lastComment: 'Last comment',
    lastMeasurement: 'Last measurement',
    laboratoryTracking: 'Lab tracking',
    lineageAction: 'Lineage',
    lineageEmptyGraph: 'The lineage graph will be shown here.',
    lineageLoading: 'Loading lineage...',
    lineageRetry: 'Reload',
    loginAction: 'Sign in',
    loginRequired: 'Sign-in required',
    logoutAction: 'Sign out',
    logoutError: 'Unable to sign out at the moment.',
    measurementDate: 'Measurement date',
    measurementCountsRequired: 'Polyps and ephyrae are required.',
    measurementForbidden: 'This account cannot create measurements.',
    measurementHistory: 'Measurement history',
    measurementSaved: 'Measurement saved',
    measurementUpdated: 'Measurement updated',
    editLastMeasurement: 'Edit latest reading',
    editLastMeasurementHelp: 'Load the latest reading into the form for adjustment.',
    cancelEdit: 'Cancel edit',
    holdToUpdate: 'Hold to update',
    measurementEditing: 'Latest reading loaded for editing',
    saveMeasurementEdit: 'Save adjustment',
    moveAction: 'Move',
    moveForbidden: 'This account cannot move boxes.',
    moveSaved: 'Movement saved',
    movementHistoryTitle: 'Location history',
    noMovementHistory: 'No movement recorded for this box.',
    movedTo: 'Moved to',
    movementEvent: 'Move',
    newMeasurement: 'New measurement',
    noComment: 'No recent comment for this box.',
    noDate: 'no date',
    noMeasurementHistory: 'No measurement for this box.',
    noRecentScans: 'No recent scan yet.',
    noZone: 'No zone',
    observation: 'Observation',
    observationPlaceholder: 'Quick lab note',
    overview: 'Overview',
    overviewTitle: 'Overview',
    overviewSubtitle: 'Boxes tracked in the application, with a 6-month trend.',
    overviewActiveBoxes: 'living boxes',
    overviewBoxColumn: 'Box',
    overviewLatestReading: 'Latest reading',
    overviewLocationColumn: 'Location',
    overviewNoMeasurement: 'No reading',
    overviewOpenBox: 'Open sheet',
    overviewTemperature: 'Location temperature',
    overviewChartTitle: 'Last 6 months',
    overviewShowChart: 'Show trend',
    overviewHideChart: 'Hide trend',
    overviewNoHistory: 'Not enough data to draw the trend.',
    overviewEmpty: 'No living box to display.',
    overviewFilters: 'Filters',
    overviewSort: 'Sort',
    overviewSortOldest: 'Oldest reading',
    overviewSortNewest: 'Latest reading',
    overviewFilterAllSpecies: 'All species',
    overviewFilterAllZones: 'All locations',
    overviewSearch: 'Search',
    overviewSearchPlaceholder: 'Code, species or location',
    overviewFilteredBoxes: 'shown boxes',
    overviewNoZoneMetric: 'without location',
    overviewWithEphyrae: 'with ephyrae',
    overviewPriorityBoxes: 'to check',
    overviewTrackedBoxes: 'tracked in app',
    overviewRecordedBoxes: 'recorded',
    overviewFilterHint: 'filter',
    overviewClearFilter: 'clear',
    overviewByZone: 'By location',
    overviewZoneDone: 'done',
    overviewZoneRemaining: 'left to record',
    overviewZoneUpToDate: 'up to date',
    overviewShowMore: 'Show more',
    overviewShowing: 'shown',
    weeklyDueNow: 'to record',
    weeklyDueSoon: 'soon',
    weeklyUpToDate: 'up to date',
    weeklyNoRecentReading: 'No recent reading',
    weeklyLastReading: 'Latest reading',
    weeklyNoActiveBoxes: 'No active box tracked in the application.',
    weeklyDayShort: 'd',
    labels: 'Labels',
    labelsTitle: 'Labels',
    pilotage: 'Tracking',
    pilotageTitle: 'Lab tracking',
    polyps: 'Polyps',
    speciesLabel: 'Species',
    parents: 'Parents',
    children: 'Children',
    probes: 'Probes',
    profile: 'Profile',
    profileTitle: 'My profile',
    print: 'Print',
    prototype: 'prototype',
    qrCode: 'QR code',
    qrLabelAddToSelection: 'Add to selection',
    qrLabelAlreadySelected: 'Already selected',
    qrLabelClearSelection: 'Clear',
    qrLabelDownload: 'Download',
    qrLabelHelp: 'Label ready to print and attach to the box.',
    qrLabelPrintSelection: 'Print selection',
    qrLabelSelectionCount: 'selected label(s)',
    qrLabelSelectionEmpty: 'No label selected.',
    qrLabelSelectionHelp: 'Add boxes from their sheet or from the search below, then print them on the same sheet.',
    qrLabelSelectionSearch: 'Add a box',
    qrLabelSelectionTitle: 'Label selection',
    qrLabelTitle: 'QR code label',
    qrLabelViewSelection: 'View selection',
    qrDownload: 'Download',
    qrScanHint: 'Scan to open this sheet',
    qrScannerFound: 'Box detected',
    qrScannerPermission: 'Unable to open the camera.',
    qrScannerSecureContext: 'Camera scanning on a phone requires an HTTPS address.',
    qrScannerStart: 'Scan',
    qrScannerStop: 'Stop',
    qrScannerText: 'Scan a box QR code to open its sheet directly.',
    qrScannerTitle: 'QR code scan',
    qrScannerUnsupported: 'Scanner unavailable in this browser.',
    recentAccess: 'Recent access',
    holdToSave: 'Hold to save',
    saveMeasurement: 'Save measurement',
    saving: 'Saving...',
    scanSearch: 'current account',
    searchOrScan: 'Search or scan',
    searchPlaceholder: 'Box code, species, strain',
    searchTab: 'Search',
    suggestions: 'Suggestions',
    subcultureAction: 'Subculture',
    subcultureEvent: 'Subculture',
    subcultureForbidden: 'This account cannot create subculture events.',
    subcultureSaved: 'Subculture created',
    polypDropAdviceText: 'fewer polyps than the previous measurement. Check the box before the next entry.',
    polypDropAdviceAction: 'Check this box during the next lab round.',
    polypDropAdviceTitle: 'Polyp decrease',
    checkImportanceHigh: 'Important',
    checkImportanceInfo: 'Information',
    checkImportanceMedium: 'Monitor',
    detectedSignal: 'Detected signal',
    suggestedAction: 'Suggested action',
    temperatureShort: 'Temp.',
    targetTemperature: 'Target',
    salinityShort: 'Sal.',
    zoneSalinityShort: 'Cabinet sal.',
    boxSalinityShort: 'Box sal.',
    salinityFull: 'Measurement salinity (PSU)',
    temperature: 'Temperature',
    temperatureNoData: 'No temperature data for this period.',
    oneMonth: '1 month',
    threeMonths: '3 months',
    sixMonths: '6 months',
    oneYear: '1 year',
    allPeriod: 'All',
    aliveBoxes: 'Alive',
    backToZones: 'Back to zones',
    boxAttention: 'Needs attention',
    boxesHealthy: 'No alert',
    zoneActivityTitle: 'Recent activity',
    zoneAttentionTitle: 'Needs review',
    zoneFilterAll: 'All',
    zoneFilterAttention: 'Needs review',
    zoneFilterLiving: 'Living',
    zoneOverviewAttentionDetails: 'Review the zones that need attention.',
    zoneOverviewAttentionTitle: 'Needs review',
    zoneOverviewHeading: 'Tracked zones and incubators',
    zoneOverviewNoProbe: 'No probe',
    zoneOverviewSortLocation: 'Location',
    zoneOverviewSortTemperature: 'Temperature',
    zoneOverviewSortTemperatureAsc: 'Coldest first',
    zoneOverviewSortTemperatureDesc: 'Warmest first',
    zoneOverviewThermalGap: 'Thermal gap',
    zoneOverviewMissingMeasurements: 'missing measurement(s)',
    zoneAddAction: 'Add location',
    zoneAddTitle: 'New location',
    zoneAddProbeAction: 'Add probe',
    zoneAddProbeTitle: 'New probe',
    zoneEditCapacityAction: 'Edit capacity',
    zoneEditTitle: 'Location settings',
    zoneTarget: 'Target',
    zoneCapacity: 'Capacity',
    zoneSalinity: 'Salinity',
    zoneSalinityMissing: 'Salinity not set',
    zoneOccupancy: 'Occupancy',
    zoneNoAttention: 'No action is needed for this zone.',
    zoneNoRecentActivity: 'No recent measurement in this zone.',
    zoneSummaryAlive: 'Living',
    zoneSummaryAttention: 'Needs review',
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
    temperatureManualReading: 'One-time reading',
    temperatureContinuousReading: 'Live probe',
    temperatureOk: 'Close to target',
    temperatureSamples: 'readings',
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
type ConfirmAction = (options: ConfirmActionOptions) => Promise<boolean>;

const labTabs: TabId[] = ['pilotage', 'overview', 'zones', 'labels', 'profile'];
const desktopTabs: TabId[] = ['pilotage', 'overview', 'zones', 'exports', 'labels', 'profile'];

export default function App() {
  const [route, setRoute] = useState<RouteState>(() => getCurrentRoute());
  const [isLoginRoute, setIsLoginRoute] = useState(() => window.location.pathname === '/login');
  const [search, setSearch] = useState('');
  const [recentBoxIds, setRecentBoxIds] = useState<number[]>([]);
  const [qrLabelSelection, setQrLabelSelection] = useState<QrLabelItem[]>([]);
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
  const [error, setError] = useState<string | null>(null);

  const activeTab = route.tab;
  const isBoxRoute = route.boxCode != null || route.boxId != null;
  const isZoneRoute = activeTab === 'zones' && route.zoneId != null;
  const language = getLanguage(data.profile);
  const t: TFunction = (key) => translations[language][key];
  const { confirmAction, confirmActionModal } = useConfirmAction();
  const isDesktopApp = useIsDesktopApp();
  const hasAdminRole = userHasAdminRole(data.profile);
  const canUseAdmin = isDesktopApp && hasAdminRole;
  const isProfileAdminLoading = activeTab === 'profile' && canUseAdmin && data.exportOptions === null;
  const isPilotageOptionsLoading = activeTab === 'pilotage' && data.exportOptions === null;
  const isExportOptionsLoading = (
    activeTab === 'exports' ||
    isProfileAdminLoading ||
    isPilotageOptionsLoading
  ) && data.exportOptions === null;
  const isOverviewLoading = activeTab === 'overview' && data.overview === null;
  const workspacePageKey = `${activeTab}-${route.boxCode ?? route.boxId ?? 'list'}-${route.zoneId ?? 'list'}`;
  const brandOrganizationName = getBrandOrganizationName(data.profile, t);
  const availableTabs = useMemo(() => {
    if (!isDesktopApp) return labTabs;
    return desktopTabs;
  }, [isDesktopApp]);

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

        const [boxes, zones, dashboard, profile] = await Promise.all([
          apiGet<PaginatedResponse<BoxItem>>(`/api/boxes/?limit=${BOX_LIST_LIMIT}`),
          apiGet<PaginatedResponse<ThermalZone>>('/api/thermal-zones/?limit=80'),
          apiGet<Dashboard>('/api/dashboard/'),
          apiGet<UserProfile>('/api/profile/'),
        ]);

        if (!isActive) return;

        setData({
          boxes: boxes.results,
          boxDetails: {},
          zones: zones.results,
          dashboard,
          overview: null,
          exportOptions: null,
          profile,
        });
        setRecentBoxIds(buildRecentBoxIds(boxes.results, dashboard));
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
    if (isLoginRoute || activeTab !== 'overview' || data.overview !== null) return;

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
  }, [activeTab, data.overview, isLoginRoute]);

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

  function openTab(tab: TabId) {
    const paths: Record<TabId, string> = {
      pilotage: '/',
      overview: '/overview',
      zones: '/zones',
      exports: '/exports',
      labels: '/labels',
      admin: '/administration',
      profile: '/profile',
    };
    navigateTo({ tab, boxCode: null, boxId: null }, paths[tab]);
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

  function printQrLabelSelection() {
    printQrLabels(qrLabelSelection);
  }

  function openQrLabelSelection() {
    openTab('labels');
  }

  useEffect(() => {
    if (availableTabs.includes(activeTab)) return;

    navigateTo({ tab: 'pilotage', boxCode: null, boxId: null }, '/');
  }, [activeTab, availableTabs]);

  useEffect(() => {
    const shouldLoadExportOptions =
      activeTab === 'pilotage' ||
      activeTab === 'exports' ||
      (activeTab === 'profile' && canUseAdmin);
    if (isLoginRoute || data.exportOptions || !shouldLoadExportOptions) return;

    let isActive = true;

    async function loadExportOptions() {
      try {
        const exportOptions = await apiGet<ExportOptions>('/api/exports/options/');
        if (!isActive) return;
        setData((current) => ({ ...current, exportOptions }));
      } catch (requestError) {
        if (isActive) setError(getErrorMessage(requestError));
      }
    }

    void loadExportOptions();

    return () => {
      isActive = false;
    };
  }, [activeTab, canUseAdmin, data.exportOptions, isLoginRoute]);

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
    const detail = await apiPost<BoxDetail>(`/api/boxes/${boxId}/move/`, payload);
    const zones = await apiGet<PaginatedResponse<ThermalZone>>('/api/thermal-zones/?limit=80');

    setData((current) => ({
      ...mergeBoxDetail(current, detail),
      zones: zones.results,
      overview: null,
      exportOptions: null,
    }));
  }

  async function archiveBox(boxId: number) {
    const detail = await apiPost<BoxDetail>(`/api/boxes/${boxId}/archive/`, {});

    setData((current) => ({
      ...mergeBoxDetail(current, detail),
      boxes: upsertBoxes(current.boxes, [detail]),
      overview: null,
      exportOptions: null,
    }));
  }

  async function activateBox(boxId: number) {
    const detail = await apiPost<BoxDetail>(`/api/boxes/${boxId}/activate/`, {});

    setData((current) => ({
      ...mergeBoxDetail(current, detail),
      boxes: upsertBoxes(current.boxes, [detail]),
      overview: null,
      exportOptions: null,
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

  if (isLoginRoute) {
    return <LoginPage onAuthenticated={handleAuthenticated} />;
  }

  return (
    <main className="app-shell">
      <aside className="sidebar">
        <div className="brand-block">
          <span className="brand-mark" aria-hidden="true">
            <img src="/jellyfish.svg" alt="" />
          </span>
          <div>
            <p className="eyebrow">Polypbase</p>
            <strong>{brandOrganizationName}</strong>
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
                onArchiveBox={archiveBox}
                onActivateBox={activateBox}
                onLoadLineageGraph={loadLineageGraph}
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
                onSelectBox={openBox}
                onOpenZone={openZone}
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
                  canRecordManualTemperature={userCanWriteLabData(
                    data.profile,
                    data.zones.find((zone) => zone.id === route.zoneId)?.organization.id ?? -1,
                  )}
                  onBack={closeZonePage}
                  onRecordManualTemperature={recordManualTemperature}
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
                isLoading={isLoading || isExportOptionsLoading}
                options={data.exportOptions}
                language={language}
              />
            )}

            {activeTab === 'admin' && (
              <AdminView
                boxes={data.boxes}
                exportOptions={data.exportOptions}
                isLoading={isLoading || isExportOptionsLoading}
                profile={data.profile}
                onCreateZone={createThermalZone}
                onUpdateZone={updateThermalZone}
                onCreateProbe={createProbe}
                onCreateOrganization={createOrganization}
                onUpdateOrganization={updateOrganization}
                onDeleteOrganization={deleteOrganization}
                onCreateTransfer={createBoxTransfer}
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
                onPrintQrLabelSelection={printQrLabelSelection}
                onRemoveQrLabel={removeQrLabelFromSelection}
              />
            )}

            {activeTab === 'profile' && (
              <ProfileView
                isLoading={isLoading}
                labels={getProfileLabels(t)}
                profile={data.profile}
                adminSection={canUseAdmin ? (
                  <AdminView
                    boxes={data.boxes}
                    exportOptions={data.exportOptions}
                    isLoading={isLoading || isProfileAdminLoading}
                    profile={data.profile}
                    onCreateZone={createThermalZone}
                    onUpdateZone={updateThermalZone}
                    onCreateProbe={createProbe}
                    onCreateOrganization={createOrganization}
                    onUpdateOrganization={updateOrganization}
                    onDeleteOrganization={deleteOrganization}
                    onCreateTransfer={createBoxTransfer}
                    t={t}
                    zones={data.zones}
                  />
                ) : null}
                onLogout={logoutCurrentUser}
                onUpdateLanguage={updateLanguage}
              />
            )}
          </div>
        )}
        {confirmActionModal}
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
  confirmAction: ConfirmAction;
  t: TFunction;
  onSearch: (value: string) => void;
  onSelectBox: (id: number) => void;
}) {
  const visibleSuggestions = search.trim() ? suggestions : [];
  const [tabletLookupMode, setTabletLookupMode] = useState<'qr' | 'search'>('qr');
  const canCreateBox = userCanCreateBoxes(profile);

  function selectFirstSuggestion() {
    if (visibleSuggestions[0]) {
      onSelectBox(visibleSuggestions[0].id);
      onSearch(visibleSuggestions[0].global_code);
    }
  }

  if (isLoading) {
    return <PageLoader variant="pilotage" label={t('pilotageTitle')} />;
  }

  return (
    <section className="pilotage-flow">
      <div className="lookup-panel">
        <div className="desktop-search-panel">
          <SearchField
            labels={{
              label: t('searchOrScan'),
              placeholder: t('searchPlaceholder'),
            }}
            value={search}
            onChange={onSearch}
            onSubmit={selectFirstSuggestion}
          />
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
              <SearchField
                labels={{
                  label: t('searchOrScan'),
                  placeholder: t('searchPlaceholder'),
                }}
                value={search}
                onChange={onSearch}
                onSubmit={selectFirstSuggestion}
              />
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

        <RecentAccessList boxes={recentBoxes} onSelectBox={onSelectBox} t={t} />

        {canCreateBox ? (
          <CreateBoxPanel
            boxes={boxes}
            exportOptions={exportOptions}
            isOptionsLoading={isOptionsLoading}
            profile={profile}
            t={t}
            onCreateBox={onCreateBox}
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
  onSearch: (value: string) => void;
  onSelectBox: (id: number) => void;
  t: TFunction;
}) {
  const [isOpen, setIsOpen] = useState(false);
  const writableOrganizations = useMemo(() => getWritableOrganizations(profile), [profile]);
  const initialOrganizationId = writableOrganizations[0]?.id ?? null;
  const [organizationId, setOrganizationId] = useState<number | null>(initialOrganizationId);
  const [strainId, setStrainId] = useState<number | null>(null);
  const [zoneId, setZoneId] = useState<number | null>(null);
  const [globalCode, setGlobalCode] = useState('');
  const [boxNumber, setBoxNumber] = useState('');
  const [enteredOn, setEnteredOn] = useState(() => new Date().toISOString().slice(0, 10));
  const [notes, setNotes] = useState('');
  const [isSaving, setIsSaving] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const strains = exportOptions?.strains ?? [];
  const selectedStrain = strains.find((strain) => strain.id === strainId) ?? null;
  const availableZones = (exportOptions?.zones ?? []).filter((zone) => zone.organization_id === organizationId);
  const selectedOrganization = writableOrganizations.find((organization) => organization.id === organizationId) ?? null;
  const selectedZone = availableZones.find((zone) => zone.id === zoneId) ?? null;
  const canSubmit = organizationId != null && strainId != null && globalCode.trim() && boxNumber.trim();

  useEffect(() => {
    if (organizationId != null || initialOrganizationId == null) return;
    setOrganizationId(initialOrganizationId);
  }, [initialOrganizationId, organizationId]);

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
    if (isSaving || !canSubmit || organizationId == null || strainId == null) return;

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
        { label: t('confirmDetailOrganization'), value: selectedOrganization?.name },
        { label: t('confirmDetailLocation'), value: selectedZone?.name ?? t('createBoxNoZone') },
      ],
    });
    if (!confirmed) return;

    setIsSaving(true);
    setMessage(null);
    setError(null);

    try {
      const created = await onCreateBox({
        organization: organizationId,
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
        onClick={() => setIsOpen((current) => !current)}
      >
        <span aria-hidden="true">{isOpen ? '×' : '+'}</span>
        <strong>{isOpen ? t('createBoxClose') : t('createBoxOpen')}</strong>
      </button>

      {isOpen ? (
        <form className="create-box-form" onSubmit={handleSubmit}>
          <div className="section-title">
            <h2>{t('createBoxTitle')}</h2>
          </div>

          {isOptionsLoading ? <p className="muted compact-text">{t('loading')}</p> : null}
          {!isOptionsLoading && !strains.length ? <p className="muted compact-text">{t('createBoxNoOptions')}</p> : null}

          <label>
            <span>{t('createBoxOrganization')}</span>
            <select
              value={organizationId ?? ''}
              onChange={(event) => {
                setOrganizationId(Number(event.target.value));
                setGlobalCode('');
                setBoxNumber('');
              }}
            >
              {writableOrganizations.map((organization) => (
                <option key={organization.id} value={organization.id}>
                  {organization.name}
                </option>
              ))}
            </select>
          </label>

          <label>
            <span>{t('createBoxStrain')}</span>
            <select
              value={strainId ?? ''}
              onChange={(event) => {
                setStrainId(Number(event.target.value));
                setGlobalCode('');
                setBoxNumber('');
              }}
            >
              {strains.map((strain) => (
                <option key={strain.id} value={strain.id}>
                  {strain.species_name} - {strain.code}
                </option>
              ))}
            </select>
          </label>

          <label>
            <span>{t('createBoxZone')}</span>
            <select value={zoneId ?? ''} onChange={(event) => setZoneId(event.target.value ? Number(event.target.value) : null)}>
              <option value="">{t('createBoxNoZone')}</option>
              {availableZones.map((zone) => (
                <option key={zone.id} value={zone.id}>
                  {zone.name}
                </option>
              ))}
            </select>
          </label>

          <label>
            <span>{t('createBoxGlobalCode')}</span>
            <input required value={globalCode} onChange={(event) => setGlobalCode(event.target.value)} />
          </label>

          <label>
            <span>{t('createBoxNumber')}</span>
            <input required value={boxNumber} onChange={(event) => setBoxNumber(event.target.value)} />
          </label>

          <label>
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

function OverviewView({
  boxes,
  isLoading,
  onSelectBox,
  onOpenZone,
  t,
}: {
  boxes: OverviewBox[] | null;
  isLoading: boolean;
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
      .filter((box) => box.tracked_in_app)
      .map((box) => {
        const latest = getLastItem(box.measurements);
        const latestTemperature = getLastItem(box.temperatures);
        const daysSince = latest ? getDaysSinceDate(latest.date) : null;
        const status = getWeeklyStatus(daysSince);
        const zoneName = box.thermal_zone?.name ?? noZoneLabel;

        return {
          box,
          latest,
          latestTemperature,
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

  useEffect(() => {
    setVisibleCount(12);
  }, [focusFilter, normalizedQuery, sortOrder, speciesFilter, zoneFilter]);

  if (isLoading) {
    return <PageLoader variant="pilotage" label={t('overviewTitle')} />;
  }

  return (
    <section className="overview-page">
      <header className="overview-intro overview-intro-priority">
        <div className="overview-summary-actions" aria-label={t('overviewFilters')}>
          <button
            type="button"
            aria-pressed={focusFilter === 'done'}
            className={focusFilter === 'done' ? 'is-active is-done' : 'is-done'}
            onClick={() => toggleFocusFilter('done')}
          >
            <span>{t('overviewRecordedBoxes')}</span>
            <strong>{doneCount}</strong>
            <small>{focusFilter === 'done' ? t('overviewClearFilter') : t('overviewFilterHint')}</small>
          </button>
          <button
            type="button"
            aria-pressed={focusFilter === 'due'}
            className={focusFilter === 'due' ? 'is-active is-due' : 'is-due'}
            onClick={() => toggleFocusFilter('due')}
          >
            <span>{t('weeklyDueNow')}</span>
            <strong>{dueCount}</strong>
            <small>{focusFilter === 'due' ? t('overviewClearFilter') : t('overviewFilterHint')}</small>
          </button>
          <button
            type="button"
            aria-pressed={focusFilter === 'soon'}
            className={focusFilter === 'soon' ? 'is-active is-soon' : 'is-soon'}
            onClick={() => toggleFocusFilter('soon')}
          >
            <span>{t('weeklyDueSoon')}</span>
            <strong>{soonCount}</strong>
            <small>{focusFilter === 'soon' ? t('overviewClearFilter') : t('overviewFilterHint')}</small>
          </button>
        </div>
      </header>

      {zoneSummaries.length ? (
        <section className="overview-zone-progress" aria-label={t('overviewByZone')}>
          <header>
            <h2>{t('overviewByZone')}</h2>
          </header>
          <div className="overview-zone-progress-list">
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
                  <span>
                    <strong>{summary.zoneName}</strong>
                    <small>
                      {summary.due
                        ? `${summary.due} ${t('overviewZoneRemaining')}`
                        : t('overviewZoneUpToDate')}
                    </small>
                  </span>
                  <em>{summary.done}/{summary.total}</em>
                  <small className="overview-zone-filter-hint">
                    {isZoneActive ? t('overviewClearFilter') : t('overviewFilterHint')}
                  </small>
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
        <label>
          <span>{t('overviewSearch')}</span>
          <input
            type="search"
            placeholder={t('overviewSearchPlaceholder')}
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
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
        <label>
          <span>{t('speciesLabel')}</span>
          <select value={speciesFilter} onChange={(event) => setSpeciesFilter(event.target.value)}>
            <option value="">{t('overviewFilterAllSpecies')}</option>
            {speciesOptions.map((speciesName) => (
              <option key={speciesName} value={speciesName}>{speciesName}</option>
            ))}
          </select>
        </label>
      </section>

      {trackedEntries.length > 0 && filteredEntries.length > 0 ? (
        <div className="overview-list">
          <div className="overview-result-count" aria-label={t('overviewShowing')}>
            <strong>{visibleEntries.length}/{filteredEntries.length}</strong>
          </div>
          <div className="overview-box-list">
            {visibleEntries.map((entry) => (
              <article
                className={`overview-box-summary overview-box-summary-priority is-${entry.status}`}
                key={entry.box.id}
              >
                <header>
                  <button type="button" onClick={() => onSelectBox(entry.box.id)}>
                    <strong>{entry.box.global_code}</strong>
                    <span>{entry.box.species_name}</span>
                  </button>
                  {entry.box.thermal_zone ? (
                    <button
                      type="button"
                      className="overview-zone-button"
                      onClick={() => onOpenZone(entry.box.thermal_zone!.id)}
                    >
                      {entry.zoneName}
                    </button>
                  ) : (
                    <small>{entry.zoneName}</small>
                  )}
                </header>

                <div className="overview-priority-row">
                  <span>
                    <small>{t('weeklyLastReading')}</small>
                    <strong>{entry.latest ? formatDisplayDate(entry.latest.date) : t('weeklyNoRecentReading')}</strong>
                  </span>
                  <em className={`overview-box-status is-${entry.status}`}>
                    {formatWeeklyBadgeAge(entry.daysSince, entry.latest?.date, t)}
                  </em>
                </div>

                <div className="overview-box-kpis">
                  <span>
                    <small>{t('polyps')}</small>
                    <strong>{entry.latest?.polyp_count ?? '-'}</strong>
                  </span>
                  <span>
                    <small>{t('ephyraeFull')}</small>
                    <strong>{entry.latest?.ephyrae_count ?? '-'}</strong>
                  </span>
                  <span>
                    <small>{t('temperatureShort')}</small>
                    <strong>{formatTemperature(entry.latestTemperature?.average_temperature_c)}</strong>
                  </span>
                </div>

                <OverviewMiniChart box={entry.box} t={t} />
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
  latestTemperature: OverviewTemperaturePoint | undefined;
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
    second.due - first.due
    || second.soon - first.soon
    || first.zoneName.localeCompare(second.zoneName)
  ));
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

function formatWeeklyBadgeAge(daysSince: number | null, date: string | undefined, t: TFunction) {
  if (daysSince === null || !date) return t('weeklyNoRecentReading');
  return `${daysSince} ${t('weeklyDayShort')}`;
}

function getDaysSinceDate(date: string) {
  const parsedDate = new Date(`${date}T00:00:00`);
  if (Number.isNaN(parsedDate.getTime())) return null;
  const today = new Date();
  const todayStart = new Date(today.getFullYear(), today.getMonth(), today.getDate());
  return Math.max(0, Math.floor((todayStart.getTime() - parsedDate.getTime()) / 86_400_000));
}

function OverviewMiniChart({ box, t }: { box: OverviewBox; t: TFunction }) {
  const [activeDate, setActiveDate] = useState<string | null>(null);
  const measurementDates = box.measurements.map((point) => point.date);
  const temperaturePoints = box.temperatures.filter((point) => Number.isFinite(point.average_temperature_c));
  const temperatureDates = temperaturePoints.map((point) => point.date);
  const dates = Array.from(new Set([...measurementDates, ...temperatureDates])).sort();
  const hasChart = dates.length >= 2 && (box.measurements.length >= 2 || temperaturePoints.length >= 2);

  if (!hasChart) {
    return (
      <div className="overview-chart overview-chart-empty">
        <strong>{t('overviewChartTitle')}</strong>
        <span>{t('overviewNoHistory')}</span>
      </div>
    );
  }

  const width = 420;
  const height = 168;
  const padding = { top: 16, right: 14, bottom: 28, left: 28 };
  const innerWidth = width - padding.left - padding.right;
  const innerHeight = height - padding.top - padding.bottom;
  const countMax = Math.max(
    1,
    ...box.measurements.map((point) => Math.max(point.polyp_count, point.ephyrae_count)),
  );
  const temperatureValues = temperaturePoints.map((point) => point.average_temperature_c);
  const minTemperature = temperatureValues.length ? Math.min(...temperatureValues) : 0;
  const maxTemperature = temperatureValues.length ? Math.max(...temperatureValues) : 1;
  const temperatureRange = Math.max(1, maxTemperature - minTemperature);
  const measurementByDate = new Map(box.measurements.map((point) => [point.date, point]));
  const temperatureByDate = new Map(temperaturePoints.map((point) => [point.date, point]));
  const xForDate = (date: string) => {
    const dateIndex = dates.indexOf(date);
    return padding.left + (dateIndex / Math.max(1, dates.length - 1)) * innerWidth;
  };
  const yForCount = (value: number) => padding.top + innerHeight - (value / countMax) * innerHeight;
  const yForTemperature = (value: number) => (
    padding.top + innerHeight - ((value - minTemperature) / temperatureRange) * innerHeight
  );
  const polypPath = buildOverviewPath(
    box.measurements.map((point) => ({ date: point.date, value: point.polyp_count })),
    xForDate,
    yForCount,
  );
  const ephyraePath = buildOverviewPath(
    box.measurements.map((point) => ({ date: point.date, value: point.ephyrae_count })),
    xForDate,
    yForCount,
  );
  const temperaturePath = buildOverviewPath(
    temperaturePoints.map((point) => ({ date: point.date, value: point.average_temperature_c })),
    xForDate,
    yForTemperature,
  );
  const activeDateValue = activeDate;
  const activeMeasurement = activeDateValue ? measurementByDate.get(activeDateValue) : undefined;
  const activeTemperature = activeDateValue ? temperatureByDate.get(activeDateValue) : undefined;
  const activeX = activeDateValue ? xForDate(activeDateValue) : null;
  const tooltipLeft = activeX === null ? 50 : Math.min(82, Math.max(18, (activeX / width) * 100));

  return (
    <div className="overview-chart">
      <header>
        <strong>{t('overviewChartTitle')}</strong>
        <div className="overview-chart-legend">
          <span className="is-polyps">{t('polyps')}</span>
          <span className="is-ephyrae">{t('ephyraeFull')}</span>
          <span className="is-temperature">{t('temperatureShort')}</span>
        </div>
      </header>
      <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label={t('overviewChartTitle')}>
        <line className="overview-chart-grid" x1={padding.left} x2={width - padding.right} y1={padding.top} y2={padding.top} />
        <line className="overview-chart-grid" x1={padding.left} x2={width - padding.right} y1={padding.top + innerHeight / 2} y2={padding.top + innerHeight / 2} />
        <line className="overview-chart-axis" x1={padding.left} x2={width - padding.right} y1={padding.top + innerHeight} y2={padding.top + innerHeight} />
        {polypPath ? <path className="overview-chart-line is-polyps" d={polypPath} /> : null}
        {ephyraePath ? <path className="overview-chart-line is-ephyrae" d={ephyraePath} /> : null}
        {temperaturePath ? <path className="overview-chart-line is-temperature" d={temperaturePath} /> : null}
        {activeDateValue && activeX !== null ? (
          <line
            className="overview-chart-hover-line"
            x1={activeX}
            x2={activeX}
            y1={padding.top}
            y2={padding.top + innerHeight}
          />
        ) : null}
        {box.measurements.flatMap((point) => [
          <circle
            className="overview-chart-dot is-polyps"
            key={`${point.date}-polyps`}
            cx={xForDate(point.date)}
            cy={yForCount(point.polyp_count)}
            r={activeDate === point.date ? 4.2 : 3}
          />,
          <circle
            className="overview-chart-dot is-ephyrae"
            key={`${point.date}-ephyrae`}
            cx={xForDate(point.date)}
            cy={yForCount(point.ephyrae_count)}
            r={activeDate === point.date ? 4.2 : 3}
          />,
        ])}
        {temperaturePoints.map((point) => (
          <circle
            className="overview-chart-dot is-temperature"
            key={`${point.date}-temperature`}
            cx={xForDate(point.date)}
            cy={yForTemperature(point.average_temperature_c)}
            r={activeDate === point.date ? 4.2 : 3}
          />
        ))}
        {dates.map((date) => {
          const x = xForDate(date);
          const hitWidth = Math.max(20, innerWidth / Math.max(1, dates.length - 1) * 0.75);

          return (
            <rect
              aria-label={formatIsoWeekDateLabel(date)}
              className="overview-chart-hit-area"
              key={`hit-${date}`}
              tabIndex={0}
              x={x - hitWidth / 2}
              y={padding.top}
              width={hitWidth}
              height={innerHeight}
              onClick={() => setActiveDate((currentDate) => (currentDate === date ? null : date))}
              onKeyDown={(event) => {
                if (event.key === 'Enter' || event.key === ' ') {
                  event.preventDefault();
                  setActiveDate((currentDate) => (currentDate === date ? null : date));
                }
              }}
            />
          );
        })}
        <text className="overview-chart-label" x={padding.left} y={height - 8}>{formatIsoWeekDateLabel(dates[0])}</text>
        <text className="overview-chart-label is-end" x={width - padding.right} y={height - 8}>{formatIsoWeekDateLabel(dates[dates.length - 1])}</text>
        <text className="overview-chart-y-label" x={padding.left - 6} y={padding.top + 4}>{countMax}</text>
        <text className="overview-chart-y-label" x={padding.left - 6} y={padding.top + innerHeight + 4}>0</text>
      </svg>
      {activeDateValue ? (
        <div className="overview-chart-detail" style={{ left: `${tooltipLeft}%` }}>
          <strong>{formatIsoWeekDateLabel(activeDateValue)}</strong>
          <span>{t('polyps')} : {activeMeasurement?.polyp_count ?? '-'}</span>
          <span>{t('ephyraeFull')} : {activeMeasurement?.ephyrae_count ?? '-'}</span>
          <span>{t('temperatureShort')} : {formatTemperature(activeTemperature?.average_temperature_c)}</span>
        </div>
      ) : null}
    </div>
  );
}

function buildOverviewPath(
  points: Array<{ date: string; value: number }>,
  xForDate: (date: string) => number,
  yForValue: (value: number) => number,
) {
  if (points.length < 2) return '';
  return points
    .map((point, index) => `${index === 0 ? 'M' : 'L'} ${xForDate(point.date).toFixed(1)} ${yForValue(point.value).toFixed(1)}`)
    .join(' ');
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
  profile,
  language,
  qrLabelSelection,
  isLoading,
  onCreateMeasurement,
  onUpdateMeasurement,
  onCreateSubculture,
  onMoveBox,
  onArchiveBox,
  onActivateBox,
  onLoadLineageGraph,
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
  onArchiveBox: (boxId: number) => Promise<void>;
  onActivateBox: (boxId: number) => Promise<void>;
  onLoadLineageGraph: (boxId: number) => Promise<LineageGraph>;
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
  const [moveError, setMoveError] = useState<string | null>(null);
  const [moveMessage, setMoveMessage] = useState<string | null>(null);
  const [statusError, setStatusError] = useState<string | null>(null);
  const [statusMessage, setStatusMessage] = useState<string | null>(null);
  const [isSubcultureOpen, setIsSubcultureOpen] = useState(false);
  const [isSavingSubculture, setIsSavingSubculture] = useState(false);
  const [isQrLabelOpen, setIsQrLabelOpen] = useState(false);
  const [isChecksOpen, setIsChecksOpen] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [saveMessage, setSaveMessage] = useState<string | null>(null);
  // Measurement saved during this visit (enables the "Modifier" button) and the
  // one currently being edited (null = creating a new measurement).
  const [lastSavedMeasurementId, setLastSavedMeasurementId] = useState<number | null>(null);
  const [editingMeasurementId, setEditingMeasurementId] = useState<number | null>(null);
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
  }, [box?.id]);

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
  const canShowStatusButton = canChangeBoxStatus && ['active', 'archived'].includes(box.status);

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

  async function handleChangeBoxStatus() {
    if (!box || isChangingBoxStatus) return;
    const isActivating = box.status === 'archived';
    const confirmed = await confirmAction({
      title: t(isActivating ? 'confirmActivateBoxTitle' : 'confirmArchiveBoxTitle'),
      message: t(isActivating ? 'confirmActivateBoxMessage' : 'confirmArchiveBoxMessage'),
      confirmLabel: t(isActivating ? 'confirmActivateBoxAction' : 'confirmArchiveBoxAction'),
      cancelLabel: t('confirmCancel'),
      variant: isActivating ? 'default' : 'danger',
      details: [
        { label: t('confirmDetailBox'), value: box.global_code },
        { label: t('confirmDetailSpecies'), value: box.species.scientific_name },
      ],
    });
    if (!confirmed) return;

    setIsChangingBoxStatus(true);
    setStatusError(null);
    setStatusMessage(null);

    try {
      if (isActivating) {
        await onActivateBox(box.id);
        setStatusMessage(t('boxActivated'));
      } else {
        await onArchiveBox(box.id);
        setStatusMessage(t('boxArchived'));
      }
    } catch (requestError) {
      if (requestError instanceof ApiError && requestError.status === 403) {
        setStatusError(t(isActivating ? 'boxActivateForbidden' : 'boxArchiveForbidden'));
      } else {
        setStatusError(getErrorMessage(requestError));
      }
    } finally {
      setIsChangingBoxStatus(false);
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

      <header className={`box-sheet-hero is-status-${statusPresentation.tone}`}>
        <div className="box-sheet-identity">
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

          {qr && canWriteLabData ? (
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

        <button
          className={checkCount > 0 ? 'box-alert-trigger' : 'box-alert-trigger is-empty'}
          type="button"
          aria-label={`${t('boxChecksButton')} (${checkCount})`}
          title={`${t('boxChecksButton')} (${checkCount})`}
          onClick={() => setIsChecksOpen(true)}
        >
          <BellIcon />
          <strong>{checkCount}</strong>
        </button>

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
          <InfoPill label={t('zoneSalinityShort')} value={formatSalinity(currentZone?.salinity_psu)} />
          {/* Salinity recorded for this box (the last measurement's PSU), shown
              right after the zone reference so both are read side by side. */}
          <InfoPill label={t('boxSalinityShort')} value={formatSalinity(box.latest_salinity_psu)} />
          <InfoPill label={t('temperatureShort')} value={formatTemperature(currentZone?.latest_temperature?.average_temperature_c)} />
        </div>

        <div className="box-action-stack">
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
              onClick={() => void handleChangeBoxStatus()}
            >
              {isChangingBoxStatus ? t('saving') : t(isBoxActive ? 'boxArchiveAction' : 'boxActivateAction')}
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

      {checkCount > 0 ? (
        <div className="box-alert-banner" role="alert">
          <span className="box-alert-banner-icon" aria-hidden="true"><BellIcon /></span>
          <strong>{t('boxAlertBanner')}</strong>
          <button type="button" onClick={() => setIsChecksOpen(true)}>
            {t('boxAlertBannerAction')} ({checkCount})
          </button>
        </div>
      ) : null}

      <div className="box-page-grid">
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

        {canWriteLabData ? (
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
                      -
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
                      +
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
                      -
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
                      +
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
                      -
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
                      +
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
                    save: editingMeasurementId != null ? t('saveMeasurementEdit') : t('saveMeasurement'),
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
                          {t('editLastMeasurement')}
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
            measurements={measurements}
            movements={getBoxMovements(box)}
            temperatureHistory={'temperature_history' in box ? box.temperature_history : []}
            onLoadLineageGraph={handleLoadLineageGraph}
            onOpenHistory={() => setIsHistoryOpen(true)}
            onSelectBox={onOpenBox}
            onSelectTab={setActiveInsightTab}
          />
        </section>

        {isHistoryOpen ? (
          <MeasurementHistoryModal
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
            t={t}
            onClose={() => setIsChecksOpen(false)}
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
  children: string;
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
  t,
  onClose,
}: {
  activeAlerts: BoxAlert[];
  polypDropCount: number;
  polypDropDetected: boolean;
  t: TFunction;
  onClose: () => void;
}) {
  const hasAlerts = activeAlerts.length > 0 || polypDropDetected;

  return (
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
          <button type="button" aria-label={t('close')} onClick={onClose}>×</button>
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
              </div>
            </article>
          ))}

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
    profileAdminTitle: t('profileAdminTitle'),
    profileAdminText: t('profileAdminText'),
    profileMemberships: t('profileMemberships'),
    profileNoEmail: t('profileNoEmail'),
    profileNoMembership: t('profileNoMembership'),
    profileAllOrganizationsAccess: t('profileAllOrganizationsAccess'),
    profilePreferences: t('profilePreferences'),
    roleDescAdmin: t('roleDescAdmin'),
    roleDescTechnician: t('roleDescTechnician'),
    roleDescViewer: t('roleDescViewer'),
    saving: t('saving'),
  };
}

function getLabelsViewLabels(t: TFunction) {
  return {
    noZone: t('noZone'),
    qrLabelAddToSelection: t('qrLabelAddToSelection'),
    qrLabelClearSelection: t('qrLabelClearSelection'),
    qrLabelPrintSelection: t('qrLabelPrintSelection'),
    qrLabelSelectionCount: t('qrLabelSelectionCount'),
    qrLabelSelectionEmpty: t('qrLabelSelectionEmpty'),
    qrLabelSelectionHelp: t('qrLabelSelectionHelp'),
    qrLabelSelectionSearch: t('qrLabelSelectionSearch'),
    qrLabelSelectionTitle: t('qrLabelSelectionTitle'),
    qrLabelSearchPlaceholder: t('adminPrintLabelsSearchPlaceholder'),
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
  if (error instanceof ApiError && error.status === 403) {
    return t('moveForbidden');
  }

  return getErrorMessage(error);
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
  return profile?.interface_language === 'en' ? 'en' : 'fr';
}

function getBrandOrganizationName(profile: UserProfile | null, t: TFunction) {
  if (!profile) return t('laboratoryTracking');

  const organizations = profile.memberships.length > 0
    ? profile.memberships.map((membership) => membership.organization)
    : profile.organizations;

  const uniqueOrganizations = organizations.filter(
    (organization, index) =>
      organizations.findIndex((candidate) => candidate.id === organization.id) === index,
  );

  if (uniqueOrganizations.length === 0) return t('laboratoryTracking');
  if (uniqueOrganizations.length === 1) return uniqueOrganizations[0].name;

  return `${uniqueOrganizations[0].name} +${uniqueOrganizations.length - 1}`;
}

function userHasAdminRole(profile: UserProfile | null) {
  if (!profile) return false;
  if (profile.is_superuser) return true;
  return profile.memberships.some((membership) => membership.role === 'admin');
}

function userCanCreateBoxes(profile: UserProfile | null) {
  if (!profile) return false;
  if (profile.is_superuser) return true;
  return profile.memberships.some((membership) => ['admin', 'lab_technician'].includes(membership.role));
}

function getWritableOrganizations(profile: UserProfile | null) {
  if (!profile) return [];
  if (profile.is_superuser) return profile.organizations;
  const writableIds = new Set(
    profile.memberships
      .filter((membership) => ['admin', 'lab_technician'].includes(membership.role))
      .map((membership) => membership.organization.id),
  );
  return profile.organizations.filter((organization) => writableIds.has(organization.id));
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
    return { tab: 'profile', boxCode: null, boxId: null };
  }

  if (path === '/profile') {
    return { tab: 'profile', boxCode: null, boxId: null };
  }

  return { tab: 'pilotage', boxCode: null, boxId: null };
}
