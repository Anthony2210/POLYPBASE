import { useCallback, useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';

import { useAnchoredPopover } from '../hooks/useAnchoredPopover';
import type { Translator } from '../i18n';
import PolypbaseIcon from './PolypbaseIcon';

export default function BoxInventorySuggestion({
  ageMonths,
  onSetInactive,
  thresholdMonths,
  t,
  zeroZero,
}: {
  ageMonths: number;
  onSetInactive: () => void;
  thresholdMonths: number;
  t: Translator;
  zeroZero: boolean;
}) {
  const [isOpen, setIsOpen] = useState(false);
  const closeTimer = useRef<number>();
  const pointerInside = useRef(false);
  const clearCloseTimer = useCallback(() => window.clearTimeout(closeTimer.current), []);
  const close = useCallback((restoreFocus = false) => {
    clearCloseTimer();
    pointerInside.current = false;
    setIsOpen(false);
    if (restoreFocus) anchorRef.current?.focus({ preventScroll: true });
  }, [clearCloseTimer]);
  const { anchorRef, panelRef, position, id } = useAnchoredPopover<HTMLButtonElement>(isOpen, close);

  useEffect(() => clearCloseTimer, [clearCloseTimer]);

  function scheduleClose() {
    clearCloseTimer();
    closeTimer.current = window.setTimeout(() => {
      if (!pointerInside.current && !anchorRef.current?.matches(':focus-visible')) close();
    }, 180);
  }

  const ageValue = t('boxInventorySuggestionAgeValue')
    .replace('{count}', String(ageMonths))
    .replace('{unit}', t(ageMonths === 1 ? 'boxInventoryMonth' : 'boxInventoryMonths'));
  const thresholdValue = `${thresholdMonths} ${t(thresholdMonths === 1 ? 'boxInventoryMonth' : 'boxInventoryMonths')}`;

  return (
    <div className="box-inventory-suggestion">
      <span className="box-inventory-suggestion-label">
        {t('boxInventoryDeactivationSuggested')}
        <button
          ref={anchorRef}
          type="button"
          aria-label={t('boxInventorySuggestionWhy')}
          aria-describedby={isOpen ? id : undefined}
          aria-expanded={isOpen}
          onPointerEnter={(event) => {
            if (event.pointerType !== 'mouse') return;
            pointerInside.current = true;
            clearCloseTimer();
            setIsOpen(true);
          }}
          onPointerLeave={() => {
            pointerInside.current = false;
            scheduleClose();
          }}
          onFocus={() => {
            clearCloseTimer();
            setIsOpen(true);
          }}
          onBlur={scheduleClose}
          onClick={() => setIsOpen(true)}
        >
          <PolypbaseIcon name="info" size={15} strokeWidth={1.8} />
        </button>
      </span>

      <button className="box-inventory-suggestion-action" type="button" onClick={onSetInactive}>
        <PolypbaseIcon name="inactive-alt" size={15} strokeWidth={1.8} />
        {t('boxInventorySetInactive')}
      </button>

      {isOpen ? createPortal(
        <div
          ref={panelRef}
          id={id}
          className="anchored-popover box-inventory-suggestion-popover"
          role="tooltip"
          style={position}
          onPointerEnter={() => {
            pointerInside.current = true;
            clearCloseTimer();
          }}
          onPointerLeave={() => {
            pointerInside.current = false;
            scheduleClose();
          }}
        >
          <strong>{t('boxInventorySuggestionWhy')}</strong>
          <dl>
            <div>
              <dt>{t('boxInventorySuggestionAge')}</dt>
              <dd>{ageValue}</dd>
            </div>
            <div>
              <dt>{t('boxInventorySuggestionThreshold')}</dt>
              <dd>{thresholdValue}</dd>
            </div>
          </dl>
          {zeroZero ? <p>{t('boxInventorySuggestionLatestZero')}</p> : null}
        </div>,
        document.body,
      ) : null}
    </div>
  );
}
