import type { ToolResult } from './types';

/**
 * Typed readers over a run's persisted trace. They only pick fields out of what
 * each graph tool already returned (spec section 7); nothing is scored, ranked
 * or recomputed here, so every number on screen is the graph's own answer.
 */

export interface IssueContext {
  key: string;
  title: string;
  number: number | null;
  author: string | null;
  createdAt: number | null;
}

export interface ReviewerStat {
  login: string;
  rank: number;
  expertise: number;
  load: number;
  score: number;
  rules: string[];
}

export interface BusFactor {
  module: string;
  total: number;
  topLogin: string | null;
  topCount: number;
  topShare: number;
  activeAuthors: number;
}

export interface DecisionSource {
  key: string;
  url: string | null;
}

export interface DecisionInfo {
  key: string;
  summary: string;
  rationale: string | null;
  quote: string | null;
  sources: DecisionSource[];
}

type Json = Record<string, unknown>;
const isObject = (value: unknown): value is Json =>
  typeof value === 'object' && value !== null && !Array.isArray(value);
const str = (value: unknown): string | null =>
  typeof value === 'string' && value.trim() ? value : null;
const num = (value: unknown): number | null =>
  typeof value === 'number' && Number.isFinite(value) ? value : null;

function toolData(trace: ToolResult[], tool: string): Json[] {
  return trace
    .filter((t) => t.tool === tool && !t.empty && isObject(t.data))
    .map((t) => t.data as Json);
}

export function issueContext(trace: ToolResult[]): IssueContext | null {
  const data = toolData(trace, 'issue_context')[0];
  const key = data && str(data.issue);
  if (!data || !key) return null;
  return {
    key,
    title: str(data.title) ?? key,
    number: num(data.number),
    author: str(data.author),
    createdAt: num(data.created_at),
  };
}

/** rank_reviewers rows by lowercase login, ranked in the order the graph returned them. */
export function reviewerStats(trace: ToolResult[]): Map<string, ReviewerStat> {
  const stats = new Map<string, ReviewerStat>();
  for (const data of toolData(trace, 'rank_reviewers')) {
    const rows = Array.isArray(data.reviewers) ? data.reviewers.filter(isObject) : [];
    rows.forEach((row, index) => {
      const login = str(row.login);
      if (!login || stats.has(login.toLowerCase())) return;
      stats.set(login.toLowerCase(), {
        login,
        rank: index + 1,
        expertise: num(row.expertise) ?? 0,
        load: num(row.load) ?? 0,
        score: num(row.score) ?? 0,
        rules: Array.isArray(row.rules)
          ? row.rules.filter((r): r is string => typeof r === 'string')
          : [],
      });
    });
  }
  return stats;
}

export function busFactors(trace: ToolResult[]): BusFactor[] {
  return toolData(trace, 'bus_factor').flatMap((data) => {
    const module = str(data.module);
    const share = num(data.top_share);
    if (!module || share === null) return [];
    const top = isObject(data.top) ? data.top : {};
    return [
      {
        module,
        total: num(data.total) ?? 0,
        topLogin: str(top.login),
        topCount: num(top.n) ?? 0,
        topShare: share,
        activeAuthors: num(data.active_authors) ?? 0,
      },
    ];
  });
}

/** find_decisions rows by decision key. */
export function decisionInfo(trace: ToolResult[]): Map<string, DecisionInfo> {
  const decisions = new Map<string, DecisionInfo>();
  for (const data of toolData(trace, 'find_decisions')) {
    for (const row of Array.isArray(data.decisions) ? data.decisions.filter(isObject) : []) {
      const key = str(row.decision);
      if (!key || decisions.has(key)) continue;
      const sources = (Array.isArray(row.sources) ? row.sources.filter(isObject) : []).flatMap(
        (s) => (str(s.key) ? [{ key: s.key as string, url: str(s.url) }] : []),
      );
      decisions.set(key, {
        key,
        summary: str(row.summary) ?? key,
        rationale: str(row.rationale),
        quote: str(row.quote),
        sources,
      });
    }
  }
  return decisions;
}

/** A one-line, human summary of what one tool call returned, for the trace row. */
export function toolSummary(tool: ToolResult): string {
  if (tool.empty) return 'No evidence';
  const data = tool.data;
  if (Array.isArray(data)) return `${data.length} ${data.length === 1 ? 'row' : 'rows'} returned`;
  if (!isObject(data)) return 'Structured result';
  const count = (value: unknown, one: string, many: string) => {
    const n = Array.isArray(value) ? value.length : 0;
    return `${n} ${n === 1 ? one : many}`;
  };
  switch (tool.tool) {
    case 'issue_context':
      return str(data.title) ?? 'Issue found';
    case 'affected_modules': {
      const modules = Array.isArray(data.modules) ? data.modules.filter(isObject) : [];
      const top = modules[0] && str(modules[0].path);
      return `${count(modules, 'module', 'modules')}${top ? ` · top ${top}` : ''}`;
    }
    case 'rank_reviewers': {
      const rows = Array.isArray(data.reviewers) ? data.reviewers.filter(isObject) : [];
      const top = rows[0] && str(rows[0].login);
      return `${count(rows, 'reviewer', 'reviewers')}${top ? ` · top @${top}` : ''}`;
    }
    case 'bus_factor': {
      const share = num(data.top_share);
      const top = isObject(data.top) ? str(data.top.login) : null;
      return share !== null && top
        ? `${Math.round(share * 100)}% of merged PRs by @${top}`
        : 'Ownership measured';
    }
    case 'find_decisions':
      return `${count(data.decisions, 'decision', 'decisions')} · ${count(data.chunks, 'doc passage', 'doc passages')}`;
    case 'explain_path':
      return `${count(data.keys, 'node', 'nodes')} on the path`;
    case 'similar_issues':
      return count(data.issues, 'similar issue', 'similar issues');
    default: {
      const firstList = Object.values(data).find(Array.isArray);
      return firstList
        ? `${firstList.length} ${firstList.length === 1 ? 'row' : 'rows'} returned`
        : 'Structured result';
    }
  }
}
