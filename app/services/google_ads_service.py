"""
Module 20: Google Ads Intelligence
Queries Google's public Transparency Report API to detect if a company
is running Google Ads / Display ads.

Public API — no credentials required for the Transparency Report endpoint.
The GOOGLE_ADS_DEVELOPER_TOKEN config is optional (needed only for the
full Google Ads API, which requires OAuth and is documented as a future
upgrade path).

Signal fields map to google_ads_signals table columns:
  user_id, prospect_id, company_domain, company_name, is_advertiser,
  ad_count, google_ad_intensity, google_ad_keyword_themes,
  google_ad_recency, first_seen_at, last_seen_at, fetched_at,
  raw_response (JSONB)
"""
import asyncio
import json
import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx

from app.core.config import settings
from app.db.database import get_service_client

logger = logging.getLogger(__name__)

TRANSPARENCY_REPORT_URL = (
    "https://transparencyreport.google.com/transparencyreport/api/v3/report/ahm/ads/EN"
)

# High-intent B2B keywords that signal active sales/marketing spend
HIGH_INTENT_KEYWORDS = [
    "enterprise",
    "api",
    "security",
    "cloud",
    "saas",
    "b2b",
    "software",
    "solution",
    "platform",
    "pricing",
    "demo",
    "trial",
    "contact sales",
    "get started",
    "business",
    "team",
    "company",
    "compare",
    "alternative",
    "review",
    "vs ",
    "integrat",
    "automate",
    "scale",
    " ROI ",
    "workflow",
    "integrations",
]


class GoogleAdsServiceError(Exception):
    """Base exception for Google Ads service."""
    pass


