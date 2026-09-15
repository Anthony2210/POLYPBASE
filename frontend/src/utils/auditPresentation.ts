import { getDocumentLocale } from './dateFormat';

type Translate = (key: string) => string;

export type AuditEntryLike = {
  action: string;
  action_label?: string;
  description: string;
  object_type?: string;
  object_id?: string;
  metadata?: Record<string, unknown>;
};

export type AuditResourceLike = {
  type: string;
  identifier: string;
  label?: string | null;
};

const ACTION_LABEL_KEYS: Record<string, string> = {
  entry: 'auditActionEntry',
  update: 'auditActionUpdate',
  creation: 'auditActionCreation',
  archive: 'auditActionArchive',
  subculture: 'auditActionSubculture',
  transfer: 'auditActionTransfer',
  import: 'auditActionImport',
  export: 'auditActionExport',
  scan: 'auditActionScan',
  view: 'auditActionView',
  login: 'auditActionLogin',
};

const OBJECT_TYPE_KEYS: Record<string, string> = {
  box: 'auditObjectBox',
  measurement: 'auditObjectMeasurement',
  measurements: 'auditObjectMeasurements',
  thermal_zone: 'auditObjectThermalZone',
  organization: 'auditObjectOrganization',
  user: 'auditObjectUser',
  account: 'auditObjectAccount',
  probe: 'auditObjectProbe',
  species: 'auditObjectSpecies',
  strain: 'auditObjectStrain',
  alert: 'auditObjectAlert',
  box_inventory_initialization: 'auditObjectBoxInventoryInitialization',
};

const METADATA_KEY_KEYS: Record<string, string> = {
  acces_actif: 'auditMetaAccessActive',
  a_verifier: 'auditMetaNeedsAttention',
  ancienne_zone: 'auditMetaPreviousZone',
  apres: 'auditMetaAfter',
  avant: 'auditMetaBefore',
  box_count: 'auditMetaBoxCount',
  box_id: 'auditMetaBoxId',
  capacite: 'auditMetaCapacity',
  child_box_ids: 'auditMetaChildBoxIds',
  child_global_codes: 'auditMetaChildGlobalCodes',
  code: 'auditMetaCode',
  code_global: 'auditMetaGlobalCode',
  date: 'auditMetaDate',
  date_deplacement: 'auditMetaMovementDate',
  date_entree: 'auditMetaEntryDate',
  email: 'auditMetaEmail',
  email_contact: 'auditMetaContactEmail',
  emplacement: 'auditMetaLocation',
  ephyrules: 'auditMetaEphyrae',
  espece: 'auditMetaSpecies',
  file_name: 'auditMetaFile',
  filters: 'auditMetaFilters',
  from_thermal_zone_name: 'auditMetaPreviousZone',
  identifiant: 'auditMetaIdentifier',
  initial_polyp_counts: 'auditMetaInitialPolypCounts',
  is_responsable: 'auditMetaResponsableStatus',
  measurement_count: 'auditMetaMeasurementCount',
  measurement_id: 'auditMetaMeasurementId',
  membership_id: 'auditMetaMembershipId',
  movement_id: 'auditMetaMovementId',
  nom: 'auditMetaName',
  note: 'auditMetaNote',
  notes: 'auditMetaNotes',
  nouvelle_zone: 'auditMetaNewZone',
  numero_boite: 'auditMetaBoxNumber',
  pays: 'auditMetaCountry',
  polypes: 'auditMetaPolyps',
  position: 'auditMetaPosition',
  probe_id: 'auditMetaProbeId',
  raison_arret: 'auditMetaStopReason',
  role: 'auditMetaRole',
  salinite_psu: 'auditMetaSalinity',
  source: 'auditMetaSource',
  souche: 'auditMetaStrain',
  statut: 'auditMetaStatus',
  statut_culture: 'auditMetaCultureStatus',
  strobiles: 'auditMetaStrobilae',
  structure: 'auditMetaOrganization',
  subculture_event_id: 'auditMetaSubcultureEventId',
  temperature_c: 'auditMetaMeasuredTemperature',
  temperature_consigne: 'auditMetaTargetTemperature',
  thermal_zone_id: 'auditMetaThermalZoneId',
  to_organization: 'auditMetaTargetOrganization',
  to_thermal_zone_name: 'auditMetaNewZone',
  transfer_id: 'auditMetaTransferId',
  type: 'auditMetaType',
  user_id: 'auditMetaUserId',
  ville: 'auditMetaCity',
  volume_litres: 'auditMetaVolumeLiters',
  week_count: 'auditMetaWeekCount',
};

