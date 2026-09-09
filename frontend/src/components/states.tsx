interface EmptyStateProps {
  onPick: (company: string) => void;
  blocked?: boolean;
}

const EXAMPLES = ["Stripe", "Snowflake", "Datadog", "Figma"];

export function EmptyState({ onPick, blocked = false }: EmptyStateProps) {
  return (
    <div className="empty">
      <h1 className="empty__title">Walk in knowing the room.</h1>
      <p className="empty__lead">
        Type a company name and you'll get a two-minute briefing: what they do, who runs
        it, what's happened lately, the numbers, and what could catch you off guard.
      </p>
      <p className="empty__hint">Try one:</p>
      <div className="empty__chips">
        {EXAMPLES.map((company) => (
          <button key={company} type="button" className="chip" onClick={() => onPick(company)} disabled={blocked}>
            {company}
          </button>
        ))}
      </div>
    </div>
  );
}

interface ErrorPanelProps {
  message: string;
  code: string | null;
  onRetry?: () => void;
}

export function ErrorPanel({ message, code, onRetry }: ErrorPanelProps) {
  const notFound = code === "not_found";
  const quota = code === "quota_exceeded" || code === "daily_limit";
  return (
    <div className={`notice notice--${notFound || quota ? "info" : "error"}`} role="alert">
      {quota && <p className="notice__title">Research limit reached</p>}
      <p className="notice__message">{message}</p>
      <p className="notice__hint">
        {notFound
          ? "Check the spelling, or try the company's full legal name."
          : quota
          ? "This attempt was not saved. You can still open your saved briefings."
          : "Nothing was saved. You can try again."}
      </p>
      {onRetry && (
        <button type="button" className="button button--ghost" onClick={onRetry}>
          {quota ? "Try again later" : "Try again"}
        </button>
      )}
    </div>
  );
}
