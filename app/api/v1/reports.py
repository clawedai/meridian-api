from fastapi import APIRouter, Depends, HTTPException, Query, BackgroundTasks
from typing import List, Optional
from datetime import datetime, timedelta
from collections import Counter
import httpx
from ..deps import get_current_user, get_supabase, SupabaseClient
from ...schemas.entity import Report, ReportCreate, ReportStatus, ReportType

router = APIRouter(prefix="/reports", tags=["Reports"])


# ============================================================================
# Report Generation Functions
# ============================================================================

async def _fetch_insights_for_entity(
    supabase_url: str,
    anon_key: str,
    entity_id: str,
    user_id: str,
    date_from: str,
    date_to: str
) -> List[dict]:
    """Fetch insights for a specific entity within a date range"""
    url = (
        f"{supabase_url}/rest/v1/insights"
        f"?entity_id=eq.{entity_id}"
        f"&user_id=eq.{user_id}"
        f"&generated_at=gte.{date_from}"
        f"&generated_at=lte.{date_to}"
        f"&is_archived=eq.false"
        f"&order=generated_at.desc"
        f"&select=*"
    )
    headers = {
        "apikey": anon_key,
        "Authorization": f"Bearer {anon_key}",
    }

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(url, headers=headers)
            response.raise_for_status()
            return response.json()
    except httpx.HTTPStatusError as e:
        print(f"HTTP error fetching insights for entity {entity_id}: {e.response.status_code} - {e.response.text}")
    except httpx.RequestError as e:
        print(f"Request error fetching insights for entity {entity_id}: {e}")
    return []


async def _fetch_sources_for_entity(
    supabase_url: str,
    anon_key: str,
    entity_id: str,
    user_id: str
) -> List[dict]:
    """Fetch sources for an entity"""
    url = (
        f"{supabase_url}/rest/v1/sources"
        f"?entity_id=eq.{entity_id}"
        f"&user_id=eq.{user_id}"
        f"&is_active=eq.true"
        f"&select=*"
    )
    headers = {
        "apikey": anon_key,
        "Authorization": f"Bearer {anon_key}",
    }

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(url, headers=headers)
            response.raise_for_status()
            return response.json()
    except httpx.HTTPStatusError as e:
        print(f"HTTP error fetching sources for entity {entity_id}: {e.response.status_code} - {e.response.text}")
    except httpx.RequestError as e:
        print(f"Request error fetching sources for entity {entity_id}: {e}")
    return []


def _analyze_trends(insights: List[dict]) -> dict:
    """Analyze insights to identify trends"""
    if not insights:
        return {"trend_summary": "No data available for trend analysis.", "trend_direction": "neutral"}

    # Group by insight type
    type_counts = Counter(i.get("insight_type", "unknown") for i in insights)

    # Group by importance
    importance_counts = Counter(i.get("importance", "medium") for i in insights)

    # Calculate average confidence
    confidences = [i.get("confidence", 0) for i in insights if i.get("confidence")]
    avg_confidence = sum(confidences) / len(confidences) if confidences else 0

    # Determine trend direction based on importance distribution
    critical_high = importance_counts.get("critical", 0) + importance_counts.get("high", 0)
    low_medium = importance_counts.get("low", 0) + importance_counts.get("medium", 0)

    if critical_high > low_medium:
        trend_direction = "increasing"
    elif low_medium > critical_high * 2:
        trend_direction = "stable"
    else:
        trend_direction = "mixed"

    # Generate trend summary
    top_types = type_counts.most_common(3)
    top_types_str = ", ".join([f"{t[1]} {t[0]}" for t in top_types])

    trend_summary = f"Detected {len(insights)} insights across {len(type_counts)} categories. "
    trend_summary += f"Top categories: {top_types_str}. "
    trend_summary += f"Average confidence: {avg_confidence:.1%}."

    return {
        "trend_summary": trend_summary,
        "trend_direction": trend_direction,
        "total_insights": len(insights),
        "insights_by_type": dict(type_counts),
        "insights_by_importance": dict(importance_counts),
        "average_confidence": round(avg_confidence, 3),
    }


