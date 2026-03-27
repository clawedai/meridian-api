"""Bulk import prospects from CSV/Excel files."""
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
import httpx
import logging

from ..deps import get_current_user
from ...schemas.bulk_import import BulkImportResponse
from ...services.bulk_import_service import BulkImportService
from ...core.config import settings

router = APIRouter(prefix="/prospects", tags=["Prospects"])
logger = logging.getLogger(__name__)


def _get_headers() -> dict:
    """Get Supabase admin headers."""
    return {
        "apikey": settings.SUPABASE_SERVICE_KEY,
        "Authorization": f"Bearer {settings.SUPABASE_SERVICE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }


@router.post("/bulk-import", response_model=BulkImportResponse)
async def bulk_import_prospects(
    file: UploadFile = File(...),
    current_user: dict = Depends(get_current_user),
):
    """Upload CSV or Excel file to bulk import prospects."""
    user_id = current_user["id"]

    # Validate file type
    filename = file.filename or ""
    if not filename.lower().endswith((".csv", ".xlsx")):
        raise HTTPException(400, "Only .csv or .xlsx files are supported")

    # Read file content
    content = await file.read()
    if len(content) > 10 * 1024 * 1024:  # 10MB limit
        raise HTTPException(400, "File too large. Maximum size is 10MB")

    # Parse file
    svc = BulkImportService()
    rows, parse_errors = svc.parse_file(content, filename)

    if parse_errors:
        return BulkImportResponse(
            success=False,
            message="Failed to parse file",
            created=0,
            skipped=0,
            failed=len(rows),
            errors=parse_errors,
            preview=[],
        )

    if not rows:
        raise HTTPException(400, "No valid rows found in file")

    # Validate and separate valid/invalid
    valid_rows = []
    validation_errors = []
    for i, row in enumerate(rows, 1):
        valid, err = svc.validate_row(row, i)
        if valid:
            valid_rows.append(row)
        else:
            validation_errors.append(err)

    if not valid_rows:
        raise HTTPException(400, "No valid rows found. All rows have errors.")

    # Check for existing emails
    emails = [r.get("email") for r in valid_rows if r.get("email")]
    url = f"{settings.SUPABASE_URL}/rest/v1/prospects?email=in.({','.join(emails)})&user_id=eq.{user_id}&select=email"

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(url, headers=_get_headers())

    existing_emails = set()
    if resp.status_code == 200:
        existing_emails = {r["email"].lower() for r in resp.json() if r.get("email")}

    # Build payloads for new prospects only
    new_prospects = []
    skipped = 0
    for row in valid_rows:
        email = row.get("email", "").lower()
        if email in existing_emails:
            skipped += 1
        else:
            new_prospects.append(svc.build_prospect_payload(row, user_id))

    # Bulk insert
    created = 0
    insert_errors = []
    created_prospects = []

    if new_prospects:
        try:
            insert_url = f"{settings.SUPABASE_URL}/rest/v1/prospects"
            async with httpx.AsyncClient(timeout=60.0) as client:
                insert_resp = await client.post(
                    insert_url,
                    json=new_prospects,
                    headers=_get_headers(),
                )
            if insert_resp.status_code in [200, 201]:
                data = insert_resp.json()
                created = len(data) if isinstance(data, list) else 0
                created_prospects = (data[:5] if isinstance(data, list) else []) or []
            else:
                insert_errors.append(f"Database error: {insert_resp.text}")
        except Exception as e:
            insert_errors.append(f"Database error: {str(e)}")

    return BulkImportResponse(
        success=True,
        message=f"Import complete: {created} created, {skipped} skipped",
        created=created,
        skipped=skipped,
        failed=len(validation_errors),
        errors=validation_errors + insert_errors,
        preview=[{
            "id": p.get("id", ""),
            "full_name": p.get("full_name"),
            "company": p.get("company"),
            "email": p.get("email"),
        } for p in created_prospects],
    )
