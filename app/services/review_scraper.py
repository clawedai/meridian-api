"""
Module 12: Review Site Signals
Monitors G2 and Capterra for competitor review signals.
Based on Playbook Steps 28-29.
"""
import httpx
from bs4 import BeautifulSoup
import re
import logging
from typing import List, Optional
from datetime import datetime

logger = logging.getLogger(__name__)


def scrape_g2_reviews(competitor_name: str) -> List[dict]:
    """
    Scrape G2 reviews for a competitor.
    Based on Playbook Step 28.

    Looks for:
    - 1-3 star reviews (dissatisfied customers = your leads)
    - Switching intent signals
    - Specific complaints that Meridian solves
    """
    # G2 search URL
    search_url = f"https://www.g2.com/search?term={competitor_name.replace(' ', '+')}"

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
    }

    reviews = []

    try:
        with httpx.Client(timeout=10.0, headers=headers, follow_redirects=True) as client:
            response = client.get(search_url)

            if response.status_code != 200:
                logger.warning(f"G2 search failed: {response.status_code}")
                return reviews

            soup = BeautifulSoup(response.text, "lxml")

            # G2 is heavily JS-rendered, so we get limited data
            # Look for product links from search results
            product_links = soup.find_all("a", href=re.compile(r"/products/"))

            for link in product_links[:5]:
                product_name = link.get_text(strip=True)
                if competitor_name.lower() in product_name.lower():
                    # Try to get reviews from the product page
                    product_url = f"https://www.g2.com{link.get('href')}"
                    product_reviews = _scrape_g2_product_reviews(product_url, competitor_name)
                    reviews.extend(product_reviews)
                    break

    except Exception as e:
        logger.warning(f"G2 scraping failed for {competitor_name}: {e}")

    return reviews


def _scrape_g2_product_reviews(product_url: str, competitor_name: str) -> List[dict]:
    """Scrape reviews from a G2 product page."""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    }

    reviews = []
    try:
        with httpx.Client(timeout=10.0, headers=headers, follow_redirects=True) as client:
            response = client.get(product_url)
            if response.status_code != 200:
                return reviews

            soup = BeautifulSoup(response.text, "lxml")

            # Look for review cards (G2 renders these dynamically)
            review_cards = soup.find_all("article", {"class": re.compile(r"review")})

            for card in review_cards[:10]:
                rating_elem = card.find("span", {"class": re.compile(r"rating")})
                rating_text = rating_elem.get_text(strip=True) if rating_elem else "0"

                # Extract rating number
                rating_match = re.search(r"(\d+\.?\d*)", rating_text)
                rating = float(rating_match.group(1)) if rating_match else 0

                if rating > 3:
                    continue  # Only capture 1-3 star reviews

                review_text = ""
                text_elem = card.find("p")
                if text_elem:
                    review_text = text_elem.get_text(strip=True)[:500]

                # Check for switching intent keywords
                switching_intent = any(kw in review_text.lower() for kw in [
                    "looking for", "switching", "migrating", "alternative",
                    "switching from", "leaving", "replacing", "done with",
                    "searching for", "trying to find"
                ])

                # Extract pain mentioned
                pain_keywords = ["setup", "integration", "expensive", "slow", "complicated",
                                 "support", "bugs", "crashes", "missing", "doesn t"]
                pain_mentioned = "; ".join([kw for kw in pain_keywords if kw in review_text.lower()])

                reviews.append({
                    "competitor_name": competitor_name,
                    "review_platform": "G2",
                    "review_text": review_text,
                    "rating": int(rating),
                    "switching_intent": switching_intent,
                    "pain_mentioned": pain_mentioned,
                    "scraped_at": datetime.utcnow().isoformat(),
                })

    except Exception as e:
        logger.warning(f"Failed to scrape G2 reviews: {e}")

    return reviews


def scrape_capterra_reviews(competitor_name: str) -> List[dict]:
    """
    Scrape Capterra reviews for a competitor.
    Based on Playbook Step 29.
    """
    search_url = f"https://www.capterra.com/search/?query={competitor_name.replace(' ', '+')}"

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    }

    reviews = []
    try:
        with httpx.Client(timeout=10.0, headers=headers, follow_redirects=True) as client:
            response = client.get(search_url)
            if response.status_code != 200:
                return reviews

            soup = BeautifulSoup(response.text, "lxml")

            # Look for product listings
            product_links = soup.find_all("a", href=re.compile(r"/reviews/"))

            for link in product_links[:5]:
                product_name = link.get_text(strip=True)
                if competitor_name.lower()[:5] in product_name.lower()[:5]:
                    product_url = link.get("href")
                    if not product_url.startswith("http"):
                        product_url = f"https://www.capterra.com{product_url}"
                    product_reviews = _scrape_capterra_product_reviews(product_url, competitor_name)
                    reviews.extend(product_reviews)
                    break

    except Exception as e:
        logger.warning(f"Capterra scraping failed for {competitor_name}: {e}")

    return reviews


def _scrape_capterra_product_reviews(product_url: str, competitor_name: str) -> List[dict]:
    """Scrape reviews from a Capterra product page."""
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    reviews = []

    try:
        with httpx.Client(timeout=10.0, headers=headers, follow_redirects=True) as client:
            response = client.get(product_url)
            if response.status_code != 200:
                return reviews

            soup = BeautifulSoup(response.text, "lxml")

            # Look for review cards
            review_sections = soup.find_all("article", {"class": re.compile(r"review")})

            for section in review_sections[:10]:
                # Rating
                rating_elem = section.find("span", {"class": re.compile(r"rating")})
                rating_text = rating_elem.get_text(strip=True) if rating_elem else "0"
                rating_match = re.search(r"(\d)", rating_text)
                rating = int(rating_match.group(1)) if rating_match else 0

                if rating > 3:
                    continue

                # Review text
                text_elem = section.find("p")
                review_text = text_elem.get_text(strip=True)[:500] if text_elem else ""

                # Reviewer info
                role_elem = section.find("span", {"class": re.compile(r"role|job|title")})
                reviewer_role = role_elem.get_text(strip=True) if role_elem else ""

                # Switching intent
                switching_kw = ["looking for", "switching", "migrating", "alternative",
                               "trying to find", "replacing", "leaving"]
                switching_intent = any(kw in review_text.lower() for kw in switching_kw)

                reviews.append({
                    "competitor_name": competitor_name,
                    "review_platform": "Capterra",
                    "reviewer_role": reviewer_role,
                    "review_text": review_text,
                    "rating": rating,
                    "switching_intent": switching_intent,
                    "pain_mentioned": "",
                    "scraped_at": datetime.utcnow().isoformat(),
                })

    except Exception as e:
        logger.warning(f"Failed to scrape Capterra reviews: {e}")

    return reviews


def scrape_all_competitors(competitor_names: List[str]) -> List[dict]:
    """
    Master function: scrape reviews for all competitors.
    Returns combined review signals with switching intent flagged.
    """
    all_reviews = []

    for name in competitor_names:
        # Scrape both platforms
        g2_reviews = scrape_g2_reviews(name)
        capterra_reviews = scrape_capterra_reviews(name)

        for review in g2_reviews + capterra_reviews:
            review["competitor_name"] = name
            all_reviews.append(review)

    return all_reviews
