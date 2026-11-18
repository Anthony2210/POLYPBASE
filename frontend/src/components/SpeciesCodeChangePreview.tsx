import { type FormEvent, useEffect, useMemo, useRef, useState } from 'react';

import { ApiError, apiGet } from '../api/client';

import type { Language, TranslationKey, Translator } from '../i18n';
import type { SpeciesStrain } from '../utils/speciesCatalog';
import {
  type CodeChangeAction,

  type CodeChangeIssueGroup,

  codeChangePreviewPath,

  getProposedCodeProblem,
  getProposedCodeRefusal,
  groupCodeChangeIssues,
  isPlanFor,
  normalizeSpeciesCode,
  type ProposedCodeProblem,
  type SpeciesCodeChangePlan,
  splitSpeciesCodePrefix,
} from '../utils/speciesCodeChange';
import BoxTrackingPreview from './BoxTrackingPreview';
import PolypbaseIcon from './PolypbaseIcon';

// Server reason codes (blockers, warnings and record-level reasons) to readable
// text. Codes are never shown verbatim; an unknown one gets a neutral sentence.
const REASON_KEYS: Record<string, TranslationKey> = {
  species_code_missing: 'speciesCodeChangeReasonCodeMissing',
  current_code_unusable: 'speciesCodeChangeReasonCurrentUnusable',
  code_unchanged: 'speciesCodeChangeReasonUnchanged',
  code_assigned_in_institution: 'speciesCodeChangeReasonAssigned',
  proposed_code_used_by_other_species: 'speciesCodeChangeReasonUsedByOtherSpecies',
  strain_used_by_another_institution: 'speciesCodeChangeReasonStrainShared',
  strain_without_owner: 'speciesCodeChangeReasonStrainUnowned',
  strain_owned_by_another_institution: 'speciesCodeChangeReasonStrainForeign',
  local_identity_mismatch: 'speciesCodeChangeReasonIdentityMismatch',
  prefix_mismatch: 'speciesCodeChangeReasonStrainPrefix',
  strain_prefix_mismatch: 'speciesCodeChangeReasonStrainPrefix',
  code_format: 'speciesCodeChangeReasonStrainFormat',
  noncanonical_strain_unchanged: 'speciesCodeChangeReasonStrainFormat',
  assignment_used_by_unplanned_strain: 'speciesCodeChangeReasonOtherStrains',
  strain_code_collision_in_batch: 'speciesCodeChangeReasonStrainBatchCollision',
  strain_code_collision: 'speciesCodeChangeReasonStrainCollision',
  box_namespace_reserved: 'speciesCodeChangeReasonNumberingReserved',
  box_namespace_in_use: 'speciesCodeChangeReasonNumberingInUse',
  box_local_id_collision: 'speciesCodeChangeReasonBoxCollision',
  box_local_id_historically_reserved: 'speciesCodeChangeReasonBoxHistoricalCollision',
  box_local_id_collision_in_batch: 'speciesCodeChangeReasonBoxBatchCollision',
  local_id_history_unresolved: 'speciesCodeChangeReasonHistoryUnresolved',
  reviewed_identity_protected: 'speciesCodeChangeReasonReviewedIdentity',
  local_id_strain_mismatch: 'speciesCodeChangeReasonBoxStrainMismatch',
  box_local_id_strain_mismatch: 'speciesCodeChangeReasonBoxStrainMismatch',
  strain_blocked: 'speciesCodeChangeReasonStrainBlocked',
  merged_alias_retained: 'speciesCodeChangeReasonMergedRecord',
  local_id_without_current_code: 'speciesCodeChangeReasonBoxWithoutCode',
  box_without_current_code_unchanged: 'speciesCodeChangeReasonBoxWithoutCode',
  proposed_code_used_outside_institution: 'speciesCodeChangeReasonUsedOutside',
  current_code_on_other_species_boxes: 'speciesCodeChangeReasonCurrentOnOtherSpecies',
  missing_global_id: 'speciesCodeChangeReasonMissingGlobalId',
};


