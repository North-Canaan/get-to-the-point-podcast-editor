const ANONYMOUS_FEED_STORAGE_KEY = "get-to-the-point-private-feed-token";

export async function claimAnonymousFeed() {
  const token = localStorage.getItem(ANONYMOUS_FEED_STORAGE_KEY) || "";
  if (!/^[A-Za-z0-9_-]{32,256}$/.test(token)) return { claimed_episodes: 0 };

  const response = await fetch("/me/claim-anonymous-feed", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ token })
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    throw new Error(payload.detail || "Could not associate existing episodes with this account");
  }
  const payload = await response.json();
  localStorage.removeItem(ANONYMOUS_FEED_STORAGE_KEY);
  return payload;
}
