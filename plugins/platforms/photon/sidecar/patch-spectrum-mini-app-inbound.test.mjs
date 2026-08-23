import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { pathToFileURL } from "node:url";

import { patchMiniAppInbound } from "./patch-spectrum-mini-app-inbound.mjs";

function writeFixture() {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "hermes-mini-app-patch-"));
  const dist = path.join(
    root,
    "node_modules",
    "@spectrum-ts",
    "imessage",
    "dist"
  );
  fs.mkdirSync(dist, { recursive: true });
  const chunk = path.join(dist, "index.js");
  fs.writeFileSync(
    chunk,
    [
      'const asCustom = (raw) => ({ type: "custom", raw });',
      'const unsupportedMessageContent = () => asCustom({ imessage_type: "unsupported-message" });',
      "const build = (message) => {",
      "\tif (!message.content.attachments.length) {",
      "\t\treturn { content: unsupportedMessageContent() };",
      "\t}",
      "\treturn { content: unsupportedMessageContent() };",
      "};",
      "export { build };",
      "",
    ].join("\n"),
    "utf8"
  );
  return { root, chunk };
}

test("decoded mini-app fields survive without opaque identifiers", async (t) => {
  const { root, chunk } = writeFixture();
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));

  const first = patchMiniAppInbound(root);
  assert.equal(first.patched, true);
  const { build } = await import(pathToFileURL(chunk).href + "?first");
  const message = {
    content: {
      attachments: [],
      balloonBundleId: "com.apple.Maps.MessagesExtension",
      miniApp: {
        extensionBundleId: "com.apple.Maps.MessagesExtension",
        appName: "Maps",
        url: "https://maps.apple.com/?q=Test",
        live: false,
        sessionId: "private-session",
        teamId: "private-team",
        layout: {
          caption: "Test",
          subcaption: "1 Example Road",
          hidden: "must-not-forward",
        },
      },
    },
  };
  const raw = build(message).content.raw;
  assert.equal(raw.imessage_type, "mini-app");
  assert.equal(raw.miniApp.url, "https://maps.apple.com/?q=Test");
  assert.equal(raw.miniApp.layout.subcaption, "1 Example Road");
  assert.equal("sessionId" in raw.miniApp, false);
  assert.equal("teamId" in raw.miniApp, false);
  assert.equal("hidden" in raw.miniApp.layout, false);

  const second = patchMiniAppInbound(root);
  assert.equal(second.patched, false);
  assert.equal(second.reason, "already patched");
});

test("ordinary unsupported messages keep their old representation", async (t) => {
  const { root, chunk } = writeFixture();
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  patchMiniAppInbound(root);
  const { build } = await import(pathToFileURL(chunk).href + "?ordinary");
  assert.deepEqual(
    build({ content: { attachments: [] } }).content.raw,
    { imessage_type: "unsupported-message" }
  );
});

test("balloon identity survives when the server omits mini-app details", async (t) => {
  const { root, chunk } = writeFixture();
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  patchMiniAppInbound(root);
  const { build } = await import(pathToFileURL(chunk).href + "?balloon");
  assert.deepEqual(
    build({
      content: {
        attachments: [],
        balloonBundleId: "com.apple.findmy.FindMyMessagesApp",
      },
    }).content.raw,
    {
      imessage_type: "unsupported-message",
      balloonBundleId: "com.apple.findmy.FindMyMessagesApp",
    }
  );
});

test("unknown mapper layouts fail without editing", (t) => {
  const { root, chunk } = writeFixture();
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  fs.writeFileSync(chunk, "export const untouched = true;\n", "utf8");
  assert.throws(
    () => patchMiniAppInbound(root),
    /could not find .* inbound mini-app mapper/
  );
  assert.equal(fs.readFileSync(chunk, "utf8"), "export const untouched = true;\n");
});
