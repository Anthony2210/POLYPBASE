import { type FormEvent, type ReactNode, useEffect, useMemo, useState } from 'react';

import { apiGet, apiPatch, apiPost } from '../api/client';
import type { Translator } from '../i18n';
import type {
  LocalizedReferenceValues,
  LocalizedReferenceValue,
  ReferenceLanguage,
  SpeciesReference,
  SpeciesReferencePayload,
  StrainReference,
  StrainReferencePayload,
  TaxonomyReferences,
} from '../types/admin';
import { getErrorMessage } from '../utils/errors';
import PageLoader from './PageLoader';

type ReferenceTab = 'species' | 'strains';
type FormState =
  | { kind: 'species'; item?: SpeciesReference }
  | { kind: 'strains'; item?: StrainReference }
  | null;

const EMPTY_TRANSLATION = { name: '', description: '' };

export default function TaxonomyAdminSection({ t }: { t: Translator }) {
  const [data, setData] = useState<TaxonomyReferences | null>(null);
  const [activeTab, setActiveTab] = useState<ReferenceTab>('species');
  const [formState, setFormState] = useState<FormState>(null);
  const [search, setSearch] = useState('');
  const [isLoading, setIsLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);

  useEffect(() => {
    let isCurrent = true;
    setIsLoading(true);
    apiGet<TaxonomyReferences>('/api/taxonomy/references/')
      .then((references) => {
        if (!isCurrent) return;
        setData(references);
        setLoadError(null);
      })
      .catch(() => {
        if (isCurrent) setLoadError(t('taxonomyLoadError'));
      })
      .finally(() => {
        if (isCurrent) setIsLoading(false);
      });
    return () => {
      isCurrent = false;
    };
  }, [t]);

  const normalizedSearch = search.trim().toLocaleLowerCase();
  const filteredSpecies = useMemo(
    () => (data?.species ?? []).filter((species) => {
      if (!normalizedSearch) return true;
      return [
        species.scientific_name,
        species.genus_species_code,
        ...Object.values<LocalizedReferenceValue>(species.translations).map((translation) => translation.name),
      ].some((value) => value.toLocaleLowerCase().includes(normalizedSearch));
    }),
    [data?.species, normalizedSearch],
  );
  const filteredStrains = useMemo(
    () => (data?.strains ?? []).filter((strain) => {
      if (!normalizedSearch) return true;
      return [
        strain.code,
        strain.origin_code,
        strain.species_scientific_name,
        ...Object.values<LocalizedReferenceValue>(strain.translations).map((translation) => translation.name),
      ].some((value) => value.toLocaleLowerCase().includes(normalizedSearch));
    }),
    [data?.strains, normalizedSearch],
  );

  function saveSpecies(species: SpeciesReference) {
    setData((current) => current ? {
      ...current,
      species: [
        ...current.species.filter((item) => item.id !== species.id),
        species,
      ].sort((first, second) =>
        first.scientific_name.localeCompare(second.scientific_name)),
    } : current);
    setFormState(null);
  }

  function saveStrain(strain: StrainReference, previousSpeciesId?: number) {
    setData((current) => current ? {
      ...current,
      species: current.species.map((species) => {
        if (previousSpeciesId === undefined && species.id === strain.species) {
          return { ...species, strain_count: species.strain_count + 1 };
        }
        if (previousSpeciesId !== strain.species && species.id === previousSpeciesId) {
          return { ...species, strain_count: Math.max(0, species.strain_count - 1) };
        }
        if (previousSpeciesId !== strain.species && species.id === strain.species) {
          return { ...species, strain_count: species.strain_count + 1 };
        }
        return species;
      }),
      strains: [
        ...current.strains.filter((item) => item.id !== strain.id),
        strain,
      ].sort(compareStrains),
    } : current);
    setFormState(null);
  }

  if (isLoading) return <PageLoader variant="admin" label={t('taxonomyTitle')} />;
  if (!data || loadError) return <p className="inline-error">{loadError ?? t('taxonomyLoadError')}</p>;

  return (
    <section className="admin-section taxonomy-admin-section" id="admin-taxonomy">
      <header className="taxonomy-admin-heading">
        <div>
          <h2>{t('taxonomyTitle')}</h2>
          <div className="taxonomy-counts" aria-label={t('taxonomyTitle')}>
            <span><strong>{data.species.length}</strong> {t('taxonomySpeciesCount')}</span>
            <span><strong>{data.strains.length}</strong> {t('taxonomyStrainCount')}</span>
          </div>
        </div>
        <button
          className="primary-button taxonomy-add-button"
          disabled={activeTab === 'strains' && data.species.length === 0}
          type="button"
          title={activeTab === 'strains' && data.species.length === 0
            ? t('taxonomyCreateSpeciesFirst')
            : undefined}
          onClick={() => setFormState({ kind: activeTab })}
        >
          <span aria-hidden="true">+</span>
          {activeTab === 'species' ? t('taxonomyNewSpecies') : t('taxonomyNewStrain')}
        </button>
      </header>

      <div className="taxonomy-toolbar">
        <div className="segmented-control taxonomy-tabs" role="tablist">
          <button
            className={activeTab === 'species' ? 'active' : ''}
            type="button"
            role="tab"
            aria-selected={activeTab === 'species'}
            onClick={() => setActiveTab('species')}
          >
            {t('taxonomySpecies')}
          </button>
          <button
            className={activeTab === 'strains' ? 'active' : ''}
            type="button"
            role="tab"
            aria-selected={activeTab === 'strains'}
            onClick={() => setActiveTab('strains')}
          >
            {t('taxonomyStrains')}
          </button>
        </div>
        <label className="taxonomy-search">
          <span className="sr-only">{t('taxonomySearch')}</span>
          <input
            type="search"
            value={search}
            placeholder={t('taxonomySearch')}
            onChange={(event) => setSearch(event.target.value)}
          />
        </label>
      </div>

      {formState?.kind === 'species' ? (
        <SpeciesForm
          key={formState.item?.id ?? 'new-species'}
          initialSpecies={formState.item}
          languages={data.languages}
          t={t}
          onCancel={() => setFormState(null)}
          onSaved={saveSpecies}
        />
      ) : null}
      {formState?.kind === 'strains' ? (
        <StrainForm
          key={formState.item?.id ?? 'new-strain'}
          initialStrain={formState.item}
          languages={data.languages}
          species={data.species}
          t={t}
          onCancel={() => setFormState(null)}
          onSaved={saveStrain}
        />
      ) : null}

      {activeTab === 'species' ? (
        <ReferenceGrid emptyText={t('taxonomyEmptySpecies')}>
          {filteredSpecies.map((species) => (
            <SpeciesCard
              key={species.id}
              species={species}
              languages={data.languages}
              t={t}
              onEdit={() => setFormState({ kind: 'species', item: species })}
            />
          ))}
        </ReferenceGrid>
      ) : (
        <ReferenceGrid emptyText={t('taxonomyEmptyStrains')}>
          {filteredStrains.map((strain) => (
            <StrainCard
              key={strain.id}
              strain={strain}
              languages={data.languages}
              t={t}
              onEdit={() => setFormState({ kind: 'strains', item: strain })}
            />
          ))}
        </ReferenceGrid>
      )}
    </section>
  );
}