def _extract_key_findings(insights: List[dict]) -> List[dict]:
    """Extract the most important findings from insights"""
    importance_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}

    def get_importance_weight(insight):
        imp = insight.get("importance", "medium").lower()
        conf = insight.get("confidence", 0.5)
        order = importance_order.get(imp, 2)
        return order - conf

    sorted_insights = sorted(insights, key=get_importance_weight)
    top_insights = sorted_insights[:10]

    findings = []
    for insight in top_insights:
        findings.append({
            "title": insight.get("title", ""),
            "type": insight.get("insight_type", "summary"),
            "importance": insight.get("importance", "medium"),
            "summary": insight.get("summary") or insight.get("content", "")[:300],
            "confidence": insight.get("confidence", 0),
            "source_count": len(insight.get("source_ids", [])),
        })

    return findings


def _generate_summary_statistics(
    insights: List[dict],
    sources: List[dict],
    entity_name: str,
    date_from: str,
    date_to: str
) -> dict:
    """Generate summary statistics for the report"""
    if not insights and not sources:
        return {
            "entity_name": entity_name,
            "period": f"{date_from[:10]} to {date_to[:10]}",
            "insights_count": 0,
            "sources_monitored": 0,
            "critical_findings": 0,
            "high_findings": 0,
            "avg_confidence": 0,
            "coverage_percentage": 0,
        }

    critical_count = sum(1 for i in insights if i.get("importance") == "critical")
    high_count = sum(1 for i in insights if i.get("importance") == "high")
    medium_count = sum(1 for i in insights if i.get("importance") == "medium")
    low_count = sum(1 for i in insights if i.get("importance") == "low")

    confidences = [i.get("confidence", 0) for i in insights if i.get("confidence")]
    avg_confidence = sum(confidences) / len(confidences) if confidences else 0

    active_sources = sum(1 for s in sources if s.get("is_active", True))
    sources_with_fetch = sum(1 for s in sources if s.get("fetch_count", 0) > 0)

    return {
        "entity_name": entity_name,
        "period": f"{date_from[:10]} to {date_to[:10]}",
        "insights_count": len(insights),
        "sources_monitored": len(sources),
        "active_sources": active_sources,
        "sources_with_data": sources_with_fetch,
        "critical_findings": critical_count,
        "high_findings": high_count,
        "medium_findings": medium_count,
        "low_findings": low_count,
        "avg_confidence": round(avg_confidence, 3),
        "coverage_percentage": round((sources_with_fetch / len(sources) * 100) if sources else 0, 1),
    }


async def _get_entity_name(supabase_url: str, anon_key: str, entity_id: str, user_id: str) -> str:
    """Get entity name by ID"""
    url = f"{supabase_url}/rest/v1/entities?id=eq.{entity_id}&user_id=eq.{user_id}&select=name"
    headers = {"apikey": anon_key, "Authorization": f"Bearer {anon_key}"}

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(url, headers=headers)
            response.raise_for_status()
            data = response.json()
            if data:
                return data[0].get("name", "Unknown Entity")
    except httpx.HTTPStatusError as e:
        print(f"HTTP error fetching entity name: {e.response.status_code} - {e.response.text}")
    except httpx.RequestError as e:
        print(f"Request error fetching entity name: {e}")
    return "Unknown Entity"


