import { type KeyboardEvent, useState } from 'react';

import type { Translator } from '../i18n';
import type { BoxCreatePayload, BoxCreationResolution, BoxDetail, BoxItem, ExportOptions, UserProfile } from '../types';
import type { BoxCollectionState } from '../utils/boxCollection';
import { userCanCreateBoxes } from '../utils/profilePermissions';
import BoxSearchStatus from './BoxSearchStatus';
import type { ConfirmActionOptions } from './ConfirmActionModal';
import CreateBoxPanel from './CreateBoxPanel';
import PageLoader from './PageLoader';
import RecentAccessList from './RecentAccessList';
import SearchField from './SearchField';
import SuggestionList from './SuggestionList';
import TabletQrScanner from './TabletQrScanner';

const PILOTAGE_RESULT_LIMIT = 15;
const PHONE_RESULT_LIMIT = 5;

export default function PilotageView({
  activeOrganizationId,
  boxes,
  boxCollectionStatus,
  onRetryBoxCollection,
  onResolveBoxCode,
  exportOptions,
  isLoading,
  isCreateBoxOpen,
  isPhoneLayout,
  isOptionsLoading,
  profile,
  recentBoxes,
  search,
  searchResults,
  onCreateBox,
  onResolveBoxCreation,
  isOperationCurrent,
  onCreateBoxOpenChange,
  onManageSpeciesCodes,
  onRequestOptions,
  confirmAction,
  t,
  onSearch,
  onSelectBox,
}: {
  activeOrganizationId: number | null;
  boxes: BoxItem[];
  boxCollectionStatus: BoxCollectionState['status'];
  onRetryBoxCollection: () => void;
  onResolveBoxCode: (code: string) => Promise<number | null>;
  exportOptions: ExportOptions | null;
  isLoading: boolean;
  isCreateBoxOpen: boolean;
  isPhoneLayout: boolean;
  isOptionsLoading: boolean;
  profile: UserProfile | null;
  recentBoxes: BoxItem[];
  search: string;
  searchResults: BoxItem[];
  onCreateBox: (payload: BoxCreatePayload) => Promise<BoxDetail>;
  onResolveBoxCreation: (requestId: string) => Promise<BoxCreationResolution>;
  isOperationCurrent: () => boolean;
  onCreateBoxOpenChange: (isOpen: boolean) => void;
  onManageSpeciesCodes?: () => void;
  onRequestOptions: () => void;
  confirmAction: (options: ConfirmActionOptions) => Promise<boolean>;
  t: Translator;
  onSearch: (value: string) => void;
  onSelectBox: (id: number) => void;
}) {
  const hasSearch = Boolean(search.trim());
  const isBoxCollectionReady = boxCollectionStatus === 'ready';
  const visibleSuggestions = hasSearch
    ? searchResults.slice(0, isPhoneLayout ? PHONE_RESULT_LIMIT : PILOTAGE_RESULT_LIMIT)
    : [];
  const [tabletLookupMode, setTabletLookupMode] = useState<'qr' | 'search'>('qr');
  const [highlightedSuggestionIndex, setHighlightedSuggestionIndex] = useState(0);
  const canCreateBox = userCanCreateBoxes(profile);

  function selectFirstSuggestion() {
    const selectedSuggestion = visibleSuggestions[highlightedSuggestionIndex] ?? visibleSuggestions[0];
    if (selectedSuggestion) {
      onSelectBox(selectedSuggestion.id);
      return;
    }
    // While the full list loads, submitting an exact box code still opens that box.
    if (hasSearch && !isBoxCollectionReady) {
      void onResolveBoxCode(search)
        .then((boxId) => {
          if (boxId != null) onSelectBox(boxId);
        })
        .catch(() => {
          // The search keeps its loading or retry state; nothing else to report.
        });
    }
  }

  function handleSearchChange(value: string) {
    setHighlightedSuggestionIndex(0);
    onSearch(value);
  }

  function highlightSuggestion(index: number) {
    setHighlightedSuggestionIndex(index);
    const prefix = isPhoneLayout ? 'box-suggestion' : 'box-search-result';
    document.getElementById(`${prefix}-${visibleSuggestions[index].id}`)?.scrollIntoView({ block: 'nearest', inline: 'nearest' });
  }

  function handleSearchKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === 'Escape') {
      event.preventDefault();
      handleSearchChange('');
      return;
    }
    if (!visibleSuggestions.length) return;

    if (event.key === 'ArrowDown') {
      event.preventDefault();
      highlightSuggestion((highlightedSuggestionIndex + 1) % visibleSuggestions.length);
    } else if (event.key === 'ArrowUp') {
      event.preventDefault();
      highlightSuggestion((highlightedSuggestionIndex - 1 + visibleSuggestions.length) % visibleSuggestions.length);
    }
  }

  const resultIdPrefix = isPhoneLayout ? 'box-suggestion' : 'box-search-result';
  const resultListId = isPhoneLayout ? 'box-suggestions' : 'box-search-results';
  const searchFieldProps = {
    activeDescendant: visibleSuggestions[highlightedSuggestionIndex]
      ? `${resultIdPrefix}-${visibleSuggestions[highlightedSuggestionIndex].id}`
      : undefined,
    controls: isPhoneLayout || hasSearch ? resultListId : undefined,
    expanded: hasSearch,
    labels: {
      label: t('searchOrScan'),
      placeholder: t('searchPlaceholder'),
    },
    value: search,
    onChange: handleSearchChange,
    onKeyDown: handleSearchKeyDown,
    onSubmit: selectFirstSuggestion,
  };

  if (isLoading) {
    return <PageLoader variant="pilotage" label={t('pilotageTitle')} />;
  }

  return (
    <section className="pilotage-flow">
      <div className="lookup-panel">
        {!isPhoneLayout ? (
          <div className="pilotage-search-surface">
            <SearchField
              {...searchFieldProps}
              labels={{
                label: t('searchOrScan'),
                placeholder: t('pilotageSearchPlaceholder'),
              }}
              clearLabel={t('searchClear')}
              variant="control-deck"
            />
            {hasSearch && !isBoxCollectionReady ? (
              <BoxSearchStatus status={boxCollectionStatus} onRetry={onRetryBoxCollection} t={t} />
            ) : hasSearch ? (
              <SuggestionList
                boxes={visibleSuggestions}
                listId={resultListId}
                resultIdPrefix={resultIdPrefix}
                selectedBoxId={visibleSuggestions[highlightedSuggestionIndex]?.id ?? null}
                totalCount={searchResults.length}
                heading={t('searchResults')}
                isPhoneLayout={false}
                onClear={() => handleSearchChange('')}
                onSelectBox={onSelectBox}
                t={t}
              />
            ) : null}
          </div>
        ) : (
          <>
            <section className={`phone-lookup-panel is-${tabletLookupMode}-mode`}>
              <div className="tablet-lookup-tabs" role="tablist" aria-label={t('searchOrScan')}>
                <button
                  className={tabletLookupMode === 'qr' ? 'is-active' : ''}
                  type="button"
                  role="tab"
                  aria-selected={tabletLookupMode === 'qr'}
                  onClick={() => setTabletLookupMode('qr')}
                >
                  {t('qrCode')}
                </button>
                <button
                  className={tabletLookupMode === 'search' ? 'is-active' : ''}
                  type="button"
                  role="tab"
                  aria-selected={tabletLookupMode === 'search'}
                  onClick={() => setTabletLookupMode('search')}
                >
                  {t('searchTab')}
                </button>
              </div>

              {tabletLookupMode === 'qr' ? (
                <TabletQrScanner
                  boxes={boxes}
                  labels={{
                    found: t('qrScannerFound'),
                    loading: t('qrScannerLoading'),
                    permission: t('qrScannerPermission'),
                    secureContext: t('qrScannerSecureContext'),
                    start: t('qrScannerStart'),
                    stop: t('qrScannerStop'),
                    unsupported: t('qrScannerUnsupported'),
                  }}
                  onResolveBoxCode={onResolveBoxCode}
                  onSelectBox={onSelectBox}
                />
              ) : (
                <div className="tablet-manual-search">
                  <SearchField {...searchFieldProps} />
                </div>
              )}
            </section>

            <div className="mobile-suggestion-slot">
              {tabletLookupMode === 'search' && hasSearch && !isBoxCollectionReady ? (
                <BoxSearchStatus status={boxCollectionStatus} onRetry={onRetryBoxCollection} t={t} />
              ) : tabletLookupMode === 'search' && hasSearch ? (
                <SuggestionList
                  boxes={visibleSuggestions}
                  listId={resultListId}
                  resultIdPrefix={resultIdPrefix}
                  selectedBoxId={visibleSuggestions[highlightedSuggestionIndex]?.id ?? null}
                  totalCount={visibleSuggestions.length}
                  heading={t('suggestions')}
                  isPhoneLayout
                  onClear={() => handleSearchChange('')}
                  onSelectBox={onSelectBox}
                  t={t}
                />
              ) : null}
            </div>
          </>
        )}

        {(!hasSearch || isPhoneLayout) ? (
          <RecentAccessList boxes={recentBoxes} isPhoneLayout={isPhoneLayout} onSelectBox={onSelectBox} t={t} />
        ) : null}

        {canCreateBox ? (
          <CreateBoxPanel
            key={activeOrganizationId ?? 'none'}
            exportOptions={exportOptions}
            isOpen={isPhoneLayout ? undefined : isCreateBoxOpen}
            isOptionsLoading={isOptionsLoading}
            presentation={isPhoneLayout ? 'inline' : 'modal'}
            profile={profile}
            t={t}
            onCreateBox={onCreateBox}
            onResolveBoxCreation={onResolveBoxCreation}
            isOperationCurrent={isOperationCurrent}
            onManageSpeciesCodes={onManageSpeciesCodes}
            onOpenChange={isPhoneLayout ? undefined : onCreateBoxOpenChange}
            onRequestOptions={onRequestOptions}
            confirmAction={confirmAction}
            onSelectBox={onSelectBox}
          />
        ) : null}
      </div>
    </section>
  );
}
