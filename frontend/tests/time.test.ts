import { describe, expect, it } from "vitest";

import { relativeTime } from "../src/utils/time";

const now = new Date("2026-09-09T12:00:00Z");
const ago = (seconds: number) => new Date(now.getTime() - seconds * 1000).toISOString();

describe("relativeTime", () => {
  it.each([
    [10, "just now"],
    [60, "1 minute ago"],
    [60 * 7, "7 minutes ago"],
    [3600, "1 hour ago"],
    [3600 * 5, "5 hours ago"],
    [86400 * 2, "2 days ago"],
  ])("renders %i seconds ago as %s", (seconds, expected) => {
    expect(relativeTime(ago(seconds), now)).toBe(expected);
  });

  it("falls back to a date once a week has passed", () => {
    expect(relativeTime(ago(86400 * 30), now)).toMatch(/Aug/);
  });

  it("returns nothing for an unparseable timestamp", () => {
    expect(relativeTime("not a date", now)).toBe("");
  });
});
