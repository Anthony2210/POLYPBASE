import { useRef, type KeyboardEvent } from 'react';

import PolypbaseIcon from './PolypbaseIcon';

type SearchFieldLabels = {
  label: string;
  placeholder: string;
};

export default function SearchField({
  labels,
  activeDescendant,
  controls,
  expanded,
  clearLabel,
  onChange,
  onKeyDown,
  onSubmit,
  value,
  variant = 'default',
}: {
  labels: SearchFieldLabels;
  activeDescendant?: string;
  controls?: string;
  expanded?: boolean;
  clearLabel?: string;
  onChange: (value: string) => void;
  onKeyDown?: (event: KeyboardEvent<HTMLInputElement>) => void;
  onSubmit?: () => void;
  value: string;
  variant?: 'default' | 'control-deck';
}) {
  const inputRef = useRef<HTMLInputElement | null>(null);

  return (
    <form
      className={variant === 'control-deck' ? 'search-field is-control-deck' : 'search-field'}
      onSubmit={(event) => {
        event.preventDefault();
        onSubmit?.();
      }}
    >
      <label>
        <span className={variant === 'control-deck' ? 'sr-only' : undefined}>{labels.label}</span>
        <input
          ref={inputRef}
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
        <PolypbaseIcon name="search" size={17} />
        {variant === 'control-deck' && value && clearLabel ? (
          <button
            className="search-field-clear"
            type="button"
            aria-label={clearLabel}
            title={clearLabel}
            onClick={() => {
              onChange('');
              inputRef.current?.focus();
            }}
          >
            <PolypbaseIcon name="close" size={15} />
          </button>
        ) : null}
      </label>
    </form>
  );
}
