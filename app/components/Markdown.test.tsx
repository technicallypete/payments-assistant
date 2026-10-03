import { describe, expect, it } from "vitest";

import { normalizeModelMarkdown } from "./Markdown";

describe("normalizeModelMarkdown", () => {
  it("strips indentation that would turn prose into a code block", () => {
    const raw = "    Proposed a **$120.00 partial refund** to Acme.\n\n    Check the card.";
    expect(normalizeModelMarkdown(raw)).toBe(
      "Proposed a **$120.00 partial refund** to Acme.\n\nCheck the card.",
    );
  });

  it("leaves list nesting alone", () => {
    const md = "Owed:\n- Acme\n  - $1,200\n  - $3,500";
    expect(normalizeModelMarkdown(md)).toBe(md);
  });
});
