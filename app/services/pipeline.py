"""
Intelligence Pipeline - Complete data collection, analysis, and insight generation
"""
import httpx
import hashlib
import feedparser
from bs4 import BeautifulSoup
from datetime import datetime
from typing import List, Dict, Any, Optional
import json

class DataCollector:
    """Collects data from various sources"""

    @staticmethod
    def compute_hash(content: str) -> str:
        return hashlib.sha256(content.encode()).hexdigest()

    @staticmethod
    async def fetch_rss(url: str, keywords: List[str] = None) -> Dict[str, Any]:
        """Fetch and parse RSS/Atom feeds"""
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(url)
                response.raise_for_status()

            feed = feedparser.parse(response.text)
            entries = []

            for entry in feed.entries[:50]:
                title = entry.get("title", "")
                summary = entry.get("summary", entry.get("description", ""))
                link = entry.get("link", "")
                published = entry.get("published", "")

                # Filter by keywords
                if keywords:
                    content_lower = f"{title} {summary}".lower()
                    if not any(kw.lower() in content_lower for kw in keywords):
                        continue

                entries.append({
                    "title": title,
                    "content": summary,
                    "url": link,
                    "published": published,
                    "source": feed.feed.get("title", url),
                })

            return {
                "type": "rss",
                "source_name": feed.feed.get("title", url),
                "entries": entries,
                "fetched_at": datetime.utcnow().isoformat(),
            }
        except Exception as e:
            return {
                "type": "rss",
                "error": str(e),
                "fetched_at": datetime.utcnow().isoformat(),
            }

    @staticmethod
    async def fetch_webpage(url: str, selectors: Dict = None) -> Dict[str, Any]:
        """Fetch and scrape a webpage"""
        if selectors is None:
            selectors = {
                "title": "h1, title",
                "content": "main, article, .content, body",
            }

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(
                    url,
                    headers={"User-Agent": "Mozilla/5.0 (compatible; Almanac/1.0)"}
                )
                response.raise_for_status()

            soup = BeautifulSoup(response.text, "html.parser")

            # Extract title
            title_elem = soup.select_one(selectors.get("title", "title"))
            title = title_elem.get_text(strip=True) if title_elem else ""

            # Extract content
            content_elem = soup.select_one(selectors.get("content", "body"))
            content = content_elem.get_text(separator=" ", strip=True) if content_elem else ""

            # Truncate if too long
            if len(content) > 10000:
                content = content[:10000] + "..."

            return {
                "type": "webpage",
                "title": title,
                "content": content,
                "url": url,
                "hash": DataCollector.compute_hash(content),
                "fetched_at": datetime.utcnow().isoformat(),
            }
        except Exception as e:
            return {
                "type": "webpage",
                "error": str(e),
                "url": url,
                "fetched_at": datetime.utcnow().isoformat(),
            }

    @staticmethod
    async def fetch_api(url: str, headers: Dict = None, params: Dict = None) -> Dict[str, Any]:
        """Fetch from REST API"""
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(url, headers=headers, params=params)
                response.raise_for_status()

            return {
                "type": "api",
                "data": response.json(),
                "url": url,
                "fetched_at": datetime.utcnow().isoformat(),
            }
        except Exception as e:
            return {
                "type": "api",
                "error": str(e),
                "url": url,
                "fetched_at": datetime.utcnow().isoformat(),
            }


