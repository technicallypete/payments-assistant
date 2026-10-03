import { describe, expect, it } from "vitest";

import { greetingFor } from "./format";

describe("greetingFor", () => {
  it.each([
    [3, "Burning the midnight oil."],
    [9, "Morning."],
    [12, "Afternoon."],
    [16, "Afternoon."],
    [17, "Evening."],
    [23, "Evening."],
  ])("hour %i → %s", (hour, word) => {
    expect(greetingFor(hour)).toBe(word);
  });
});