async def generate_report_content(
    report_id: str,
    supabase_url: str,
    anon_key: str,
    user_id: str,
    entity_ids: List[str],
    date_from: str,
    date_to: str,
    report_type: str
) -> dict:
    """
    Generate the actual report content asynchronously.
    This function performs the heavy lifting of collecting and analyzing data.
    """
    try:
        all_insights = []
        all_sources = []
        entity_summaries = {}

        for entity_id in entity_ids:
            entity_name = await _get_entity_name(supabase_url, anon_key, entity_id, user_id)

            insights = await _fetch_insights_for_entity(
                supabase_url, anon_key, entity_id, user_id, date_from, date_to
            )
            all_insights.extend(insights)

            sources = await _fetch_sources_for_entity(
                supabase_url, anon_key, entity_id, user_id
            )
            all_sources.extend(sources)

            entity_summaries[entity_id] = {
                "entity_id": entity_id,
                "entity_name": entity_name,
                "insights_count": len(insights),
                "sources_count": len(sources),
                "summary_stats": _generate_summary_statistics(
                    insights, sources, entity_name, date_from, date_to
                ),
                "trends": _analyze_trends(insights),
                "key_findings": _extract_key_findings(insights),
            }

        report_content = {
            "report_type": report_type,
            "generated_at": datetime.utcnow().isoformat(),
            "date_range": {
                "from": date_from,
                "to": date_to,
            },
            "summary": {
                "total_insights": len(all_insights),
                "total_entities": len(entity_ids),
                "total_sources": len(all_sources),
                "overall_trends": _analyze_trends(all_insights),
                "overall_stats": _generate_summary_statistics(
                    all_insights, all_sources, "All Entities", date_from, date_to
                ),
            },
            "entity_reports": entity_summaries,
            "key_findings": _extract_key_findings(all_insights),
            "metadata": {
                "report_id": report_id,
                "user_id": user_id,
                "generated_by": "Almanac Intelligence Platform",
                "version": "1.0",
            },
        }

        update_url = f"{supabase_url}/rest/v1/reports?id=eq.{report_id}&user_id=eq.{user_id}"
        headers = {
            "apikey": anon_key,
            "Authorization": f"Bearer {anon_key}",
            "Content-Type": "application/json",
            "Prefer": "return=representation",
        }

        update_data = {
            "status": "ready",
            "content": report_content,
            "generated_at": datetime.utcnow().isoformat(),
        }

        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.patch(update_url, json=update_data, headers=headers)
            response.raise_for_status()
            return {"success": True, "report_id": report_id}

    except Exception as e:
        print(f"Error generating report {report_id}: {e}")
        try:
            update_url = f"{supabase_url}/rest/v1/reports?id=eq.{report_id}&user_id=eq.{user_id}"
            headers = {
                "apikey": anon_key,
                "Authorization": f"Bearer {anon_key}",
                "Content-Type": "application/json",
            }
            update_data = {"status": "failed"}
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.patch(update_url, json=update_data, headers=headers)
                response.raise_for_status()
        except httpx.HTTPStatusError as e:
            print(f"HTTP error updating report status to failed: {e.response.status_code} - {e.response.text}")
        except httpx.RequestError as e:
            print(f"Request error updating report status to failed: {e}")
        except Exception as inner_e:
            print(f"Error updating report status to failed: {inner_e}")
        return {"success": False, "error": str(e)}


# ============================================================================
# API Endpoints
# ============================================================================

@router.get("", response_model=List[Report])
async def list_reports(
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    current_user: dict = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_supabase)
):
    """List all reports for current user"""
    try:
        user_id = current_user["id"]
        headers = supabase._get_headers()
        query = f"user_id=eq.{user_id}&order=created_at.desc&limit={limit}&offset={skip}"
        url = f"{supabase.url}/rest/v1/reports?{query}"

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(url, headers=headers)
            response.raise_for_status()
            return response.json()
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail=f"Failed to list reports: {e.response.text}")
    except httpx.RequestError as e:
        raise HTTPException(status_code=503, detail=f"Service unavailable: {str(e)}")


@router.post("", response_model=dict)
async def create_report(
    report_in: ReportCreate,
    background_tasks: BackgroundTasks,
    current_user: dict = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_supabase)
):
    """
    Generate a new report asynchronously.
    Returns immediately with report_id, the report is generated in background.
    """
    try:
        now = datetime.utcnow()

        if report_in.report_type == ReportType.WEEKLY_DIGEST:
            date_from = report_in.date_from or (now - timedelta(days=7))
            date_to = report_in.date_to or now
        elif report_in.report_type == ReportType.MONTHLY_SUMMARY:
            date_from = report_in.date_from or (now - timedelta(days=30))
            date_to = report_in.date_to or now
        elif report_in.report_type == ReportType.ENTITY_REPORT:
            date_from = report_in.date_from or (now - timedelta(days=7))
            date_to = report_in.date_to or now
        else:
            date_from = report_in.date_from or (now - timedelta(days=30))
            date_to = report_in.date_to or now

        data = {
            "user_id": current_user["id"],
            "report_type": report_in.report_type.value if hasattr(report_in.report_type, 'value') else report_in.report_type,
            "title": report_in.title,
            "entity_ids": report_in.entity_ids,
            "date_from": date_from.isoformat(),
            "date_to": date_to.isoformat(),
            "status": "pending",
            "content": {},
        }

        headers = supabase._get_headers()
        headers["Prefer"] = "return=representation"

        url = f"{supabase.url}/rest/v1/reports"
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(url, json=data, headers=headers)
            response.raise_for_status()
            result = response.json()
            report = result[0] if isinstance(result, list) else result
            report_id = report["id"]

        # Queue background task
        background_tasks.add_task(
            generate_report_content,
            report_id=report_id,
            supabase_url=supabase.url,
            anon_key=supabase.anon_key,
            user_id=current_user["id"],
            entity_ids=report_in.entity_ids,
            date_from=date_from.isoformat(),
            date_to=date_to.isoformat(),
            report_type=report_in.report_type.value if hasattr(report_in.report_type, 'value') else report_in.report_type,
        )

        return {
            "id": report_id,
            "status": "pending",
            "message": "Report generation started. Use GET /reports/{report_id}/status to check progress.",
            "created_at": report.get("created_at"),
        }
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail=f"Failed to create report: {e.response.text}")
    except httpx.RequestError as e:
        raise HTTPException(status_code=503, detail=f"Service unavailable: {str(e)}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/status/{report_id}")
