import { en } from './en';
import { fr, type TranslationKey } from './fr';
import { ja } from './ja';

type Catalog = Readonly<Record<TranslationKey, string>>;

// Displayed catalogs. FR and EN are complete. The Japanese catalog is partial:
// any key without validated Japanese shows the English text instead.
export const translations: { readonly fr: Catalog; readonly en: Catalog; readonly ja: Catalog } = {
  fr,
  en,
  ja: { ...en, ...ja },
};

export type Language = keyof typeof translations;
export type { TranslationKey };
export type Translator = (key: TranslationKey) => string;

export const DEFAULT_LANGUAGE: Language = 'fr';
const INTERFACE_LANGUAGE_STORAGE_KEY = 'polypbase.interfaceLanguage';

const LANGUAGE_LOCALES: Record<Language, string> = {
  fr: 'fr-FR',
  en: 'en-GB',
  ja: 'ja-JP',
};

export function getLanguageLocale(language: Language): string {
  return LANGUAGE_LOCALES[language];
}

/** Picks a local FR/EN presentation table; partial Japanese entries fall back to English. */
export function selectLocalCopy<T extends Record<string, string>>(
  language: Language,
  copy: { fr: T; en: T; ja?: Partial<T> },
): T {
  return language === 'ja' ? { ...copy.en, ...copy.ja } : copy[language];
}

export function resolveLanguage(language: string | null | undefined): Language {
  const shortCode = language?.trim().toLocaleLowerCase().split('-')[0];
  return shortCode && Object.prototype.hasOwnProperty.call(translations, shortCode) ? shortCode as Language : DEFAULT_LANGUAGE;
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
