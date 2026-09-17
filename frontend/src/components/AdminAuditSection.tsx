import { useEffect, useMemo, useRef, useState } from 'react';

import { apiGet } from '../api/client';
import type { Language, Translator } from '../i18n';
import type {
  AuditBoxReference,
  AuditBusinessDetails,
  AuditContext,
  AuditFamily,
  EditableMeasurement,
} from '../types';
import {
  formatAuditTime,
  getAccountDisplayLabel,
  getAuditBoxSummaryParts,
  getAuditEditedMark,
  getAuditFamilyLabel,
  getAuditTargetLabel,
  groupAuditEntriesByDay,
  hasAuditSubcultureSummary,
} from '../utils/auditPresentation';
import {
  applyAdminAuditOutcome,
  buildAdminAuditQuery,
  createAdminAuditState,
  hasAdminAuditBusinessDetails,
  type AdminAuditState,
} from '../utils/adminAudit';

import { getErrorMessage } from '../utils/errors';
import {
  AuditBusinessDetail,
  AuditBusinessNote,
  AuditContextSummary,
  AuditDayHeading,
  AuditDisclosureButton,
  AuditInlineBusinessSummary,
  AuditPrimarySummary,
} from './AuditTimeline';
import AuditLinkedActionsPopover from './AuditLinkedActionsPopover';
import BoxTrackingPreview from './BoxTrackingPreview';
import SkeletonRows from './SkeletonRows';

export type AdminAuditLogEntry = {
  id: number;
  created_at: string;
  organization: string | null;
  user: string | null;
  user_display: string | null;
  action: string;
  action_label: string;
  family: AuditFamily;
  object_type: string;
  object_id: string;
  description: string;
  effective_at: string;
  edited_at: string | null;
  edited_by: string | null;
  edited_by_display: string | null;
  business_details: AuditBusinessDetails;
  box_reference: AuditBoxReference | null;
  context: AuditContext;
  editable_measurement: EditableMeasurement | null;
  related_action_count: number;
};

type AdminAuditFamilyOption = {
  key: AuditFamily;
  count: number;
};

type AdminAuditLogResponse = {
  results: AdminAuditLogEntry[];
  limit?: number;
  offset?: number;
  has_more?: boolean;
  next_offset?: number | null;
  total_count?: number;
  family_options?: AdminAuditFamilyOption[];
};

const ADMIN_AUDIT_PAGE_SIZE = 40;
// Presentation order only. The backend family taxonomy is unchanged; the
// references family keeps its audit rows but has no filter pill for now.
const ADMIN_AUDIT_FAMILY_ORDER: readonly AuditFamily[] = [
  'measurements',
  'boxes',
  'subcultures',
  'transfers',
  'environment',
  'exports',
  'accounts',
];

