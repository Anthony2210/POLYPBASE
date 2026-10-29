import { type KeyboardEvent, useMemo, useState } from 'react';

import type { Translator } from '../i18n';
import type { BoxStrainOption } from '../types';
import PolypbaseIcon from './PolypbaseIcon';

const LISTBOX_ID = 'create-box-strain-listbox';


export function strainOptionId(id: number) {
  return `create-box-strain-option-${id}`;
}

function normalize(value: string) {
  return value.normalize('NFD').replace(/[̀-ͯ]/g, '').trim().toLocaleLowerCase('fr-FR');
}

function searchableText(option: BoxStrainOption) {
  const identity = option.identity;
  return normalize([
    option.species.scientific_name,
    option.name,
    option.code,
    ...(identity.status === 'usable' ? [identity.origin_code, identity.recorded_provenance_name ?? '', identity.strain_number] : []),
  ].join(' '));
}

type SpeciesGroup = { speciesId: number; scientificName: string; options: BoxStrainOption[] };

function groupBySpecies(options: BoxStrainOption[]) {
  const groups: SpeciesGroup[] = [];
  for (const option of options) {
    const last = groups[groups.length - 1];
    if (last?.speciesId === option.species.id) last.options.push(option);
    else groups.push({ speciesId: option.species.id, scientificName: option.species.scientific_name, options: [option] });
  }
  return groups;
}

export default function StrainPicker({
  options,
  selectedId,
  disabled,
  onSelect,
  t,
}: {
  options: BoxStrainOption[];
  selectedId: number | null;
  disabled: boolean;
  onSelect: (id: number) => void;
  t: Translator;
}) {
  const [query, setQuery] = useState('');
  const [activeId, setActiveId] = useState<number | null>(null);
  const normalizedQuery = normalize(query);
  const eligible = useMemo(() => options.filter((option) => option.identity.status === 'usable'), [options]);
  const visible = useMemo(() => {
    const sorted = [...eligible].sort((first, second) =>
      first.species.scientific_name.localeCompare(second.species.scientific_name)
      || first.species.id - second.species.id);
    if (!normalizedQuery) return sorted;
    const terms = normalizedQuery.split(/\s+/);
    return sorted.filter((option) => {
      const haystack = searchableText(option);
      return terms.every((term) => haystack.includes(term));
    });
  }, [normalizedQuery, eligible]);
  const groups = groupBySpecies(visible);
  const activeOption = visible.find((option) => option.id === activeId)
    ?? visible.find((option) => option.id === selectedId) ?? null;

  function moveActive(offset: number) {
    if (!visible.length) return;
    const index = activeOption ? visible.indexOf(activeOption) : -1;
    const next = visible[Math.min(Math.max(index + offset, 0), visible.length - 1)];
    setActiveId(next.id);
    document.getElementById?.(strainOptionId(next.id))?.scrollIntoView?.({ block: 'nearest' });
  }

  function handleKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (disabled) return;
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault();
      moveActive(event.key === 'ArrowDown' ? 1 : -1);
    } else if (event.key === 'Enter') {
      // Enter chooses a strain; it never submits the creation form from the search field.
      event.preventDefault();
      if (activeOption) onSelect(activeOption.id);
    }
  }

  return (
    <div className="strain-picker">
      <label className="strain-picker-search">
        <PolypbaseIcon name="search" size={16} />
        <span className="sr-only">{t('createBoxStrainSearch')}</span>
        <input
          type="search"
          role="combobox"
          aria-expanded="true"
          aria-controls={LISTBOX_ID}
          aria-autocomplete="list"
          aria-activedescendant={activeOption ? strainOptionId(activeOption.id) : undefined}
          autoComplete="off"
          disabled={disabled}
          value={query}
          placeholder={t('createBoxStrainSearch')}
          onChange={(event) => {
            setQuery(event.target.value);
            setActiveId(null);
          }}
          onKeyDown={handleKeyDown}
        />
      </label>
      <div
        className="strain-picker-list"
        id={LISTBOX_ID}
        role="listbox"
        aria-label={t('createBoxStrain')}
        aria-disabled={disabled || undefined}
      >
        {groups.map((group) => (
          <div className="strain-picker-group" role="group" key={group.speciesId} aria-labelledby={`create-box-species-${group.speciesId}`}>
            <p className="strain-picker-species" id={`create-box-species-${group.speciesId}`}>
              <em>{group.scientificName}</em>
            </p>
            {group.options.map((option) => renderStrainOption({
              option,
              isActive: option.id === activeOption?.id,
              isSelected: option.id === selectedId,
              disabled,
              onSelect: () => {
                setActiveId(option.id);
                onSelect(option.id);
              },
              t,
            }))}
          </div>
        ))}
        {!visible.length ? (
          <p className="strain-picker-empty" role="status">{t(eligible.length ? 'createBoxStrainNoMatch' : 'createBoxNoOptions')}</p>
        ) : null}
      </div>
    </div>
  );
}

function renderStrainOption({
  option,
  isActive,
  isSelected,
  disabled,
  onSelect,
  t,
}: {
  option: BoxStrainOption;
  isActive: boolean;
  isSelected: boolean;
  disabled: boolean;
  onSelect: () => void;
  t: Translator;
}) {
  const identity = option.identity;
  if (identity.status !== 'usable') return null;
  const className = ['strain-picker-option', isSelected ? 'is-selected' : '', isActive ? 'is-active' : '']
    .filter(Boolean).join(' ');
  const name = `${t('createBoxStrainLabel')} ${identity.strain_number}`;
  const details = `${option.active_box_count} ${t(option.active_box_count === 1 ? 'createBoxActiveBox' : 'createBoxActiveBoxes')}`;

  return (
    <div
      key={option.id}
      className={className}
      id={strainOptionId(option.id)}
      role="option"
      aria-selected={isSelected}
      aria-disabled={disabled || undefined}
      onMouseDown={(event) => event.preventDefault()}
      onClick={() => { if (!disabled) onSelect(); }}
    >
      <span className="strain-picker-option-main">
        <strong title={name}>{name}</strong>
        <small>{details}</small>
      </span>
      <span className="strain-picker-option-code" title={option.code}>
        {option.code}
      </span>
    </div>
  );
}
