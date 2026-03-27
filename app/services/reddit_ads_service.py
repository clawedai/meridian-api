"""
Module 19: Reddit Ads Intelligence
Queries Reddit's public JSON API to detect if a company is running
promoted/sponsored posts on Reddit.

Public API — no OAuth required for search. A REDDIT_CLIENT_ID
increases the rate limit (60 req/min unauthenticated vs 600/min with
a registered app). Service works without credentials.

Signal fields map to reddit_ad_signals table columns:
  user_id, company_domain, company_name, is_advertiser,
  ad_count, promoted_posts_found, first_seen_at, last_seen_at,
  fetched_at, raw_response (JSONB)
"""
import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx

from app.core.config import settings
from app.db.database import get_service_client

logger = logging.getLogger(__name__)

REDDIT_SEARCH_URL = "https://www.reddit.com/search.json"
REDDIT_USER_AGENT = "AlmanacBot/1.0 (by /u/almanac_ai)"
REDDIT_RATE_LIMIT = 60  # requests per minute (unauthenticated)


class RedditAdsServiceError(Exception):
    """Base exception for Reddit Ads service."""
    pass


class RedditAdsService:
    """
    Detects Reddit promoted/sponsored posts for a company.
    No OAuth required — uses Reddit's public JSON search API.
    """

    def __init__(self) -> None:
        self._http_client: Optional[httpx.AsyncClient] = None

    # ------------------------------------------------------------------
    # HTTP client
    # ------------------------------------------------------------------

    @property
    def _client(self) -> httpx.AsyncClient:
        if self._http_client is None:
            self._http_client = httpx.AsyncClient(timeout=30.0, follow_redirects=True)
        return self._http_client

    async def close(self) -> None:
        if self._http_client is not None:
            await self._http_client.aclose()
            self._http_client = None

    # ------------------------------------------------------------------
    # HTTP helpers
    # ------------------------------------------------------------------

    def _build_headers(self) -> Dict[str, str]:
        """Return request headers with optional Reddit app credentials."""
        headers = {"User-Agent": REDDIT_USER_AGENT}
        client_id = getattr(settings, "REDDIT_CLIENT_ID", None) or ""
        if client_id and not client_id.startswith("YOUR_"):
            headers["Authorization"] = f"Basic {client_id}"
        return headers

    async def _get_with_retry(self, url: str) -> Dict[str, Any]:
        """
        Execute GET with exponential backoff retry on 429 / 5xx.
        Max 3 retries (4 total attempts).
        """
        last_error: Optional[Exception] = None
        for attempt in range(4):
            try:
                response = await self._client.get(
                    url, headers=self._build_headers()
                )

                if response.status_code == 429:
                    retry_after = int(response.headers.get("Retry-After", 60))
                    wait = min(retry_after, 120)
                    logger.warning(f"REDDIT: rate-limited (429). Waiting {wait}s.")
                    await asyncio.sleep(wait)
                    continue

                if response.status_code >= 500:
                    delay = min(2 ** attempt + 1, 30)
                    logger.warning(
                        f"REDDIT: server error {response.status_code}. "
                        f"Retry in {delay}s (attempt {attempt + 1})."
                    )
                    await asyncio.sleep(delay)
                    continue

                response.raise_for_status()
                return response.json()

            except httpx.TimeoutException as e:
                last_error = e
                logger.warning(f"REDDIT: timeout (attempt {attempt + 1}): {e}")
                if attempt < 3:
                    await asyncio.sleep(2 ** attempt)
                continue
            except httpx.HTTPStatusError as e:
                last_error = e
                logger.warning(f"REDDIT: HTTP error (attempt {attempt + 1}): {e}")
                if attempt < 3:
                    await asyncio.sleep(2 ** attempt)
                continue
            except Exception as e:
                last_error = e
                logger.warning(f"REDDIT: unexpected error (attempt {attempt + 1}): {e}")
                if attempt < 3:
                    await asyncio.sleep(2 ** attempt)
                continue

        logger.error(f"REDDIT: all 4 attempts failed: {last_error}")
        return {}

    # ------------------------------------------------------------------
    # Core search
    # ------------------------------------------------------------------

    async def search_ads(
        self,
        company_name: str,
        company_domain: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Search Reddit for promoted posts mentioning the company.

        Detects promoted posts by checking:
          - link_flair_text == "Promoted"
          - link_flair_type == "promoted"
          - top_awarded_metadata fields (less reliable)

        Args:
            company_name: Company display name to search.
            company_domain: Optional domain for result filtering.

        Returns:
            Raw API response dict with posts and metadata.
        """
        logger.info(f"REDDIT: searching promoted posts for '{company_name}'")

        url = (
            f"{REDDIT_SEARCH_URL}"
            f"?q={company_name}&sort=top&t=month&limit=25&restrict_sr=0"
        )

        try:
            result = await self._get_with_retry(url)

            if not result:
                return self._empty_result(company_name, company_domain)

            raw_posts = result.get("data", {}).get("children", [])
            posts = [item.get("data", {}) for item in raw_posts]

            if company_domain and posts:
                domain_clean = self._clean_domain(company_domain)
                posts = [p for p in posts if self._post_matches_domain(p, domain_clean)]

            promoted_posts = [p for p in posts if self.analyze_post(p)]
            ad_count = len(promoted_posts)

            logger.info(
                f"REDDIT: search '{company_name}' -> "
                f"{len(posts)} posts, {ad_count} promoted"
            )

            return {
                "posts": posts,
                "promoted_posts": promoted_posts,
                "searched_company": company_name,
                "searched_domain": company_domain,
                "total_returned": len(posts),
                "promoted_count": ad_count,
                "searched_at": datetime.now(timezone.utc).isoformat(),
            }

        except Exception as e:
            logger.error(f"REDDIT: search_ads failed for '{company_name}': {e}")
            return self._empty_result(company_name, company_domain)

    def _empty_result(self, company_name: str, company_domain: Optional[str]) -> Dict[str, Any]:
        return {
            "posts": [],
            "promoted_posts": [],
            "searched_company": company_name,
            "searched_domain": company_domain,
            "total_returned": 0,
            "promoted_count": 0,
            "searched_at": datetime.now(timezone.utc).isoformat(),
        }

    def _clean_domain(self, domain: str) -> str:
        return (
            domain.lower()
            .replace("https://", "")
            .replace("http://", "")
            .replace("www.", "")
            .rstrip("/")
        )

    def _post_matches_domain(self, post: Dict[str, Any], domain: str) -> bool:
        text = (
            f"{post.get('selftext', '')} "
            f"{post.get('title', '')} "
            f"{post.get('url', '')} "
            f"{post.get('subreddit', '')}"
        ).lower()
        base = domain.split(".")[0]
        return base in text or domain in text

    # ------------------------------------------------------------------
    # Post analysis
    # ------------------------------------------------------------------

    def analyze_post(self, post: Dict[str, Any]) -> bool:
        """
        Determine if a Reddit post is a promoted/sponsored post.

        Checks (any match = promoted):
          - link_flair_text == "Promoted"
          - link_flair_type == "promoted"
          - Domain-level ads (some ads appear as link posts)
        """
        if not post:
            return False

        flair_text = str(post.get("link_flair_text", "") or "").strip()
        if flair_text.lower() == "promoted":
            return True

        flair_type = str(post.get("link_flair_type", "") or "").strip()
        if flair_type.lower() == "promoted":
            return True

        # Some promoted posts show as regular link posts but have ad indicators
        is_self_post = not bool(post.get("url"))
        has_title_ad_indicators = any(
            kw in (post.get("title", "") or "").lower()
            for kw in ["sponsored", "ad", "sponsored by", "paid promotion"]
        )
        if is_self_post and has_title_ad_indicators:
            return True

        return False

    # ------------------------------------------------------------------
    # Signal extraction
    # ------------------------------------------------------------------

    def build_ad_signals(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Extract scoring signals from Reddit search response.

        Returns signal dict for reddit_ad_signals table.
        """
        promoted_posts = data.get("promoted_posts", [])
        posts = data.get("posts", [])

        is_advertiser = len(promoted_posts) > 0
        ad_count = len(promoted_posts)
        total_posts = len(posts)

        # Intensity: 0-10 based on how many posts are promoted
        if ad_count == 0:
            intensity = 0
        elif ad_count >= 5:
            intensity = 10
        elif ad_count >= 3:
            intensity = 7
        elif ad_count >= 2:
            intensity = 5
        else:
            intensity = 3

        # Recency: when was the most recent promoted post
        recency = self._calculate_recency(promoted_posts)

        # Subreddits where promoted posts appeared
        subreddits = list({
            p.get("subreddit", "")
            for p in promoted_posts
            if p.get("subreddit")
        })

        return {
            "is_advertiser": is_advertiser,
            "ad_count": ad_count,
            "promoted_posts_found": ad_count,
            "total_posts_found": total_posts,
            "intensity": intensity,
            "recency": recency,
            "promoted_subreddits": subreddits,
            "sample_promoted": [
                {
                    "title": p.get("title", ""),
                    "subreddit": p.get("subreddit", ""),
                    "score": p.get("score", 0),
                    "num_comments": p.get("num_comments", 0),
                    "url": p.get("url", ""),
                    "permalink": f"https://reddit.com{p.get('permalink', '')}",
                }
                for p in promoted_posts[:3]
            ],
            "built_at": datetime.now(timezone.utc).isoformat(),
        }

    def _calculate_recency(self, promoted_posts: List[Dict[str, Any]]) -> int:
        """Score 0-5: how recently was the most recent promoted post."""
        if not promoted_posts:
            return 0

        now = datetime.now(timezone.utc)
        latest: Optional[datetime] = None

        for post in promoted_posts:
            created_utc = post.get("created_utc")
            if created_utc:
                try:
                    dt = datetime.fromtimestamp(created_utc, tz=timezone.utc)
                    if latest is None or dt > latest:
                        latest = dt
                except Exception:
                    continue

        if latest is None:
            return 2

        days_ago = (now - latest).days
        if days_ago <= 7:
            return 5
        elif days_ago <= 14:
            return 4
        elif days_ago <= 30:
            return 3
        elif days_ago <= 60:
            return 2
        return 1

    # ------------------------------------------------------------------
    # Supabase storage / retrieval
    # ------------------------------------------------------------------

    async def store_signals(
        self,
        user_id: str,
        company_domain: str,
        company_name: str,
        signals: Dict[str, Any],
        raw_data: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Upsert reddit_ad_signals row for a company + user.

        Table columns:
          user_id, company_domain, company_name, is_advertiser,
          ad_count, promoted_posts_found, intensity, recency,
          promoted_subreddits, sample_promoted (JSONB),
          first_seen_at, last_seen_at, fetched_at, raw_response (JSONB)
        """
        logger.info(
            f"REDDIT: storing reddit_ad_signals: "
            f"domain={company_domain} user={user_id}"
        )

        try:
            client = get_service_client()
            now = datetime.now(timezone.utc).isoformat()

            # Look up prospect_id for this company
            prospect_result = (
                client.table("prospects")
                .select("id")
                .eq("user_id", user_id)
                .eq("company_domain", company_domain)
                .limit(1)
                .execute()
            )
            prospect_id = (
                prospect_result.data[0]["id"]
                if prospect_result.data
                else None
            )

            promoted_posts = (raw_data or {}).get("promoted_posts", [])
            timestamps = [
                p.get("created_utc", 0)
                for p in promoted_posts
                if p.get("created_utc")
            ]
            timestamps_sorted = sorted(timestamps)

            first_seen = None
            last_seen = None
            if timestamps_sorted:
                first_seen = datetime.fromtimestamp(
                    timestamps_sorted[0], tz=timezone.utc
                ).isoformat()
                last_seen = datetime.fromtimestamp(
                    timestamps_sorted[-1], tz=timezone.utc
                ).isoformat()

            payload = {
                "user_id": user_id,
                "prospect_id": prospect_id,
                "company_domain": company_domain,
                "company_name": company_name,
                "is_advertiser": signals.get("is_advertiser", False),
                "ad_count": signals.get("ad_count", 0),
                "promoted_posts_found": signals.get("promoted_posts_found", 0),
                "promoted_subreddits": signals.get("promoted_subreddits", []),
                "sample_promoted": signals.get("sample_promoted", []),
                "reddit_intensity": signals.get("intensity", 0),
                "first_seen_at": first_seen,
                "last_seen_at": last_seen,
                "fetched_at": now,
                "raw_response": raw_data,
                "updated_at": now,
            }

            result = client.table("reddit_ad_signals").upsert(
                payload,
                on_conflict="user_id,company_domain",
            ).execute()

            if not result.data:
                logger.warning("REDDIT: no data returned from reddit_ad_signals upsert")
                return None

            record = result.data[0]
            logger.info(
                f"REDDIT: stored reddit_ad_signals: "
                f"id={record.get('id')} domain={company_domain} "
                f"ad_count={signals.get('ad_count', 0)}"
            )
            return record

        except Exception as e:
            logger.error(f"REDDIT: failed to store reddit_ad_signals: {e}")
            return None

    async def get_cached_signals(
        self,
        user_id: str,
        company_domain: str,
    ) -> Optional[Dict[str, Any]]:
        """
        Retrieve cached reddit_ad_signals from Supabase.
        Returns the row with enriched signal fields.
        """
        try:
            client = get_service_client()
            result = client.table("reddit_ad_signals").select("*").eq(
                "user_id", user_id
            ).eq("company_domain", company_domain).execute()

            if not result.data:
                logger.debug(
                    f"REDDIT: no cached reddit_ad_signals for {company_domain}"
                )
                return None

            record = result.data[0]
            logger.debug(f"REDDIT: retrieved cached signals for {company_domain}")
            return record

        except Exception as e:
            logger.error(f"REDDIT: failed to get cached signals: {e}")
            return None

    # ------------------------------------------------------------------
    # Router-facing convenience methods
    # ------------------------------------------------------------------

    async def refresh_signals(
        self,
        user_id: str,
        company_domain: str,
        company_name: str,
    ) -> Dict[str, Any]:
        """
        Force-refresh Reddit ad signals from the public API.
        Called by POST /api/v1/reddit-ads/refresh.
        """
        logger.info(
            f"REDDIT: refreshing ad signals for '{company_name}' "
            f"(domain={company_domain})"
        )
        search_results = await self.search_ads(company_name, company_domain)
        signals = self.build_ad_signals(search_results)
        await self.store_signals(
            user_id, company_domain, company_name, signals, search_results
        )

        return {
            "company_domain": company_domain,
            "company_name": company_name,
            "signals": signals,
            "raw_data": search_results,
            "refreshed_at": datetime.now(timezone.utc).isoformat(),
        }