export default function AdminAuditSection({
  activeOrganizationId,
  language,
  onEditMeasurement,
  onOpenBox,
  t,
}: {
  activeOrganizationId: number;
  language: Language;
  onEditMeasurement: (measurement: EditableMeasurement) => void;
  onOpenBox: (boxId: number, code: string) => void;
  t: Translator;
}) {
  const [state, setState] = useState<AdminAuditState<AdminAuditLogEntry>>(
    () => createAdminAuditState(activeOrganizationId),
  );
  const [familyOptions, setFamilyOptions] = useState<AdminAuditFamilyOption[] | null>(null);
  const [familyFilter, setFamilyFilter] = useState<AuditFamily | ''>('');
  const [dateFilter, setDateFilter] = useState('');
  const [expandedEntryId, setExpandedEntryId] = useState<number | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [isLoadingMore, setIsLoadingMore] = useState(false);
  const requestGeneration = useRef(0);

  function invalidateAuditRequests() {
    requestGeneration.current += 1;
    setIsLoadingMore(false);
    setExpandedEntryId(null);
  }

  useEffect(() => {
    let isActive = true;
    const generation = requestGeneration.current + 1;
    requestGeneration.current = generation;
    setState(createAdminAuditState(activeOrganizationId));
    setExpandedEntryId(null);
    setIsLoading(true);
    setIsLoadingMore(false);

    async function loadAuditLog() {
      const query = buildAdminAuditQuery({
        family: familyFilter,
        date: dateFilter,
        includeOptions: true,
        includeTotal: true,
        limit: ADMIN_AUDIT_PAGE_SIZE,
        offset: 0,
      });

      try {
        const response = await apiGet<AdminAuditLogResponse>(`/api/accounts/audit-log/?${query}`);
        if (!isActive || requestGeneration.current !== generation) return;
        setState((current) => applyAdminAuditOutcome(current, {
          kind: 'initial-page',
          organizationId: activeOrganizationId,
          page: response,
        }));
        setFamilyOptions(response.family_options ?? []);
      } catch (requestError) {
        if (!isActive || requestGeneration.current !== generation) return;
        setState((current) => applyAdminAuditOutcome(current, {
          kind: 'initial-error',
          organizationId: activeOrganizationId,
          message: getErrorMessage(requestError),
        }));
      } finally {
        if (isActive && requestGeneration.current === generation) setIsLoading(false);
      }
    }

    void loadAuditLog();
    return () => {
      isActive = false;
    };
  }, [activeOrganizationId, dateFilter, familyFilter]);

  async function loadMoreAuditLog() {
    if (isLoadingMore || !state.hasMore || state.nextOffset == null) return;

    const generation = requestGeneration.current;
    const requestedOffset = state.nextOffset;
    const query = buildAdminAuditQuery({
      family: familyFilter,
      date: dateFilter,
      includeOptions: false,
      includeTotal: false,
      limit: ADMIN_AUDIT_PAGE_SIZE,
      offset: requestedOffset,
    });

    try {
      setIsLoadingMore(true);
      const response = await apiGet<AdminAuditLogResponse>(`/api/accounts/audit-log/?${query}`);
      if (requestGeneration.current !== generation) return;
      setState((current) => applyAdminAuditOutcome(current, {
        kind: 'load-more-page',
        organizationId: activeOrganizationId,
        page: response,
      }));
    } catch (requestError) {
      if (requestGeneration.current !== generation) return;
      setState((current) => applyAdminAuditOutcome(current, {
        kind: 'load-more-error',
        organizationId: activeOrganizationId,
        message: getErrorMessage(requestError),
      }));
    } finally {
      if (requestGeneration.current === generation) setIsLoadingMore(false);
    }
  }

  const dayGroups = useMemo(
    () => groupAuditEntriesByDay(state.entries, (entry) => entry.effective_at),
    [state.entries],
  );
  const counts = new Map(familyOptions?.map((option) => [option.key, option.count]) ?? []);
  const allCount = familyOptions?.reduce((total, option) => total + option.count, 0);

  function selectFamily(family: AuditFamily | '') {
    if (family === familyFilter) return;
    invalidateAuditRequests();
    setFamilyFilter(family);
  }

  return (
    <section className="admin-section admin-audit-section admin-audit-page" id="admin-history">
      <header className="box-inventory-heading">
        <div>
          <h2>{t('adminAuditTitle')}</h2>
        </div>
      </header>

      <div className="admin-audit-filter-bar">
        <div className="admin-audit-family-filters" role="group" aria-label={t('adminAuditFilterFamily')}>
          <FamilyFilterButton
            count={allCount}
            isSelected={familyFilter === ''}
            label={t('adminAuditAllFamilies')}
            onSelect={() => selectFamily('')}
          />
          {ADMIN_AUDIT_FAMILY_ORDER.map((family) => (
            <FamilyFilterButton
              count={counts.get(family)}
              isSelected={familyFilter === family}
              key={family}
              label={getAuditFamilyLabel(family, t)}
              onSelect={() => selectFamily(family)}
            />
          ))}
        </div>
        <label className="admin-audit-date-filter">
          <span className="sr-only">{t('adminAuditFilterDate')}</span>
          <input
            type="date"
            value={dateFilter}
            onChange={(event) => {
              invalidateAuditRequests();
              setDateFilter(event.target.value);
            }}
          />
        </label>
      </div>

      <div className="admin-audit-page-body">
        {isLoading ? (
          <SkeletonRows count={6} />
        ) : state.error && !state.entries.length ? (
          <p className="inline-error">{state.error}</p>
        ) : dayGroups.length ? (
          <div className="admin-audit-stream">
            {dayGroups.map((group) => (
              <section className="admin-audit-day-group" key={group.key}>
                <AuditDayHeading>{group.label}</AuditDayHeading>
                <div className="admin-audit-list">
                  {group.entries.map((entry) => (
                    <AdminAuditRow
                      entry={entry}
                      isExpanded={expandedEntryId === entry.id}
                      key={entry.id}
                      language={language}
                      onEditMeasurement={onEditMeasurement}
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
          <p className="muted compact-text">{t('adminAuditEmpty')}</p>
        )}

        {!isLoading && state.hasMore ? (
          <div className="admin-audit-pagination">
            {state.loadMoreError ? (
              <p className="admin-audit-pagination-error" role="alert">{state.loadMoreError}</p>
            ) : null}
            <button
              className="admin-audit-load-more"
              type="button"
              disabled={isLoadingMore}
              onClick={() => void loadMoreAuditLog()}
            >
              {isLoadingMore
                ? t('loading')
                : state.loadMoreError
                  ? t('profileActionsRetry')
                  : t('adminAuditLoadMore')}
            </button>
          </div>
        ) : null}
      </div>
    </section>
  );
}

function FamilyFilterButton({
  count,
  isSelected,
  label,
  onSelect,
}: {
  count: number | undefined;
  isSelected: boolean;
  label: string;
  onSelect: () => void;
}) {
  return (
    <button type="button" aria-pressed={isSelected} onClick={onSelect}>
      <span>{label}</span>
      {count !== undefined ? <small>{count}</small> : null}
    </button>
  );
}

function AdminAuditRow({
  entry,
  isExpanded,
  language,
  onEditMeasurement,
  onOpenBox,
  onToggle,
  t,
}: {
  entry: AdminAuditLogEntry;
  isExpanded: boolean;
  language: Language;
  onEditMeasurement: (measurement: EditableMeasurement) => void;
  onOpenBox: (boxId: number, code: string) => void;
  onToggle: () => void;
  t: Translator;
}) {
  const hasDetails = hasAdminAuditBusinessDetails(entry);
  const hasInlineBoxSummary = Boolean(entry.box_reference && getAuditBoxSummaryParts(entry, t));
  const hasSubcultureSummary = hasAuditSubcultureSummary(entry.business_details);
  const targetLabel = getAuditTargetLabel(entry);
  const detailsId = `admin-audit-details-${entry.id}`;

  return (
    <article className={isExpanded ? 'admin-audit-entry is-expanded' : 'admin-audit-entry'} data-family={entry.family}>
      <div className="admin-audit-row">
        <time className="admin-audit-time" dateTime={entry.effective_at}>
          {formatAuditTime(entry.effective_at)}
        </time>
        <strong className="admin-audit-author">{getAccountDisplayLabel(entry.user_display) || '-'}</strong>
        <div className="admin-audit-main">
          <AuditPrimarySummary
            boxReference={entry.box_reference}
            className="admin-audit-summary"
            entry={entry}
            language={language}
            onOpenBox={onOpenBox}
            t={t}
          />
          <AuditInlineBusinessSummary details={entry.business_details} t={t} />
          {hasInlineBoxSummary || hasSubcultureSummary ? null : entry.box_reference ? (
            <div className="admin-audit-target">
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
            <div className="admin-audit-target"><span>{targetLabel}</span></div>
          ) : null}
          <AuditContextSummary
            context={entry.context}
            hidePrimaryResource={hasInlineBoxSummary || hasSubcultureSummary}
            t={t}
          />
          <AuditBusinessNote details={entry.business_details} />
        </div>
        <div className="admin-audit-row-actions">
          <AuditLinkedActionsPopover
            entry={entry}
            language={language}
            onEditMeasurement={onEditMeasurement}
            t={t}
          />
          {hasDetails ? (
            <AuditDisclosureButton
              controls={detailsId}
              isExpanded={isExpanded}
              onToggle={onToggle}
              t={t}
            />
          ) : null}
        </div>
      </div>

      {hasDetails && isExpanded ? (
        <div className="admin-audit-details" id={detailsId}>
          <AuditBusinessDetail details={entry.business_details} id={`${detailsId}-business`} t={t} />
          {entry.edited_at ? (
            <p className="admin-audit-provenance">
              {getAuditEditedMark(entry, t)}
            </p>
          ) : null}
        </div>
      ) : null}
    </article>
  );
}
