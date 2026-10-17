'use client';

import { useEffect, useRef, useState } from 'react';
import { getRuns, runKeyUuid } from '@/lib/api-client';
import type { RunSummary } from '@/lib/types';

export function LiveRuns({ tenant, selectedRunKey, autoSelect, onSelect }: {
  tenant: string;
  selectedRunKey: string | null;
  autoSelect: boolean;
  onSelect: (runKey: string) => Promise<boolean>;
}) {
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [unavailable, setUnavailable] = useState(false);
  const [newKeys, setNewKeys] = useState<Set<string>>(new Set());
  const [announcement, setAnnouncement] = useState('');
  const attempted = useRef<string | null>(null);

  useEffect(() => {
    let active = true;
    let inFlight = false;
    let seen: Set<string> | null = null;
    let controller: AbortController | null = null;
    setRuns([]); setLoaded(false); setUnavailable(false); setNewKeys(new Set()); setAnnouncement('');
    attempted.current = null;
    async function poll() {
      if (inFlight) return;
      inFlight = true;
      controller = new AbortController();
      try {
        const body = await getRuns(tenant, 20, controller.signal);
        if (!active) return;
        const unique = new Map<string, RunSummary>();
        for (const run of [...body.runs].sort((a, b) => b.at - a.at)) {
          const uuid = runKeyUuid(run.run_key);
          if (uuid && !unique.has(`run:${uuid}`)) unique.set(`run:${uuid}`, { ...run, run_key: `run:${uuid}` });
        }
        const next = [...unique.values()].slice(0, 20);
        const arrivals = seen ? next.filter(run => !seen!.has(run.run_key)) : [];
        if (arrivals.length) {
          setNewKeys(current => new Set([...current, ...arrivals.map(run => run.run_key)]));
          setAnnouncement(`${arrivals.length} new ${arrivals.length === 1 ? 'run' : 'runs'} available.`);
        }
        seen = new Set([...(seen ?? []), ...next.map(run => run.run_key)]);
        setRuns(next); setLoaded(true); setUnavailable(false);
      } catch {
        if (active) setUnavailable(true);
      } finally {
        inFlight = false;
      }
    }
    void poll();
    const timer = setInterval(() => { void poll(); }, 5_000);
    return () => { active = false; clearInterval(timer); controller?.abort(); };
  }, [tenant]);

  useEffect(() => {
    const first = runs[0]?.run_key;
    if (autoSelect && !selectedRunKey && first && attempted.current !== first) {
      attempted.current = first;
      void onSelect(first);
    }
  }, [runs, autoSelect, selectedRunKey, onSelect]);

  return <section className="live-runs" aria-label="Recent runs">
    <h2 className="type-body-strong">Recent runs</h2>
    <p className="sr-only" role="status" aria-live="polite">{announcement}</p>
    {unavailable && <p className="type-caption muted-text">{loaded ? 'Run list stale; retrying automatically.' : 'Run list unavailable; retrying automatically.'}</p>}
    {!unavailable && !loaded && <p className="type-caption muted-text">Loading runs…</p>}
    {loaded && !runs.length && !unavailable && <p className="type-caption muted-text">No runs yet</p>}
    <ul>{runs.map(run => <li key={run.run_key}>
      <button type="button" className="live-run" data-live-run={run.run_key} aria-pressed={selectedRunKey === run.run_key} onClick={() => {
        setNewKeys(current => { const next = new Set(current); next.delete(run.run_key); return next; });
        void onSelect(run.run_key);
      }}>
        <span className="type-mono-sm live-run-issue">{run.issue_key}</span>
        <span className="type-caption live-run-meta"><time dateTime={new Date(run.at * 1000).toISOString()}>{new Date(run.at * 1000).toLocaleString()}</time><span>{run.fallback ? 'Fallback' : 'Completed'}</span>{newKeys.has(run.run_key) && selectedRunKey !== run.run_key && <strong>New</strong>}</span>
      </button>
    </li>)}</ul>
  </section>;
}
