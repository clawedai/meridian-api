"""
Module 01: Social Content Analysis
Scrapes LinkedIn company pages and extracts public posts.
Fallback: Use company careers pages if LinkedIn is blocked.
"""
import httpx
from bs4 import BeautifulSoup
from typing import List, Optional
from datetime import datetime
import logging
import re

logger = logging.getLogger(__name__)


class LinkedInScraper:
    """Scrapes LinkedIn company pages and career pages for public signals."""

    def __init__(self):
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
            "Accept-Encoding": "gzip, deflate",
            "Connection": "keep-alive",
        }

    def scrape_company_posts(self, company_domain: str) -> List[dict]:
        """
        Attempt to scrape public LinkedIn company posts.
        Returns list of post data dictionaries.
        """
        # Try LinkedIn company page
        company_slug = company_domain.replace("www.", "").replace("https://", "").replace("http://", "").split(".")[0]

        linkedin_urls = [
            f"https://www.linkedin.com/company/{company_slug}/posts/",
            f"https://www.linkedin.com/company/{company_slug}/about/",
        ]

        posts = []
        for url in linkedin_urls:
            try:
                result = self._scrape_url(url)
                if result:
                    posts.extend(result)
            except Exception as e:
                logger.warning(f"Failed to scrape {url}: {e}")
                continue

        return posts

    def scrape_careers_page(self, company_domain: str) -> dict:
        """
        Scrape company careers page for hiring signals.
        This is publicly accessible and reveals growth signals.
        """
        company_slug = company_domain.replace("www.", "").replace("https://", "").replace("http://", "").split(".")[0]

        careers_urls = [
            f"https://www.linkedin.com/company/{company_slug}/careers/",
            f"https://careers.{company_domain}",
            f"https://{company_domain}/careers",
        ]

        for url in careers_urls:
            try:
                result = self._scrape_careers(url)
                if result:
                    return result
            except Exception as e:
                logger.warning(f"Failed to scrape careers {url}: {e}")
                continue

        return {"hiring_active": False, "open_roles": 0, "departments": []}

    def _scrape_url(self, url: str) -> List[dict]:
        """Internal: scrape a URL and parse LinkedIn-style content."""
        with httpx.Client(timeout=10.0, headers=self.headers, follow_redirects=True) as client:
            response = client.get(url)
            if response.status_code != 200:
                return []

            soup = BeautifulSoup(response.text, "lxml")

            # LinkedIn company pages are JS-rendered, so we get minimal content
            # Look for any structured data
            posts = []

            # Try to find job listings on the careers page
            return self._scrape_careers(url)

    def _scrape_careers(self, url: str) -> Optional[dict]:
        """Parse careers page for hiring signals."""
        with httpx.Client(timeout=10.0, headers=self.headers, follow_redirects=True) as client:
            response = client.get(url)
            if response.status_code != 200:
                return None

            soup = BeautifulSoup(response.text, "lxml")
            text = soup.get_text()

            # Count job posting indicators
            job_keywords = ["hiring", "join us", "open position", "we're hiring", "careers", "job"]
            role_count = 0

            # Look for job role patterns
            role_patterns = [
                r"(\d+)\s*(?:open|active|new)\s*roles?",
                r"hiring\s*(\d+)",
                r"(\d+)\s*position",
            ]

            for pattern in role_patterns:
                matches = re.findall(pattern, text, re.IGNORECASE)
                for match in matches:
                    role_count = max(role_count, int(match) if match.isdigit() else 0)

            # Check if hiring is mentioned
            is_hiring = any(kw in text.lower() for kw in ["hiring", "we're hiring", "join our team"])

            # Extract departments hiring
            departments = []
            dept_keywords = ["engineering", "sales", "marketing", "product", "operations", "design", "data", "customer"]
            for dept in dept_keywords:
                if dept in text.lower():
                    departments.append(dept)

            return {
                "hiring_active": is_hiring or role_count > 0,
                "open_roles": role_count,
                "departments": list(set(departments)),
                "careers_url": url,
                "scraped_at": datetime.utcnow().isoformat(),
            }

    def scrape_linkedin_jobs(self, company_name: str) -> dict:
        """
        Scrape LinkedIn jobs page for a company.
        This gives us hiring intent signals.
        """
        search_query = company_name.replace(" ", "+")
        jobs_url = f"https://www.linkedin.com/jobs/search/?keywords={search_query}&location=United+States"

        try:
            with httpx.Client(timeout=10.0, headers=self.headers, follow_redirects=True) as client:
                response = client.get(jobs_url)
                if response.status_code != 200:
                    return {"job_count": 0, "roles": []}

                soup = BeautifulSoup(response.text, "lxml")

                # Count job cards (LinkedIn renders these dynamically)
                # Without JS rendering, we get minimal data
                job_count = 0
                roles = []

                # Try to find job titles
                job_titles = soup.find_all("span", {"class": "result-card__title"})
                for title in job_titles[:10]:
                    if title.get_text():
                        roles.append(title.get_text().strip())
                        job_count += 1

                return {
                    "job_count": job_count,
                    "roles": roles[:10],
                    "hiring_signal": job_count > 0,
                    "scraped_at": datetime.utcnow().isoformat(),
                }
        except Exception as e:
            logger.warning(f"Failed to scrape LinkedIn jobs for {company_name}: {e}")
            return {"job_count": 0, "roles": [], "error": str(e)}


def scrape_prospect_linkedin(prospect_data: dict) -> dict:
    """
    Main entry point: scrape all available LinkedIn signals for a prospect.
    Returns dict with posts, careers, and jobs data.
    """
    scraper = LinkedInScraper()
    company_domain = prospect_data.get("company_domain", "")
    company_name = prospect_data.get("company", "")
    linkedin_url = prospect_data.get("linkedin_url", "")

    result = {
        "posts": [],
        "careers": {},
        "jobs": {},
    }

    if company_domain:
        # Try careers page
        result["careers"] = scraper.scrape_careers_page(company_domain)

        # Try job listings
        if company_name:
            result["jobs"] = scraper.scrape_linkedin_jobs(company_name)

    if linkedin_url:
        # Try scraping from LinkedIn URL
        try:
            result["posts"] = scraper.scrape_company_posts(linkedin_url)
        except Exception as e:
            logger.warning(f"Failed to scrape LinkedIn URL {linkedin_url}: {e}")

    return result
