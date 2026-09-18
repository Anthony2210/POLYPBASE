import type { ReactNode } from 'react';

import type { Language, Translator } from '../i18n';
import type { AuditBoxReference, AuditBusinessDetails, AuditContext } from '../types';
import {
  fillTemplate,
  formatAuditMetadataValue,
  getAuditBoxSummaryParts,
  getAuditBusinessDetailContent,
  getAuditBusinessNote,
  getAuditBusinessSummary,
  getAuditInlineBusinessItems,
  getAuditInitialPolypsLabel,
  getAuditMetadataKeyLabel,
  getAuditValueChange,
  hasAuditSubcultureSummary,
  isAuditNoteField,
  orderAuditFieldEntries,
} from '../utils/auditPresentation';
import BoxTrackingPreview from './BoxTrackingPreview';
import PolypbaseIcon from './PolypbaseIcon';

export function AuditDayHeading({ children }: { children: ReactNode }) {
  return <h3 className="audit-day-heading">{children}</h3>;
}

export function AuditPrimarySummary({
  boxReference,
  className,
  entry,
  language,
  onOpenBox,
  t,
}: {
  boxReference: AuditBoxReference | null | undefined;
  className: string;
  entry: Parameters<typeof getAuditBusinessSummary>[0];
  language: Language;
  onOpenBox?: (boxId: number, code: string) => void;
  t: Translator;
}) {
  const boxSummary = boxReference ? getAuditBoxSummaryParts(entry, t) : null;
  const subcultureDetails = entry.business_details?.type === 'subculture'
    && hasAuditSubcultureSummary(entry.business_details)
    ? entry.business_details
    : null;
  return (
    <p className={className}>
      {subcultureDetails ? (
        renderSubcultureSummary({
          boxReference,
          childCodes: subcultureDetails.child_global_codes ?? [],
          context: entry.context,
          language,
          onOpenBox,
          parentCode: subcultureDetails.parent_global_code ?? '',
          t,
        })
      ) : boxSummary && boxReference ? (
        <>
          {boxSummary[0]}
          {onOpenBox ? (
            <BoxTrackingPreview
              boxId={boxReference.id}
              code={boxReference.global_code}
              speciesName={boxReference.species_scientific_name}
              language={language}
              onOpenBox={onOpenBox}
              t={t}
            />
          ) : <span>{boxReference.global_code}</span>}
          {boxSummary[1]}
        </>
      ) : getAuditBusinessSummary(entry, t)}
    </p>
  );
}

function renderSubcultureSummary({
  boxReference,
  childCodes,
  context,
  language,
  onOpenBox,
  parentCode,
  t,
}: {
  boxReference: AuditBoxReference | null | undefined;
  childCodes: string[];
  context: AuditContext | null | undefined;
  language: Language;
  onOpenBox?: (boxId: number, code: string) => void;
  parentCode: string;
  t: Translator;
}) {
  const template = t(
    childCodes.length === 1
      ? 'auditSummarySubcultureOneChild'
      : 'auditSummarySubcultureManyChildren',
  );
  const childrenByCode = new Map(
    (context?.subculture?.children ?? []).map((child) => [child.global_code, child]),
  );
  const segments = template.split(/(\{children\}|\{parent\})/g);

  return segments.map((segment, segmentIndex) => {
    if (segment === '{children}') {
      return childCodes.map((code, childIndex) => {
        const reference = childrenByCode.get(code)?.box_reference;
        return (
          <span className="audit-box-reference-group" key={code}>
            {childIndex ? <span aria-hidden="true">, </span> : null}
            <AuditBoxReferenceLink
              code={code}
              language={language}
              onOpenBox={onOpenBox}
              reference={reference}
              t={t}
            />
          </span>
        );
      });
    }
    if (segment === '{parent}') {
      return (
        <AuditBoxReferenceLink
          code={parentCode}
          key="parent"
          language={language}
          onOpenBox={onOpenBox}
          reference={boxReference?.global_code === parentCode ? boxReference : undefined}
          t={t}
        />
      );
    }
    return <span key={`text-${segmentIndex}`}>{segment}</span>;
  });
}

function AuditBoxReferenceLink({
  code,
  language,
  onOpenBox,
  reference,
  t,
}: {
  code: string;
  language: Language;
  onOpenBox?: (boxId: number, code: string) => void;
  reference: AuditBoxReference | null | undefined;
  t: Translator;
}) {
  if (!reference || !onOpenBox) return <span>{code}</span>;
  return (
    <BoxTrackingPreview
      boxId={reference.id}
      code={reference.global_code}
      speciesName={reference.species_scientific_name}
      language={language}
      onOpenBox={onOpenBox}
      t={t}
    />
  );
}

