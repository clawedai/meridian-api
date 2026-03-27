"""
Module 20: Reddit Organic Intelligence
Queries Reddit's public JSON API to find organic brand mentions,
measure sentiment, and track engagement across subreddits.

Public API — no OAuth required. Uses Reddit's search.json and
domain endpoints.

Signal fields map to reddit_organic_signals table columns:
  user_id, company_domain, company_name, mention_count,
  sentiment_score, sentiment_label, positive_mentions,
  negative_mentions, subreddit_count, total_upvotes,
  total_comments, last_post_at, reddit_intensity,
  reddit_organic_active, fetched_at
"""
import asyncio
import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx

from app.core.config import settings
from app.db.database import get_service_client

logger = logging.getLogger(__name__)

REDDIT_SEARCH_URL = "https://www.reddit.com/search.json"
REDDIT_DOMAIN_URL = "https://www.reddit.com/domain/{domain}/top.json"
REDDIT_USER_AGENT = "AlmanacBot/1.0 (by /u/almanac_ai)"

POSITIVE_KEYWORDS = [
    "love", "great", "amazing", "best", "awesome", "helpful",
    "recommend", "fantastic", "excellent", "wonderful", "perfect",
    "impressive", "outstanding", "useful", "easy", "intuitive",
    "smooth", "fast", "reliable", "solid", "polished",
]
NEGATIVE_KEYWORDS = [
    "hate", "terrible", "worst", "awful", "scam", "broken",
    "slow", "bug", "crash", "fail", "sucks", "trash",
    "horrible", "garbage", "useless", "disappointing", "frustrating",
    "annoying", "unreliable", "overpriced", "unresponsive", "spam",
]


class RedditOrganicServiceError(Exception):
    """Base exception for Reddit Organic service."""
    pass


