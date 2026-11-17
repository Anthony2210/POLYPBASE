import { useLayoutEffect } from 'react';
import { useConfirmAction } from '../components/ConfirmActionModal';

export function useSharingConfirmation() {
  const confirmation = useConfirmAction();
  const open = confirmation.confirmActionModal !== null;
  useLayoutEffect(() => {
    if (!open) return;
    // ConfirmActionModal is portalled to body, outside the application root.
    const root = document.getElementById('root');
    if (!root) return;
    const previous = root.inert;
    root.inert = true;
    return () => { root.inert = previous; };
  }, [open]);
  return confirmation;
}
