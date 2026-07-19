from html import escape

import httpx

from .config import Settings


def send_private_feed_email(email: str, feed_url: str, settings: Settings) -> None:
    if not settings.resend_api_key:
        raise RuntimeError("Email delivery is not configured")

    safe_url = escape(feed_url, quote=True)
    html = (
        "<h1>Your personal podcast feed</h1>"
        "<p>Add this private feed once and your edited episodes will appear in your podcast app.</p>"
        f'<p><a href="{safe_url}">Open your private feed</a></p>'
        f"<p><code>{safe_url}</code></p>"
        "<p>In Apple Podcasts, open Library, choose the More menu, then choose "
        "<strong>Follow a Show by URL</strong>. Other apps usually call this "
        "<strong>Add URL</strong> or <strong>Add RSS feed</strong>.</p>"
        "<p><strong>Keep this link private.</strong> Anyone with it can play your edited episodes.</p>"
    )
    with httpx.Client(timeout=20.0) as client:
        response = client.post(
            "https://api.resend.com/emails",
            headers={
                "Authorization": f"Bearer {settings.resend_api_key}",
                "Content-Type": "application/json",
            },
            json={
                "from": settings.feed_email_from,
                "to": [email],
                "subject": "Your personal Get To The Point podcast feed",
                "html": html,
            },
        )
        response.raise_for_status()
