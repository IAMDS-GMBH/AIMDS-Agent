"""Source adapters for the LLM-free brief collector (AIS-305).

Every adapter follows the same contract (``base.SourceAdapter``): check
availability deterministically, fetch **only the user's** data with bounded,
bundled calls, and return normalised ``BriefItem`` rows. New sources
(OpenProject, …) are added here and registered in ``ADAPTERS``.
"""

from __future__ import annotations

from typing import List

from cron.brief_sources.base import SourceAdapter


def default_adapters() -> List[SourceAdapter]:
    from cron.brief_sources.m365 import M365Adapter
    from cron.brief_sources.jira import JiraAdapter
    from cron.brief_sources.tempo import TempoAdapter
    from cron.brief_sources.workspace import WorkspaceAdapter

    return [M365Adapter(), JiraAdapter(), TempoAdapter(), WorkspaceAdapter()]
