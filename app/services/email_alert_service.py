"""
Email alert service — score-change notifications via Resend.

Template is distinct from the generic insight alerts in notifier.py.
Sent by the AlertEngine for tier changes and score spikes.
"""
import logging
from typing import Optional

import httpx

from ..core.config import settings

logger = logging.getLogger(__name__)

RESEND_API_URL = "https://api.resend.com/emails"


async def send_score_alert_email(
    to_email: str,
    prospect_name: str,
    company: str,
    alert_type: str,
    old_score: float,
    new_score: float,
    old_tier: Optional[str] = None,
    new_tier: Optional[str] = None,
    prospect_id: Optional[str] = None,
) -> bool:
    """
    Send a score-change alert email via Resend.

    Args:
        to_email:        Recipient email address.
        prospect_name:   Full name of the prospect.
        company:         Company name.
        alert_type:      One of "score_spike", "tier_up", "tier_down".
        old_score:       Score before the change.
        new_score:       Score after the change.
        old_tier:        Previous tier (for tier_change types).
        new_tier:        New tier (for tier_change types).
        prospect_id:     Prospect UUID, used to build the deep link.

    Returns:
        True if email was accepted by Resend, False otherwise.
    """
    if not to_email:
        logger.debug(f"EMAIL: No email address for {prospect_name}, skipping.")
        return False

    api_key = settings.RESEND_API_KEY
    if not api_key:
        logger.warning("EMAIL: RESEND_API_KEY not configured, skipping email.")
        return False

    frontend_url = getattr(settings, "FRONTEND_URL", "http://localhost:3000")
    prospect_url = (
        f"{frontend_url}/prospects/{prospect_id}" if prospect_id else frontend_url
    )

    # ── Build subject + HTML body ──────────────────────────────────────────

    if alert_type == "score_spike":
        change = new_score - old_score
        subject = f"Score Spike Alert: {prospect_name} +{change:.0f}pts"
        color = "#16a34a"  # green
        heading = "Score Spike"
        badge_text = f"+{change:.0f} pts"
        body_lines = [
            f"<strong>{prospect_name}</strong> at <strong>{company}</strong> just increased "
            f"by <strong style=\"color:{color}\">+{change:.0f} points</strong>.",
            f"Current score: <strong>{new_score:.0f}</strong>",
            "This is a strong buying signal — time to reach out.",
        ]

    elif alert_type in ("tier_up", "tier_down"):
        direction_emoji = "&#9650;" if alert_type == "tier_up" else "&#9660;"
        tier_color = "#f97316" if new_tier == "hot" else "#eab308" if new_tier == "warm" else "#6b7280"
        badge_text = new_tier.upper() if new_tier else "TIER CHANGE"
        heading = "Tier Change"
        subject = f"Tier Alert: {prospect_name} → {new_tier.upper() if new_tier else '?'}"
        body_lines = [
            f"<strong>{prospect_name}</strong> at <strong>{company}</strong> "
            f"moved from <strong>{old_tier.upper() if old_tier else '?'}</strong> "
            f'to <strong style="color:{tier_color}">'
            f"{new_tier.upper() if new_tier else '?'}</strong>.",
            f"Current score: <strong>{new_score:.0f}</strong>",
            "Priority level has changed — review your outreach strategy.",
        ]

    else:
        subject = f"Alert: {prospect_name}"
        color = "#6366f1"
        heading = "Alert"
        badge_text = "ALERT"
        body_lines = [
            f"<strong>{prospect_name}</strong> at <strong>{company}</strong>",
            f"Current score: <strong>{new_score:.0f}</strong>",
        ]

    html_body = "\n".join(f"<p>{line}</p>" for line in body_lines)

    html = f"""
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
           line-height: 1.6; color: #1f2937; background: #f3f4f6; margin: 0; padding: 0; }}
    .wrapper {{ max-width: 600px; margin: 32px auto; background: #ffffff;
               border-radius: 12px; overflow: hidden; box-shadow: 0 2px 8px rgba(0,0,0,0.08); }}
    .header {{ background: linear-gradient(135deg, #6366f1 0%, #8b5cf6 100%);
               padding: 32px; color: white; }}
    .header h1 {{ margin: 0; font-size: 22px; font-weight: 700; }}
    .badge {{ display: inline-block; background: rgba(255,255,255,0.2);
              padding: 4px 12px; border-radius: 20px; font-size: 11px;
              font-weight: 700; text-transform: uppercase; letter-spacing: 0.05em;
              margin-bottom: 8px; }}
    .header p {{ margin: 8px 0 0; opacity: 0.85; font-size: 14px; }}
    .body {{ padding: 32px; }}
    .body p {{ margin: 0 0 16px; font-size: 16px; color: #374151; }}
    .body p:last-child {{ margin-bottom: 0; }}
    .cta {{ display: inline-block; margin-top: 24px;
            background: #6366f1; color: white; padding: 14px 28px;
            border-radius: 8px; text-decoration: none; font-weight: 600;
            font-size: 15px; }}
    .cta:hover {{ background: #4f46e5; }}
    .footer {{ text-align: center; padding: 20px 32px;
                border-top: 1px solid #e5e7eb; color: #9ca3af;
                font-size: 12px; }}
    .footer a {{ color: #6366f1; text-decoration: none; }}
  </style>
</head>
<body>
  <div class="wrapper">
    <div class="header">
      <div class="badge">{badge_text}</div>
      <h1>{heading} — {prospect_name}</h1>
      <p>{company}</p>
    </div>
    <div class="body">
      {html_body}
      <a href="{prospect_url}" class="cta">View in Almanac &rarr;</a>
    </div>
    <div class="footer">
      <p>You're receiving this because you have prospect alerts enabled in Almanac.</p>
      <p><a href="#">Manage alerts</a> &middot; <a href="#">Unsubscribe</a></p>
    </div>
  </div>
</body>
</html>
"""

    # ── Send via Resend --------------------------------------------------------
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                RESEND_API_URL,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "from": settings.FROM_EMAIL or "Almanac <alerts@resend.dev>",
                    "to": [to_email],
                    "subject": subject,
                    "html": html,
                },
            )

        if resp.status_code in (200, 201):
            logger.info(f"EMAIL: Sent {alert_type} alert for {prospect_name} to {to_email}")
            return True

        logger.warning(f"EMAIL: Resend returned {resp.status_code}: {resp.text[:200]}")
        return False

    except Exception as e:
        logger.error(f"EMAIL: Failed to send alert to {to_email}: {e}")
        return False
