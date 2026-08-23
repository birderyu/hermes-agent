// Privacy-bounded normalization for iMessage location cards.
//
// spectrum-ts 8 surfaces no-text/no-attachment iMessage app balloons as
// `custom(rawMessage)`. The raw Apple message contains a balloon bundle id,
// but forwarding the entire value would expose unrelated message metadata.
// This module therefore allowlists only location-shaped balloons and, when
// available, asks the already-authenticated Advanced iMessage client for the
// sender's current shared-location snapshot.

const LOCATION_BUNDLE_HINTS = ["findmy", "maps", "location"];
const DEFAULT_TIMEOUT_MS = 5000;
const MAX_LOCATION_AGE_MS = 15 * 60 * 1000;

function firstString(...values) {
  for (const value of values) {
    if (typeof value === "string" && value.trim()) return value.trim();
  }
  return null;
}

export function locationBalloonBundleId(content) {
  if (!content || content.type !== "custom") return null;
  const raw = content.raw;
  if (!raw || typeof raw !== "object") return null;
  const rawContent =
    raw.content && typeof raw.content === "object" ? raw.content : {};
  const metadata =
    raw.metadata && typeof raw.metadata === "object" ? raw.metadata : {};
  return firstString(
    rawContent.balloonBundleId,
    rawContent.balloon_bundle_id,
    metadata.balloonBundleId,
    metadata.balloon_bundle_id,
    raw.balloonBundleId,
    raw.balloon_bundle_id
  );
}

export function isIMessageLocationCustom(content) {
  const bundleId = locationBalloonBundleId(content);
  if (!bundleId) return false;
  const lowered = bundleId.toLowerCase();
  return LOCATION_BUNDLE_HINTS.some((hint) => lowered.includes(hint));
}

function runtimeForIMessage(app) {
  const platforms = app?.__internal?.platforms;
  if (!(platforms instanceof Map)) return null;
  const direct = platforms.get("iMessage");
  if (direct) return direct;
  for (const runtime of platforms.values()) {
    if (runtime?.definition?.name === "iMessage") return runtime;
  }
  return null;
}

export function selectIMessageLocationClient(app, phone) {
  const clients = runtimeForIMessage(app)?.client;
  if (!Array.isArray(clients) || clients.length === 0) return null;
  const entry =
    clients.find((candidate) => candidate?.phone === phone) ??
    (clients.length === 1 ? clients[0] : null);
  const client = entry?.client;
  return typeof client?.locations?.get === "function" ? client : null;
}

function finiteCoordinate(value, min, max) {
  return typeof value === "number" &&
    Number.isFinite(value) &&
    value >= min &&
    value <= max
    ? value
    : null;
}

function toTimestamp(value) {
  if (value instanceof Date) {
    return Number.isFinite(value.getTime()) ? value : null;
  }
  if (typeof value !== "string" && typeof value !== "number") return null;
  const parsed = new Date(value);
  return Number.isFinite(parsed.getTime()) ? parsed : null;
}

function locationIsCurrent(snapshot, messageTimestamp, now) {
  const expiresAt = toTimestamp(snapshot?.expiresAt);
  if (expiresAt && expiresAt.getTime() < now.getTime()) return false;

  const locatedAt = toTimestamp(snapshot?.locationTimestamp);
  const messageAt = toTimestamp(messageTimestamp);
  if (locatedAt && messageAt) {
    return (
      Math.abs(locatedAt.getTime() - messageAt.getTime()) <=
      MAX_LOCATION_AGE_MS
    );
  }
  return true;
}

export function sanitizeSharedLocation(
  snapshot,
  messageTimestamp,
  now = new Date()
) {
  if (!snapshot || typeof snapshot !== "object") return null;
  if (snapshot.isLocatingInProgress === true) return null;
  if (!locationIsCurrent(snapshot, messageTimestamp, now)) return null;

  const latitude = finiteCoordinate(snapshot.latitude, -90, 90);
  const longitude = finiteCoordinate(snapshot.longitude, -180, 180);
  const address = firstString(
    snapshot.longAddress,
    snapshot.address,
    snapshot.shortAddress
  );
  const name = firstString(snapshot.name);
  if (latitude === null && longitude === null && !address && !name) return null;
  // A single coordinate is not useful and is more likely a malformed payload.
  if ((latitude === null) !== (longitude === null)) return null;

  const locatedAt = toTimestamp(snapshot.locationTimestamp);
  return {
    resolved: true,
    ...(name ? { name } : {}),
    ...(address ? { address } : {}),
    ...(latitude !== null ? { latitude, longitude } : {}),
    ...(locatedAt ? { locationTimestamp: locatedAt.toISOString() } : {}),
    locationType: firstString(snapshot.locationType) || "unknown",
  };
}

function withTimeout(promise, timeoutMs) {
  let timer;
  const timeout = new Promise((_, reject) => {
    timer = setTimeout(
      () => reject(new Error("location lookup timed out")),
      timeoutMs
    );
    timer.unref?.();
  });
  return Promise.race([promise, timeout]).finally(() => clearTimeout(timer));
}

export async function normalizeIMessageLocation(content, context = {}) {
  if (!isIMessageLocationCustom(content)) return null;

  // Deliberately omit the bundle id and raw Apple message from the normalized
  // event. Hermes only needs to know that this is a location card.
  const unresolved = { type: "location", resolved: false };
  const senderId = firstString(context.senderId);
  const client = selectIMessageLocationClient(context.app, context.phone);
  if (!senderId || !client) return unresolved;

  try {
    const timeoutMs =
      Number.isFinite(context.timeoutMs) && context.timeoutMs > 0
        ? context.timeoutMs
        : DEFAULT_TIMEOUT_MS;
    const snapshot = await withTimeout(
      client.locations.get(senderId),
      timeoutMs
    );
    const normalized = sanitizeSharedLocation(
      snapshot,
      context.messageTimestamp,
      context.now instanceof Date ? context.now : new Date()
    );
    return normalized
      ? { type: "location", source: "shared-location", ...normalized }
      : unresolved;
  } catch {
    // Not sharing, transient lookup failure, and unsupported private API all
    // have the same safe fallback: recognize the card without fabricating a
    // place or leaking an SDK error that may contain account metadata.
    return unresolved;
  }
}