const VALUE_LABEL_KEYS: Record<string, string> = {
  active: 'auditValueActive',
  inactive: 'auditValueInactive',
  pending_review: 'auditValuePendingReview',
  not_specified: 'auditValueNotSpecified',
  good: 'auditValueGood',
  medium: 'auditValueMedium',
  bad: 'auditValueBad',
  dead: 'auditValueDead',
  web_app: 'auditValueWebApp',
  qr_link: 'auditValueQrLink',
  csv: 'auditValueCsv',
  measurements: 'auditValueMeasurements',
  box: 'auditValueBox',
  admin: 'auditValueAdmin',
  lab_technician: 'auditValueLabTechnician',
  viewer: 'auditValueViewer',
  cabinet: 'auditValueCabinet',
  incubator: 'auditValueIncubator',
  manual: 'auditValueManual',
  other: 'auditValueOther',
};

const DESCRIPTION_EXACT_KEYS: Record<string, string> = {
  // Legacy measurement rows carry no date in the description. They are still
  // creations, so they must never fall back to the raw English sentence.
  'Biological measurement recorded': 'auditDescriptionMeasurementCreatedLegacy',
  'Member access created': 'auditDescriptionMemberCreated',
  'Member access restored': 'auditDescriptionMemberRestored',
  'Member access updated': 'auditDescriptionMemberUpdated',
  'Weekly biological measurement CSV export': 'auditDescriptionWeeklyExport',
  'Organization created': 'auditDescriptionOrganizationCreated',
  'Organization updated': 'auditDescriptionOrganizationUpdated',
  'Organization deleted': 'auditDescriptionOrganizationDeleted',
  'Institution Responsable granted by platform': 'auditDescriptionResponsableGranted',
  'Institution Responsable revoked by platform': 'auditDescriptionResponsableRevoked',
  'Institution Responsable relinquished': 'auditDescriptionResponsableRelinquished',
  'Password reset from the login page': 'auditDescriptionPasswordReset',
};

const DESCRIPTION_RULES: Array<{
  prefix: string;
  render: (rest: string, t: Translate) => string;
}> = [
  { prefix: 'Box created manually: ', render: (rest, t) => fillTemplate(t('auditDescriptionBoxCreated'), { code: rest }) },
  { prefix: 'Box opened: ', render: (rest, t) => fillTemplate(t('auditDescriptionBoxOpened'), { code: rest }) },
  { prefix: 'QR scan of ', render: (rest, t) => fillTemplate(t('auditDescriptionQrScanned'), { code: rest }) },
  { prefix: 'Box archived: ', render: (rest, t) => fillTemplate(t('auditDescriptionBoxArchived'), { code: rest }) },
  { prefix: 'Box activated: ', render: (rest, t) => fillTemplate(t('auditDescriptionBoxActivated'), { code: rest }) },
  { prefix: 'Box deactivated: ', render: (rest, t) => fillTemplate(t('auditDescriptionBoxDeactivated'), { code: rest }) },
  { prefix: 'Box reactivated: ', render: (rest, t) => fillTemplate(t('auditDescriptionBoxReactivated'), { code: rest }) },
  { prefix: 'Box moved to ', render: (rest, t) => fillTemplate(t('auditDescriptionBoxMoved'), { zone: rest }) },
  { prefix: 'Subculture created from ', render: (rest, t) => fillTemplate(t('auditDescriptionSubcultureCreated'), { code: rest }) },
  { prefix: 'Manual temperature recorded: ', render: (rest, t) => fillTemplate(t('auditDescriptionManualTemperature'), { zone: rest }) },
  { prefix: 'Thermal zone created: ', render: (rest, t) => fillTemplate(t('auditDescriptionZoneCreated'), { zone: rest }) },
  { prefix: 'Thermal zone updated: ', render: (rest, t) => fillTemplate(t('auditDescriptionZoneUpdated'), { zone: rest }) },
  { prefix: 'Probe created: ', render: (rest, t) => fillTemplate(t('auditDescriptionProbeCreated'), { code: rest }) },
  { prefix: 'Box transfer prepared: ', render: (rest, t) => fillTemplate(t('auditDescriptionTransferPrepared'), { code: rest }) },
  { prefix: 'Transfer imported from ', render: (rest, t) => fillTemplate(t('auditDescriptionTransferImported'), { source: rest }) },
  { prefix: 'Alert resolved: ', render: (rest, t) => fillTemplate(t('auditDescriptionAlertResolved'), { message: rest }) },
  {
    prefix: 'Historical box inventory initialized for ',
    render: (rest, t) => fillTemplate(t('auditDescriptionInventoryInitialized'), { organization: rest }),
  },
  {
    prefix: 'Historical box qualified as ',
    render: (rest, t) => {
      const [status, code] = splitOnce(rest, ': ');
      return fillTemplate(t('auditDescriptionBoxQualified'), {
        status: getAuditValueLabel(status, t),
        code: code ?? '',
      });
    },
  },
  { prefix: 'Species created: ', render: (rest, t) => fillTemplate(t('auditDescriptionSpeciesCreated'), { name: rest }) },
  { prefix: 'Species updated: ', render: (rest, t) => fillTemplate(t('auditDescriptionSpeciesUpdated'), { name: rest }) },
  { prefix: 'Strain created: ', render: (rest, t) => fillTemplate(t('auditDescriptionStrainCreated'), { code: rest }) },
  { prefix: 'Strain updated: ', render: (rest, t) => fillTemplate(t('auditDescriptionStrainUpdated'), { code: rest }) },
];

