#!/usr/bin/env node
// Patch spectrum-ts' iMessage poll cache until upstream accepts poll.changed
// events whose poll title is empty. Apple can omit the title even though the
// original poll rendered with one; @spectrum-ts/imessage then passes the empty
// value to asPoll(), which rejects it before Hermes receives the selected
// option. The fallback below is internal cache metadata only: it does not
// change the poll bubble, question, or choice text shown to the user.
//
// The SDK is pinned to spectrum-ts 12.8.0. Anchors match that published output
// exactly and fail loudly if a future version reshapes the cache path.
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const MARKER = "Hermes patch: Accept empty iMessage poll titles";
const INTERNAL_FALLBACK_TITLE = "Poll";

function scriptDir() {
  return path.dirname(fileURLToPath(import.meta.url));
}

function replaceOnce(source, from, to, label) {
  const count = source.split(from).length - 1;
  if (count !== 1) {
    throw new Error(`expected exactly one ${label} match, found ${count}`);
  }
  return source.replace(from, to);
}

export function patchPollEmptyTitle(root = scriptDir()) {
  const dist = path.join(
    root,
    "node_modules",
    "@spectrum-ts",
    "imessage",
    "dist"
  );
  if (!fs.existsSync(dist)) {
    throw new Error(`@spectrum-ts/imessage dist not found: ${dist}`);
  }

  const files = fs.readdirSync(dist)
    .filter((name) => name.endsWith(".js"))
    .map((name) => path.join(dist, name));

  for (const file of files) {
    const raw = fs.readFileSync(file, "utf8");
    if (raw.includes(MARKER)) {
      return { patched: false, file, reason: "already patched" };
    }

    const CR = String.fromCharCode(13);
    const CRLF = CR + "\n";
    const usedCRLF = raw.includes(CRLF);
    const original = usedCRLF ? raw.split(CRLF).join("\n") : raw;
    if (!original.includes("const toCachedPoll =")) {
      continue;
    }

    let patched = replaceOnce(
      original,
      `const toCachedPoll = (input) => {\n\tconst poll = asPoll({\n\t\ttitle: input.title,`,
      `const toCachedPoll = (input) => {\n\tconst poll = asPoll({\n\t\ttitle: input.title || ${JSON.stringify(INTERNAL_FALLBACK_TITLE)}, // ${MARKER}`,
      "poll cache title"
    );
    if (usedCRLF) {
      patched = patched.split("\n").join(CRLF);
    }
    fs.writeFileSync(file, patched, "utf8");
    return { patched: true, file };
  }

  throw new Error(
    "could not find @spectrum-ts/imessage poll cache chunk to patch"
  );
}

const _invokedDirectly =
  process.argv[1] &&
  import.meta.url === pathToFileURL(process.argv[1]).href;
if (_invokedDirectly) {
  try {
    const root = process.argv[2] ? path.resolve(process.argv[2]) : scriptDir();
    const result = patchPollEmptyTitle(root);
    const action = result.patched ? "patched" : "ok";
    console.error(
      `photon-sidecar: spectrum empty poll title patch ${action}: ${result.file}`
    );
  } catch (err) {
    console.error(
      `photon-sidecar: spectrum empty poll title patch failed: ${err?.stack || err}`
    );
    process.exit(1);
  }
}
