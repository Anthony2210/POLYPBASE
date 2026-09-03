import { useEffect, useMemo } from 'react';

import { AlertTriangle, CheckCircle2, X, XCircle } from 'lucide-react';

import type { Translator } from '../i18n';
import type { BoxInventoryBatchResult, BoxInventorySelectionItem } from '../types';
import ModalPortal from './ModalPortal';

export type BoxInventoryBatchAction = 'active' | 'inactive';

export default function BoxInventoryBatchModal({
  action,
  error,
  isSaving,
  onClose,
  onConfirm,
  result,
  selectedBoxes,
  t,
}: {
  action: BoxInventoryBatchAction;
  error: string | null;
  isSaving: boolean;
  onClose: () => void;
  onConfirm: () => Promise<void>;
  result: BoxInventoryBatchResult | null;
  selectedBoxes: BoxInventorySelectionItem[];
  t: Translator;
}) {
  const selectedById = useMemo(
    () => new Map(selectedBoxes.map((box) => [box.id, box])),
    [selectedBoxes],
  );
  const withLocationCount = selectedBoxes.filter((box) => box.has_location).length;
  const withoutLocationCount = selectedBoxes.length - withLocationCount;

  useEffect(() => {
    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === 'Escape' && !isSaving) onClose();
    }

    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [isSaving, onClose]);

  const title = result
    ? t('boxInventoryBatchResultTitle')
    : t(action === 'active' ? 'boxInventoryBatchActiveTitle' : 'boxInventoryBatchInactiveTitle');

  return (
    <ModalPortal>
      <div className="modal-backdrop box-inventory-batch-backdrop" role="presentation" onMouseDown={isSaving ? undefined : onClose}>
        <section
          className="box-inventory-batch-modal"
          role="dialog"
          aria-modal="true"
          aria-labelledby="box-inventory-batch-title"
          onMouseDown={(event) => event.stopPropagation()}
        >
          <header className="box-lifecycle-heading">
            <div>
              <span>{t('boxInventoryBatchKicker')}</span>
              <h2 id="box-inventory-batch-title">{title}</h2>
            </div>
            <button type="button" aria-label={t('close')} title={t('close')} disabled={isSaving} onClick={onClose}>
              <X aria-hidden="true" size={19} />
            </button>
          </header>

          {result ? (
            <div className="box-inventory-batch-report">
              <div className="box-inventory-batch-result-summary" aria-live="polite">
                <div className="is-success">
                  <CheckCircle2 aria-hidden="true" size={21} />
                  <span><strong>{result.success_count}</strong>{t('boxInventoryBatchSucceeded')}</span>
                </div>
                <div className={result.failure_count ? 'is-failure' : ''}>
                  <XCircle aria-hidden="true" size={21} />
                  <span><strong>{result.failure_count}</strong>{t('boxInventoryBatchFailed')}</span>
                </div>
              </div>

              {action === 'active' && result.success_count > 0 ? (
                <dl className="box-inventory-batch-metrics is-result">
                  <div>
                    <dt>{t('boxInventoryBatchWithLocation')}</dt>
                    <dd>{result.active_with_location_count}</dd>
                  </div>
                  <div>
                    <dt>{t('boxInventoryBatchWithoutLocation')}</dt>
                    <dd>{result.active_without_location_count}</dd>
                  </div>
                </dl>
              ) : null}

              {result.successes.length ? (
                <section className="box-inventory-batch-result-list is-success">
                  <h3>{t('boxInventoryBatchSuccessfulBoxes')}</h3>
                  <ul>
                    {result.successes.map((item) => <li key={item.box_id}>{item.global_code}</li>)}
                  </ul>
                </section>
              ) : null}

              {result.failures.length ? (
                <section className="box-inventory-batch-result-list is-failure">
                  <h3>{t('boxInventoryBatchFailedBoxes')}</h3>
                  <ul>
                    {result.failures.map((item) => (
                      <li key={item.box_id}>
                        <strong>{item.global_code ?? selectedById.get(item.box_id)?.global_code ?? `#${item.box_id}`}</strong>
                        <span>{item.error}</span>
                      </li>
                    ))}
                  </ul>
                </section>
              ) : null}

              <footer className="box-lifecycle-actions">
                <button className="primary-button" type="button" onClick={onClose}>
                  {t('boxInventoryBatchCloseReport')}
                </button>
              </footer>
            </div>
          ) : (
            <div className="box-inventory-batch-confirmation">
              <dl className="box-inventory-batch-metrics">
                <div>
                  <dt>{t('boxInventoryBatchSelectedTotal')}</dt>
                  <dd>{selectedBoxes.length}</dd>
                </div>
                {action === 'active' ? (
                  <>
                    <div>
                      <dt>{t('boxInventoryBatchWithLocation')}</dt>
                      <dd>{withLocationCount}</dd>
                    </div>
                    <div className={withoutLocationCount ? 'is-warning' : ''}>
                      <dt>{t('boxInventoryBatchWithoutLocation')}</dt>
                      <dd>{withoutLocationCount}</dd>
                    </div>
                  </>
                ) : null}
              </dl>

              {action === 'active' && withoutLocationCount > 0 ? (
                <div className="box-lifecycle-warning" role="status">
                  <AlertTriangle aria-hidden="true" size={20} />
                  <p>{t('boxInventoryBatchActiveWarning')}</p>
                </div>
              ) : null}

              {action === 'inactive' ? (
                <div className="box-inventory-batch-history-note">
                  <strong>{t('boxInventoryBatchInactiveHistoryTitle')}</strong>
                  <p>{t('boxInventoryBatchInactiveHistoryText')}</p>
                </div>
              ) : null}

              <section className="box-inventory-batch-boxes">
                <h3>{t('boxInventoryBatchAffectedBoxes')}</h3>
                <ul>
                  {selectedBoxes.map((box) => (
                    <li key={box.id}>
                      <strong>{box.global_code}</strong>
                      <span>{box.species_name}</span>
                    </li>
                  ))}
                </ul>
              </section>

              {error ? <p className="inline-error box-lifecycle-error">{error}</p> : null}

              <footer className="box-lifecycle-actions">
                <button className="secondary-button" type="button" disabled={isSaving} onClick={onClose}>
                  {t('confirmCancel')}
                </button>
                <button className="primary-button" type="button" disabled={isSaving} onClick={() => void onConfirm()}>
                  {isSaving ? t('saving') : t('boxInventoryBatchConfirm')}
                </button>
              </footer>
            </div>
          )}
        </section>
      </div>
    </ModalPortal>
  );
}