function SpeciesForm({
  initialSpecies,
  languages,
  t,
  onCancel,
  onSaved,
}: {
  key?: string | number;
  initialSpecies?: SpeciesReference;
  languages: ReferenceLanguage[];
  t: Translator;
  onCancel: () => void;
  onSaved: (species: SpeciesReference) => void;
}) {
  const [scientificName, setScientificName] = useState(initialSpecies?.scientific_name ?? '');
  const [speciesCode, setSpeciesCode] = useState(initialSpecies?.genus_species_code ?? '');
  const [aphiaId, setAphiaId] = useState(initialSpecies?.worms_aphia_id?.toString() ?? '');
  const [isDescribed, setIsDescribed] = useState(initialSpecies?.is_described ?? true);
  const [notes, setNotes] = useState(initialSpecies?.notes ?? '');
  const [translations, setTranslations] = useState(() =>
    createTranslationState(languages, initialSpecies?.translations));
  const [activeLanguage, setActiveLanguage] = useState(languages[0]?.code ?? 'fr');
  const [isSaving, setIsSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (isSaving) return;
    setIsSaving(true);
    setError(null);
    const payload: SpeciesReferencePayload = {
      scientific_name: scientificName.trim(),
      genus_species_code: speciesCode.trim(),
      worms_aphia_id: aphiaId ? Number(aphiaId) : null,
      is_described: isDescribed,
      notes: notes.trim(),
      translations: cleanTranslations(translations),
    };
    try {
      const savedSpecies = initialSpecies
        ? await apiPatch<SpeciesReference>(`/api/taxonomy/species/${initialSpecies.id}/`, payload)
        : await apiPost<SpeciesReference>('/api/taxonomy/species/', payload);
      onSaved(savedSpecies);
    } catch (requestError) {
      setError(getErrorMessage(requestError, t('taxonomySaveError')));
    } finally {
      setIsSaving(false);
    }
  }

  return (
    <form className="taxonomy-form" onSubmit={submit}>
      <FormHeading
        title={t(initialSpecies ? 'taxonomyEditSpecies' : 'taxonomyNewSpecies')}
        closeLabel={t('close')}
        onCancel={onCancel}
      />
      <div className="taxonomy-form-columns">
        <fieldset>
          <legend>{t('taxonomyUniversalData')}</legend>
          <label>
            <span>{t('taxonomyScientificName')}</span>
            <input required value={scientificName} onChange={(event) => setScientificName(event.target.value)} />
          </label>
          <div className="taxonomy-inline-fields">
            <label>
              <span>{t('taxonomySpeciesCode')}</span>
              <input value={speciesCode} onChange={(event) => setSpeciesCode(event.target.value.toUpperCase())} />
            </label>
            <label>
              <span>{t('taxonomyAphiaId')}</span>
              <input min="1" type="number" value={aphiaId} onChange={(event) => setAphiaId(event.target.value)} />
            </label>
          </div>
          <label className="taxonomy-checkbox">
            <input checked={isDescribed} type="checkbox" onChange={(event) => setIsDescribed(event.target.checked)} />
            <span>{t('taxonomyDescribed')}</span>
          </label>
          <label>
            <span>{t('taxonomyNotes')}</span>
            <textarea rows={2} value={notes} onChange={(event) => setNotes(event.target.value)} />
          </label>
        </fieldset>
        <LocalizedFields
          activeLanguage={activeLanguage}
          languages={languages}
          translations={translations}
          t={t}
          onLanguageChange={setActiveLanguage}
          onTranslationsChange={setTranslations}
        />
      </div>
      <FormActions
        error={error}
        isEditing={Boolean(initialSpecies)}
        isSaving={isSaving}
        t={t}
        onCancel={onCancel}
      />
    </form>
  );
}

