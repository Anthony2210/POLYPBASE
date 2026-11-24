export type ThermalZonePayload = {
  organization: number;
  name: string;
  // Omitted by Laboratory configuration: the server keeps its model default.
  zone_type?: string;
  target_temperature_c: string | null;
  capacity: number | null;
  salinity_psu: string | null;
};

/** Partial update: only the fields sent are changed; the institution never is. */
export type ThermalZoneUpdatePayload = Partial<Omit<ThermalZonePayload, 'organization'>>;

/** Why a location cannot be deleted: it holds boxes, or it was used or is referenced. */
export type ThermalZoneDeletionReason = 'occupied' | 'history';

/** Informative eligibility read by the delete dialog; the DELETE request re-checks it. */
export type ThermalZoneDeletionEligibility = {
  thermal_zone_id: number;
  deletable: boolean;
  reasons: ThermalZoneDeletionReason[];
  box_count: number;
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

export type ManualSalinityPayload = {
  measured_on: string;
  salinity_psu: string;
  notes?: string;
};

export type ManualSalinityUpdatePayload = {
  salinity_psu: string;
  notes?: string;
};
