import type { Claim } from '@/lib/types';

/**
 * A citation chip. The accessible label and number stay as before; selecting a
 * chip now carries the owning claim so the workspace can resolve every citation
 * of that claim, not just this one key.
 */
export function CitationChip({ citation, selected, onSelect, number, claimId, claim }: { citation: string; selected: boolean; onSelect: (claimId: string, claim: Claim) => void; number: number; claimId: string; claim: Claim }) {
  return <button type="button" className="citation type-mono-sm" aria-label={`Show evidence: ${citation}`} aria-pressed={selected} title={citation} onClick={() => onSelect(claimId, claim)}>[{number}]</button>;
}
