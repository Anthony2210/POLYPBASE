export type ChartPeriodId = '1m' | '3m' | '6m' | '12m';

export const CHART_PERIODS: Array<{ id: ChartPeriodId; days: number }> = [
  { id: '1m', days: 31 },
  { id: '3m', days: 92 },
  { id: '6m', days: 184 },
  { id: '12m', days: 365 },
];

type ChartPeriodSelectorProps = {
  ariaLabel: string;
  labels: Record<ChartPeriodId, string>;
  onChange: (period: ChartPeriodId) => void;
  value: ChartPeriodId;
};

export default function ChartPeriodSelector({
  ariaLabel,
  labels,
  onChange,
  value,
}: ChartPeriodSelectorProps) {
  return (
    <div className="chart-periods" aria-label={ariaLabel} role="group">
      {CHART_PERIODS.map((period) => (
        <button
          key={period.id}
          className={value === period.id ? 'is-active' : ''}
          type="button"
          aria-pressed={value === period.id}
          onClick={() => onChange(period.id)}
        >
          {labels[period.id]}
        </button>
      ))}
    </div>
  );
}
