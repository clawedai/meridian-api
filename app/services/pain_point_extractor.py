"""
Module 01: Pain Point Extraction
Uses Claude AI to extract pain points, tools mentioned, and sentiment from posts.
Based on Playbook Step 2: Extract Pain Points with AI.
"""
import os
import json
import httpx
import logging
from typing import List, Optional

logger = logging.getLogger(__name__)

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"


def extract_pain_points_from_posts(posts: List[dict], prospect_name: str = "") -> List[dict]:
    """
    Extract pain points from LinkedIn/social posts using Claude AI.

    Based on Playbook Step 2:
    'Read these posts. List: (a) problems they mention,
    (b) tools they name, (c) goals they express. Be specific.'

    Args:
        posts: List of post dicts with 'post_text' and optional 'posted_at'
        prospect_name: Name of the prospect for context

    Returns:
        List of pain point dicts ready for the pain_points table
    """
    if not posts:
        return []

    if not ANTHROPIC_API_KEY:
        logger.warning("ANTHROPIC_API_KEY not set — skipping AI extraction")
        return _keyword_fallback(posts)

    # Prepare posts text
    posts_text = "\n\n".join([
        f"[Post {i+1}] {p.get('post_text', '')}"
        for i, p in enumerate(posts) if p.get("post_text")
    ])

    if not posts_text.strip():
        return []

    prompt = f"""You are a B2B sales intelligence analyst. Read the following social media posts and extract:

1. **Pain points** — specific problems or frustrations mentioned
2. **Tools mentioned** — software, platforms, or services named
3. **Goals expressed** — what they're trying to achieve
4. **Sentiment** — is the post positive, neutral, negative, or frustrated?

For each post, extract structured information. If a category is not present in a post, mark it as 'none'.

Respond ONLY with valid JSON in this exact format (no markdown, no explanation):
[
  {{
    "post_index": 0,
    "pain_category": "tool_frustration | process_pain | growth_blocker | none",
    "pain_description": "specific description or 'none'",
    "tools_mentioned": ["tool1", "tool2"] or [],
    "goals_expressed": ["goal1"] or [],
    "sentiment": "positive | neutral | negative | frustrated"
  }}
]

Posts to analyze:
{posts_text}"""

    try:
        response = _call_claude(prompt)
        if response:
            pain_points = json.loads(response)
            return pain_points
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse AI response: {e}")
    except Exception as e:
        logger.error(f"Claude API error: {e}")

    return _keyword_fallback(posts)


def score_sentiment(post_text: str) -> str:
    """
    Score sentiment of a single post.
    Based on Playbook Step 3: Score Sentiment on Every Post.
    Returns: positive | neutral | negative | frustrated
    """
    if not ANTHROPIC_API_KEY:
        return _sentiment_keyword(post_text)

    prompt = f"""Rate the sentiment of this social media post in exactly ONE word:

Posts:
{post_text}

Respond with ONLY one word: positive, neutral, negative, or frustrated. Nothing else."""

    try:
        response = _call_claude(prompt)
        if response:
            return response.strip().lower()
    except Exception as e:
        logger.warning(f"Sentiment scoring failed: {e}")

    return _sentiment_keyword(post_text)


def extract_technographics_from_content(content: str) -> List[dict]:
    """
    Extract mentioned tools/technologies from any text content.
    Used for technographic intelligence (Module 06).
    """
    if not content:
        return []

    if not ANTHROPIC_API_KEY:
        return _extract_tools_keyword(content)

    prompt = f"""Extract all software tools, platforms, and technologies mentioned in this text.

Text:
{content}

Respond ONLY with valid JSON array of tool names:
["Tool Name", "Another Tool", ...]

If no tools are found, respond with: []"""

    try:
        response = _call_claude(prompt)
        if response:
            tools = json.loads(response)
            return [{"tool_name": t, "category": _categorize_tool(t)} for t in tools if t]
    except (json.JSONDecodeError, Exception) as e:
        logger.warning(f"Technographic extraction failed: {e}")

    return _extract_tools_keyword(content)


def generate_personalized_email(
    prospect_name: str,
    company: str,
    signal_context: str,
    pain_point: str = "",
    tools_mentioned: List[str] = None
) -> dict:
    """
    Generate a personalized email opener from signal data.
    Based on Playbook Module 17: The Personalisation Engine.

    Args:
        prospect_name: First name or full name
        company: Company name
        signal_context: The specific signal (e.g., "raised Series A", "posted about hiring engineers")
        pain_point: Their expressed pain point (optional)
        tools_mentioned: Tools they mentioned (optional)

    Returns:
        dict with subject_line, first_line, full_email_body
    """
    if not ANTHROPIC_API_KEY:
        return {
            "subject_line": f"{signal_context} at {company}",
            "first_line": f"Hi {prospect_name}, congrats on {signal_context}!",
            "full_email_body": f"Hi {prospect_name}, I noticed {signal_context} at {company}.",
            "error": "AI not configured — using fallback"
        }

    prompt = f"""Write a personalized cold email opener for a B2B sales context.

Prospect: {prospect_name}
Company: {company}
Trigger signal: {signal_context}
Pain point they've mentioned: {pain_point or 'Not specified'}
Tools they use: {', '.join(tools_mentioned) if tools_mentioned else 'Not specified'}

Rules:
- First line must reference the specific signal
- Sound natural, not salesy
- 2 sentences maximum for the opener
- If pain point is available, connect the signal to the pain
- Do NOT mention competitor tools by name
- Do NOT say "I noticed" — find a more natural opening

Respond ONLY with valid JSON:
{{"subject_line": "email subject", "first_line": "natural opening sentence", "email_body": "full short email body (3-4 sentences max)"}}"""

    try:
        response = _call_claude(prompt)
        if response:
            result = json.loads(response)
            return result
    except (json.JSONDecodeError, Exception) as e:
        logger.error(f"Email generation failed: {e}")

    return {
        "subject_line": f"{signal_context} at {company}",
        "first_line": f"Hi {prospect_name}, congrats on {signal_context}!",
        "email_body": f"Hi {prospect_name}, I noticed {signal_context} at {company} and thought of you.",
    }


