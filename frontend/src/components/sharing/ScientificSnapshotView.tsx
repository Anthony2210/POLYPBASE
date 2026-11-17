import { useState } from 'react';
import type { ScientificSnapshot } from '../../api/boxSharing';
import type { Translator } from '../../i18n';
import { fieldText, selectorLabel, snapshotSupported } from '../../utils/boxSharing';

const HISTORY_PAGE_SIZE = 50;
export default function ScientificSnapshotView({ snapshot, t }: { snapshot: ScientificSnapshot; t: Translator }) {
  const [page, setPage] = useState(0);
  if (!snapshotSupported(snapshot)) return <p className="inline-error" role="alert">{t('shareUnsupported')}</p>;
  const observations = snapshot.observations;
  const observationSelectors = snapshot.disclosed.filter(name => name.startsWith('observation.') && name !== 'observation.time');
  const rows = observations?.slice(page * HISTORY_PAGE_SIZE, (page + 1) * HISTORY_PAGE_SIZE);
  return <div className="sharing-science">
    {(['source', 'identity'] as const).map(group => {
      const selectors = snapshot.disclosed.filter(name => name.startsWith(`${group}.`));
      return selectors.length > 0 ? <section key={group}>
        <h3>{t(group === 'source' ? 'shareSource' : 'shareIdentity')}</h3>
        <dl className="sharing-facts">{selectors.map(name => <div key={name}>
          <dt>{selectorLabel(name, t)}</dt><dd><bdi>{fieldText(snapshot[group]?.[name.split('.')[1]], t)}</bdi></dd>
        </div>)}</dl>
      </section> : null;
    })}
    <section>
      <h3>{t('shareObservations')}</h3>
      {observations === undefined ? <p className="sharing-help">{t('shareHistoryUndisclosed')}</p>
        : observations.length === 0 ? <p>{t('shareHistoryEmpty')}</p>
        : <>
          <p className="sharing-help">{t('shareHistoryScope')}</p>
          <p className="sharing-help">{t('shareHistoryScroll')}</p>
          <div className="sharing-table-scroll" role="region" aria-label={t('shareObservations')} tabIndex={0}>
            <table className="sharing-table">
              <caption className="sr-only">{t('shareScientificLegend')}</caption>
              <thead><tr><th scope="col">{t('shareObservationTime')}</th>{observationSelectors.map(name => <th scope="col" key={name}>{selectorLabel(name, t)}</th>)}</tr></thead>
              <tbody>{rows?.map((row, index) => <tr key={page * HISTORY_PAGE_SIZE + index}>
                <th scope="row"><span>{t('historyIsoWeek').replace('{week}', String(row.time.iso_week)).replace('{year}', String(row.time.iso_year))}</span>
                  {row.time.precision === 'exact_date' && row.time.measured_on !== null ? <small>{row.time.measured_on}</small>
                    : <small>{t(row.time.precision === 'iso_week' ? 'shareWeekOnly' : 'shareLegacyDate')}</small>}
                </th>
                {observationSelectors.map(name => {
                  const key = name.split('.')[1];
                  const value = key === 'origin' || key === 'observer_status' ? row.provenance?.[key] : row.values?.[key];
                  return <td key={name}>{fieldText(value, t, key === 'origin' || key === 'observer_status')}{key === 'origin' && row.provenance?.import_format
                    ? <small>{fieldText(row.provenance.import_format, t)}</small> : null}</td>;
                })}
              </tr>)}</tbody>
            </table>
          </div>
          <nav className="sharing-actions" aria-label={t('shareHistoryPages')}>
            <button className="secondary-button" type="button" disabled={page === 0} onClick={() => setPage(value => value - 1)}>{t('sharePrevious')}</button>
            <span>{t('sharePage').replace('{page}', String(page + 1))}</span>
            <button className="secondary-button" type="button" disabled={(page + 1) * HISTORY_PAGE_SIZE >= observations.length} onClick={() => setPage(value => value + 1)}>{t('shareNext')}</button>
          </nav>
        </>}
    </section>
    <p className="sharing-help">{t('shareScientificLegend')}</p>
    <details><summary>{t('shareDisclosure')}</summary><ul>{snapshot.disclosed.map(name => <li key={name}>{selectorLabel(name, t)}</li>)}</ul></details>
  </div>;
}
