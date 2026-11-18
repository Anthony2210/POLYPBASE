import type { Translator } from '../i18n';

export default function DemoModeBanner({ enabled, t }: { enabled: boolean; t: Translator }) {
  if (!enabled) return null;
  return <p className="demo-mode-banner">{t('demoModeBanner')}</p>;
}