class GoogleAdsService:
    """
    Queries Google's Transparency Report for advertising signals about companies.
    Public API — no OAuth or developer token required.

    Signal scores (max 45 pts combined):
      - google_ad_intensity:   0-15 based on ad/campaign volume
      - google_ad_keyword_themes: 0-15 based on high-intent keyword count
      - google_ad_recency:    0-15 based on how recently ads were active
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

    async def _get_with_retry(self, url: str) -> Dict[str, Any]:
        """
        Execute GET with exponential backoff retry on 429 / 5xx.
        Max 3 retries (4 total attempts).
        """
        last_error: Optional[Exception] = None
        for attempt in range(4):
            try:
                response = await self._client.get(url)

                if response.status_code == 429:
                    retry_after = int(response.headers.get("Retry-After", 60))
                    wait = min(retry_after, 120)
                    logger.warning(f"GOOGLE: rate-limited (429). Waiting {wait}s.")
                    await asyncio.sleep(wait)
                    continue

                if response.status_code >= 500:
                    delay = min(2 ** attempt + 1, 30)
                    logger.warning(
                        f"GOOGLE: server error {response.status_code}. "
                        f"Retry in {delay}s (attempt {attempt + 1})."
                    )
                    await asyncio.sleep(delay)
                    continue

                if response.status_code == 404:
                    logger.debug(f"GOOGLE: 404 for URL {url} — no advertiser found")
                    return {}

                response.raise_for_status()
                # Transparency Report returns JS-wrapped JSON: )]}'\n<json>
                text = response.text
                return self._parse_js_response(text)

            except httpx.TimeoutException as e:
                last_error = e
                logger.warning(f"GOOGLE: timeout (attempt {attempt + 1}): {e}")
                if attempt < 3:
                    await asyncio.sleep(2 ** attempt)
                continue
            except httpx.HTTPStatusError as e:
                last_error = e
                logger.warning(f"GOOGLE: HTTP error (attempt {attempt + 1}): {e}")
                if attempt < 3:
                    await asyncio.sleep(2 ** attempt)
                continue
            except Exception as e:
                last_error = e
                logger.warning(f"GOOGLE: unexpected error (attempt {attempt + 1}): {e}")
                if attempt < 3:
                    await asyncio.sleep(2 ** attempt)
                continue

        logger.error(f"GOOGLE: all 4 attempts failed: {last_error}")
        return {}

    def _parse_js_response(self, text: str) -> Dict[str, Any]:
        """
        Google's Transparency Report API returns a JS-wrapped JSON response.
        Format: )]}'\\n<json payload>  or  )]}'<json>
        We strip the JS prefix and parse the JSON.
        """
        try:
            stripped = text.strip()
            # Strip )]}'\n or )]}' prefix
            match = re.match(r"^\)\]\}'(.*)$", stripped, re.DOTALL)
            if match:
                stripped = match.group(1).strip()
            return json.loads(stripped)
        except json.JSONDecodeError as e:
            logger.warning(f"GOOGLE: failed to parse Transparency Report response: {e}")
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
        Query Google's Transparency Report for a company's advertising data.

        Args:
            company_name: Company display name (used for logging / fallback).
            company_domain: Domain to query (e.g. "stripe.com"). Preferred over
                company_name for accuracy — the API accepts domains directly.

        Returns:
            Parsed Transparency Report dict with advertiser data, ad counts,
            domains advertised on, and last-active timestamps.
        """
        domain = self._clean_domain(company_domain) if company_domain else ""

        if not domain:
            logger.warning(f"GOOGLE: no domain provided for '{company_name}', returning empty result")
            return self._empty_result(company_name, company_domain)

        url = f"{TRANSPARENCY_REPORT_URL}/{domain}"
        logger.info(f"GOOGLE: querying Transparency Report for domain={domain}")

        try:
            data = await self._get_with_retry(url)
            return self._normalize_response(data, company_name, company_domain)

        except Exception as e:
            logger.error(f"GOOGLE: search_ads failed for '{domain}': {e}")
            return self._empty_result(company_name, company_domain)

    def _normalize_response(
        self,
        data: Any,
        company_name: str,
        company_domain: Optional[str],
    ) -> Dict[str, Any]:
        """
        Normalize the Transparency Report response into a consistent dict.

        The API returns a JS array with structure roughly:
          [status, advertiser_info, ads_summary, domains_list, ...]
        We extract what we can from the nested structure.
        """
        if not data:
            return self._empty_result(company_name, company_domain)

        # Handle list response (most common format)
        if isinstance(data, list):
            if len(data) < 2:
                return self._empty_result(company_name, company_domain)

            # data[0] is typically the status array [status_code, ...]
            status_arr = data[0] if data else []
            status_code = status_arr[0] if isinstance(status_arr, list) and status_arr else None

            if status_code != 200:
                return self._empty_result(company_name, company_domain)

            # data[1] often contains advertiser info (name, id, etc.)
            advertiser_info = data[1] if len(data) > 1 else {}

            # data[2] often contains ads summary (ad count, last active, etc.)
            ads_summary = data[2] if len(data) > 2 else {}

            # data[3] may contain domains list
            domains_list = data[3] if len(data) > 3 else []

            return self._extract_fields(
                advertiser_info, ads_summary, domains_list,
                company_name, company_domain,
            )

        # Handle dict response (fallback for unexpected format)
        if isinstance(data, dict):
            return self._extract_fields(
                data.get("advertiser_info", {}),
                data.get("ads_summary", data),
                data.get("domains_list", []),
                company_name, company_domain,
            )

        return self._empty_result(company_name, company_domain)

    def _extract_fields(
        self,
        advertiser_info: Any,
        ads_summary: Any,
        domains_list: Any,
        company_name: str,
        company_domain: Optional[str],
    ) -> Dict[str, Any]:
        """Extract meaningful fields from raw Transparency Report arrays."""

        # Advertiser identity
        advertiser_name = self._extract_text(advertiser_info)
        ad_count = self._extract_number(ads_summary)
        last_active = self._extract_date(ads_summary)
        domains = self._extract_list(domains_list)

        # Recency from last active date
        recency_days = None
        if last_active:
            try:
                last_dt = datetime.fromisoformat(last_active.replace("Z", "+00:00"))
                recency_days = (datetime.now(timezone.utc) - last_dt).days
            except Exception:
                recency_days = None

        # Keyword themes from advertiser name (a weak but available signal)
        keyword_themes = self.extract_keyword_themes({"advertiser_name": advertiser_name})

        return {
            "advertiser_name": advertiser_name,
            "ad_count": ad_count,
            "last_active_date": last_active,
            "recency_days": recency_days,
            "domains_advertised_on": domains,
            "searched_company": company_name,
            "searched_domain": company_domain,
            "searched_at": datetime.now(timezone.utc).isoformat(),
            "keyword_themes": keyword_themes,
        }

    def _extract_text(self, value: Any) -> str:
        """Extract a string from a potentially nested array."""
        if not value:
            return ""
        if isinstance(value, str):
            return value.strip()
        if isinstance(value, list):
            for item in value:
                text = self._extract_text(item)
                if text:
                    return text
        if isinstance(value, dict):
            for key in ["name", "advertiserName", "advertiser_name", "0"]:
                if key in value:
                    text = self._extract_text(value[key])
                    if text:
                        return text
        return ""

    def _extract_number(self, value: Any) -> int:
        """Extract an integer from a potentially nested array or dict."""
        if not value:
            return 0
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return int(value)
        if isinstance(value, str):
            try:
                return int(float(value.replace(",", "")))
            except (ValueError, AttributeError):
                return 0
        if isinstance(value, list):
            for item in value:
                num = self._extract_number(item)
                if num > 0:
                    return num
        if isinstance(value, dict):
            for key in ["ad_count", "num_ads", "count", "ads", "0", "1"]:
                if key in value:
                    num = self._extract_number(value[key])
                    if num > 0:
                        return num
        return 0

    def _extract_date(self, value: Any) -> Optional[str]:
        """Extract an ISO date string from a potentially nested structure."""
        if not value:
            return None
        if isinstance(value, str) and re.match(r"\d{4}-\d{2}-\d{2}", value):
            return value
        if isinstance(value, list):
            for item in value:
                date = self._extract_date(item)
                if date:
                    return date
        if isinstance(value, dict):
            for key in ["last_active", "lastActive", "last_active_date", "date", "0", "1"]:
                if key in value:
                    date = self._extract_date(value[key])
                    if date:
                        return date
        return None

    def _extract_list(self, value: Any) -> List[str]:
        """Extract a list of strings from a potentially nested structure."""
        if not value:
            return []
        if isinstance(value, list):
            results = []
            for item in value:
                if isinstance(item, str) and item:
                    results.append(item)
                elif isinstance(item, list):
                    results.extend(self._extract_list(item))
                elif isinstance(item, dict):
                    for k, v in item.items():
                        if isinstance(v, str) and v:
                            results.append(v)
                        elif isinstance(v, list):
                            results.extend(self._extract_list(v))
            return list(dict.fromkeys(results))  # dedupe, preserve order
        return []

    def _empty_result(self, company_name: str, company_domain: Optional[str]) -> Dict[str, Any]:
        return {
            "advertiser_name": "",
            "ad_count": 0,
            "last_active_date": None,
            "recency_days": None,
            "domains_advertised_on": [],
            "searched_company": company_name,
            "searched_domain": company_domain,
            "searched_at": datetime.now(timezone.utc).isoformat(),
            "keyword_themes": [],
        }

    # ------------------------------------------------------------------
    # Keyword theme extraction
    # ------------------------------------------------------------------

    def extract_keyword_themes(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Analyze Transparency Report data for high-intent B2B keyword themes.

        The Transparency Report doesn't expose ad creative text publicly,
        so we analyze available fields (advertiser name, domain list) as
        a proxy for keyword targeting signals.

        Args:
            data: Normalized search result dict.

        Returns:
            Dict with matched_themes (list), theme_count (int),
            high_intent_count (int).
        """
        # Combine all available text fields for analysis
        text_fields = [
            data.get("advertiser_name", ""),
            data.get("searched_company", ""),
        ]
        text_fields.extend(data.get("domains_advertised_on", []))
        combined = " ".join(text_fields).lower()

        matched_themes: List[str] = []
        high_intent_count = 0

        for keyword in HIGH_INTENT_KEYWORDS:
            if keyword.lower() in combined:
                matched_themes.append(keyword.strip())
                # B2B-specific keywords get counted as high-intent
                if keyword.lower() in [
                    "enterprise", "api", "security", "pricing", "demo",
                    "trial", "contact sales", "get started", "business",
                    "solution", "platform", "saas", "b2b",
                ]:
                    high_intent_count += 1

        return {
            "matched_themes": matched_themes,
            "theme_count": len(matched_themes),
            "high_intent_count": high_intent_count,
        }

    # ------------------------------------------------------------------
    # Signal building
    # ------------------------------------------------------------------

    def build_signals(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Extract scoring signals from Transparency Report data.

        Signal scores (max 45 pts combined):
          - google_ad_intensity:       0-15 based on ad count
          - google_ad_keyword_themes:   0-15 based on high-intent keyword count
          - google_ad_recency:         0-15 based on last active date

        Returns:
            Signal dict ready for google_ads_signals table upsert and
            for injecting into the intent_scores table.
        """
        if not data or not data.get("advertiser_name"):
            return self._empty_signals()

        ad_count = data.get("ad_count", 0)
        is_advertiser = ad_count > 0

        # Intensity: 0-15 based on ad volume
        intensity = self._calculate_intensity(ad_count)

        # Keyword themes: 0-15 based on high-intent keyword matches
        keyword_themes = self._calculate_keyword_score(data.get("keyword_themes", {}))

        # Recency: 0-15 based on last active date
        recency = self._calculate_recency(data.get("recency_days"))

        return {
            "is_advertiser": is_advertiser,
            "ad_count": ad_count,
            "google_ad_active": is_advertiser,
            "google_ad_intensity": intensity,
            "google_ad_keyword_themes": keyword_themes,
            "google_ad_recency": recency,
            "keyword_themes": data.get("keyword_themes", {}),
            "built_at": datetime.now(timezone.utc).isoformat(),
        }

    def _empty_signals(self) -> Dict[str, Any]:
        return {
            "is_advertiser": False,
            "ad_count": 0,
            "google_ad_active": False,
            "google_ad_intensity": 0,
            "google_ad_keyword_themes": 0,
            "google_ad_recency": 0,
            "keyword_themes": {},
            "built_at": datetime.now(timezone.utc).isoformat(),
        }

    def _calculate_intensity(self, ad_count: int) -> int:
        """Score 0-15: more ads = higher spend = more aggressive marketing."""
        if ad_count == 0:
            return 0
        if ad_count >= 50:
            return 15
        if ad_count >= 20:
            return 12
        if ad_count >= 10:
            return 9
        if ad_count >= 5:
            return 6
        if ad_count >= 2:
            return 3
        return 1

    def _calculate_keyword_score(self, keyword_data: Dict[str, Any]) -> int:
        """
        Score 0-15: based on count of matched high-intent B2B keywords.
        More high-intent themes = more targeted B2B advertising.
        """
        high_intent_count = keyword_data.get("high_intent_count", 0)
        if high_intent_count == 0:
            return 0
        if high_intent_count >= 6:
            return 15
        if high_intent_count >= 5:
            return 12
        if high_intent_count >= 4:
            return 10
        if high_intent_count >= 3:
            return 8
        if high_intent_count >= 2:
            return 5
        return 3

    def _calculate_recency(self, recency_days: Optional[int]) -> int:
        """Score 0-15: how recently was the advertiser last active."""
        if recency_days is None:
            return 0
        if recency_days <= 7:
            return 15
        if recency_days <= 14:
            return 12
        if recency_days <= 30:
            return 10
        if recency_days <= 60:
            return 7
        if recency_days <= 90:
            return 4
        if recency_days <= 180:
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
        Upsert google_ads_signals row for a company + user.

        Table columns:
          user_id, prospect_id, company_domain, company_name,
          is_advertiser, ad_count, advertiser_name,
          google_ad_intensity, google_ad_keyword_themes, google_ad_recency,
          domains_advertised_on, last_active_date, first_seen_at,
          last_seen_at, fetched_at, raw_response (JSONB)

        on_conflict: user_id, company_domain
        """
        logger.info(
            f"GOOGLE: storing google_ads_signals: "
            f"domain={company_domain} user={user_id}"
        )

        try:
            client = get_service_client()
            now = datetime.now(timezone.utc).isoformat()

            # Look up prospect_id for this company (same pattern as reddit_ads_service.py)
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

            last_active = (raw_data or {}).get("last_active_date")

            # Parse last_active_date into a TIMESTAMPTZ for first_seen_at / last_seen_at
            first_seen = None
            last_seen = None
            if last_active:
                try:
                    last_dt = datetime.fromisoformat(last_active.replace("Z", "+00:00"))
                    last_seen = last_dt.isoformat()
                    # If no prior record, first_seen == last_seen
                    first_seen = last_seen
                except Exception:
                    pass

            # keyword_themes dict -> list for JSONB column
            keyword_themes_dict = signals.get("keyword_themes", {})
            keyword_themes_list = keyword_themes_dict.get("matched_themes", [])

            payload = {
                "user_id": user_id,
                "prospect_id": prospect_id,
                "company_domain": company_domain,
                "company_name": company_name,
                "is_advertiser": signals.get("is_advertiser", False),
                "ad_count": signals.get("ad_count", 0),
                "campaigns_found": 0,  # Transparency Report doesn't expose campaigns
                "keywords_found": keyword_themes_dict.get("theme_count", 0),
                "keyword_themes": keyword_themes_list,
                "high_intent_keywords": keyword_themes_dict.get("high_intent_count", 0),
                "first_seen_at": first_seen,
                "last_seen_at": last_seen,
                "fetched_at": now,
                "raw_response": raw_data,
                "updated_at": now,
            }

            result = client.table("google_ads_signals").upsert(
                payload,
                on_conflict="user_id,company_domain",
            ).execute()

            if not result.data:
                logger.warning("GOOGLE: no data returned from google_ads_signals upsert")
                return None

            record = result.data[0]
            logger.info(
                f"GOOGLE: stored google_ads_signals: "
                f"id={record.get('id')} domain={company_domain} "
                f"ad_count={signals.get('ad_count', 0)}"
            )
            return record

        except Exception as e:
            logger.error(f"GOOGLE: failed to store google_ads_signals: {e}")
            return None

    async def get_cached_signals(
        self,
        user_id: str,
        company_domain: str,
    ) -> Optional[Dict[str, Any]]:
        """
        Retrieve cached google_ads_signals from Supabase.
        Returns the row with enriched signal fields.
        """
        try:
            client = get_service_client()
            result = (
                client.table("google_ads_signals")
                .select("*")
                .eq("user_id", user_id)
                .eq("company_domain", company_domain)
                .execute()
            )

            if not result.data:
                logger.debug(
                    f"GOOGLE: no cached google_ads_signals for {company_domain}"
                )
                return None

            record = result.data[0]
            logger.debug(f"GOOGLE: retrieved cached signals for {company_domain}")
            return record

        except Exception as e:
            logger.error(f"GOOGLE: failed to get cached signals: {e}")
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
        Force-refresh Google Ads signals from the Transparency Report API.
        Called by POST /api/v1/google-ads/refresh.
        """
        logger.info(
            f"GOOGLE: refreshing ad signals for '{company_name}' "
            f"(domain={company_domain})"
        )

        search_results = await self.search_ads(company_name, company_domain)
        signals = self.build_signals(search_results)
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
