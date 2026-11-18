import { useId, useState } from 'react';
import { FilterX, Search } from 'lucide-react';
import { ApiError } from '../../api/client';
import { getDonation, listDonations, type Donation, type DonationDirection, type DonationState } from '../../api/boxDonations';
import useDonationRead from '../../hooks/useDonationRead';
import useMutationDialog from '../../hooks/useMutationDialog';
import type { Language, Translator } from '../../i18n';
import { formatDisplayDate } from '../../utils/dateFormat';
import BoxTrackingPreview from '../BoxTrackingPreview';
import ModalPortal from '../ModalPortal';
import PolypbaseIcon from '../PolypbaseIcon';
import { RowActionMenu } from '../RowActionMenu';
import SkeletonRows from '../SkeletonRows';
import ExchangeFormPreview from './ExchangeFormPreview';

type Props = { organizationId: number; language: Language; onOpenBox: (id: number, code: string) => void; t: Translator };
const stateLabel = (state: DonationState) => state === 'pending' ? 'donationPending' : 'donationCancelled';

export function filterDonations(rows: readonly Donation[], direction: DonationDirection, query: string, type: string, state: string) {
  const normalized = query.trim().normalize('NFD').replace(/\p{M}/gu, '').toLocaleLowerCase();
  return rows.filter((row) => (!type || type === 'donation') && (!state || row.state === state) && [
    row.box.global_code, row.box.species_scientific_name,
    direction === 'outgoing' ? row.destination_institution.name : row.source_institution.name,
  ].some((value) => value.normalize('NFD').replace(/\p{M}/gu, '').toLocaleLowerCase().includes(normalized)));
}

export default function InstitutionalExchangesPage(props: Props) {
  return <>
    <DonationSection {...props} direction="outgoing" />
    <DonationSection {...props} direction="incoming" />
  </>;
}

function ReadError({ error, retry, t }: { error: unknown; retry: () => void; t: Translator }) {
  const key = error instanceof ApiError && error.status === 403 ? 'donationForbidden'
    : error instanceof ApiError && error.status === 404 ? 'donationNotFound' : 'donationReadError';
  return <div className="box-inventory-empty" role="alert">
    <p>{t(key)}</p>
    <button className="secondary-button" type="button" onClick={retry}>{t('lineageRetry')}</button>
  </div>;
}

export function DonationSection({ organizationId, language, onOpenBox, t, direction }: Props & { direction: DonationDirection }) {
  const { state, refresh } = useDonationRead(organizationId, direction, (signal) => listDonations(direction, organizationId, signal));
  const [query, setQuery] = useState('');
  const [type, setType] = useState('');
  const [status, setStatus] = useState('');
  const [detailId, setDetailId] = useState<string | null>(null);
  const [previewOpen, setPreviewOpen] = useState(false);
  const outgoing = direction === 'outgoing';
  const counterpart = t(outgoing ? 'donationDestination' : 'donationSource');
  const rows = state.status === 'ready' ? filterDonations(state.data.results, direction, query, type, status) : [];
  const filtered = Boolean(query.trim() || type || status);

  return <section className="admin-section box-inventory-section exchange-section" id={outgoing ? 'admin-transfers' : 'admin-receipts'}>
    <header className="admin-section-heading section-title exchange-heading">
      <h2>{t(outgoing ? 'shareOutgoing' : 'donationIncoming')}</h2>
      {outgoing ? <button className="admin-primary-action" type="button" onClick={() => setPreviewOpen(true)}>
        <PolypbaseIcon name="plus" size={18} />{t('donationSend')}
      </button> : null}
    </header>

    <div className="box-inventory-filters exchange-filters">
      <label className="box-inventory-search">
        <span>{t('boxInventorySearch')}</span>
        <span className="box-inventory-search-control"><Search size={17} aria-hidden="true" />
          <input type="search" value={query} placeholder={t('donationSearch')} onChange={(event) => setQuery(event.target.value)} />
        </span>
      </label>
      <label><span>{t('donationType')}</span><select value={type} onChange={(event) => setType(event.target.value)}>
        <option value="">{t('donationAllTypes')}</option><option value="donation">{t('donationTypeDon')}</option>
      </select></label>
      <label><span>{t('donationState')}</span><select value={status} onChange={(event) => setStatus(event.target.value)}>
        <option value="">{t('donationAllStates')}</option>
        <option value="pending">{t('donationPending')}</option><option value="cancelled">{t('donationCancelled')}</option>
      </select></label>
      <div className="box-inventory-reset-slot">{filtered ? <button type="button" title={t('overviewResetFilters')} aria-label={t('overviewResetFilters')} onClick={() => { setQuery(''); setType(''); setStatus(''); }}><FilterX size={17} aria-hidden="true" /></button> : null}</div>
    </div>
    <div className="box-inventory-list exchange-list" aria-busy={state.status === 'loading'}>
      <div className="box-inventory-table-head" aria-hidden="true">
        <span>{t('boxInventoryBox')}</span><span>{counterpart}</span><span>{t('donationType')}</span><span>{t('donationDate')}</span><span>{t('donationState')}</span><span />
      </div>
      {state.status === 'loading' ? <div role="status" aria-label={t('loading')}><SkeletonRows count={3} /></div>
        : state.status === 'error' ? <ReadError error={state.error} retry={refresh} t={t} />
          : rows.length ? rows.map((row) => <article className="box-inventory-row exchange-row" key={row.proposal_id} aria-label={`${row.box.global_code}, ${t(stateLabel(row.state))}`}>
            <div className="box-inventory-cell box-inventory-identity" data-label={t('boxInventoryBox')}>
              {outgoing && 'id' in row.box ? <BoxTrackingPreview boxId={row.box.id} code={row.box.global_code} speciesName={row.box.species_scientific_name} language={language} onOpenBox={onOpenBox} t={t} /> : <strong className="exchange-box-code">{row.box.global_code}</strong>}
              <span className="box-inventory-species">{row.box.species_scientific_name}</span>
            </div>
            <div className="box-inventory-cell exchange-institution" data-label={counterpart}><bdi>{outgoing ? row.destination_institution.name : row.source_institution.name}</bdi></div>
            <div className="box-inventory-cell exchange-type" data-label={t('donationType')}>{t('donationTypeDon')}</div>
            <div className="box-inventory-cell exchange-date" data-label={t('donationDate')}>{formatDisplayDate(row.created_at)}</div>
            <div className="box-inventory-cell" data-label={t('donationState')}><span className="exchange-status">{t(stateLabel(row.state))}</span></div>
            <div className="box-inventory-cell box-inventory-actions"><RowActionMenu ariaLabel={t('auditRowActions').replace('{target}', row.box.global_code)} actions={[{ action: 'details', label: t('donationDetails') }]} onAction={() => setDetailId(row.proposal_id)} /></div>
          </article>) : <div className="box-inventory-empty"><strong>{t(filtered ? 'donationNoMatch' : 'donationEmpty')}</strong></div>}
    </div>
    {previewOpen ? <ExchangeFormPreview onClose={() => setPreviewOpen(false)} t={t} /> : null}
    {detailId ? <DonationDetailDialog key={detailId} proposalId={detailId} organizationId={organizationId} onClose={() => setDetailId(null)} t={t} /> : null}
  </section>;
}