function StrainForm({
  initialStrain,
  languages,
  species,
  t,
  onCancel,
  onSaved,
}: {
  key?: string | number;
  initialStrain?: StrainReference;
  languages: ReferenceLanguage[];
  species: SpeciesReference[];
  t: Translator;
  onCancel: () => void;
  onSaved: (strain: StrainReference, previousSpeciesId?: number) => void;
}) {
  const [speciesId, setSpeciesId] = useState(initialStrain?.species ?? species[0]?.id ?? 0);
  const [code, setCode] = useState(initialStrain?.code ?? '');
  const [number, setNumber] = useState(initialStrain?.number?.toString() ?? '');
  const [originCode, setOriginCode] = useState(initialStrain?.origin_code ?? '');
  const [notes, setNotes] = useState(initialStrain?.notes ?? '');
  const [translations, setTranslations] = useState(() =>
    createTranslationState(languages, initialStrain?.translations));
  const [activeLanguage, setActiveLanguage] = useState(languages[0]?.code ?? 'fr');
  const [isSaving, setIsSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (isSaving || !speciesId) return;
    setIsSaving(true);
    setError(null);
    const payload: StrainReferencePayload = {
      species: speciesId,
      code: code.trim(),
      number: number ? Number(number) : null,
      origin_code: originCode.trim(),
      notes: notes.trim(),
      translations: cleanTranslations(translations),
    };
    try {
      const savedStrain = initialStrain
        ? await apiPatch<StrainReference>(`/api/taxonomy/strains/${initialStrain.id}/`, payload)
        : await apiPost<StrainReference>('/api/taxonomy/strains/', payload);
      onSaved(savedStrain, initialStrain?.species);
    } catch (requestError) {
      setError(getErrorMessage(requestError, t('taxonomySaveError')));
    } finally {
      setIsSaving(false);
    }
  }

  return (
    <form className="taxonomy-form" onSubmit={submit}>
      <FormHeading
        title={t(initialStrain ? 'taxonomyEditStrain' : 'taxonomyNewStrain')}
        closeLabel={t('close')}
        onCancel={onCancel}
      />
      <div className="taxonomy-form-columns">
        <fieldset>
          <legend>{t('taxonomyUniversalData')}</legend>
          <label>
            <span>{t('taxonomySpeciesSelect')}</span>
            <select required value={speciesId} onChange={(event) => setSpeciesId(Number(event.target.value))}>
              {species.map((item) => <option key={item.id} value={item.id}>{item.scientific_name}</option>)}
            </select>
          </label>
          <label>
            <span>{t('taxonomyStrainCode')}</span>
            <input required value={code} onChange={(event) => setCode(event.target.value.toUpperCase())} />
          </label>
          <div className="taxonomy-inline-fields">
            <label>
              <span>{t('taxonomyStrainNumber')}</span>
              <input min="1" type="number" value={number} onChange={(event) => setNumber(event.target.value)} />
            </label>
            <label>
              <span>{t('taxonomyOriginCode')}</span>
              <input value={originCode} onChange={(event) => setOriginCode(event.target.value.toUpperCase())} />
            </label>
          </div>
          <label>
            <span>{t('taxonomyNotes')}</span>
            <textarea rows={2} value={notes} onChange={(event) => setNotes(event.target.value)} />
          </label>
        </fieldset>
        <LocalizedFields
          activeLanguage={activeLanguage}
          languages={languages}
          translations={translations}
          t={t}
          onLanguageChange={setActiveLanguage}
          onTranslationsChange={setTranslations}
        />
      </div>
      <FormActions
        error={error}
        isEditing={Boolean(initialStrain)}
        isSaving={isSaving}
        t={t}
        onCancel={onCancel}
      />
    </form>
  );
}