const HIDDEN_DISPLAY_KEYS = new Set(['strobiles', 'statut_culture', 'a_verifier']);

export function getAuditActionLabel(
  entry: Pick<AuditEntryLike, 'action' | 'action_label'>,
  t: Translate,
): string {
  const key = ACTION_LABEL_KEYS[entry.action];
  if (key) return t(key);
  return entry.action_label || entry.action || '-';
}

export function getAuditDescriptionLabel(entry: AuditEntryLike, t: Translate): string {
  const description = (entry.description || '').trim();
  const metadata = getMetadataRecord(entry.metadata);

  if (description.startsWith('Biological measurement edited for ')) {
    return fillTemplate(t('auditDescriptionMeasurementCorrected'), {
      date: formatTechnicalDate(description.slice('Biological measurement edited for '.length)),
    });
  }
  if (description.startsWith('Biological measurement for ')) {
    const date = formatTechnicalDate(description.slice('Biological measurement for '.length));
    return fillTemplate(
      t(
        entry.action === 'entry'
          ? 'auditDescriptionMeasurementCreated'
          : 'auditDescriptionMeasurementCorrected',
      ),
      { date },
    );
  }

  const rule = DESCRIPTION_RULES.find((candidate) => description.startsWith(candidate.prefix));
  if (rule) return rule.render(description.slice(rule.prefix.length), t);

  const exactKey = DESCRIPTION_EXACT_KEYS[description];
  if (exactKey) return t(exactKey);

  if (metadata?.source === 'web_app') return t('auditDescriptionFromApp');
  return description || '-';
}

export function getAuditObjectTypeLabel(value: string, t: Translate): string {
  const key = OBJECT_TYPE_KEYS[value];
  return key ? t(key) : value || '-';
}

export function getAuditMetadataKeyLabel(key: string, t: Translate): string {
  const translationKey = METADATA_KEY_KEYS[key];
  return translationKey ? t(translationKey) : key.replace(/_/g, ' ');
}

export function getAuditValueLabel(value: string, t: Translate): string {
  const key = VALUE_LABEL_KEYS[value];
  return key ? t(key) : formatTechnicalDate(value);
}

export function formatAuditMetadataValue(value: unknown, t: Translate): string {
  if (value === null || value === undefined || value === '') return '-';
  if (typeof value === 'boolean') return value ? t('auditValueYes') : t('auditValueNo');
  if (typeof value === 'number') return String(value);
  if (typeof value === 'string') return getAuditValueLabel(value, t);
  return JSON.stringify(value);
}

export function formatAuditChange(value: unknown, t: Translate): string {
  const change = getMetadataRecord(value);
  if (!change) return formatAuditMetadataValue(value, t);

  const before = 'avant' in change ? change.avant : change.before;
  const after = 'apres' in change ? change.apres : change.after;
  if (before === undefined && after === undefined) return formatAuditMetadataValue(value, t);

  return `${formatAuditMetadataValue(before, t)} -> ${formatAuditMetadataValue(after, t)}`;
}

export function formatAuditDateTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat(getDocumentLocale(), {
    day: '2-digit',
    month: '2-digit',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  }).format(date);
}

