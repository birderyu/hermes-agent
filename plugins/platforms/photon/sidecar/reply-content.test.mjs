import assert from "node:assert/strict";
import test from "node:test";

import {
  messageTextPreview,
  normalizeReplyContent,
} from "./reply-content.mjs";

test("normalizeReplyContent preserves inner content and target context", async () => {
  const inner = { type: "text", text: "follow-up" };
  const result = await normalizeReplyContent(
    {
      type: "reply",
      content: inner,
      target: {
        id: "bot-msg-1",
        direction: "outbound",
        content: { type: "text", text: "earlier answer" },
      },
    },
    async (value) => ({ ...value, normalized: true })
  );

  assert.deepEqual(result, {
    type: "reply",
    content: { type: "text", text: "follow-up", normalized: true },
    targetMessageId: "bot-msg-1",
    targetDirection: "outbound",
    targetText: "earlier answer",
  });
});

test("normalizeReplyContent keeps user content when target is unavailable", async () => {
  const result = await normalizeReplyContent(
    { type: "reply", content: { type: "text", text: "still readable" } },
    async (value) => value
  );

  assert.deepEqual(result, {
    type: "reply",
    content: { type: "text", text: "still readable" },
    targetMessageId: null,
    targetDirection: null,
    targetText: null,
  });
});

test("normalizeReplyContent preserves malformed inner content as null", async () => {
  let called = false;
  const result = await normalizeReplyContent(
    { type: "reply", content: null },
    async (value) => {
      called = true;
      return value;
    }
  );

  assert.equal(called, false);
  assert.equal(result.content, null);
});

test("messageTextPreview resolves grouped and nested reply targets", () => {
  assert.equal(
    messageTextPreview({
      content: {
        type: "group",
        items: [
          { content: { type: "attachment", name: "image.png" } },
          { content: { type: "richlink", url: "https://example.com" } },
        ],
      },
    }),
    "https://example.com"
  );
  assert.equal(
    messageTextPreview({
      content: {
        type: "reply",
        content: { type: "text", text: "nested target text" },
      },
    }),
    "nested target text"
  );
});

test("messageTextPreview caps large target text", () => {
  assert.equal(
    messageTextPreview({ content: { type: "text", text: "x".repeat(2500) } })
      .length,
    2000
  );
});
