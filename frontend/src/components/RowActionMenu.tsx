import { useCallback, useLayoutEffect, useState } from 'react';
import { createPortal } from 'react-dom';
import { MoreVertical } from 'lucide-react';

import { useAnchoredPopover } from '../hooks/useAnchoredPopover';

export interface RowActionMenuItem<TAction extends string> {
  action: TAction;
  label: string;
  danger?: boolean;
}

export interface RowActionMenuProps<TAction extends string> {
  disabled?: boolean;
  actions: RowActionMenuItem<TAction>[];
  onAction: (action: TAction) => void;
  ariaLabel: string;
}

export function RowActionMenu<TAction extends string>({
  disabled,
  actions,
  onAction,
  ariaLabel,
}: RowActionMenuProps<TAction>) {
  const [isOpen, setIsOpen] = useState(false);
  const close = useCallback((restoreFocus = false) => {
    setIsOpen(false);
    if (restoreFocus) anchorRef.current?.focus();
  }, []);
  const { anchorRef, panelRef, position, id } = useAnchoredPopover<HTMLButtonElement>(isOpen, close, 'end');

  useLayoutEffect(() => {
    if (isOpen && position.visibility === 'visible') panelRef.current?.querySelector<HTMLButtonElement>('[role="menuitem"]')?.focus();
  }, [isOpen, panelRef, position.visibility]);

  return (
    <>
      <button
        className="row-action-menu-trigger"
        ref={anchorRef}
        type="button"
        disabled={disabled}
        aria-label={ariaLabel}
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
          className="anchored-popover row-action-menu"
          id={id}
          ref={panelRef}
          role="menu"
          aria-label={ariaLabel}
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