export function DonationDetailDialog({ proposalId, organizationId, onClose, t }: { proposalId: string; organizationId: number; onClose: () => void; t: Translator }) {
  const { state, refresh } = useDonationRead(organizationId, proposalId, (signal) => getDonation(proposalId, organizationId, signal));
  const titleId = useId();
  const { dialogRef, close } = useMutationDialog<HTMLElement>(false, onClose);
  const row = state.status === 'ready' ? state.data : null;
  return <ModalPortal><div className="modal-backdrop" role="presentation" onMouseDown={close}>
    <section ref={dialogRef} tabIndex={-1} className="lab-location-modal exchange-modal" role="dialog" aria-modal="true" aria-labelledby={titleId} onMouseDown={(event) => event.stopPropagation()}>
      <header className="lab-location-modal-heading"><h2 id={titleId}>{t('donationDetails')}</h2><button className="modal-close-button" type="button" aria-label={t('close')} onClick={close}><PolypbaseIcon name="close" size={18} /></button></header>
      {state.status === 'loading' ? <div role="status" aria-label={t('loading')}><SkeletonRows count={3} /></div> : state.status === 'error' ? <ReadError error={state.error} retry={refresh} t={t} /> : null}
      {row ? <>
        <dl className="exchange-facts">
          <div><dt>{t('donationReference')}</dt><dd>{row.proposal_id}</dd></div>
          <div><dt>{t('boxInventoryBox')}</dt><dd><strong>{row.box.global_code}</strong> <em>{row.box.species_scientific_name}</em></dd></div>
          <div><dt>{t('donationSource')}</dt><dd><bdi>{row.source_institution.name}</bdi></dd></div>
          <div><dt>{t('donationDestination')}</dt><dd><bdi>{row.destination_institution.name}</bdi></dd></div>
          <div><dt>{t('donationType')}</dt><dd>{t('donationTypeDon')}</dd></div>
          <div><dt>{t('donationDate')}</dt><dd>{formatDisplayDate(row.created_at)}</dd></div>
          <div><dt>{t('donationState')}</dt><dd>{t(stateLabel(row.state))}</dd></div>
          {row.cancelled_at !== null ? <div><dt>{t('donationCancelledAt')}</dt><dd>{formatDisplayDate(row.cancelled_at)}</dd></div> : null}
        </dl>
        <p className="muted compact-text">{t('donationAcceptanceUnavailable')}</p>
      </> : null}
      <footer className="lab-location-form-actions"><button className="secondary-button" type="button" onClick={close}>{t('close')}</button></footer>
    </section>
  </div></ModalPortal>;
}
