import { en } from './en';
import { fr, type TranslationKey } from './fr';

export const translations = { fr, en } as const;

export type Language = keyof typeof translations;
export type { TranslationKey };
export type Translator = (key: TranslationKey) => string;

export const DEFAULT_LANGUAGE: Language = 'fr';
const INTERFACE_LANGUAGE_STORAGE_KEY = 'polypbase.interfaceLanguage';

export function resolveLanguage(language: string | null | undefined): Language {
  const shortCode = language?.trim().toLocaleLowerCase().split('-')[0];
  return shortCode && shortCode in translations ? shortCode as Language : DEFAULT_LANGUAGE;
}

export function getStoredInterfaceLanguage(): Language | null {
  try {
    const storedLanguage = window.localStorage.getItem(INTERFACE_LANGUAGE_STORAGE_KEY);
    return storedLanguage ? resolveLanguage(storedLanguage) : null;
  } catch {
    return null;
  }
}

export function setStoredInterfaceLanguage(language: string): Language {
  const resolvedLanguage = resolveLanguage(language);

  try {
    window.localStorage.setItem(INTERFACE_LANGUAGE_STORAGE_KEY, resolvedLanguage);
  } catch {
    // The server preference remains authoritative when storage is unavailable.
  }

  return resolvedLanguage;
}

export function createTranslator(language: Language): Translator {
  return (key) => translations[language][key] ?? translations[DEFAULT_LANGUAGE][key] ?? key;
}
