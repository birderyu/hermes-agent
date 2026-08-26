import test from "node:test";
import assert from "node:assert/strict";

import { normalizeVoiceAttachmentName } from "./send-format.mjs";

test("voice upload names match Spectrum's M4A output", () => {
  assert.equal(normalizeVoiceAttachmentName("/tmp/reply.mp3"), "reply.m4a");
  assert.equal(
    normalizeVoiceAttachmentName("/tmp/reply.mp3", "spoken-answer.mp3"),
    "spoken-answer.m4a",
  );
  assert.equal(normalizeVoiceAttachmentName("C:\\tmp\\reply.wav"), "reply.m4a");
  assert.equal(normalizeVoiceAttachmentName("", ""), "voice.m4a");
});
