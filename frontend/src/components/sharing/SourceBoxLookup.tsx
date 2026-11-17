import { useState } from 'react';
import { apiGet, getStoredActiveOrganizationId, ApiResourceCancelledError } from '../../api/client';
import { useSharingResource } from '../../hooks/useSharingResource';
import type { Translator } from '../../i18n';
import type { BoxItem, PaginatedResponse, UserProfile } from '../../types';
import { readForSourceAdmin } from '../../api/sourceSharingSession';
import { sharingError } from '../../utils/boxSharing';
import SearchField from '../SearchField';

export type SourceBox = Pick<BoxItem, 'id' | 'global_code' | 'organization'>;
export default function SourceBoxLookup({ profile, organizationId, selected, onSelect, t }: {
  profile: UserProfile; organizationId: number; selected: SourceBox | null; onSelect: (box: SourceBox | null) => void; t: Translator;
}) {
  const [query, setQuery] = useState('');
  const [search, setSearch] = useState('');
  const [offset, setOffset] = useState(0);
  const result = useSharingResource(`${organizationId}:${search}:${offset}`, async signal => {
    if (!search) return { results: [], next: null };
    if (getStoredActiveOrganizationId() !== organizationId) throw new ApiResourceCancelledError();
    const response = await readForSourceAdmin(profile, organizationId, signal, () => apiGet<PaginatedResponse<BoxItem>>(`/api/boxes/?q=${encodeURIComponent(search)}&limit=20&offset=${offset}`, { signal, cache: 'no-store' }));
    // Defence in depth, not an authorization substitute. Django owns the scope.
    if (response.results.some(box => box.organization.id !== organizationId)) throw new ApiResourceCancelledError();
    return response;
  });
  if (selected) return <div className="sharing-selected-box"><span><small>{t('shareSelectedBox')}</small><strong><bdi>{selected.global_code}</bdi></strong><small>{selected.organization.name}</small></span>
    <button className="secondary-button" type="button" onClick={() => { onSelect(null); setSearch(''); }}>{t('shareChangeBox')}</button></div>;
  return <div className="sharing-lookup">
    <SearchField labels={{ label: t('shareFindBox'), placeholder: t('shareBoxSearchHint') }} value={query}
      onChange={value => { setQuery(value); setSearch(''); }} onSubmit={() => { setOffset(0); setSearch(query.trim()); }} />
    <button className="secondary-button" type="button" disabled={!query.trim()} onClick={() => { setOffset(0); setSearch(query.trim()); }}>{t('shareSearch')}</button>
    {search && result.loading ? <p role="status">{t('loading')}</p> : null}
    {search && result.error ? <div role="alert"><p>{sharingError(result.error, t)}</p><button className="secondary-button" type="button" onClick={result.refresh}>{t('reloadAction')}</button></div> : null}
    {search && result.data ? <>
      {result.data.results.length === 0 ? <p>{t('shareNoBox')}</p> : <ul className="sharing-lookup-results">{result.data.results.map(box => <li key={box.id}><button className="secondary-button" type="button" onClick={() => onSelect(box)}><bdi>{box.global_code}</bdi></button></li>)}</ul>}
      <nav className="sharing-actions" aria-label={t('shareBoxPages')}>
        <button className="secondary-button" type="button" disabled={offset === 0} onClick={() => setOffset(value => Math.max(0, value - 20))}>{t('sharePrevious')}</button>
        <button className="secondary-button" type="button" disabled={!result.data.next} onClick={() => setOffset(value => value + 20)}>{t('shareNext')}</button>
      </nav>
    </> : null}
  </div>;
}
