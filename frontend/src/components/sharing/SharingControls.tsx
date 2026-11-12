import type { ShareMode, SharingOptions } from '../../api/boxSharing';
import type { Translator } from '../../i18n';
import { selectorIsLocked, selectorLabel, selectorLabels } from '../../utils/boxSharing';

export function DisclosureControls({ options, selected, onChange, disabled, t }: {
  options: SharingOptions; selected: string[]; onChange: (selected: string[]) => void; disabled?: boolean; t: Translator;
}) {
  const groups = [
    { name: 'source', label: 'shareSource' }, { name: 'identity', label: 'shareIdentity' },
    { name: 'observation', label: 'shareObservations' }, { name: 'provenance', label: 'shareProvenance' },
  ] as const;
  return <div className="sharing-disclosure">
    <p className="sharing-help">{t('shareDisclosureHelp')}</p>
    {groups.map(group => <fieldset key={group.name} disabled={disabled}>
      <legend>{t(group.label)}</legend>
      {options.selectors.filter(option => {
        const provenance = option.name === 'observation.origin' || option.name === 'observation.observer_status';
        return group.name === 'provenance' ? provenance : option.group === group.name && !provenance;
      }).map(option => <label className="sharing-check" key={option.name}>
        <input type="checkbox" checked={selected.includes(option.name)} required={option.mandatory}
          disabled={!selectorLabels[option.name] || selectorIsLocked(option, options.selectors, selected)}
          onChange={event => onChange(event.target.checked ? [...selected, option.name] : selected.filter(name => name !== option.name))} />
        <span>{selectorLabel(option.name, t)}{option.mandatory ? <small>{t('shareRequired')}</small> : null}
          {option.requires.length > 0 ? <small>{t('shareRequires').replace('{fields}', option.requires.map(name => selectorLabel(name, t)).join(', '))}</small> : null}
        </span>
      </label>)}
    </fieldset>)}
    <p className="sharing-help">{t('shareHistoryScope')}</p>
  </div>;
}
export function ModeControls({ options, mode, onChange, t }: { options: SharingOptions; mode: ShareMode | ''; onChange: (mode: ShareMode) => void; t: Translator }) {
  return <fieldset className="sharing-mode"><legend>{t('shareMode')}</legend>
    {options.modes.map(value => <label className="sharing-check" key={value}>
      <input type="radio" name="sharing-mode" required checked={mode === value}
        disabled={value === 'frozen' && !options.frozen_capture_available} onChange={() => onChange(value)} />
      <span>{t(value === 'live' ? 'shareLive' : 'shareFrozen')}
        <small>{t(value === 'live' ? 'shareLiveHelp' : 'shareFrozenHelp')}</small>
        {value === 'frozen' && !options.frozen_capture_available ? <small>{t('shareErrorFrozen')}</small> : null}
      </span>
    </label>)}
  </fieldset>;
}
export function ExpiryField({ kind, value, onChange, maximum, required = true, t }: { kind: 'invitation' | 'access'; value: string; onChange: (value: string) => void; maximum: number | null; required?: boolean; t: Translator }) {
  return <label className="sharing-field">{t(kind === 'invitation' ? 'shareInvitationExpiry' : 'shareAccessExpiry')}
    <input type="datetime-local" required={required} value={value} onChange={event => onChange(event.target.value)} />
    <small>{t('shareUTC')}{maximum !== null ? ` ${t('shareLifetimeLimit').replace('{hours}', String(maximum / 3600))}` : ''}</small>
  </label>;
}
