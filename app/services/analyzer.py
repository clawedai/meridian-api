import os
import httpx
from typing import Dict, Any, List, Optional
from datetime import datetime
import json

class ClaudeAnalyzer:
    """AI-powered content analyzer using Claude API"""

    def __init__(self):
        self.api_key = os.getenv("ANTHROPIC_API_KEY") or os.getenv("CLAUDE_API_KEY")
        self.api_url = "https://api.anthropic.com/v1/messages"
        self.model = "claude-sonnet-4-20250514"  # Cost-effective for analysis
        self.max_tokens = 1024

    async def analyze(self, content: str, context: Dict[str, Any]) -> Dict[str, Any]:
        """Analyze content and generate insights"""

        if not self.api_key:
            return self._fallback_analysis(content, context)

        prompt = self._build_analysis_prompt(content, context)

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
                        "messages": [
                            {
                                "role": "user",
                                "content": prompt
                            }
                        ]
                    }
                )

                if response.status_code == 200:
                    data = response.json()
                    analysis_text = data["content"][0]["text"]
                    return self._parse_analysis(analysis_text, context)
                else:
                    return self._fallback_analysis(content, context)

        except Exception as e:
            print(f"Claude API error: {e}")
            return self._fallback_analysis(content, context)

    def _build_analysis_prompt(self, content: str, context: Dict[str, Any]) -> str:
        """Build analysis prompt for Claude"""

        entity_name = context.get("entity_name", "Unknown Entity")
        source_type = context.get("source_type", "web")

        prompt = f"""Analyze the following content from {entity_name} ({source_type}) and provide structured insights.

CONTENT:
{content[:4000]}

Provide your analysis in this exact JSON format:
{{
    "insight_type": "funding|product|hiring|pr|leadership|partnership|anomaly|summary",
    "title": "Brief, actionable title for this insight",
    "summary": "2-3 sentence summary of what this means",
    "importance": "critical|high|medium|low",
    "confidence": 0.0-1.0,
    "key_points": ["Point 1", "Point 2", "Point 3"],
    "actionable": "What you should do about this"
}}

Be specific and actionable. If the content doesn't contain notable insights, return "summary" type with importance "low"."""

        return prompt

    def _parse_analysis(self, analysis_text: str, context: Dict[str, Any]) -> Dict[str, Any]:
        """Parse Claude's analysis response"""

        try:
            # Try to extract JSON from response
            # Claude sometimes wraps JSON in markdown
            json_str = analysis_text
            if "```json" in analysis_text:
                json_str = analysis_text.split("```json")[1].split("```")[0]
            elif "```" in analysis_text:
                json_str = analysis_text.split("```")[1].split("```")[0]

            analysis = json.loads(json_str.strip())

            return {
                "insight_type": analysis.get("insight_type", "summary"),
                "title": analysis.get("title", "Analysis Summary"),
                "content": analysis.get("summary", ""),
                "summary": analysis.get("summary", ""),
                "importance": analysis.get("importance", "medium"),
                "confidence": analysis.get("confidence", 0.5),
                "key_points": analysis.get("key_points", []),
                "actionable": analysis.get("actionable", ""),
                "entity_id": context.get("entity_id"),
                "generated_at": datetime.utcnow().isoformat(),
            }

        except json.JSONDecodeError:
            return self._fallback_analysis(context.get("raw_content", ""), context)

    def _fallback_analysis(self, content: str, context: Dict[str, Any]) -> Dict[str, Any]:
        """Fallback analysis when Claude API is unavailable"""

        # Simple keyword-based analysis
        content_lower = content.lower()

        insight_type = "summary"
        importance = "low"
        confidence = 0.5

        # Keyword detection
        keywords_map = {
            "funding": ["raised", "series", "investment", "funding", "million", "billion"],
            "product": ["launch", "release", "new feature", "update", "announce"],
            "hiring": ["hiring", "job opening", "recruit", "cto", "vp of"],
            "pr": ["press release", "featured in", "interview", "article"],
            "leadership": ["ceo", "founder", "appoint", "executive"],
            "partnership": ["partnered", "collaboration", "integration"],
        }

        for itype, keywords in keywords_map.items():
            if any(kw in content_lower for kw in keywords):
                insight_type = itype
                importance = "medium" if itype in ["funding", "product"] else "low"
                confidence = 0.7
                break

        # Generate title from content
        title = context.get("title", "Content Update")
        if len(title) > 100:
            title = title[:100] + "..."

        return {
            "insight_type": insight_type,
            "title": title,
            "content": content[:1000] if len(content) > 1000 else content,
            "summary": f"New {insight_type} activity detected for {context.get('entity_name', 'entity')}",
            "importance": importance,
            "confidence": confidence,
            "entity_id": context.get("entity_id"),
            "generated_at": datetime.utcnow().isoformat(),
        }

class InsightOrchestrator:
    """Orchestrates the full insight generation pipeline"""

    def __init__(self):
        self.analyzer = ClaudeAnalyzer()

    async def process_content(
        self,
        content: Dict[str, Any],
        entity_id: str,
        entity_name: str,
        source_ids: List[str]
    ) -> Optional[Dict[str, Any]]:
        """Process raw content into structured insights"""

        # Get raw content to analyze
        raw_content = ""
        if content.get("type") == "rss":
            entries = content.get("entries", [])
            if entries:
                raw_content = "\n\n".join([
                    f"{e.get('title', '')}: {e.get('content', '')}"
                    for e in entries[:5]
                ])
        else:
            raw_content = content.get("content", "")

        if not raw_content:
            return None

        context = {
            "entity_id": entity_id,
            "entity_name": entity_name,
            "source_type": content.get("type", "web"),
            "title": content.get("title", ""),
            "raw_content": raw_content,
            "source_ids": source_ids,
        }

        return await self.analyzer.analyze(raw_content, context)
