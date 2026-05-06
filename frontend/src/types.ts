export type PaginatedResponse<T> = {
  count: number;
  next: string | null;
  previous: string | null;
  results: T[];
};

export type Organization = {
  id: number;
  name: string;
  slug: string | null;
  city: string;
  country: string;
};

export type Species = {
  id: number;
  scientific_name: string;
  common_name: string;
  genus_species_code: string;
};

export type Strain = {
  id: number;
  code: string;
  number: number | null;
  origin_code: string;
  species: Species;
};

export type ThermalZoneSummary = {
  id: number;
  name: string;
  zone_type: string;
  target_temperature_c: string | null;
  is_active: boolean;
};

export type BiologicalMeasurement = {
  id: number;
  measured_on: string;
  polyp_count: number;
  ephyrae_count: number;
  culture_status: string;
  needs_attention: boolean;
  notes: string;
  user: string | null;
  created_at: string;
};

export type BoxItem = {
  id: number;
  global_code: string;
  local_code: string;
  box_number: string;
  status: string;
  organization: Organization;
  species: Species;
  strain: Strain;
  thermal_zone: ThermalZoneSummary | null;
  entered_on: string | null;
  latest_measurement: BiologicalMeasurement | null;
  active_alert_count: number;
};

export type Probe = {
  id: number;
  code: string;
  probe_type: string;
  location: string;
  is_active: boolean;
};

export type ThermalZone = {
  id: number;
  name: string;
  zone_type: string;
  organization: Organization;
  target_temperature_c: string | null;
  is_active: boolean;
  box_count: number;
  latest_temperature: {
    date: string;
    average_temperature_c: number;
    min_temperature_c: number | null;
    max_temperature_c: number | null;
    measurement_count: number;
  } | null;
  latest_salinity: {
    measured_on: string;
    salinity_psu: number;
  } | null;
  probes: Probe[];
};

export type Dashboard = {
  stats: {
    boxes_total: number;
    active_boxes: number;
    species_count: number;
    thermal_zones: number;
    active_alerts: number;
    measured_polyps: number;
    measured_ephyrae: number;
  };
  latest_entries: BiologicalMeasurement[];
  latest_scans: Array<{
    id: number;
    object_id: string;
    description: string;
    created_at: string;
    user: string | null;
  }>;
};

export type UserProfile = {
  id: number;
  username: string;
  email: string;
  first_name: string;
  last_name: string;
  interface_language: string;
  organizations: Organization[];
  available_languages: Array<{
    code: string;
    label: string;
  }>;
};
