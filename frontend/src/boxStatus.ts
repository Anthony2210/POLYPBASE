import type { Language } from './i18n';

export type BoxStatusTone = 'alive' | 'dead' | 'warning' | 'neutral';

type BoxStatusPresentation = {
  label: string;
  tone: BoxStatusTone;
};

const statusLabels: Record<'fr' | 'en', Record<string, BoxStatusPresentation>> = {
  fr: {
    pending_review: { label: 'À vérifier', tone: 'warning' },
    active: { label: 'Active', tone: 'alive' },
    inactive: { label: 'Inactive', tone: 'dead' },
  },
  en: {
    pending_review: { label: 'Pending review', tone: 'warning' },
    active: { label: 'Active', tone: 'alive' },
    inactive: { label: 'Inactive', tone: 'dead' },
  },
};

// Validated Japanese labels; tones and unknown statuses follow the English table.
const japaneseStatusLabels: Record<string, string> = {
  pending_review: '確認待ち',
  active: '有効',
  inactive: '無効',
};

export function getBoxStatusPresentation(
  status: string,
  language: Language,
): BoxStatusPresentation {
  const presentation = statusLabels[language === 'ja' ? 'en' : language][status];
  const japaneseLabel = language === 'ja' && presentation ? japaneseStatusLabels[status] : undefined;
  if (japaneseLabel) return { ...presentation, label: japaneseLabel };
  return presentation ?? {
    label: status.replaceAll('_', ' '),
    tone: 'neutral',
  };
}
