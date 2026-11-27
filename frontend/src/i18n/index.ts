import { sections } from './sections';

type KeysOfUnion<T> = T extends unknown ? keyof T : never;
type TranslationKey = KeysOfUnion<(typeof sections)[number]>;
type Catalog = Readonly<Record<TranslationKey, string>>;
type LanguageEntry = { fr: string; en: string; ja?: string };

function buildCatalogs() {
  const fr: Partial<Record<TranslationKey, string>> = {};
  const en: Partial<Record<TranslationKey, string>> = {};
  const ja: Partial<Record<TranslationKey, string>> = {};
  for (const section of sections) {
    for (const [key, entry] of Object.entries<LanguageEntry>(section)) {
      const translationKey = key as TranslationKey;
      fr[translationKey] = entry.fr;
      en[translationKey] = entry.en;
      ja[translationKey] = entry.ja ?? entry.en;
    }
  }
  // The section checks verify that every literal key is present exactly once.
  return { fr, en, ja } as {
    readonly fr: Catalog;
    readonly en: Catalog;
    readonly ja: Catalog;
  };
}

export const translations = buildCatalogs();

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
  return shortCode && Object.keys(translations).includes(shortCode)
    ? (shortCode as Language)
    : DEFAULT_LANGUAGE;
}

export function getStoredInterfaceLanguage(): Language | null {
  try {
    const storedLanguage = window.localStorage.getItem(
      INTERFACE_LANGUAGE_STORAGE_KEY,
    );
    return storedLanguage ? resolveLanguage(storedLanguage) : null;
  } catch {
    return null;
  }
}

export function setStoredInterfaceLanguage(language: string): Language {
  const resolvedLanguage = resolveLanguage(language);

  try {
    window.localStorage.setItem(
      INTERFACE_LANGUAGE_STORAGE_KEY,
      resolvedLanguage,
    );
  } catch {
    // The server preference remains authoritative when storage is unavailable.
  }

  return resolvedLanguage;
}

export function createTranslator(language: Language): Translator {
  return (key) =>
    translations[language][key] ?? translations[DEFAULT_LANGUAGE][key] ?? key;
}
