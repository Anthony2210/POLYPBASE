// Pure helpers for Administration > Species. The server owns
// permissions, duplicates, generated codes, revisions and WoRMS verification;
// these only shape, filter and label institution-scoped data.

// Technical route identities for the same user-facing Species concept.
// Existing-reference URLs remain readable after an explicit association.
export type AdminSpeciesTarget = { kind: 'profile' | 'historical'; id: number };

export type InstitutionSpecies = {
  // Institutional profile ID; species_id is the shared technical reference.
  id: number;
  species_id: number;
  scientific_name: string;
  worms_aphia_id: number | null;
  worms_url: string | null;
  species_code: string | null;
  species_code_issue?: 'species_code_inconsistent' | null;
  revision: number;
  strain_count: number;
};

export type HistoricalSpecies = {
  species_id: number;
  shared_scientific_name: string;
  species_code: string | null;
  species_code_issue?: 'species_code_inconsistent' | null;
  strain_count: number;
  requires_historical_adoption: true;
};

export type InstitutionSpeciesList = {
  institution_species: InstitutionSpecies[];
  historical_species: HistoricalSpecies[];
};

export type SpeciesStrain = {
  id: number;
  code: string;
  name: string;
  strain_number: string | number | null;
  active_box_count: number;
  is_box_usable: boolean;
  // False for a legacy Strain without owning institution, used here.
  is_owned: boolean;
  // The active institution's verified recorded biological provenance; null when
  // none is recorded or verifiable, never a provider, place or inferred value.
  recorded_provenance_name: string | null;
  // ISO 3166-1 alpha-2 country recorded for that BBB; null or absent when none is recorded.
  recorded_country_code?: string | null;
  // Optional on creation responses and older detail responses.
  translations?: Record<string, { name: string; description: string }>;
  origin?: {
    source_type: string;
    event_date: string | null;
    description: string;
    origin_institution_name: string;
    latitude: string | null;
    longitude: string | null;
    technique: string;
  } | null;
};

export type SpeciesActionReadiness = {
  can_create_strain: boolean;
  strain_creation_issue: 'species_code_missing' | 'species_code_unusable' | 'species_code_inconsistent' | null;
};

export type InstitutionSpeciesDetail = InstitutionSpecies & SpeciesActionReadiness & { strains: SpeciesStrain[] };

// A coded geographical collection location of the active institution (BBB) and
// its recorded country (ISO 3166-1 alpha-2), null when none is recorded.
export type CollectionLocation = { id: number; code: string; name: string; country?: string | null };
export type CollectionLocationsResponse = { collection_locations: CollectionLocation[]; countries?: string[] };
export type StrainCreationResponse = { institution_species_id: number; strain: SpeciesStrain; strains: SpeciesStrain[] };

// Product-approved acquisition types of a new Strain, in form order.
export const ACQUISITION_TYPES = [
  'donation',
  'field_collection',
  'artificial_fertilization_wild',
  'artificial_fertilization_captive',
] as const;
export type AcquisitionType = typeof ACQUISITION_TYPES[number];

// Same three-letter grammar the server requires for AAA and BBB segments.
export const LOCATION_CODE_PATTERN = /^[A-Z]{3}$/;

export function normalizeSpeciesCode(value: string) {
  return value.replace(/\s+/g, '').toUpperCase().slice(0, 3);
}

export function isUsableSpeciesCode(value: string | null | undefined): value is string {
  return typeof value === 'string' && LOCATION_CODE_PATTERN.test(value);
}

export function normalizeLocationCode(value: string) {
  return normalizeSpeciesCode(value);
}

const collapse = (value: string) => value.trim().replace(/\s+/g, ' ');

// Localized country name from its ISO code; the code itself when the runtime has none.
export function countryName(code: string, language: string) {
  try {
    const names = typeof Intl.DisplayNames === 'function' ? new Intl.DisplayNames([language], { type: 'region' }) : null;
    const name = names?.of(code);
    return name && name !== code ? name : code;
  } catch {
    return code;
  }
}

export function sortedCountries(codes: readonly string[], language: string) {
  return codes
    .map((code) => ({ code, name: countryName(code, language) }))
    .sort((first, second) => first.name.localeCompare(second.name, language) || first.code.localeCompare(second.code));
}

