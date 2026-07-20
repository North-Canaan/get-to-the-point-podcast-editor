import test from "node:test";
import assert from "node:assert/strict";

import {
  HARD_OUTPUT_BYTES,
  LARGE_SOURCE_BYTES,
  SAFE_OUTPUT_BYTES,
  estimateMp3Bytes,
  filterValidHighlights,
  interleaveTransitionFiles,
  minimumOutputFits,
  needsCompressedEditing,
  selectedHighlightCountsByTopic,
  selectedOutputSeconds,
  totalOutputSeconds
} from "../../public/assets/review-logic.js";

test("invalid highlight ranges never reach the review picker", () => {
  const valid = { id: "valid", start: 2, end: 8 };
  const highlights = [
    valid,
    { id: "zero", start: 5, end: 5 },
    { id: "reverse", start: 9, end: 2 },
    { id: "negative", start: -1, end: 2 },
    { id: "too-long", start: 1, end: 86_401 },
    { id: "nan", start: "not-a-number", end: 3 },
    null
  ];

  assert.deepEqual(filterValidHighlights(highlights), [valid]);
  assert.deepEqual(filterValidHighlights(undefined), []);
});

test("topics retain an indicator while any of their highlights are selected", () => {
  const highlights = [
    { id: "a", topic: "Science" },
    { id: "b", topic: "Science" },
    { id: "c", topic: "Health" },
    { id: "d", topic: "  " }
  ];
  const decisions = new Map([
    ["a", { decision: "approve" }],
    ["b", { decision: "reject" }],
    ["c", { decision: "approve" }],
    ["d", { decision: "approve" }]
  ]);

  assert.deepEqual(
    [...selectedHighlightCountsByTopic(highlights, decisions)],
    [["Science", 1], ["Health", 1]]
  );
  decisions.get("a").decision = "";
  assert.deepEqual([...selectedHighlightCountsByTopic(highlights, decisions)], [["Health", 1]]);
});

test("selected duration includes padding and clamps to source boundaries", () => {
  const seconds = selectedOutputSeconds(
    [{ start: 0.1, end: 1 }, { start: 9.8, end: 10 }],
    10
  );
  assert.equal(seconds, 1.8);
});

test("transitions are inserted only between highlights and included in estimates", () => {
  const clips = ["one.mp3", "two.mp3", "three.mp3"];
  assert.deepEqual(
    interleaveTransitionFiles(clips, "pause.mp3"),
    ["one.mp3", "pause.mp3", "two.mp3", "pause.mp3", "three.mp3"]
  );
  assert.deepEqual(interleaveTransitionFiles(clips, ""), clips);
  assert.ok(Math.abs(
    totalOutputSeconds([{ start: 0, end: 10 }, { start: 20, end: 30 }], 40, 0.5) - 21.4
  ) < 0.0001);
});

test("malformed segments do not poison the selected duration with NaN", () => {
  assert.equal(selectedOutputSeconds([{ start: "bad", end: 5 }]), 0);
  assert.equal(selectedOutputSeconds([{ start: 1, end: Infinity }]), 0);
  assert.equal(selectedOutputSeconds(undefined), 0);
});

test("a large estimated edit uses compressed editing even with a small source", () => {
  const segments = [{ start: 0, end: 62 * 60 }];
  const metadata = { size_bytes: 40 * 1024 * 1024, duration: 70 * 60 };

  assert.ok(estimateMp3Bytes(selectedOutputSeconds(segments), 128) > SAFE_OUTPUT_BYTES);
  assert.equal(needsCompressedEditing(metadata, segments), true);
});

test("a source above the browser-safe limit uses ranged editing", () => {
  const metadata = { size_bytes: LARGE_SOURCE_BYTES + 1, duration: 3600 };
  assert.equal(needsCompressedEditing(metadata, [{ start: 0, end: 30 }]), true);
});

test("a short edit from a small source stays on the standard path", () => {
  const metadata = { size_bytes: 20 * 1024 * 1024, duration: 3600 };
  assert.equal(needsCompressedEditing(metadata, [{ start: 10, end: 70 }]), false);
});

test("the preflight rejects selections that cannot fit even at minimum bitrate", () => {
  assert.equal(minimumOutputFits([{ start: 0, end: 4 * 60 * 60 }], 4 * 60 * 60), false);
  assert.equal(minimumOutputFits([{ start: 0, end: 10 * 60 }], 10 * 60), true);
});

test("warning and hard upload limits retain a safety margin", () => {
  assert.ok(SAFE_OUTPUT_BYTES < HARD_OUTPUT_BYTES);
  assert.equal(estimateMp3Bytes(0, 128), 0);
  assert.ok(estimateMp3Bytes(45 * 60, 128) > SAFE_OUTPUT_BYTES);
});
