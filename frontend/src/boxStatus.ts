export type BoxStatusTone = 'alive' | 'dead' | 'warning' | 'neutral';

type Language = 'fr' | 'en';

type BoxStatusPresentation = {
  label: string;
  tone: BoxStatusTone;
};

const statusLabels: Record<Language, Record<string, BoxStatusPresentation>> = {
  fr: {
    active: { label: 'Vivante', tone: 'alive' },
    stopped: { label: 'Morte', tone: 'dead' },
    lost: { label: 'Perdue', tone: 'warning' },
    archived: { label: 'Archivée', tone: 'dead' },
  },
  en: {
    active: { label: 'Alive', tone: 'alive' },
    stopped: { label: 'Dead', tone: 'dead' },
    lost: { label: 'Lost', tone: 'warning' },
    archived: { label: 'Archived', tone: 'dead' },
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