// Country shown for a Strain: the recorded country, else the recorded location name.
export function strainCountryLabel(
  strain: Pick<SpeciesStrain, 'recorded_country_code' | 'recorded_provenance_name'>, language: string,
) {
  return strain.recorded_country_code ? countryName(strain.recorded_country_code, language) : strain.recorded_provenance_name;
}

// Decimal degrees: empty is absent, 0 is a real coordinate.
const COORDINATE = /^[+-]?(\d+(\.\d{1,6})?|\.\d{1,6})$/;
export type CoordinateProblem = 'invalid' | 'out_of_range' | 'required_pair';

export function coordinateProblems(latitude: string, longitude: string) {
  const problems: { latitude?: CoordinateProblem; longitude?: CoordinateProblem } = {};
  const check = (value: string, limit: number) => {
    const text = value.trim().replace(',', '.');
    if (!text) return null;
    if (!COORDINATE.test(text)) return 'invalid' as const;
    return Math.abs(Number(text)) > limit ? 'out_of_range' as const : null;
  };
  const lat = check(latitude, 90);
  const lon = check(longitude, 180);
  if (lat) problems.latitude = lat;
  if (lon) problems.longitude = lon;
  if (!lat && !lon && Boolean(latitude.trim()) !== Boolean(longitude.trim())) {
    problems[latitude.trim() ? 'longitude' : 'latitude'] = 'required_pair';
  }
  return problems;
}

export type StrainForm = {
  country: string;
  // Only for a country without a BBB here: either a new code or an explicit link.
  bbbCode: string;
  linkLocationId: number | null;
  acquisitionType: AcquisitionType | '';
  donor: string;
  latitude: string;
  longitude: string;
  name: string;
};

// Request body of POST .../strains/ (without its idempotency key). Optional
// values are omitted rather than sent empty; 0 coordinates are kept.
export function buildStrainPayload(form: StrainForm, { mapped, language }: { mapped: boolean; language: string }) {
  const payload: Record<string, string | number> = { country: form.country };
  if (!mapped) {
    if (form.linkLocationId !== null) payload.link_collection_location = form.linkLocationId;
    else {
      payload.bbb_code = normalizeLocationCode(form.bbbCode);
      payload.country_name = countryName(form.country, language);
    }
  }
  if (form.acquisitionType) payload.acquisition_type = form.acquisitionType;
  const donor = collapse(form.donor);
  if (form.acquisitionType === 'donation' && donor) payload.donor_institution = donor;
  if (form.latitude.trim() && form.longitude.trim()) {
    payload.latitude = form.latitude.trim().replace(',', '.');
    payload.longitude = form.longitude.trim().replace(',', '.');
  }
  const name = collapse(form.name);
  if (name) {
    payload.name = name;
    payload.name_language = language;
  }
  return payload;
}
export type HistoricalSpeciesDetail = Omit<HistoricalSpecies, 'requires_historical_adoption'> & SpeciesActionReadiness & {
  requires_historical_adoption: boolean;
  institution_species_id?: number | null;
  scientific_name?: string;
  worms_aphia_id?: number | null;
  worms_url?: string | null;
  strains: SpeciesStrain[];
};

export type SpeciesListEntry = {
  target: AdminSpeciesTarget;
  // Shared technical reference carried by Strains and Boxes.
  speciesId: number;
  scientificName: string;
  code: string | null;
  aphiaId: number | null;
  strainCount: number;
  isHistorical: boolean;
};

// The active institution's own match for a WoRMS result, never another's.
export type LocalSpeciesMatch = {
  institution_species_id: number | null;
  species_id: number;
  scientific_name: string;
  requires_historical_adoption: boolean;
};

export type WormsRecord = {
  aphia_id: number;
  scientific_name: string;
  authority: string;
  status: string;
  unaccept_reason: string;
  rank: string;
  rank_id: number | null;
  // Species or infraspecific rank; genus or higher (or unknown) is never a species reference.
  is_species_level: boolean;
  valid_aphia_id: number | null;
  valid_name: string;
  valid_authority: string;
  kingdom: string;
  phylum: string;
  class_name: string;
  order: string;
  family: string;
  url: string;
  local_species: LocalSpeciesMatch | null;
  valid_local_species: LocalSpeciesMatch | null;
};

