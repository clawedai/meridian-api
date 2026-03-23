"""
Module 06: Technographic Intelligence
Detects what tools/technology a company uses on their website.
Based on Playbook Steps 16-17.
"""
import httpx
from bs4 import BeautifulSoup
import logging
import re
from typing import List, Optional
from datetime import datetime

logger = logging.getLogger(__name__)


# Comprehensive tech stack signatures
# Maps tool names to their detection patterns (JS files, meta tags, etc.)
TECH_SIGNATURES = {
    # CRM
    "Salesforce": {
        "js_patterns": ["salesforce", "sfdc", "SFDC", "/sfdc/"],
        "meta_patterns": [],
    },
    "HubSpot": {
        "js_patterns": ["hubspot", "hs-analytics", "hsforms", "//js.hs-scripts.com", "hs-analytics"],
        "meta_patterns": ["hubspot"],
    },
    "Pipedrive": {
        "js_patterns": ["pipedrive", "//cdn.pipedriveassets.com"],
        "meta_patterns": [],
    },
    "Zoho CRM": {
        "js_patterns": ["zoho", "//crm.zoho.com"],
        "meta_patterns": [],
    },
    "Close": {
        "js_patterns": ["close.com", "close.io"],
        "meta_patterns": [],
    },

    # Email / Outreach
    "Outreach": {
        "js_patterns": ["outreach.io", "//outreach.io"],
        "meta_patterns": [],
    },
    "Apollo": {
        "js_patterns": ["apollo.io", "//apollo.io"],
        "meta_patterns": [],
    },
    "Mailchimp": {
        "js_patterns": ["mailchimp", "//cdn MC", "chimpstatic"],
        "meta_patterns": ["mailchimp"],
    },
    "Marketo": {
        "js_patterns": ["marketo", "munchkin", "//munchkin.marketo.net"],
        "meta_patterns": ["marketo"],
    },
    "Klaviyo": {
        "js_patterns": ["klaviyo", "//static.klaviyo.com"],
        "meta_patterns": [],
    },
    "SendGrid": {
        "js_patterns": ["sendgrid", "//sg-cdn"],
        "meta_patterns": [],
    },
    "Instantly": {
        "js_patterns": ["instantly.ai"],
        "meta_patterns": [],
    },

    # Sales Intelligence (COMPETITORS)
    "ZoomInfo": {
        "js_patterns": ["zoominfo", "iris.zoominfo"],
        "meta_patterns": [],
    },
    "6sense": {
        "js_patterns": ["6sense", "//6sense.com"],
        "meta_patterns": [],
    },
    "Bombora": {
        "js_patterns": ["bombora", "//d2c3.ton朝鲜"],
        "meta_patterns": [],
    },
    "Clearbit": {
        "js_patterns": ["clearbit", "//clearbit.com"],
        "meta_patterns": [],
    },
    "Hunter.io": {
        "js_patterns": ["hunter.io", "//hunter.io"],
        "meta_patterns": [],
    },

    # Analytics
    "Google Analytics": {
        "js_patterns": ["google-analytics", "gtag(", "analytics.js", "/ga.js"],
        "meta_patterns": [],
    },
    "Mixpanel": {
        "js_patterns": ["mixpanel", "//cdn.mxpnl.com"],
        "meta_patterns": [],
    },
    "Amplitude": {
        "js_patterns": ["amplitude", "//cdn.amplitude.com"],
        "meta_patterns": [],
    },
    "Segment": {
        "js_patterns": ["segment.io", "//segment.com", "segment.writeKey"],
        "meta_patterns": [],
    },
    "Heap": {
        "js_patterns": ["heap.io", "//heap.io"],
        "meta_patterns": [],
    },

    # Marketing
    "ActiveCampaign": {
        "js_patterns": ["activecampaign", "//activecampaign.com"],
        "meta_patterns": [],
    },
    "Intercom": {
        "js_patterns": ["intercom", "//widget.intercom.io", "//js.intercomcdn"],
        "meta_patterns": [],
    },
    "Drift": {
        "js_patterns": ["drift", "//js.driftt.com"],
        "meta_patterns": [],
    },
    "Hotjar": {
        "js_patterns": ["hotjar", "//static.hotjar.com"],
        "meta_patterns": [],
    },
    "Crisp": {
        "js_patterns": ["crisp.chat", "//client.crisp.chat"],
        "meta_patterns": [],
    },

    # Review / Social Proof
    "G2": {
        "js_patterns": ["g2.com", "//www.g2.com"],
        "meta_patterns": [],
    },
    "Capterra": {
        "js_patterns": ["capterra", "//www.capterra.com"],
        "meta_patterns": [],
    },

    # Chat / Support
    "Zendesk": {
        "js_patterns": ["zendesk", "//static.zdassets.com", "zdassets"],
        "meta_patterns": [],
    },
    "Intercom": {
        "js_patterns": ["intercom", "//widget.intercom.io"],
        "meta_patterns": [],
    },
    "LiveChat": {
        "js_patterns": ["livechat", "//cdn.livechatinc.com"],
        "meta_patterns": [],
    },

    # Payment
    "Stripe": {
        "js_patterns": ["stripe.com", "//js.stripe.com", "checkout.stripe.com"],
        "meta_patterns": [],
    },
    "PayPal": {
        "js_patterns": ["paypal.com", "//paypal.me"],
        "meta_patterns": [],
    },

    # CMS
    "WordPress": {
        "js_patterns": ["wp-content", "wp-includes", "/wp-json/"],
        "meta_patterns": ["wordpress"],
    },
    "Webflow": {
        "js_patterns": ["webflow.io", "//d3e6vo.com"],
        "meta_patterns": ["webflow"],
    },
    "Framer": {
        "js_patterns": ["framer.app", "//framer.app"],
        "meta_patterns": [],
    },
    "Shopify": {
        "js_patterns": ["shopify", "//cdn.shopify.com"],
        "meta_patterns": ["shopify"],
    },

    # Productivity
    "Slack": {
        "js_patterns": ["slack.com", "slack.btn", "//a.slack-edge.com"],
        "meta_patterns": [],
    },
    "Notion": {
        "js_patterns": ["notion.so", "//www.notion.so"],
        "meta_patterns": [],
    },
    "Jira": {
        "js_patterns": ["jira", "atlassian", "//jira.atlassian.com"],
        "meta_patterns": [],
    },
}


