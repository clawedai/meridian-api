import httpx
import os
import ipaddress
import re
from typing import Dict, Any, List
from datetime import datetime


def validate_webhook_url(url: str) -> tuple[bool, str]:
    """
    Validate webhook URL to prevent SSRF attacks.

    Returns:
        tuple: (is_valid, error_message)
    """
    if not url:
        return False, "No webhook URL provided"

    # Only allow HTTPS URLs in production
    if not url.startswith("https://"):
        return False, "Webhook URL must use HTTPS"

    # Only allow http:// for localhost development
    if url.startswith("http://") and not url.startswith("http://localhost") and not url.startswith("http://127.0.0.1"):
        return False, "Only localhost HTTP URLs are allowed for development"

    try:
        # Parse the URL
        parsed = re.match(r"^(https?)://([^/:]+)(?::(\d+))?(/.*)?$", url)
        if not parsed:
            return False, "Invalid URL format"

        scheme, host, port, path = parsed.groups()

        # Check for localhost variations
        localhost_patterns = [
            "localhost",
            "127.0.0.1",
            "::1",
            "0.0.0.0",
            "::",
            # IPv6 localhost
            "localhost.localdomain",
        ]
        if host.lower() in localhost_patterns:
            return False, "Localhost URLs are not allowed"

        # Resolve hostname to IP
        try:
            # Block private/reserved ranges
            private_ranges = [
                ipaddress.ip_network("10.0.0.0/8"),
                ipaddress.ip_network("172.16.0.0/12"),
                ipaddress.ip_network("192.168.0.0/16"),
                ipaddress.ip_network("169.254.0.0/16"),  # Link-local
                ipaddress.ip_network("0.0.0.0/8"),  # Current network
                ipaddress.ip_network("100.64.0.0/10"),  # Carrier-grade NAT
                ipaddress.ip_network("192.0.0.0/24"),  # IETF Protocol Assignments
                ipaddress.ip_network("192.0.2.0/24"),  # TEST-NET-1
                ipaddress.ip_network("198.51.100.0/24"),  # TEST-NET-2
                ipaddress.ip_network("203.0.113.0/24"),  # TEST-NET-3
                ipaddress.ip_network("fc00::/7"),  # IPv6 unique local
                ipaddress.ip_network("fe80::/10"),  # IPv6 link-local
                ipaddress.ip_network("127.0.0.0/8"),  # IPv4 loopback
            ]

            # Try to resolve host
            try:
                addr_info = __import__("socket").getaddrinfo(host, None)
                for family, _, _, _, sockaddr in addr_info:
                    ip_str = sockaddr[0]
                    try:
                        ip = ipaddress.ip_address(ip_str)
                        for network in private_ranges:
                            if ip in network:
                                return False, f"Private IP range not allowed: {ip_str}"
                    except ValueError:
                        continue
            except Exception:
                # If we can't resolve, still check the hostname itself
                pass

            # Additional hostname-based checks
            host_lower = host.lower()

            # Block internal hostnames
            internal_patterns = [
                r".*\.local$",
                r".*\.localdomain$",
                r".*\.internal$",
                r".*\.intranet$",
                r".*\.private$",
                r"host\.docker\.internal",
                r"gateway\.internal",
                r"metadata\.google\.internal",  # GCP metadata
                r"169\.254\.169\.254",  # AWS/GCP/Azure metadata endpoint
            ]

            for pattern in internal_patterns:
                if re.match(pattern, host_lower):
                    return False, f"Internal hostname not allowed: {host}"

            # Block numeric IP addresses (except explicit localhost 127.0.0.1 which we already blocked)
            ip_pattern = r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$"
            if re.match(ip_pattern, host):
                return False, "Direct IP addresses are not allowed. Use a domain name."

        except Exception as e:
            return False, f"URL validation error: {str(e)}"

        return True, ""

    except Exception as e:
        return False, f"Invalid webhook URL: {str(e)}"