// "truncated": WoRMS returned more records than the displayed "limit".
export type WormsSearchResponse = { query: string; results: WormsRecord[]; truncated: boolean; limit: number };

export const SPECIES_ADMIN_PATH = '/administration/species-strains';

export function adminSpeciesPath(target: AdminSpeciesTarget) {
  return target.kind === 'historical'
    ? `${SPECIES_ADMIN_PATH}/historical/${target.id}`
    : `${SPECIES_ADMIN_PATH}/${target.id}`;
}

export function adminSpeciesTargetFor(match: LocalSpeciesMatch): AdminSpeciesTarget {
  return match.institution_species_id !== null
    ? { kind: 'profile', id: match.institution_species_id }
    : { kind: 'historical', id: match.species_id };
}

export function sameSpeciesTarget(first: AdminSpeciesTarget | null, second: AdminSpeciesTarget | null) {
  return Boolean(first && second && first.kind === second.kind && first.id === second.id);
}

export function normalizeCatalogText(value: string) {
  return value.normalize('NFD').replace(/[\u0300-\u036f]/g, '').trim().toLocaleLowerCase('fr-FR');
}

// Profiles and historical references in one list, sorted by scientific name.
export function buildSpeciesEntries(list: InstitutionSpeciesList): SpeciesListEntry[] {
  const entries: SpeciesListEntry[] = [
    ...list.institution_species.map((item) => ({
      target: { kind: 'profile' as const, id: item.id },
      speciesId: item.species_id,
      scientificName: item.scientific_name,
      code: item.species_code,
      aphiaId: item.worms_aphia_id,
      strainCount: item.strain_count,
      isHistorical: false,
    })),
    ...list.historical_species.map((item) => ({
      target: { kind: 'historical' as const, id: item.species_id },
      speciesId: item.species_id,
      scientificName: item.shared_scientific_name,
      code: item.species_code,
      aphiaId: null,
      strainCount: item.strain_count,
      isHistorical: true,
    })),
  ];
  return entries.sort((first, second) => (
    first.scientificName.localeCompare(second.scientificName, 'fr', { sensitivity: 'base' })
    || first.target.kind.localeCompare(second.target.kind)
    || first.target.id - second.target.id
  ));
}

// Every term must match the scientific name, the code or the WoRMS AphiaID.
export function filterSpeciesEntries(entries: SpeciesListEntry[], query: string) {
  const terms = normalizeCatalogText(query).split(/\s+/).filter(Boolean);
  if (!terms.length) return entries;
  return entries.filter((entry) => {
    const text = normalizeCatalogText([entry.scientificName, entry.code ?? '', entry.aphiaId ?? ''].join(' '));
    return terms.every((term) => text.includes(term));
  });
}

export function isWormsAccepted(record: WormsRecord) {
  return record.status === 'accepted';
}

// Only a species-level record may be attached to a Species as its WoRMS reference.
export function canReferenceWormsRecord(record: WormsRecord) {
  return record.is_species_level === true;
}

export function hasDistinctAcceptedName(record: WormsRecord) {
  return Boolean(record.valid_aphia_id && record.valid_name && record.valid_aphia_id !== record.aphia_id);
}

// The accepted record proposed from a synonym, chosen only explicitly.
export function acceptedRecordFrom(record: WormsRecord): WormsRecord {
  return {
    ...record,
    aphia_id: record.valid_aphia_id ?? record.aphia_id,
    scientific_name: record.valid_name,
    authority: record.valid_authority,
    status: 'accepted',
    unaccept_reason: '',
    url: `https://www.marinespecies.org/aphia.php?p=taxdetails&id=${record.valid_aphia_id}`,
    local_species: record.valid_local_species,
    valid_local_species: null,
  };
}

export type WormsQueryProblem = 'unsupported_characters' | 'too_short' | 'too_long';

