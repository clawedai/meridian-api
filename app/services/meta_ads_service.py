"""
Module 18: Meta Ads Library Intelligence
Queries Meta's Ads Library API to find ads run by any company.
Public data — no OAuth needed, uses App Access Token.
"""
import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx

from app.core.config import settings
from app.db.database import get_service_client

logger = logging.getLogger(__name__)

META_API_BASE = "https://graph.facebook.com/v19.0"
TOKEN_ENDPOINT = f"{META_API_BASE}/oauth/access_token"
ADS_ARCHIVE_ENDPOINT = f"{META_API_BASE}/ads_archive"

ADS_ARCHIVE_FIELDS = (
    "id,ad_creative_body,ad_creative_link_caption,"
    "ad_delivery_start,ad_delivery_stop,page_id,page_name,"
    "ad_snapshot_url,delivery_by_region,estimated_audience_size,"
    "ad_status,ad_type,funding_entity,spend"
)

LEAD_GEN_KEYWORDS = [
    "sign up", "get a quote", "download", "register", "book a demo",
    "request access", "get started", "start free", "try free",
    "schedule a call", "contact us", "get a demo", "free trial",
    "no credit card", "start now", "join waitlist",
]

BRAND_KEYWORDS = [
    "proud", "introducing", "announcing", "mission",
    "values", "sustainability", "community", "thank you",
    "brand", "about us", "our story", "partnership",
]

CONVERSION_KEYWORDS = [
    "shop now", "buy now", "limited time", "offer ends",
    "discount", "sale", "deal", "order", "checkout",
    "subscribe", "upgrade", "compare", "pricing",
]


class MetaAdsServiceError(Exception):
    """Base exception for Meta Ads service."""
    pass


class MetaAdsConfigError(MetaAdsServiceError):
    """Raised when META_APP_ID or META_APP_SECRET is missing."""
    pass


