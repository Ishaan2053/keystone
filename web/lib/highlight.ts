import type { HighlighterCore, ThemedToken } from 'shiki/core';

export type CodeLang = 'json' | 'cypher';

/**
 * One shared shiki highlighter, loaded on first use: only the JSON and Cypher
 * grammars and GitHub's two themes, on the JavaScript regex engine (no WASM).
 * Tokens carry both themes as CSS variables, so the theme toggle needs no
 * re-highlight.
 */
let loading: Promise<HighlighterCore> | null = null;

function highlighter(): Promise<HighlighterCore> {
  loading ??= (async () => {
    const [{ createHighlighterCore }, { createJavaScriptRegexEngine }] = await Promise.all([
      import('shiki/core'),
      import('shiki/engine/javascript'),
    ]);
    return createHighlighterCore({
      themes: [import('shiki/themes/github-dark-default.mjs'), import('shiki/themes/github-light-default.mjs')],
      langs: [import('shiki/langs/json.mjs'), import('shiki/langs/cypher.mjs')],
      engine: createJavaScriptRegexEngine(),
    });
  })();
  return loading;
}

export async function highlight(code: string, lang: CodeLang): Promise<ThemedToken[][]> {
  const shiki = await highlighter();
  return shiki.codeToTokens(code, {
    lang,
    themes: { dark: 'github-dark-default', light: 'github-light-default' },
    defaultColor: false,
  }).tokens;
}
