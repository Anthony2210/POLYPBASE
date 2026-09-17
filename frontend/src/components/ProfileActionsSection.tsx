import { useEffect, useMemo, useState } from 'react';

import { apiGet } from '../api/client';
import type { Language, Translator } from '../i18n';
import type { PersonalAction, PersonalActionsResponse } from '../types';
import {
  formatAuditTime,
  getAuditBoxSummaryParts,
  getPersonalResourceLabel,
  groupAuditEntriesByDay,
  hasAuditSubcultureSummary,
} from '../utils/auditPresentation';
import { getErrorMessage } from '../utils/errors';
import {
  applyPersonalActionsOutcome,
  EMPTY_PERSONAL_ACTIONS_STATE,
  hasPersonalActionDetails,
  type PersonalActionsState,
} from '../utils/personalActions';
import {
  AuditBusinessDetail,
  AuditBusinessNote,
  AuditContextSummary,
  AuditDayHeading,
  AuditInlineBusinessSummary,
  AuditPrimarySummary,
  AuditDisclosureButton,
} from './AuditTimeline';
import BoxTrackingPreview from './BoxTrackingPreview';
import SkeletonRows from './SkeletonRows';

const PROFILE_ACTIONS_PAGE_SIZE = 20;

export default function ProfileActionsSection({
  activeOrganizationId,
  language,
  onOpenBox,
  t,
}: {
  activeOrganizationId: number | null;
  language: Language;
  onOpenBox: (boxId: number, code: string) => void;
  t: Translator;
}) {
  const [state, setState] = useState<PersonalActionsState>(EMPTY_PERSONAL_ACTIONS_STATE);
  const [expandedEntryId, setExpandedEntryId] = useState<number | null>(null);
  const [isLoading, setIsLoading] = useState(activeOrganizationId != null);
  const [isLoadingMore, setIsLoadingMore] = useState(false);

  useEffect(() => {
    if (activeOrganizationId == null) {
      setState(EMPTY_PERSONAL_ACTIONS_STATE);
      setExpandedEntryId(null);
      setIsLoading(false);
      return;
    }

    let isActive = true;

    async function loadActions() {
      setState(EMPTY_PERSONAL_ACTIONS_STATE);
      setExpandedEntryId(null);
      setIsLoading(true);

      try {
        const page = await apiGet<PersonalActionsResponse>(
          `/api/profile/actions/?limit=${PROFILE_ACTIONS_PAGE_SIZE}&offset=0`,
        );
        if (!isActive) return;
        setState((current) => applyPersonalActionsOutcome(current, { kind: 'initial-page', page }));
      } catch (requestError) {
        if (!isActive) return;
        setState((current) =>
          applyPersonalActionsOutcome(current, {
            kind: 'initial-error',
            message: getErrorMessage(requestError),
          }),
        );
      } finally {
        if (isActive) setIsLoading(false);
      }
    }

    void loadActions();
    return () => {
      isActive = false;
    };
  }, [activeOrganizationId]);

  async function loadMoreActions() {
    if (isLoadingMore || !state.hasMore || state.nextOffset == null) return;

    try {
      setIsLoadingMore(true);
      const page = await apiGet<PersonalActionsResponse>(
        `/api/profile/actions/?limit=${PROFILE_ACTIONS_PAGE_SIZE}&offset=${state.nextOffset}`,
      );
      setState((current) => applyPersonalActionsOutcome(current, { kind: 'load-more-page', page }));
    } catch (requestError) {
      setState((current) =>
        applyPersonalActionsOutcome(current, {
          kind: 'load-more-error',
          message: getErrorMessage(requestError),
        }),
      );
    } finally {
      setIsLoadingMore(false);
    }
  }

  const dayGroups = useMemo(
    () => groupAuditEntriesByDay(state.entries, (entry) => entry.created_at),
    [state.entries],
  );

  return (
    <section className="profile-block profile-actions">
      <div className="section-title">
        <div>
          <h2>{t('profileActionsTitle')}</h2>
        </div>
      </div>

      {isLoading ? (
        <SkeletonRows count={4} />
      ) : state.error && !state.entries.length ? (
        <p className="inline-error">{state.error}</p>
      ) : dayGroups.length ? (
        <div className="profile-actions-stream">
          {dayGroups.map((group) => (
            <section className="profile-actions-day-group" key={group.key}>
              <AuditDayHeading>{group.label}</AuditDayHeading>
              <div className="profile-actions-list">
                {group.entries.map((entry) => (
                  <ProfileActionRow
                    entry={entry}
                    isExpanded={expandedEntryId === entry.id}
                    key={entry.id}
                    language={language}
                    onOpenBox={onOpenBox}
                    onToggle={() => setExpandedEntryId(expandedEntryId === entry.id ? null : entry.id)}
                    t={t}
                  />
                ))}
              </div>
            </section>
          ))}
        </div>
      ) : (
        <p className="muted compact-text">{t('profileActionsEmpty')}</p>
      )}

      {!isLoading && state.hasMore ? (
        <div className="profile-actions-pagination">
          {state.loadMoreError ? (
            <p className="profile-actions-pagination-error" role="alert">
              {state.loadMoreError}
            </p>
          ) : null}
          <button
            className="profile-actions-load-more"
            type="button"
            disabled={isLoadingMore}
            onClick={() => void loadMoreActions()}
          >
            {isLoadingMore
              ? t('loading')
              : state.loadMoreError
                ? t('profileActionsRetry')
                : t('adminAuditLoadMore')}
          </button>
        </div>
      ) : null}
    </section>
  );
}

