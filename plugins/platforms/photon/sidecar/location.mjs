// Privacy-bounded normalization for decoded iMessage mini-app location cards.
//
// Advanced iMessage 2.1 exposes the URL and visible layout text embedded in an
// inbound app-extension balloon. Hermes forwards those exact card fields only;
// opaque session/team identifiers and the full Apple message are discarded.

const LOCATION_BUNDLE_HINTS = ["findmy", "maps", "location"];
const CARD_TEXT_FIELDS = [
  "caption",
  "subcaption",
  "trailingCaption",
  "trailingSubcaption",
  "imageTitle",
  "imageSubtitle",
  "summary",
];
const MAX_URL_LENGTH = 4096;
const MAX_TEXT_LENGTH = 1000;

function firstString(...values) {
  for (const value of values) {
    if (typeof value === "string" && value.trim()) return value.trim();
  }
  return null;
}

function boundedString(value, maxLength) {
  const text = firstString(value);
  return text && text.length <= maxLength ? text : null;
}

function customRaw(content) {
  return content?.type === "custom" &&
    content.raw &&
    typeof content.raw === "object"
    ? content.raw
    : null;
}

function miniAppFromContent(content) {
  const raw = customRaw(content);
  if (!raw) return null;
  const miniApp = raw.miniApp ?? raw.content?.miniApp;
  return miniApp && typeof miniApp === "object" ? miniApp : null;
}

export function locationBalloonBundleId(content) {
  const raw = customRaw(content);
  if (!raw) return null;
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
  const miniApp = miniAppFromContent(content);
  const identifiers = [
    locationBalloonBundleId(content),
    miniApp?.extensionBundleId,
    miniApp?.appName,
  ];
  return identifiers.some((identifier) => {
    const lowered = firstString(identifier)?.toLowerCase();
    return (
      lowered && LOCATION_BUNDLE_HINTS.some((hint) => lowered.includes(hint))
    );
  });
}

function finiteCoordinate(value, min, max) {
  const number = typeof value === "number" ? value : Number(value);
  return Number.isFinite(number) && number >= min && number <= max
    ? number
    : null;
}

function coordinatePair(value) {
  const text = firstString(value);
  if (!text) return null;
  const match = text.match(
    /^\s*(-?\d+(?:\.\d+)?)\s*[, ]\s*(-?\d+(?:\.\d+)?)\s*$/
  );
  if (!match) return null;
  const latitude = finiteCoordinate(match[1], -90, 90);
  const longitude = finiteCoordinate(match[2], -180, 180);
  return latitude === null || longitude === null
    ? null
    : { latitude, longitude };
}

function parseMapUrl(value) {
  const url = boundedString(value, MAX_URL_LENGTH);
  if (!url) return { url: null, name: null, address: null, coordinates: null };
  try {
    const parsed = new URL(url);
    const scheme = parsed.protocol.toLowerCase();
    if (scheme !== "http:" && scheme !== "https:" && scheme !== "maps:") {
      return { url: null, name: null, address: null, coordinates: null };
    }
    const coordinates =
      coordinatePair(parsed.searchParams.get("ll")) ??
      coordinatePair(parsed.searchParams.get("coordinate")) ??
      coordinatePair(parsed.searchParams.get("center")) ??
      coordinatePair(parsed.searchParams.get("sll"));
    const query = boundedString(parsed.searchParams.get("q"), MAX_TEXT_LENGTH);
    return {
      url,
      name: query && !coordinatePair(query) ? query : null,
      address: boundedString(
        parsed.searchParams.get("address"),
        MAX_TEXT_LENGTH
      ),
      coordinates,
    };
  } catch {
    return { url: null, name: null, address: null, coordinates: null };
  }
}

function visibleLayoutText(layout) {
  if (!layout || typeof layout !== "object") return [];
  const values = [];
  for (const field of CARD_TEXT_FIELDS) {
    const value = boundedString(layout[field], MAX_TEXT_LENGTH);
    if (value && !values.includes(value)) values.push(value);
  }
  return values;
}

export function sanitizeMiniAppLocation(content) {
  if (!isIMessageLocationCustom(content)) return null;
  const miniApp = miniAppFromContent(content);
  if (!miniApp) return { type: "location", resolved: false };

  const mapUrl = parseMapUrl(miniApp.url);
  const layout =
    miniApp.layout && typeof miniApp.layout === "object"
      ? miniApp.layout
      : {};
  const cardText = visibleLayoutText(layout);
  const name = firstString(
    mapUrl.name,
    boundedString(layout.caption, MAX_TEXT_LENGTH),
    boundedString(layout.imageTitle, MAX_TEXT_LENGTH)
  );
  const address = firstString(
    mapUrl.address,
    boundedString(layout.subcaption, MAX_TEXT_LENGTH),
    boundedString(layout.imageSubtitle, MAX_TEXT_LENGTH)
  );
  const hasExactCardData = Boolean(
    mapUrl.url || name || address || mapUrl.coordinates || cardText.length
  );
  if (!hasExactCardData) return { type: "location", resolved: false };

  return {
    type: "location",
    source: "map-card",
    resolved: true,
    ...(name ? { name } : {}),
    ...(address && address !== name ? { address } : {}),
    ...(mapUrl.coordinates ?? {}),
    ...(mapUrl.url ? { url: mapUrl.url } : {}),
    ...(cardText.length ? { cardText } : {}),
  };
}

export async function normalizeIMessageLocation(content) {
  return sanitizeMiniAppLocation(content);
}