class ContentAnalyzer:
    """AI-powered content analysis using Claude API"""

    def __init__(self, api_key: str = None):
        self.api_key = api_key or self._get_api_key()
        self.api_url = "https://api.anthropic.com/v1/messages"
        self.model = "claude-3-5-haiku-20241022"  # Cheapest + fast
        self.max_tokens = 1024

    def _get_api_key(self) -> Optional[str]:
        import os
        return os.getenv("ANTHROPIC_API_KEY")

    async def analyze(self, content: str, context: Dict[str, Any]) -> Dict[str, Any]:
        """Analyze content and generate structured insights"""

        if not self.api_key:
            return self._fallback_analysis(content, context)

        prompt = self._build_prompt(content, context)

        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.post(
                    self.api_url,
                    headers={
                        "x-api-key": self.api_key,
                        "anthropic-version": "2023-06-01",
                        "content-type": "application/json",
                    },
                    json={
                        "model": self.model,
                        "max_tokens": self.max_tokens,
                        "messages": [{"role": "user", "content": prompt}]
                    }
                )

            if response.status_code == 200:
                data = response.json()
                analysis_text = data["content"][0]["text"]
                return self._parse_response(analysis_text, context)
            else:
                return self._fallback_analysis(content, context)

        except Exception as e:
            return self._fallback_analysis(content, context)

    def _build_prompt(self, content: str, context: Dict) -> str:
        entity_name = context.get("entity_name", "this company")
        source_type = context.get("source_type", "content")

        return f"""Analyze this content from {entity_name} ({source_type}) and provide structured insights.

CONTENT:
{content[:3000]}

Respond with ONLY valid JSON (no markdown, no explanation):
{{
    "insight_type": "funding|product|hiring|pr|leadership|partnership|anomaly|summary",
    "title": "Brief actionable title",
    "content": "2-3 sentence summary of what this means for competitive intelligence",
    "importance": "critical|high|medium|low",
    "confidence": 0.0-1.0
}}"""

    def _parse_response(self, text: str, context: Dict) -> Dict:
        try:
            # Clean up the response
            text = text.strip()
            if text.startswith("```"):
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]
                text = text.rstrip("`")

            data = json.loads(text.strip())

            return {
                "insight_type": data.get("insight_type", "summary"),
                "title": data.get("title", "Content Update"),
                "content": data.get("content", ""),
                "importance": data.get("importance", "medium"),
                "confidence": data.get("confidence", 0.5),
                "entity_id": context.get("entity_id"),
                "source_ids": context.get("source_ids", []),
                "generated_at": datetime.utcnow().isoformat(),
            }
        except json.JSONDecodeError:
            return self._fallback_analysis(context.get("raw_content", ""), context)

    def _fallback_analysis(self, content: str, context: Dict) -> Dict:
        """Keyword-based fallback when API unavailable"""
        content_lower = content.lower()

        # Detect insight type by keywords
        keywords_map = {
            "funding": ["raised", "series", "funding", "investment", "million", "billion"],
            "product": ["launch", "release", "new feature", "announce"],
            "hiring": ["hiring", "job", "recruit", "cto", "vp of"],
            "pr": ["featured in", "interview", "article", "press release"],
            "leadership": ["ceo", "founder", "appoint", "executive"],
            "partnership": ["partnered", "collaboration", "integration with"],
        }

        insight_type = "summary"
        importance = "low"

        for itype, keywords in keywords_map.items():
            if any(kw in content_lower for kw in keywords):
                insight_type = itype
                importance = "high" if itype in ["funding", "product"] else "medium"
                break

        # Generate title from content
        title = context.get("title", "Content Update")[:100]

        return {
            "insight_type": insight_type,
            "title": title,
            "content": content[:500],
            "importance": importance,
            "confidence": 0.6,
            "entity_id": context.get("entity_id"),
            "source_ids": context.get("source_ids", []),
            "generated_at": datetime.utcnow().isoformat(),
        }


class IntelligencePipeline:
    """Main pipeline orchestrator"""

    def __init__(self):
        self.collector = DataCollector()
        self.analyzer = ContentAnalyzer()

    async def process_source(self, source: Dict, entity: Dict) -> Optional[Dict]:
        """Process a single data source"""
        source_type = source.get("source_type")
        url = source.get("url")
        config = source.get("config", {})
        keywords = config.get("keywords", [])

        if not url:
            return None

        # Collect data
        if source_type == "rss":
            raw_data = await self.collector.fetch_rss(url, keywords)
        elif source_type == "scrape":
            raw_data = await self.collector.fetch_webpage(url, config.get("selectors"))
        elif source_type == "api":
            raw_data = await self.collector.fetch_api(url, config.get("headers"), config.get("params"))
        else:
            return None

        # Check for errors
        if "error" in raw_data:
            return {"error": raw_data["error"], "source_id": source.get("id")}

        # Analyze content
        context = {
            "entity_id": entity.get("id"),
            "entity_name": entity.get("name"),
            "source_type": source_type,
            "title": raw_data.get("title", ""),
            "raw_content": raw_data.get("content", ""),
            "source_ids": [source.get("id")],
        }

        # For RSS, analyze each entry
        if source_type == "rss":
            insights = []
            for entry in raw_data.get("entries", []):
                entry_context = {
                    **context,
                    "title": entry.get("title", ""),
                    "raw_content": entry.get("content", ""),
                }
                insight = await self.analyzer.analyze(entry.get("content", ""), entry_context)
                insights.append(insight)
            return {"insights": insights, "raw_data": raw_data}
        else:
            # For web/api, analyze the main content
            insight = await self.analyzer.analyze(raw_data.get("content", ""), context)
            return {"insights": [insight], "raw_data": raw_data}

    async def process_entity(self, entity: Dict, sources: List[Dict]) -> List[Dict]:
        """Process all sources for an entity"""
        all_insights = []

        for source in sources:
            if not source.get("is_active"):
                continue

            result = await self.process_source(source, entity)
            if result and "insights" in result:
                all_insights.extend(result["insights"])

        return all_insights
