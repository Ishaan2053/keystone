'use client';
import { useState, type CSSProperties, type ReactNode } from 'react';
import { CircleCheck, CircleDot, Waypoints } from 'lucide-react';
import type { Claim } from '@/lib/types';
import { avatarUrl, bareLogin, profileUrl } from '@/lib/github';

/** A GitHub avatar, loaded from github.com in the browser; initials if it fails. */
export function Avatar({ login, size = 20, link = false }: { login: string; size?: number; link?: boolean }) {
  const name = bareLogin(login);
  const [failed, setFailed] = useState(false);
  const image = failed
    ? <span className="avatar avatar-fallback" style={{ width: size, height: size, fontSize: Math.max(9, size * 0.42) }} aria-hidden="true">{name.slice(0, 2).toUpperCase()}</span>
    // eslint-disable-next-line @next/next/no-img-element
    : <img className="avatar" src={avatarUrl(name, size)} width={size} height={size} alt="" loading="lazy" onError={() => setFailed(true)} />;
  return link ? <a href={profileUrl(name)} target="_blank" rel="noopener noreferrer" aria-label={`@${name} on GitHub`} className="avatar-link">{image}</a> : image;
}

/** `@login`, linked to the GitHub profile, in the person colour. */
export function UserLink({ login, className = '' }: { login: string; className?: string }) {
  const name = bareLogin(login);
  return <a className={`user-link ${className}`} href={profileUrl(name)} target="_blank" rel="noopener noreferrer">@{name}</a>;
}

/** A GitHub label pill in the label's own colour (hex without `#`), else neutral. */
export function LabelPill({ name, color }: { name: string; color?: string | null }) {
  const hex = color && /^[0-9a-f]{6}$/i.test(color) ? `#${color}` : null;
  return <span className={`label-pill${hex ? '' : ' label-pill-plain'}`} style={hex ? { '--label-color': hex } as CSSProperties : undefined}>{name}</span>;
}

/** Open / closed issue state, as on GitHub. */
export function StateBadge({ state }: { state: string | null }) {
  if (!state) return null;
  const open = state.toLowerCase() === 'open';
  return <span className={`state-badge ${open ? 'state-open' : 'state-closed'}`}>{open ? <CircleDot /> : <CircleCheck />}{open ? 'Open' : 'Closed'}</span>;
}

/** A small rounded count, like GitHub's Counter. */
export function Counter({ children }: { children: ReactNode }) {
  return <span className="counter">{children}</span>;
}

/** A neutral or toned token: `good`, `attention`, `risk`, `done`, `link` or plain. */
export function Token({ tone, icon, children, title }: { tone?: 'good' | 'attention' | 'risk' | 'done' | 'link'; icon?: ReactNode; children: ReactNode; title?: string }) {
  return <span className={`token${tone ? ` token-${tone}` : ''}`} title={title}>{icon}{children}</span>;
}

/** A claim lights up when it cites everything selected: its own evidence, or a node it cites. */
export function isSelected(claim: Claim, selectedEvidence: string[]): boolean {
  return selectedEvidence.length > 0 && selectedEvidence.every(k => claim.citations.includes(k));
}

/**
 * The one evidence control per claim. Selecting it resolves every citation of
 * the claim on the graph and in the trace, which is what the numbered chips did
 * one key at a time.
 */
export function EvidenceButton({ claim, selected, onSelect, id }: { claim: Claim; selected: boolean; onSelect: (claimId: string, claim: Claim) => void; id: string }) {
  const n = claim.citations.length;
  return (
    <button type="button" className="citation evidence-button type-caption" aria-pressed={selected} aria-label={`Show evidence: ${claim.citations.join(', ')}`} title={claim.citations.join('\n')} onClick={() => onSelect(id, claim)} data-tour-evidence>
      <Waypoints />{n} {n === 1 ? 'source' : 'sources'}
    </button>
  );
}