const PROBLEM_KEYS: Record<ProposedCodeProblem, TranslationKey> = {
  required: 'speciesCodeChangeCodeRequired',
  invalid_format: 'speciesCodeChangeCodeInvalid',
  unchanged: 'speciesCodeChangeReasonUnchanged',
};


const BOX_PAGE_SIZE = 50;
const ISSUE_IDENTIFIER_LIMIT = 6;

const fill = (template: string, values: Record<string, string | number>) => (
  template.replace(/\{([a-z]+)\}/g, (match, name: string) => (name in values ? String(values[name]) : match))
);


function reasonText(code: string, t: Translator) {
  return t(REASON_KEYS[code] ?? 'speciesCodeChangeReasonOther');
}

type PreviewState =
  | { status: 'idle' }
  | { status: 'loading'; code: string }
  | { status: 'error'; code: string; message: TranslationKey; canRetry: boolean }
  | { status: 'ready'; plan: SpeciesCodeChangePlan };

// Read-only simulation of an institutional Species code change. It only ever
// issues GET requests to the preview endpoint: there is no apply action here.
export default function SpeciesCodeChangePreview({
  profileId, organizationId, currentCode, strains, language, onOpenBox, onClose, t,
}: {
  profileId: number;
  organizationId: number;
  organizationName: string;
  scientificName: string;
  currentCode: string;
  // Strains already loaded for this Species, for their number and collection location.
  strains: readonly SpeciesStrain[];
  language: Language;
  onOpenBox: (boxId: number, code: string) => void;
  onClose: () => void;
  t: Translator;
}) {
  const [input, setInput] = useState('');
  const [isValidationShown, setIsValidationShown] = useState(false);
  const [state, setState] = useState<PreviewState>({ status: 'idle' });
  const inputRef = useRef<HTMLInputElement>(null);
  // Only the latest request may publish; earlier ones are aborted and ignored.
  const requestRef = useRef<{ id: number; controller: AbortController | null }>({ id: 0, controller: null });

  function cancelRequest() {
    requestRef.current.id += 1;
    requestRef.current.controller?.abort();
    requestRef.current.controller = null;
  }

  // Another institution or Species never inherits an entry, request or result.
  useEffect(() => {
    setInput('');
    setIsValidationShown(false);
    setState({ status: 'idle' });
    inputRef.current?.focus();
    return cancelRequest;
  }, [organizationId, profileId]);

  const proposedCode = normalizeSpeciesCode(input);
  const problem = getProposedCodeProblem(input, currentCode);
  const scope = { organizationId, profileId, proposedCode };

  function changeInput(value: string) {
    // A result or a pending request belongs to the code it was asked for.
    cancelRequest();
    setInput(value);
    setIsValidationShown(false);
    setState({ status: 'idle' });
  }

  function preview() {
    setIsValidationShown(true);
    if (problem) return;
    cancelRequest();
    const controller = new AbortController();
    const requestId = requestRef.current.id;
    requestRef.current.controller = controller;
    const requested = { organizationId, profileId, proposedCode };
    setState({ status: 'loading', code: proposedCode });
    apiGet<SpeciesCodeChangePlan>(codeChangePreviewPath(profileId, proposedCode), { signal: controller.signal })
      .then((plan) => {
        if (requestRef.current.id !== requestId) return;
        // A plan computed for another institution, Species or code is refused.
        setState(isPlanFor(plan, requested)
          ? { status: 'ready', plan }
          : { status: 'error', code: proposedCode, message: 'speciesCodeChangeLoadError', canRetry: true });
      })
      .catch((error: unknown) => {
        if (requestRef.current.id !== requestId) return;
        const status = error instanceof ApiError ? error.status : null;
        const refusal = status === 400 && error instanceof ApiError ? getProposedCodeRefusal(error.data) : null;
        setState({
          status: 'error',
          code: proposedCode,
          message: status === 403 ? 'speciesCodeChangeForbidden'
            : status === 404 ? 'speciesDetailNotFound'
              : refusal ? PROBLEM_KEYS[refusal] : 'speciesCodeChangeLoadError',
          canRetry: status !== 403 && status !== 404 && !refusal,
        });
      });
  }

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    preview();
  }

  const isLoading = state.status === 'loading';
  const shownProblem = isValidationShown && problem ? PROBLEM_KEYS[problem] : null;
  const plan = state.status === 'ready' && isPlanFor(state.plan, scope) ? state.plan : null;

  return (
    <section className="code-change-panel" id="species-code-change" aria-labelledby="species-code-change-title">
      <header className="code-change-heading">
        <h3 id="species-code-change-title">{t('speciesCodeChangeTitle')}</h3>
        <button className="icon-button" type="button" aria-label={t('close')} title={t('close')} onClick={onClose}>
          <PolypbaseIcon name="close" size={18} aria-hidden="true" />
        </button>
      </header>


      <form className="code-change-form" onSubmit={handleSubmit} noValidate>
        <div className="code-change-field">
          <span className="code-change-field-label">{t('speciesCodeChangeCurrent')}</span>
          <span className="code-change-current">{currentCode}</span>
        </div>
        <span className="code-change-arrow" aria-hidden="true">→</span>
        <label className="code-change-field" htmlFor="species-code-change-input">
          <span className="code-change-field-label">{t('speciesCodeChangeProposed')}</span>
          <input
            ref={inputRef}
            id="species-code-change-input"
            value={proposedCode}
            maxLength={3}
            autoComplete="off"
            spellCheck={false}
            aria-invalid={shownProblem !== null}
            aria-describedby={shownProblem ? 'species-code-change-problem' : undefined}
            onChange={(event) => changeInput(event.target.value)}
          />
        </label>
        <button className="primary-button code-change-submit" type="submit" disabled={isLoading}>
          {t(isLoading ? 'speciesCodeChangeLoading' : 'speciesCodeChangeSubmit')}
        </button>
        {shownProblem ? <p id="species-code-change-problem" className="code-change-problem" role="alert">{t(shownProblem)}</p> : null}
      </form>

      <div className="code-change-live" role="status" aria-live="polite">
        {isLoading ? <p className="code-change-hint">{t('speciesCodeChangeLoading')}</p> : null}

      </div>

      {state.status === 'error' && state.code === proposedCode ? (
        <div className="species-admin-notice is-error" role="alert">
          <p>{t(state.message)}</p>
          {state.canRetry ? (
            <button className="secondary-button" type="button" onClick={preview}>{t('taxonomyRetry')}</button>
          ) : null}
        </div>
      ) : null}

      {plan ? (
        <SpeciesCodeChangeImpact plan={plan} strains={strains} language={language} onOpenBox={onOpenBox} t={t} />
      ) : null}
    </section>
  );
}

