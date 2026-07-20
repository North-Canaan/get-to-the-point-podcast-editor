export const PADDING_SECONDS = 0.3;
export const STANDARD_OUTPUT_KBPS = 128;
export const SAFE_OUTPUT_BYTES = 43_000_000;
export const HARD_OUTPUT_BYTES = 47_000_000;
export const LARGE_SOURCE_BYTES = 60 * 1024 * 1024;

export function isValidHighlight(item) {
  if (!item || typeof item !== "object") return false;
  const start = Number(item.start);
  const end = Number(item.end);
  return Number.isFinite(start)
    && Number.isFinite(end)
    && start >= 0
    && end > start
    && end <= 86_400;
}

export function filterValidHighlights(items) {
  return Array.isArray(items) ? items.filter(isValidHighlight) : [];
}

export function selectedHighlightCountsByTopic(highlights, decisions) {
  const counts = new Map();
  if (!Array.isArray(highlights) || !decisions || typeof decisions.get !== "function") {
    return counts;
  }
  highlights.forEach((highlight) => {
    const topic = typeof highlight?.topic === "string" ? highlight.topic.trim() : "";
    if (topic && decisions.get(highlight.id)?.decision === "approve") {
      counts.set(topic, (counts.get(topic) || 0) + 1);
    }
  });
  return counts;
}

export function selectedOutputSeconds(
  segments,
  sourceDuration = Infinity,
  paddingSeconds = PADDING_SECONDS
) {
  if (!Array.isArray(segments)) return 0;
  return segments.reduce((total, segment) => {
    const start = Math.max(0, Number(segment?.start) - paddingSeconds);
    const end = Math.min(sourceDuration, Number(segment?.end) + paddingSeconds);
    if (!Number.isFinite(start) || !Number.isFinite(end)) return total;
    return total + Math.max(0, end - start);
  }, 0);
}

export function totalOutputSeconds(segments, sourceDuration = Infinity, transitionSeconds = 0) {
  const highlightSeconds = selectedOutputSeconds(segments, sourceDuration);
  const transitionCount = Math.max(0, (Array.isArray(segments) ? segments.length : 0) - 1);
  const safeTransitionSeconds = Number.isFinite(Number(transitionSeconds))
    ? Math.max(0, Number(transitionSeconds))
    : 0;
  return highlightSeconds + transitionCount * safeTransitionSeconds;
}

export function interleaveTransitionFiles(clipNames, transitionName = "") {
  if (!Array.isArray(clipNames)) return [];
  return clipNames.flatMap((name, index) => (
    transitionName && index < clipNames.length - 1 ? [name, transitionName] : [name]
  ));
}

export function estimateMp3Bytes(seconds, bitrateKbps) {
  if (!(seconds > 0) || !(bitrateKbps > 0)) return 0;
  return Math.ceil((seconds * bitrateKbps * 1000) / 8 * 1.02 + 64 * 1024);
}

export function needsCompressedEditing(metadata, segments, transitionSeconds = 0) {
  const sourceBytes = Number(metadata?.size_bytes) || 0;
  const selectedSeconds = totalOutputSeconds(segments, metadata?.duration, transitionSeconds);
  return sourceBytes > LARGE_SOURCE_BYTES
    || estimateMp3Bytes(selectedSeconds, STANDARD_OUTPUT_KBPS) > SAFE_OUTPUT_BYTES;
}

export function minimumOutputFits(segments, sourceDuration, transitionSeconds = 0) {
  const seconds = totalOutputSeconds(segments, sourceDuration, transitionSeconds);
  return estimateMp3Bytes(seconds, 32) <= SAFE_OUTPUT_BYTES;
}