# Tool categories
TOOL_CATEGORIES = {
    "CRM": ["Salesforce", "HubSpot", "Pipedrive", "Zoho CRM", "Close"],
    "EMAIL": ["Outreach", "Apollo", "Mailchimp", "Marketo", "Klaviyo", "SendGrid", "Instantly"],
    "SALES_INTEL": ["ZoomInfo", "6sense", "Bombora", "Clearbit", "Hunter.io"],
    "ANALYTICS": ["Google Analytics", "Mixpanel", "Amplitude", "Segment", "Heap"],
    "MARKETING": ["ActiveCampaign", "Intercom", "Drift", "Hotjar", "Crisp"],
    "REVIEW": ["G2", "Capterra"],
    "SUPPORT": ["Zendesk", "Intercom", "LiveChat"],
    "PAYMENT": ["Stripe", "PayPal"],
    "CMS": ["WordPress", "Webflow", "Framer", "Shopify"],
    "PRODUCTIVITY": ["Slack", "Notion", "Jira"],
}

# Competitor tools (for checking if they already have sales intel)
COMPETITOR_TOOLS = ["ZoomInfo", "6sense", "Bombora", "Clearbit", "Apollo", "Hunter.io"]


def detect_technographics(company_domain: str) -> dict:
    """
    Detect the tech stack of a company from their website.
    Based on Playbook Steps 16-17.

    Returns:
        dict with detected tools, categories, and gap analysis
    """
    if not company_domain:
        return {"tools": [], "categories": [], "has_crm": False, "has_sales_intel": False}

    # Normalize URL
    if not company_domain.startswith("http"):
        url = f"https://{company_domain}"
    else:
        url = company_domain

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
    }

    try:
        with httpx.Client(timeout=15.0, headers=headers, follow_redirects=True) as client:
            response = client.get(url)
            if response.status_code != 200:
                return {"tools": [], "categories": [], "has_crm": False, "has_sales_intel": False}

            text = response.text
            soup = BeautifulSoup(text, "lxml")
            html = soup.get_text()

            detected_tools = []
            detected_categories = set()
            competitor_tools_found = []

            # Check each tech signature
            for tool_name, patterns in TECH_SIGNATURES.items():
                found = False

                # Check JS patterns
                for pattern in patterns.get("js_patterns", []):
                    if pattern in text or pattern in html:
                        found = True
                        break

                if found:
                    # Categorize the tool
                    category = _get_tool_category(tool_name)
                    detected_tools.append({
                        "tool_name": tool_name,
                        "tool_category": category,
                        "is_competitor_tool": tool_name in COMPETITOR_TOOLS,
                    })
                    detected_categories.add(category)

                    if tool_name in COMPETITOR_TOOLS:
                        competitor_tools_found.append(tool_name)

            # Check for CRM tools
            crm_cats = TOOL_CATEGORIES.get("CRM", [])
            has_crm = any(t["tool_name"] in crm_cats for t in detected_tools)

            # Check for sales intelligence tools
            sales_intel_cats = TOOL_CATEGORIES.get("SALES_INTEL", [])
            has_sales_intel = any(t["tool_name"] in sales_intel_cats for t in detected_tools)

            # Gap analysis (playbook Step 17)
            gap_analysis = None
            if has_crm and not has_sales_intel:
                gap_analysis = "HIGH_FIT: Has CRM but no sales intelligence tool — perfect Meridian prospect"

            return {
                "company_domain": company_domain,
                "tools": detected_tools,
                "categories": list(detected_categories),
                "has_crm": has_crm,
                "has_sales_intel": has_sales_intel,
                "competitor_tools_found": competitor_tools_found,
                "gap_analysis": gap_analysis,
                "scraped_at": datetime.utcnow().isoformat(),
            }

    except Exception as e:
        logger.warning(f"Technographic detection failed for {company_domain}: {e}")
        return {"tools": [], "categories": [], "error": str(e)}


