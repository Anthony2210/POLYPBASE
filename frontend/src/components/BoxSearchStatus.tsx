import type { Translator } from '../i18n';
import type { BoxCollectionState } from '../utils/boxCollection';

export default function BoxSearchStatus({
  status,
  onRetry,
  t,
}: {
  status: BoxCollectionState['status'];
  onRetry: () => void;
  t: Translator;
}) {
  if (status === 'error') {
    return (
      <section className="suggestion-panel" role="alert">
        <p className="muted compact-text">{t('boxCollectionLoadError')}</p>
        <button className="secondary-button" type="button" onClick={onRetry}>
          {t('boxCollectionRetry')}
        </button>
      </section>
    );
  }

  return (
    <section className="suggestion-panel" role="status">
      <p className="muted compact-text">{t('loading')}</p>
    </section>
  );
}
