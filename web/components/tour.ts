'use client';
import type { DriveStep } from 'driver.js';

/**
 * The guided tour: one step per part of the workspace, each pointing at a
 * `data-tour` anchor. Steps whose anchor is not on screen (no run selected,
 * tablet layout) are skipped. driver.js and its stylesheet load on first use.
 */
const steps: DriveStep[] = [
  {
    element: '[data-tour="runs"]',
    popover: {
      title: 'Recent runs',
      description:
        "Every triage Keystone has recorded, newest first. Triages started by the sandbox repo's webhook appear here on their own.",
      side: 'right',
      align: 'start',
    },
  },
  {
    element: '[data-tour="triage-form"]',
    popover: {
      title: 'Triage an issue',
      description:
        'Enter a full key (issue:owner/repo#123), a number, or just a title, then choose Run triage. A live triage takes up to a minute and never writes to GitHub.',
      side: 'right',
      align: 'start',
    },
  },
  {
    element: '[data-tour="issue"]',
    popover: {
      title: "The issue and Keystone's read of it",
      description:
        'The issue as on GitHub, a one-line summary, and the model, LLM calls and graph queries the run used.',
      side: 'right',
      align: 'start',
    },
  },
  {
    element: '[data-tour="tabs"]',
    popover: {
      title: 'Three views',
      description:
        'Triage lists the suggestions. Actions lists what Keystone would do on GitHub, including the comment it drafted. Ask answers questions from past decisions and docs.',
      side: 'bottom',
      align: 'start',
    },
  },
  {
    element: '[data-tour-evidence]',
    popover: {
      title: 'Sources',
      description:
        'Every suggestion has a sources button. Choose it to light up the supporting path on the graph and open the query that found it.',
      side: 'left',
      align: 'center',
    },
  },
  {
    element: '[data-tour="graph"]',
    popover: {
      title: 'Evidence graph',
      description:
        "The issue's neighbourhood in FalkorDB. Shapes and colours mark people, code, PRs, issues and knowledge, and the amber path is the selected evidence. Hover over a node for details.",
      side: 'left',
      align: 'start',
    },
  },
  {
    element: '[data-tour="trace"]',
    popover: {
      title: 'Tool trace',
      description:
        'Each graph query the run made: what it found, its parameters, the raw result and the Cypher, ready to copy.',
      side: 'left',
      align: 'start',
    },
  },
  {
    element: '[data-tour="apply"]',
    popover: {
      title: 'Apply to sandbox',
      description:
        'Preview the labels, assignees and comment. Here it is always a dry run. Real writes happen only in the sandbox repo, through its webhook.',
      side: 'top',
      align: 'start',
    },
  },
  {
    element: '[data-tour="proof"]',
    popover: {
      title: 'Proof',
      description:
        "How often the graph's top three reviewers include the person who actually reviewed, compared with CODEOWNERS and similar-issue baselines.",
      side: 'bottom',
      align: 'end',
    },
  },
  {
    element: '[data-tour="help"]',
    popover: {
      title: 'Help',
      description: 'Reopen the introduction or this tour at any time.',
      side: 'bottom',
      align: 'end',
    },
  },
];

export async function startTour() {
  const { driver } = await import('driver.js');
  const visible = steps.filter((step) => {
    const el = typeof step.element === 'string' ? document.querySelector(step.element) : null;
    return el instanceof HTMLElement && el.offsetParent !== null;
  });
  if (!visible.length) return;
  driver({
    steps: visible,
    showProgress: true,
    progressText: '{{current}} of {{total}}',
    nextBtnText: 'Next',
    prevBtnText: 'Back',
    doneBtnText: 'Done',
    popoverClass: 'keystone-tour',
    stagePadding: 4,
    stageRadius: 10,
    overlayOpacity: 0.6,
    smoothScroll: true,
  }).drive();
}
