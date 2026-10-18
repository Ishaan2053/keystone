import ReactMarkdown, { type Components } from 'react-markdown';
import remarkGfm from 'remark-gfm';

const blockComponents: Components = {
  a: ({ href, children }) => <a href={href} target="_blank" rel="noopener noreferrer">{children}</a>,
};

/** Inline markdown keeps claim text on one line: paragraphs unwrap, blocks are dropped. */
const inlineComponents: Components = {
  ...blockComponents,
  p: ({ children }) => <>{children}</>,
};

/**
 * GitHub renders `<sub>` in comments; raw HTML is never rendered here, so the
 * comment's `<sub>` footer (its evidence list) is split out and set small.
 */
function splitSub(text: string): { body: string; subs: string[] } {
  const subs: string[] = [];
  const body = text.replace(/<sub>([\s\S]*?)<\/sub>/g, (_, inner: string) => { subs.push(inner.trim()); return ''; }).trim();
  return { body, subs };
}

/** GitHub-flavoured markdown, styled like a GitHub comment body. */
export function Markdown({ text, inline = false, className = '' }: { text: string; inline?: boolean; className?: string }) {
  if (inline) {
    return (
      <span className={`markdown markdown-inline ${className}`}>
        <ReactMarkdown remarkPlugins={[remarkGfm]} components={inlineComponents} allowedElements={['p', 'code', 'strong', 'em', 'a', 'del']} unwrapDisallowed>
          {text}
        </ReactMarkdown>
      </span>
    );
  }
  const { body, subs } = splitSub(text);
  return (
    <div className={`markdown ${className}`}>
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={blockComponents}>{body}</ReactMarkdown>
      {subs.map((sub, i) => <div key={i} className="markdown-sub"><ReactMarkdown remarkPlugins={[remarkGfm]} components={blockComponents}>{sub}</ReactMarkdown></div>)}
    </div>
  );
}