# ============================================================
# INTERNAL HELPERS
# ============================================================

def _call_claude(prompt: str, model: str = "claude-3-5-haiku-20241017") -> Optional[str]:
    """Make a call to Claude API."""
    headers = {
        "x-api-key": ANTHROPIC_API_KEY,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }

    data = {
        "model": model,
        "max_tokens": 1024,
        "messages": [{"role": "user", "content": prompt}]
    }

    with httpx.Client(timeout=30.0) as client:
        response = client.post(ANTHROPIC_API_URL, headers=headers, json=data)
        if response.status_code == 200:
            result = response.json()
            return result.get("content", [{}])[0].get("text", "")
        else:
            logger.warning(f"Claude API error: {response.status_code} {response.text}")
            return None


def _keyword_fallback(posts: List[dict]) -> List[dict]:
    """Fallback extraction when AI is not available."""
    results = []
    pain_keywords = ["frustrated", "mess", "broken", "waste", "hard", "difficult", "struggling", "problem", "slow", "pain"]
    tool_patterns = ["salesforce", "hubspot", "outreach", "apollo", "zoominfo", "slack", "notion", "jira", "tableau", "looker"]

    for i, post in enumerate(posts):
        text = (post.get("post_text") or "").lower()
        if not text:
            continue

        pain_points = []
        for kw in pain_keywords:
            if kw in text:
                pain_points.append(kw)

        tools = [t for t in tool_patterns if t in text]

        if pain_points or tools:
            results.append({
                "post_index": i,
                "pain_category": "tool_frustration" if tools else "process_pain",
                "pain_description": " | ".join(pain_points) if pain_points else "None identified",
                "tools_mentioned": tools,
                "goals_expressed": [],
                "sentiment": _sentiment_keyword(post.get("post_text", "")),
            })

    return results


def _sentiment_keyword(text: str) -> str:
    """Keyword-based sentiment fallback."""
    text = (text or "").lower()
    frustrated_kw = ["frustrated", "angry", "hate", "terrible", "worst", "useless", "broken"]
    negative_kw = ["slow", "problem", "issue", "difficult", "struggling", "challenging"]
    positive_kw = ["excited", "love", "great", "amazing", "awesome", "thrilled", "delighted", "congrats", "congratulations"]

    if any(kw in text for kw in frustrated_kw):
        return "frustrated"
    if any(kw in text for kw in negative_kw):
        return "negative"
    if any(kw in text for kw in positive_kw):
        return "positive"
    return "neutral"


def _categorize_tool(tool_name: str) -> str:
    """Categorize a tool into tech categories."""
    tool = tool_name.lower()
    categories = {
        "CRM": ["salesforce", "hubspot", "pipedrive", "zoho", "dynamics", "close.io", "freshsales"],
        "EMAIL": ["outreach", "mailshake", "apollo", "woodpecker", "mixmax", " Reply.io", "instantly"],
        "SALES_INTEL": ["zoominfo", "apollo", "6sense", "bombora", "clearbit", "hunter", "snov"],
        "MARKETING": ["marketo", "mailchimp", "activecampaign", "klaviyo", "hubspot marketing", "pardot"],
        "ANALYTICS": ["tableau", "looker", "mixpanel", "amplitude", "google analytics", "segment"],
        "REVIEW": ["g2", "capterra", "trustpilot", "getapp"],
    }

    for cat, tools in categories.items():
        if any(t in tool for t in tools):
            return cat

    return "OTHER"


def _extract_tools_keyword(content: str) -> List[dict]:
    """Keyword-based tool extraction fallback."""
    content = content.lower()
    known_tools = {
        "salesforce": "CRM",
        "hubspot": "CRM",
        "pipedrive": "CRM",
        "outreach": "EMAIL",
        "apollo": "SALES_INTEL",
        "zoominfo": "SALES_INTEL",
        "6sense": "SALES_INTEL",
        "bombora": "SALES_INTEL",
        "marketo": "MARKETING",
        "mailchimp": "MARKETING",
        "tableau": "ANALYTICS",
        "looker": "ANALYTICS",
        "mixpanel": "ANALYTICS",
        "segment": "ANALYTICS",
    }

    results = []
    for tool, category in known_tools.items():
        if tool in content:
            results.append({"tool_name": tool.title(), "category": category})

    return results
