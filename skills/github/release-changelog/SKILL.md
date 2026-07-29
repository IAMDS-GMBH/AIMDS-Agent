---
name: release-changelog
description: Generates clean, HTML-formatted release notes and release changelogs from git commits, merged pull requests, and linked Jira tickets for a version release. Trigger when user asks "build release notes", "generate changelog", "create release notes", or "changelog for today".
---

# Release Notes & Changelog Generator Skill

This skill parses git commits, merged PRs, and linked Jira issues for a repository to build clean, professional HTML release notes.

## Input Data & Extraction
1. **Git Commits & PRs**:
   - Query commits for today or between release tags (`git log --since="today" --oneline` or `git log tag1..tag2`).
   - Retrieve PR details via `gh pr list --state merged --limit 20` or GitHub tools.
2. **Jira Ticket References**:
   - Extract ticket keys (e.g., `AIS-209`, `AIS-164`) from commit messages or PR titles.

## Output Format Standard (HTML Template)

Always generate release notes following this exact HTML structure:

```html
<h2>AIMDS Suite v[VERSION] – Release Notes</h2>

<p><strong>Released:</strong> [Date, e.g. July 29, 2026]<br>
<strong>Status:</strong> Deployed to dev, staging, and production</p>

<h3>Overview</h3>
<p>[1-2 sentence executive overview of the release focus and primary improvements.]</p>

<h3>Key Changes</h3>

<p><strong>[FEATURE] [Feature Name] ([JIRA-KEY])</strong></p>
<ul>
<li>[Bullet point detail 1 on capability or improvement]</li>
<li>[Bullet point detail 2]</li>
<li><a href="https://github.com/[OWNER]/[REPO]/pull/[PR_NUMBER]">PR #[PR_NUMBER]</a></li>
</ul>

<p><strong>[BUGFIX] [Bugfix Name] ([JIRA-KEY])</strong></p>
<ul>
<li>[Bullet point detail 1 on bug fix or resolution]</li>
<li>[Bullet point detail 2]</li>
<li><a href="https://github.com/[OWNER]/[REPO]/pull/[PR_NUMBER]">PR #[PR_NUMBER]</a></li>
</ul>

<h3>Documentation</h3>
<p><a href="https://github.com/[OWNER]/[REPO]/compare/[PREV_TAG]...[NEW_TAG]">Complete changelog</a> | <a href="https://github.com/[OWNER]/[REPO]/releases/tag/[NEW_TAG]">Release tag</a></p>
```

## Categorization Rules
- **`[FEATURE]`**: New capabilities, API additions, performance scaling, or UI enhancements.
- **`[BUGFIX]`**: Bug fixes, routing alignment, patch corrections, or edge-case handling.
- **`[REFACTOR]` / `[CHORE]`**: Architecture cleanups, dependency bumps, or pipeline updates.
