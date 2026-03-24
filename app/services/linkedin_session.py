"""
LinkedIn Session Manager — Playwright-based scraping with user credentials.
Handles login, session storage, and full JS-rendered scraping.
"""
import asyncio
import json
import logging
from typing import Optional, List, Dict, Any
from datetime import datetime, timezone
from dataclasses import dataclass, field, asdict
from urllib.parse import urljoin

from ..core.config import settings

logger = logging.getLogger(__name__)


@dataclass
class LinkedInPost:
    """A single LinkedIn post."""
    post_text: str
    post_url: str
    posted_at: str
    likes: int = 0
    comments: int = 0
    shares: int = 0
    sentiment: str = "neutral"  # positive, neutral, negative, frustrated


@dataclass
class LinkedInProfile:
    """A LinkedIn profile result."""
    name: str
    headline: str
    current_company: str
    location: str
    about: str
    recent_posts: List[LinkedInPost] = field(default_factory=list)
    hiring_signals: Dict[str, Any] = field(default_factory=dict)


@dataclass
class LinkedInScrapeResult:
    """Result of scraping a LinkedIn URL."""
    url: str
    profile: Optional[LinkedInProfile] = None
    posts: List[LinkedInPost] = field(default_factory=list)
    hiring_signals: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None


def _encrypt(data: str) -> str:
    """Encrypt session data using Fernet (AES-128-CBC)."""
    from cryptography.fernet import Fernet
    key = settings.SECRET_KEY.encode()[:32].ljust(32, b"0")
    f = Fernet(Fernet.generate_key() if len(key) < 32 else key[:32].hex()[:32].encode())
    # Actually use the secret key properly
    import base64
    key_bytes = settings.SECRET_KEY.encode().ljust(32, b"0")[:32]
    import hashlib
    hashed_key = base64.urlsafe_b64encode(hashlib.sha256(key_bytes).digest())
    f = Fernet(hashed_key)
    return f.encrypt(data.encode()).decode()


def _decrypt(data: str) -> str:
    """Decrypt session data."""
    from cryptography.fernet import Fernet
    import base64
    key_bytes = settings.SECRET_KEY.encode().ljust(32, b"0")[:32]
    import hashlib
    hashed_key = base64.urlsafe_b64encode(hashlib.sha256(key_bytes).digest())
    f = Fernet(hashed_key)
    return f.decrypt(data.encode()).decode()


def encrypt_cookies(cookies: List[dict]) -> str:
    """Encrypt a list of cookies to a JSON string."""
    return _encrypt(json.dumps(cookies))


def decrypt_cookies(encrypted: str) -> List[dict]:
    """Decrypt cookies from stored string."""
    return json.loads(_decrypt(encrypted))