// The identity impact of one plan. It owns no request and no decision, so a
// later Species correction workflow can show the same plan presentation.
export function SpeciesCodeChangeImpact({ plan, strains, language, onOpenBox, t }: {
  plan: SpeciesCodeChangePlan;
  strains: readonly SpeciesStrain[];
  language: Language;
  onOpenBox: (boxId: number, code: string) => void;
  t: Translator;
}) {
  const [boxFilter, setBoxFilter] = useState<'all' | CodeChangeAction>('all');
  const [areAllBoxesShown, setAreAllBoxesShown] = useState(false);
  const planStrains = plan.strains ?? [];
  const planBoxes = plan.boxes ?? [];
  const summary = plan.summary ?? {};
  const blockers = useMemo(() => groupCodeChangeIssues(plan.blockers, plan), [plan]);
  const warnings = useMemo(() => groupCodeChangeIssues(plan.warnings, plan), [plan]);
  const strainContext = useMemo(() => new Map(strains.map((item) => [item.id, item])), [strains]);

  const boxActions = useMemo(() => new Set((plan.boxes ?? []).map((item) => item.action)), [plan]);

  // Another plan starts from its own complete Box list.
  useEffect(() => {
    setBoxFilter('all');
    setAreAllBoxesShown(false);
  }, [plan]);

  const matchingBoxes = boxFilter === 'all' ? planBoxes : planBoxes.filter((item) => item.action === boxFilter);
  const shownBoxes = areAllBoxesShown ? matchingBoxes : matchingBoxes.slice(0, BOX_PAGE_SIZE);
  const isEmpty = planStrains.length === 0 && planBoxes.length === 0;

  function strainLabel(strainId: number) {
    const known = strainContext.get(strainId);
    return known && known.strain_number !== null ? `${t('createBoxStrainLabel')} ${known.strain_number}` : null;
  }

  function fact(label: TranslationKey, value: number | undefined) {
    // A count the server did not supply is omitted; a reported zero is shown.
    if (typeof value !== 'number') return null;
    return (
      <div>
        <dt>{t(label)}</dt>
        <dd><strong>{value}</strong></dd>
      </div>
    );
  }

  const filters: Array<['all' | CodeChangeAction, TranslationKey, number | undefined]> = [
    ['all', 'speciesCodeChangeFilterAll', summary.boxes],
    ['rename', 'speciesCodeChangeActionRename', summary.boxes_to_rename],
    ['blocked', 'speciesCodeChangeActionBlocked', summary.boxes_blocked],
    ['unchanged', 'speciesCodeChangeActionUnchanged', summary.boxes_unchanged],
  ];

  return (
    <div className="code-change-impact">
      {!plan.feasible ? <p className="code-change-problem" role="alert">{t('speciesCodeChangeImpossible')}</p> : null}
      <dl className="code-change-facts">
        {fact('speciesCodeChangeStrainsConcerned', summary.strains)}
        {fact('speciesCodeChangeBoxesConcerned', summary.boxes)}
      </dl>

      {blockers.length ? (
        <IssueList titleId="code-change-blockers-title" title={t('speciesCodeChangeBlockers')} tone="blocker" groups={blockers} t={t} />
      ) : null}
      {warnings.length ? (
        <IssueList titleId="code-change-warnings-title" title={t('speciesCodeChangeWarnings')} tone="warning" groups={warnings} t={t} />
      ) : null}

      {planStrains.length ? (
        <section className="code-change-records" aria-labelledby="code-change-strains-title">
          <h4 id="code-change-strains-title">{t('speciesDetailStrains')}</h4>
          <table className="code-change-table">
            <thead>
              <tr>
                <th scope="col">{t('createBoxStrainLabel')}</th>
                <th scope="col">{t('speciesCodeChangeBefore')}</th>
                <th scope="col">{t('speciesCodeChangeAfter')}</th>

              </tr>
            </thead>
            <tbody>
              {planStrains.map((item) => {
                const location = strainContext.get(item.id)?.recorded_provenance_name;
                return (
                  <tr key={item.id} className={`is-${item.action}`}>
                    <th scope="row" className="code-change-strain">
                      <span>{strainLabel(item.id) ?? item.current_code}</span>
                      {/* Shown only when recorded and verified; absence is not filled in. */}
                      {location ? <small>{fill(t('speciesStrainRecordedProvenance'), { name: location })}</small> : null}
                    </th>
                    <td className="code-change-old">{item.current_code}</td>
                    <td><ProposedIdentifier value={item.proposed_code} action={item.action} speciesCode={plan.proposed_code} t={t} /></td>

                  </tr>
                );
              })}
            </tbody>
          </table>
        </section>
      ) : null}

      {planBoxes.length ? (
        <section className="code-change-records" aria-labelledby="code-change-boxes-title">
          <div className="code-change-records-heading">
            <h4 id="code-change-boxes-title">{t('speciesCodeChangeBoxes')}</h4>
            {boxActions.size > 1 ? (
              <div className="code-change-filters" role="group" aria-label={t('speciesCodeChangeFilterLabel')}>
                {filters.filter(([value]) => value === 'all' || boxActions.has(value)).map(([value, label, count]) => (
                  <button
                    key={value} type="button" aria-pressed={boxFilter === value}
                    className={boxFilter === value ? 'is-active' : undefined}
                    onClick={() => { setBoxFilter(value); setAreAllBoxesShown(false); }}
                  >
                    {t(label)}{typeof count === 'number' ? <span>{count}</span> : null}
                  </button>
                ))}
              </div>
            ) : null}
          </div>

          <table className="code-change-table">
            <thead>
              <tr>
                <th scope="col">{t('boxInventoryBox')}</th>
                <th scope="col">{t('speciesCodeChangeBefore')}</th>
                <th scope="col">{t('speciesCodeChangeAfter')}</th>
              </tr>
            </thead>
            <tbody>
              {shownBoxes.map((item) => (
                <tr key={item.id} className={`is-${item.action}`}>
                  <th scope="row" className="code-change-old">
                    {/* The real tracking preview of the real Box: its data never depends on the proposed code. */}
                    <BoxTrackingPreview
                      boxId={item.id} code={item.current_local_id}
                      speciesName={plan.institution_species.scientific_name}
                      language={language} onOpenBox={onOpenBox} t={t}
                    />
                  </th>
                  <td className="code-change-old">{item.current_local_id}</td>
                  <td><ProposedIdentifier value={item.proposed_local_id} action={item.action} speciesCode={plan.proposed_code} t={t} /></td>
                </tr>
              ))}
            </tbody>
          </table>
          {shownBoxes.length < matchingBoxes.length ? (
            <button className="secondary-button code-change-more" type="button" onClick={() => setAreAllBoxesShown(true)}>
              {fill(t('speciesCodeChangeShowAllBoxes'), { count: matchingBoxes.length })}
            </button>
          ) : null}
        </section>
      ) : null}

      {isEmpty ? <p className="species-detail-empty">{t('speciesCodeChangeEmpty')}</p> : null}

    </div>
  );
}

