'use client';
import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import {
  AlertTriangle,
  ArrowUpRight,
  Ban,
  Bot,
  CircleCheck,
  CircleDashed,
  CircleX,
  ExternalLink,
  Eye,
  FlaskConical,
  FolderTree,
  GitMerge,
  GitPullRequest,
  Lightbulb,
  ListChecks,
  MessageSquare,
  MessageSquareText,
  RotateCcw,
  Sparkles,
  Tag,
  UserPlus,
  Users,
  X,
} from 'lucide-react';
import type {
  ApplyResult,
  AskResult,
  Claim,
  FeedbackRequest,
  FeedbackResult,
  GraphNode,
  RecallRunData,
  RecordedAction,
  ToolResult,
  TriageResult,
} from '@/lib/types';
import {
  busFactors,
  decisionInfo,
  issueContext,
  reviewerStats,
  type BusFactor,
} from '@/lib/run-insights';
import { bareLogin, keyUrl, parseNumbered, relativeTime } from '@/lib/github';
import { claimId } from '@/lib/evidence-selection';
import { ApiError } from '@/lib/api-client';
import { Button } from './ui/button';
import { Input } from './ui/input';
import { Switch } from './ui/switch';
import { AskBox } from './ask-box';
import {
  Avatar,
  Counter,
  EvidenceButton,
  LabelPill,
  StateBadge,
  Token,
  UserLink,
  isSelected,
} from './github-ui';
import { ClaimText } from './claim-text';
import { Markdown } from './markdown';

const sections = {
  labels: 'Suggested labels',
  reviewers: 'Suggested reviewers',
  risks: 'Risk flags',
  decisions: 'Past decisions',
} as const;
type Section = keyof typeof sections;
const sectionIcons: Record<Section, ReactNode> = {
  labels: <Tag className="icon-muted" />,
  reviewers: <Users className="icon-person" />,
  risks: <AlertTriangle className="icon-risk" />,
  decisions: <Lightbulb className="icon-knowledge" />,
};

/** Recorded action key and status beside a claim row, plus its feedback control. */
type ActionSlot = (kind: 'label' | 'assign', value: string) => ReactNode;

function percent(share: number): string {
  return `${Number((share * 100).toFixed(1))}%`;
}

function BusMeter({ bus }: { bus: BusFactor }) {
  return (
    <div className="bus-meter" data-bus-meter>
      <div className="bus-bar" aria-hidden="true">
        <span style={{ width: percent(bus.topShare) }} />
      </div>
      <p className="type-caption muted-text">
        {bus.topLogin ? (
          <>
            <UserLink login={bus.topLogin} /> authored {bus.topCount} of {bus.total} merged PRs (
            {percent(bus.topShare)})
          </>
        ) : (
          `${percent(bus.topShare)} of ${bus.total} merged PRs by one author`
        )}
        {' · '}
        {bus.activeAuthors} active {bus.activeAuthors === 1 ? 'author' : 'authors'}
      </p>
    </div>
  );
}

/**
 * The four claim sections, shared by the fixture demo card and the live run
 * card. A claim is shown only when every citation is backed by the supplied
 * evidence, so neither card can present an unsupported suggestion. Each row
 * adds what the graph tools returned about it (rank, load, ownership, sources).
 */
