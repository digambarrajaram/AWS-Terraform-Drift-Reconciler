/** Shorten long Terraform addresses for chart ticks (keep the resource tail). */
export function shortenResourceId(id: string, maxLen = 42): string {
  if (id.length <= maxLen) return id;
  const tailLen = Math.min(18, Math.floor(maxLen * 0.45));
  const headLen = maxLen - tailLen - 1;
  return `${id.slice(0, headLen)}…${id.slice(-tailLen)}`;
}
