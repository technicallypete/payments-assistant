/** Small display helpers. */

export function relativeTime(iso: string, now: Date = new Date()): string {
  const diff = (now.getTime() - new Date(iso).getTime()) / 1000;
  if (diff < 45) return "just now";
  if (diff < 3600) return `${Math.round(diff / 60)} min ago`;
  if (diff < 86400) return `${Math.round(diff / 3600)} h ago`;
  return new Date(iso).toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

/** "4:59" style countdown; null once expired. */
export function countdown(expiresAt: string, now: Date = new Date()): string | null {
  const ms = new Date(expiresAt).getTime() - now.getTime();
  if (ms <= 0) return null;
  const s = Math.floor(ms / 1000);
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

export const REASON_LABEL: Record<string, string> = {
  amount_over_threshold: "Over the $2,000 limit",
  customer_requested: "Asked for a person",
  dispute: "Dispute",
  other: "Other",
};

export function greetingFor(hour: number): string {
  if (hour < 5) return "Burning the midnight oil.";
  if (hour < 12) return "Morning.";
  if (hour < 17) return "Afternoon.";
  return "Evening.";
}

/** Fired on window when an action executes in Stripe, so panels showing money can refresh. */
export const MONEY_MOVED_EVENT = "penny:money-moved";
