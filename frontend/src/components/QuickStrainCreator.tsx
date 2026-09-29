import { type FormEvent, useEffect, useMemo, useRef, useState } from 'react';

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
import { hasSpeciesCode, isMissingStrainSpeciesCode, type SpeciesCodeAssignment } from '../utils/strainSpeciesCode';
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
  onManageCodes,
}: {
  t: Translator;
  onClose: () => void;
  onCreated: (strain: QuickCreatedStrain) => void;
  onManageCodes?: () => void;
}) {
  const isMounted = useRef(true);
  const [references, setReferences] = useState<TaxonomyReferences | null>(null);
  const [assignments, setAssignments] = useState<SpeciesCodeAssignment[] | null>(null);
  const [codeLoadError, setCodeLoadError] = useState(false);
  const [codeReload, setCodeReload] = useState(0);
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
  const [missingAAAError, setMissingAAAError] = useState(false);

  useEffect(() => {
    isMounted.current = true;
    return () => { isMounted.current = false; };
  }, []);

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

  useEffect(() => {
    let isCurrent = true;
    setAssignments(null);
    setCodeLoadError(false);
    apiGet<SpeciesCodeAssignment[]>('/api/taxonomy/species-codes/')
      .then((result) => { if (isCurrent) setAssignments(result); })
      .catch(() => { if (isCurrent) setCodeLoadError(true); });
    return () => { isCurrent = false; };
  }, [codeReload]);

  const speciesHasCode = speciesId == null ? null : hasSpeciesCode(assignments, speciesId);
  const defaultLanguage = useMemo(
    () => references?.languages.find((language) => language.required)?.code ?? 'fr',
    [references?.languages],
  );

  async function createStrain(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (isSaving || speciesId == null || speciesHasCode === false) return;
    setIsSaving(true);
    setError(null);
    setMissingAAAError(false);

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
      if (!isMounted.current) return;
      onCreated({
        id: created.id,
        code: created.code,
        species_id: created.species,
        species_name: created.species_scientific_name,
      });
    } catch (requestError) {
      if (isMounted.current) {
        const missing = isMissingStrainSpeciesCode(requestError);
        setMissingAAAError(missing);
        setError(missing ? t('taxonomyStrainMissingAAA') : getErrorMessage(requestError, t('taxonomySaveError')));
      }
    } finally {
      if (isMounted.current) setIsSaving(false);
    }
  }

  async function createSpecies(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (isSaving) return;
    setIsSaving(true);
    setError(null);
    setMissingAAAError(false);

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
      if (!isMounted.current) return;
      setReferences((current) => current ? {
        ...current,
        species: [...current.species, created].sort((first, second) =>
          first.scientific_name.localeCompare(second.scientific_name)),
      } : current);
      setSpeciesId(created.id);
      setMode('strain');
      setNotes('');
    } catch (requestError) {
      if (isMounted.current) setError(getErrorMessage(requestError, t('taxonomySaveError')));
    } finally {
      if (isMounted.current) setIsSaving(false);
    }
  }

  return (
    <AdminActionPanel title={t('quickStrainTitle')} closeLabel={t('close')} onClose={onClose}>
      {!references && !error ? <PageLoader variant="admin" label={t('loading')} /> : null}
      {error && !missingAAAError ? <p className="inline-error" role="alert">{error}</p> : null}

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
                setMissingAAAError(false);
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
                setMissingAAAError(false);
              }}
            >
              {t('taxonomySpecies')}
            </button>
          </div>

          {mode === 'strain' ? (
            <form className="quick-reference-form" onSubmit={createStrain}>
              <label>
                <span>{t('taxonomySpeciesSelect')}</span>
                <select required value={speciesId ?? ''} onChange={(event) => {
                  setSpeciesId(Number(event.target.value));
                  setError(null);
                  setMissingAAAError(false);
                }}>
                  {references.species.map((species) => (
                    <option key={species.id} value={species.id}>{species.scientific_name}</option>
                  ))}
                </select>
              </label>
              {assignments === null && !codeLoadError ? <p role="status">{t('taxonomyLocalCodeLoading')}</p> : null}
              {codeLoadError ? (
                <p className="inline-error" role="alert">
                  {t('taxonomyLocalCodeLoadError')}{' '}
                  <button type="button" onClick={() => setCodeReload((current) => current + 1)}>{t('taxonomyRetry')}</button>
                </p>
              ) : null}
              {(speciesHasCode === false || missingAAAError) ? (
                <p className="inline-error" role="alert">
                  {t('taxonomyStrainMissingAAA')}{' '}
                  {onManageCodes ? <button type="button" onClick={onManageCodes}>{t('taxonomyGoToSharedReferences')}</button> : null}
                </p>
              ) : null}
              <button className="quick-reference-inline-action" type="button" onClick={() => { setMode('species'); setMissingAAAError(false); }}>
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
              <button className="primary-button quick-reference-submit" disabled={isSaving || speciesId == null || speciesHasCode === false} type="submit">
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
