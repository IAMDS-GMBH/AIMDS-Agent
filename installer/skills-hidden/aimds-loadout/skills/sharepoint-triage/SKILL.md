---
name: sharepoint-triage
description: Searches and summarizes files across OneDrive and SharePoint sites, locates document templates, and extracts key document insights.
---

# SharePoint & OneDrive File Search & Triage

## Procedure
1. **Search & Locate:**
   - Call `m365_search_drive_files` for personal OneDrive files.
   - Call `m365_list_sharepoint_sites` & `m365_search_sharepoint_files` for team and company documents on SharePoint.
2. **Filter & Summarize:**
   - Filter by date, relevance, and file type (.docx, .pdf, .xlsx, .pptx).
   - Extract key insights, status updates, or contract details efficiently.
3. **Template & Spec Discovery:**
   - Locate company document templates, project specifications, and architecture drawings quickly.

## Context Window & Token Optimization
- **Metadata First:** Query file name, webUrl, lastModifiedDateTime, and size before downloading or reading file content.
- **Selective Section Extraction:** Extract only relevant paragraphs, tables, or sections. Never paste whole document dumps into context.
- **Top-N Limits:** Limit search results to maximum 5 files per query (`top=5`).

## Guardrail (hard)
- Respect file permissions and tenant access boundaries.
- Do NOT delete or overwrite files without explicit confirmation.

## Verification
- File locations and SharePoint site references are accurate.
- Summaries capture critical content concisely.