// Why a query breaks the server's input rule; characters first, so "Aurelia sp. 2"
// is reported for its digit, never as too short.
export function getWormsQueryProblem(value: string): WormsQueryProblem | null {
  const query = value.split(/\s+/).filter(Boolean).join(' ');
  if (!/^[A-Za-z][A-Za-z .()'×-]*$/.test(query)) return 'unsupported_characters';
  if (query.length < 3) return 'too_short';
  if (query.length > 120) return 'too_long';
  return null;
}

// Mirrors the server's input rule so obviously invalid queries are not sent.
export function normalizeWormsQuery(value: string) {
  return getWormsQueryProblem(value) ? null : value.split(/\s+/).filter(Boolean).join(' ');
}

export function normalizeScientificName(value: string) {
  return value.split(/\s+/).filter(Boolean).join(' ');
}

// A WoRMS reference attached to the form: a record chosen in this dialog, or
// the profile's saved AphiaID (whose record was not reloaded).
export type FormReference =
  | { kind: 'record'; record: WormsRecord }
  | { kind: 'saved'; aphiaId: number; url: string | null; name: string };

export function referenceAphiaId(reference: FormReference | null) {
  if (!reference) return null;
  return reference.kind === 'record' ? reference.record.aphia_id : reference.aphiaId;
}

// A reference stays compatible only while the field shows its exact name.
// An incompatible one is never dropped silently: it must be removed or replaced.
export function isReferenceCompatible(reference: FormReference | null, scientificName: string) {
  if (!reference) return true;
  const name = reference.kind === 'record' ? reference.record.scientific_name : reference.name;
  return normalizeScientificName(scientificName) === name;
}

// Registration: the scientific name and an optional verified AphiaID only;
// the institution code is generated by the server.
export function buildRegistrationPayload(scientificName: string, aphiaId: number | null) {
  return {
    scientific_name: normalizeScientificName(scientificName),
    ...(aphiaId !== null ? { worms_aphia_id: aphiaId } : {}),
  };
}

// Editing: the explicit final state, guarded by the revision that was read.
export function buildEditPayload(expectedRevision: number, scientificName: string, aphiaId: number | null) {
  return {
    expected_revision: expectedRevision,
    scientific_name: normalizeScientificName(scientificName),
    worms_aphia_id: aphiaId,
  };
}

// Editing an existing Species without institutional information yet: the explicit
// final state only. The server associates it on this save, never on opening.
export function buildExistingSpeciesEditPayload(scientificName: string, aphiaId: number | null) {
  return {
    scientific_name: normalizeScientificName(scientificName),
    worms_aphia_id: aphiaId,
  };
}

export type ServiceFailure = {
  code: string;
  institution_species_id?: number | null;
  species_id?: number;
  scientific_name?: string;
  shared_scientific_name?: string;
  species_code?: string | null;
  current_revision?: number;
};

// Stable server code of a refused registration or edit, with its own-institution details.
export function getServiceFailure(data: unknown): ServiceFailure | null {
  if (!data || typeof data !== 'object') return null;
  const code = (data as { code?: unknown }).code;
  return typeof code === 'string' ? data as ServiceFailure : null;
}

export function isWormsReferenceFailure(code: string | undefined) {
  return code === 'worms_reference_not_found' || code === 'worms_reference_rank'
    || code === 'worms_reference_name_mismatch';
}

// "Espèce enregistrée. Code attribué : {code}" -> text and placeholder segments.
export function splitTemplate(template: string) {
  return template.split(/(\{[a-z]+\})/).filter(Boolean).map((part) => {
    const match = /^\{([a-z]+)\}$/.exec(part);
    return match ? { placeholder: match[1] } : { text: part };
  });
}

// A Box of the complete institutional collection, reduced to what grouping needs.
type GroupableBox = { organization: { id: number }; strain: { id: number; species: { id: number } } };

// Box totals per shared Species reference, for the active institution only.
// Only meaningful on the complete Box collection, never on a partial cache.
export function countBoxesBySpecies(boxes: readonly GroupableBox[], organizationId: number) {
  const counts = new Map<number, number>();
  for (const box of boxes) {
    if (box.organization.id !== organizationId) continue;
    counts.set(box.strain.species.id, (counts.get(box.strain.species.id) ?? 0) + 1);
  }
  return counts;
}

export type SpeciesBoxSummary = {
  boxCount: number;
  activeBoxCount: number;
  firstMeasurementOn: string | null;
  lastMeasurementOn: string | null;
};

// Reduce authoritative per-Box MIN/MAX dates only after the complete collection
// is ready. Inactive Boxes retain their scientific history in these bounds.
export function summarizeBoxesBySpecies(
  boxes: readonly (GroupableBox & {
    status: string;
    first_measurement_on?: string | null;
    last_measurement_on?: string | null;
  })[],
  organizationId: number,
) {
  const summaries = new Map<number, SpeciesBoxSummary>();
  for (const box of boxes) {
    if (box.organization.id !== organizationId) continue;
    const speciesId = box.strain.species.id;
    const summary = summaries.get(speciesId) ?? {
      boxCount: 0, activeBoxCount: 0, firstMeasurementOn: null, lastMeasurementOn: null,
    };
    summary.boxCount += 1;
    if (box.status === 'active') summary.activeBoxCount += 1;
    const first = box.first_measurement_on;
    const last = box.last_measurement_on;
    if (first && (!summary.firstMeasurementOn || first < summary.firstMeasurementOn)) summary.firstMeasurementOn = first;
    if (last && (!summary.lastMeasurementOn || last > summary.lastMeasurementOn)) summary.lastMeasurementOn = last;
    summaries.set(speciesId, summary);
  }
  return summaries;
}

// Boxes of the listed Strains, grouped by Strain and kept in collection order.
export function groupBoxesByStrain<T extends GroupableBox>(
  boxes: readonly T[], strainIds: readonly number[], organizationId: number,
) {
  const groups = new Map<number, T[]>(strainIds.map((id) => [id, []]));
  for (const box of boxes) {
    if (box.organization.id !== organizationId) continue;
    groups.get(box.strain.id)?.push(box);
  }
  return groups;
}

// The single recorded collection location shared by every Strain, or null when
// there is no Strain, one has none recorded, or they differ. Never inferred.
export function commonRecordedProvenance(strains: readonly Pick<SpeciesStrain, 'recorded_provenance_name'>[]) {
  const first = strains[0]?.recorded_provenance_name ?? null;
  if (first === null) return null;
  return strains.every((strain) => strain.recorded_provenance_name === first) ? first : null;
}

// Only the authoritative X participates in numeric ordering, never code segments.
// Decimal strings are compared by length to avoid precision loss for large X.
function sortableStrainNumber(value: SpeciesStrain['strain_number']) {
  if (typeof value === 'number' && (!Number.isSafeInteger(value) || value < 0)) return null;
  const text = String(value);
  return /^\d+$/.test(text) ? text.replace(/^0+(?=\d)/, '') : null;
}

export function sortSpeciesStrains<T extends Pick<SpeciesStrain, 'id' | 'code' | 'strain_number'>>(strains: readonly T[]): T[] {
  return [...strains].sort((first, second) => {
    const a = sortableStrainNumber(first.strain_number);
    const b = sortableStrainNumber(second.strain_number);
    if (a !== null && b !== null) {
      const order = a.length - b.length || (a < b ? -1 : a > b ? 1 : 0);
      if (order) return order;
    } else if (a !== null || b !== null) {
      return a !== null ? -1 : 1;
    }
    // Equal X and malformed/missing X use exact code then ID, independent of locale.
    return (first.code < second.code ? -1 : first.code > second.code ? 1 : 0) || first.id - second.id;
  });
}

export function recordedStrainName(strain: Pick<SpeciesStrain, 'name' | 'translations'>, language: string) {
  return strain.translations?.[language]?.name?.trim() || strain.name.trim();
}

// Descriptive name first, then the server's Strain number; the official code
// is always shown separately and is the title only when nothing else exists.
export function strainTitle(strain: Pick<SpeciesStrain, 'name' | 'strain_number' | 'code'>, strainLabel: string) {
  if (strain.name) return strain.name;
  return strain.strain_number !== null ? `${strainLabel} ${strain.strain_number}` : strain.code;
}
