import { useEffect, useMemo, useState } from 'react';

import { apiGet } from '../api/client';
import type { Translator } from '../i18n';
import type { PersonalAction, PersonalActionsResponse } from '../types';
import {
  formatAuditChange,
  formatAuditDateTime,
  formatAuditMetadataValue,
  getAuditActionLabel,
  getAuditDescriptionLabel,
  getAuditMetadataKeyLabel,
  getAuditObjectTypeLabel,
  getPersonalResourceLabel,
  groupAuditEntriesByDay,
} from '../utils/auditPresentation';
import { getErrorMessage } from '../utils/errors';
import {
  applyPersonalActionsOutcome,
  EMPTY_PERSONAL_ACTIONS_STATE,
  getPersonalActionDetails,
  hasPersonalActionDetails,
  type PersonalActionsState,
} from '../utils/personalActions';
import SkeletonRows from './SkeletonRows';

const PROFILE_ACTIONS_PAGE_SIZE = 20;

export default function ProfileActionsSection({
  activeOrganizationId,
  t,
}: {
  activeOrganizationId: number | null;
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
      // The previous institution's actions and both error states must never
      // survive the switch to another institution.
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
      // The displayed actions stay visible; only the pagination reports the
      // failure, and nextOffset is kept so the retry requests the same page.
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
          <p>{t('profileActionsText')}</p>
        </div>
        <strong className="profile-actions-count">
          {isLoading ? null : (
            <>
              {state.entries.length} {t('profileActionsCount')}
              {state.hasMore ? ' +' : ''}
            </>
          )}
        </strong>
      </div>

      {isLoading ? (
        <SkeletonRows count={4} />
      ) : state.error && !state.entries.length ? (
        <p className="inline-error">{state.error}</p>
      ) : dayGroups.length ? (
        <div className="profile-actions-timeline">
          {dayGroups.map((group) => (
            <section className="profile-actions-day-group" key={group.key}>
              <h3>{group.label}</h3>
              <div className="profile-actions-list">
                {group.entries.map((entry) => (
                  <ProfileActionRow
                    entry={entry}
                    isExpanded={expandedEntryId === entry.id}
                    key={entry.id}
                    onToggle={() =>
                      setExpandedEntryId(expandedEntryId === entry.id ? null : entry.id)
                    }
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
  onToggle,
  t,
}: {
  entry: PersonalAction;
  isExpanded: boolean;
  onToggle: () => void;
  t: Translator;
}) {
  const { values, changes } = getPersonalActionDetails(entry.details);
  const hasDetails = hasPersonalActionDetails(entry.details);
  const targetLabel = getPersonalResourceLabel(entry.resource);

  const content = (
    <>
      <span className="profile-action-time">{formatAuditDateTime(entry.created_at)}</span>
      <span className="profile-action-main">
        <strong>{getAuditActionLabel(entry, t)}</strong>
        <small>{getAuditDescriptionLabel(entry, t)}</small>
      </span>
      <span className="profile-action-target">
        <strong>{targetLabel || '-'}</strong>
        <small>{getAuditObjectTypeLabel(entry.resource.type, t)}</small>
      </span>
    </>
  );

  return (
    <article className="profile-action-entry">
      {hasDetails ? (
        <button
          className="profile-action-row"
          type="button"
          aria-expanded={isExpanded}
          onClick={onToggle}
        >
          {content}
          <span className="profile-action-toggle">
            {isExpanded ? t('profileActionsHideDetails') : t('profileActionsDetails')}
          </span>
        </button>
      ) : (
        <div className="profile-action-row is-static">{content}</div>
      )}

      {hasDetails && isExpanded ? (
        <div className="profile-action-details">
          {values && Object.keys(values).length ? (
            <div>
              <small>{t('auditDetailValues')}</small>
              <dl>
                {Object.entries(values).map(([key, value]) => (
                  <div key={key}>
                    <dt>{getAuditMetadataKeyLabel(key, t)}</dt>
                    <dd>{formatAuditMetadataValue(value, t)}</dd>
                  </div>
                ))}
              </dl>
            </div>
          ) : null}
          {changes && Object.keys(changes).length ? (
            <div>
              <small>{t('auditDetailChanges')}</small>
              <dl>
                {Object.entries(changes).map(([key, value]) => (
                  <div key={key}>
                    <dt>{getAuditMetadataKeyLabel(key, t)}</dt>
                    <dd>{formatAuditChange(value, t)}</dd>
                  </div>
                ))}
              </dl>
            </div>
          ) : null}
        </div>
      ) : null}
    </article>
  );
}
