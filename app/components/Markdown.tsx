"use client";

import ReactMarkdown from "react-markdown";

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
        {text}
      </ReactMarkdown>
    </div>
  );
}
