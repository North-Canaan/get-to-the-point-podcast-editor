(async () => {
  const links = document.querySelectorAll("[data-auth-link]");
  if (!links.length) return;
  try {
    const response = await fetch("/api/auth/get-session", { credentials: "same-origin" });
    const session = response.ok ? await response.json() : null;
    for (const link of links) {
      link.href = session?.user ? "/account.html" : "/auth.html";
      link.textContent = session?.user ? "My episodes" : "Sign in";
    }
  } catch {
    for (const link of links) {
      link.href = "/auth.html";
      link.textContent = "Sign in";
    }
  }
})();
