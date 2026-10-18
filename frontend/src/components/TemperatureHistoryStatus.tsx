import type { Language } from '../i18n';
import type { BoxTemperaturePoint } from '../types';

// Temperature loading and failure messages are intentionally not shown; the stable status span stays.
export default function TemperatureHistoryStatus(_props: {
  points?: readonly BoxTemperaturePoint[];
  loading: boolean;
  failed: boolean;
  retry: () => void;
  language: Language;
}) {
  return <span className="bio-trend-series-status" role="status" />;
}
