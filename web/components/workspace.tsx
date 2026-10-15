'use client';
import { useRef, useState } from 'react';
import type { WorkspaceData } from '@/lib/workspace-data';
import { Header } from './header';
import { TriageCard } from './triage-card';
import { CanvasPlaceholder } from './canvas-placeholder';
import { TracePanel } from './trace-panel';
import { Button } from './ui/button';
import { Sheet, SheetContent, SheetHeader, SheetTitle } from './ui/sheet';
import { Terminal } from 'lucide-react';

export function Workspace(data: WorkspaceData) {
  const [dryRun, setDryRun] = useState(true);
  const [selectedEvidence, setSelectedEvidence] = useState<string[]>([]);
  const [traceOpen, setTraceOpen] = useState(false);
  const triggerRef = useRef<HTMLButtonElement>(null);
  return (
    <>
      <Header tenant={data.tenant} tenants={data.tenants} />
      <main className="workspace">
        <TriageCard triage={data.triage} dryRun={dryRun} onDryRunChange={setDryRun} selectedEvidence={selectedEvidence} onSelectEvidence={key => { setSelectedEvidence([key]); if (window.matchMedia('(min-width: 640px) and (max-width: 1023px)').matches) setTraceOpen(true); }} applyPreview={data.applyPreview} applyRefused={data.applyRefused} />
        <CanvasPlaceholder subgraph={data.subgraph} selectedEvidence={selectedEvidence} />
        <section className="trace-inline">
          <TracePanel trace={data.triage.trace} selectedEvidence={selectedEvidence} />
        </section>
        <div className="trace-drawer">
          <Button ref={triggerRef} variant="secondary" className="trace-drawer-trigger" onClick={() => setTraceOpen(true)} aria-label="Open tool trace"><Terminal />Tool trace</Button>
          <Sheet open={traceOpen} onOpenChange={open => { setTraceOpen(open); if (!open) requestAnimationFrame(() => triggerRef.current?.focus()); }}>
            <SheetContent className="trace-sheet">
              <SheetHeader><SheetTitle>Tool trace</SheetTitle></SheetHeader>
              <TracePanel trace={data.triage.trace} selectedEvidence={selectedEvidence} />
            </SheetContent>
          </Sheet>
        </div>
      </main>
    </>
  );
}