export function AuditInlineBusinessSummary({
  details,
  t,
}: {
  details: AuditBusinessDetails | null | undefined;
  t: Translator;
}) {
  const items = getAuditInlineBusinessItems(details, t);
  if (!items.length) return null;
  return (
    <p className="audit-inline-business-summary">
      {items.map((item, index) => (
        <span className="audit-inline-business-item" key={item.key}>
          {index ? <span className="audit-inline-separator" aria-hidden="true">/</span> : null}
          {item.showLabel === false ? null : <span className="audit-inline-label">{item.label} </span>}
          {item.before !== undefined && item.after !== undefined ? (
            <>
              <span className="sr-only">{t('auditPrevious')}: </span>
              <span className="audit-inline-value">{item.before}</span>
              <span className="audit-change-arrow" aria-hidden="true">→</span>
              <span className="sr-only">{t('auditNew')}: </span>
              <span className="audit-inline-value">{item.after}</span>
            </>
          ) : <span className="audit-inline-value">{item.value}</span>}
          {item.unit ? <span className="audit-inline-unit">{item.unit}</span> : null}
        </span>
      ))}
    </p>
  );
}

export function AuditDisclosureButton({
  controls,
  isExpanded,
  onToggle,
  t,
}: {
  controls: string;
  isExpanded: boolean;
  onToggle: () => void;
  t: Translator;
}) {
  return (
    <button
      className="audit-disclosure-button"
      type="button"
      aria-controls={controls}
      aria-expanded={isExpanded}
      aria-label={isExpanded ? t('profileActionsHideDetails') : t('profileActionsDetails')}
      onClick={onToggle}
    >
      <PolypbaseIcon name="chevron-down" size={17} aria-hidden="true" />
    </button>
  );
}

export function AuditBusinessDetail({
  details,
  id,
  t,
}: {
  details: AuditBusinessDetails | null | undefined;
  id: string;
  t: Translator;
}) {
  const content = getAuditBusinessDetailContent(details);
  const valueEntries = content.values ? orderAuditFieldEntries(Object.entries(content.values)) : [];
  const changeEntries = content.changes ? orderAuditFieldEntries(Object.entries(content.changes)) : [];
  if (!valueEntries.length && !changeEntries.length) return null;

  return (
    <div className="audit-detail-region" id={id}>
      {changeEntries.length ? (
        <dl className="audit-change-list">
          {changeEntries.map(([key, value]) => {
            const change = getAuditValueChange(value);
            if (!change) return null;
            return (
              <div className="audit-change-row" key={key}>
                <dt>{getAuditMetadataKeyLabel(key, t)}</dt>
                <dd>
                  <span className="sr-only">{t('auditPrevious')}: </span>
                  <span>{formatAuditMetadataValue(change.before, t)}</span>
                  <span className="audit-change-arrow" aria-hidden="true">→</span>
                  <span className="sr-only">{t('auditNew')}: </span>
                  <span>{formatAuditMetadataValue(change.after, t)}</span>
                </dd>
              </div>
            );
          })}
        </dl>
      ) : null}
      {valueEntries.length ? (
        <dl className="audit-detail-list">
          {valueEntries.map(([key, value]) => (
            <div className={isAuditNoteField(key) ? 'audit-detail-item is-note' : 'audit-detail-item'} key={key}>
              <dt>{getAuditMetadataKeyLabel(key, t)}</dt>
              <dd>{formatAuditMetadataValue(value, t)}</dd>
            </div>
          ))}
        </dl>
      ) : null}
    </div>
  );
}

export function AuditBusinessNote({
  details,
}: {
  details: AuditBusinessDetails | null | undefined;
}) {
  const note = getAuditBusinessNote(details);
  return note ? <p className="audit-business-note">{note}</p> : null;
}

export function AuditContextSummary({
  context,
  hidePrimaryResource = false,
  t,
}: {
  context: AuditContext | null | undefined;
  hidePrimaryResource?: boolean;
  t: Translator;
}) {
  if (!context) return null;

  if (context.subculture) {
    if (hidePrimaryResource) return null;
    const parentCode = (context.subculture.parent_global_code ?? '').trim();
    const children = context.subculture.children ?? [];
    // Never invent a relation when neither the parent nor any child is known.
    if (!parentCode && !children.length) return null;
    return (
      <div className="audit-relation" data-relation="subculture">
        {!parentCode ? null : (
          <p>{fillTemplate(t('auditRelationSubcultureFrom'), { code: parentCode })}</p>
        )}
        {children.length ? (
          <ul>
            {children.map((child) => (
              <li key={child.global_code}>
                <span aria-hidden="true">↳</span>
                <span>{child.global_code}</span>
                {child.initial_polyp_count !== null ? (
                  <small>{getAuditInitialPolypsLabel(child.initial_polyp_count, t)}</small>
                ) : null}
              </li>
            ))}
          </ul>
        ) : null}
      </div>
    );
  }

  if (context.transfer) {
    const source = context.transfer.source_organization || t('auditRelationUnknownOrganization');
    const destination = context.transfer.destination_organization || t('auditRelationUnknownOrganization');
    return (
      <div className="audit-relation" data-relation="transfer">
        <p>{fillTemplate(t('auditRelationTransfer'), { source, destination })}</p>
        {context.transfer.source_global_code ? <small>{context.transfer.source_global_code}</small> : null}
      </div>
    );
  }

  return null;
}
