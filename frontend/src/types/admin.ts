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
  notes: string;
};

export type BoxTransferResult = BoxTransferPayload & {
  id: number;
  transfer_date: string;
};
