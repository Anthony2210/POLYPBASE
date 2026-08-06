export type ThermalZonePayload = {
  organization: number;
  name: string;
  zone_type: string;
  target_temperature_c: string | null;
  capacity: number | null;
  salinity_psu: string | null;
};

export type ProbePayload = {
  thermal_zone: number;
  code: string;
  probe_type: string;
  location: string;
};

export type ManualTemperaturePayload = {
  measured_on: string;
  temperature_c: string;
};

export type OrganizationPayload = {
  name: string;
  city: string;
  country: string;
  contact_email: string;
  notes: string;
};

export type BoxTransferPayload = {
  box: number;
  to_organization: number;
  polyp_count: number;
  notes: string;
};

export type BoxTransferResult = BoxTransferPayload & {
  id: number;
  transfer_date: string;
  prepared_by: string | null;
  parent_box_codes: string[];
  origin: {
    source_type: string;
    institution: string;
    description: string;
  } | null;
};

export type ReferenceLanguage = {
  code: string;
  label: string;
  required: boolean;
};

export type LocalizedReferenceValue = {
  name: string;
  description: string;
};

export type LocalizedReferenceValues = Record<string, LocalizedReferenceValue>;

export type SpeciesReference = {
  id: number;
  scientific_name: string;
  genus_species_code: string;
  worms_aphia_id: number | null;
  is_described: boolean;
  notes: string;
  translations: LocalizedReferenceValues;
  strain_count: number;
};

export type StrainReference = {
  id: number;
  species: number;
  species_scientific_name: string;
  code: string;
  number: number | null;
  origin_code: string;
  notes: string;
  translations: LocalizedReferenceValues;
};

export type TaxonomyReferences = {
  languages: ReferenceLanguage[];
  species: SpeciesReference[];
  strains: StrainReference[];
};

export type SpeciesReferencePayload = {
  scientific_name: string;
  genus_species_code: string;
  worms_aphia_id: number | null;
  is_described: boolean;
  notes: string;
  translations: LocalizedReferenceValues;
};

export type StrainReferencePayload = {
  species: number;
  code: string;
  number: number | null;
  origin_code: string;
  notes: string;
  translations: LocalizedReferenceValues;
};