async def get_report_status(
    report_id: str,
    current_user: dict = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_supabase)
):
    """Get the status of a report generation task. Returns: pending, processing, ready, or failed"""
    try:
        user_id = current_user["id"]
        headers = supabase._get_headers()

        url = f"{supabase.url}/rest/v1/reports?id=eq.{report_id}&user_id=eq.{user_id}&select=status,generated_at,created_at"
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(url, headers=headers)
            response.raise_for_status()
            data = response.json()
            if data:
                return {
                    "report_id": report_id,
                    "status": data[0].get("status", "unknown"),
                    "generated_at": data[0].get("generated_at"),
                    "created_at": data[0].get("created_at"),
                }

        raise HTTPException(status_code=404, detail="Report not found")
    except HTTPException:
        raise
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail=f"Failed to get report status: {e.response.text}")
    except httpx.RequestError as e:
        raise HTTPException(status_code=503, detail=f"Service unavailable: {str(e)}")


@router.get("/{report_id}")
async def get_report(
    report_id: str,
    current_user: dict = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_supabase)
):
    """Get report by ID (full report details)"""
    try:
        user_id = current_user["id"]
        headers = supabase._get_headers()

        url = f"{supabase.url}/rest/v1/reports?id=eq.{report_id}&user_id=eq.{user_id}"
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(url, headers=headers)
            response.raise_for_status()
            data = response.json()
            if data:
                return data[0]

        raise HTTPException(status_code=404, detail="Report not found")
    except HTTPException:
        raise
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail=f"Failed to get report: {e.response.text}")
    except httpx.RequestError as e:
        raise HTTPException(status_code=503, detail=f"Service unavailable: {str(e)}")


@router.get("/{report_id}/content")
async def get_report_content(
    report_id: str,
    current_user: dict = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_supabase)
):
    """
    Get the generated content of a completed report.
    Returns 404 if report is not ready yet.
    """
    try:
        user_id = current_user["id"]
        headers = supabase._get_headers()

        status_url = f"{supabase.url}/rest/v1/reports?id=eq.{report_id}&user_id=eq.{user_id}&select=status,content,generated_at,title"
        async with httpx.AsyncClient(timeout=30.0) as client:
            status_response = await client.get(status_url, headers=headers)
            status_response.raise_for_status()
            data = status_response.json()

        if not data:
            raise HTTPException(status_code=404, detail="Report not found")

        report = data[0]
        status = report.get("status", "unknown")

        if status == "pending":
            raise HTTPException(
                status_code=202,
                detail={
                    "message": "Report is pending generation",
                    "status": status,
                    "hint": "Check back shortly or use GET /reports/status/{report_id} to monitor progress"
                }
            )

        if status == "failed":
            raise HTTPException(
                status_code=500,
                detail={
                    "message": "Report generation failed",
                    "status": status,
                    "hint": "Please try creating a new report"
                }
            )

        return {
            "report_id": report_id,
            "title": report.get("title"),
            "status": status,
            "generated_at": report.get("generated_at"),
            "content": report.get("content", {}),
        }

    except HTTPException:
        raise
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail=f"Failed to get report content: {e.response.text}")
    except httpx.RequestError as e:
        raise HTTPException(status_code=503, detail=f"Service unavailable: {str(e)}")


@router.delete("/{report_id}")
async def delete_report(
    report_id: str,
    current_user: dict = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_supabase)
):
    """Delete a report"""
    try:
        headers = supabase._get_headers()
        headers["Prefer"] = "return=minimal"

        url = f"{supabase.url}/rest/v1/reports?id=eq.{report_id}&user_id=eq.{current_user['id']}"

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.delete(url, headers=headers)
            response.raise_for_status()

        return {"message": "Report deleted"}
    except HTTPException:
        raise
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail=f"Failed to delete report: {e.response.text}")
    except httpx.RequestError as e:
        raise HTTPException(status_code=503, detail=f"Service unavailable: {str(e)}")
