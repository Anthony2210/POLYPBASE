import type { AuditBusinessDetails, AuditChanges, AuditContext, AuditFamily, AuditValue, AuditValues } from '../types';
import { getDocumentLocale } from './dateFormat';

type Translate = (key: string) => string;

export const AUDIT_FAMILIES: readonly AuditFamily[] = [
  'measurements',
  'transfers',
  'subcultures',
  'boxes',
  'exports',
  'environment',
  'accounts',
  'references',
];

/**
 * Central presentation boundary for backend family keys. Icons intentionally
 * remain unset until the product taxonomy is approved; callers never choose
 * an icon or label directly.
 */
export const AUDIT_FAMILY_PRESENTATION: Record<AuditFamily, { labelKey: string; icon: null }> = {
  measurements: { labelKey: 'auditFamilyMeasurements', icon: null },
  transfers: { labelKey: 'auditFamilyTransfers', icon: null },
  subcultures: { labelKey: 'auditFamilySubcultures', icon: null },
  boxes: { labelKey: 'auditFamilyBoxes', icon: null },
  exports: { labelKey: 'auditFamilyExports', icon: null },
  environment: { labelKey: 'auditFamilyEnvironment', icon: null },
  accounts: { labelKey: 'auditFamilyAccounts', icon: null },
  references: { labelKey: 'auditFamilyReferences', icon: null },
};

export function getAuditFamilyLabel(family: AuditFamily, t: Translate): string {
  return t(AUDIT_FAMILY_PRESENTATION[family].labelKey);
}

export type AuditEntryLike = {
  action: string;
  action_label?: string;
  description: string;
  object_type?: string;
  object_id?: string;
  metadata?: Record<string, unknown>;
  business_details?: AuditBusinessDetails | null;
  context?: AuditContext | null;
};

export type AuditResourceLike = {
  type: string;
  identifier?: string | null;
  label?: string | null;
};

export type AuditEventFamily = AuditFamily;

export type AuditInlineBusinessItem = {
  key: string;
  label: string;
  value?: string;
  before?: string;
  after?: string;
  unit?: string;
  showLabel?: boolean;
};

