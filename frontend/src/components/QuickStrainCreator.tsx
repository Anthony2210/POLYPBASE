import { type FormEvent, useEffect, useMemo, useState } from 'react';

import { apiGet, apiPost } from '../api/client';
import type { Translator } from '../i18n';
import type {
  SpeciesReference,
  SpeciesReferencePayload,
  StrainReference,
  StrainReferencePayload,
  TaxonomyReferences,
} from '../types/admin';
import { getErrorMessage } from '../utils/errors';
import AdminActionPanel from './AdminActionPanel';
import PolypbaseIcon from './PolypbaseIcon';
import PageLoader from './PageLoader';

export type QuickCreatedStrain = {
  id: number;
  code: string;
  species_id: number;
  species_name: string;
};

type QuickReferenceMode = 'strain' | 'species';

export default function QuickStrainCreator({
  t,
  onClose,
  onCreated,
}: {
  t: Translator;
  onClose: () => void;
  onCreated: (strain: QuickCreatedStrain) => void;
}) {
  const [references, setReferences] = useState<TaxonomyReferences | null>(null);
  const [mode, setMode] = useState<QuickReferenceMode>('strain');
  const [speciesId, setSpeciesId] = useState<number | null>(null);
  const [strainCode, setStrainCode] = useState('');
  const [strainName, setStrainName] = useState('');
  const [strainNumber, setStrainNumber] = useState('');
  const [originCode, setOriginCode] = useState('');
  const [scientificName, setScientificName] = useState('');
  const [speciesCode, setSpeciesCode] = useState('');
  const [speciesName, setSpeciesName] = useState('');
  const [notes, setNotes] = useState('');
  const [isSaving, setIsSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let isCurrent = true;
    apiGet<TaxonomyReferences>('/api/taxonomy/references/')
      .then((data) => {
        if (!isCurrent) return;
        setReferences(data);
        setSpeciesId(data.species[0]?.id ?? null);
      })
      .catch((requestError) => {
        if (isCurrent) setError(getErrorMessage(requestError, t('taxonomyLoadError')));
      });
    return () => {
      isCurrent = false;
    };
  }, [t]);

  const defaultLanguage = useMemo(
    () => references?.languages.find((language) => language.required)?.code ?? 'fr',
    [references?.languages],
  );

  async function createStrain(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (isSaving || speciesId == null) return;
    setIsSaving(true);
    setError(null);

    const payload: StrainReferencePayload = {
      species: speciesId,
      code: strainCode.trim(),
      number: strainNumber ? Number(strainNumber) : null,
      origin_code: originCode.trim(),
      notes: notes.trim(),
      translations: {
        [defaultLanguage]: { name: strainName.trim(), description: '' },
      },
    };

    try {
      const created = await apiPost<StrainReference>('/api/taxonomy/strains/', payload);
      onCreated({
        id: created.id,
        code: created.code,
        species_id: created.species,
        species_name: created.species_scientific_name,
      });
    } catch (requestError) {
      setError(getErrorMessage(requestError, t('taxonomySaveError')));
    } finally {
      setIsSaving(false);
    }
  }

  async function createSpecies(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (isSaving) return;
    setIsSaving(true);
    setError(null);

    const payload: SpeciesReferencePayload = {
      scientific_name: scientificName.trim(),
      genus_species_code: speciesCode.trim(),
      worms_aphia_id: null,
      is_described: true,
      notes: notes.trim(),
      translations: {
        [defaultLanguage]: { name: speciesName.trim(), description: '' },
      },
    };

    try {
      const created = await apiPost<SpeciesReference>('/api/taxonomy/species/', payload);
      setReferences((current) => current ? {
        ...current,
        species: [...current.species, created].sort((first, second) =>
          first.scientific_name.localeCompare(second.scientific_name)),
      } : current);
      setSpeciesId(created.id);
      setMode('strain');
      setNotes('');
    } catch (requestError) {
      setError(getErrorMessage(requestError, t('taxonomySaveError')));
    } finally {
      setIsSaving(false);
    }
  }

  return (
    <AdminActionPanel title={t('quickStrainTitle')} closeLabel={t('close')} onClose={onClose}>
      {!references && !error ? <PageLoader variant="admin" label={t('loading')} /> : null}
      {error ? <p className="inline-error">{error}</p> : null}

      {references ? (
        <>
          <div className="quick-reference-tabs segmented-control" role="tablist">
            <button
              className={mode === 'strain' ? 'active' : ''}
              type="button"
              role="tab"
              aria-selected={mode === 'strain'}
              onClick={() => {
                setMode('strain');
                setError(null);
              }}
            >
              {t('taxonomyStrains')}
            </button>
            <button
              className={mode === 'species' ? 'active' : ''}
              type="button"
              role="tab"
              aria-selected={mode === 'species'}
              onClick={() => {
                setMode('species');
                setError(null);
              }}
            >
              {t('taxonomySpecies')}
            </button>
          </div>

          {mode === 'strain' ? (
            <form className="quick-reference-form" onSubmit={createStrain}>
              <label>
                <span>{t('taxonomySpeciesSelect')}</span>
                <select required value={speciesId ?? ''} onChange={(event) => setSpeciesId(Number(event.target.value))}>
                  {references.species.map((species) => (
                    <option key={species.id} value={species.id}>{species.scientific_name}</option>
                  ))}
                </select>
              </label>
              <button className="quick-reference-inline-action" type="button" onClick={() => setMode('species')}>
                <PolypbaseIcon name="plus" size={17} />
                {t('quickStrainNewSpecies')}
              </button>
              <div className="quick-reference-two-columns">
                <label>
                  <span>{t('taxonomyStrainCode')}</span>
                  <input required value={strainCode} onChange={(event) => setStrainCode(event.target.value.toUpperCase())} />
                </label>
                <label>
                  <span>{t('taxonomyNameByLanguage')}</span>
                  <input required value={strainName} onChange={(event) => setStrainName(event.target.value)} />
                </label>
                <label>
                  <span>{t('taxonomyStrainNumber')}</span>
                  <input min="1" type="number" value={strainNumber} onChange={(event) => setStrainNumber(event.target.value)} />
                </label>
                <label>
                  <span>{t('taxonomyOriginCode')}</span>
                  <input value={originCode} onChange={(event) => setOriginCode(event.target.value.toUpperCase())} />
                </label>
              </div>
              <label>
                <span>{t('taxonomyNotes')}</span>
                <textarea rows={3} value={notes} onChange={(event) => setNotes(event.target.value)} />
              </label>
              <button className="primary-button quick-reference-submit" disabled={isSaving || speciesId == null} type="submit">
                <PolypbaseIcon name="check" size={18} />
                {isSaving ? t('taxonomyCreating') : t('quickStrainCreateAndUse')}
              </button>
            </form>
          ) : (
            <form className="quick-reference-form" onSubmit={createSpecies}>
              <button className="quick-reference-inline-action" type="button" onClick={() => setMode('strain')}>
                <PolypbaseIcon name="chevron-left" size={17} />
                {t('quickStrainBackToStrain')}
              </button>
              <label>
                <span>{t('taxonomyScientificName')}</span>
                <input required value={scientificName} onChange={(event) => setScientificName(event.target.value)} />
              </label>
              <div className="quick-reference-two-columns">
                <label>
                  <span>{t('taxonomySpeciesCode')}</span>
                  <input value={speciesCode} onChange={(event) => setSpeciesCode(event.target.value.toUpperCase())} />
                </label>
                <label>
                  <span>{t('taxonomyNameByLanguage')}</span>
                  <input required value={speciesName} onChange={(event) => setSpeciesName(event.target.value)} />
                </label>
              </div>
              <label>
                <span>{t('taxonomyNotes')}</span>
                <textarea rows={3} value={notes} onChange={(event) => setNotes(event.target.value)} />
              </label>
              <button className="primary-button quick-reference-submit" disabled={isSaving} type="submit">
                <PolypbaseIcon name="plus" size={18} />
                {isSaving ? t('taxonomyCreating') : t('taxonomyNewSpecies')}
              </button>
            </form>
          )}
        </>
      ) : null}
    </AdminActionPanel>
  );
}
