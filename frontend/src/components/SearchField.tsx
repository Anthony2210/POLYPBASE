type SearchFieldLabels = {
  label: string;
  placeholder: string;
};

export default function SearchField({
  labels,
  onChange,
  onSubmit,
  value,
}: {
  labels: SearchFieldLabels;
  onChange: (value: string) => void;
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
          value={value}
          placeholder={labels.placeholder}
          type="search"
          onChange={(event) => onChange(event.target.value)}
        />
      </label>
    </form>
  );
}
