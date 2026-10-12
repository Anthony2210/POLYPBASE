import type { Translator } from '../i18n';
import type { BoxItem } from '../types';
import { formatDisplayDate } from '../utils/dateFormat';
import PolypbaseIcon from './PolypbaseIcon';

export default function SuggestionList({
  boxes,
  listId,
  resultIdPrefix,
  selectedBoxId,
  totalCount,
  heading,
  isPhoneLayout,
  onClear,
  onSelectBox,
  t,
}: {
  boxes: BoxItem[];
  listId: string;
  resultIdPrefix: string;
  selectedBoxId: number | null;
  totalCount: number;
  heading: string;
  isPhoneLayout: boolean;
  onClear?: () => void;
  onSelectBox: (id: number) => void;
  t: Translator;
}) {
  return (
    <section className="suggestion-panel" aria-label={heading}>
      <div className="section-title">
        <h2>{heading}</h2>
        <span aria-live="polite">{totalCount}</span>
      </div>

      {boxes.length > 0 ? (
        <div className="suggestion-list" id={listId} role="listbox">
          {boxes.map((box) => (
            <button
              key={box.id}
              id={`${resultIdPrefix}-${box.id}`}
              className={selectedBoxId === box.id ? 'suggestion-row is-selected' : 'suggestion-row'}
              type="button"
              role="option"
              aria-selected={selectedBoxId === box.id}
              onClick={() => onSelectBox(box.id)}
            >
              {isPhoneLayout ? (
                <>
                  <span className="suggestion-identity">
                    <strong>{box.global_code}</strong>
                    <small>{box.species.scientific_name}</small>
                  </span>
                  <span className="suggestion-reading">
                    {box.latest_measurement ? (
                      <>
                        <strong>
                          {box.latest_measurement.polyp_count} {t('polyps').toLocaleLowerCase()},{' '}
                          {box.latest_measurement.ephyrae_count} {t('ephyrae').toLocaleLowerCase()}
                        </strong>
                        <small>{formatDisplayDate(box.latest_measurement.measured_on)}</small>
                      </>
                    ) : (
                      <small>{t('noMeasurementHistory')}</small>
                    )}
                  </span>
                  <span className="suggestion-location">
                    <strong>{box.thermal_zone?.name ?? t('noZone')}</strong>
                  </span>
                </>
              ) : (
                <>
                  <span className="suggestion-identity">
                    <strong>{box.global_code}</strong>
                  </span>
                  <span className="suggestion-context">
                    <strong>{box.species.scientific_name}</strong>
                  </span>
                  <span className="suggestion-reading">
                    {box.latest_measurement ? (
                      <>
                        <strong>
                          {box.latest_measurement.polyp_count} {t('polyps').toLocaleLowerCase()},{' '}
                          {box.latest_measurement.ephyrae_count} {t('ephyrae').toLocaleLowerCase()}
                        </strong>
                        <small>{formatDisplayDate(box.latest_measurement.measured_on)}</small>
                      </>
                    ) : (
                      <small>{t('noMeasurementHistory')}</small>
                    )}
                  </span>
                  <span className="suggestion-location">
                    <strong>{box.thermal_zone?.name ?? t('noZone')}</strong>
                  </span>
                </>
              )}
              <span className="suggestion-chevron" aria-hidden="true">
                <PolypbaseIcon name="chevron-right" size={17} />
              </span>
            </button>
          ))}
        </div>
      ) : (
        <div className="search-empty-state" id={listId} role="status">
          <p>{t('searchNoResults')}</p>
          {onClear ? (
            <button type="button" onClick={onClear}>{t('searchClear')}</button>
          ) : null}
        </div>
      )}
    </section>
  );
}
