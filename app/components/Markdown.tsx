"use client";

import ReactMarkdown from "react-markdown";

/**
 * Models sometimes indent a paragraph (Kimi often starts replies with spaces). Four leading spaces
 * make Markdown render a code block, so prose showed up as raw monospace with literal `**`.
 * Strip indentation at the start of the text and of each paragraph; list nesting is untouched.
 */
export function normalizeModelMarkdown(text: string): string {
  return text.replace(/^\s+/, "").replace(/\n\n[ \t]+(?=\S)/g, "\n\n");
}

/** Assistant text: Markdown without raw HTML (react-markdown ignores HTML by default). */
export function Markdown({ text }: { text: string }) {
  return (
    <div className="prose-penny">
      <ReactMarkdown
        components={{
          a: ({ href, children }) => (
            <a href={href} target="_blank" rel="noreferrer noopener" className="underline text-accent">
              {children}
            </a>
          ),
        }}
      >
        {normalizeModelMarkdown(text)}
      </ReactMarkdown>
    </div>
  );
}
