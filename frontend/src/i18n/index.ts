import { en } from './en';
import { fr, type TranslationKey } from './fr';

export const translations = { fr, en } as const;

export type Language = keyof typeof translations;
export type { TranslationKey };
export type Translator = (key: TranslationKey) => string;

export const DEFAULT_LANGUAGE: Language = 'fr';

export function resolveLanguage(language: string | null | undefined): Language {
  const shortCode = language?.trim().toLocaleLowerCase().split('-')[0];
  return shortCode && shortCode in translations ? shortCode as Language : DEFAULT_LANGUAGE;
}

export function createTranslator(language: Language): Translator {
  return (key) => translations[language][key] ?? translations[DEFAULT_LANGUAGE][key] ?? key;
}
