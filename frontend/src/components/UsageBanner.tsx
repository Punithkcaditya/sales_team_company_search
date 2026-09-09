import type { UsageStatus } from "../types";

interface Props {
  usage: UsageStatus | null;
  unavailable: boolean;
  onRefresh: () => void;
}

export function UsageBanner({ usage, unavailable, onRefresh }: Props) {
  if (!usage) {
    return <div className="usage usage--muted" role="status">
      {unavailable ? "Usage status unavailable. Research availability will be checked when you search." : "Checking research availability…"}
    </div>;
  }

  const demo = usage.mode === "demo";
  const exhausted = usage.remaining === 0;
  const provider = usage.provider === "gemini" ? "Gemini" : "Demo";
  const reset = usage.resets_at
    ? new Date(usage.resets_at).toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" })
    : null;

  return (
    <div className={`usage${exhausted ? " usage--exhausted" : ""}`} role="status">
      <div className="usage__row">
        <span className="usage__mode"><span className={`usage__dot${demo ? " usage__dot--demo" : ""}`} aria-hidden="true" />
          {demo ? "Demo mode" : `Live research · ${provider}`}
        </span>
        {!demo && usage.remaining !== null && <span className="usage__count">
          {usage.remaining} of {usage.daily_limit} briefings left today
        </span>}
      </div>
      <p className="usage__detail">
        {demo ? "Sample briefings only. No AI tokens or live search calls are used."
          : usage.daily_limit === null ? "Provider token balance is unavailable here. Requests are subject to your provider's limits."
          : exhausted ? `This app's daily limit is reached. Resets ${reset} (your local time). Saved briefings are still available.`
          : `This app's own daily limit, resetting ${reset} (your local time). Gemini's free tier is also rate limited per minute, so it may pause sooner.`}
      </p>
      {exhausted && <button type="button" className="usage__refresh" onClick={onRefresh}>Refresh availability</button>}
    </div>
  );
}
