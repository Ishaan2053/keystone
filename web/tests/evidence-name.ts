/**
 * Each claim has one evidence button whose accessible name lists every key it
 * cites (`Show evidence: a, b, c`). This matches the button that cites `key`.
 */
export function evidenceName(key: string): RegExp {
  const escaped = key.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  return new RegExp(`^Show evidence: (?:.*, )?${escaped}(?:,|$)`);
}
