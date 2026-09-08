import type { Translator } from '../i18n';
import type { BoxInventoryItem } from '../types';
import { RowActionMenu } from './RowActionMenu';

type RowAction = 'qualify' | 'deactivate' | 'reactivate';

export default function BoxInventoryRowMenu({ box, disabled, onAction, t }: {
  box: Pick<BoxInventoryItem, 'global_code' | 'status'>;
  disabled: boolean;
  onAction: (action: RowAction) => void;
  t: Translator;
}) {
  const actions: Array<{ action: RowAction; label: string; danger?: boolean }> = box.status === 'active'
    ? [{ action: 'deactivate', label: t('boxArchiveAction'), danger: true }]
    : box.status === 'inactive'
      ? [{ action: 'reactivate', label: t('boxActivateAction') }]
      : [{ action: 'qualify', label: t('boxLifecycleQualifyTitle') }];

  return (
    <RowActionMenu<RowAction>
      disabled={disabled}
      ariaLabel={`${t('boxInventoryActions')} ${box.global_code}`}
      actions={actions}
      onAction={onAction}
    />
  );
}
