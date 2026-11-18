import { useId, useState } from 'react';
import type { Translator } from '../../i18n';
import useMutationDialog from '../../hooks/useMutationDialog';
import ModalPortal from '../ModalPortal';
import PolypbaseIcon from '../PolypbaseIcon';

// Deliberately local: no lookup, persistence or mutation belongs in this preview.
export default function ExchangeFormPreview({ onClose, t }: { onClose: () => void; t: Translator }) {
  const id = useId();
  const [type, setType] = useState('donation');
  const [box, setBox] = useState('');
  const [institution, setInstitution] = useState('');
  const [email, setEmail] = useState('');
  const { dialogRef, initialFocusRef, close } = useMutationDialog<HTMLSelectElement>(false, onClose);

  return <ModalPortal>
    <div className="modal-backdrop" role="presentation" onMouseDown={close}>
      <section ref={dialogRef} tabIndex={-1} className="lab-location-modal" role="dialog" aria-modal="true" aria-labelledby={`${id}-title`} onMouseDown={(event) => event.stopPropagation()}>
        <header className="lab-location-modal-heading">
          <h2 id={`${id}-title`}>{t('exchangePreviewTitle')}</h2>
          <button className="modal-close-button" type="button" aria-label={t('close')} title={t('close')} onClick={close}><PolypbaseIcon name="close" size={18} /></button>
        </header>
        <form className="lab-location-form" onSubmit={(event) => event.preventDefault()} noValidate>

          <div className="lab-location-fields">
            <div className="lab-location-field lab-location-field-wide">
              <label htmlFor={`${id}-type`}>{t('exchangePreviewType')}</label>
              <select ref={initialFocusRef} id={`${id}-type`} value={type} onChange={(event) => setType(event.target.value)}>
                <option value="donation">{t('donationTypeDon')}</option>
                <option value="live">{t('exchangePreviewLive')}</option>
                <option value="frozen">{t('exchangePreviewFrozen')}</option>
                <option value="email_frozen">{t('exchangePreviewEmail')}</option>
              </select>
            </div>
            <div className="lab-location-field lab-location-field-wide">
              <label htmlFor={`${id}-box`}>{t('exchangePreviewBox')}</label>
              <input id={`${id}-box`} type="text" autoComplete="off" value={box} placeholder={t('exchangePreviewBoxPlaceholder')} onChange={(event) => setBox(event.target.value)} />
            </div>
            <div className="lab-location-field lab-location-field-wide">
              <label htmlFor={`${id}-institution`}>{t('exchangePreviewInstitution')}</label>
              <input id={`${id}-institution`} type="text" autoComplete="off" value={institution} placeholder={t('exchangePreviewInstitutionPlaceholder')} onChange={(event) => setInstitution(event.target.value)} />
            </div>
            {type === 'email_frozen' ? <div className="lab-location-field lab-location-field-wide">
              <label htmlFor={`${id}-email`}>{t('manageFieldEmail')}</label>
              <input id={`${id}-email`} type="email" autoComplete="off" value={email} onChange={(event) => setEmail(event.target.value)} />
            </div> : null}
          </div>
          <footer className="lab-location-form-actions"><button className="secondary-button" type="button" onClick={close}>{t('close')}</button></footer>
        </form>
      </section>
    </div>
  </ModalPortal>;
}
