import { expect, it } from "vitest";
import { isSameOrigin } from "@/lib/request-origin";

it("accepts the public Docker port rather than comparing to the internal URL", () => {
  expect(isSameOrigin(new Request("http://localhost:3000/api/escritorios", {
    headers: { host: "localhost:3001", origin: "http://localhost:3001" },
  }))).toBe(true);
});

it.each(["https://evil.example", "null", "", "http://localhost:3002"])(
  "rejects unrelated or missing origin %s", (origin) => {
    expect(isSameOrigin(new Request("http://localhost:3000/api/escritorios", {
      headers: { host: "localhost:3001", origin },
    }))).toBe(false);
  },
);
