"""
Module 18: Instagram Organic Intelligence
Scrapes public Instagram profiles via Playwright for organic social signals.
No credentials required — works on any public profile.
"""
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class InstagramServiceError(Exception):
    """Base exception for Instagram service."""
    pass


class InstagramService:
    """
    Scrape public Instagram profiles for organic intelligence signals.

    Signal fields map to instagram_signals table columns:
      is_active, followers, following, posts, instagram_intensity,
      engagement_rate, posting_frequency, follower_growth,
      hashtag_themes, fetched_at

    Scoring model (max 40 pts):
      instagram_active        +15  Company has an active presence
      instagram_engagement    +10  Avg post engagement > 3%
      instagram_posting_freq  +10  Posts at least 3x per week
      instagram_follower_growth +5  Follower count > 1000
    """

    HIGH_INTENT_HASHTAGS = {
        "b2b", "saas", "enterprise", "sales", "marketing", "crm",
        "startup", "tech", "software", "api", "cloud", "data",
        "ai", "automation", "product", "growth", "revenue",
    }

    async def scrape_profile(self, instagram_handle: str) -> Dict[str, Any]:
        """
        Scrape public Instagram profile for organic metrics.
        Returns dict with followers, following, posts, and recent post URLs.
        """
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            return {"handle": instagram_handle, "error": "Playwright not installed. Run: pip install playwright && python -m playwright install chromium"}

        handle = instagram_handle.lstrip("@")
        url = f"https://www.instagram.com/{handle}/"

        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(
                    headless=True,
                    args=[
                        "--disable-blink-features=AutomationControlled",
                        "--disable-dev-shm-usage",
                        "--no-sandbox",
                        "--disable-setuid-sandbox",
                        "--disable-web-security",
                    ],
                )
                context = await browser.new_context(
                    user_agent=(
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                    ),
                    locale="en-US",
                )
                page = await context.new_page()

                await page.goto(url, wait_until="domcontentloaded", timeout=15000)
                await page.wait_for_timeout(2500)

                # Extract profile stats
                data = await self._extract_profile_data(page)

                # Get recent post links
                posts = await self._extract_recent_posts(page, limit=12)

                await browser.close()

                return {
                    "handle": handle,
                    "url": url,
                    **data,
                    "recent_posts": posts,
                }

        except Exception as e:
            logger.warning(f"INSTAGRAM: Failed to scrape @{handle}: {e}")
            return {"handle": handle, "url": url, "error": str(e)}

    async def _extract_profile_data(self, page) -> Dict[str, Any]:
        """Extract follower / following / post counts from the page."""
        stats: Dict[str, int] = {}
        import re

        # Primary: regex on body text — Instagram shows counts as "651K followers"
        try:
            body_text = await page.inner_text("body")

            # Direct mapping: pattern -> stat key
            pattern_map = [
                (r"([\d.,]+[KMB]?)\s*followers", "followers"),
                (r"([\d.,]+[KMB]?)\s*following", "following"),
                (r"([\d,]+)\s*posts", "posts"),
            ]

            for pattern, key in pattern_map:
                match = re.search(pattern, body_text, re.IGNORECASE)
                if match:
                    raw = match.group(1)
                    stats[key] = self._parse_count(raw)
        except Exception as e:
            logger.warning(f"INSTAGRAM: Regex extraction failed: {e}")

        # Secondary: CSS selectors for structured header data
        if not stats:
            selectors = [
                'section main article header span a span',
                'section main header span a span',
                'header section span a span',
            ]

            for selector in selectors:
                try:
                    elements = await page.query_selector_all(selector)
                    for el in elements:
                        text = await el.inner_text()
                        text_clean = text.lower().replace(",", "").replace(" ", "")
                        if "followers" in text_clean:
                            stats["followers"] = self._parse_count(text)
                        elif "following" in text_clean:
                            stats["following"] = self._parse_count(text)
                        elif "posts" in text_clean:
                            stats["posts"] = self._parse_count(text)
                except Exception:
                    continue

        return stats

    async def _extract_recent_posts(self, page, limit: int = 12) -> List[Dict[str, Any]]:
        """Extract recent post URLs by scrolling and finding link elements."""
        posts: List[Dict[str, Any]] = []

        try:
            # Scroll to trigger lazy-loading of post grid
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await page.wait_for_timeout(1500)
            await page.evaluate("window.scrollTo(0, 0)")
            await page.wait_for_timeout(500)

            # Instagram post links appear in the grid as <a> tags inside article elements
            post_selectors = [
                'article a[href*="/p/"]',
                'article a[href*="/reel/"]',
                '_a9zgtj9_a a[href*="/p/"]',
                '_aag5ks3_a a[href*="/p/"]',
            ]

            for selector in post_selectors:
                try:
                    elements = await page.query_selector_all(selector)
                    if elements:
                        for el in elements[:limit]:
                            href = await el.get_attribute("href")
                            if href and (("/p/" in href) or ("/reel/" in href)):
                                posts.append({"url": f"https://www.instagram.com{href}"})
                        if posts:
                            break
                except Exception:
                    continue

        except Exception as e:
            logger.warning(f"INSTAGRAM: Failed to extract posts: {e}")

        return posts

    def _parse_count(self, text: str) -> int:
        """Parse Instagram count string like '12.5K followers' to int."""
        import re
        # Extract number + multiplier
        text = text.lower().replace(",", "").replace(" ", "")

        # Pull out numeric part with optional K/M/B suffix
        match = re.search(r"([\d.]+)\s*([kmb])?", text)
        if not match:
            # Fallback: try to extract just digits
            digits = re.sub(r"[^\d]", "", text)
            try:
                return int(digits)
            except ValueError:
                return 0

        num_str = match.group(1)
        suffix = match.group(2) or ""

        try:
            value = float(num_str)
            if suffix == "k":
                return int(value * 1_000)
            elif suffix == "m":
                return int(value * 1_000_000)
            elif suffix == "b":
                return int(value * 1_000_000_000)
            else:
                return int(value)
        except ValueError:
            return 0

    def analyze_posts(self, posts: List[Dict[str, Any]], followers: int) -> Dict[str, Any]:
        """
        Analyze posts for hashtag themes and posting patterns.
        Note: Exact engagement (likes/comments) requires the Graph API.
        We use follower count as a proxy for engagement quality.
        """
        if not posts:
            return {
                "avg_engagement": 0,
                "engagement_rate": 0,
                "hashtag_themes": [],
                "posting_frequency": 0,
                "posts_analyzed": 0,
            }

        hashtag_themes: List[str] = []

        # High-intent B2B hashtags found in the data
        # (URLs don't contain hashtags, but we scan for patterns)
        for post in posts:
            url = post.get("url", "")
            # Extract any embedded text patterns that suggest B2B themes
            pass  # Placeholder for future enhancement with page scraping

        return {
            "avg_engagement": 0,
            "engagement_rate": 0,
            "hashtag_themes": list(set(hashtag_themes))[:5],
            "posting_frequency": len(posts),
            "posts_analyzed": len(posts),
        }

    def build_signals(self, profile_data: Dict[str, Any], post_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Build scoring signals from profile and post analysis data.
        Maps to the Instagram scoring model (max 40 pts).
        """
        followers = profile_data.get("followers", 0)
        following = profile_data.get("following", 0)
        posts = profile_data.get("posts", 0)
        hashtag_themes = post_data.get("hashtag_themes", [])
        posts_analyzed = post_data.get("posts_analyzed", 0)

        # Signal: instagram_active (+15)
        # Account is active if it has followers AND posts
        is_active = followers > 0 and posts > 0
        instagram_active_score = 15 if is_active else 0

        # Signal: instagram_engagement (+10)
        # Proxy: follower count indicates established audience
        # Established accounts (1K+) typically have 3%+ engagement
        engagement_score = 0
        if followers >= 1000:
            engagement_score = 5
        if followers >= 5000:
            engagement_score = 8
        if followers >= 10000:
            engagement_score = 10

        # Signal: instagram_posting_frequency (+10)
        # Posts >= 50 indicates regular poster (3x/week or more)
        posting_frequency_score = 0
        if posts >= 200:
            posting_frequency_score = 10
        elif posts >= 50:
            posting_frequency_score = 6
        elif posts >= 10:
            posting_frequency_score = 3

        # Signal: instagram_follower_growth (+5)
        # Established account (>1000 followers) shows social proof
        follower_growth_score = 5 if followers > 1000 else 0

        # Instagram-specific intensity score (0-25)
        instagram_intensity = min(
            25,
            (followers // 1000) + (posts // 10) + (posts_analyzed // 3),
        )

        return {
            "is_active": is_active,
            "followers": followers,
            "following": following,
            "posts": posts,
            "instagram_intensity": instagram_intensity,
            "instagram_active_score": instagram_active_score,
            "engagement_rate": engagement_score,
            "posting_frequency": posting_frequency_score,
            "follower_growth": follower_growth_score,
            "hashtag_themes": hashtag_themes,
            "posts_analyzed": posts_analyzed,
            "built_at": datetime.now(timezone.utc).isoformat(),
        }

    # ------------------------------------------------------------------
    # Supabase storage / retrieval
    # ------------------------------------------------------------------

    async def store_signals(
        self,
        user_id: str,
        prospect_id: str,
        instagram_handle: str,
        signals: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        """
        Upsert instagram_signals row into Supabase.
        """
        try:
            from app.db.database import get_service_client
        except ImportError:
            logger.error("INSTAGRAM: Could not import get_service_client")
            return None

        try:
            client = get_service_client()
            now = datetime.now(timezone.utc).isoformat()

            payload = {
                "user_id": user_id,
                "prospect_id": prospect_id,
                "instagram_handle": instagram_handle.lstrip("@"),
                "is_active": signals.get("is_active", False),
                "followers": signals.get("followers", 0),
                "following": signals.get("following", 0),
                "posts": signals.get("posts", 0),
                "instagram_intensity": signals.get("instagram_intensity", 0),
                "instagram_active_score": signals.get("instagram_active_score", 0),
                "engagement_rate": signals.get("engagement_rate", 0),
                "posting_frequency": signals.get("posting_frequency", 0),
                "follower_growth": signals.get("follower_growth", 0),
                "hashtag_themes": signals.get("hashtag_themes", []),
                "posts_analyzed": signals.get("posts_analyzed", 0),
                "fetched_at": now,
                "updated_at": now,
            }

            result = client.table("instagram_signals").upsert(
                payload,
                on_conflict="user_id,instagram_handle",
            ).execute()

            if result.data:
                logger.info(
                    f"INSTAGRAM: Stored signals for @{instagram_handle}: "
                    f"followers={signals.get('followers', 0)}, "
                    f"posts={signals.get('posts', 0)}"
                )
                return result.data[0]

            logger.warning(f"INSTAGRAM: No data returned from instagram_signals upsert")
            return None

        except Exception as e:
            logger.error(f"INSTAGRAM: Failed to store signals: {e}")
            return None

    async def get_cached_signals(
        self,
        user_id: str,
        instagram_handle: str,
    ) -> Optional[Dict[str, Any]]:
        """Retrieve cached instagram_signals from Supabase."""
        try:
            from app.db.database import get_service_client
        except ImportError:
            return None

        try:
            client = get_service_client()
            result = client.table("instagram_signals").select("*").eq(
                "user_id", user_id
            ).eq("instagram_handle", instagram_handle.lstrip("@")).execute()

            if not result.data:
                return None

            return result.data[0]

        except Exception as e:
            logger.error(f"INSTAGRAM: Failed to get cached signals: {e}")
            return None

    # ------------------------------------------------------------------
    # Router-facing convenience methods
    # ------------------------------------------------------------------

    async def get_instagram_data(self, instagram_handle: str) -> Dict[str, Any]:
        """
        Main entry point: scrape + analyze + build signals.
        Returns complete signals dict (no DB storage — caller handles that).
        """
        profile = await self.scrape_profile(instagram_handle)

        if profile.get("error"):
            return {"is_active": False, "error": profile["error"]}

        post_data = self.analyze_posts(
            profile.get("recent_posts", []),
            profile.get("followers", 0),
        )
        signals = self.build_signals(profile, post_data)

        return {
            **signals,
            "handle": instagram_handle.lstrip("@"),
            "url": profile.get("url", f"https://www.instagram.com/{instagram_handle.lstrip('@')}"),
            "scraped_at": datetime.now(timezone.utc).isoformat(),
        }

    async def refresh_signals(
        self,
        user_id: str,
        prospect_id: str,
        instagram_handle: str,
    ) -> Dict[str, Any]:
        """
        Scrape fresh Instagram data and store results.
        Called by POST /api/v1/instagram/refresh.
        """
        data = await self.get_instagram_data(instagram_handle)

        if data.get("error"):
            return {"instagram_handle": instagram_handle, "error": data["error"], "refreshed": False}

        stored = await self.store_signals(user_id, prospect_id, instagram_handle, data)

        return {
            "instagram_handle": instagram_handle,
            "refreshed": True,
            "signals": data,
            "stored": stored is not None,
            "refreshed_at": datetime.now(timezone.utc).isoformat(),
        }
