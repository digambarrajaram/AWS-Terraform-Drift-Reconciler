/** Shorten long Terraform addresses for chart ticks — keep the leaf so siblings differ. */
export function shortenResourceId(id: string, maxLen = 42): string {
  if (id.length <= maxLen) return id;

  // Prefer the last 1–2 dotted segments (type.name[index]) for distinction.
  const parts = id.split('.');
  const leaf =
    parts.length >= 2
      ? parts.slice(-2).join('.')
      : parts[0] ?? id;

  if (leaf.length <= maxLen) return leaf;
  // End-heavy ellipsis so route/index suffixes stay visible.
  return `…${leaf.slice(-(maxLen - 1))}`;
}
