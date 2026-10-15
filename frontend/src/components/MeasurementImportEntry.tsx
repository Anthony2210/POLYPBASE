import { useState } from 'react';
import type { Translator } from '../i18n';
import type { UserProfile } from '../types';
import { getMembershipRole } from '../utils/profilePermissions';
import MeasurementImportPanel from './MeasurementImportPanel';

export default function MeasurementImportEntry({ profile, organizationId, isOperationCurrent, t }: {
  profile: UserProfile | null;
  organizationId: number | null;
  isOperationCurrent: () => boolean;
  t: Translator;
}) {
  const [open, setOpen] = useState(false);
  const role = getMembershipRole(profile, organizationId);
  // This entry intentionally has no superuser bypass; the server remains authoritative.
  if (organizationId === null || (role !== 'admin' && role !== 'lab_technician')) return null;
  return <div className="measurement-csv-entry">
    <button type="button" className="secondary-button" aria-expanded={open} aria-controls="measurement-csv-panel"
      onClick={() => setOpen(!open)}>{t(open ? 'csvClose' : 'csvOpen')}</button>
    {open && <div id="measurement-csv-panel">
      <MeasurementImportPanel key={organizationId} organizationId={organizationId} isOperationCurrent={isOperationCurrent} t={t} />
    </div>}
  </div>;
}