function LocalizedFields({
  activeLanguage,
  languages,
  translations,
  t,
  onLanguageChange,
  onTranslationsChange,
}: {
  activeLanguage: string;
  languages: ReferenceLanguage[];
  translations: LocalizedReferenceValues;
  t: Translator;
  onLanguageChange: (language: string) => void;
  onTranslationsChange: (translations: LocalizedReferenceValues) => void;
}) {
  const active = languages.find((language) => language.code === activeLanguage) ?? languages[0];
  const value = translations[active?.code] ?? EMPTY_TRANSLATION;
  if (!active) return null;

  function update(field: 'name' | 'description', nextValue: string) {
    onTranslationsChange({
      ...translations,
      [active.code]: { ...value, [field]: nextValue },
    });
  }

  return (
    <fieldset className="localized-reference-fields">
      <legend>{t('taxonomyLocalizedData')}</legend>
      <div className="language-tabs" role="tablist">
        {languages.map((language) => {
          const hasName = Boolean(translations[language.code]?.name.trim());
          return (
            <button
              className={language.code === active.code ? 'active' : ''}
              key={language.code}
              type="button"
              role="tab"
              aria-selected={language.code === active.code}
              onClick={() => onLanguageChange(language.code)}
            >
              {language.label}
              {language.required ? <small>{t('taxonomyLanguageRequired')}</small> : null}
              <span className={hasName ? 'complete' : ''} aria-hidden="true" />
            </button>
          );
        })}
      </div>
      <label>
        <span>{t('taxonomyNameByLanguage')} · {active.label}</span>
        <input
          required={active.required}
          value={value.name}
          placeholder={t('taxonomyNamePlaceholder')}
          onChange={(event) => update('name', event.target.value)}
        />
      </label>
      <label>
        <span>{t('taxonomyDescriptionByLanguage')} · {active.label}</span>
        <textarea
          rows={4}
          value={value.description}
          placeholder={t('taxonomyDescriptionPlaceholder')}
          onChange={(event) => update('description', event.target.value)}
        />
      </label>
    </fieldset>
  );
}

function FormHeading({
  title,
  closeLabel,
  onCancel,
}: {
  title: string;
  closeLabel: string;
  onCancel: () => void;
}) {
  return (
    <header className="taxonomy-form-heading">
      <h3>{title}</h3>
      <button className="icon-button" type="button" aria-label={closeLabel} onClick={onCancel}>×</button>
    </header>
  );
}

function FormActions({
  error,
  isEditing,
  isSaving,
  t,
  onCancel,
}: {
  error: string | null;
  isEditing: boolean;
  isSaving: boolean;
  t: Translator;
  onCancel: () => void;
}) {
  return (
    <footer className="taxonomy-form-actions">
      {error ? <p className="inline-error">{error}</p> : <span />}
      <button className="secondary-button" type="button" onClick={onCancel}>{t('confirmCancel')}</button>
      <button className="primary-button" disabled={isSaving} type="submit">
        {isSaving
          ? t(isEditing ? 'taxonomySaving' : 'taxonomyCreating')
          : t(isEditing ? 'taxonomySave' : 'taxonomyCreate')}
      </button>
    </footer>
  );
}