function ProfileActionRow({
  entry,
  isExpanded,
  language,
  onOpenBox,
  onToggle,
  t,
}: {
  entry: PersonalAction;
  isExpanded: boolean;
  language: Language;
  onOpenBox: (boxId: number, code: string) => void;
  onToggle: () => void;
  t: Translator;
}) {
  const hasDetails = hasPersonalActionDetails(entry.business_details);
  const hasInlineBoxSummary = Boolean(entry.box_reference && getAuditBoxSummaryParts(entry, t));
  const hasSubcultureSummary = hasAuditSubcultureSummary(entry.business_details);
  const targetLabel = getPersonalResourceLabel(entry.resource);
  const detailsId = `profile-action-details-${entry.id}`;

  return (
    <article className={isExpanded ? 'profile-action-entry is-expanded' : 'profile-action-entry'} data-family={entry.family}>
      <div className="profile-action-row">
        <time className="profile-action-time" dateTime={entry.created_at}>
          {formatAuditTime(entry.created_at)}
        </time>
        <div className="profile-action-main">
          <AuditPrimarySummary
            boxReference={entry.box_reference}
            className="profile-action-summary"
            entry={entry}
            language={language}
            onOpenBox={onOpenBox}
            t={t}
          />
          <AuditInlineBusinessSummary details={entry.business_details} t={t} />
          {hasInlineBoxSummary || hasSubcultureSummary ? null : entry.box_reference ? (
            <div className="profile-action-target">
              <BoxTrackingPreview
                boxId={entry.box_reference.id}
                code={entry.box_reference.global_code}
                speciesName={entry.box_reference.species_scientific_name}
                language={language}
                onOpenBox={onOpenBox}
                t={t}
              />
            </div>
          ) : targetLabel ? (
            <div className="profile-action-target"><span>{targetLabel}</span></div>
          ) : null}
          <AuditContextSummary
            context={entry.context}
            hidePrimaryResource={hasInlineBoxSummary || hasSubcultureSummary}
            t={t}
          />
          <AuditBusinessNote details={entry.business_details} />
        </div>
        {hasDetails ? (
          <AuditDisclosureButton
            controls={detailsId}
            isExpanded={isExpanded}
            onToggle={onToggle}
            t={t}
          />
        ) : null}
      </div>
      {hasDetails && isExpanded ? (
        <AuditBusinessDetail details={entry.business_details} id={detailsId} t={t} />
      ) : null}
    </article>
  );
}
