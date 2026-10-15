import { useEffect, useRef, useState } from 'react';
import { getOrganizationResourceSignal } from '../api/client';
import {
  applyMeasurements, canApplyPreview, diagnosticErrorKey, downloadImportResource, fieldLabels,
  inspectMeasurements, issueLabels, mappingComplete, outcomeLabels, previewMeasurements, summaryFields,
  type DiagnosticPreview, type DiagnosticValue, type ImportAcceptance, type ImportIssue, type Inspection, type Mapping,
} from '../api/measurementImport';
import type { Translator, TranslationKey } from '../i18n';

const PAGE_SIZE = 50;
const summaryLabels: Record<typeof summaryFields[number], TranslationKey> = {
  total_source_rows: 'csvSummaryTotal', ignored_rows: 'csvSummaryIgnored', create_candidates: 'csvSummaryCreate',
  already_matching: 'csvSummaryMatches', conflicts: 'csvSummaryConflicts', unknown_boxes: 'csvSummaryUnknown',
  invalid_or_blocked: 'csvSummaryInvalid', blocked_rows: 'csvSummaryBlocked',
};

export default function MeasurementImportPanel({ organizationId, isOperationCurrent, t }: {
  organizationId: number;
  isOperationCurrent: () => boolean;
  t: Translator;
}) {
  const [file, setFile] = useState<File | null>(null);
  const [delimiter, setDelimiter] = useState(',');
  const [inspection, setInspection] = useState<Inspection | null>(null);
  const [mapping, setMapping] = useState<Mapping>({});
  const [confirmed, setConfirmed] = useState(false);
  const [preview, setPreview] = useState<DiagnosticPreview | null>(null);
  const [busy, setBusy] = useState<'inspect' | 'preview' | 'apply' | 'download' | null>(null);
  const [importConfirmed, setImportConfirmed] = useState(false);
  const [acceptance, setAcceptance] = useState<ImportAcceptance | null>(null);
  const [error, setError] = useState<TranslationKey | null>(null);
  const [page, setPage] = useState(0);
  const generation = useRef(0);
  const request = useRef<AbortController | null>(null);
  const acceptedRequest = useRef(false);
  const fileInput = useRef<HTMLInputElement | null>(null);
  const context = getOrganizationResourceSignal();
  const currentOperation = useRef(isOperationCurrent);
  currentOperation.current = isOperationCurrent;

  function invalidate() {
    generation.current += 1;
    request.current?.abort();
    request.current = null;
    setPreview(null);
    setImportConfirmed(false);
    setAcceptance(null);
    acceptedRequest.current = false;
    setConfirmed(false);
    setError(null);
    setBusy(null);
    setPage(0);
  }

  useEffect(() => {
    const resetContext = () => {
      invalidate();
      setInspection(null);
      setMapping({});
      setFile(null);
      if (fileInput.current) fileInput.current.value = '';
    };
    context.addEventListener('abort', resetContext, { once: true });
    if (context.aborted) resetContext();
    return () => {
      generation.current += 1;
      request.current?.abort();
      context.removeEventListener('abort', resetContext);
    };
  }, [context, organizationId]);

  async function diagnose(mode: 'inspect' | 'preview') {
    if (!file || request.current || context.aborted || !currentOperation.current()) return;
    if (mode === 'preview' && (!confirmed || !mappingComplete(inspection, mapping))) return;
    generation.current += 1;
    const token = generation.current;
    const controller = new AbortController();
    request.current = controller;
    const isCurrent = () => token === generation.current && !controller.signal.aborted
      && !context.aborted && currentOperation.current();
    setBusy(mode);
    setError(null);
    setPreview(null);
    setImportConfirmed(false);
    setAcceptance(null);
    acceptedRequest.current = false;
    setPage(0);
    if (mode === 'inspect') {
      setInspection(null);
      setMapping({});
      setConfirmed(false);
    }
    try {
      if (mode === 'inspect') {
        const result = await inspectMeasurements(file, delimiter, organizationId, controller.signal);
        if (isCurrent()) setInspection(result);
      } else if (inspection) {
        const result = await previewMeasurements(file, delimiter, organizationId, inspection, mapping, confirmed, controller.signal);
        if (isCurrent()) setPreview(result);
      }
    } catch (failure) {
      if (isCurrent()) setError(diagnosticErrorKey(failure));
    } finally {
      if (isCurrent()) { setBusy(null); request.current = null; }
    }
  }

  async function acceptImport() {
    if (!file || !inspection || !confirmed || !importConfirmed || !canApplyPreview(preview)
      || acceptance || acceptedRequest.current || request.current || context.aborted || !currentOperation.current()) return;
    const token = ++generation.current;
    const controller = new AbortController();
    request.current = controller;
    const isCurrent = () => token === generation.current && !controller.signal.aborted
      && !context.aborted && currentOperation.current();
    setBusy('apply');
    setError(null);
    try {
      const result = await applyMeasurements(file, delimiter, organizationId, inspection, mapping, importConfirmed, preview, controller.signal);
      if (isCurrent()) { acceptedRequest.current = true; setAcceptance(result); setImportConfirmed(false); }
    } catch (failure) {
      if (isCurrent()) {
        const key = diagnosticErrorKey(failure);
        setError(key === 'csvErrorRequest' ? 'csvErrorApply' : key);
        setImportConfirmed(false);
        if (key === 'csvErrorStale') setPreview(null);
      }
    } finally {
      if (isCurrent()) { setBusy(null); request.current = null; }
    }
  }

  async function download(kind: 'source' | 'receipt') {
    if (!acceptance || request.current || context.aborted || !currentOperation.current()) return;
    const token = ++generation.current;
    const controller = new AbortController();
    request.current = controller;
    const isCurrent = () => token === generation.current && !controller.signal.aborted
      && !context.aborted && currentOperation.current();
    setBusy('download');
    setError(null);
    try {
      await downloadImportResource(kind === 'source' ? acceptance.receipt.source_url : acceptance.receipt.receipt_url,
        organizationId, controller.signal, kind === 'source' ? 'measurement-source.csv' : 'measurement-receipt.json', isCurrent);
    } catch (failure) {
      if (isCurrent()) setError(diagnosticErrorKey(failure) === 'csvErrorPermission' ? 'csvErrorPermission' : 'csvErrorDownload');
    } finally {
      if (isCurrent()) { setBusy(null); request.current = null; }
    }
  }

  const complete = mappingComplete(inspection, mapping);
  const pages = Math.max(1, Math.ceil((preview?.rows.length ?? 0) / PAGE_SIZE));
  return (
    <section className="measurement-csv" aria-labelledby="measurement-csv-title">
      <h2 id="measurement-csv-title">{t('csvTitle')}</h2>
      <p className="login-notice">{t('csvPreviewHelp')}</p>
      <p>{t('csvSourceHelp')}</p>
      <div className="measurement-csv-controls">
        <label className="field-label">
          {t('csvFile')}
          <input ref={fileInput} type="file" accept=".csv,text/csv,text/plain" onChange={event => {
            invalidate();
            setInspection(null);
            setMapping({});
            const selected = event.target.files?.[0] ?? null;
            setFile(selected);
            if (selected && selected.size > 5 * 1024 * 1024) setError('csvErrorSize');
          }} />
        </label>
        <label className="field-label">
          {t('csvDelimiter')}
          <select value={delimiter} onChange={event => {
            invalidate(); setInspection(null); setMapping({}); setDelimiter(event.target.value);
          }}>
            <option value=",">{t('csvComma')}</option>
            <option value=";">{t('csvSemicolon')}</option>
            <option value={'\t'}>{t('csvTab')}</option>
          </select>
        </label>
        <button type="button" className="secondary-button" disabled={!file || file.size > 5 * 1024 * 1024 || Boolean(busy) || context.aborted}
          onClick={() => void diagnose('inspect')}>{t('csvInspect')}</button>
      </div>
      {busy && <p role="status">{t(busy === 'inspect' ? 'csvInspecting' : busy === 'preview' ? 'csvPreviewing' : busy === 'apply' ? 'csvApplying' : 'csvDownloading')}</p>}
      {error && <p role="alert" className="inline-error">{t(error)}</p>}
      {inspection && <>
        <IssueList issues={inspection.issues} t={t} />
        {inspection.headers.length > 0 && !inspection.issues.length && <fieldset>
          <legend>{t('csvMapping')}</legend>
          <p>{t('csvMappingHelp')}</p>
          <div className="measurement-csv-mapping">
            {inspection.headers.map((header, index) => <label className="field-label" key={header}>
              <span>{index + 1}. {header}</span>
              <select value={!Object.prototype.hasOwnProperty.call(mapping, header) || mapping[header] === undefined ? '' : mapping[header] === null ? 'ignore' : `field:${mapping[header]}`}
                onChange={event => {
                  invalidate();
                  const value = event.target.value;
                  setMapping(previous => ({ ...previous, [header]: value === 'ignore' ? null : value === '' ? undefined : value.slice(6) }));
                }}>
                <option value="">{t('csvUnselected')}</option>
                <option value="ignore">{t('csvIgnore')}</option>
                {inspection.fields.map(field => <option key={field} value={`field:${field}`}>
                  {fieldLabels[field] ? t(fieldLabels[field]) : field} ({field})
                </option>)}
              </select>
            </label>)}
          </div>
          <label className="measurement-csv-confirm">
            <input type="checkbox" checked={confirmed} disabled={!complete} onChange={event => {
              invalidate(); setConfirmed(event.target.checked);
            }} />
            <span>{t('csvConfirmMapping')}</span>
          </label>
          <button type="button" className="primary-button" disabled={!complete || !confirmed || Boolean(busy) || context.aborted}
            onClick={() => void diagnose('preview')}>{t('csvPreview')}</button>
        </fieldset>}
      </>}
      {acceptance && <section aria-labelledby="measurement-csv-receipt">
        <h3 id="measurement-csv-receipt">{t('csvReceiptTitle')}</h3>
        <p role="status">{t(acceptance.replayed ? 'csvReplayed' : 'csvAccepted')}</p>
        <dl className="measurement-csv-summary">
          <div><dt>{t('csvReceiptId')}</dt><dd>{acceptance.receipt.id}</dd></div>
          <div><dt>{t('csvAcceptedAt')}</dt><dd>{acceptance.receipt.accepted_at}</dd></div>
          <div><dt>{t('csvCreated')}</dt><dd>{acceptance.receipt.created_count}</dd></div>
          <div><dt>{t('csvSummaryMatches')}</dt><dd>{acceptance.receipt.already_matching_count}</dd></div>
          <div><dt>{t('csvSummaryIgnored')}</dt><dd>{acceptance.receipt.ignored_count}</dd></div>
          <div><dt>{t('csvSourceHash')}</dt><dd><code>{acceptance.receipt.source_sha256}</code></dd></div>
        </dl>
        <p>{t('csvReceiptHelp')}</p>
        <div className="measurement-csv-controls">
          <button type="button" className="secondary-button" disabled={Boolean(busy)} onClick={() => void download('receipt')}>{t('csvDownloadReceipt')}</button>
          <button type="button" className="secondary-button" disabled={Boolean(busy)} onClick={() => void download('source')}>{t('csvDownloadSource')}</button>
        </div>
      </section>}
      {preview && <section aria-labelledby="measurement-csv-preview">
        <h3 id="measurement-csv-preview">{t('csvPreviewTitle')}</h3>
        {!acceptance && <p role="status">{t(!preview.source_complete ? 'csvSourceIncomplete' : preview.summary.ready ? 'csvDiagnosticReady' : 'csvDiagnosticBlocked')}</p>}
        <dl className="measurement-csv-summary">
          {summaryFields.map(field => <div key={field}><dt>{t(summaryLabels[field])}</dt><dd>{preview.summary[field]}</dd></div>)}
        </dl>
        <IssueList issues={preview.issues} t={t} />
        {!acceptance && (canApplyPreview(preview) ? <fieldset disabled={Boolean(busy)}>
          <legend>{t('csvImportConfirmation')}</legend>
          <p>{t('csvRetentionHelp')}</p>
          <label className="measurement-csv-confirm">
            <input type="checkbox" checked={importConfirmed} onChange={event => setImportConfirmed(event.target.checked)} />
            <span>{t('csvConfirmImport')}</span>
          </label>
          <button type="button" className="primary-button" disabled={!importConfirmed || Boolean(busy) || context.aborted}
            onClick={() => void acceptImport()}>{t('csvApply')}</button>
        </fieldset> : <p>{t('csvApplyUnavailable')}</p>)}
        <p>{t('csvValuesHelp')}</p>
        {preview.rows.length === 0 ? <p>{t('csvNoRows')}</p> : <>
          <nav className="measurement-csv-pagination" aria-label={t('csvPagination')}>
            <button type="button" className="secondary-button" disabled={page === 0} onClick={() => setPage(page - 1)}>{t('csvPrevious')}</button>
            <span>{t('csvPage')} {page + 1} / {pages}</span>
            <button type="button" className="secondary-button" disabled={page + 1 >= pages} onClick={() => setPage(page + 1)}>{t('csvNext')}</button>
          </nav>
          <ol className="measurement-csv-rows" start={page * PAGE_SIZE + 1}>
            {preview.rows.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE).map(row => <li key={row.record_number}>
              <h4>{t('csvRecord')} {row.record_number} — {t('csvLines')} {row.line_start}–{row.line_end}</h4>
              <p>{t(outcomeLabels[row.outcome] ?? 'csvOutcomeUnknownCode')} — {t('csvFieldBox')}: {row.box_reference ?? t('csvAbsent')}</p>
              <p>{t('csvFieldDate')}: {row.measured_on ?? t('csvAbsent')} — {t('csvPrecision')}: {t(row.precision === 'iso_week' ? 'csvWeekOnly' : row.precision === 'exact_date' ? 'csvExactDate' : 'csvAbsent')}
                {' — '}{t('csvFieldYear')}: {row.iso_year ?? t('csvAbsent')} — {t('csvFieldWeek')}: {row.iso_week ?? t('csvAbsent')}</p>
              <dl className="measurement-csv-values">{row.values.map(value => <div key={value.field}>
                <dt>{fieldLabels[value.field] ? t(fieldLabels[value.field]) : value.field}</dt><dd>{displayValue(value, t)}</dd>
              </div>)}</dl>
              <IssueList issues={row.issues} t={t} />
            </li>)}
          </ol>
        </>}
      </section>}
    </section>
  );
}

export function displayValue(value: DiagnosticValue, t: Translator) {
  if (value.state === 'absent') return t('csvAbsent');
  if (value.state === 'null') return t('csvNull');
  if (value.state === 'blank') return t('csvBlank');
  if (value.value === null) return t('csvNull');
  if (typeof value.value === 'boolean') return t(value.value ? 'csvTrue' : 'csvFalse');
  return String(value.value);
}

function IssueList({ issues, t }: { issues: ImportIssue[]; t: Translator }) {
  if (!issues.length) return null;
  return <ul className="measurement-csv-issues">{issues.map((issue, index) => <li key={index}>
    {t(issueLabels[issue.code] ?? 'csvIssueUnknown')}
    {issue.field !== null && <> — {fieldLabels[issue.field] ? t(fieldLabels[issue.field]) : issue.field}</>}
    {issue.column !== null && <> — {t('csvColumn')} {issue.column}</>}
    {' '}<code>({issue.code})</code>
  </li>)}</ul>;
}
