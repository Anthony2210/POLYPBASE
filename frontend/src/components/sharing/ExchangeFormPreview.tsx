import { useEffect, useId, useRef, useState } from 'react';
import { ApiError, getOrganizationResourceSignal, getStoredActiveOrganizationId } from '../../api/client';
import {
  DONATION_BOX_PAGE_SIZE, listDonationDestinations, listDonationSourceBoxes, preflightDonation, sendDonation,
  type DonationDestination, type DonationSubmission,
} from '../../api/boxDonations';
import type { Language, Translator } from '../../i18n';
import useDonationRead from '../../hooks/useDonationRead';
import useMutationDialog from '../../hooks/useMutationDialog';
import ModalPortal from '../ModalPortal';
import PolypbaseIcon from '../PolypbaseIcon';

export function groupDestinations(rows: DonationDestination[], language: Language, unspecified: string) {
  const sort = new Intl.Collator(language);
  const groups = new Map<string, DonationDestination[]>();
  for (const row of rows) {
    const country = row.country.trim();
    const members = groups.get(country) ?? [];
    members.push(row);
    groups.set(country, members);
  }
  return [...groups].sort(([a], [b]) => sort.compare(a || unspecified, b || unspecified))
    .map(([country, institutions]) => ({
      country, label: country || unspecified,
      institutions: institutions.sort((a, b) => sort.compare(a.name, b.name) || a.institution_id.localeCompare(b.institution_id)),
    }));
}

function ChoiceStatus({ status, error, empty, retry, t }: {
  status: 'loading' | 'error' | 'ready'; error?: unknown; empty: string | null; retry: () => void; t: Translator;
}) {
  return <>
    <div role="status">{status === 'loading' ? t('loading') : empty}</div>
    {status === 'error' ? <div>
      <p role="alert">{t(error instanceof ApiError && error.status === 403 ? 'donationForbidden' : 'donationChoicesError')}</p>
      <button type="button" className="secondary-button" onClick={retry}>{t('lineageRetry')}</button>
    </div> : null}
  </>;
}

function donationErrorKey(error: unknown, phase: 'preflight' | 'send') {
  if (error instanceof ApiError) {
    if (error.status === 403) return 'donationForbidden';
    const code = (error.data as { code?: string } | null)?.code;
    if (code === 'source_history_changed') return 'donationHistoryChanged';
    if (code === 'donation_already_pending') return 'donationAlreadyPending';
    if (code === 'destination_not_found') return 'donationDestinationUnavailable';
    if (code?.startsWith('box_') || code === 'strain_global_identity_required') return 'donationBoxIneligible';
  }
  return phase === 'preflight' ? 'donationPreflightError' : 'donationSendError';
}

