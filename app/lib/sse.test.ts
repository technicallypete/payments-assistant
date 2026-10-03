import { describe, expect, it } from "vitest";

import { readSse } from "./sse";

function streamOf(chunks: string[]): ReadableStream<Uint8Array> {
  const enc = new TextEncoder();
  return new ReadableStream({
    start(c) {
      chunks.forEach((s) => c.enqueue(enc.encode(s)));
      c.close();
    },
  });
}

async function collect(chunks: string[]) {
  const out = [];
  for await (const m of readSse(streamOf(chunks))) out.push(m);
  return out;
}

describe("readSse", () => {
  it("parses events split across arbitrary chunk boundaries", async () => {
    const whole = 'event: token\ndata: {"delta":"Hi"}\n\nevent: message_end\ndata: {"text":"Hi"}\n\n';
    for (let cut = 1; cut < whole.length; cut += 7) {
      const msgs = await collect([whole.slice(0, cut), whole.slice(cut)]);
      expect(msgs).toEqual([
        { event: "token", data: '{"delta":"Hi"}' },
        { event: "message_end", data: '{"text":"Hi"}' },
      ]);
    }
  });

  it("handles CRLF, comments, multi-line data and a missing trailing blank line", async () => {
    const msgs = await collect([": keepalive\r\n\r\nevent: x\r\ndata: a\r\ndata: b\r\n\r\ndata: tail"]);
    expect(msgs).toEqual([
      { event: "x", data: "a\nb" },
      { event: "message", data: "tail" },
    ]);
  });

  it("decodes multi-byte characters split across chunks", async () => {
    const bytes = new TextEncoder().encode("data: café €\n\n");
    const stream = new ReadableStream<Uint8Array>({
      start(c) {
        c.enqueue(bytes.slice(0, 10));
        c.enqueue(bytes.slice(10));
        c.close();
      },
    });
    const out = [];
    for await (const m of readSse(stream)) out.push(m);
    expect(out).toEqual([{ event: "message", data: "café €" }]);
  });
});
