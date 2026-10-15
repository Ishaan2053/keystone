"""Keep Markdown written to the sandbox from pinging people or linking upstream (B6).

Replay copies an upstream issue body into the sandbox and apply posts a triage
comment there. GitHub notifies anyone @-mentioned in either, and adds a
"mentioned this" backlink to every issue or pull request referenced, so both
pass their text through :func:`neutralize`. Outside fenced code blocks and
inline code spans it rewrites into code spans, which GitHub never links:

- ``@login`` and ``@org/team`` (dropping the ``@``) when the ``@`` follows the
  start of the text or a non-word character, so ``user@example.com`` is kept;
- the same written with an entity at-sign: ``&#64;``, ``&#x40;``, ``&commat;``;
- ``owner/repo#123`` cross-references;
- GitHub issue, pull request and discussion URLs; a Markdown link to one becomes
  ``text (`url`)``.

Everything else is left as it is. When code delimiters are ambiguous (an
escaped or unmatched backtick run, or a code span a Markdown block could split)
and the text has something to rewrite, :class:`AmbiguousMarkdown` is raised
instead of guessing where code ends.
"""
from __future__ import annotations

import re
from collections.abc import Iterable
from itertools import accumulate

_BACKTICK_RUN = re.compile(r"`+")
_GITHUB_URL = (
    r"https?://(?:www\.)?github\.com/[A-Za-z0-9-]+/[A-Za-z0-9._-]+"
    r"/(?:issues|pull|pulls|discussions)/[0-9]+"
    r"(?:[^\s()<>\[\]`\"']*[^\s()<>\[\]`\"'.,;:!?])?"
)
_REWRITE = re.compile(
    # A Markdown link to a GitHub issue, pull request or discussion.
    rf"\[(?P<text>[^\[\]\r\n]*)\]\([ \t]*<?(?P<link>{_GITHUB_URL})>?"
    r"(?:[ \t]+(?:\"[^\"\r\n]*\"|'[^'\r\n]*'))?[ \t]*\)"
    # The same URL bare (or autolinked).
    rf"|(?P<url>{_GITHUB_URL})"
    # A user or team mention; an `@` after a word character is an email address.
    r"|(?<![A-Za-z0-9_])(?:@|&#0*64;|&#[xX]0*40;|&commat;)"
    r"(?P<login>[A-Za-z0-9][A-Za-z0-9-]*(?:/[A-Za-z0-9._-]+)?)"
    # An owner/repo#123 cross-reference.
    r"|(?<![A-Za-z0-9_.-])(?P<ref>[A-Za-z0-9-]+/[A-Za-z0-9._-]+#[0-9]+)"
)
# Inside a multiline code span, only an unindented, alphanumeric continuation is
# unambiguous. Anything else may start or end a Markdown block (blank lines,
# quotes, lists, headings, fences, HTML, setext and thematic breaks) and split it.
_BLOCK_BREAK = re.compile(r"\n(?:$|[\W_]|[0-9]{1,9}[.)](?:[ \t]|$))")
_FENCE_OPEN = re.compile(r" ?(`{3,}|~{3,})")
# A raw HTML block of these kinds runs past blank lines until its end marker; a
# fence-looking line inside one is HTML, not code.
_OPEN_RAW_HTML = re.compile(
    r"<!--(?!.*?-->)|<(pre|script|style|textarea)\b(?!.*?</\1\s*>)|<\?(?!.*?\?>)",
    re.IGNORECASE | re.DOTALL,
)
_LINE = re.compile(r"[^\r\n]*(?:\r\n|\r|\n)|[^\r\n]+\Z")
_PLACEHOLDER = re.compile(r"\x00([0-9]+)\x00")


class AmbiguousMarkdown(ValueError):
    """Code delimiters are ambiguous, so the text cannot be rewritten safely."""


def code_span(text: str) -> str:
    """``text`` as one inline code span, its delimiter longer than any backtick run inside."""
    longest = max((len(run) for run in _BACKTICK_RUN.findall(text)), default=0)
    tick = "`" * (longest + 1)
    pad = " " if text.startswith("`") or text.endswith("`") else ""
    return f"{tick}{pad}{text}{pad}{tick}"


def neutralize(markdown: str, logins: Iterable[str] = ()) -> str:
    """Rewrite mentions and references outside code into code spans (module docstring).

    An existing code span holding exactly ``@login`` for one of ``logins`` is
    normalized to `` `login` ``. Raises :class:`AmbiguousMarkdown` when code
    delimiters are ambiguous and there is something to rewrite.
    """
    known = frozenset(
        login.strip().lstrip("@").lower() for login in logins if isinstance(login, str) and login.strip()
    )
    text = markdown.replace("\x00", "�")  # GitHub renders NUL as U+FFFD; frees it for placeholders
    masked, spans = _mask(text, known)
    result = _PLACEHOLDER.sub(lambda m: spans[int(m.group(1))], _rewrite(masked))
    # The rewrite must not move where code starts or ends: check the text itself.
    checked, _ = _mask(result, frozenset())
    if _REWRITE.search(checked):
        raise AmbiguousMarkdown("rewriting changed where code starts or ends")
    return result


