"""AIS-740: delegate_task is a pinned schema sent with every request — keep it lean."""

from __future__ import annotations

import json

from toolsets import HARD_DISABLED_TOOLSETS


def _schema() -> dict:
    from tools.delegate_tool import DELEGATE_TASK_SCHEMA, _build_dynamic_schema_overrides

    return {**DELEGATE_TASK_SCHEMA, **_build_dynamic_schema_overrides()}


def test_hard_disabled_toolsets_are_not_offered():
    from tools.delegate_tool import _SUBAGENT_TOOLSETS

    assert not set(_SUBAGENT_TOOLSETS) & set(HARD_DISABLED_TOOLSETS)
    desc = _schema()["parameters"]["properties"]["toolsets"]["description"]
    assert "'browser'" not in desc and "'spotify'" not in desc


def test_toolset_list_is_sent_once():
    from tools.delegate_tool import _TOOLSET_LIST_STR

    assert json.dumps(_schema()).count(_TOOLSET_LIST_STR) == 1
