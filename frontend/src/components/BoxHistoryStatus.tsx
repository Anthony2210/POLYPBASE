import { createTranslator, type Language } from '../i18n';
import type { BoxHistoryState } from '../hooks/useBoxHistory';

export default function BoxHistoryStatus({ history, language, showPartial = true }: {
  history: BoxHistoryState;
  language: Language;
  showPartial?: boolean;
}) {
  const t = createTranslator(language);
  const unresolved = history.events.some(event => event.temporal_precision === 'unresolved')
    || history.candidates?.some(candidate => ['blocked', 'review_required'].includes(candidate.reconciliation_status))
    || history.assessments?.some(assessment => assessment.classification !== 'initial_assignment');
  if (!history.loading && !history.failed && (!showPartial || !unresolved)) return null;
  return <div className="lineage-inline-status" role="status">
    <p>{t(history.failed ? 'historyFailed' : history.loading ? 'historyLoading' : 'historyPartial')}</p>
    {history.failed ? <button type="button" className="secondary-button compact-button" onClick={history.retry}>
      {t('reloadAction')}
    </button> : null}
  </div>;
}
