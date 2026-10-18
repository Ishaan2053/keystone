'use client';
import { useState, type ReactNode } from 'react';
import { ExternalLink, Network, Play, Webhook } from 'lucide-react';
import { SANDBOX_ISSUES_URL } from '@/lib/sandbox';
import { Button } from './ui/button';
import { Input } from './ui/input';

/** The left column: start a triage, then pick from recent runs. */
export function LiveSidebar({
  issueInput,
  onIssueInputChange,
  onTriage,
  onLoadIssue,
  loading,
  triaging,
  triageStatus,
  issueHint,
  recentRuns,
}: {
  issueInput: string;
  onIssueInputChange: (value: string) => void;
  onTriage: () => void;
  onLoadIssue: () => void;
  loading: boolean;
  triaging: boolean;
  triageStatus: string;
  issueHint: string;
  recentRuns: ReactNode;
}) {
  return (
    <aside className="panel sidebar" aria-label="Runs">
      <form
        className="issue-form"
        data-tour="triage-form"
        onSubmit={(e) => {
          e.preventDefault();
          onTriage();
        }}
      >
        <h2 className="type-body-strong">Triage an issue</h2>
        <label htmlFor="issue-key" className="type-caption muted-text">
          Issue key, number or title
        </label>
        <Input
          id="issue-key"
          value={issueInput}
          onChange={(e) => onIssueInputChange(e.target.value)}
          placeholder="issue:owner/repo#123, #123 or a title"
          autoComplete="off"
          required
        />
        <div className="issue-actions">
          <Button type="submit" disabled={triaging} aria-busy={triaging} data-run-triage>
            <Play />
            {triaging ? 'Running triage…' : 'Run triage'}
          </Button>
          <Button
            type="button"
            variant="ghost"
            onClick={onLoadIssue}
            aria-busy={loading}
            title="Show the issue's neighbourhood in the graph without triaging"
          >
            <Network />
            Load issue graph
          </Button>
        </div>
        {issueHint && (
          <p className="type-caption muted-text" data-issue-hint>
            {issueHint}
          </p>
        )}
        {triageStatus && (
          <p className="type-caption muted-text triage-status" role="status" data-triage-status>
            {triaging && <span className="spinner" aria-hidden="true" />}
            {triageStatus}
          </p>
        )}
      </form>
      <div className="sandbox-callout" data-sandbox-callout>
        <p className="sandbox-callout-title type-body-strong">
          <Webhook aria-hidden="true" />
          Try the live webhook
        </p>
        <p className="type-caption muted-text">
          Open an issue in the sandbox repo. In about 30 seconds Keystone&rsquo;s bot labels it and
          posts a cited triage comment, and the run appears below.
        </p>
        <a
          className="sandbox-callout-link type-caption"
          href={SANDBOX_ISSUES_URL}
          target="_blank"
          rel="noreferrer"
        >
          Open sandbox issues
          <ExternalLink aria-hidden="true" />
        </a>
      </div>
      {recentRuns}
    </aside>
  );
}

/** Fixture left column: the synthetic issue form, which never changes the supplied fixture. */
export function FixtureSidebar() {
  const [issue, setIssue] = useState('Synthetic fixture demonstration');
  const [notice, setNotice] = useState('');
  return (
    <aside className="panel sidebar" aria-label="Runs">
      <form
        className="issue-form"
        data-tour="triage-form"
        onSubmit={(e) => {
          e.preventDefault();
          setNotice(
            'Fixture preview redisplayed. Issue input does not change the supplied fixture.',
          );
        }}
      >
        <label htmlFor="issue" className="type-body-strong">
          Issue title or number
        </label>
        <Input id="issue" value={issue} onChange={(e) => setIssue(e.target.value)} required />
        <Button type="submit">
          <Play />
          Run triage preview
        </Button>
        <p className="type-caption muted-text" role="status">
          {notice || 'Illustrative fixture · synthetic issue and reviewers.'}
        </p>
      </form>
    </aside>
  );
}
