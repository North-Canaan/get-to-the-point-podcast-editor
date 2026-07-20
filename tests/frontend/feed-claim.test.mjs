import test from "node:test";
import assert from "node:assert/strict";

import { claimAnonymousFeed } from "../../public/assets/feed-claim.js";

const STORAGE_KEY = "get-to-the-point-private-feed-token";

function memoryStorage(token) {
  const values = new Map(token ? [[STORAGE_KEY, token]] : []);
  return {
    getItem: (key) => values.get(key) || null,
    removeItem: (key) => values.delete(key),
    has: (key) => values.has(key)
  };
}

test("a successful account claim sends the anonymous bearer token then removes it", async () => {
  const token = "a".repeat(43);
  const storage = memoryStorage(token);
  globalThis.localStorage = storage;
  let request;
  globalThis.fetch = async (url, options) => {
    request = { url, options };
    return new Response(JSON.stringify({ claimed_episodes: 2 }), {
      status: 200,
      headers: { "Content-Type": "application/json" }
    });
  };

  const payload = await claimAnonymousFeed();

  assert.equal(payload.claimed_episodes, 2);
  assert.equal(request.url, "/me/claim-anonymous-feed");
  assert.equal(JSON.parse(request.options.body).token, token);
  assert.equal(storage.has(STORAGE_KEY), false);
});

test("a failed claim retains the token so the account page can retry", async () => {
  const token = "b".repeat(43);
  const storage = memoryStorage(token);
  globalThis.localStorage = storage;
  globalThis.fetch = async () => new Response(JSON.stringify({ detail: "temporary failure" }), {
    status: 503,
    headers: { "Content-Type": "application/json" }
  });

  await assert.rejects(claimAnonymousFeed(), /temporary failure/);
  assert.equal(storage.has(STORAGE_KEY), true);
});
