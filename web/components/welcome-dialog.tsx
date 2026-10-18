'use client';
import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react';
import * as DialogPrimitive from '@radix-ui/react-dialog';
import {
  ArrowLeft,
  ArrowRight,
  Compass,
  FlaskConical,
  GitPullRequest,
  ShieldCheck,
  Waypoints,
  X,
} from 'lucide-react';
import { Button } from './ui/button';
import { startTour } from './tour';

const SEEN_KEY = 'keystone-welcome-v1';
const OPEN_EVENT = 'keystone:welcome';

/** Opens the welcome dialog from anywhere (empty state, help button). */
export function openWelcome() {
  window.dispatchEvent(new Event(OPEN_EVENT));
}

/** Small node glyphs in the canvas's own shapes and colours, so the intro teaches the legend. */
function Node({
  x,
  y,
  kind,
  label,
}: {
  x: number;
  y: number;
  kind: 'issue' | 'pr' | 'code' | 'person' | 'knowledge';
  label: string;
}) {
  const color = `var(--node-${kind})`;
  return (
    <g>
      {kind === 'issue' && (
        <circle cx={x} cy={y} r="9" fill="none" stroke={color} strokeWidth="2.5" />
      )}
      {kind === 'pr' && <circle cx={x} cy={y} r="9" fill={color} />}
      {kind === 'code' && <rect x={x - 9} y={y - 9} width="18" height="18" rx="4" fill={color} />}
      {kind === 'person' && <circle cx={x} cy={y} r="9" fill={color} />}
      {kind === 'knowledge' && (
        <polygon
          points={`${x},${y - 10} ${x + 10},${y} ${x},${y + 10} ${x - 10},${y}`}
          fill={color}
        />
      )}
      <text x={x} y={y + 24} textAnchor="middle" className="welcome-node-label">
        {label}
      </text>
    </g>
  );
}

function GraphDiagram({ evidence = false }: { evidence?: boolean }) {
  const stroke = evidence ? 'var(--evidence)' : 'var(--edge-strong)';
  const width = evidence ? 2.5 : 1.5;
  return (
    <svg
      viewBox="0 0 360 96"
      className="welcome-diagram"
      role="img"
      aria-label="New issue, similar past issue, pull request, module, then reviewer"
    >
      <line x1="30" y1="40" x2="100" y2="40" stroke={stroke} strokeWidth={width} />
      <line x1="100" y1="40" x2="170" y2="40" stroke={stroke} strokeWidth={width} />
      <line x1="170" y1="40" x2="240" y2="40" stroke={stroke} strokeWidth={width} />
      <line x1="240" y1="40" x2="320" y2="40" stroke={stroke} strokeWidth={width} />
      <Node x={30} y={40} kind="issue" label="New issue" />
      <Node x={100} y={40} kind="issue" label="Similar issue" />
      <Node x={170} y={40} kind="pr" label="Fix PR" />
      <Node x={240} y={40} kind="code" label="Module" />
      <Node x={320} y={40} kind="person" label="Reviewer" />
    </svg>
  );
}

interface Slide {
  title: string;
  body: ReactNode;
  visual: ReactNode;
}

const slides: Slide[] = [
  {
    title: 'Keystone triages new issues for an open-source org',
    visual: <GraphDiagram />,
    body: (
      <p>
        It loads the org&apos;s public GitHub history (issues, pull requests, reviews, labels and
        docs) into a FalkorDB graph. When a new issue arrives, it follows the graph from the issue
        to the code it touches and the people who know that code.
      </p>
    ),
  },
  {
    title: 'One triage answers four questions',
    visual: (
      <ul className="welcome-facts">
        <li>
          <span className="welcome-fact-dot" style={{ background: 'var(--node-code)' }} />
          Which code is affected?
        </li>
        <li>
          <span className="welcome-fact-dot" style={{ background: 'var(--node-person)' }} />
          Who should review it, given expertise and current load?
        </li>
        <li>
          <span className="welcome-fact-dot" style={{ background: 'var(--risk)' }} />
          Is that code a bus-factor risk?
        </li>
        <li>
          <span className="welcome-fact-dot" style={{ background: 'var(--node-knowledge)' }} />
          Which past decisions apply?
        </li>
      </ul>
    ),
    body: (
      <p>
        Each answer comes from a Cypher query over the graph. An LLM only phrases the summary, and a
        triage still works without one.
      </p>
    ),
  },
  {
    title: 'Every suggestion shows its proof',
    visual: (
      <div className="welcome-proof">
        <span className="citation evidence-button type-caption" aria-hidden="true">
          <Waypoints />4 sources
        </span>
        <GraphDiagram evidence />
      </div>
    ),
    body: (
      <p>
        Choose <strong>sources</strong> beside any label, reviewer or risk. The path that supports
        it lights up in amber on the graph, and the tool trace opens the exact query that found it.
      </p>
    ),
  },
  {
    title: 'It acts only where it is allowed to',
    visual: (
      <ul className="welcome-facts">
        <li>
          <ShieldCheck className="icon-good" />
          Labels, assignees and comments go only to the sandbox repo, through its webhook.
        </li>
        <li>
          <FlaskConical className="icon-muted" />
          Apply here is always a dry run, so nothing on GitHub changes.
        </li>
        <li>
          <GitPullRequest className="icon-pr" />
          Reject a reviewer and Keystone stores a rule that the next triage follows.
        </li>
      </ul>
    ),
    body: (
      <p>
        Proof, in the header, measures how often the graph&apos;s top three reviewers include the
        person who actually reviewed, compared with CODEOWNERS and similar-issue baselines.
      </p>
    ),
  },
  {
    title: 'Take a quick tour',
    visual: (
      <div className="welcome-tour-icon" aria-hidden="true">
        <Compass />
      </div>
    ),
    body: (
      <p>
        The tour points out each part of the workspace in about a minute. You can reopen it, or this
        introduction, from the help button in the header.
      </p>
    ),
  },
];

