(async () => {
  const links = document.querySelectorAll("[data-auth-link]");
  if (!links.length) return;
  try {
    const response = await fetch("/api/auth/get-session", { credentials: "same-origin" });
    const session = response.ok ? await response.json() : null;
    if (session?.user) {
      try {
        const { claimAnonymousFeed } = await import("/assets/feed-claim.js");
        await claimAnonymousFeed();
      } catch (error) {
        console.error("Anonymous feed claim failed", error);
      }
    }
    for (const link of links) {
      link.href = session?.user ? "/account.html" : "/auth.html";
      link.textContent = session?.user ? "My saved episodes" : "Sign in to save progress";
    }
  } catch {
    for (const link of links) {
      link.href = "/auth.html";
      link.textContent = "Sign in to save progress";
    }
  }
})();
