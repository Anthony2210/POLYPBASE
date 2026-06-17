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
  strobila_count: number;
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

export type BoxDetail = BoxItem & {
  created_on: string;
  volume_liters: string | null;
  stop_reason: string;
  notes: string;
  lineage: BoxLineage;
  locations: BoxLocation[];
  movements: BoxMovement[];
  biological_measurements: BiologicalMeasurement[];
  scan_url: string;
  qr_image_url: string;
};

export type BoxLocation = {
  id: number;
  thermal_zone: ThermalZoneSummary;
  starts_at: string;
  ends_at: string | null;
  notes: string;
};

export type BoxMovement = {
  id: number;
  from_thermal_zone: ThermalZoneSummary | null;
  to_thermal_zone: ThermalZoneSummary;
  moved_at: string;
  notes: string;
  user: string | null;
};

export type LineageEvent = {
  id: number;
  event_date: string;
  reason: string;
  notes: string;
  user: string | null;
};

export type LineageRelation = {
  id: number;
  relationship_type: string;
  box: {
    id: number;
    global_code: string;
    local_code: string;
    status: string;
    species_name: string;
    thermal_zone_name: string | null;
  };
  event: LineageEvent | null;
};

export type BoxLineage = {
  parents: LineageRelation[];
  children: LineageRelation[];
};

export type LineageGraphNode = {
  id: number;
  global_code: string;
  local_code: string;
  status: string;
  species_name: string;
  thermal_zone_name: string | null;
  organization_name: string;
  is_root: boolean;
};

export type LineageGraphEdge = {
  id: number;
  source: number;
  target: number;
  relationship_type: string;
  event: LineageEvent | null;
};

export type LineageGraph = {
  root_box_id: number;
  nodes: LineageGraphNode[];
  edges: LineageGraphEdge[];
  truncated: boolean;
  max_nodes: number;
};

export type SubcultureChildPayload = {
  global_code: string;
  local_code: string;
  box_number: string;
  thermal_zone_id: number;
  copy_origin: boolean;
  copy_volume_liters: boolean;
  notes: string;
};

export type SubculturePayload = {
  event_date: string;
  reason: string;
  notes: string;
  children: SubcultureChildPayload[];
};

export type SubcultureResult = {
  id: number;
  parent_box: string;
  event_date: string;
  reason: string;
  notes: string;
  user: string | null;
  children: BoxItem[];
};

export type BoxMovePayload = {
  thermal_zone_id: number;
  moved_at: string;
  notes: string;
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
  is_superuser: boolean;
  interface_language: string;
  organizations: Organization[];
  memberships: Array<{
    organization: {
      id: number;
      name: string;
      slug: string | null;
    };
    role: 'admin' | 'lab_technician' | 'viewer';
    role_label: string;
  }>;
  available_languages: Array<{
    code: string;
    label: string;
  }>;
};

export type ExportOptions = {
  organizations: Array<{
    id: number;
    name: string;
  }>;
  species: Array<{
    id: number;
    name: string;
  }>;
  strains: Array<{
    id: number;
    code: string;
    species_id: number;
    species_name: string;
  }>;
  boxes: Array<{
    id: number;
    global_code: string;
    local_code: string;
    species_id: number;
    strain_id: number;
    thermal_zone_id: number | null;
    organization_id: number;
  }>;
  zones: Array<{
    id: number;
    name: string;
    organization_id: number;
  }>;
};