def _get_tool_category(tool_name: str) -> str:
    """Get the category for a detected tool."""
    for category, tools in TOOL_CATEGORIES.items():
        if tool_name in tools:
            return category
    return "OTHER"


def check_technographic_gap(technographics: List[dict]) -> dict:
    """
    Analyze technographic data to identify ICP fit.
    Based on Playbook Step 17.

    Returns gap analysis and prospect quality score.
    """
    if not technographics:
        return {"fit": "unknown", "score": 0, "recommendation": "No tech data — manual research needed"}

    tools = [t.get("tool_name", "").lower() for t in technographics]
    categories = set(t.get("tool_category", "") for t in technographics)

    has_crm = any(cat == "CRM" for cat in categories)
    has_sales_intel = any(cat == "SALES_INTEL" for cat in categories)
    has_analytics = any(cat == "ANALYTICS" for cat in categories)
    competitor_tools = [t.get("tool_name") for t in technographics if t.get("is_competitor_tool")]

    # Score based on ICP fit
    score = 0
    fit = "low"

    if has_crm and not has_sales_intel:
        # Perfect ICP: has budget for CRM, needs sales intel
        score += 40
        fit = "high"

    if has_analytics:
        score += 20  # Data-aware company

    if has_crm:
        score += 20  # Proven tool buyer

    if competitor_tools:
        score -= 30  # Already using competitor
        fit = "low"

    if score >= 50:
        fit = "high"
    elif score >= 30:
        fit = "medium"

    return {
        "fit": fit,
        "score": min(100, score),
        "has_crm": has_crm,
        "has_sales_intel": has_sales_intel,
        "competitor_tools": competitor_tools,
        "recommendation": _get_recommendation(fit, has_crm, has_sales_intel, competitor_tools),
    }


def _get_recommendation(fit: str, has_crm: bool, has_sales_intel: bool, competitor_tools: list) -> str:
    """Generate recommendation based on technographic analysis."""
    if competitor_tools:
        return f"LOW FIT: Already using {'/'.join(competitor_tools)}. Find unserved prospects first."

    if has_crm and not has_sales_intel:
        return "HIGH FIT: CRM user with no sales intel — prime Meridian prospect."

    if not has_crm:
        return "MEDIUM FIT: No CRM detected — may not be a tool buyer yet."

    return "MEDIUM FIT: Already sophisticated stack — position against current tools."
