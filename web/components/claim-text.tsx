import { keyKind, keyLabel, keyUrl } from '@/lib/github';

const PATTERN = /`([^`]+)`|\b((?:person|team|repo|module|file|issue|pr|label|doc|decision):[^\s,;()`]+[^\s,;()`.])/g;

/**
 * Claim text as a reader expects it: inline `code` spans, and stable graph keys
 * (`person:octocat`, `module:o/r/src`) shown as their short labels in the
 * entity's colour, with the full key on hover. The text itself is unchanged.
 */
export function ClaimText({ text }: { text: string }) {
  const parts: React.ReactNode[] = [];
  let last = 0;
  for (const match of text.matchAll(PATTERN)) {
    const index = match.index ?? 0;
    if (index > last) parts.push(text.slice(last, index));
    if (match[1] !== undefined) parts.push(<code key={index}>{match[1]}</code>);
    else {
      const key = match[2];
      const kind = keyKind(key);
      const url = keyUrl(key);
      const label = keyLabel(key);
      const className = `key-ref key-${(kind ?? 'other').toLowerCase()}`;
      parts.push(url
        ? <a key={index} className={className} href={url} target="_blank" rel="noopener noreferrer" title={key}>{label}</a>
        : <span key={index} className={className} title={key}>{label}</span>);
    }
    last = index + match[0].length;
  }
  if (last < text.length) parts.push(text.slice(last));
  return <>{parts}</>;
}
