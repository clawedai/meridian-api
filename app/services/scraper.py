import httpx
from abc import ABC, abstractmethod
from typing import List, Dict, Optional, Any
from datetime import datetime
import hashlib
import feedparser
from bs4 import BeautifulSoup
import asyncio

class BaseScraper(ABC):
    """Base class for all scrapers"""

    @abstractmethod
    async def fetch(self, url: str, config: Dict[str, Any]) -> Dict[str, Any]:
        """Fetch data from source"""
        pass

    def compute_hash(self, content: str) -> str:
        """Compute content hash for deduplication"""
        return hashlib.sha256(content.encode()).hexdigest()

class RSSScraper(BaseScraper):
    """RSS/Atom feed scraper"""

    async def fetch(self, url: str, config: Dict[str, Any]) -> Dict[str, Any]:
        """Fetch and parse RSS feed"""
        keywords = config.get("keywords", [])

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(url)
            response.raise_for_status()

            feed = feedparser.parse(response.text)

            entries = []
            for entry in feed.entries[:50]:  # Limit to 50 entries
                title = entry.get("title", "")
                summary = entry.get("summary", entry.get("description", ""))
                link = entry.get("link", "")
                published = entry.get("published", "")

                # Filter by keywords if provided
                if keywords:
                    content = f"{title} {summary}".lower()
                    if not any(kw.lower() in content for kw in keywords):
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
                "entries": entries,
                "fetched_at": datetime.utcnow().isoformat(),
            }

class WebScraper(BaseScraper):
    """Webpage scraper"""

    async def fetch(self, url: str, config: Dict[str, Any]) -> Dict[str, Any]:
        """Fetch and scrape webpage"""
        selectors = config.get("selectors", {
            "title": "h1, title",
            "content": "main, article, .content, body",
        })

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(url, headers={
                "User-Agent": "Mozilla/5.0 (compatible; Almanac/1.0)"
            })
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
                "fetched_at": datetime.utcnow().isoformat(),
                "hash": self.compute_hash(content),
            }

class APIScraper(BaseScraper):
    """API endpoint scraper"""

    async def fetch(self, url: str, config: Dict[str, Any]) -> Dict[str, Any]:
        """Fetch from API endpoint"""
        headers = config.get("headers", {})
        params = config.get("params", {})

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                url,
                headers=headers,
                params=params,
            )
            response.raise_for_status()

            return {
                "type": "api",
                "data": response.json(),
                "url": url,
                "fetched_at": datetime.utcnow().isoformat(),
            }

class ScraperFactory:
    """Factory to create appropriate scraper"""

    _scrapers = {
        "rss": RSSScraper,
        "scrape": WebScraper,
        "api": APIScraper,
    }

    @classmethod
    def get_scraper(cls, source_type: str) -> BaseScraper:
        """Get scraper instance for source type"""
        scraper_class = cls._scrapers.get(source_type, WebScraper)
        return scraper_class()

    @classmethod
    async def fetch_source(cls, source_type: str, url: str, config: Dict[str, Any]) -> Dict[str, Any]:
        """Fetch data from a source"""
        scraper = cls.get_scraper(source_type)
        return await scraper.fetch(url, config)
