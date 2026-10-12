import type { Translator } from '../i18n';
import type { BoxItem } from '../types';
import PolypbaseIcon from './PolypbaseIcon';

export default function RecentAccessList({
  boxes,
  isPhoneLayout,
  onSelectBox,
  t,
}: {
  boxes: BoxItem[];
  isPhoneLayout: boolean;
  onSelectBox: (id: number) => void;
  t: Translator;
}) {
  if (!boxes.length) {
    return <p className="muted compact-text">{t('noRecentScans')}</p>;
  }

  return (
    <section className="recent-panel" aria-label={t('recentAccessAriaLabel')}>
      <div className="section-title">
        <h2>{t('recentAccess')}</h2>
        <span>{t('scanSearch')}</span>
      </div>

      <div className="recent-strip">
        {boxes.map((box) => (
          <button
            key={box.id}
            type="button"
            onClick={() => onSelectBox(box.id)}
          >
            <span className="recent-box-heading">
              <strong>{box.global_code}</strong>
            </span>
            <small>{box.species.scientific_name}</small>
            <span className="recent-box-meta">
              <span>{box.thermal_zone?.name ?? t('noZone')}</span>
            </span>
            {!isPhoneLayout ? (
              <span className="recent-box-chevron" aria-hidden="true">
                <PolypbaseIcon name="chevron-right" size={16} />
              </span>
            ) : null}
          </button>
        ))}
      </div>
    </section>
  );
}