class RedditOrganicService:
    """
    Tracks organic Reddit mentions and sentiment for a company.
    No OAuth required — uses Reddit's public JSON API.
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
                    logger.warning(f"REDDIT: organic rate-limited (429). Waiting {wait}s.")
                    await asyncio.sleep(wait)
                    continue

                if response.status_code >= 500:
                    delay = min(2 ** attempt + 1, 30)
                    logger.warning(
                        f"REDDIT: organic server error {response.status_code}. "
                        f"Retry in {delay}s (attempt {attempt + 1})."
                    )
                    await asyncio.sleep(delay)
                    continue

                response.raise_for_status()
                return response.json()

            except httpx.TimeoutException as e:
                last_error = e
                logger.warning(f"REDDIT: organic timeout (attempt {attempt + 1}): {e}")
                if attempt < 3:
                    await asyncio.sleep(2 ** attempt)
                continue
            except httpx.HTTPStatusError as e:
                last_error = e
                logger.warning(f"REDDIT: organic HTTP error (attempt {attempt + 1}): {e}")
                if attempt < 3:
                    await asyncio.sleep(2 ** attempt)
                continue
            except Exception as e:
                last_error = e
                logger.warning(f"REDDIT: organic error (attempt {attempt + 1}): {e}")
                if attempt < 3:
                    await asyncio.sleep(2 ** attempt)
                continue

        logger.error(f"REDDIT: organic all 4 attempts failed: {last_error}")
        return {}

    # ------------------------------------------------------------------
    # Core search
    # ------------------------------------------------------------------

    async def search_mentions(
        self,
        company_name: str,
        company_domain: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Search Reddit for organic posts mentioning the company.

        Runs two queries:
          1. Keyword search on company_name (top, last month, 50 posts)
          2. Domain search on company_domain (if provided)

        Combines and deduplicates results.

        Args:
            company_name: Company display name to search.
            company_domain: Domain to search via /domain/ endpoint.

        Returns:
            Combined posts and metadata.
        """
        logger.info(f"REDDIT: searching organic mentions for '{company_name}'")

        search_url = (
            f"{REDDIT_SEARCH_URL}"
            f"?q={company_name}&sort=top&t=month&limit=50&restrict_sr=0"
        )

        try:
            search_result = await self._get_with_retry(search_url)
        except Exception as e:
            logger.error(f"REDDIT: search_mentions failed for '{company_name}': {e}")
            search_result = {}

        search_posts = self._extract_posts(search_result)

        # Supplement with domain search if domain is available
        domain_posts: List[Dict[str, Any]] = []
        if company_domain:
            domain_clean = self._clean_domain(company_domain)
            domain_url = (
                f"https://www.reddit.com/domain/{domain_clean}/top.json"
                f"?t=month&limit=25"
            )
            try:
                domain_result = await self._get_with_retry(domain_url)
                domain_posts = self._extract_posts(domain_result)
                logger.info(
                    f"REDDIT: domain search for '{domain_clean}' -> "
                    f"{len(domain_posts)} posts"
                )
            except Exception as e:
                logger.warning(f"REDDIT: domain search failed for '{domain_clean}': {e}")

        # Combine and deduplicate by post ID
        all_posts = self._deduplicate_posts(search_posts + domain_posts)

        # Filter out promoted posts
        organic_posts = [
            p for p in all_posts
            if not self._is_promoted_post(p)
        ]

        logger.info(
            f"REDDIT: organic search '{company_name}' -> "
            f"{len(all_posts)} total, {len(organic_posts)} organic"
        )

        return {
            "posts": organic_posts,
            "total_found": len(all_posts),
            "organic_count": len(organic_posts),
            "searched_company": company_name,
            "searched_domain": company_domain,
            "searched_at": datetime.now(timezone.utc).isoformat(),
        }

    def _empty_result(self, company_name: str, company_domain: Optional[str]) -> Dict[str, Any]:
        return {
            "posts": [],
            "total_found": 0,
            "organic_count": 0,
            "searched_company": company_name,
            "searched_domain": company_domain,
            "searched_at": datetime.now(timezone.utc).isoformat(),
        }

    def _extract_posts(self, result: Dict[str, Any]) -> List[Dict[str, Any]]:
        data = result.get("data", {})
        if isinstance(data, dict):
            children = data.get("children", [])
            return [item.get("data", {}) for item in children if isinstance(item, dict)]
        return []

    def _deduplicate_posts(self, posts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        seen_ids: set = set()
        unique: List[Dict[str, Any]] = []
        for post in posts:
            post_id = post.get("id", "")
            if post_id and post_id not in seen_ids:
                seen_ids.add(post_id)
                unique.append(post)
        return unique

    def _clean_domain(self, domain: str) -> str:
        return (
            domain.lower()
            .replace("https://", "")
            .replace("http://", "")
            .replace("www.", "")
            .rstrip("/")
        )

    def _is_promoted_post(self, post: Dict[str, Any]) -> bool:
        flair_text = str(post.get("link_flair_text", "") or "").strip()
        flair_type = str(post.get("link_flair_type", "") or "").strip()
        return (
            flair_text.lower() == "promoted"
            or flair_type.lower() == "promoted"
        )

    # ------------------------------------------------------------------
    # Sentiment analysis
    # ------------------------------------------------------------------

    def analyze_sentiment(self, post: Dict[str, Any]) -> Dict[str, Any]:
        """
        Perform keyword-based sentiment analysis on a Reddit post.

        Positive / negative keyword counts are normalized against total
        word count and clamped to [-1.0, 1.0].

        Returns:
            sentiment_score: -1.0 to 1.0
            sentiment_label: 'positive' | 'negative' | 'neutral'
        """
        title = (post.get("title", "") or "").lower()
        body = (post.get("selftext", "") or "").lower()
        combined = f"{title} {body}"

        words = re.findall(r"\b\w+\b", combined)
        total_words = max(len(words), 1)
        word_set = set(words)

        positive_count = sum(1 for kw in POSITIVE_KEYWORDS if kw in word_set)
        negative_count = sum(1 for kw in NEGATIVE_KEYWORDS if kw in word_set)

        # Raw score normalized to [-1, 1]
        raw_score = (positive_count - negative_count) / total_words
        score = max(-1.0, min(1.0, raw_score * 10))  # amplify small differences

        # Label
        if score > 0.1:
            label = "positive"
        elif score < -0.1:
            label = "negative"
        else:
            label = "neutral"

        return {
            "sentiment_score": round(score, 4),
            "sentiment_label": label,
            "positive_count": positive_count,
            "negative_count": negative_count,
        }

    # ------------------------------------------------------------------
    # Signal extraction
    # ------------------------------------------------------------------

    def build_organic_signals(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Extract scoring signals from Reddit organic search response.

        Returns signal dict for reddit_organic_signals table.
        """
        posts = data.get("posts", [])
        if not posts:
            return self._empty_signals()

        # Per-post sentiment
        sentiments = [self.analyze_sentiment(p) for p in posts]
        positive_mentions = sum(1 for s in sentiments if s["sentiment_label"] == "positive")
        negative_mentions = sum(1 for s in sentiments if s["sentiment_label"] == "negative")
        avg_sentiment = sum(s["sentiment_score"] for s in sentiments) / len(sentiments)

        # Sentiment label
        if avg_sentiment > 0.1:
            sentiment_label = "positive"
        elif avg_sentiment < -0.1:
            sentiment_label = "negative"
        else:
            sentiment_label = "neutral"

        # Engagement metrics
        total_upvotes = sum(p.get("score", 0) for p in posts)
        total_comments = sum(p.get("num_comments", 0) for p in posts)

        # Unique subreddits
        subreddits = list({
            p.get("subreddit", "")
            for p in posts
            if p.get("subreddit")
        })

        # Most recent post timestamp
        last_post_at = self._latest_post_timestamp(posts)

        # Intensity: 0-20 based on mention volume and engagement
        reddit_intensity = self._calculate_intensity(
            mention_count=len(posts),
            total_upvotes=total_upvotes,
            positive_count=positive_mentions,
        )

        return {
            "mention_count": len(posts),
            "sentiment_score": round(avg_sentiment, 4),
            "sentiment_label": sentiment_label,
            "positive_mentions": positive_mentions,
            "negative_mentions": negative_mentions,
            "subreddit_count": len(subreddits),
            "total_upvotes": total_upvotes,
            "total_comments": total_comments,
            "last_post_at": last_post_at,
            "reddit_intensity": reddit_intensity,
            "reddit_organic_active": len(posts) > 0,
            "top_subreddits": subreddits[:5],
            "sample_posts": [
                {
                    "title": p.get("title", ""),
                    "subreddit": p.get("subreddit", ""),
                    "score": p.get("score", 0),
                    "num_comments": p.get("num_comments", 0),
                    "permalink": f"https://reddit.com{p.get('permalink', '')}",
                    "created_utc": p.get("created_utc"),
                }
                for p in posts[:5]
            ],
            "built_at": datetime.now(timezone.utc).isoformat(),
        }

    def _empty_signals(self) -> Dict[str, Any]:
        return {
            "mention_count": 0,
            "sentiment_score": 0.0,
            "sentiment_label": "neutral",
            "positive_mentions": 0,
            "negative_mentions": 0,
            "subreddit_count": 0,
            "total_upvotes": 0,
            "total_comments": 0,
            "last_post_at": None,
            "reddit_intensity": 0,
            "reddit_organic_active": False,
            "top_subreddits": [],
            "sample_posts": [],
            "built_at": datetime.now(timezone.utc).isoformat(),
        }

    def _latest_post_timestamp(self, posts: List[Dict[str, Any]]) -> Optional[str]:
        latest_utc = max(
            (p.get("created_utc", 0) for p in posts if p.get("created_utc")),
            default=None,
        )
        if latest_utc:
            return datetime.fromtimestamp(latest_utc, tz=timezone.utc).isoformat()
        return None

    def _calculate_intensity(
        self,
        mention_count: int,
        total_upvotes: int,
        positive_count: int,
    ) -> int:
        """
        Score 0-20 based on mention volume, upvotes, and positive mentions.
        Higher score = more active organic Reddit presence.
        """
        score = 0

        # Volume score (0-8)
        if mention_count >= 20:
            score += 8
        elif mention_count >= 10:
            score += 5
        elif mention_count >= 5:
            score += 3
        elif mention_count >= 1:
            score += 1

        # Engagement score (0-7)
        if total_upvotes >= 5000:
            score += 7
        elif total_upvotes >= 1000:
            score += 5
        elif total_upvotes >= 100:
            score += 3
        elif total_upvotes >= 10:
            score += 1

        # Sentiment score (0-5)
        score += min(positive_count, 5)

        return min(score, 20)

    # ------------------------------------------------------------------
    # Supabase storage / retrieval
    # ------------------------------------------------------------------

    async def store_signals(
        self,
        user_id: str,
        company_domain: str,
        company_name: str,
        signals: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        """
        Upsert reddit_organic_signals row for a company + user.

        Table columns:
          user_id, company_domain, company_name, mention_count,
          sentiment_score, sentiment_label, positive_mentions,
          negative_mentions, subreddit_count, total_upvotes,
          total_comments, last_post_at, reddit_intensity,
          reddit_organic_active, top_subreddits (JSONB),
          sample_posts (JSONB), fetched_at
        """
        logger.info(
            f"REDDIT: storing reddit_organic_signals: "
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

            payload = {
                "user_id": user_id,
                "prospect_id": prospect_id,
                "company_domain": company_domain,
                "company_name": company_name,
                "mention_count": signals.get("mention_count", 0),
                "sentiment_score": signals.get("sentiment_score", 0.0),
                "sentiment_label": signals.get("sentiment_label", "neutral"),
                "positive_mentions": signals.get("positive_mentions", 0),
                "negative_mentions": signals.get("negative_mentions", 0),
                "subreddit_count": signals.get("subreddit_count", 0),
                "total_upvotes": signals.get("total_upvotes", 0),
                "total_comments": signals.get("total_comments", 0),
                "last_post_at": signals.get("last_post_at"),
                "reddit_intensity": signals.get("reddit_intensity", 0),
                "reddit_organic_active": signals.get("reddit_organic_active", False),
                "top_subreddits": signals.get("top_subreddits", []),
                "sample_posts": signals.get("sample_posts", []),
                "fetched_at": now,
                "updated_at": now,
            }

            result = client.table("reddit_organic_signals").upsert(
                payload,
                on_conflict="user_id,company_domain",
            ).execute()

            if not result.data:
                logger.warning("REDDIT: no data returned from reddit_organic_signals upsert")
                return None

            record = result.data[0]
            logger.info(
                f"REDDIT: stored reddit_organic_signals: "
                f"id={record.get('id')} domain={company_domain} "
                f"mentions={signals.get('mention_count', 0)} "
                f"sentiment={signals.get('sentiment_label', 'unknown')}"
            )
            return record

        except Exception as e:
            logger.error(f"REDDIT: failed to store reddit_organic_signals: {e}")
            return None

    async def get_cached_signals(
        self,
        user_id: str,
        company_domain: str,
    ) -> Optional[Dict[str, Any]]:
        """
        Retrieve cached reddit_organic_signals from Supabase.
        Returns the row with signal fields.
        """
        try:
            client = get_service_client()
            result = client.table("reddit_organic_signals").select("*").eq(
                "user_id", user_id
            ).eq("company_domain", company_domain).execute()

            if not result.data:
                logger.debug(
                    f"REDDIT: no cached reddit_organic_signals for {company_domain}"
                )
                return None

            record = result.data[0]
            logger.debug(f"REDDIT: retrieved cached organic signals for {company_domain}")
            return record

        except Exception as e:
            logger.error(f"REDDIT: failed to get cached organic signals: {e}")
            return None