export default function ExchangeFormPreview({ organizationId, language, onClose, onSent, t }: {
  organizationId: number; language: Language; onClose: (needsRefresh?: boolean) => void; onSent: () => void; t: Translator;
}) {
  const id = useId();
  const [search, setSearch] = useState('');
  const [query, setQuery] = useState('');
  const [offset, setOffset] = useState(0);
  const [boxId, setBoxId] = useState('');
  const [destinationId, setDestinationId] = useState('');
  const [validatedHash, setValidatedHash] = useState<string | null>(null);
  const [confirmed, setConfirmed] = useState(false);
  const [retry, setRetry] = useState<DonationSubmission | null>(null);
  const [error, setError] = useState<ReturnType<typeof donationErrorKey> | 'donationSendUncertain' | null>(null);
  const lifetime = useRef<AbortController | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    lifetime.current = controller;
    return () => controller.abort();
  }, []);
  const { dialogRef, initialFocusRef, close, isBusy, submit } = useMutationDialog<HTMLSelectElement>(false, () => onClose(retry !== null));
  const destinations = useDonationRead(organizationId, 'destinations', (signal) => listDonationDestinations(organizationId, signal));
  const sources = useDonationRead(organizationId, JSON.stringify(['sources', query, offset]), (signal) => listDonationSourceBoxes(organizationId, query, offset, signal));
  const directory = destinations.state.status === 'ready' ? destinations.state.data.results : [];
  const page = sources.state.status === 'ready' ? sources.state.data : null;
  const box = page?.results.find((item) => String(item.id) === boxId);
  const destination = directory.find((item) => item.institution_id === destinationId);
  const groups = groupDestinations(directory, language, t('donationCountryUnspecified'));
  const locked = isBusy || retry !== null;

  function resetValidation() {
    setValidatedHash(null);
    setConfirmed(false);
    setError(null);
  }

  function changePage(nextOffset: number) {
    setOffset(nextOffset);
    setBoxId('');
    resetValidation();
  }

  function runSearch() {
    setQuery(search.trim());
    changePage(0);
  }

  function run() {
    void submit(async () => {
      const signal = lifetime.current?.signal;
      const context = getOrganizationResourceSignal();
      const current = () => signal && !signal.aborted && !context.aborted && getStoredActiveOrganizationId() === organizationId;
      if (!signal || !current() || (!retry && (!box || !destination))) return;
      setError(null);
      const selection = { source_box_id: Number(boxId), destination_institution_id: destinationId };
      if (!validatedHash && !retry) {
        try {
          const result = await preflightDonation(selection, organizationId, signal);
          if (current()) setValidatedHash(result.history.sha256);
        } catch (failure) {
          if (current()) setError(donationErrorKey(failure, 'preflight'));
        }
        return;
      }
      if (!confirmed || !validatedHash) return;
      try {
        const payload: DonationSubmission = retry ?? {
          ...selection, request_id: crypto.randomUUID(), history_verified: true, history_sha256: validatedHash,
        };
        // A transport failure can occur after commit. Replay the exact body without another preflight.
        setRetry(payload);
        await sendDonation(payload, organizationId, signal);
      } catch (failure) {
        if (!current()) return;
        if (failure instanceof ApiError && failure.status >= 400 && failure.status < 500 && ![408, 429].includes(failure.status)) {
          setRetry(null);
          resetValidation();
          if (donationErrorKey(failure, 'send') === 'donationDestinationUnavailable') {
            setDestinationId('');
            destinations.refresh();
          }
          setError(donationErrorKey(failure, 'send'));
        } else {
          setError('donationSendUncertain');
        }
        return;
      }
      if (current()) onSent();
    });
  }

  return <ModalPortal>
    <div className="modal-backdrop" role="presentation" onMouseDown={close}>
      <section ref={dialogRef} tabIndex={-1} className="lab-location-modal" role="dialog" aria-modal="true" aria-labelledby={`${id}-title`} onMouseDown={(event) => event.stopPropagation()}>
        <header className="lab-location-modal-heading">
          <h2 id={`${id}-title`}>{t('exchangePreviewTitle')}</h2>
          <button className="modal-close-button" type="button" aria-label={t('close')} title={t('close')} disabled={isBusy} onClick={close}><PolypbaseIcon name="close" size={18} /></button>
        </header>
        <form className="lab-location-form" onSubmit={(event) => { event.preventDefault(); run(); }}>
          <div className="lab-location-fields">
            <div className="lab-location-field lab-location-field-wide">
              <label htmlFor={`${id}-type`}>{t('exchangePreviewType')}</label>
              <select ref={initialFocusRef} id={`${id}-type`} defaultValue="donation" disabled={isBusy} aria-describedby={`${id}-availability`}>
                <option value="donation">{t('donationTypeDon')}</option>
                <option value="live" disabled>{t('exchangePreviewLive')}</option>
                <option value="frozen" disabled>{t('exchangePreviewFrozen')}</option>
                <option value="email_frozen" disabled>{t('exchangePreviewEmail')}</option>
              </select>
              <small id={`${id}-availability`}>{t('donationOnlyExchange')}</small>
            </div>
            <div className="lab-location-field lab-location-field-wide">
              <label htmlFor={`${id}-search`}>{t('shareFindBox')}</label>
              <input id={`${id}-search`} type="search" value={search} disabled={locked} placeholder={t('shareBoxSearchHint')}
                onChange={(event) => setSearch(event.target.value)} onKeyDown={(event) => { if (event.key === 'Enter') { event.preventDefault(); runSearch(); } }} />
              <button className="secondary-button" type="button" disabled={locked} onClick={runSearch}>{t('shareSearch')}</button>
              <label htmlFor={`${id}-box`}>{t('exchangePreviewBox')}</label>
              <select id={`${id}-box`} value={boxId} disabled={locked || !page?.results.length} onChange={(event) => { setBoxId(event.target.value); resetValidation(); }}>
                <option value="">{t('donationChooseBox')}</option>
                {page?.results.map((item) => <option key={item.id} value={item.id}>{item.global_code}</option>)}
              </select>
              <ChoiceStatus status={sources.state.status} error={sources.state.status === 'error' ? sources.state.error : undefined}
                empty={page?.results.length === 0 ? t('shareNoBox') : null} retry={sources.refresh} t={t} />
              {page && (offset > 0 || page.next) ? <nav className="sharing-actions" aria-label={t('shareBoxPages')}>
                <button type="button" className="secondary-button" disabled={locked || offset === 0} onClick={() => changePage(offset - DONATION_BOX_PAGE_SIZE)}>{t('sharePrevious')}</button>
                <button type="button" className="secondary-button" disabled={locked || !page.next} onClick={() => changePage(offset + DONATION_BOX_PAGE_SIZE)}>{t('shareNext')}</button>
              </nav> : null}
            </div>
            <div className="lab-location-field lab-location-field-wide">
              <label htmlFor={`${id}-institution`}>{t('exchangePreviewInstitution')}</label>
              <select id={`${id}-institution`} value={destinationId} disabled={locked || !directory.length} onChange={(event) => { setDestinationId(event.target.value); resetValidation(); }}>
                <option value="">{t('donationChooseDestination')}</option>
                {groups.map(({ country, label, institutions }) => <optgroup key={country} label={label}>
                  {institutions.map((institution) => <option key={institution.institution_id} value={institution.institution_id}>{institution.name}</option>)}
                </optgroup>)}
              </select>
              <ChoiceStatus status={destinations.state.status} error={destinations.state.status === 'error' ? destinations.state.error : undefined}
                empty={destinations.state.status === 'ready' && directory.length === 0 ? t('donationNoDestination') : null} retry={destinations.refresh} t={t} />
            </div>
            {validatedHash && box ? <div className="lab-location-field lab-location-field-wide">
              <a href={`/boxes/${encodeURIComponent(box.global_code)}`} target="_blank" rel="noopener noreferrer">{t('donationOpenHistory')}</a>
              <label><input type="checkbox" checked={confirmed} disabled={locked} onChange={(event) => setConfirmed(event.target.checked)} /> {t('donationConfirmHistory')}</label>
            </div> : null}
          </div>
          <div role="status">{isBusy ? t('loading') : null}</div>
          {error ? <p role="alert">{t(error)}</p> : null}
          <footer className="lab-location-form-actions">
            <button className="secondary-button" type="button" disabled={isBusy} onClick={close}>{t('close')}</button>
            <button className="primary-button" type="submit" disabled={isBusy || (!retry && (!box || !destination || (validatedHash !== null && !confirmed)))}>
              {t(retry && !isBusy ? 'lineageRetry' : validatedHash ? 'donationSubmitProposal' : 'donationCheckEligibility')}
            </button>
          </footer>
        </form>
      </section>
    </div>
  </ModalPortal>;
}
