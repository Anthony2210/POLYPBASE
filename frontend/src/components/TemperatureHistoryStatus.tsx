import { createTranslator, type Language } from '../i18n';

export default function TemperatureHistoryStatus({ loading, failed, retry, language }: {
  loading: boolean;
  failed: boolean;
  retry: () => void;
  language: Language;
}) {
  const t = createTranslator(language);
  return <>
    {loading ? <p className="sr-only" role="status">{t('temperatureHistoryLoading')}</p> : null}
    {failed ? <div role="alert">
      <p>{t('temperatureHistoryFailed')}</p>
      <button type="button" className="secondary-button compact-button" onClick={retry}>
        {t('temperatureHistoryRetry')}
      </button>
    </div> : null}
  </>;
}
