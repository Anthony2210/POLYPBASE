import {
  type FormEvent,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react';

import { ApiError, apiGet, apiPatch, apiPost } from './api/client';
import { getBoxStatusPresentation } from './boxStatus';
import AdminView from './components/AdminView';
import BoxInsights, { MeasurementHistoryModal, type BoxInsightTab } from './components/BoxInsights';
import ExportsView from './components/ExportsView';
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
  BoxDetail,
  BoxItem,
  BoxLineage,
  BoxMovement,
  BoxMovePayload,
  Dashboard,
  ExportOptions,
  LineageGraph,
  Organization,
  PaginatedResponse,
  Probe,
  SubculturePayload,
  SubcultureResult,
  ThermalZone,
  UserProfile,
} from './types';
import type {
  BoxTransferPayload,
  OrganizationPayload,
  ProbePayload,
  ThermalZonePayload,
} from './types/admin';
import { upsertBoxes } from './utils/boxCollection';
import { formatDisplayDate } from './utils/dateFormat';
import { getErrorMessage } from './utils/errors';
import { triggerHaptic } from './utils/haptics';
import { getBoxQrImageUrl, getBoxScanUrl, printQrLabels, type QrLabelItem } from './utils/qrLabels';

// Boxes are filtered client-side, so the whole collection must be loaded.
// Kept well above the current box count to leave room for growth.
const BOX_LIST_LIMIT = 1000;

