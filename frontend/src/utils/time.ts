const MINUTE = 60;
const HOUR = 60 * MINUTE;
const DAY = 24 * HOUR;

/** "3 minutes ago" -- a rep cares how fresh a briefing is, not its timestamp. */
export function relativeTime(iso: string, now: Date = new Date()): string {
  const then = new Date(iso);
  if (Number.isNaN(then.getTime())) return "";

  const seconds = Math.max(0, Math.round((now.getTime() - then.getTime()) / 1000));
  if (seconds < 45) return "just now";
  if (seconds < HOUR) return plural(Math.round(seconds / MINUTE), "minute");
  if (seconds < DAY) return plural(Math.round(seconds / HOUR), "hour");
  if (seconds < 7 * DAY) return plural(Math.round(seconds / DAY), "day");

  return then.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

const plural = (count: number, unit: string) =>
  `${count} ${unit}${count === 1 ? "" : "s"} ago`;