function ProposedIdentifier({ value, action, speciesCode, t }: {
  value: string | null;
  action: string;
  speciesCode: string;
  t: Translator;
}) {
  if (!value) return <span className="code-change-kept">{t('speciesCodeChangeKept')}</span>;
  const { code, rest } = splitSpeciesCodePrefix(value, speciesCode);
  return (
    <span className={action === 'blocked' ? 'code-change-proposed is-blocked' : 'code-change-proposed'}>
      {code ? <strong>{code}</strong> : null}{rest}
    </span>
  );
}

function IssueList({ titleId, title, tone, groups, t }: {
  titleId: string;
  title: string;
  tone: 'blocker' | 'warning';
  groups: CodeChangeIssueGroup[];
  t: Translator;
}) {
  return (
    <section className={`code-change-issues is-${tone}`} aria-labelledby={titleId}>
      <h4 id={titleId}>{title}<span>{groups.length}</span></h4>
      <ul>
        {groups.map((group) => {
          const named = group.identifiers.slice(0, ISSUE_IDENTIFIER_LIMIT);
          const hidden = group.recordCount - named.length;
          return (
            <li key={group.code}>
              <p>{reasonText(group.code, t)}</p>
              {/* Only a Species of this same institution is ever named by the server. */}
              {group.scientificName ? <small><em>{group.scientificName}</em></small> : null}
              {/* A record outside the institution is never named or described. */}
              {group.isOutsideInstitution ? <small>{t('speciesCodeChangeOutsideInstitution')}</small> : null}
              {group.recordCount > 0 ? (
                <small className="code-change-issue-records">
                  <span>{fill(t(group.recordCount === 1 ? 'speciesCodeChangeRecord' : 'speciesCodeChangeRecords'), { count: group.recordCount })}</span>
                  {named.map((identifier) => <code key={identifier}>{identifier}</code>)}
                  {named.length && hidden > 0 ? <span>{fill(t('speciesCodeChangeMoreRecords'), { count: hidden })}</span> : null}
                </small>
              ) : null}
            </li>
          );
        })}
      </ul>
    </section>
  );
}
