export type ThermalZonePayload = {
  organization: number;
  name: string;
  zone_type: string;
  target_temperature_c: string | null;
};

export type ProbePayload = {
  thermal_zone: number;
  code: string;
  probe_type: string;
  location: string;
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