function ReferenceGrid({ children, emptyText }: { children: ReactNode; emptyText: string }) {
  const items = Array.isArray(children) ? children.filter(Boolean) : children;
  if (Array.isArray(items) && items.length === 0) return <p className="empty-state">{emptyText}</p>;
  return <div className="taxonomy-reference-grid">{children}</div>;
}

function SpeciesCard({
  species,
  languages,
  t,
  onEdit,
}: {
  key?: string | number;
  species: SpeciesReference;
  languages: ReferenceLanguage[];
  t: Translator;
  onEdit: () => void;
}) {
  const localizedName = preferredName(species.translations, languages) || species.scientific_name;
  return (
    <article className="taxonomy-reference-card">
      <header>
        <div>
          <h3>{species.scientific_name}</h3>
          <p>{localizedName}</p>
        </div>
        <div className="taxonomy-card-actions">
          {species.genus_species_code ? <span className="reference-code">{species.genus_species_code}</span> : null}
          <button className="taxonomy-edit-button" type="button" onClick={onEdit}>{t('taxonomyEdit')}</button>
        </div>
      </header>
      <dl>
        <div><dt>{t('taxonomyStrains')}</dt><dd>{species.strain_count}</dd></div>
        <div><dt>{t('taxonomyAphiaId')}</dt><dd>{species.worms_aphia_id ?? '—'}</dd></div>
      </dl>
      <LanguageCoverage languages={languages} translations={species.translations} t={t} />
    </article>
  );
}

function StrainCard({
  strain,
  languages,
  t,
  onEdit,
}: {
  key?: string | number;
  strain: StrainReference;
  languages: ReferenceLanguage[];
  t: Translator;
  onEdit: () => void;
}) {
  const localizedName = preferredName(strain.translations, languages);
  return (
    <article className="taxonomy-reference-card strain-reference-card">
      <header>
        <div>
          <h3>{strain.code}</h3>
          <p>{localizedName || strain.species_scientific_name}</p>
        </div>
        <div className="taxonomy-card-actions">
          {strain.origin_code ? <span className="reference-code">{strain.origin_code}</span> : null}
          <button className="taxonomy-edit-button" type="button" onClick={onEdit}>{t('taxonomyEdit')}</button>
        </div>
      </header>
      <dl>
        <div><dt>{t('taxonomySpecies')}</dt><dd>{strain.species_scientific_name}</dd></div>
        <div><dt>{t('taxonomyStrainNumber')}</dt><dd>{strain.number ?? '—'}</dd></div>
      </dl>
      <LanguageCoverage languages={languages} translations={strain.translations} t={t} />
    </article>
  );
}

function LanguageCoverage({
  languages,
  translations,
  t,
}: {
  languages: ReferenceLanguage[];
  translations: LocalizedReferenceValues;
  t: Translator;
}) {
  return (
    <div className="reference-language-coverage" aria-label={t('taxonomyTranslationCoverage')}>
      {languages.map((language) => (
        <span
          className={translations[language.code]?.name ? 'complete' : ''}
          key={language.code}
          title={language.label}
        >
          {language.code.toUpperCase()}
        </span>
      ))}
    </div>
  );
}

function createTranslationState(
  languages: ReferenceLanguage[],
  existingTranslations: LocalizedReferenceValues = {},
) {
  return Object.fromEntries(
    languages.map((language) => [
      language.code,
      { ...EMPTY_TRANSLATION, ...existingTranslations[language.code] },
    ]),
  );
}

function cleanTranslations(translations: LocalizedReferenceValues) {
  return Object.fromEntries(
    Object.entries(translations).map(([language, value]) => [
      language,
      { name: value.name.trim(), description: value.description.trim() },
    ]),
  );
}

function preferredName(translations: LocalizedReferenceValues, languages: ReferenceLanguage[]) {
  const interfaceLanguage = document.documentElement.lang.split('-')[0];
  if (translations[interfaceLanguage]?.name) {
    return translations[interfaceLanguage].name;
  }
  const requiredLanguage = languages.find((language) => language.required)?.code;
  if (requiredLanguage && translations[requiredLanguage]?.name) {
    return translations[requiredLanguage].name;
  }
  return Object.values(translations).find((translation) => translation.name)?.name ?? '';
}

function compareStrains(first: StrainReference, second: StrainReference) {
  return first.species_scientific_name.localeCompare(second.species_scientific_name)
    || first.code.localeCompare(second.code);
}
