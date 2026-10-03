import { ArrowLeft } from 'lucide-react';
import { useIsDesktopApp } from '../hooks/useIsDesktopApp';

type DetailBackButtonProps = {
  label: string;
  onBack: () => void;
  desktopClassName?: string;
};

export default function DetailBackButton({ label, onBack, desktopClassName }: DetailBackButtonProps) {
  const isDesktopApp = useIsDesktopApp();

  return (
    <button
      className={isDesktopApp
        ? ['text-button', 'detail-back-button', desktopClassName].filter(Boolean).join(' ')
        : 'icon-button detail-back-button detail-back-button--icon'}
      type="button"
      aria-label={label}
      title={label}
      onClick={onBack}
    >
      {isDesktopApp ? label : <ArrowLeft aria-hidden="true" size={20} />}
    </button>
  );
}
