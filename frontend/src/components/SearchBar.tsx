import { useEffect, useRef, useState, type FormEvent } from "react";

interface Props {
  onSearch: (company: string) => void;
  onCancel: () => void;
  busy: boolean;
}

export function SearchBar({ onSearch, onCancel, busy }: Props) {
  const [value, setValue] = useState("");
  const input = useRef<HTMLInputElement>(null);

  // Cmd/Ctrl+K jumps back to the search box from anywhere.
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key.toLowerCase() === "k" && (event.metaKey || event.ctrlKey)) {
        event.preventDefault();
        input.current?.focus();
        input.current?.select();
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  const submit = (event: FormEvent) => {
    event.preventDefault();
    const company = value.trim();
    if (company && !busy) onSearch(company);
  };

  return (
    <form className="search" onSubmit={submit} role="search">
      <label className="visually-hidden" htmlFor="company">
        Company name
      </label>
      <div className="search__field">
        <SearchIcon />
        <input
          id="company"
          ref={input}
          className="search__input"
          type="text"
          autoComplete="off"
          placeholder="Which company are you meeting?"
          value={value}
          onChange={(event) => setValue(event.target.value)}
          disabled={busy}
        />
        <kbd className="search__hint" aria-hidden="true">
          {isMac() ? "⌘" : "Ctrl"} K
        </kbd>
      </div>

      {busy ? (
        <button type="button" className="button button--ghost" onClick={onCancel}>
          Cancel
        </button>
      ) : (
        <button type="submit" className="button" disabled={!value.trim()}>
          Research
        </button>
      )}
    </form>
  );
}

const isMac = () =>
  typeof navigator !== "undefined" && /mac/i.test(navigator.platform || navigator.userAgent);

function SearchIcon() {
  return (
    <svg className="search__icon" viewBox="0 0 20 20" aria-hidden="true">
      <circle cx="9" cy="9" r="6" fill="none" stroke="currentColor" strokeWidth="2" />
      <path d="M13.5 13.5 L17.5 17.5" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
    </svg>
  );
}
