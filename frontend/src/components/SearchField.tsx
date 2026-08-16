import type { KeyboardEvent } from 'react';

type SearchFieldLabels = {
  label: string;
  placeholder: string;
};

export default function SearchField({
  labels,
  activeDescendant,
  controls,
  expanded,
  onChange,
  onKeyDown,
  onSubmit,
  value,
}: {
  labels: SearchFieldLabels;
  activeDescendant?: string;
  controls?: string;
  expanded?: boolean;
  onChange: (value: string) => void;
  onKeyDown?: (event: KeyboardEvent<HTMLInputElement>) => void;
  onSubmit?: () => void;
  value: string;
}) {
  return (
    <form
      className="search-field"
      onSubmit={(event) => {
        event.preventDefault();
        onSubmit?.();
      }}
    >
      <label>
        <span>{labels.label}</span>
        <input
          aria-activedescendant={activeDescendant}
          aria-controls={controls}
          aria-expanded={expanded}
          aria-autocomplete="list"
          value={value}
          placeholder={labels.placeholder}
          type="search"
          onChange={(event) => onChange(event.target.value)}
          onKeyDown={onKeyDown}
        />
      </label>
    </form>
  );
}
