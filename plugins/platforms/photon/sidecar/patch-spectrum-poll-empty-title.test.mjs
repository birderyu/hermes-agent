import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { pathToFileURL } from "node:url";

import { patchPollEmptyTitle } from "./patch-spectrum-poll-empty-title.mjs";

function writeFixture() {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "hermes-poll-patch-"));
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
      "const asPoll = (value) => {",
      "\tif (!value.title) throw new Error('poll title is required');",
      "\treturn value;",
      "};",
      "const toCachedPoll = (input) => {",
      "\tconst poll = asPoll({",
      "\t\ttitle: input.title,",
      "\t\toptions: input.options.map((optionInfo) => ({ title: optionInfo.text }))",
      "\t});",
      "\tconst optionsByIdentifier = new Map();",
      "\tfor (const [index, optionInfo] of input.options.entries()) {",
      "\t\tconst option = poll.options[index];",
      "\t\tif (option && optionInfo.optionIdentifier) optionsByIdentifier.set(optionInfo.optionIdentifier, option);",
      "\t}",
      "\treturn { poll, optionsByIdentifier };",
      "};",
      "export { toCachedPoll };",
      "",
    ].join("\n"),
    "utf8"
  );
  return { root, chunk };
}

async function importFixture(chunk, suffix) {
  return import(`${pathToFileURL(chunk).href}?case=${suffix}`);
}

test("empty poll titles use internal cache metadata and keep choices", async (t) => {
  const { root, chunk } = writeFixture();
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));

  const first = patchPollEmptyTitle(root);
  assert.equal(first.patched, true);

  const { toCachedPoll } = await importFixture(chunk, "empty");
  const cached = toCachedPoll({
    title: "",
    options: [{ text: "Confirm", optionIdentifier: "confirm" }],
  });
  assert.equal(cached.poll.title, "Poll");
  assert.equal(cached.poll.options[0].title, "Confirm");
  assert.equal(
    cached.optionsByIdentifier.get("confirm"),
    cached.poll.options[0]
  );

  const second = patchPollEmptyTitle(root);
  assert.equal(second.patched, false);
  assert.equal(second.reason, "already patched");
});

test("existing non-empty poll titles are preserved", async (t) => {
  const { root, chunk } = writeFixture();
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));

  patchPollEmptyTitle(root);
  const { toCachedPoll } = await importFixture(chunk, "non-empty");
  const cached = toCachedPoll({ title: "Create event?", options: [] });
  assert.equal(cached.poll.title, "Create event?");
});

test("unknown poll cache layouts fail without editing", (t) => {
  const { root, chunk } = writeFixture();
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));

  fs.writeFileSync(
    chunk,
    "const toCachedPoll = input => ({ poll: input });\nexport { toCachedPoll };\n",
    "utf8"
  );
  assert.throws(
    () => patchPollEmptyTitle(root),
    /expected exactly one poll cache title match, found 0/
  );
});