class LinkedInSessionManager:
    """
    Manages LinkedIn browser sessions using Playwright.
    User logs in once → cookies stored → reused for all scraping.
    """

    def __init__(self):
        self.browser_args = [
            "--disable-blink-features=AutomationControlled",
            "--disable-dev-shm-usage",
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--disable-web-security",
            "--disable-features=IsolateOrigins,site-per-process",
        ]

    async def login(self, email: str, password: str) -> Dict[str, Any]:
        """
        Log into LinkedIn using Playwright headless browser.
        Returns: {success, cookies, username, error}
        """
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            return {
                "success": False,
                "error": "Playwright not installed. Run: pip install playwright && python -m playwright install chromium",
            }

        cookies = []
        username = ""

        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(
                    headless=True,
                    args=self.browser_args,
                )
                context = await browser.new_context(
                    viewport={"width": 1280, "height": 800},
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                    locale="en-US",
                )

                page = await context.new_page()

                # Go to LinkedIn login
                await page.goto("https://www.linkedin.com/login", wait_until="domcontentloaded", timeout=30000)

                # Fill credentials
                await page.fill("#username", email)
                await page.fill("#password", password)
                await page.click('button[type="submit"]')

                # Wait for either feed redirect or error
                try:
                    await page.wait_for_url("**/feed/**", timeout=15000)
                    username = await self._extract_username(page)
                except Exception:
                    # Check for error message
                    error_elem = await page.query_selector(".alert.error")
                    if error_elem:
                        error_text = await error_elem.inner_text()
                        if "wrong" in error_text.lower() or "incorrect" in error_text.lower():
                            return {"success": False, "error": "Incorrect email or password"}
                        if "2fa" in error_text.lower() or "verification" in error_text.lower():
                            return {"success": False, "error": "Two-factor authentication required. Please disable 2FA on your LinkedIn account or use a backup code."}
                        return {"success": False, "error": error_text.strip()}

                    # Check URL - might be on feed anyway
                    url = page.url
                    if "feed" not in url:
                        return {"success": False, "error": "Login failed. Check your credentials."}

                # Extract cookies
                cookies = await context.cookies()
                # Filter to just the ones we need
                cookie_names = {"li_a", "li_at", "JSESSIONID", "li_mc", "lidc", "bcookie", "s_psi", "s_tapp"}
                cookies = [c for c in cookies if c["name"] in cookie_names]

                if not cookies:
                    return {"success": False, "error": "Login succeeded but couldn't extract session cookies. Try again."}

                await browser.close()

            return {
                "success": True,
                "cookies": cookies,
                "username": username or email.split("@")[0],
            }

        except Exception as e:
            logger.error(f"LinkedIn login error: {e}")
            return {"success": False, "error": str(e)}

    async def _extract_username(self, page) -> str:
        """Extract the user's name from the LinkedIn profile."""
        try:
            # Try to get from the profile dropdown
            name_elem = await page.query_selector(".profile-card-details__name")
            if name_elem:
                return await name_elem.inner_text()
            # Fallback: from the avatar alt text or nav
            nav_name = await page.query_selector(".feed-identity-module__name")
            if nav_name:
                return await nav_name.inner_text()
            return ""
        except Exception:
            return ""

    async def scrape_url(self, url: str, cookies: List[dict]) -> LinkedInScrapeResult:
        """
        Scrape a LinkedIn URL with full JS rendering.
        Handles profiles, company pages, and posts.
        """
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            return LinkedInScrapeResult(
                url=url,
                error="Playwright not installed. Run: pip install playwright && python -m playwright install chromium"
            )

        if not cookies:
            return LinkedInScrapeResult(url=url, error="No session cookies provided")

        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True, args=self.browser_args)
                context = await browser.new_context(
                    viewport={"width": 1280, "height": 800},
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                    locale="en-US",
                )
                await context.add_cookies(cookies)

                page = await context.new_page()
                result = LinkedInScrapeResult(url=url)

                if "/company/" in url or "/school/" in url:
                    result.posts = await self._scrape_company_posts(page, url)
                    result.hiring_signals = await self._scrape_company_hiring(page, url)
                elif "/in/" in url:
                    result.profile = await self._scrape_profile(page, url)
                else:
                    result.posts = await self._scrape_company_posts(page, url)

                await browser.close()
                return result

        except Exception as e:
            logger.error(f"LinkedIn scrape error for {url}: {e}")
            return LinkedInScrapeResult(url=url, error=str(e))

    async def _scrape_profile(self, page, url: str) -> Optional[LinkedInProfile]:
        """Scrape a LinkedIn profile page."""
        try:
            await page.goto(url, wait_until="networkidle", timeout=30000)
            await page.wait_for_timeout(2000)  # Extra wait for JS

            name = ""
            headline = ""
            current_company = ""
            location = ""
            about = ""

            # Try multiple selectors for name
            for selector in [".pv-top-card vce-display-flex align-items-center", ".pv-top-card--about"]:
                try:
                    elem = await page.query_selector(selector)
                    if elem:
                        name = await elem.inner_text()
                        break
                except Exception:
                    continue

            # Headline
            for selector in [".pv-top-card--headline", ".text-body-medium"]:
                try:
                    elem = await page.query_selector(selector)
                    if elem:
                        headline = await elem.inner_text()
                        break
                except Exception:
                    continue

            # About section
            for selector in [".pv-about-section", "#about-section"]:
                try:
                    elem = await page.query_selector(selector)
                    if elem:
                        about = await elem.inner_text()
                        break
                except Exception:
                    continue

            return LinkedInProfile(
                name=name.strip(),
                headline=headline.strip(),
                current_company=current_company.strip(),
                location=location.strip(),
                about=about.strip().replace("…see more", ""),
            )
        except Exception as e:
            logger.warning(f"Profile scrape error: {e}")
            return None

    async def _scrape_company_posts(self, page, url: str) -> List[LinkedInPost]:
        """Scrape company posts with engagement data."""
        posts = []
        try:
            posts_url = url if "/posts" in url else url.rstrip("/") + "/posts/"
            await page.goto(posts_url, wait_until="networkidle", timeout=30000)
            await page.wait_for_timeout(3000)

            # Scroll to load more posts
            for _ in range(3):
                await page.evaluate("window.scrollBy(0, 800)")
                await page.wait_for_timeout(1000)

            # Look for post containers — LinkedIn uses various selectors
            post_selectors = [
                ".occludable-update",
                ".feed-shared-update-v2",
                ".scaffold-finite-cover",
                "[data-urn*='activity']",
            ]

            for selector in post_selectors:
                elements = await page.query_selector_all(selector)
                if elements:
                    for elem in elements[:10]:  # Top 10 posts
                        try:
                            text = await elem.inner_text()
                            # Extract likes/comments/shares
                            likes = self._extract_metric(text, "like")
                            comments = self._extract_metric(text, "comment")
                            shares = self._extract_metric(text, "share")

                            sentiment = "neutral"
                            if any(w in text.lower() for w in ["frustrated", "hate", "terrible", "worst", "disappointed", "annoyed"]):
                                sentiment = "frustrated"
                            elif any(w in text.lower() for w in ["love", "great", "amazing", "excellent", "best", "thank"]):
                                sentiment = "positive"

                            posts.append(LinkedInPost(
                                post_text=text[:500] if text else "",
                                post_url="",
                                posted_at=datetime.now(timezone.utc).isoformat(),
                                likes=likes,
                                comments=comments,
                                shares=shares,
                                sentiment=sentiment,
                            ))
                        except Exception:
                            continue
                    break

        except Exception as e:
            logger.warning(f"Company posts scrape error: {e}")

        return posts

    def _extract_metric(self, text: str, metric: str) -> int:
        """Extract a metric (likes/comments/shares) from post text."""
        import re
        patterns = [
            rf"(\d+(?:,\d+)*)\s*{metric}",
            rf"(\d+(?:,\d+)*)\s*{metric}s",
            rf"(\d+(?:,\d+)*)\s*(?:reaction|{metric})",
        ]
        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                num_str = match.group(1).replace(",", "")
                return int(num_str)
        return 0

    async def _scrape_company_hiring(self, page, url: str) -> Dict[str, Any]:
        """Scrape company hiring signals."""
        try:
            await page.goto(url.rstrip("/") + "/careers/", wait_until="networkidle", timeout=20000)
            await page.wait_for_timeout(2000)

            text = await page.inner_text("body")
            text_lower = text.lower()

            is_hiring = any(kw in text_lower for kw in [
                "we're hiring", "join our team", "open position",
                "hiring now", "careers at", "job opening"
            ])

            # Count job postings
            job_count = text_lower.count("job") // 2  # Rough estimate

            departments = []
            for dept in ["engineering", "sales", "marketing", "product", "operations", "design", "data", "customer success"]:
                if dept in text_lower:
                    departments.append(dept)

            return {
                "hiring_active": is_hiring,
                "open_roles": job_count,
                "departments": departments,
            }
        except Exception:
            return {"hiring_active": False, "open_roles": 0, "departments": []}

    async def validate_session(self, cookies: List[dict]) -> bool:
        """Check if cookies are still valid."""
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            return False

        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True, args=self.browser_args)
                context = await browser.new_context()
                await context.add_cookies(cookies)
                page = await context.new_page()
                await page.goto("https://www.linkedin.com/feed", wait_until="domcontentloaded", timeout=15000)
                url = page.url
                await browser.close()
                return "feed" in url or "mynetwork" not in url
        except Exception:
            return False


# Singleton instance
_linkedin_manager: Optional[LinkedInSessionManager] = None

def get_linkedin_manager() -> LinkedInSessionManager:
    global _linkedin_manager
    if _linkedin_manager is None:
        _linkedin_manager = LinkedInSessionManager()
    return _linkedin_manager