type TabId = 'pilotage' | 'zones' | 'exports' | 'admin' | 'profile';

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
    adminEnvironment: 'Armoires et sondes',
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
    adminZonesProbesTitle: 'Armoires thermiques et sondes',
    adminZonesProbesText: 'Créer une armoire ou une étuve, puis y associer une ou plusieurs sondes.',
    adminZoneName: 'Nom de l’armoire',
    adminZoneType: 'Type d’armoire',
    adminZoneTypeCabinet: 'Armoire',
    adminZoneTypeIncubator: 'Étuve',
    adminTargetTemperature: 'Température consigne',
    adminZoneOrganization: 'Structure',
    adminCreateZone: 'Créer l’armoire',
    adminZoneCreated: 'Armoire créée.',
    adminZoneNoOrganization: 'Aucune structure que vous administrez.',
    adminProbeLocation: 'Emplacement',
    adminProbeCreated: 'Sonde ajoutée.',
    adminProbeNoZone: 'Aucune armoire que vous administrez.',
    adminOrganizationCreated: 'Structure créée.',
    adminSuperuserOnly: 'Réservé au super-administrateur.',
    adminTransferNotes: 'Notes',
    adminTransferCreated: 'Transfert enregistré.',
    adminTransferNoBox: 'Aucune boîte que vous administrez.',
    adminProbeCode: 'Code sonde',
    adminProbeType: 'Type de sonde',
    adminProbeZone: 'Armoire associée',
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
    adminPrintLabelsSearch: 'Rechercher une boîte',
    adminPrintLabelsSearchPlaceholder: 'Code, espèce ou souche',
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
    boxAttentionTitle: 'Suivi prioritaire',
    boxChecksButton: 'Alertes',
    boxChecksIntro: 'Signaux détectés à partir des derniers relevés.',
    boxChecksTitle: 'Alertes de la boîte',
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
    logoutAction: 'Se d\u00e9connecter',
    logoutError: 'D\u00e9connexion impossible pour le moment.',
    measurementDate: 'Date du relevé',
    measurementForbidden: 'Ce compte ne peut pas créer de relevé.',
    measurementHistory: 'Historique des relevés',
    measurementSaved: 'Relevé enregistré',
    measurementUpdated: 'Relevé modifié',
    editMeasurement: 'Modifier',
    cancelEdit: 'Annuler la modification',
    measurementEditing: 'Modification du relevé en cours',
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
    noZone: 'Sans armoire',
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
    qrLabelAddToSelection: 'Ajouter à la sélection',
    qrLabelAlreadySelected: 'Déjà dans la sélection',
    qrLabelClearSelection: 'Vider',
    qrLabelDownload: 'Télécharger',
    qrLabelHelp: 'Étiquette prête à imprimer et coller sur la boîte.',
    qrLabelPrintSelection: 'Imprimer la sélection',
    qrLabelSelectionCount: 'étiquette(s) sélectionnée(s)',
    qrLabelTitle: 'Étiquette QR code',
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
    subcultureForbidden: 'Ce compte ne peut pas créer de repiquage.',
    subcultureSaved: 'Repiquage enregistré',
    polypDropAdviceText: 'polypes de moins que le relevé précédent. Vérifier la boîte avant la prochaine saisie.',
    polypDropAdviceAction: 'Contrôler la boîte au prochain passage.',
    polypDropAdviceTitle: 'Baisse de polypes',
    checkImportanceHigh: 'Important',
    checkImportanceMedium: 'À surveiller',
    detectedSignal: 'Signal détecté',
    suggestedAction: 'Action proposée',
    temperatureShort: 'Temp.',
    targetTemperature: 'Consigne',
    salinityShort: 'Sal.',
    salinityFull: 'Salinité (PSU)',
    aliveBoxes: 'Vivantes',
    backToZones: 'Retour aux armoires',
    boxAttention: 'À surveiller',
    boxesHealthy: 'Sans alerte',
    zoneActivityTitle: 'Activité récente',
    zoneAttentionTitle: 'À vérifier',
    zoneFilterAll: 'Toutes',
    zoneFilterAttention: 'À vérifier',
    zoneFilterLiving: 'Vivantes',
    zoneOverviewAttentionDetails: 'Consultez les armoires qui demandent une vérification.',
    zoneOverviewAttentionTitle: 'À vérifier',
    zoneOverviewNoProbe: 'Aucune sonde',
    zoneOverviewSortLocation: 'Rangement',
    zoneOverviewSortTemperature: 'Température',
    zoneOverviewThermalGap: 'Écart thermique',
    zoneOverviewMissingMeasurements: 'relevé(s) manquant(s)',
    zoneTarget: 'Consigne',
    zoneNoAttention: 'Aucune action à prévoir dans cette armoire.',
    zoneNoRecentActivity: 'Aucun relevé récent dans cette armoire.',
    zoneSummaryAlive: 'Vivantes',
    zoneSummaryAttention: 'À vérifier',
    deadBoxes: 'Mortes',
    emptyZone: 'Aucune boîte dans cette armoire.',
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
    zoneSheet: 'Fiche armoire thermique',
    zoneBoxesTitle: 'Boîtes dans l’armoire',
    zoneProbesTitle: 'Sondes associées',
    zones: 'Armoires',
    zonesTitle: 'Armoires thermiques',
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
    adminZoneOrganization: 'Organization',
    adminCreateZone: 'Create zone',
    adminZoneCreated: 'Zone created.',
    adminZoneNoOrganization: 'No organization you administer.',
    adminProbeLocation: 'Location',
    adminProbeCreated: 'Probe added.',
    adminProbeNoZone: 'No zone you administer.',
    adminOrganizationCreated: 'Organization created.',
    adminSuperuserOnly: 'Superuser only.',
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
    adminTransferTarget: 'Target institution',
    adminTransferPolyps: 'Transferred polyps',
    adminKeepTransferDate: 'Keep transfer date',
    adminPrepareTransfer: 'Prepare transfer',
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
    boxAttentionTitle: 'Priority follow-up',
    boxChecksButton: 'Alerts',
    boxChecksIntro: 'Signals detected from the latest measurements.',
    boxChecksTitle: 'Box alerts',
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
    logoutAction: 'Sign out',
    logoutError: 'Unable to sign out at the moment.',
    measurementDate: 'Measurement date',
    measurementForbidden: 'This account cannot create measurements.',
    measurementHistory: 'Measurement history',
    measurementSaved: 'Measurement saved',
    measurementUpdated: 'Measurement updated',
    editMeasurement: 'Edit',
    cancelEdit: 'Cancel edit',
    measurementEditing: 'Editing the measurement',
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
    qrLabelAddToSelection: 'Add to selection',
    qrLabelAlreadySelected: 'Already selected',
    qrLabelClearSelection: 'Clear',
    qrLabelDownload: 'Download',
    qrLabelHelp: 'Label ready to print and attach to the box.',
    qrLabelPrintSelection: 'Print selection',
    qrLabelSelectionCount: 'selected label(s)',
    qrLabelTitle: 'QR code label',
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
    subcultureForbidden: 'This account cannot create subculture events.',
    subcultureSaved: 'Subculture created',
    polypDropAdviceText: 'fewer polyps than the previous measurement. Check the box before the next entry.',
    polypDropAdviceAction: 'Check this box during the next lab round.',
    polypDropAdviceTitle: 'Polyp decrease',
    checkImportanceHigh: 'Important',
    checkImportanceMedium: 'Monitor',
    detectedSignal: 'Detected signal',
    suggestedAction: 'Suggested action',
    temperatureShort: 'Temp.',
    targetTemperature: 'Target',
    salinityShort: 'Sal.',
    salinityFull: 'Salinity (PSU)',
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
    zoneOverviewNoProbe: 'No probe',
    zoneOverviewSortLocation: 'Location',
    zoneOverviewSortTemperature: 'Temperature',
    zoneOverviewThermalGap: 'Thermal gap',
    zoneOverviewMissingMeasurements: 'missing measurement(s)',
    zoneTarget: 'Target',
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
  const isExportOptionsLoading = ['exports', 'admin'].includes(activeTab) && data.exportOptions === null;
  const workspacePageKey = `${activeTab}-${route.boxCode ?? route.boxId ?? 'list'}-${route.zoneId ?? 'list'}`;
  const availableTabs = useMemo(() => {
    if (!isDesktopApp) return labTabs;
    return canUseAdmin
      ? ([...desktopTabs.slice(0, -1), 'admin', 'profile'] as TabId[])
      : desktopTabs;
  }, [canUseAdmin, isDesktopApp]);

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
      zones: '/zones',
      exports: '/exports',
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

  function printQrLabelSelection() {
    printQrLabels(qrLabelSelection);
  }

  useEffect(() => {
    const shouldWaitForProfile = activeTab === 'admin' && isLoading && !data.profile;
    if (shouldWaitForProfile) return;
    if (availableTabs.includes(activeTab)) return;

    navigateTo({ tab: 'pilotage', boxCode: null, boxId: null }, '/');
  }, [activeTab, availableTabs, data.profile, isLoading]);

  useEffect(() => {
    if (isLoginRoute || data.exportOptions || !['exports', 'admin'].includes(activeTab)) return;

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
  }, [activeTab, data.exportOptions, isLoginRoute]);

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

    setData((current) => mergeBoxDetail(current, detail));
    return created;
  }

  async function updateMeasurement(boxId: number, measurementId: number, payload: MeasurementPayload) {
    const updated = await apiPatch<BiologicalMeasurement>(
      `/api/boxes/${boxId}/measurements/${measurementId}/`,
      payload,
    );
    const detail = await apiGet<BoxDetail>(`/api/boxes/${boxId}/`);

    setData((current) => mergeBoxDetail(current, detail));
    return updated;
  }

  async function createSubculture(boxId: number, payload: SubculturePayload) {
    const result = await apiPost<SubcultureResult>(`/api/boxes/${boxId}/subcultures/`, payload);
    const detail = await apiGet<BoxDetail>(`/api/boxes/${boxId}/`);

    setData((current) => ({
      ...mergeBoxDetail(current, detail),
      boxes: upsertBoxes(current.boxes, [detail, ...result.children]),
      exportOptions: null,
    }));
  }

  async function moveBox(boxId: number, payload: BoxMovePayload) {
    const detail = await apiPost<BoxDetail>(`/api/boxes/${boxId}/move/`, payload);
    const zones = await apiGet<PaginatedResponse<ThermalZone>>('/api/thermal-zones/?limit=80');

    setData((current) => ({
      ...mergeBoxDetail(current, detail),
      zones: zones.results,
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

  async function createProbe(payload: ProbePayload) {
    await apiPost<Probe>('/api/probes/', payload);
    // Probes are nested inside the zone payload, so refresh the zones list.
    const zones = await apiGet<PaginatedResponse<ThermalZone>>('/api/thermal-zones/?limit=80');
    setData((current) => ({ ...current, zones: zones.results }));
  }

  async function createOrganization(payload: OrganizationPayload) {
    await apiPost<Organization>('/api/organizations/', payload);
    // Refresh export options so the new organization appears in the lists.
    const exportOptions = await apiGet<ExportOptions>('/api/exports/options/');
    setData((current) => ({ ...current, exportOptions }));
  }

  async function createBoxTransfer(payload: BoxTransferPayload) {
    await apiPost<unknown>('/api/box-transfers/', payload);
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
                onLoadLineageGraph={loadLineageGraph}
                onOpenBox={openBox}
                onOpenZone={openZone}
                onAddQrLabel={addQrLabelToSelection}
                onBack={closeBoxPage}
                onClearQrLabelSelection={clearQrLabelSelection}
                onPrintQrLabelSelection={printQrLabelSelection}
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
                onCreateProbe={createProbe}
                onCreateOrganization={createOrganization}
                onCreateTransfer={createBoxTransfer}
                t={t}
                zones={data.zones}
              />
            )}

            {activeTab === 'profile' && (
              <ProfileView
                isLoading={isLoading}
                labels={getProfileLabels(t)}
                profile={data.profile}
                onLogout={logoutCurrentUser}
                onUpdateLanguage={updateLanguage}
              />
            )}
          </div>
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
  profile,
  language,
  qrLabelSelection,
  isLoading,
  onCreateMeasurement,
  onUpdateMeasurement,
  onCreateSubculture,
  onMoveBox,
  onLoadLineageGraph,
  onOpenBox,
  onOpenZone,
  onAddQrLabel,
  onBack,
  onClearQrLabelSelection,
  onPrintQrLabelSelection,
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
  onLoadLineageGraph: (boxId: number) => Promise<LineageGraph>;
  onOpenBox: (boxId: number, globalCode: string) => void;
  onOpenZone: (zoneId: number) => void;
  onAddQrLabel: (label: QrLabelItem) => void;
  onBack: () => void;
  onClearQrLabelSelection: () => void;
  onPrintQrLabelSelection: () => void;
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
    setIsChecksOpen(false);
    setSaveError(null);
    setSaveMessage(null);
    setLastSavedMeasurementId(null);
    setEditingMeasurementId(null);
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

  const checkCount = Number(Boolean(polypDropDetected));

  const qr = 'qr_image_url' in box
    ? { imageUrl: getBoxQrImageUrl(box), scanUrl: getBoxScanUrl(box) }
    : null;
  const lineage = getBoxLineage(box);
  const currentZone = getCurrentThermalZone(box, zones);
  const createdOn = getBoxCreatedDate(box);
  const statusPresentation = getBoxStatusPresentation(box.status, language);
  const canWriteLabData = userCanWriteLabData(profile, box.organization.id);

  async function saveMeasurement(): Promise<boolean> {
    if (!box || isSaving) return false;

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
      setForm(getInitialMeasurementForm());
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
    setForm(getInitialMeasurementForm());
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
    <section className={canWriteLabData ? 'box-page' : 'box-page is-read-only'}>
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
              </div>
              <p className="box-species-name">{box.species.scientific_name}</p>
              <span className={`box-life-status is-${statusPresentation.tone}`}>
                {statusPresentation.label}
              </span>
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
          <InfoPill label={t('salinityShort')} value={formatSalinity(box.latest_salinity_psu)} />
        </div>

        {(checkCount > 0 || canWriteLabData) ? (
          <div className="box-action-stack">
            {checkCount > 0 ? (
              <button
                className="box-alert-trigger"
                type="button"
                aria-label={`${t('boxChecksButton')} (${checkCount})`}
                title={`${t('boxChecksButton')} (${checkCount})`}
                onClick={() => setIsChecksOpen(true)}
              >
                <BellIcon />
                <strong>{checkCount}</strong>
              </button>
            ) : null}

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
          </div>
        ) : null}
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

                <label>
                  {t('salinityFull')}
                  <input
                    min="0"
                    step="0.1"
                    inputMode="decimal"
                    placeholder="35"
                    type="number"
                    value={form.salinity}
                    onChange={(event) => setForm((current) => ({ ...current, salinity: event.target.value }))}
                  />
                </label>
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
                labels={{
                  hold: t('holdToSave'),
                  save: t('saveMeasurement'),
                  saved: t('measurementSaved'),
                  saving: t('saving'),
                }}
                onSave={saveMeasurement}
              />

              {!isDesktopApp ? (
                <div className="measurement-edit-actions">
                  {editingMeasurementId != null ? (
                    <div className="measurement-edit-row">
                      <span className="measurement-edit-hint">{t('measurementEditing')}</span>
                      <button type="button" className="text-button" onClick={cancelEditingMeasurement}>
                        {t('cancelEdit')}
                      </button>
                    </div>
                  ) : (
                    <button
                      type="button"
                      className="measurement-save-button measurement-edit-button"
                      disabled={lastSavedMeasurementId == null}
                      onClick={startEditingLastMeasurement}
                    >
                      <span>{t('editMeasurement')}</span>
                    </button>
                  )}
                </div>
              ) : null}
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

        {isChecksOpen && checkCount > 0 ? (
          <BoxChecksModal
            polypDropCount={polypDropCount}
            polypDropDetected={Boolean(polypDropDetected)}
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
              clearSelection: t('qrLabelClearSelection'),
              close: t('close'),
              download: t('qrLabelDownload'),
              help: t('qrLabelHelp'),
              print: t('print'),
              printSelection: t('qrLabelPrintSelection'),
              qrCode: t('qrCode'),
              selectionCount: t('qrLabelSelectionCount'),
              title: t('qrLabelTitle'),
            }}
            qrImageUrl={qr.imageUrl}
            selectedLabels={qrLabelSelection}
            onAddToSelection={onAddQrLabel}
            onClearSelection={onClearQrLabelSelection}
            onClose={() => setIsQrLabelOpen(false)}
            onPrintSelection={onPrintQrLabelSelection}
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

function BoxChecksModal({
  polypDropCount,
  polypDropDetected,
  t,
  onClose,
}: {
  polypDropCount: number;
  polypDropDetected: boolean;
  t: TFunction;
  onClose: () => void;
}) {
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
    historyButton: t('historyButton'),
    lineageEmptyGraph: t('lineageEmptyGraph'),
    lineageLoading: t('lineageLoading'),
    lineageRetry: t('lineageRetry'),
    lineageTab: t('analysisTabLineage'),
    measurementHistory: t('measurementHistory'),
    measurementsTab: t('analysisTabMeasurements'),
    movedTo: t('movedTo'),
    movementHistoryTitle: t('movementHistoryTitle'),
    movementsTab: t('analysisTabMovements'),
    noComment: t('noComment'),
    noMeasurementHistory: t('noMeasurementHistory'),
    noMovementHistory: t('noMovementHistory'),
    parents: t('parents'),
    polyps: t('polyps'),
  };
}

function getProfileLabels(t: TFunction) {
  return {
    account: t('account'),
    logoutAction: t('logoutAction'),
    logoutError: t('logoutError'),
    profileEmail: t('profileEmail'),
    profileLanguage: t('profileLanguage'),
    profileMemberships: t('profileMemberships'),
    profileNoEmail: t('profileNoEmail'),
    profileNoMembership: t('profileNoMembership'),
    profilePreferences: t('profilePreferences'),
    roleDescAdmin: t('roleDescAdmin'),
    roleDescTechnician: t('roleDescTechnician'),
    roleDescViewer: t('roleDescViewer'),
    saving: t('saving'),
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

function getInitialMeasurementForm() {
  return {
    measuredOn: getTodayDateValue(),
    polypCount: '',
    ephyraeCount: '',
    salinity: '',
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

function userHasAdminRole(profile: UserProfile | null) {
  if (!profile) return false;
  if (profile.is_superuser) return true;
  return profile.memberships.some((membership) => membership.role === 'admin');
}

function userCanWriteLabData(profile: UserProfile | null, organizationId: number) {
  if (!profile) return false;
  if (profile.is_superuser) return true;

  return profile.memberships.some(
    (membership) => membership.organization.id === organizationId
      && ['admin', 'lab_technician'].includes(membership.role),
  );
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
    return { tab: 'admin', boxCode: null, boxId: null };
  }

  if (path === '/profile') {
    return { tab: 'profile', boxCode: null, boxId: null };
  }

  return { tab: 'pilotage', boxCode: null, boxId: null };
}