export function WelcomeDialog() {
  const [open, setOpen] = useState(false);
  const [index, setIndex] = useState(0);
  const primaryRef = useRef<HTMLButtonElement>(null);

  const show = useCallback(() => {
    setIndex(0);
    setOpen(true);
  }, []);
  useEffect(() => {
    let seen = true;
    try {
      seen = localStorage.getItem(SEEN_KEY) === '1';
    } catch {
      /* Storage is optional; skip the auto-open. */
    }
    if (!seen) show();
    window.addEventListener(OPEN_EVENT, show);
    return () => window.removeEventListener(OPEN_EVENT, show);
  }, [show]);

  function close(next: boolean) {
    setOpen(next);
    if (!next) {
      try {
        localStorage.setItem(SEEN_KEY, '1');
      } catch {
        /* Storage is optional. */
      }
    }
  }

  const slide = slides[index];
  const last = index === slides.length - 1;
  return (
    <DialogPrimitive.Root open={open} onOpenChange={close}>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Overlay className="dialog-overlay" />
        <DialogPrimitive.Content
          className="welcome-dialog"
          aria-describedby="welcome-body"
          data-welcome
          onOpenAutoFocus={(e) => {
            e.preventDefault();
            primaryRef.current?.focus();
          }}
          onKeyDown={(e) => {
            if (e.key === 'ArrowRight' && !last) setIndex((i) => i + 1);
            if (e.key === 'ArrowLeft' && index > 0) setIndex((i) => i - 1);
          }}
        >
          <div className="welcome-visual">{slide.visual}</div>
          <div className="welcome-text">
            <p className="type-caption muted-text">
              Welcome to Keystone · {index + 1} of {slides.length}
            </p>
            <DialogPrimitive.Title className="welcome-title">{slide.title}</DialogPrimitive.Title>
            <div id="welcome-body" className="welcome-body">
              {slide.body}
            </div>
          </div>
          <div className="welcome-footer">
            <div className="welcome-dots" role="tablist" aria-label="Slides">
              {slides.map((s, i) => (
                <button
                  key={s.title}
                  type="button"
                  role="tab"
                  aria-selected={i === index}
                  aria-label={`Slide ${i + 1}: ${s.title}`}
                  className="welcome-dot"
                  onClick={() => setIndex(i)}
                />
              ))}
            </div>
            <div className="welcome-nav">
              {index > 0 && (
                <Button variant="ghost" onClick={() => setIndex((i) => i - 1)}>
                  <ArrowLeft />
                  Back
                </Button>
              )}
              {last ? (
                <>
                  <Button variant="secondary" onClick={() => close(false)}>
                    Explore on my own
                  </Button>
                  <Button
                    ref={primaryRef}
                    variant="default"
                    onClick={() => {
                      close(false);
                      window.setTimeout(startTour, 200);
                    }}
                  >
                    <Compass />
                    Start the tour
                  </Button>
                </>
              ) : (
                <Button ref={primaryRef} variant="default" onClick={() => setIndex((i) => i + 1)}>
                  Next
                  <ArrowRight />
                </Button>
              )}
            </div>
          </div>
          <DialogPrimitive.Close className="dialog-close" aria-label="Close welcome">
            <X />
          </DialogPrimitive.Close>
        </DialogPrimitive.Content>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  );
}