class EmailNotifier:
    """Email notification service using Resend"""

    def __init__(self):
        self.api_key = os.getenv("RESEND_API_KEY")
        self.from_email = os.getenv("FROM_EMAIL", "Almanac <alerts@resend.dev>")
        self.api_url = "https://api.resend.com/emails"

    async def send(
        self,
        to: str,
        subject: str,
        html_content: str
    ) -> Dict[str, Any]:
        """Send email notification"""

        if not self.api_key:
            print("RESEND_API_KEY not set - email not sent")
            return {"success": False, "error": "Email service not configured"}

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    self.api_url,
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "from": self.from_email,
                        "to": [to],
                        "subject": subject,
                        "html": html_content,
                    }
                )

                if response.status_code == 200:
                    return {"success": True, "data": response.json()}
                else:
                    return {"success": False, "error": response.text}

        except Exception as e:
            return {"success": False, "error": str(e)}

    async def send_alert(self, to: str, insight: Dict[str, Any], entity_name: str) -> Dict[str, Any]:
        """Send alert notification email"""

        importance = insight.get("importance", "medium")
        importance_emoji = {
            "critical": "🚨",
            "high": "⚠️",
            "medium": "📢",
            "low": "ℹ️",
        }.get(importance, "📢")

        badge_color = {
            "critical": "#dc2626",
            "high": "#d97706",
            "medium": "#6366f1",
            "low": "#6b7280",
        }.get(importance, "#6366f1")

        html = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <style>
                body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; line-height: 1.6; color: #333; }}
                .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
                .header {{ background: linear-gradient(135deg, #6366f1 0%, #8b5cf6 100%); color: white; padding: 30px; border-radius: 12px 12px 0 0; }}
                .content {{ background: #f9fafb; padding: 30px; border-radius: 0 0 12px 12px; }}
                .alert-card {{ background: white; border-radius: 8px; padding: 20px; margin: 15px 0; border-left: 4px solid {badge_color}; }}
                .title {{ font-size: 18px; font-weight: 600; margin-bottom: 10px; color: #111; }}
                .body-text {{ color: #555; margin-bottom: 15px; }}
                .meta {{ display: flex; gap: 15px; font-size: 12px; color: #888; }}
                .badge {{ display: inline-block; padding: 4px 12px; border-radius: 20px; font-size: 11px; font-weight: 600; text-transform: uppercase; }}
                .footer {{ text-align: center; padding: 20px; color: #888; font-size: 12px; }}
                a {{ color: #6366f1; text-decoration: none; }}
            </style>
        </head>
        <body>
            <div class="container">
                <div class="header">
                    <h1 style="margin: 0; font-size: 24px;">Almanac Alert</h1>
                    <p style="margin: 10px 0 0 0; opacity: 0.9;">Intelligence Platform</p>
                </div>
                <div class="content">
                    <div class="alert-card">
                        <div class="title">
                            {importance_emoji} {insight.get('title', 'New Insight')}
                        </div>
                        <div class="body-text">
                            {insight.get('content', insight.get('summary', ''))}
                        </div>
                        <div class="meta">
                            <span class="badge" style="background: {badge_color}20; color: {badge_color};">
                                {importance.upper()}
                            </span>
                            <span>Entity: {entity_name}</span>
                            <span>Confidence: {int(insight.get('confidence', 0) * 100)}%</span>
                        </div>
                    </div>
                    <p style="margin-top: 20px;">
                        <a href="https://almanac.app/dashboard/insights">View in Almanac →</a>
                    </p>
                </div>
                <div class="footer">
                    <p>You received this because you have alerts configured.</p>
                    <p><a href="#">Manage alerts</a> | <a href="#">Unsubscribe</a></p>
                </div>
            </div>
        </body>
        </html>
        """

        return await self.send(
            to=to,
            subject=f"{importance_emoji} Almanac: {insight.get('title', 'New Insight')}",
            html_content=html
        )

class WebhookNotifier:
    """Webhook notification service with SSRF protection"""

    async def send(self, webhook_url: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Send webhook notification with URL validation to prevent SSRF attacks."""

        if not webhook_url:
            return {"success": False, "error": "No webhook URL"}

        # Validate URL to prevent SSRF
        is_valid, error_msg = validate_webhook_url(webhook_url)
        if not is_valid:
            return {"success": False, "error": f"Invalid webhook URL: {error_msg}"}

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(
                    webhook_url,
                    json=payload,
                    headers={
                        "Content-Type": "application/json",
                        "User-Agent": "Almanac/1.0",
                    }
                )

                if response.status_code < 400:
                    return {"success": True, "status": response.status_code}
                else:
                    return {"success": False, "error": f"HTTP {response.status_code}"}

        except Exception as e:
            return {"success": False, "error": str(e)}