export type AuditValueChange = {
  before: unknown;
  after: unknown;
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
  actif: 'auditMetaAccessActive',
  active: 'auditMetaActive',
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
  source_global_code: 'auditMetaSourceGlobalCode',
  source_organization: 'auditMetaSourceOrganization',
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
  date_from: 'auditMetaDateFrom',
  date_to: 'auditMetaDateTo',
  include_other_zones: 'auditMetaIncludeOtherZones',
  responsable: 'auditMetaResponsableStatus',
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
  'Demo biological measurement entry.': 'auditDescriptionMeasurementCreatedLegacy',
  // A subculture row without a usable parent code loses its trailing separator,
  // so it must still resolve to the translated subculture summary.
  'Subculture created from': 'auditSummarySubcultureCreated',
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

const BUSINESS_SUMMARY_PREFIX_KEYS: Array<[string, string]> = [
  ['Box created manually: ', 'auditSummaryBoxCreated'],
  ['Box opened: ', 'auditSummaryBoxOpened'],
  ['QR scan of ', 'auditSummaryBoxScanned'],
  ['Box archived: ', 'auditSummaryBoxArchived'],
  ['Box activated: ', 'auditSummaryBoxActivated'],
  ['Box deactivated: ', 'auditSummaryBoxDeactivated'],
  ['Box reactivated: ', 'auditSummaryBoxReactivated'],
  ['Box moved to ', 'auditSummaryBoxMoved'],
  ['Subculture created from ', 'auditSummarySubcultureCreated'],
  ['Manual temperature recorded: ', 'auditSummaryTemperatureRecorded'],
  ['Thermal zone created: ', 'auditSummaryLocationCreated'],
  ['Thermal zone updated: ', 'auditSummaryLocationUpdated'],
  ['Probe created: ', 'auditSummaryProbeCreated'],
  ['Box transfer prepared: ', 'auditSummaryTransferPrepared'],
  ['Transfer imported from ', 'auditSummaryTransferImported'],
  ['Historical box inventory initialized for ', 'auditSummaryInventoryInitialized'],
  ['Historical box qualified as ', 'auditSummaryBoxQualified'],
];

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
const AUDIT_FIELD_PRIORITY = [
  'polypes',
  'ephyrules',
  'salinite_psu',
  'date',
  'note',
  'notes',
] as const;

export function getAuditActionLabel(
  entry: Pick<AuditEntryLike, 'action' | 'action_label'>,
  t: Translate,
): string {
  const key = ACTION_LABEL_KEYS[entry.action];
  if (key) return t(key);
  return entry.action_label || entry.action || '-';
}

/**
 * A subculture entry only replaces the parent box presentation when the
 * normalized payload actually carries both the parent and at least one usable
 * child. Legacy rows without a usable child list keep the parent box visible.
 */
export function hasAuditSubcultureSummary(details: AuditBusinessDetails | null | undefined): boolean {
  return details?.type === 'subculture'
    && Boolean(details.parent_global_code)
    && (details.child_global_codes?.length ?? 0) > 0;
}

export function getAuditBoxSummaryParts(entry: AuditEntryLike, t: Translate): [string, string] | null {
  const details = entry.business_details;
  const description = (entry.description || '').trim();
  let key: string | null = null;

  if (details?.type === 'measurement') {
    key = entry.action === 'update' || (details.changes && Object.keys(details.changes).length)
      ? 'auditInlineMeasurementCorrected'
      : 'auditInlineMeasurementRecorded';
  } else if (details?.type === 'box_movement') {
    if (!details.to_zone) {
      key = 'auditInlineBoxMoved';
    } else {
      const template = fillTemplate(t('auditInlineBoxMovedTo'), {
        location: details.to_zone,
      });
      const placeholderIndex = template.indexOf('{box}');
      return placeholderIndex < 0
        ? null
        : [template.slice(0, placeholderIndex), template.slice(placeholderIndex + '{box}'.length)];
    }
  } else if (details?.type === 'box_status') {
    if (details.transition?.to === 'inactive') key = 'auditInlineBoxDeactivated';
    else if (details.transition?.from === 'inactive' && details.transition.to === 'active') key = 'auditInlineBoxReactivated';
    else if (details.transition?.to === 'active') key = 'auditInlineBoxActivated';
  } else if (details?.type === 'subculture') {
    // Legacy subculture rows without a usable child list must keep the parent
    // box visible instead of collapsing to a generic "subculture created" line.
    if (hasAuditSubcultureSummary(details)) return null;
    key = 'auditInlineSubculture';
  } else if (details?.type === 'transfer_out') {
    key = 'auditInlineTransferOut';
  } else if (details?.type === 'transfer_import') {
    key = 'auditInlineTransferImport';
  } else if (description.startsWith('Box created')) {
    key = 'auditInlineBoxCreated';
  }

  if (!key) return null;
  const template = t(key);
  const placeholderIndex = template.indexOf('{box}');
  return placeholderIndex < 0
    ? null
    : [template.slice(0, placeholderIndex), template.slice(placeholderIndex + '{box}'.length)];
}

export function getAuditInlineBusinessItems(
  details: AuditBusinessDetails | null | undefined,
  t: Translate,
): AuditInlineBusinessItem[] {
  if (!details) return [];

  if (details.type === 'measurement') {
    const source = details.changes ?? details.values ?? {};
    return ['polypes', 'ephyrules', 'salinite_psu'].flatMap<AuditInlineBusinessItem>((key) => {
      if (!(key in source)) return [];
      const label = getAuditMetadataKeyLabel(key, t);
      const change = getAuditValueChange(source[key]);
      if (change) {
        return [{
          key,
          label,
          before: formatAuditMeasurementValue(key, change.before, t),
          after: formatAuditMeasurementValue(key, change.after, t),
        }];
      }
      if (source[key] === null || source[key] === undefined || source[key] === '') return [];
      return [{ key, label, value: formatAuditMeasurementValue(key, source[key], t) }];
    });
  }

  if (details.type === 'transfer_out') {
    return compactAuditInlineItems([
      details.destination_organization
        ? { key: 'destination', label: getAuditMetadataKeyLabel('to_organization', t), value: details.destination_organization }
        : null,
      details.polyp_count !== undefined
        ? { key: 'polypes', label: getAuditMetadataKeyLabel('polypes', t), value: String(details.polyp_count) }
        : null,
    ]);
  }

  if (details.type === 'transfer_import') {
    return compactAuditInlineItems([
      details.source_organization
        ? { key: 'source', label: getAuditMetadataKeyLabel('source_organization', t), value: details.source_organization }
        : null,
    ]);
  }

  return [];
}

export function getAuditBusinessSummary(entry: AuditEntryLike, t: Translate): string {
  const description = (entry.description || '').trim();
  const details = entry.business_details;
  if (details?.type === 'box_movement' && details.to_zone) {
    return fillTemplate(t('auditSummaryBoxMovedTo'), { location: details.to_zone });
  }
  if (details?.type === 'subculture' && hasAuditSubcultureSummary(details)) {
    const children = details.child_global_codes ?? [];
    return fillTemplate(
      t(children.length === 1 ? 'auditSummarySubcultureOneChild' : 'auditSummarySubcultureManyChildren'),
      { children: children.join(', '), parent: details.parent_global_code ?? '' },
    );
  }
  const summaryRule = BUSINESS_SUMMARY_PREFIX_KEYS.find(([prefix]) => description.startsWith(prefix));
  if (summaryRule) return t(summaryRule[1]);
  return getAuditDescriptionLabel(entry, t);
}

export function getAuditDescriptionLabel(entry: AuditEntryLike, t: Translate): string {
  const description = (entry.description || '').trim();

  if (description.startsWith('Biological measurement edited for ')) {
    return t('auditDescriptionMeasurementCorrected');
  }
  if (description.startsWith('Biological measurement for ')) {
    return t(
      entry.action === 'entry'
        ? 'auditDescriptionMeasurementCreated'
        : 'auditDescriptionMeasurementCorrected',
    );
  }

  const rule = DESCRIPTION_RULES.find((candidate) => description.startsWith(candidate.prefix));
  if (rule) return rule.render(description.slice(rule.prefix.length), t);

  const exactKey = DESCRIPTION_EXACT_KEYS[description];
  if (exactKey) return t(exactKey);
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

function formatAuditMeasurementValue(key: string, value: unknown, t: Translate): string {
  const formatted = formatAuditMetadataValue(value, t);
  return key === 'salinite_psu' ? formatted.replace(/\s*PSU$/i, '') : formatted;
}

export function formatAuditMetadataValue(value: unknown, t: Translate): string {
  if (value === null || value === undefined || value === '') return '-';
  if (typeof value === 'boolean') return value ? t('auditValueYes') : t('auditValueNo');
  if (typeof value === 'number') return String(value);
  if (typeof value === 'string') return value.startsWith('internal_') ? '-' : getAuditValueLabel(value, t);
  if (Array.isArray(value)) {
    const items = value
      .filter((item) => typeof item !== 'string' || !item.startsWith('internal_'))
      .map((item) => formatAuditMetadataValue(item, t));
    return items.length ? items.join(', ') : '-';
  }
  const change = getAuditValueChange(value);
  return change ? formatAuditChange(change, t) : t('auditValueUnavailable');
}

export function getAuditValueChange(value: unknown): AuditValueChange | null {
  const change = getMetadataRecord(value);
  if (!change) return null;

  const hasBefore = 'avant' in change || 'before' in change;
  const hasAfter = 'apres' in change || 'after' in change;
  if (!hasBefore && !hasAfter) return null;

  return {
    before: 'avant' in change ? change.avant : change.before,
    after: 'apres' in change ? change.apres : change.after,
  };
}

export function formatAuditChange(value: unknown, t: Translate): string {
  const change = getAuditValueChange(value);
  if (!change) return formatAuditMetadataValue(value, t);
  return `${formatAuditMetadataValue(change.before, t)} -> ${formatAuditMetadataValue(change.after, t)}`;
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

export function formatAuditTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat(getDocumentLocale(), {
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

export function getAuditEventFamily(entry: AuditEntryLike & { family?: AuditFamily }): AuditEventFamily {
  if (entry.family && AUDIT_FAMILIES.includes(entry.family)) return entry.family;
  if (entry.action === 'transfer' || entry.action === 'import' || entry.description.startsWith('Transfer')) {
    return 'transfers';
  }
  if (entry.action === 'subculture') return 'subcultures';
  if (entry.action === 'export') return 'exports';
  if (entry.object_type === 'measurement' || entry.object_type === 'measurements') return 'measurements';
  if (entry.object_type === 'account' || entry.object_type === 'user') return 'accounts';
  if (entry.object_type === 'thermal_zone' || entry.object_type === 'probe' || entry.object_type === 'alert') {
    return 'environment';
  }
  if (entry.object_type === 'species' || entry.object_type === 'strain' || entry.object_type === 'organization') {
    return 'references';
  }
  return 'boxes';
}

export type AuditDetailContent = {
  values: AuditValues | null;
  changes: AuditChanges | null;
};

export function getAuditBusinessDetailContent(details: AuditBusinessDetails | null | undefined): AuditDetailContent {
  if (!details || typeof details !== 'object') return { values: null, changes: null };

  switch (details.type) {
    case 'measurement':
      return {
        values: null,
        changes: compactAuditChanges(withoutAuditInlineMeasurementFields(details.changes)),
      };
    case 'box':
    case 'account':
    case 'reference': {
      const changes = compactAuditChanges('changes' in details ? details.changes : undefined);
      return {
        values: compactAuditRecord(withoutAuditRepeatedFields(
          'values' in details ? details.values : undefined,
          changes,
          true,
        )),
        changes,
      };
    }
    case 'environment': {
      const changes = compactAuditChanges(details.changes);
      return {
        values: compactAuditRecord(withoutAuditRepeatedFields(details.values, changes, false)),
        changes,
      };
    }
    case 'transfer_out':
    case 'transfer_import':
    case 'box_movement':
      return { values: null, changes: null };
    case 'box_status':
      return { values: null, changes: null };
    case 'box_inventory_initialization':
      return {
        values: compactAuditRecord({ box_count: details.box_count, statut: details.target_status }),
        changes: null,
      };
    case 'export':
      return {
        values: compactAuditRecord({
          box_count: details.box_count,
          measurement_count: details.measurement_count,
          week_count: details.week_count,
          ...(details.filters ?? {}),
        }),
        changes: null,
      };
    case 'subculture':
    default:
      return { values: null, changes: null };
  }
}

export function getAuditBusinessNote(details: AuditBusinessDetails | null | undefined): string {
  if (!details) return '';
  if (details.type === 'measurement') {
    const changedNote = getAuditValueChange(details.changes?.note ?? details.changes?.notes);
    if (changedNote) return typeof changedNote.after === 'string' ? changedNote.after.trim() : '';
    return getAuditNote(details.values);
  }
  if (details.type === 'transfer_out' || details.type === 'box_movement') {
    return typeof details.note === 'string' ? details.note.trim() : '';
  }
  if (details.type === 'box_status') {
    return typeof details.stop_reason === 'string' ? details.stop_reason.trim() : '';
  }
  if ('values' in details) return getAuditNote(details.values);
  return '';
}

export function hasAuditBusinessDetails(details: AuditBusinessDetails | null | undefined): boolean {
  const content = getAuditBusinessDetailContent(details);
  return Boolean(content.values || content.changes);
}


function withoutAuditRepeatedFields(
  values: AuditValues | undefined,
  changes: AuditChanges | null,
  hideDate: boolean,
): AuditValues | undefined {
  if (!values) return undefined;
  const repeatedKeys = new Set(['note', 'notes', ...(hideDate ? ['date'] : [])]);
  return Object.fromEntries(
    Object.entries(values).filter(([key]) => !repeatedKeys.has(key) && !(key in (changes ?? {}))),
  );
}

function getAuditNote(values: AuditValues | undefined): string {
  const value = values?.note ?? values?.notes;
  return typeof value === 'string' ? value.trim() : '';
}

function compactAuditRecord(record: Record<string, AuditValue | undefined> | undefined): AuditValues | null {
  if (!record) return null;
  const entries = Object.entries(record).filter(([, value]) => value !== undefined && value !== null && value !== '');
  return entries.length ? Object.fromEntries(entries) as AuditValues : null;
}

function compactAuditChanges(changes: AuditChanges | undefined): AuditChanges | null {
  return changes && Object.keys(changes).length ? changes : null;
}

function withoutAuditInlineMeasurementFields(changes: AuditChanges | undefined): AuditChanges | undefined {
  if (!changes) return undefined;
  const inlineKeys = new Set(['polypes', 'ephyrules', 'salinite_psu', 'note', 'notes']);
  return Object.fromEntries(Object.entries(changes).filter(([key]) => !inlineKeys.has(key)));
}

function compactAuditInlineItems(
  items: Array<AuditInlineBusinessItem | null>,
): AuditInlineBusinessItem[] {
  return items.filter((item): item is AuditInlineBusinessItem => item !== null);
}

export function orderAuditFieldEntries(
  entries: ReadonlyArray<[string, unknown]>,
): Array<[string, unknown]> {
  const priorities = new Map<string, number>(AUDIT_FIELD_PRIORITY.map((key, index) => [key, index]));
  return entries
    .map((entry, index) => ({ entry, index }))
    .sort((first, second) => {
      const firstPriority = priorities.get(first.entry[0]) ?? AUDIT_FIELD_PRIORITY.length;
      const secondPriority = priorities.get(second.entry[0]) ?? AUDIT_FIELD_PRIORITY.length;
      return firstPriority - secondPriority || first.index - second.index;
    })
    .map(({ entry }) => entry);
}

export function getAuditInitialPolypsLabel(count: number, t: Translate): string {
  const key = count === 0
    ? 'auditRelationInitialPolypsZero'
    : count === 1
      ? 'auditRelationInitialPolypsOne'
      : 'auditRelationInitialPolypsMany';
  return fillTemplate(t(key), { count: String(count) });
}

export function isAuditNoteField(key: string): boolean {
  return key === 'note' || key === 'notes';
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
 * Object types whose stored object id is business-readable text written by the
 * audit writers, such as a box global code, a zone name or a probe code.
 * Every other object type is treated as an opaque internal identifier, so a new
 * writer falls out of the interface by default instead of leaking a primary key.
 */
const READABLE_TARGET_OBJECT_TYPES = new Set([
  'box',
  'measurements',
  'organization',
  'probe',
  'thermal_zone',
]);

/** A numeric primary key or a UUID is a database identifier, never a label. */
const OPAQUE_IDENTIFIER = /^(?:\d+|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$/i;

/**
 * Resolve a secondary target only when it is a known business label. When no
 * safe label exists the history renders no target instead of a technical value.
 */
function getReadableTargetLabel(objectType: string | undefined, value: string | null | undefined): string {
  if (!objectType || !READABLE_TARGET_OBJECT_TYPES.has(objectType)) return '';
  const label = (value ?? '').trim();
  if (!label || label.startsWith('internal_') || OPAQUE_IDENTIFIER.test(label)) return '';
  return label;
}

/**
 * Readable target of an administration entry. Account targets come from the
 * trusted audit values, because the raw object id is an opaque internal
 * username that must never reach the interface.
 */
export function getAuditTargetLabel(entry: AuditEntryLike): string {
  if (entry.object_type === 'account') {
    const details = entry.business_details;
    const values = details?.type === 'account' ? details.values : undefined;
    const name = typeof values?.nom === 'string' ? values.nom : '';
    const email = typeof values?.email === 'string' ? values.email : '';
    return getAccountDisplayLabel(name) || getAccountDisplayLabel(email);
  }
  return getReadableTargetLabel(entry.object_type, entry.object_id);
}

export function getPersonalResourceLabel(resource: AuditResourceLike): string {
  const label = (resource.label ?? resource.identifier ?? '').trim();
  if (resource.type === 'account') return getAccountDisplayLabel(label);
  return getReadableTargetLabel(resource.type, label);
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
