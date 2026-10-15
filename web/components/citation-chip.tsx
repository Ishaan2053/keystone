export function CitationChip({ citation, selected, onSelect, number }: { citation: string; selected: boolean; onSelect: (key: string) => void; number: number }) {
  return <button type="button" className="citation type-mono-sm" aria-label={`Show evidence: ${citation}`} aria-pressed={selected} title={citation} onClick={() => onSelect(citation)}>[{number}]</button>;
}
