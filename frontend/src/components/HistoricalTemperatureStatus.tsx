import { createTranslator, type Language } from '../i18n';

// Compact inline feedback for the week-only series; sits in the chart's series feedback area.
export default function HistoricalTemperatureStatus({ pointCount, loading, failed, retry, language }: {
  pointCount: number;
  loading: boolean;
  failed: boolean;
  retry: () => void;
  language: Language;
}) {
  const t = createTranslator(language);
  return <span className="bio-trend-series-status">
    {loading ? <span role="status">{t('historicalTemperatureLoading')}</span> : null}
    {failed ? <span role="alert">
      {t('historicalTemperatureFailed')}{' '}
      <button type="button" className="bio-trend-series-retry" onClick={retry}>{t('historicalTemperatureRetry')}</button>
    </span> : null}
    {!loading && !failed && pointCount === 0 ? <span role="status">{t('historicalTemperatureEmpty')}</span> : null}
  </span>;
}
