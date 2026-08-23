#!/usr/bin/env node
// spectrum-ts 12 receives Advanced iMessage's decoded inbound mini-app data,
// but currently turns every no-text/no-attachment balloon into the same
// `unsupported-message` custom value. Apple Maps and Find My cards therefore
// lose their URL and visible layout text before Hermes can inspect them.
//
// Preserve only the public, user-visible mini-app fields. Session and team
// identifiers are intentionally not forwarded.
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const MARKER = "Hermes patch: Preserve decoded inbound mini-app content";

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

function replaceExactly(source, from, to, expected, label) {
  const count = source.split(from).length - 1;
  if (count !== expected) {
    throw new Error(
      `expected exactly ${expected} ${label} matches, found ${count}`
    );
  }
  return source.split(from).join(to);
}

export function patchMiniAppInbound(root = scriptDir()) {
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
    const unsupported =
      'const unsupportedMessageContent = () => asCustom({ imessage_type: "unsupported-message" });';
    if (!original.includes(unsupported)) continue;

    let patched = replaceOnce(
      original,
      unsupported,
      [
        `// ${MARKER}`,
        "const unsupportedMessageContent = (message) => {",
        "\tconst miniApp = message?.content?.miniApp;",
        "\tif (!miniApp) return asCustom({ imessage_type: \"unsupported-message\" });",
        "\tconst layout = miniApp.layout;",
        "\treturn asCustom({",
        "\t\timessage_type: \"mini-app\",",
        "\t\tballoonBundleId: message.content.balloonBundleId,",
        "\t\tminiApp: {",
        "\t\t\textensionBundleId: miniApp.extensionBundleId,",
        "\t\t\tappName: miniApp.appName,",
        "\t\t\turl: miniApp.url,",
        "\t\t\tlive: miniApp.live,",
        "\t\t\t...(layout ? {",
        "\t\t\t\tlayout: {",
        "\t\t\t\t\tcaption: layout.caption,",
        "\t\t\t\t\tsubcaption: layout.subcaption,",
        "\t\t\t\t\ttrailingCaption: layout.trailingCaption,",
        "\t\t\t\t\ttrailingSubcaption: layout.trailingSubcaption,",
        "\t\t\t\t\timageTitle: layout.imageTitle,",
        "\t\t\t\t\timageSubtitle: layout.imageSubtitle,",
        "\t\t\t\t\tsummary: layout.summary",
        "\t\t\t\t}",
        "\t\t\t} : {})",
        "\t\t}",
        "\t});",
        "};",
      ].join("\n"),
      "unsupported mini-app mapper"
    );
    patched = replaceExactly(
      patched,
      "unsupportedMessageContent()",
      "unsupportedMessageContent(message)",
      2,
      "unsupported-message call"
    );
    if (usedCRLF) patched = patched.split("\n").join(CRLF);
    fs.writeFileSync(file, patched, "utf8");
    return { patched: true, file };
  }

  throw new Error("could not find @spectrum-ts/imessage inbound mini-app mapper");
}

const invokedDirectly =
  process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href;
if (invokedDirectly) {
  try {
    const root = process.argv[2] ? path.resolve(process.argv[2]) : scriptDir();
    const result = patchMiniAppInbound(root);
    const action = result.patched ? "patched" : "ok";
    console.error(
      `photon-sidecar: spectrum mini-app inbound patch ${action}: ${result.file}`
    );
  } catch (err) {
    console.error(
      `photon-sidecar: spectrum mini-app inbound patch failed: ${err?.stack || err}`
    );
    process.exit(1);
  }
}