function ClaimSections({
  triage,
  evidence,
  selectedEvidence,
  onSelectClaim,
  uncitedNote,
  labelColors = {},
  actionSlot,
}: {
  triage: TriageResult;
  evidence: Set<string>;
  selectedEvidence: string[];
  onSelectClaim: (claimId: string, claim: Claim) => void;
  uncitedNote: string | null;
  labelColors?: Record<string, string>;
  actionSlot?: ActionSlot;
}) {
  const reviewers = useMemo(() => reviewerStats(triage.trace), [triage.trace]);
  const buses = useMemo(() => busFactors(triage.trace), [triage.trace]);
  const decisions = useMemo(() => decisionInfo(triage.trace), [triage.trace]);
  const backed = (c: Claim) => c.citations.length > 0 && c.citations.every((k) => evidence.has(k));

  function row(section: Section, claim: Claim, index: number) {
    const id = claimId(section, index);
    const selected = isSelected(claim, selectedEvidence);
    const button = (
      <EvidenceButton claim={claim} selected={selected} onSelect={onSelectClaim} id={id} />
    );
    const key = `${claim.text}:${claim.citations.join('|')}`;
    if (section === 'labels') {
      const labelKey = claim.citations.find((k) => k.startsWith('label:'));
      return (
        <li key={key} className="claim-row label-row" data-selected={selected}>
          <LabelPill name={claim.text} color={labelKey ? labelColors[labelKey] : null} />
          <span className="row-end">
            {actionSlot?.('label', claim.text)}
            {button}
          </span>
        </li>
      );
    }
    if (section === 'reviewers') {
      const login = bareLogin(claim.text.trim());
      const stat = reviewers.get(login.toLowerCase());
      const owns = buses.find((b) => b.topLogin?.toLowerCase() === login.toLowerCase());
      return (
        <li key={key} className="claim-row reviewer-row" data-selected={selected}>
          <Avatar login={login} size={32} link />
          <div className="reviewer-main">
            <div className="reviewer-name">
              <span className="reviewer type-body-strong">
                <UserLink login={login} />
              </span>
              {stat?.rank === 1 && (
                <Token tone="good" icon={<Sparkles />}>
                  Top match
                </Token>
              )}
              {stat && stat.rules.length > 0 && (
                <Token tone="risk" icon={<Ban />} title={stat.rules.join(', ')}>
                  Avoid rule
                </Token>
              )}
            </div>
            {(stat || owns) && (
              <div className="reviewer-stats type-caption muted-text">
                {stat && (
                  <span title="Merged PRs in this module the person authored or reviewed, recent ones weighted 3x">
                    <GitMerge />
                    {stat.expertise} expertise
                  </span>
                )}
                {stat && (
                  <span title="Open review requests right now">
                    <Eye />
                    {stat.load} open {stat.load === 1 ? 'review' : 'reviews'}
                  </span>
                )}
                {owns && (
                  <Token tone="attention" title={`Bus factor: ${owns.module}`}>
                    Owns {percent(owns.topShare)} of merged PRs
                  </Token>
                )}
              </div>
            )}
            {actionSlot?.('assign', login)}
          </div>
          <span className="row-end">{button}</span>
        </li>
      );
    }
    if (section === 'risks') {
      const bus = buses.find((b) => claim.citations.includes(b.module));
      return (
        <li key={key} className="claim-row risk-row" data-selected={selected}>
          <div className="risk-body">
            <p>
              <ClaimText text={claim.text} />
            </p>
            {bus && <BusMeter bus={bus} />}
          </div>
          <span className="row-end">{button}</span>
        </li>
      );
    }
    const decisionKey = claim.citations.find((k) => k.startsWith('decision:'));
    const info = decisionKey ? decisions.get(decisionKey) : undefined;
    const sources = info?.sources.length
      ? info.sources
      : claim.citations
          .filter((k) => k.startsWith('pr:') || k.startsWith('issue:'))
          .map((k) => ({ key: k, url: keyUrl(k) }));
    const extra =
      info && info.summary !== claim.text
        ? info.summary
        : info?.rationale && info.rationale !== claim.text
          ? info.rationale
          : null;
    return (
      <li key={key} className="claim-row decision-row" data-selected={selected}>
        <div className="decision-body">
          <p>
            <ClaimText text={claim.text} />
          </p>
          {extra && (
            <p className="type-caption muted-text">
              <ClaimText text={extra} />
            </p>
          )}
          {info?.quote && (
            <blockquote className="decision-quote type-caption">
              <ClaimText text={info.quote} />
            </blockquote>
          )}
          {sources.length > 0 && (
            <p className="decision-sources type-caption">
              {sources.map((s) => {
                const parsed = parseNumbered(s.key);
                const label = parsed ? `${parsed.repo}#${parsed.number}` : s.key;
                return s.url ? (
                  <a
                    key={s.key}
                    href={s.url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="source-link"
                  >
                    <GitPullRequest className="icon-pr" />
                    {label}
                  </a>
                ) : (
                  <span key={s.key} className="source-link">
                    <GitPullRequest className="icon-pr" />
                    {label}
                  </span>
                );
              })}
            </p>
          )}
        </div>
        <span className="row-end">{button}</span>
      </li>
    );
  }

  return (
    <div className="claim-sections">
      {(Object.keys(sections) as Section[]).map((section) => {
        const claims = triage[section];
        const valid = claims.filter(backed);
        return (
          <section
            data-claim-section={section}
            className={`claim-section ${section}`}
            key={section}
          >
            <h2 className="section-heading">
              {sectionIcons[section]}
              <span>{sections[section]}</span>
              {valid.length > 0 && <Counter>{valid.length}</Counter>}
            </h2>
            {!valid.length && (
              <p className="muted-text type-caption section-empty">No suggestions</p>
            )}
            {uncitedNote && valid.length !== claims.length && (
              <p className="error-text type-caption">{uncitedNote}</p>
            )}
            {valid.length > 0 && (
              <ul className="claim-list">
                {claims.map((c, index) => (backed(c) ? row(section, c, index) : null))}
              </ul>
            )}
          </section>
        );
      })}
    </div>
  );
}

/** Tabs inside the issue panel. Every panel stays mounted, so an Ask answer or an open Reject form survives a tab switch. */
function PanelTabs({
  tabs,
  active,
  onChange,
}: {
  tabs: { id: string; label: string; icon: ReactNode; count?: number }[];
  active: string;
  onChange: (id: string) => void;
}) {
  const refs = useRef<Record<string, HTMLButtonElement | null>>({});
  function onKey(event: React.KeyboardEvent, index: number) {
    if (event.key !== 'ArrowRight' && event.key !== 'ArrowLeft') return;
    event.preventDefault();
    const next = tabs[(index + (event.key === 'ArrowRight' ? 1 : tabs.length - 1)) % tabs.length];
    onChange(next.id);
    refs.current[next.id]?.focus();
  }
  return (
    <div className="panel-tabs" role="tablist" aria-label="Issue views" data-tour="tabs">
      {tabs.map((tab, index) => (
        <button
          key={tab.id}
          ref={(el) => {
            refs.current[tab.id] = el;
          }}
          type="button"
          role="tab"
          id={`tab-${tab.id}`}
          aria-controls={`tabpanel-${tab.id}`}
          aria-selected={active === tab.id}
          tabIndex={active === tab.id ? 0 : -1}
          className="panel-tab"
          onClick={() => onChange(tab.id)}
          onKeyDown={(e) => onKey(e, index)}
        >
          {tab.icon}
          {tab.label}
          {tab.count !== undefined && tab.count > 0 && <Counter>{tab.count}</Counter>}
        </button>
      ))}
    </div>
  );
}

function TabPanel({ id, active, children }: { id: string; active: string; children: ReactNode }) {
  return (
    <div
      role="tabpanel"
      id={`tabpanel-${id}`}
      aria-labelledby={`tab-${id}`}
      hidden={active !== id}
      className="tab-panel"
    >
      {children}
    </div>
  );
}

const STATUS: Record<
  RecordedAction['status'],
  { label: string; icon: ReactNode; tone?: 'good' | 'risk' }
> = {
  proposed: { label: 'Proposed', icon: <CircleDashed /> },
  applied: { label: 'Applied', icon: <CircleCheck />, tone: 'good' },
  dry_run: { label: 'Skipped (dry run)', icon: <FlaskConical /> },
  refused: { label: 'Refused', icon: <Ban />, tone: 'risk' },
  rejected: { label: 'Rejected', icon: <CircleX />, tone: 'risk' },
};

function ActionStatus({ status }: { status: RecordedAction['status'] }) {
  const s = STATUS[status] ?? STATUS.proposed;
  return (
    <Token tone={s.tone} icon={s.icon}>
      {s.label}
    </Token>
  );
}

const KIND_ICON: Record<RecordedAction['kind'], ReactNode> = {
  label: <Tag className="icon-muted" />,
  assign: <UserPlus className="icon-person" />,
  comment: <MessageSquare className="icon-muted" />,
};

/** What a run would do on GitHub, in the order the agent recorded it, then the comment it drafted. */
function ActionsPanel({
  actions,
  statusOf,
  comment,
  labelColors,
  children,
}: {
  actions: RecordedAction[];
  statusOf: (action: RecordedAction) => RecordedAction['status'];
  comment: string;
  labelColors: Record<string, string>;
  children?: ReactNode;
}) {
  const colorFor = (name: string) =>
    Object.entries(labelColors).find(([key]) => key.endsWith(`/${name}`))?.[1] ?? null;
  return (
    <div className="actions-panel">
      {actions.length > 0 && (
        <section className="run-actions" data-run-actions aria-label="Recorded actions">
          <h2 className="section-heading">
            <ListChecks className="icon-muted" />
            <span>On GitHub</span>
            <Counter>{actions.length}</Counter>
          </h2>
          <ul className="claim-list">
            {actions.map((action) => (
              <li
                key={action.action_key}
                className="claim-row action-row"
                data-action-item={action.action_key}
              >
                {KIND_ICON[action.kind]}
                <div className="action-main">
                  <p>
                    {action.kind === 'label' && (
                      <>
                        Add label <LabelPill name={action.value} color={colorFor(action.value)} />
                      </>
                    )}
                    {action.kind === 'assign' && (
                      <>
                        Assign <Avatar login={action.value} size={18} />{' '}
                        <UserLink login={action.value} />
                      </>
                    )}
                    {action.kind === 'comment' && <>Post the triage comment</>}
                  </p>
                  {action.module && (
                    <p className="type-caption muted-text">
                      <FolderTree />
                      {action.module.split('/').slice(2).join('/') || action.module}
                    </p>
                  )}
                  {action.note && <p className="type-caption muted-text">{action.note}</p>}
                </div>
                <span className="row-end">
                  <ActionStatus status={statusOf(action)} />
                </span>
              </li>
            ))}
          </ul>
        </section>
      )}
      {children}
      <section className="comment-preview" aria-label="Comment preview">
        <h2 className="section-heading">
          <MessageSquareText className="icon-muted" />
          <span>Comment preview</span>
        </h2>
        {comment.trim() ? (
          <div className="gh-comment">
            <div className="gh-comment-header type-caption">
              <span className="bot-avatar" aria-hidden="true">
                <Bot />
              </span>
              <strong>Keystone</strong>
              <span className="muted-text">will comment on the issue</span>
            </div>
            <Markdown text={comment} className="gh-comment-body" />
          </div>
        ) : (
          <p className="muted-text type-caption section-empty">No comment drafted for this run.</p>
        )}
      </section>
    </div>
  );
}

function applyLabel(status: ApplyResult['actions'][number]['status']): {
  text: string;
  icon: ReactNode;
  tone?: 'good' | 'risk';
} {
  if (status === 'applied') return { text: 'Applied', icon: <CircleCheck />, tone: 'good' };
  if (status === 'dry_run') return { text: 'Skipped (dry run)', icon: <FlaskConical /> };
  return { text: 'Refused', icon: <Ban />, tone: 'risk' };
}

function ApplyResults({ notice, result }: { notice: string; result: ApplyResult | null }) {
  return (
    <div role="status" className="preview-status">
      {notice && <p className="type-caption">{notice}</p>}
      {result && (
        <ul>
          {result.actions.map((a) => {
            const s = applyLabel(a.status);
            return (
              <li key={a.action_key}>
                <Token tone={s.tone} icon={s.icon}>
                  {s.text}
                </Token>{' '}
                <span className="type-caption">{a.kind}</span>{' '}
                <span className="type-mono-sm">{a.value}</span>
                {a.note && <p className="type-caption muted-text">{a.note}</p>}
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}

interface FixtureProps {
  triage: TriageResult;
  dryRun: boolean;
  onDryRunChange: (value: boolean) => void;
  onSelectClaim: (claimId: string, claim: Claim) => void;
  selectedEvidence: string[];
  applyPreview: ApplyResult;
  applyRefused: ApplyResult;
  /** Fixture Ask: answers with the supplied `ask` fixture whatever the question. */
  onAsk?: (question: string) => Promise<AskResult>;
  onAskResult?: (result: AskResult | null) => void;
  labelColors?: Record<string, string>;
}

/** Fixture demo card, labelled as synthetic. */
export function TriageCard({
  triage,
  dryRun,
  onDryRunChange,
  onSelectClaim,
  selectedEvidence,
  applyPreview,
  applyRefused,
  onAsk,
  onAskResult,
  labelColors = {},
}: FixtureProps) {
  const [notice, setNotice] = useState('');
  const [actions, setActions] = useState<ApplyResult | null>(null);
  const [tab, setTab] = useState('triage');
  const evidence = new Set(triage.trace.flatMap((t) => t.evidence));
  function apply() {
    setActions(dryRun ? applyPreview : applyRefused);
    setNotice('Fixture preview: no GitHub changes');
  }
  return (
    <section className="panel triage-panel issue-panel" aria-label="Triage card" data-tour="issue">
      <header className="issue-header">
        <div className="issue-title-row">
          <h1 className="issue-title">Synthetic fixture demonstration</h1>
          <span className="badge type-caption">Fixture preview</span>
        </div>
        <p className="issue-meta type-caption muted-text">
          Illustrative fixture · synthetic issue and reviewers.
        </p>
      </header>
      <PanelTabs
        tabs={[
          { id: 'triage', label: 'Triage', icon: <Sparkles /> },
          { id: 'actions', label: 'Actions', icon: <ListChecks /> },
          ...(onAsk ? [{ id: 'ask', label: 'Ask', icon: <MessageSquare /> }] : []),
        ]}
        active={tab}
        onChange={setTab}
      />
      <div className="issue-scroll">
        <TabPanel id="triage" active={tab}>
          <ClaimSections
            triage={triage}
            evidence={evidence}
            selectedEvidence={selectedEvidence}
            onSelectClaim={onSelectClaim}
            uncitedNote="Uncited fixture claims hidden."
            labelColors={labelColors}
          />
        </TabPanel>
        <TabPanel id="actions" active={tab}>
          <ActionsPanel
            actions={[]}
            statusOf={(a) => a.status}
            comment={triage.comment_markdown}
            labelColors={labelColors}
          />
        </TabPanel>
        {onAsk && (
          <TabPanel id="ask" active={tab}>
            <AskBox
              onAsk={onAsk}
              onResult={onAskResult}
              onSelectClaim={onSelectClaim}
              selectedEvidence={selectedEvidence}
              note="Fixture answer · the question does not change the supplied fixture."
            />
          </TabPanel>
        )}
      </div>
      <footer className="apply-area" data-tour="apply">
        <div className="apply-controls">
          <Button variant="default" onClick={apply}>
            Apply to sandbox{dryRun ? ' (dry run)' : ''}
            <ArrowUpRight />
          </Button>
          <label className="dry-control">
            <Switch checked={dryRun} onCheckedChange={onDryRunChange} aria-label="Dry run" />
            <span className="type-caption">Dry run</span>
          </label>
        </div>
        <p className="type-caption muted-text">Fixture mode never writes to GitHub.</p>
        <ApplyResults notice={notice} result={actions} />
      </footer>
    </section>
  );
}

interface LiveProps {
  issueKey: string | null;
  tenant?: string;
  runKey?: string | null;
  /** Persisted recall for the selected run; null keeps the honest "no run" state. */
  run?: RecallRunData | null;
  /** The anchored issue's graph node (state, url), when the subgraph has it. */
  issueNode?: GraphNode | null;
  labelColors?: Record<string, string>;
  onSelectClaim?: (claimId: string, claim: Claim) => void;
  selectedEvidence?: string[];
  onApply?: (dryRun: boolean) => Promise<ApplyResult>;
  /** A triage is in flight; it can take tens of seconds. */
  triaging?: boolean;
  /** Record feedback on one of the selected run's actions. */
  onFeedback?: (body: FeedbackRequest) => Promise<FeedbackResult>;
  /** Triage the selected run's issue (or raw title) again; null when neither is known. */
  onRetriage?: (() => void) | null;
  onAsk?: (question: string) => Promise<AskResult>;
  onAskResult?: (result: AskResult | null) => void;
  /** Example runs to open from the empty state. */
  emptyActions?: ReactNode;
}

/**
 * Reject one suggested reviewer (an assign action): one reason field, then the
 * feedback route records it and creates an avoid Rule that rank_reviewers applies
 * on the next triage. Secondary styling: Reject is a destructive outline.
 */
function RejectAssign({
  action,
  onFeedback,
  onRetriage,
  triaging,
  onRejected,
}: {
  action: RecordedAction;
  onFeedback: (body: FeedbackRequest) => Promise<FeedbackResult>;
  onRetriage: (() => void) | null;
  triaging: boolean;
  onRejected: (result: FeedbackResult) => void;
}) {
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState('');
  const [pending, setPending] = useState(false);
  const [error, setError] = useState('');
  const [result, setResult] = useState<FeedbackResult | null>(null);
  const alive = useRef(true);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);
  const scope: FeedbackRequest['scope'] = action.module ? 'module' : 'global';

  async function submit() {
    const why = reason.trim();
    if (!why || pending) return;
    setPending(true);
    setError('');
    try {
      const response = await onFeedback({
        action_key: action.action_key,
        verdict: 'reject',
        reason: why,
        scope,
      });
      if (!alive.current) return;
      setResult(response);
      setOpen(false);
      onRejected(response);
    } catch (failure) {
      if (!alive.current) return;
      setError(
        failure instanceof ApiError && failure.status === 404
          ? 'Reject failed: the run did not propose this action.'
          : failure instanceof ApiError && failure.status === 422
            ? `Reject refused: ${failure.detail ?? 'the request was invalid'}.`
            : 'Reject failed; nothing was recorded.',
      );
    } finally {
      if (alive.current) setPending(false);
    }
  }

  if (result)
    return (
      <div className="feedback-result" role="status" data-feedback-result>
        <p className="type-caption">
          {result.rule_key ? (
            <>
              Avoid rule{' '}
              <span className="type-mono-sm" data-rule-key>
                {result.rule_key}
              </span>{' '}
              applies to the next triage{scope === 'module' ? ' of this module' : ''}.
            </>
          ) : (
            'Feedback recorded; no rule was created.'
          )}
        </p>
        {onRetriage ? (
          <Button
            type="button"
            onClick={onRetriage}
            disabled={triaging}
            aria-busy={triaging}
            data-retriage
          >
            <RotateCcw />
            {triaging ? 'Re-triaging…' : 'Re-triage'}
          </Button>
        ) : (
          <p className="type-caption muted-text">
            Re-triage needs an issue key or the original title; this run has neither.
          </p>
        )}
      </div>
    );

  if (!open)
    return (
      <Button
        type="button"
        className="button-reject"
        onClick={() => setOpen(true)}
        aria-label={`Reject @${bareLogin(action.value)}`}
      >
        <X />
        Reject
      </Button>
    );

  return (
    <form
      className="reject-form"
      onSubmit={(e) => {
        e.preventDefault();
        void submit();
      }}
      data-reject-form
    >
      <label htmlFor={`reject-${action.action_key}`} className="type-caption muted-text">
        Why? Keystone will remember this as a rule
        {scope === 'module' ? ' for this module' : ' for every module'}.
      </label>
      <Input
        id={`reject-${action.action_key}`}
        value={reason}
        onChange={(e) => setReason(e.target.value)}
        autoComplete="off"
        required
        autoFocus
      />
      <div className="reject-controls">
        <Button
          type="submit"
          className="button-reject"
          disabled={pending || !reason.trim()}
          aria-busy={pending}
        >
          {pending ? 'Rejecting…' : 'Reject reviewer'}
        </Button>
        <Button
          type="button"
          variant="ghost"
          onClick={() => {
            setOpen(false);
            setError('');
          }}
          disabled={pending}
        >
          Cancel
        </Button>
      </div>
      {error && (
        <p className="type-caption error-text" role="alert">
          {error}
        </p>
      )}
    </form>
  );
}

function modelLabel(run: RecallRunData['run']): string {
  return run.fallback || run.model === 'none' ? 'no-LLM fallback' : run.model;
}

function seconds(ms: number): string {
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)} s` : `${ms} ms`;
}

function topModule(trace: ToolResult[]): string | null {
  const data = trace.find((t) => t.tool === 'affected_modules' && !t.empty)?.data as
    { modules?: { path?: unknown }[] } | undefined;
  const path = data?.modules?.[0]?.path;
  return typeof path === 'string' && path ? path : null;
}

/** GitHub-issue header: title, number, state, repo, author and a link out. */
function IssueHeader({
  run,
  issueKey,
  issueNode,
}: {
  run: RecallRunData;
  issueKey: string | null;
  issueNode: GraphNode | null;
}) {
  const context = issueContext(run.triage.trace);
  const anchor = issueKey ?? (run.run.issue_key.startsWith('issue:') ? run.run.issue_key : null);
  const parsed = anchor ? parseNumbered(anchor) : null;
  const title =
    context?.title ??
    (typeof issueNode?.properties.title === 'string' ? issueNode.properties.title : null) ??
    run.request?.title ??
    'Title-only triage';
  const state = typeof issueNode?.properties.state === 'string' ? issueNode.properties.state : null;
  const url =
    (typeof issueNode?.properties.url === 'string' ? issueNode.properties.url : null) ??
    (anchor ? keyUrl(anchor) : null);
  return (
    <header className="issue-header">
      <div className="issue-title-row">
        <h1 className="issue-title">
          {title}
          {parsed && <span className="issue-number"> #{parsed.number}</span>}
        </h1>
        {url && (
          <a
            className="button button-ghost issue-link type-caption"
            href={url}
            target="_blank"
            rel="noopener noreferrer"
          >
            <ExternalLink />
            GitHub
          </a>
        )}
      </div>
      <div className="issue-meta type-caption muted-text">
        {anchor ? (
          <StateBadge state={state} />
        ) : (
          <Token icon={<MessageSquareText />}>No issue · title only</Token>
        )}
        {parsed && <span className="repo-name">{parsed.repo}</span>}
        {context?.author && (
          <span className="issue-author">
            <Avatar login={context.author} size={16} /> <UserLink login={context.author} /> opened
            {context.createdAt ? ` ${relativeTime(context.createdAt)}` : ''}
          </span>
        )}
      </div>
    </header>
  );
}

/**
 * Live card: when a persisted run is selected its real triage, evidence and
 * recorded actions are shown; otherwise it stays an honest "no run selected"
 * state with no synthetic claims or reviewers.
 */
export function LiveTriageCard({
  issueKey,
  tenant,
  runKey,
  run = null,
  issueNode = null,
  labelColors = {},
  onSelectClaim,
  selectedEvidence = [],
  onApply,
  triaging = false,
  onFeedback,
  onRetriage = null,
  onAsk,
  onAskResult,
  emptyActions,
}: LiveProps) {
  // The API refuses `dry_run: false` (403): live writes happen only through the
  // GitHub webhook, so the switch stays locked on rather than offering a write.
  const dryRun = true;
  const [pending, setPending] = useState(false);
  const [result, setResult] = useState<ApplyResult | null>(null);
  const [notice, setNotice] = useState('');
  const [tab, setTab] = useState('triage');
  const hasRun = !!onApply || !!runKey;
  const applyGeneration = useRef(0);
  // Feedback recorded in this view, by action key; a new run starts clean.
  const [rejected, setRejected] = useState<Record<string, FeedbackResult>>({});
  useEffect(() => {
    applyGeneration.current += 1;
    setPending(false);
    setResult(null);
    setNotice('');
    setRejected({});
    return () => {
      applyGeneration.current += 1;
    };
  }, [runKey]);
  const triage = run?.triage ?? null;
  // A claim is evidence-backed only if the run's trace or recall supplied the key.
  const evidence = new Set([
    ...(triage?.trace.flatMap((t) => t.evidence) ?? []),
    ...(run?.evidence ?? []),
  ]);
  const actions = run?.actions ?? [];
  const statusOf = (action: RecordedAction) =>
    rejected[action.action_key] ? 'rejected' : action.status;

  async function apply() {
    if (!onApply || pending) return;
    const generation = applyGeneration.current;
    setPending(true);
    setResult(null);
    setNotice('');
    try {
      const response = await onApply(dryRun);
      if (generation !== applyGeneration.current) return;
      setResult(response);
      setNotice('Apply complete.');
    } catch (error) {
      if (generation !== applyGeneration.current) return;
      setNotice(
        error instanceof ApiError && error.status === 403
          ? 'Refused: live GitHub writes happen only through the sandbox webhook.'
          : 'Apply failed; no action was reported as applied.',
      );
      setResult(null);
    } finally {
      if (generation === applyGeneration.current) setPending(false);
    }
  }

  const actionSlot: ActionSlot = (kind, value) => {
    const action = actions.find(
      (a) => a.kind === kind && bareLogin(a.value).toLowerCase() === bareLogin(value).toLowerCase(),
    );
    if (!action) return null;
    const status = statusOf(action);
    const canReject = kind === 'assign' && status !== 'rejected' && !!runKey;
    // Proposed is every action's starting state, so only a change is worth a token here.
    const showStatus = status !== 'proposed';
    if (!showStatus && !canReject && !rejected[action.action_key])
      return (
        <span
          className="row-action"
          data-action-status={status}
          data-action-key={action.action_key}
        />
      );
    return (
      <span
        className={`row-action${kind === 'assign' ? ' row-action-block' : ''}`}
        data-action-status={status}
        data-action-key={action.action_key}
      >
        {showStatus && <ActionStatus status={status} />}
        {kind === 'assign' && onFeedback && (canReject || rejected[action.action_key]) && (
          <span className="action-feedback">
            <RejectAssign
              key={`${runKey}:${action.action_key}`}
              action={action}
              onFeedback={onFeedback}
              onRetriage={onRetriage}
              triaging={triaging}
              onRejected={(response) =>
                setRejected((current) => ({ ...current, [action.action_key]: response }))
              }
            />
          </span>
        )}
      </span>
    );
  };

  const module = triage ? topModule(triage.trace) : null;
  const toolMs = triage ? triage.trace.reduce((sum, t) => sum + t.ms, 0) : 0;

  return (
    <section className="panel triage-panel issue-panel" aria-label="Triage card" data-tour="issue">
      {run && triage ? (
        <>
          <IssueHeader run={run} issueKey={issueKey} issueNode={issueNode} />
          <div className="run-summary" data-run-summary>
            <div className="run-summary-head type-caption">
              <span className="bot-avatar" aria-hidden="true">
                <Bot />
              </span>
              <strong>Keystone</strong>
              <span className="muted-text">
                triaged{' '}
                <time
                  dateTime={new Date(run.run.at * 1000).toISOString()}
                  title={new Date(run.run.at * 1000).toLocaleString()}
                >
                  {relativeTime(run.run.at)}
                </time>
              </span>
              {run.run.fallback && (
                <Token
                  tone="attention"
                  icon={<AlertTriangle />}
                  title="The LLM was unavailable; the graph answered alone"
                >
                  Fallback
                </Token>
              )}
              {module && (
                <Token icon={<FolderTree />} title="Most affected module">
                  {module}
                </Token>
              )}
            </div>
            <p className="run-summary-text">
              <ClaimText text={run.run.summary} />
            </p>
            <p className="run-facts type-caption muted-text">
              <span>{modelLabel(run.run)}</span>
              <span>
                {run.run.llm_calls} LLM {run.run.llm_calls === 1 ? 'call' : 'calls'}
              </span>
              <span>
                {triage.trace.length} graph {triage.trace.length === 1 ? 'query' : 'queries'} in{' '}
                {seconds(toolMs)}
              </span>
              <span>{seconds(run.run.ms)} total</span>
              <span className="type-mono-sm run-key" data-run-key title="Run key">
                {run.run.run_key}
              </span>
            </p>
          </div>
          <PanelTabs
            tabs={[
              { id: 'triage', label: 'Triage', icon: <Sparkles /> },
              { id: 'actions', label: 'Actions', icon: <ListChecks />, count: actions.length },
              ...(onAsk && onSelectClaim
                ? [{ id: 'ask', label: 'Ask', icon: <MessageSquare /> }]
                : []),
            ]}
            active={tab}
            onChange={setTab}
          />
          <div className="issue-scroll">
            <TabPanel id="triage" active={tab}>
              {onSelectClaim && (
                <ClaimSections
                  triage={triage}
                  evidence={evidence}
                  selectedEvidence={selectedEvidence}
                  onSelectClaim={onSelectClaim}
                  uncitedNote="Claims without persisted evidence were hidden."
                  labelColors={labelColors}
                  actionSlot={actionSlot}
                />
              )}
            </TabPanel>
            <TabPanel id="actions" active={tab}>
              <ActionsPanel
                actions={actions}
                statusOf={statusOf}
                comment={triage.comment_markdown}
                labelColors={labelColors}
              />
            </TabPanel>
            {onAsk && onSelectClaim && (
              <TabPanel id="ask" active={tab}>
                <AskBox
                  onAsk={onAsk}
                  onResult={onAskResult}
                  onSelectClaim={onSelectClaim}
                  selectedEvidence={selectedEvidence}
                  resetKey={tenant ?? null}
                />
              </TabPanel>
            )}
          </div>
        </>
      ) : (
        <>
          <div className="issue-empty" data-triage-empty>
            <span className="issue-empty-icon" aria-hidden="true">
              <Sparkles />
            </span>
            <h1 className="type-title">
              {issueKey ? `No triage run selected for ${issueKey}.` : 'No triage run selected.'}
            </h1>
            <p className="muted-text">
              Pick a run on the left, or enter an issue key, number or title and choose Run triage.
              Keystone reads the org&apos;s history from the graph and shows the proof for every
              suggestion.
            </p>
            {emptyActions}
          </div>
          {onAsk && onSelectClaim && (
            <div className="issue-empty-ask">
              <AskBox
                onAsk={onAsk}
                onResult={onAskResult}
                onSelectClaim={onSelectClaim}
                selectedEvidence={selectedEvidence}
                resetKey={tenant ?? null}
              />
            </div>
          )}
        </>
      )}
      <footer className="apply-area" data-tour="apply">
        {hasRun ? (
          <>
            <div className="apply-controls">
              <Button
                variant="default"
                onClick={apply}
                disabled={pending || !onApply}
                aria-busy={pending}
              >
                Apply to sandbox{dryRun ? ' (dry run)' : ''}
                <ArrowUpRight />
              </Button>
              <label className="dry-control">
                <Switch
                  checked={dryRun}
                  disabled
                  aria-label="Dry run"
                  aria-describedby="live-dry-run-note"
                />
                <span className="type-caption">Dry run</span>
              </label>
            </div>
            <p id="live-dry-run-note" className="type-caption muted-text" data-live-dry-run-note>
              Live apply is always a dry run here. Real GitHub writes happen only through the
              sandbox webhook.
            </p>
          </>
        ) : (
          <p className="type-caption muted-text">
            Apply is unavailable until a real triage run exists. API mode never writes to GitHub.
          </p>
        )}
        <ApplyResults notice={notice} result={result} />
      </footer>
    </section>
  );
}
