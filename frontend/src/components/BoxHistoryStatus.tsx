import { createTranslator, type Language } from '../i18n';
import type { BoxHistoryState } from '../hooks/useBoxHistory';

export default function BoxHistoryStatus({ history, language }: {
  history: BoxHistoryState;
  language: Language;
  showPartial?: boolean;
}) {
  const t = createTranslator(language);
  if (!history.loading && !history.failed) return null;
  return <div className="lineage-inline-status" role="status">
    <p>{t(history.failed ? 'historyFailed' : 'historyLoading')}</p>
    {history.failed ? <button type="button" className="secondary-button compact-button" onClick={history.retry}>
      {t('reloadAction')}
    </button> : null}
  </div>;
}
