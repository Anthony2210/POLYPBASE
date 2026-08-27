export type BoxStatusTone = 'alive' | 'dead' | 'warning' | 'neutral';

type Language = 'fr' | 'en';

type BoxStatusPresentation = {
  label: string;
  tone: BoxStatusTone;
};

const statusLabels: Record<Language, Record<string, BoxStatusPresentation>> = {
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

export function getBoxStatusPresentation(
  status: string,
  language: Language,
): BoxStatusPresentation {
  return statusLabels[language][status] ?? {
    label: status.replaceAll('_', ' '),
    tone: 'neutral',
  };
}
