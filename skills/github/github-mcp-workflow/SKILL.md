---
name: github-mcp-workflow
description: LLM-optimized workflows and search patterns for GitHub API via GithubMCP (@modelcontextprotocol/server-github). Use when searching code, listing issues, pull requests, or commits via MCP tools.
category: github
---

# GitHub MCP Workflow Strategy

Use this skill when performing GitHub searches, issue/PR management, or file lookups via GithubMCP.

## Code Search Rules (`search_code`)
- **Scoped Query**: Always include `repo:owner/repo` or `org:orgname`.
- **Pagination**: Use `perPage: 10` (max 20) with `page: 1`.
- **Narrow Keywords**: Avoid single-word broad searches. Combine file extension/path with symbol: `"def process_payment" repo:org/repo extension:py`.

## Issue & PR Search Rules (`list_issues`, `list_pull_requests`)
- **Page Limits**: Set `perPage: 10` or `15` per request.
- **Minimal Output**: Use `minimal_output: true` when retrieving sets for overview summaries.
- **State Filtering**: Specify `state: "open"` or `"closed"` explicitly.

## PR Workflow
1. Use `get_pull_request` to view title, branch, and diff summary.
2. For specific code changes, fetch target files with `get_file_contents`.
3. Keep response summaries structured: PR status, changed files, key risk items.
