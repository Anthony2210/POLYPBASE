import { type ReactNode, useEffect } from 'react';

import ModalPortal from './ModalPortal';
import PolypbaseIcon from './PolypbaseIcon';

export default function AdminActionPanel({
  children,
  title,
  closeLabel,
  onClose,
  wide = false,
}: {
  children: ReactNode;
  title: string;
  closeLabel: string;
  onClose: () => void;
  wide?: boolean;
}) {
  useEffect(() => {
    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === 'Escape') onClose();
    }

    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [onClose]);

  return (
    <ModalPortal>
      <div className="admin-action-backdrop" role="presentation" onMouseDown={onClose}>
        <aside
          className={wide ? 'admin-action-panel is-wide' : 'admin-action-panel'}
          role="dialog"
          aria-modal="true"
          aria-label={title}
          onMouseDown={(event) => event.stopPropagation()}
        >
          <header className="admin-action-panel__header">
            <h2>{title}</h2>
            <button className="icon-button" type="button" aria-label={closeLabel} title={closeLabel} onClick={onClose}>
              <PolypbaseIcon name="close" size={19} />
            </button>
          </header>
          <div className="admin-action-panel__body">{children}</div>
        </aside>
      </div>
    </ModalPortal>
  );
}
