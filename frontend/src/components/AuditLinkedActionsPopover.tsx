import { useCallback, useEffect, useLayoutEffect, useState } from 'react';
import { createPortal } from 'react-dom';
import { X } from 'lucide-react';

import { apiGet } from '../api/client';
import { useAnchoredPopover } from '../hooks/useAnchoredPopover';
import type { Language, Translator } from '../i18n';
import type { EditableMeasurement } from '../types';
import {
  fillTemplate,
  formatAuditDateTime,
  getAccountDisplayLabel,
  getAuditTargetLabel,
} from '../utils/auditPresentation';
import { getErrorMessage } from '../utils/errors';
import type { AdminAuditLogEntry } from './AdminAuditSection';
import { AuditBusinessNote, AuditInlineBusinessSummary, AuditPrimarySummary } from './AuditTimeline';
import { RowActionMenu, type RowActionMenuItem } from './RowActionMenu';
import SkeletonRows from './SkeletonRows';

type AuditRowAction = 'correct-measurement' | 'view-linked-actions';

type LinkedAuditResponse = {
  results: AdminAuditLogEntry[];
};

export default function AuditLinkedActionsPopover({
  entry,
  language,
  onEditMeasurement,
  t,
}: {
  entry: AdminAuditLogEntry;
  language: Language;
  onEditMeasurement: (measurement: EditableMeasurement) => void;
  t: Translator;
}) {
  const [isLinkedOpen, setIsLinkedOpen] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const closeLinked = useCallback((restoreFocus = false) => {
    setIsLinkedOpen(false);
    if (restoreFocus) linkedAnchorRef.current?.focus({ preventScroll: true });
  }, []);
  const {
    anchorRef: linkedAnchorRef,
    panelRef,
    position,
    id,
  } = useAnchoredPopover<HTMLButtonElement>(isLinkedOpen, closeLinked, 'end');

  const actions: RowActionMenuItem<AuditRowAction>[] = [];
  if (entry.editable_measurement) {
    actions.push({ action: 'correct-measurement', label: t('auditCorrectMeasurement') });
  }
  if (entry.related_action_count > 0) {
    actions.push({ action: 'view-linked-actions', label: t('auditViewLinkedActions') });
  }

  useLayoutEffect(() => {
    if (isLinkedOpen && position.visibility === 'visible') {
      panelRef.current?.querySelector<HTMLElement>('[data-linked-heading]')?.focus();
    }
  }, [isLinkedOpen, panelRef, position.visibility]);

  if (!actions.length) return null;

  const boxCode = entry.box_reference?.global_code
    ?? entry.editable_measurement?.box_code
    ?? getAuditTargetLabel(entry);

  return (
    <>
      <RowActionMenu
        actions={actions}
        ariaLabel={fillTemplate(t('auditRowActions'), { target: boxCode || t('auditObjectMeasurement') })}
        onAction={(action) => {
          if (action === 'correct-measurement' && entry.editable_measurement) {
            onEditMeasurement(entry.editable_measurement);
            return;
          }
          if (action === 'view-linked-actions') {
            setAttempt((current) => current + 1);
            setIsLinkedOpen(true);
          }
        }}
        triggerRef={linkedAnchorRef}
      />
      {isLinkedOpen ? createPortal(
        <div
          className="anchored-popover audit-linked-actions-popover"
          id={id}
          ref={panelRef}
          role="dialog"
          aria-labelledby={`${id}-title`}
          style={position}
        >
          <header className="audit-linked-actions-heading">
            <h3 id={`${id}-title`} data-linked-heading tabIndex={-1}>
              {fillTemplate(t('auditLinkedActionsTitle'), { box: boxCode || '-' })}
            </h3>
            <button type="button" aria-label={t('close')} onClick={() => closeLinked(true)}>
              <X aria-hidden="true" size={17} />
            </button>
          </header>
          <LinkedAuditContent entryId={entry.id} attempt={attempt} language={language} t={t} />
        </div>,
        document.body,
      ) : null}
    </>
  );
}

function LinkedAuditContent({
  attempt,
  entryId,
  language,
  t,
}: {
  attempt: number;
  entryId: number;
  language: Language;
  t: Translator;
}) {
  const [entries, setEntries] = useState<AdminAuditLogEntry[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [retry, setRetry] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    setEntries(null);
    setError(null);
    void apiGet<LinkedAuditResponse>(`/api/accounts/audit-log/${entryId}/linked/`, { signal: controller.signal })
      .then((response) => {
        if (!controller.signal.aborted) setEntries(response.results);
      })
      .catch((requestError) => {
        if (!controller.signal.aborted) setError(getErrorMessage(requestError));
      });
    return () => controller.abort();
  }, [attempt, entryId, retry]);

  if (error) {
    return (
      <div className="audit-linked-actions-state" role="alert">
        <p>{error}</p>
        <button type="button" onClick={() => setRetry((current) => current + 1)}>{t('profileActionsRetry')}</button>
      </div>
    );
  }
  if (!entries) {
    return <div className="audit-linked-actions-state" aria-live="polite"><SkeletonRows count={3} /></div>;
  }
  if (!entries.length) {
    return <p className="audit-linked-actions-state">{t('auditLinkedActionsEmpty')}</p>;
  }

  return (
    <ol className="audit-linked-actions-list">
      {entries.map((linkedEntry) => {
        const author = getAccountDisplayLabel(linkedEntry.user_display);
        return (
          <li key={linkedEntry.id}>
            <time dateTime={linkedEntry.effective_at}>{formatAuditDateTime(linkedEntry.effective_at)}</time>
            {author ? (
              <span className="audit-linked-action-author">
                {fillTemplate(t('auditLinkedActionAuthor'), { name: author })}
              </span>
            ) : null}
            <AuditPrimarySummary
              boxReference={linkedEntry.box_reference}
              className="audit-linked-action-summary"
              entry={linkedEntry}
              language={language}
              t={t}
            />
            <AuditInlineBusinessSummary details={linkedEntry.business_details} t={t} />
            <AuditBusinessNote details={linkedEntry.business_details} />
          </li>
        );
      })}
    </ol>
  );
}
