export function label(id: string): string {
  return id.replace(/_/g, " ").toUpperCase();
}

export function signed(value: number, digits = 2): string {
  const fixed = Math.abs(value).toFixed(digits);
  if (Number(fixed) === 0) return `0.${"0".repeat(digits)}`;
  return `${value < 0 ? "−" : "+"}${fixed}`;
}

export function plain(value: number, digits = 2): string {
  return value.toFixed(digits);
}

export function perLap(value: number): string {
  return `${(value * 1000).toFixed(2)}‰`;
}

export function percentOf(value: number): string {
  return `${Math.round(value * 100)}%`;
}

export function isoDate(iso: string): string {
  return iso.slice(0, 10);
}

export function hoursSince(iso: string, now: Date): number {
  return (now.getTime() - new Date(iso).getTime()) / 3_600_000;
}

export function staleness(iso: string, now: Date): string {
  const hours = hoursSince(iso, now);
  if (hours < 1) return "under an hour ago";
  if (hours < 48) return `${Math.round(hours)}h ago`;
  const days = Math.round(hours / 24);
  return `${days}d ago`;
}
