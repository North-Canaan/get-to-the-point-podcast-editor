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

export function estimateMp3Bytes(seconds, bitrateKbps) {
  if (!(seconds > 0) || !(bitrateKbps > 0)) return 0;
  return Math.ceil((seconds * bitrateKbps * 1000) / 8 * 1.02 + 64 * 1024);
}

export function needsCompressedEditing(metadata, segments) {
  const sourceBytes = Number(metadata?.size_bytes) || 0;
  const selectedSeconds = selectedOutputSeconds(segments, metadata?.duration);
  return sourceBytes > LARGE_SOURCE_BYTES
    || estimateMp3Bytes(selectedSeconds, STANDARD_OUTPUT_KBPS) > SAFE_OUTPUT_BYTES;
}

export function minimumOutputFits(segments, sourceDuration) {
  const seconds = selectedOutputSeconds(segments, sourceDuration);
  return estimateMp3Bytes(seconds, 32) <= SAFE_OUTPUT_BYTES;
}