export function formatAuditDayLabel(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat(getDocumentLocale(), {
    weekday: 'long',
    day: 'numeric',
    month: 'long',
    year: 'numeric',
  }).format(date);
}

export function getAuditDayKey(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, '0');
  const day = String(date.getDate()).padStart(2, '0');
  return `${year}-${month}-${day}`;
}

export function groupAuditEntriesByDay<T>(
  entries: readonly T[],
  getDate: (entry: T) => string,
): Array<{ key: string; label: string; entries: T[] }> {
  const groups: Array<{ key: string; label: string; entries: T[] }> = [];
  entries.forEach((entry) => {
    const value = getDate(entry);
    const key = getAuditDayKey(value);
    const currentGroup = groups.find((group) => group.key === key);
    if (currentGroup) {
      currentGroup.entries.push(entry);
      return;
    }
    groups.push({ key, label: formatAuditDayLabel(value), entries: [entry] });
  });
  return groups;
}

export function getAuditEditedMark(
  entry: { edited_at: string | null; edited_by_display?: string | null; created_at: string },
  t: Translate,
): string {
  if (!entry.edited_at) return '';

  const author = getAccountDisplayLabel(entry.edited_by_display);
  if (author) {
    return fillTemplate(t('auditEditedMark'), {
      date: formatAuditDateTime(entry.edited_at),
      name: author,
      created: formatAuditDateTime(entry.created_at),
    });
  }
  return fillTemplate(t('auditEditedMarkAnonymous'), {
    date: formatAuditDateTime(entry.edited_at),
    created: formatAuditDateTime(entry.created_at),
  });
}

/**
 * Readable target of an administration entry. Account targets come from the
 * trusted audit values, because the raw object id is an opaque internal
 * username that must never reach the interface.
 */
export function getAuditTargetLabel(entry: AuditEntryLike): string {
  if (entry.object_type === 'account') {
    const values = getMetadataRecord(entry.metadata?.valeurs);
    const name = typeof values?.nom === 'string' ? values.nom : '';
    const email = typeof values?.email === 'string' ? values.email : '';
    return getAccountDisplayLabel(name) || getAccountDisplayLabel(email);
  }
  return entry.object_id ?? '';
}

export function getPersonalResourceLabel(resource: AuditResourceLike): string {
  const label = (resource.label ?? resource.identifier ?? '').trim();
  if (!label || label.startsWith('internal_')) return '';
  return resource.type === 'account' ? getAccountDisplayLabel(label) : label;
}

export function getAccountDisplayLabel(value: string | null | undefined): string {
  const label = (value ?? '').trim();
  if (!label || label.startsWith('internal_')) return '';
  return label.includes('@') ? label : formatPersonName(label);
}

export function formatPersonName(value: string): string {
  const parts = value.trim().split(/\s+/).filter(Boolean);
  if (!parts.length) return '';
  if (parts.length === 1) return formatFirstName(parts[0]);

  const lastName = parts[parts.length - 1];
  const firstNames = parts.slice(0, -1).map(formatFirstName).join(' ');
  return `${firstNames} ${formatLastName(lastName)}`;
}

export function getMetadataRecord(value: unknown): Record<string, unknown> | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
  return value as Record<string, unknown>;
}

export function filterAuditDisplayRecord(record: Record<string, unknown> | null) {
  if (!record) return null;
  return Object.fromEntries(
    Object.entries(record).filter(([key]) => !HIDDEN_DISPLAY_KEYS.has(key)),
  );
}

function formatTechnicalDate(value: string) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) return value;
  const [year, month, day] = value.split('-');
  return `${day}/${month}/${year}`;
}

function formatFirstName(value: string) {
  return value
    .trim()
    .replace(/\s+/g, ' ')
    .toLocaleLowerCase('fr-FR')
    .replace(/(^|[\s'-])(\p{L})/gu, (_match, separator: string, letter: string) => {
      return `${separator}${letter.toLocaleUpperCase('fr-FR')}`;
    });
}

function formatLastName(value: string) {
  return value.trim().replace(/\s+/g, ' ').toLocaleUpperCase('fr-FR');
}

function splitOnce(value: string, separator: string): [string, string | null] {
  const index = value.indexOf(separator);
  if (index === -1) return [value, null];
  return [value.slice(0, index), value.slice(index + separator.length)];
}

export function fillTemplate(template: string, params: Record<string, string>): string {
  return Object.entries(params).reduce(
    (result, [name, value]) => result.replace(`{${name}}`, value),
    template,
  );
}
