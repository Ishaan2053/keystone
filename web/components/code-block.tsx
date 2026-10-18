'use client';
import { useEffect, useState, type CSSProperties } from 'react';
import type { ThemedToken } from 'shiki/core';
import { highlight, type CodeLang } from '@/lib/highlight';

/**
 * Syntax-highlighted code. The plain text renders first and stays the exact
 * text content, so copying or reading the block never depends on the
 * highlighter having loaded.
 */
export function CodeBlock({
  code,
  lang,
  className = '',
}: {
  code: string;
  lang: CodeLang;
  className?: string;
}) {
  const [tokens, setTokens] = useState<ThemedToken[][] | null>(null);
  useEffect(() => {
    let active = true;
    setTokens(null);
    highlight(code, lang)
      .then((result) => {
        if (active) setTokens(result);
      })
      .catch(() => {
        /* Plain text stays. */
      });
    return () => {
      active = false;
    };
  }, [code, lang]);
  return (
    <pre className={`code-block type-code ${className}`} data-lang={lang}>
      <code>
        {tokens
          ? tokens.map((line, i) => (
              <span className="code-line" key={i}>
                {line.map((token, j) => (
                  <span key={j} className="code-token" style={token.htmlStyle as CSSProperties}>
                    {token.content}
                  </span>
                ))}
                {i < tokens.length - 1 ? '\n' : null}
              </span>
            ))
          : code}
      </code>
    </pre>
  );
}

export function JsonBlock({ value, className = '' }: { value: unknown; className?: string }) {
  return (
    <CodeBlock code={JSON.stringify(value, null, 2) ?? 'null'} lang="json" className={className} />
  );
}
