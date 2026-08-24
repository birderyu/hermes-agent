// Normalize Spectrum's reply envelope without losing either the user's inner
// content or the replied-to message context. Kept separate from index.mjs so
// the behavior can be exercised directly without starting the live sidecar.

const TARGET_TEXT_CAP = 2000;

function contentText(content) {
  if (!content || typeof content !== "object") return null;
  if (content.type === "text") return content.text;
  if (content.type === "richlink") return content.url;
  if (content.type === "reply") return contentText(content.content);
  if (content.type === "group") {
    for (const item of Array.isArray(content.items) ? content.items : []) {
      const text = contentText(item?.content);
      if (typeof text === "string" && text) return text;
    }
  }
  return null;
}

/** Return a bounded text preview for a hydrated Spectrum message. */
export function messageTextPreview(message) {
  const text = contentText(message?.content);
  if (typeof text !== "string" || !text) return null;
  return text.length > TARGET_TEXT_CAP ? text.slice(0, TARGET_TEXT_CAP) : text;
}

/**
 * Convert a Spectrum reply envelope into the sidecar's privacy-bounded shape.
 * The caller owns normalizing the inner content because it may contain binary
 * attachments or provider-specific custom content.
 */
export async function normalizeReplyContent(content, normalizeInner) {
  const target = content?.target;
  const inner =
    content?.content && typeof content.content === "object"
      ? await normalizeInner(content.content)
      : null;
  return {
    type: "reply",
    content: inner,
    targetMessageId: target?.id ?? null,
    targetDirection: target?.direction ?? null,
    targetText: messageTextPreview(target),
  };
}
