import { useMemo, useState } from 'react';

import { getBoxStatusPresentation } from '../boxStatus';
import type { Language, Translator } from '../i18n';
import type { BoxItem } from '../types';
import type { BoxCollectionStatus } from '../utils/boxCollection';
import { formatDisplayDate } from '../utils/dateFormat';
import {
  countryName, groupBoxesByStrain, recordedStrainName, sortSpeciesStrains, type SpeciesStrain, strainTitle,
} from '../utils/speciesCatalog';
import BoxTrackingPreview from './BoxTrackingPreview';
import PolypbaseIcon from './PolypbaseIcon';
import { RowActionMenu } from './RowActionMenu';

const BOX_PAGE_SIZE = 50;

// Strains stay visible with their recorded metadata and Boxes. Box counts
// come from the complete institutional collection only, never a partial cache.
export default function SpeciesStrainsPanel({
  strains, boxes, boxCollectionStatus, onRetryBoxCollection, organizationId,
  speciesName, language, onOpenBox, onOpenZone, onCreateBox, t,
}: {
  strains: readonly SpeciesStrain[];
  boxes: readonly BoxItem[];
  boxCollectionStatus: BoxCollectionStatus;
  onRetryBoxCollection: () => void;
  organizationId: number;

  speciesName: string;

  language: Language;
  onOpenBox: (boxId: number, code: string) => void;
  onOpenZone: (zoneId: number) => void;
  onCreateBox?: (strainId: number) => void;
  t: Translator;
}) {

  const sortedStrains = useMemo(() => sortSpeciesStrains(strains), [strains]);
  const isCollectionReady = boxCollectionStatus === 'ready';
  const boxesByStrain = useMemo(
    () => groupBoxesByStrain(isCollectionReady ? boxes : [], strains.map((strain) => strain.id), organizationId),
    [boxes, isCollectionReady, organizationId, strains],
  );


  return (
    <section className="species-strains-panel" aria-labelledby="species-detail-strains-title">
      <h3 id="species-detail-strains-title">{t('speciesDetailStrainsAndBoxes')}</h3>

      {boxCollectionStatus === 'error' ? (
        <div className="species-admin-notice is-error" role="alert">
          <p>{t('boxCollectionLoadError')}</p>
          <button className="secondary-button" type="button" onClick={onRetryBoxCollection}>{t('boxCollectionRetry')}</button>
        </div>
      ) : null}

      {strains.length ? (
        <ul className="species-strain-list">
          {sortedStrains.map((strain) => (
            <StrainItem
              key={strain.id}
              strain={strain}

              // Null until the complete collection is loaded: no count is derived from a partial list.
              boxes={isCollectionReady ? boxesByStrain.get(strain.id) ?? [] : null}
              isCollectionLoading={boxCollectionStatus !== 'ready' && boxCollectionStatus !== 'error'}

              speciesName={speciesName}
              language={language}

              onOpenBox={onOpenBox}
              onOpenZone={onOpenZone}
              onCreateBox={onCreateBox}
              t={t}
            />
          ))}
        </ul>
      ) : <p className="species-detail-empty">{t('speciesDetailNoStrains')}</p>}
    </section>
  );
}

