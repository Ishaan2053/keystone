import type { GraphKind } from './types';

/**
 * GitHub-flavoured display helpers. Everything here is derived from a stable
 * key or a login; nothing is fetched or stored. Avatars load straight from
 * github.com in the browser, so no personal data enters the graph (Rule 6).
 */

export function avatarUrl(login: string, size = 40): string {
  return `https://github.com/${encodeURIComponent(login)}.png?size=${size * 2}`;
}

export function profileUrl(login: string): string {
  return `https://github.com/${encodeURIComponent(login)}`;
}

/** Strip one leading `@`. */
export function bareLogin(value: string): string {
  return value.startsWith('@') ? value.slice(1) : value;
}

const PREFIX_KIND: Record<string, GraphKind> = {
  person: 'Person',
  team: 'Team',
  repo: 'Repo',
  module: 'Module',
  file: 'File',
  issue: 'Issue',
  pr: 'PR',
  comment: 'Comment',
  label: 'Label',
  doc: 'Doc',
  chunk: 'Chunk',
  decision: 'Decision',
  run: 'AgentRun',
  action: 'Action',
  feedback: 'Feedback',
  rule: 'Rule',
};

/** The graph kind a stable key names (`pr:owner/repo#1` -> PR), or null. */
export function keyKind(key: string): GraphKind | null {
  return PREFIX_KIND[key.slice(0, key.indexOf(':'))] ?? null;
}

/** `issue:owner/repo#12` -> { repo: 'owner/repo', number: '12' }. */
export function parseNumbered(key: string): { repo: string; number: string } | null {
  const match = /^(?:issue|pr):([^#\s]+\/[^#\s]+)#(\S+)$/.exec(key);
  return match ? { repo: match[1], number: match[2] } : null;
}

/** The github.com page for an issue, PR, person or repo key; null for the rest. */
export function keyUrl(key: string): string | null {
  const kind = keyKind(key);
  const rest = key.slice(key.indexOf(':') + 1);
  if (kind === 'Issue' || kind === 'PR') {
    const parsed = parseNumbered(key);
    if (!parsed || !/^\d+$/.test(parsed.number)) return null;
    return `https://github.com/${parsed.repo}/${kind === 'PR' ? 'pull' : 'issues'}/${parsed.number}`;
  }
  if (kind === 'Person') return profileUrl(rest);
  if (kind === 'Repo') return `https://github.com/${rest}`;
  return null;
}

/** A short, human label for a key: `#12`, `@octocat`, the module path, the label name. */
export function keyLabel(key: string): string {
  const kind = keyKind(key);
  const rest = key.slice(key.indexOf(':') + 1);
  if (kind === 'Issue' || kind === 'PR') return `#${parseNumbered(key)?.number ?? rest}`;
  if (kind === 'Person') return `@${rest}`;
  if (kind === 'Module' || kind === 'File' || kind === 'Doc' || kind === 'Label') {
    // module:owner/repo/path -> path; label:owner/repo/name -> name.
    const parts = rest.split('/');
    return parts.length > 2 ? parts.slice(2).join('/') : rest;
  }
  if (kind === 'Decision' || kind === 'AgentRun' || kind === 'Action')
    return `${kind === 'AgentRun' ? 'run' : kind.toLowerCase()} ${rest.slice(0, 7)}`;
  return rest;
}

/** "3 min ago" from a Unix timestamp in seconds. */
export function relativeTime(at: number, now = Date.now()): string {
  const seconds = Math.max(0, Math.round(now / 1000 - at));
  if (seconds < 45) return 'just now';
  const units: [number, string][] = [
    [60, 'min'],
    [3600, 'hour'],
    [86400, 'day'],
    [604800, 'week'],
    [2629800, 'month'],
    [31557600, 'year'],
  ];
  let chosen = units[0];
  for (const unit of units) if (seconds >= unit[0]) chosen = unit;
  const n = Math.max(1, Math.round(seconds / chosen[0]));
  return `${n} ${chosen[1]}${n === 1 ? '' : 's'} ago`;
}