def _rewrite(masked: str) -> str:
    def replace(match: re.Match[str]) -> str:
        if match.group("link") is not None:
            new = f"{_rewrite(match.group('text'))} ({code_span(match.group('link'))})"
        else:
            new = code_span(match.group("url") or match.group("login") or match.group("ref"))
        # A space keeps the new span from merging with a code span right next to it.
        before = " " if match.start() > 0 and masked[match.start() - 1] == "\x00" else ""
        after = " " if masked[match.end():match.end() + 1] == "\x00" else ""
        return f"{before}{new}{after}"

    return _REWRITE.sub(replace, masked)


def _mask(text: str, known: frozenset[str]) -> tuple[str, list[str]]:
    """Replace each fenced block and inline code span with a ``\\x00<n>\\x00`` placeholder."""
    spans: list[str] = []
    out: list[str] = []
    last = 0
    for start, end in [*_fences(text), (len(text), len(text))]:
        out.append(_mask_inline(text[last:start], known, spans))
        if start < end:
            out.append(_hold(text[start:end], spans))
        last = end
    return "".join(out), spans


def _hold(span: str, spans: list[str]) -> str:
    spans.append(span)
    return f"\x00{len(spans) - 1}\x00"


def _fences(text: str) -> list[tuple[int, int]]:
    """Offsets of the fenced code blocks GitHub will certainly render as code.

    A fence counts only at the top level: indented at most one space (so never
    inside a list item), after a blank line or the start of the text (so never
    inside an HTML block), with no raw HTML block open before it, and closed by
    its own closing fence. Any other fence is left to the inline scan, which
    reads it as a code span or as ambiguous.
    """
    lines = _LINE.findall(text)
    starts = list(accumulate((len(line) for line in lines), initial=0))
    blocks: list[tuple[int, int]] = []
    index, after_break = 0, True
    while index < len(lines):
        line = lines[index].rstrip("\r\n")
        opening = _FENCE_OPEN.match(line)
        closing = None
        if (
            opening
            and after_break
            and not (opening.group(1)[0] == "`" and "`" in line[opening.end():])
            and not _OPEN_RAW_HTML.search(text, 0, starts[index])
        ):
            fence = opening.group(1)
            close = re.compile(rf" {{0,3}}{re.escape(fence[0])}{{{len(fence)},}}[ \t]*")
            closing = next(
                (j for j in range(index + 1, len(lines)) if close.fullmatch(lines[j].rstrip("\r\n"))),
                None,
            )
        if closing is not None:
            blocks.append((starts[index], starts[closing + 1]))
            index, after_break = closing + 1, True
            continue
        after_break = not line.strip()
        index += 1
    return blocks


def _mask_inline(text: str, known: frozenset[str], spans: list[str]) -> str:
    """Mask matched backtick code spans; never escaped or unmatched runs."""
    risky = _REWRITE.search(text) is not None
    runs = list(_BACKTICK_RUN.finditer(text))
    out: list[str] = []
    index = last = 0
    while index < len(runs):
        opening = runs[index]
        start = opening.start()
        slashes = start
        while slashes > 0 and text[slashes - 1] == "\\":
            slashes -= 1
        escaped = (start - slashes) % 2 == 1
        closing_index = index + 1
        if not escaped:
            # Backslashes are literal inside code, so they never escape a closing run.
            while closing_index < len(runs) and len(runs[closing_index].group()) != len(opening.group()):
                closing_index += 1
        if escaped or closing_index == len(runs):
            # A literal or unmatched run can pair differently once text around it changes.
            if risky:
                raise AmbiguousMarkdown("the text has escaped or unmatched backtick runs")
            index += 1
            continue
        closing = runs[closing_index]
        inner = text[opening.end():closing.start()]
        if risky and _BLOCK_BREAK.search(inner.replace("\r\n", "\n").replace("\r", "\n")):
            # Even a span with nothing to rewrite can, once split by a block, steal
            # a delimiter from a later span and expose what that span hides.
            raise AmbiguousMarkdown("a code span may be split by a Markdown block")
        if ("\n" in inner or "\r" in inner) and _REWRITE.search(inner):
            raise AmbiguousMarkdown("a multiline code span holds something to rewrite")
        span = text[start:closing.end()]
        stripped = inner.strip()
        if stripped.startswith("@") and stripped[1:].lower() in known:
            span = code_span(stripped[1:])
        out.extend((text[last:start], _hold(span, spans)))
        last = closing.end()
        index = closing_index + 1
    out.append(text[last:])
    return "".join(out)
