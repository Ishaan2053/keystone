'use client';

import { useEffect, useRef, useState } from 'react';
import { AlertTriangle, CircleDot, MessageSquareText, History } from 'lucide-react';
import { getRuns, runKeyUuid } from '@/lib/api-client';
import { parseNumbered, relativeTime } from '@/lib/github';
import type { RunSummary } from '@/lib/types';
import { Counter, Token } from './github-ui';
import { GraphLoader } from './graph-loader';

export function LiveRuns({ tenant, selectedRunKey, autoSelect, onSelect, refreshKey = 0 }: {
  tenant: string;
  selectedRunKey: string | null;
  autoSelect: boolean;
  onSelect: (runKey: string, automatic?: boolean) => Promise<boolean>;
  /** A change polls at once (a UI triage just recorded a run) instead of waiting up to 5 s. */
  refreshKey?: number;
}) {
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [unavailable, setUnavailable] = useState(false);
  const [newKeys, setNewKeys] = useState<Set<string>>(new Set());
  const [announcement, setAnnouncement] = useState('');
  const attempted = useRef<string | null>(null);
  const pollRef = useRef<((refresh?: boolean) => Promise<void>) | null>(null);

  useEffect(() => {
    let active = true;
    let inFlight = false;
    // A refresh requested mid-poll runs once more right after it, so a run
    // recorded during that read still appears at once. Timer ticks never queue.
    let again = false;
    let seen: Set<string> | null = null;
    let controller: AbortController | null = null;
    setRuns([]); setLoaded(false); setUnavailable(false); setNewKeys(new Set()); setAnnouncement('');
    attempted.current = null;
    async function poll(refresh = false) {
      if (inFlight) { if (refresh) again = true; return; }
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
        if (again && active) { again = false; void poll(); }
      }
    }
    pollRef.current = poll;
    void poll();
    const timer = setInterval(() => { void poll(); }, 5_000);
    return () => { active = false; clearInterval(timer); controller?.abort(); pollRef.current = null; };
  }, [tenant]);

  useEffect(() => {
    if (refreshKey) void pollRef.current?.(true);
  }, [refreshKey]);

  useEffect(() => {
    // Remember attempts only within one eligible empty-view cycle. Returning
    // from a selected run to the bare URL must be able to select it again.
    if (!autoSelect || selectedRunKey) {
      attempted.current = null;
      return;
    }
    const first = runs[0]?.run_key;
    if (first && attempted.current !== first) {
      attempted.current = first;
      void onSelect(first, true);
    }
  }, [runs, autoSelect, selectedRunKey, onSelect]);

  useEffect(() => {
    // Only a committed selection counts as inspection. Failed or superseded
    // reads never change this key, and no async click continuation outlives it.
    if (selectedRunKey) setNewKeys(current => {
      if (!current.has(selectedRunKey)) return current;
      const next = new Set(current); next.delete(selectedRunKey); return next;
    });
  }, [selectedRunKey]);

  return <section className="live-runs" aria-label="Recent runs" data-tour="runs">
    <h2 className="section-heading"><History className="icon-muted" /><span>Recent runs</span>{runs.length > 0 && <Counter>{runs.length}</Counter>}</h2>
    <p className="sr-only" role="status" aria-live="polite">{announcement}</p>
    {unavailable && <p className="type-caption muted-text runs-note">{loaded ? 'Run list stale; retrying automatically.' : 'Run list unavailable; retrying automatically.'}</p>}
    {!unavailable && !loaded && <div className="runs-note"><GraphLoader label="Loading runs…" compact /></div>}
    {loaded && !runs.length && !unavailable && <p className="type-caption muted-text runs-note">No runs yet</p>}
    <ul className="runs-list">{runs.map(run => {
      const issue = parseNumbered(run.issue_key);
      return <li key={run.run_key}>
        <button type="button" className="live-run" data-live-run={run.run_key} aria-pressed={selectedRunKey === run.run_key} onClick={() => {
          void onSelect(run.run_key);
        }}>
          {issue ? <CircleDot className="icon-issue live-run-icon" /> : <MessageSquareText className="icon-muted live-run-icon" />}
          <span className="live-run-body">
            <span className="live-run-title">
              <span className="live-run-issue" title={run.issue_key}>{issue ? <>{issue.repo.split('/')[1]} <strong>#{issue.number}</strong></> : 'Title-only triage'}</span>
              <time className="type-caption muted-text" dateTime={new Date(run.at * 1000).toISOString()} title={new Date(run.at * 1000).toLocaleString()}>{relativeTime(run.at)}</time>
            </span>
            {run.summary && <span className="live-run-summary type-caption muted-text">{run.summary}</span>}
            {(run.fallback || (newKeys.has(run.run_key) && selectedRunKey !== run.run_key)) && <span className="live-run-meta">
              {run.fallback && <Token tone="attention" icon={<AlertTriangle />}>Fallback</Token>}
              {newKeys.has(run.run_key) && selectedRunKey !== run.run_key && <Token tone="link">New</Token>}
            </span>}
          </span>
        </button>
      </li>;
    })}</ul>
  </section>;
}
