"""
Module 04: Financial & Company Signals
Detects funding rounds and hiring surges.
Based on Playbook Steps 10-11.
"""
import httpx
from bs4 import BeautifulSoup
import re
import logging
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)


# Key job titles that indicate tool evaluation (from playbook Step 11)
HIGH_INTENT_TITLES = [
    "revops", "revenue operations", "sales operations", "growth",
    "sales enablement", "sales ops", "business development",
    "chief revenue officer", "VP of Sales", "Head of Sales",
    "Director of Marketing", "Growth Lead", "Product Marketing",
]

# Funding indicators
FUNDING_KEYWORDS = [
    "raised", "funding", "series", "seed", "million", "billion",
    "investment", "invested", "secured", "closed", "undisclosed",
]

# Funding stage patterns
FUNDING_STAGES = {
    "pre-seed": ["pre-seed", "pre seed", "preseed"],
    "seed": ["seed", "round 1"],
    "series a": ["series a", "series-a", "round a"],
    "series b": ["series b", "series-b", "round b"],
    "series c": ["series c", "series c+", "round c"],
    "growth": ["growth", "late stage", "private equity"],
}


def detect_funding_round(company_name: str, content: str = "") -> Optional[dict]:
    """
    Detect if a company recently raised funding.
    Based on Playbook Step 10.

    Returns dict with funding details or None.
    """
    content_lower = content.lower()
    company_lower = company_name.lower()

    # Check if company is mentioned with funding keywords
    if not any(kw in content_lower for kw in FUNDING_KEYWORDS):
        return None

    # Check if our company is mentioned
    if company_lower not in content_lower and company_name:
        # Try partial match
        company_parts = company_lower.split()
        if not any(part in content_lower for part in company_parts if len(part) > 3):
            return None

    # Extract funding amount
    amount = None
    amount_patterns = [
        r"\$([0-9]+)\s*(?:million|billion|M|B)?",
        r"([0-9]+)\s*(?:million|billion)\s*(?:dollar)?",
        r"raised\s+\$([0-9]+)",
        r"\$([0-9]+)\s*round",
    ]

    for pattern in amount_patterns:
        matches = re.findall(pattern, content_lower)
        for match in matches:
            num = int(match)
            if num < 100:
                amount = f"${num}M"
            elif num < 1000:
                amount = f"${num}M"
            elif num >= 1000:
                amount = f"${num // 1000}B"
            else:
                amount = f"${num}"
            break
        if amount:
            break

    # Detect stage
    stage = None
    for stage_name, keywords in FUNDING_STAGES.items():
        if any(kw in content_lower for kw in keywords):
            stage = stage_name
            break

    if not amount and not stage:
        return None

    return {
        "company_name": company_name,
        "funding_amount": amount,
        "funding_stage": stage or "undisclosed",
        "announced_date": datetime.utcnow().isoformat(),
        "source_url": "",
        "intent_score_boost": 40 if stage in ["seed", "series a", "series b"] else 25,
    }


def detect_hiring_surge(
    company_name: str,
    job_count: int = 0,
    job_roles: list = None,
    careers_data: dict = None
) -> Optional[dict]:
    """
    Detect hiring signals from job postings.
    Based on Playbook Step 11.

    Returns dict with hiring details or None.
    """
    if job_count == 0 and not (careers_data and careers_data.get("hiring_active")):
        return None

    roles = job_roles or []
    departments = []

    if careers_data:
        departments = careers_data.get("departments", [])
        if careers_data.get("open_roles", 0) > 0:
            job_count = careers_data["open_roles"]

    # Check for high-intent roles
    high_intent_count = 0
    for role in roles:
        role_lower = role.lower()
        if any(title in role_lower for title in HIGH_INTENT_TITLES):
            high_intent_count += 1

    # Check departments for revops/sales/marketing
    sales_depts = ["sales", "marketing", "revenue", "growth", "business development"]
    is_growth_hiring = any(d in departments for d in sales_depts) if departments else False

    return {
        "company_name": company_name,
        "job_count": job_count,
        "high_intent_roles": high_intent_count,
        "departments": departments,
        "is_growth_hiring": is_growth_hiring,
        "intent_score_boost": 20 if is_growth_hiring or high_intent_count > 0 else 10,
        "scraped_at": datetime.utcnow().isoformat(),
    }


def check_linkedin_jobs(company_name: str) -> dict:
    """
    Check LinkedIn for company job postings.
    Publicly accessible without login for basic search.
    """
    search_query = company_name.replace(" ", "%20")
    url = f"https://www.linkedin.com/jobs/search/?keywords={search_query}&location=United%20States&f_TPR=r86400"

    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "text/html,application/xhtml+xml",
        }

        with httpx.Client(timeout=10.0, headers=headers, follow_redirects=True) as client:
            response = client.get(url)

            if response.status_code != 200:
                return {"job_count": 0, "jobs": []}

            soup = BeautifulSoup(response.text, "lxml")

            # Count visible job listings
            job_cards = soup.find_all("li", {"class": re.compile(r"result-card")})
            jobs = []

            for card in job_cards[:15]:
                title_elem = card.find("span", {"class": re.compile(r"result-card__title")})
                company_elem = card.find("a", {"class": re.compile(r"result-card__subtitle")})

                if title_elem:
                    title = title_elem.get_text(strip=True)
                    company = company_elem.get_text(strip=True) if company_elem else ""
                    jobs.append({
                        "title": title,
                        "company": company,
                    })

            return {
                "job_count": len(jobs),
                "jobs": jobs,
                "url": url,
            }
    except Exception as e:
        logger.warning(f"LinkedIn jobs check failed for {company_name}: {e}")
        return {"job_count": 0, "jobs": [], "error": str(e)}


def check_theirstack(company_domain: str, job_title_filter: str = "RevOps") -> dict:
    """
    Check TheirStack for job posting signals.
    theirstack.com aggregates tech job postings.

    Note: Requires API key for full access. Free tier is limited.
    """
    # TheirStack public search (limited)
    search_query = job_title_filter.replace(" ", "%20")
    url = f"https://www.theirstack.com/jobs?title={search_query}"

    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        }

        with httpx.Client(timeout=10.0, headers=headers, follow_redirects=True) as client:
            response = client.get(url)
            # TheirStack is likely JS-rendered, so we get minimal data
            return {"jobs_found": 0, "note": "TheirStack requires JS rendering"}
    except Exception as e:
        return {"jobs_found": 0, "error": str(e)}
