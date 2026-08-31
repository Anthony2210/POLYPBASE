import { useCallback, useLayoutEffect, useState } from 'react';
import { createPortal } from 'react-dom';
import { MoreVertical } from 'lucide-react';

import { useAnchoredPopover } from '../hooks/useAnchoredPopover';
import type { Translator } from '../i18n';
import type { BoxInventoryItem } from '../types';

type RowAction = 'qualify' | 'deactivate' | 'reactivate';

export default function BoxInventoryRowMenu({ box, disabled, onAction, t }: {
  box: Pick<BoxInventoryItem, 'global_code' | 'status'>;
  disabled: boolean;
  onAction: (action: RowAction) => void;
  t: Translator;
}) {
  const [isOpen, setIsOpen] = useState(false);
  const close = useCallback((restoreFocus = false) => {
    setIsOpen(false);
    if (restoreFocus) anchorRef.current?.focus();
  }, []);
  const { anchorRef, panelRef, position, id } = useAnchoredPopover<HTMLButtonElement>(isOpen, close, 'end');
  const actions: Array<{ action: RowAction; label: string; danger?: boolean }> = box.status === 'active'
    ? [{ action: 'deactivate', label: t('boxArchiveAction'), danger: true }]
    : box.status === 'inactive'
      ? [{ action: 'reactivate', label: t('boxActivateAction') }]
      : [{ action: 'qualify', label: t('boxLifecycleQualifyTitle') }];

  useLayoutEffect(() => {
    if (isOpen && position.visibility === 'visible') panelRef.current?.querySelector<HTMLButtonElement>('[role="menuitem"]')?.focus();
  }, [isOpen, panelRef, position.visibility]);

  return (
    <>
      <button
        className="box-inventory-menu-trigger"
        ref={anchorRef}
        type="button"
        disabled={disabled}
        aria-label={`${t('boxInventoryActions')} ${box.global_code}`}
        aria-haspopup="menu"
        aria-expanded={isOpen}
        aria-controls={isOpen ? id : undefined}
        onClick={() => setIsOpen((current) => !current)}
        onKeyDown={(event) => {
          if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
            event.preventDefault();
            setIsOpen(true);
          }
        }}
      ><MoreVertical size={18} aria-hidden="true" /></button>
      {isOpen ? createPortal(
        <div
          className="anchored-popover box-inventory-row-menu"
          id={id}
          ref={panelRef}
          role="menu"
          aria-label={`${t('boxInventoryActions')} ${box.global_code}`}
          style={position}
          onBlur={(event) => {
            const target = event.relatedTarget as Node | null;
            if (!event.currentTarget.contains(target) && !anchorRef.current?.contains(target)) close();
          }}
          onKeyDown={(event) => {
            const items = Array.from(panelRef.current?.querySelectorAll<HTMLButtonElement>('[role="menuitem"]') ?? []);
            const index = items.indexOf(document.activeElement as HTMLButtonElement);
            const next = event.key === 'ArrowDown' ? (index + 1) % items.length
              : event.key === 'ArrowUp' ? (index + items.length - 1) % items.length
                : event.key === 'Home' ? 0 : event.key === 'End' ? items.length - 1 : null;
            if (next != null) {
              event.preventDefault();
              items[next]?.focus();
            }
            if (event.key === 'Tab') close(true);
          }}
        >
          {actions.map((item) => (
            <button className={item.danger ? 'is-danger' : undefined} key={item.action} type="button" role="menuitem" onClick={() => {
              close();
              onAction(item.action);
            }}>{item.label}</button>
          ))}
        </div>, document.body,
      ) : null}
    </>
  );
}