function StrainItem({ strain, boxes, isCollectionLoading, speciesName, language, onOpenBox, onOpenZone, onCreateBox, t }: {
  key?: number;
  strain: SpeciesStrain;

  boxes: readonly BoxItem[] | null;
  isCollectionLoading: boolean;

  speciesName: string;
  language: Language;

  onOpenBox: (boxId: number, code: string) => void;
  onOpenZone: (zoneId: number) => void;
  onCreateBox?: (strainId: number) => void;
  t: Translator;
}) {
  const [areAllBoxesShown, setAreAllBoxesShown] = useState(false);
  const name = recordedStrainName(strain, language);
  const title = strainTitle({ ...strain, name }, t('createBoxStrainLabel'));
  const origin = strain.origin;
  const description = strain.translations?.[language]?.description;
  const legacyLocation = !strain.recorded_country_code ? strain.recorded_provenance_name : null;
  const originTypeKey = {
    // The four product-approved acquisition types use the creation form labels.
    field_collection: 'strainAcquisitionFieldCollection',
    artificial_fertilization_wild: 'strainAcquisitionFertilizationWild',
    artificial_fertilization_captive: 'strainAcquisitionFertilizationCaptive',
    reproduction: 'speciesStrainOriginReproduction',
    donation: 'strainAcquisitionDonation',
    exchange: 'speciesStrainOriginExchange',
    unknown: 'speciesStrainOriginUnknown',
  } as const;
  const originType = origin && Object.prototype.hasOwnProperty.call(originTypeKey, origin.source_type)
    ? originTypeKey[origin.source_type as keyof typeof originTypeKey]
    : null;
  const bodyId = `species-strain-${strain.id}`;
  const shownBoxes = boxes && !areAllBoxesShown ? boxes.slice(0, BOX_PAGE_SIZE) : boxes;

  return (
    <li className="species-strain">
      <h4 className="species-strain-heading">
          <span className="species-strain-summary">
            <strong className="species-strain-name">{`${title} (${strain.code})`}</strong>
            <span className="species-strain-provenance">
              {`${t('adminCountry')} : ${strain.recorded_country_code ? countryName(strain.recorded_country_code, language) : t('speciesStrainNoProvenance')}`}
              {originType ? t('speciesStrainAcquisitionVia').replace('{acquisition}', t(originType)) : null}
            </span>
          </span>
          {strain.is_box_usable && onCreateBox ? (
            <button className="primary-button button-icon-label" type="button" onClick={() => onCreateBox(strain.id)}>
              <PolypbaseIcon name="plus" size={16} aria-hidden="true" />
              {t('createBoxNew')}
            </button>
          ) : null}
      </h4>

        <div className="species-strain-body" id={bodyId}>
          {name || description || origin || legacyLocation ? <dl className="species-strain-facts">
            {name ? <div><dt>{t('taxonomyNameByLanguage')}</dt><dd>{name}</dd></div> : null}

            {description ? <div><dt>{t('taxonomyDescriptionByLanguage')}</dt><dd>{description}</dd></div> : null}
            {legacyLocation ? <div><dt>{t('speciesStrainLocation')}</dt><dd>{legacyLocation}</dd></div> : null}

            {origin ? (
              <>

                {origin.event_date ? <div><dt>{t('speciesStrainOriginDate')}</dt><dd>{formatDisplayDate(origin.event_date)}</dd></div> : null}
                {origin.origin_institution_name ? <div><dt>{t('speciesStrainOriginInstitution')}</dt><dd>{origin.origin_institution_name}</dd></div> : null}
                {origin.description ? <div><dt>{t('movementOrigin')}</dt><dd>{origin.description}</dd></div> : null}
                {origin.latitude != null ? <div><dt>{t('speciesStrainLatitude')}</dt><dd>{origin.latitude}</dd></div> : null}
                {origin.longitude != null ? <div><dt>{t('speciesStrainLongitude')}</dt><dd>{origin.longitude}</dd></div> : null}
                {origin.technique ? <div><dt>{t('speciesStrainTechnique')}</dt><dd>{origin.technique}</dd></div> : null}
              </>
            ) : null}
          </dl> : null}
          {!strain.is_box_usable ? (
            <p className="species-strain-notes"><span>{t('speciesCatalogNotBoxUsable')}</span></p>
          ) : null}

          {isCollectionLoading ? <p className="species-detail-empty" role="status">{t('speciesStrainBoxesLoading')}</p> : null}
          {boxes && !boxes.length ? <p className="species-detail-empty">{t('speciesStrainNoBoxes')}</p> : null}
          {shownBoxes?.length ? (
            // Same row layout and classes as the Box Inventory.
            <div className="box-inventory-list species-box-list">
              <div className="box-inventory-table-head" aria-hidden="true">
                <span>{t('boxInventoryBox')}</span>
                <span>{t('boxInventoryLocation')}</span>
                <span>{t('boxInventoryDates')}</span>
                <span>{t('boxInventoryLatestCounts')}</span>
                <span />
              </div>
              {shownBoxes.map((box) => (
                <SpeciesBoxRow
                  key={box.id} box={box} speciesName={speciesName} language={language}
                  onOpenBox={onOpenBox} onOpenZone={onOpenZone} t={t}
                />
              ))}
            </div>
          ) : null}
          {boxes && shownBoxes && shownBoxes.length < boxes.length ? (
            <button className="secondary-button code-change-more" type="button" onClick={() => setAreAllBoxesShown(true)}>
              {t('speciesCodeChangeShowAllBoxes').replace('{count}', String(boxes.length))}
            </button>
          ) : null}
        </div>
    </li>
  );
}