class MetaAdsService:
    """
    Queries Meta's Ads Library API for advertising signals about companies.
    Public API — no user OAuth needed, uses App Access Token.

    Signal fields map to meta_ad_signals table columns:
      is_advertiser, ad_count, meta_ad_intensity, meta_ad_recency,
      meta_ad_lead_gen, meta_ad_active, fetched_at, first_seen_at, last_seen_at

    Individual ads are stored in the meta_ads table with full creative detail.
    """

    def __init__(self) -> None:
        self._app_access_token: Optional[str] = None
        self._token_expires_at: Optional[datetime] = None
        self._http_client: Optional[httpx.AsyncClient] = None
        self._ensure_credentials()

    # ------------------------------------------------------------------
    # Credentials
    # ------------------------------------------------------------------

    def _ensure_credentials(self) -> None:
        """Validate that META_APP_ID and META_APP_SECRET are configured."""
        app_id = getattr(settings, "META_APP_ID", None) or ""
        app_secret = getattr(settings, "META_APP_SECRET", None) or ""
        if not app_id or app_id.startswith("YOUR_") or not app_secret or app_secret.startswith("YOUR_"):
            raise MetaAdsConfigError(
                "META_APP_ID and META_APP_SECRET must be set in .env as a real "
                "Meta App ID and App Secret from https://developers.facebook.com. "
                "Create an app, enable the Marketing API, and set both values in .env."
            )

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
    # Token management
    # ------------------------------------------------------------------

    async def get_app_access_token(self) -> str:
        """
        Fetch and cache a Meta App Access Token.
        Tokens expire in ~3600s; cached with 5-min buffer before expiry.
        """
        now = datetime.now(timezone.utc)
        if self._app_access_token and self._token_expires_at:
            if now < self._token_expires_at:
                return self._app_access_token

        app_id = getattr(settings, "META_APP_ID", "")
        app_secret = getattr(settings, "META_APP_SECRET", "")

        logger.info(f"Fetching Meta App Access Token for app_id={app_id[:8]}...")

        try:
            response = await self._client.get(
                TOKEN_ENDPOINT,
                params={
                    "client_id": app_id,
                    "client_secret": app_secret,
                    "grant_type": "client_credentials",
                },
            )
            response.raise_for_status()
            data = response.json()
            token = data["access_token"]
            expires_in = data.get("expires_in", 3600)

            self._app_access_token = token
            self._token_expires_at = datetime.fromtimestamp(
                now.timestamp() + expires_in - 300, tz=timezone.utc
            )

            logger.info(f"Meta App Access Token cached, expires in {expires_in}s")
            return token

        except httpx.HTTPStatusError as e:
            logger.error(f"Meta token request failed: {e.response.status_code} {e.response.text}")
            raise MetaAdsServiceError(f"Failed to get Meta access token: {e}") from e
        except Exception as e:
            logger.error(f"Unexpected error getting Meta access token: {e}")
            raise MetaAdsServiceError(f"Unexpected error getting Meta access token: {e}") from e

    # ------------------------------------------------------------------
    # HTTP helpers
    # ------------------------------------------------------------------

    async def _get_with_retry(
        self, url: str, params: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Execute GET with exponential backoff retry on 429 / 5xx.
        Max 3 retries (4 total attempts).
        """
        last_error: Optional[Exception] = None
        for attempt in range(4):
            try:
                token = await self.get_app_access_token()
                merged = {**params, "access_token": token}

                response = await self._client.get(url, params=merged)

                if response.status_code == 429:
                    retry_after = int(response.headers.get("Retry-After", 60))
                    wait = min(retry_after, 120)
                    logger.warning(f"Meta API rate-limited (429). Waiting {wait}s.")
                    await asyncio.sleep(wait)
                    continue

                if response.status_code >= 500:
                    delay = min(2 ** attempt + 1, 30)
                    logger.warning(
                        f"Meta API 5xx ({response.status_code}). "
                        f"Retry in {delay}s (attempt {attempt + 1})."
                    )
                    await asyncio.sleep(delay)
                    continue

                response.raise_for_status()
                return response.json()

            except httpx.TimeoutException as e:
                last_error = e
                logger.warning(f"Meta API timeout (attempt {attempt + 1}): {e}")
                if attempt < 3:
                    await asyncio.sleep(2 ** attempt)
                continue
            except httpx.HTTPStatusError as e:
                last_error = e
                logger.warning(f"Meta API HTTP error (attempt {attempt + 1}): {e}")
                if attempt < 3:
                    await asyncio.sleep(2 ** attempt)
                continue
            except Exception as e:
                last_error = e
                logger.warning(f"Meta API error (attempt {attempt + 1}): {e}")
                if attempt < 3:
                    await asyncio.sleep(2 ** attempt)
                continue

        logger.error(f"Meta API failed after 4 attempts: {last_error}")
        return {}

    # ------------------------------------------------------------------
    # API calls
    # ------------------------------------------------------------------

    async def search_ads(
        self,
        company_name: str,
        company_domain: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Search Meta Ads Library for ads run by a company.

        Args:
            company_name: Company display name to search.
            company_domain: Optional domain to filter results.

        Returns:
            Raw API response dict with 'data' list of ads.
        """
        logger.info(f"Searching Meta Ads Library for: {company_name}")

        params = {
            "fields": ADS_ARCHIVE_FIELDS,
            "search_terms": company_name,
            "ad_type": "ALL",
            "ad_reached_countries": ["US"],
            "limit": 25,
        }

        try:
            result = await self._get_with_retry(ADS_ARCHIVE_ENDPOINT, params)

            if not result:
                return {"data": [], "searched_company": company_name}

            ads = result.get("data", [])

            if company_domain and ads:
                domain_clean = (
                    company_domain.lower()
                    .replace("https://", "")
                    .replace("http://", "")
                    .replace("www.", "")
                )
                ads = [ad for ad in ads if self._ad_matches_domain(ad, domain_clean)]

            ad_count = len(ads)
            logger.info(f"Meta Ads search '{company_name}': {ad_count} ads returned")

            return {
                "data": ads,
                "searched_company": company_name,
                "searched_domain": company_domain,
                "total_returned": ad_count,
                "searched_at": datetime.now(timezone.utc).isoformat(),
            }

        except MetaAdsServiceError:
            return {"data": [], "searched_company": company_name}
        except Exception as e:
            logger.error(f"Unexpected error in search_ads for {company_name}: {e}")
            return {"data": [], "searched_company": company_name}

    def _ad_matches_domain(self, ad: Dict[str, Any], domain: str) -> bool:
        text = (
            f"{ad.get('ad_creative_body', '')} "
            f"{ad.get('ad_creative_link_caption', '')} "
            f"{ad.get('page_name', '')}"
        ).lower()
        return domain.split(".")[0] in text

    async def fetch_ad_details(self, ad_ids: List[str]) -> List[Dict[str, Any]]:
        """
        Fetch full details for specific ad IDs.

        Args:
            ad_ids: List of Meta ad IDs.

        Returns:
            List of ad detail dicts.
        """
        if not ad_ids:
            return []

        logger.info(f"Fetching details for {len(ad_ids)} Meta ads")

        try:
            token = await self.get_app_access_token()
            params = {"fields": ADS_ARCHIVE_FIELDS, "access_token": token}
            results: List[Dict[str, Any]] = []

            for ad_id in ad_ids:
                try:
                    response = await self._client.get(
                        f"{META_API_BASE}/{ad_id}", params=params
                    )
                    response.raise_for_status()
                    data = response.json()
                    if data:
                        results.append(data)
                except Exception as e:
                    logger.warning(f"Failed to fetch ad {ad_id}: {e}")
                    continue

            return results

        except Exception as e:
            logger.error(f"Error fetching ad details: {e}")
            return []

    # ------------------------------------------------------------------
    # Signal extraction
    # ------------------------------------------------------------------

    def build_signals(self, search_results: Dict[str, Any]) -> Dict[str, Any]:
        """
        Extract scoring signals from raw Ads Library API response.

        Signal scores (max 40 pts combined):
          - intensity: 0-25 based on ad_count and campaign duration spread
          - recency: 0-15 based on how recently ads were first detected

        Also returns meta_ad_active (bool) and meta_ad_lead_gen (bool).
        """
        ads = search_results.get("data", [])
        if not ads:
            return self._empty_signals()

        active_ads = [ad for ad in ads if ad.get("ad_status") == "ACTIVE"]

        is_advertiser = len(active_ads) > 0
        ad_count = len(active_ads)
        is_lead_gen = any(
            self._detect_lead_gen(ad.get("ad_creative_body", ""))
            for ad in active_ads
        )

        intensity = self._calculate_intensity(active_ads)
        recency = self._calculate_recency(active_ads)
        top_type = self._primary_ad_type(active_ads)
        avg_spend = self._avg_spend(active_ads)
        funding_entities = list({
            ad.get("funding_entity", "")
            for ad in active_ads if ad.get("funding_entity")
        })

        return {
            "is_advertiser": is_advertiser,
            "ad_count": ad_count,
            "is_lead_gen": is_lead_gen,
            "intensity": intensity,
            "recency": recency,
            "ad_type": top_type,
            "avg_spend_min": avg_spend,
            "funding_entities": funding_entities,
            "total_ads_found": len(ads),
            "meta_ad_active": is_advertiser,
            "meta_ad_lead_gen": is_lead_gen,
            "meta_ad_intensity": intensity,
            "meta_ad_recency": recency,
            "sample_ads": [
                {
                    "id": ad.get("id"),
                    "page_name": ad.get("page_name"),
                    "body_preview": (ad.get("ad_creative_body", "") or "")[:120],
                    "ad_status": ad.get("ad_status"),
                    "delivery_start": ad.get("ad_delivery_start"),
                    "snapshot_url": ad.get("ad_snapshot_url"),
                }
                for ad in active_ads[:3]
            ],
            "built_at": datetime.now(timezone.utc).isoformat(),
        }

    def _empty_signals(self) -> Dict[str, Any]:
        return {
            "is_advertiser": False,
            "ad_count": 0,
            "is_lead_gen": False,
            "intensity": 0,
            "recency": 0,
            "ad_type": "NONE",
            "avg_spend_min": 0.0,
            "funding_entities": [],
            "total_ads_found": 0,
            "meta_ad_active": False,
            "meta_ad_lead_gen": False,
            "meta_ad_intensity": 0,
            "meta_ad_recency": 0,
            "sample_ads": [],
            "built_at": datetime.now(timezone.utc).isoformat(),
        }

    def _detect_lead_gen(self, body: str) -> bool:
        if not body:
            return False
        return any(kw in body.lower() for kw in LEAD_GEN_KEYWORDS)

    def analyze_ad_creative(self, body: str) -> Dict[str, Any]:
        """
        Classify an ad's creative type using keyword heuristics.

        Returns:
            ad_type: 'lead_gen' | 'conversion' | 'brand_awareness' | 'unknown'
            confidence: 0.0 - 1.0
        """
        if not body:
            return {"ad_type": "unknown", "confidence": 0.0}

        body_lower = body.lower()
        scores: Dict[str, float] = {
            "lead_gen": 0.0,
            "conversion": 0.0,
            "brand_awareness": 0.0,
        }

        for kw in LEAD_GEN_KEYWORDS:
            if kw in body_lower:
                scores["lead_gen"] = min(scores["lead_gen"] + 0.15, 1.0)
        for kw in CONVERSION_KEYWORDS:
            if kw in body_lower:
                scores["conversion"] = min(scores["conversion"] + 0.15, 1.0)
        for kw in BRAND_KEYWORDS:
            if kw in body_lower:
                scores["brand_awareness"] = min(scores["brand_awareness"] + 0.15, 1.0)

        top_type = max(scores, key=scores.__getitem__)
        confidence = round(min(scores[top_type], 1.0), 2)

        if confidence < 0.1:
            return {"ad_type": "unknown", "confidence": 0.0}
        return {"ad_type": top_type, "confidence": confidence}

    def _primary_ad_type(self, active_ads: List[Dict[str, Any]]) -> str:
        if not active_ads:
            return "UNKNOWN"
        types = [ad.get("ad_type", "UNKNOWN") for ad in active_ads]
        return max(set(types), key=types.count) if types else "UNKNOWN"

    def _calculate_intensity(self, active_ads: List[Dict[str, Any]]) -> int:
        """Score 0-25: more ads + longer campaign duration = higher intensity."""
        count = len(active_ads)
        if count == 0:
            return 0

        if count >= 20:
            base = 15
        elif count >= 10:
            base = 10
        elif count >= 5:
            base = 6
        else:
            base = 3

        delivery_dates = [
            ad.get("ad_delivery_start", "")
            for ad in active_ads
            if ad.get("ad_delivery_start")
        ]
        if len(delivery_dates) >= 3:
            base += 5
        elif delivery_dates:
            base += 3

        return min(base, 25)

    def _calculate_recency(self, active_ads: List[Dict[str, Any]]) -> int:
        """Score 0-15: how recently was the earliest ad detected."""
        if not active_ads:
            return 0

        now = datetime.now(timezone.utc)
        earliest: Optional[datetime] = None

        for ad in active_ads:
            start_str = ad.get("ad_delivery_start", "")
            if not start_str:
                continue
            try:
                start_dt = datetime.fromisoformat(start_str.replace("Z", "+00:00"))
                if earliest is None or start_dt < earliest:
                    earliest = start_dt
            except Exception:
                continue

        if earliest is None:
            return 5

        days_ago = (now - earliest).days

        if days_ago <= 7:
            return 15
        elif days_ago <= 14:
            return 12
        elif days_ago <= 30:
            return 9
        elif days_ago <= 60:
            return 6
        elif days_ago <= 90:
            return 3
        return 1

    def _avg_spend(self, active_ads: List[Dict[str, Any]]) -> float:
        """Parse spend ranges and return average min-spend across ads."""
        spend_ranges = [
            ad.get("spend", {}).get("min", "0")
            for ad in active_ads
            if ad.get("spend")
        ]
        if not spend_ranges:
            return 0.0
        total, parsed = 0.0, 0
        for s in spend_ranges:
            try:
                total += float(s.replace("$", "").replace(",", "").replace(" ", ""))
                parsed += 1
            except (ValueError, AttributeError):
                continue
        return round(total / parsed, 2) if parsed > 0 else 0.0

    # ------------------------------------------------------------------
    # Router-facing convenience methods
    # ------------------------------------------------------------------

    async def search_ads_library(
        self,
        company_name: str,
        company_domain: Optional[str],
        user_id: str,
    ) -> Dict[str, Any]:
        """
        Search Ads Library, extract signals, and cache results.
        Called by GET /api/v1/meta-ads/search.
        """
        search_results = await self.search_ads(company_name, company_domain)
        signals = self.build_signals(search_results)
        await self.store_signals(user_id, company_domain, company_name, signals, search_results)

        return {
            "searched_company": company_name,
            "searched_domain": company_domain,
            "signals": signals,
            "raw_ads": search_results.get("data", []),
        }

    async def refresh_signals(
        self,
        company_domain: str,
        company_name: str,
        user_id: str,
    ) -> Dict[str, Any]:
        """
        Force-refresh signals from the Ads Library API.
        Called by POST /api/v1/meta-ads/refresh.
        """
        search_results = await self.search_ads(company_name, company_domain)
        signals = self.build_signals(search_results)
        await self.store_signals(user_id, company_domain, company_name, signals, search_results)

        return {
            "company_domain": company_domain,
            "company_name": company_name,
            "signals": signals,
            "raw_ads": search_results.get("data", []),
            "refreshed_at": datetime.now(timezone.utc).isoformat(),
        }

    # ------------------------------------------------------------------
    # Supabase storage / retrieval
    # ------------------------------------------------------------------

    async def store_signals(
        self,
        user_id: str,
        company_domain: str,
        company_name: str,
        signals: Dict[str, Any],
        search_results: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Upsert meta_ad_signals row and insert individual ads into meta_ads.

        meta_ad_signals table columns:
          user_id, company_domain, company_name, fb_page_id, fb_page_url,
          is_advertiser, ad_count, first_seen_at, last_seen_at,
          fetched_at, raw_response (JSONB)
        """
        logger.info(f"Storing meta_ad_signals: domain={company_domain} user={user_id}")

        try:
            client = get_service_client()
            now = datetime.now(timezone.utc).isoformat()
            ads = search_results.get("data", []) if search_results else []
            active_ads = [ad for ad in ads if ad.get("ad_status") == "ACTIVE"]

            # Determine fb_page_id / fb_page_url from first active ad
            fb_page_id_val = None
            fb_page_url_val = None
            if active_ads:
                fb_page_id_val = active_ads[0].get("page_id")

            # First/last seen dates
            delivery_dates = sorted([
                ad.get("ad_delivery_start", "")
                for ad in active_ads
                if ad.get("ad_delivery_start")
            ])

            signals_payload = {
                "user_id": user_id,
                "company_domain": company_domain,
                "company_name": company_name,
                "fb_page_id": fb_page_id_val,
                "fb_page_url": f"https://www.facebook.com/{fb_page_id_val}" if fb_page_id_val else None,
                "is_advertiser": signals.get("is_advertiser", False),
                "ad_count": signals.get("ad_count", 0),
                "first_seen_at": delivery_dates[0] if delivery_dates else None,
                "last_seen_at": delivery_dates[-1] if delivery_dates else None,
                "fetched_at": now,
                "raw_response": search_results if search_results else None,
                "updated_at": now,
            }

            result = client.table("meta_ad_signals").upsert(
                signals_payload,
                on_conflict="user_id,company_domain",
            ).execute()

            if not result.data:
                logger.warning(f"No data returned from meta_ad_signals upsert")
                return None

            signals_id = result.data[0].get("id")
            logger.info(
                f"Stored meta_ad_signals: id={signals_id} "
                f"domain={company_domain} ad_count={signals.get('ad_count', 0)}"
            )

            # Insert individual ads into meta_ads table
            if active_ads:
                await self._store_ads(signals_id, user_id, company_domain, active_ads)

            return result.data[0] if result.data else None

        except Exception as e:
            logger.error(f"Failed to store meta_ad_signals: {e}")
            return None

    async def _store_ads(
        self,
        signals_id: str,
        user_id: str,
        company_domain: str,
        ads: List[Dict[str, Any]],
    ) -> None:
        """Insert individual ad records into meta_ads table."""
        try:
            for ad in ads:
                body = ad.get("ad_creative_body", "") or ""
                creative_type = self.analyze_ad_creative(body)

                ad_payload = {
                    "meta_ad_signals_id": signals_id,
                    "ad_id": ad.get("id"),
                    "page_id": ad.get("page_id"),
                    "ad_creative_body": body[:1000],
                    "ad_creative_link": ad.get("ad_creative_link_caption"),
                    "ad_snapshot_url": ad.get("ad_snapshot_url"),
                    "ad_status": ad.get("ad_status"),
                    "ad_delivery_start": ad.get("ad_delivery_start"),
                    "ad_delivery_end": ad.get("ad_delivery_stop"),
                    "is_lead_gen": creative_type.get("ad_type") == "lead_gen",
                    "is_brand_awareness": creative_type.get("ad_type") == "brand_awareness",
                    "is_conversion": creative_type.get("ad_type") == "conversion",
                }

                client = get_service_client()
                client.table("meta_ads").insert(ad_payload).execute()

            logger.info(f"Stored {len(ads)} individual ads for signals_id={signals_id}")

        except Exception as e:
            logger.warning(f"Failed to store individual ads: {e}")

    async def get_cached_signals(
        self,
        user_id: str,
        company_domain: str,
    ) -> Optional[Dict[str, Any]]:
        """
        Retrieve cached meta_ad_signals from Supabase.

        Returns row with computed meta_ad_intensity / meta_ad_recency
        fields added from the raw_response (if available).
        """
        try:
            client = get_service_client()
            result = client.table("meta_ad_signals").select("*").eq(
                "user_id", user_id
            ).eq("company_domain", company_domain).execute()

            if not result.data:
                logger.debug(f"No cached meta_ad_signals for {company_domain}")
                return None

            record = result.data[0]

            # Inject computed signal fields for API response compatibility
            raw = record.get("raw_response") or {}
            built = self.build_signals(raw) if raw else self._empty_signals()

            enriched = {
                **record,
                "meta_ad_active": record.get("is_advertiser", False),
                "meta_ad_lead_gen": built.get("meta_ad_lead_gen", False),
                "meta_ad_intensity": built.get("meta_ad_intensity", 0),
                "meta_ad_recency": built.get("meta_ad_recency", 0),
                "intensity": built.get("intensity", 0),
                "recency": built.get("recency", 0),
                "is_lead_gen": built.get("is_lead_gen", False),
                "ad_type": built.get("ad_type", "UNKNOWN"),
                "avg_spend_min": built.get("avg_spend_min", 0.0),
                "funding_entities": built.get("funding_entities", []),
                "sample_ads": built.get("sample_ads", []),
            }

            logger.debug(f"Retrieved cached meta_ad_signals for {company_domain}")
            return enriched

        except Exception as e:
            logger.error(f"Failed to get cached meta_ad_signals: {e}")
            return None