function SpeciesBoxRow({ box, speciesName, language, onOpenBox, onOpenZone, t }: {
  key?: number;
  box: BoxItem;
  speciesName: string;
  language: Language;
  onOpenBox: (boxId: number, code: string) => void;
  onOpenZone: (zoneId: number) => void;
  t: Translator;
}) {
  const status = getBoxStatusPresentation(box.status, language);
  // As in the Inventory, an inactive Box has no current location.
  const currentZone = box.status === 'inactive' ? null : box.thermal_zone;
  const measurement = box.latest_measurement;
  return (
    <article className={`box-inventory-row is-${status.tone}`} aria-label={`${box.global_code}, ${status.label}`}>
      {box.status !== 'active' ? (
        <span className="box-inventory-row-status">{box.status === 'pending_review' ? t('boxInventoryReviewMarker') : status.label}</span>
      ) : null}
      <div className="box-inventory-cell box-inventory-identity" data-label={t('boxInventoryBox')}>
        {/* The Inventory's own tracking preview: real data, loaded only when opened. */}
        <BoxTrackingPreview boxId={box.id} code={box.global_code} speciesName={speciesName} language={language} onOpenBox={onOpenBox} t={t} />
      </div>
      <div className="box-inventory-cell box-inventory-location" data-label={t('boxInventoryLocation')}>
        {currentZone ? (
          <a
            href={`/zones/${currentZone.id}`}
            onClick={(event) => {
              if (event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
              event.preventDefault();
              onOpenZone(currentZone.id);
            }}
          >{currentZone.name}</a>
        ) : <span className="is-muted">{t('boxInventoryWithoutLocation')}</span>}
      </div>
      <div className="box-inventory-cell box-inventory-dates" data-label={t('boxInventoryDates')}>
        {/* Species display convention: scientific bounds, never technical timestamps. */}
        <span>
          <small>{t('boxInventoryCreatedOn')}</small>
          <strong>{box.first_measurement_on ? formatDisplayDate(box.first_measurement_on) : t('boxInventoryNoData')}</strong>
        </span>
        <span>
          <small>{t('boxInventoryLastMeasurement')}</small>
          <strong>{box.last_measurement_on ? formatDisplayDate(box.last_measurement_on) : t('boxInventoryNoData')}</strong>
        </span>
      </div>
      <div className="box-inventory-cell box-inventory-counts" data-label={t('boxInventoryLatestCounts')}>
        {/* Zero is a real count; no measurement is shown as such, never as 0. */}
        {measurement ? (
          <>
            <span><strong>{measurement.polyp_count}</strong><small>{t('polyps')}</small></span>
            <span><strong>{measurement.ephyrae_count}</strong><small>{t('ephyraeFull')}</small></span>
          </>
        ) : (
          <span className="box-inventory-no-measurement">{t('boxInventoryNoMeasurement')}</span>
        )}
      </div>
      <div className="box-inventory-cell box-inventory-actions" data-label={t('boxInventoryActions')}>
        <RowActionMenu
          ariaLabel={`${t('boxInventoryActions')} ${box.global_code}`}
          actions={[{ action: 'open', label: t('createBoxOpenCreated') }]}
          onAction={() => onOpenBox(box.id, box.global_code)}
        />
      </div>
    </article>
  );
}
