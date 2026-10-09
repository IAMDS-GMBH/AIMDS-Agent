"""Bundled OpenProject MCP server (AIS-408, pm_* contract since AIS-479) against a fake OpenProject API."""

import asyncio
import datetime as dt
import importlib.util
import json
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

SERVER_PATH = Path(__file__).parent.parent.parent / "optional-mcps" / "OpenProjectMCP" / "server.py"


def _load(monkeypatch, write="AIS,PRO", read="*"):
    monkeypatch.setenv("OPENPROJECT_BASE_URL", "https://op.example.com/api/v3")
    monkeypatch.setenv("OPENPROJECT_API_TOKEN", "opapi-test")
    monkeypatch.setenv("OPENPROJECT_READ_PROJECTS", read)
    monkeypatch.setenv("OPENPROJECT_WRITE_PROJECTS", write)
    spec = importlib.util.spec_from_file_location(f"openproject_server_{write or 'ro'}", SERVER_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


PROJECTS = [
    {"_type": "Project", "id": 107, "identifier": "AIS", "name": "AIMDS Suite", "active": True, "public": False},
    {"_type": "Project", "id": 46, "identifier": "PRO", "name": "Projektmanagement", "active": True, "public": False},
    {"_type": "Project", "id": 109, "identifier": "MOAP", "name": "Mother of all Projects", "active": True, "public": True},
]
# Done is not a closed status on this instance; Rejected is (AIS-494: is_closed).
# Like the IAMDS instance: "Done" is finished (defaultDoneRatio 100) but
# deliberately not closed — an external job closes it later (AIS-518).
STATUSES = [{"id": 15, "name": "Backlog", "isClosed": False, "isDefault": True, "defaultDoneRatio": 0},
            {"id": 17, "name": "In Progress", "isClosed": False, "isDefault": False, "defaultDoneRatio": 30},
            {"id": 18, "name": "In Review", "isClosed": False, "isDefault": False, "defaultDoneRatio": 70},
            {"id": 12, "name": "Done", "isClosed": False, "isDefault": False, "defaultDoneRatio": 100},
            {"id": 14, "name": "Rejected", "isClosed": True, "isDefault": False, "defaultDoneRatio": 100}]
OPEN_IDS = ["15", "17", "18"]
TYPES = [{"id": 1, "name": "Task", "isDefault": True}, {"id": 7, "name": "Bug", "isDefault": False}]
PRIORITIES = [{"id": 8, "name": "Normal", "isDefault": True}, {"id": 9, "name": "High", "isDefault": False}]
ACTIVITIES = [{"id": 3, "name": "Development"}, {"id": 5, "name": "Support"}]
USERS = [
    {"id": 14, "name": "Johannes Huchler", "login": "jhuchler", "email": "jh@example.com"},
    {"id": 20, "name": "Tobias Hehl", "login": "thehl", "email": "th@example.com"},
]


def _wp(wid=17699, project=107, status=(17, "In Progress"), subject="Own server", created="2026-01-01T00:00:00Z",
        display=None, lock=2):
    return {
        "_type": "WorkPackage",
        "id": wid,
        "displayId": display or str(wid),
        "subject": subject,
        "lockVersion": lock,
        "createdAt": created,
        "updatedAt": "2026-01-02T00:00:00Z",
        "startDate": "2026-01-01",
        "dueDate": None,
        "description": {"format": "markdown", "raw": "A long description of the work"},
        "_links": {
            "project": {"href": f"/api/v3/projects/{project}", "title": {107: "AIMDS Suite", 46: "Projektmanagement", 109: "Mother of all Projects"}[project]},
            "status": {"href": f"/api/v3/statuses/{status[0]}", "title": status[1]},
            "type": {"href": "/api/v3/types/1", "title": "Task"},
            "priority": {"href": "/api/v3/priorities/8", "title": "Normal"},
            "assignee": {"href": "/api/v3/users/14", "title": "Johannes Huchler"},
            "author": {"href": "/api/v3/users/14", "title": "Johannes Huchler"},
            "responsible": {"href": None},
            "parent": {"href": None},
        },
    }


def _time_entry(eid, spent_on, hours, wp=17054, start=None, project=107, entity=True):
    links = {
        "project": {"href": f"/api/v3/projects/{project}", "title": "AIMDS Suite"},
        "user": {"href": "/api/v3/users/14", "title": "Johannes Huchler"},
        "activity": {"href": "/api/v3/time_entries/activities/3", "title": "Development"},
    }
    if wp:
        links["entity" if entity else "workPackage"] = {"href": f"/api/v3/work_packages/{wp}", "title": "EVN Ongoing"}
    return {
        "id": eid, "spentOn": spent_on, "hours": hours, "startTime": start, "lockVersion": 1,
        "createdAt": "2026-09-01T10:00:00Z", "comment": {"format": "plain", "raw": "work"}, "_links": links,
    }


def _collection(items, offset=1, page_size=50, total=None):
    return {"_type": "Collection", "total": len(items) if total is None else total, "count": len(items),
            "offset": offset, "pageSize": page_size, "_embedded": {"elements": items}}


class FakeOpenProject:
    """Routes `(method, path)` to canned HAL responses and records calls."""

    def __init__(self):
        self.calls = []
        self.work_packages = {"17699": _wp(display="AIS-408"), "AIS-408": _wp(display="AIS-408"),
                              "17695": _wp(17695, 109, (15, "Backlog"), "Doc"), "17054": _wp(17054, 107, subject="EVN Ongoing")}
        self.search_results = [_wp(), _wp(17695, 109, (15, "Backlog"), "Doc")]
        self.wp_total = None
        self.time_entries = []
        self.reject_entity_filter = False
        self.reject_work_package_filter = False
        self.exact_times = True
        self.users_forbidden = False
        self.conflicts = 0
        self.patch_project = None
        self.errors = {}
        self.groups = None
        self.activity_user_titles = True
        self.projects = list(PROJECTS)  # the cached project list
        self.hidden_projects = []  # found by GET projects/{ref}, not listed
        self.batch_keys = {17699: "AIS-408"}  # display ids of the key batch lookup, else EXT-<id>
        self.hidden_ids = set()  # work packages the key batch lookup does not return
        self.global_activities = None  # the instance-wide activity collection (404 when None)
        self.agenda_items = [{"id": 27, "title": "Discuss", "notes": {"raw": "Some notes."}, "itemType": "simple", "_links": {}}]

    def __call__(self, request: httpx.Request) -> httpx.Response:
        url = urlparse(str(request.url))
        path = url.path.split("/api/v3/", 1)[-1]
        query = {k: v[0] for k, v in parse_qs(url.query).items()}
        body = json.loads(request.content) if request.content else None
        method = request.method
        self.calls.append((method, path, query, body))

        def ok(payload, status=200):
            return httpx.Response(status, json=payload)

        def err(status, message):
            return httpx.Response(status, json={"_type": "Error", "message": message})

        if (method, path) in self.errors:
            status, payload = self.errors[(method, path)]
            return httpx.Response(status, json=payload)
        offset = int(query.get("offset") or 1)
        size = int(query.get("pageSize") or 50)
        if (method, path) == ("GET", "projects"):
            return ok(_collection(self.projects))
        if method == "GET" and path.startswith("projects/") and path.count("/") == 1:
            ref = path.split("/", 1)[1]
            for p in self.projects + self.hidden_projects:
                if ref in (str(p["id"]), p["identifier"]):
                    return ok(p)
            return err(404, "The requested resource could not be found.")
        if (method, path) == ("GET", "users/me"):
            return ok({"id": 14, "name": "Johannes Huchler", "login": "jhuchler", "email": "jh@example.com"})
        if (method, path) == ("GET", "statuses"):
            return ok(_collection(STATUSES))
        if (method, path) == ("GET", "types"):
            return ok(_collection(TYPES))
        if (method, path) == ("GET", "priorities"):
            return ok(_collection(PRIORITIES))
        if (method, path) == ("GET", "time_entries/activities"):
            return ok(_collection(self.global_activities)) if self.global_activities else err(404, "not found")
        if (method, path) == ("GET", "users"):
            return err(403, "You are not authorized") if self.users_forbidden else ok(_collection(USERS))
        if (method, path) == ("GET", "principals"):
            return ok(_collection([dict(u, _type="User", login="", email="") for u in USERS]))
        if (method, path) == ("GET", "memberships"):
            return ok(_collection([{"id": 1, "_links": {
                "principal": {"href": "/api/v3/users/14", "title": "Johannes Huchler"},
                "roles": [{"href": "/api/v3/roles/3", "title": "Member"}]}}]))
        if method == "POST" and path == "time_entries/form":
            schema = {"activity": {"_embedded": {"allowedValues": ACTIVITIES}}}
            schema["startTime"] = {"type": "DateTime", "writable": self.exact_times}
            return ok({"_embedded": {"payload": body, "schema": schema, "validationErrors": {}}})
        if (method, path) == ("GET", "work_packages"):
            filters = json.loads(query.get("filters", "[]"))
            if any("id" in f for f in filters):
                ids = [int(i) for i in next(f["id"]["values"] for f in filters if "id" in f)]
                return ok(_collection([_wp(i, display=self.batch_keys.get(i, f"EXT-{i}")) for i in ids if i not in self.hidden_ids]))
            items = self.search_results
            payload = _collection(items, offset, size, self.wp_total)
            if "groupBy" in query and self.groups is not None:
                payload["groups"] = self.groups
            return ok(payload)
        if method == "GET" and path.startswith("work_packages/") and path.count("/") == 1:
            ref = path.split("/", 1)[1]
            if ref in self.work_packages:
                return ok(self.work_packages[ref])
            return err(404, "The requested resource could not be found.")
        if method == "POST" and path.startswith("projects/") and path.endswith("/work_packages"):
            pid = int(path.split("/")[1])
            created = _wp(17710, pid, (15, "Backlog"), body.get("subject"), created=dt.datetime.now(dt.timezone.utc).isoformat())
            return ok(created, 201)
        if method == "PATCH" and path.startswith("work_packages/"):
            if self.conflicts:
                self.conflicts -= 1
                return err(409, "Your changes could not be saved.")
            wid = int(path.split("/")[1])
            project = self.patch_project or int((body.get("_links", {}).get("project", {}).get("href") or "/107").rsplit("/", 1)[-1])
            return ok(_wp(wid, project, lock=body["lockVersion"] + 1))
        if method == "POST" and path.endswith("/form") and path.startswith("work_packages/"):
            return ok({"_embedded": {"payload": body, "schema": {"status": {"_embedded": {"allowedValues": [STATUSES[2]]}}},
                                     "validationErrors": {}}})
        if method == "POST" and path.endswith("/activities"):
            return ok({}, 201)
        if method == "GET" and path.endswith("/activities"):
            titled = self.activity_user_titles
            return ok(_collection([
                {"id": 1, "createdAt": "2026-01-01T00:00:00Z", "comment": {"raw": "first comment"},
                 "_links": {"user": {"href": "/api/v3/users/14", **({"title": "Johannes Huchler"} if titled else {})}}},
                {"id": 2, "createdAt": "2026-01-02T00:00:00Z", "comment": {"raw": "second, a much longer comment"},
                 "_links": {"user": {"href": "/api/v3/users/20", **({"title": "Tobias Hehl"} if titled else {})}}},
                {"id": 3, "createdAt": "2026-01-03T00:00:00Z", "comment": {"raw": "third"},
                 "_links": {"user": {"href": "/api/v3/users/31", **({"title": "Former Colleague"} if titled else {})}}},
            ]))
        if method == "GET" and path.startswith("users/") and path.split("/", 1)[1].isdigit():
            return ok({"id": int(path.split("/", 1)[1]), "name": "Former Colleague", "login": "", "email": ""})
        if method == "GET" and path.endswith("/relations"):
            return ok(_collection([{"id": 5, "type": "blocks", "_links": {
                "from": {"href": "/api/v3/work_packages/17699", "title": "Own server"},
                "to": {"href": "/api/v3/work_packages/17054", "title": "EVN Ongoing"}}}]))
        if method == "POST" and path.endswith("/relations"):
            return ok({"id": 6, "type": body["type"], "_links": {
                "from": {"href": f"/api/v3/{path.rsplit('/', 1)[0]}", "title": "x"}, "to": dict(body["_links"]["to"], title="y")}}, 201)
        if method == "DELETE":
            return httpx.Response(204)
        if (method, path) == ("GET", "queries"):
            return ok(_collection([
                {"id": 31, "name": "Sprint board", "filters": [], "_links": {"project": {"href": "/api/v3/projects/107", "title": "AIMDS Suite"},
                                                                         "columns": [{"href": "/x", "title": "Subject"}]}},
                {"id": 32, "name": "MOAP board", "filters": [], "_links": {"project": {"href": "/api/v3/projects/109", "title": "Mother of all Projects"}}},
            ]))
        if method == "GET" and path.startswith("queries/"):
            return ok({"id": 31, "name": "Sprint board", "filters": [], "_links": {
                "project": {"href": "/api/v3/projects/107", "title": "AIMDS Suite"},
                "groupBy": {"href": "/api/v3/queries/group_bys/status", "title": "Status"}},
                "_embedded": {"results": _collection([_wp()])}})
        if (method, path) == ("GET", "meetings"):
            return ok(_collection([
                {"id": 4, "title": "Weekly", "startTime": "2026-09-24T08:00:00Z", "duration": 1.0, "location": "Room", "state": "open",
                 "_links": {"project": {"href": "/api/v3/projects/107", "title": "AIMDS Suite"}, "author": {"href": "/api/v3/users/14", "title": "Johannes Huchler"}}},
                {"id": 5, "title": "Old", "startTime": "2026-08-01T08:00:00Z", "duration": 0.5, "location": "", "state": "closed",
                 "_links": {"project": {"href": "/api/v3/projects/109", "title": "Mother of all Projects"}, "author": {"href": None}}},
            ]))
        if method == "GET" and path.startswith("meetings/") and path.count("/") == 1:
            return ok({"id": 4, "title": "Weekly", "startTime": "2026-09-24T08:00:00Z", "duration": 1.0, "state": "open",
                       "_links": {"project": {"href": "/api/v3/projects/107", "title": "AIMDS Suite"}}})
        if method == "GET" and path.endswith("/outcomes"):
            return ok(_collection([{"id": 9, "notes": {"raw": ""}, "kind": "work_package",
                                    "_links": {"workPackage": {"href": "/api/v3/work_packages/17686"}}}]))
        if method == "GET" and path.endswith("/agenda_items"):
            return ok(_collection(self.agenda_items))
        if (method, path) == ("POST", "meetings"):
            return ok({"id": 8, "title": body["title"], "startTime": body.get("startTime"), "duration": 1.0, "state": "open",
                       "_links": {"project": dict(body["_links"]["project"], title="AIMDS Suite")}}, 201)
        if method == "POST" and path.endswith("/outcomes"):
            return ok({"id": 10, "notes": {"raw": body["notes"]}, "kind": body["kind"]}, 201)
        if method == "POST" and path.endswith("/agenda_items"):
            return ok({"id": 28, "title": body["title"], "notes": {"raw": body.get("notes", "")}, "itemType": "simple",
                       "_links": body.get("_links", {})}, 201)
        if (method, path) == ("GET", "time_entries"):
            filters = json.loads(query.get("filters", "[]"))
            if self.reject_entity_filter and any("entity_id" in f for f in filters):
                return err(400, "Filters Entity filter does not exist.")
            if self.reject_work_package_filter and any("work_package" in f for f in filters):
                return err(400, "Filters Work package filter does not exist.")
            start = (offset - 1) * size
            return ok(_collection(self.time_entries[start:start + size], offset, size, len(self.time_entries)))
        if method == "GET" and path.startswith("time_entries/"):
            return ok(_time_entry(int(path.split("/")[1]), "2026-09-24", "PT1H", project=107))
        if (method, path) == ("POST", "time_entries"):
            return ok(_time_entry(99, body["spentOn"], body["hours"], start=body.get("startTime")), 201)
        if method == "PATCH" and path.startswith("time_entries/"):
            if self.conflicts:
                self.conflicts -= 1
                return err(409, "stale")
            return ok(_time_entry(int(path.split("/")[1]), body.get("spentOn", "2026-09-24"), body.get("hours", "PT1H")))
        return ok({"message": f"unexpected {method} {path}"}, 500)


@pytest.fixture
def op(monkeypatch):
    server = _load(monkeypatch)
    fake = FakeOpenProject()
    client = httpx.Client(transport=httpx.MockTransport(fake), base_url="https://op.example.com/api/v3/")
    monkeypatch.setattr(server, "_http", lambda: client)
    monkeypatch.setattr(server.time, "sleep", lambda _s: None)
    return server, fake


def call(server, name, **args):
    result = asyncio.run(server.call_tool(name, args))
    text = result.content[0].text
    if result.isError:
        return True, text
    return False, json.loads(text)


def ok(server, name, **args):
    is_error, payload = call(server, name, **args)
    assert not is_error, payload
    return payload


def fail(server, name, **args):
    is_error, payload = call(server, name, **args)
    assert is_error, payload
    return payload


def requests(fake, method, path):
    return [c for c in fake.calls if c[0] == method and c[1] == path]


# ─── Shape and wiring ─────────────────────────────────────────────────────────


def test_results_are_one_pretty_printed_json_text_block(op):
    server, _ = op
    result = asyncio.run(server.call_tool("pm_get_work_package", {"id": 17699}))
    assert not result.isError and len(result.content) == 1
    text = result.content[0].text
    assert text.startswith("{\n  \"assignee\"")  # MarshalIndent: 2 spaces, sorted map keys
    assert json.loads(text)["id"] == 17699


def test_unknown_tool_is_a_tool_error(op):
    server, _ = op
    assert fail(server, "pm_nope") == "unknown tool: pm_nope"


def test_base_url_api_suffix_is_stripped(monkeypatch):
    server = _load(monkeypatch)
    assert server._config()["base_url"] == "https://op.example.com"


# ─── Identity ─────────────────────────────────────────────────────────────────


def test_link_status_reports_the_token_owner(op):
    server, _ = op
    assert ok(server, "pm_link_status") == {
        "instance": "https://op.example.com", "linked": True, "local": True,
        "op_login": "jhuchler", "op_name": "Johannes Huchler", "op_user_id": 14,
    }


def test_link_status_unconfigured_points_to_hermes_settings(monkeypatch):
    server = _load(monkeypatch)
    monkeypatch.delenv("OPENPROJECT_API_TOKEN")
    data = ok(server, "pm_link_status")  # an expected state, not isError (AIS-486)
    assert data["linked"] is False and data["action_required"] == "configure_account"
    assert "Settings -> MCP -> OpenProjectMCP" in data["next_step"] and "link_url" not in data


def test_link_status_with_rejected_token(op):
    server, fake = op
    fake.errors[("GET", "users/me")] = (401, {"message": "Unauthenticated"})
    text = fail(server, "pm_link_status")
    assert text.startswith("The configured OpenProject token was rejected") and "Settings -> MCP -> OpenProjectMCP" in text


@pytest.mark.parametrize("name", ["pm_link_account", "pm_unlink_account"])
def test_link_and_unlink_explain_the_local_token(op, name):
    server, fake = op
    text = fail(server, name)
    assert "no account linking" in text and "Nothing was changed" in text
    assert not fake.calls


# ─── Reference data, users, projects ──────────────────────────────────────────


def test_reference_data_with_activity_fallback(op):
    server, fake = op
    data = ok(server, "pm_list_reference_data")
    assert list(data) == ["activities", "priorities", "statuses", "types"]
    assert data["statuses"][0] == {"id": 15, "is_closed": False, "is_default": True, "is_done": False, "name": "Backlog"}
    assert [s["name"] for s in data["statuses"] if s["is_closed"]] == ["Rejected"]  # Done is not closed here
    assert [s["name"] for s in data["statuses"] if s["is_done"]] == ["Done", "Rejected"]  # but finished (AIS-518)
    assert data["types"][1] == {"id": 7, "is_default": False, "name": "Bug"}
    assert data["activities"] == ACTIVITIES  # global endpoint 404 -> project form
    assert requests(fake, "POST", "time_entries/form")
    only = ok(server, "pm_list_reference_data", kind="priorities")
    assert only == {"priorities": [{"id": 8, "is_default": True, "name": "Normal"}, {"id": 9, "is_default": False, "name": "High"}]}


def test_reference_data_is_cached(op):
    server, fake = op
    ok(server, "pm_list_reference_data", kind="statuses")
    ok(server, "pm_list_reference_data", kind="statuses")
    assert len(requests(fake, "GET", "statuses")) == 1


def test_list_users_query_and_members(op):
    server, _ = op
    assert ok(server, "pm_list_users", query="tobi") == {"users": [{"id": 20, "name": "Tobias Hehl", "login": "thehl", "email": "th@example.com"}]}
    members = ok(server, "pm_list_users", project="AIS")
    assert members == {"members": [{"user_id": 14, "name": "Johannes Huchler", "roles": ["Member"]}], "project": "AIS"}


def test_user_directory_falls_back_to_principals_without_admin(op):
    server, fake = op
    fake.users_forbidden = True
    users = ok(server, "pm_list_users")["users"]
    assert [u["id"] for u in users] == [14, 20]
    assert requests(fake, "GET", "principals")


def test_projects_list_and_get(op):
    server, _ = op
    projects = ok(server, "pm_list_projects")["projects"]
    assert projects[0] == {"id": 107, "identifier": "AIS", "name": "AIMDS Suite", "active": True, "public": False, "writable": True}
    assert [p["writable"] for p in projects] == [True, True, False]
    searched = ok(server, "pm_list_projects", search="AIS")
    assert searched["total"] == 3 and "projects" in searched
    got = ok(server, "pm_get_project", project="AIS", include_members=True)
    assert got["project"]["identifier"] == "AIS" and got["members"][0]["roles"] == ["Member"]


def test_project_by_name_and_closest_candidates(op):
    server, _ = op
    assert ok(server, "pm_get_project", project="projektmanagement")["project"]["identifier"] == "PRO"
    text = fail(server, "pm_get_project", project="PM")
    assert text.startswith('Unknown OpenProject project "PM" (parameter project) — use an identifier or id from pm_list_projects.')
    assert "PRO (Projektmanagement, id 46)" in text


def test_get_project_requires_project(op):
    server, _ = op
    assert fail(server, "pm_get_project") == "project is required"


# ─── Work packages: reads ─────────────────────────────────────────────────────


def test_list_work_packages_resolves_filters_and_paginates(op):
    server, fake = op
    fake.wp_total = 120
    data = ok(server, "pm_list_work_packages", project="AIS", status="In Progress", assignee="thehl", type="bug",
              priority="High", open_only=False, updated_from="2026-01-01", updated_to="2026-02-01",
              sort_by=["updatedAt:desc"], limit=50, offset=2)
    assert data["total"] == 120 and data["has_more"] is True and data["next_offset"] == 3
    call_ = requests(fake, "GET", "work_packages")[-1]
    filters = json.loads(call_[2]["filters"])
    assert {"project": {"operator": "=", "values": ["107"]}} in filters
    assert {"status": {"operator": "=", "values": ["17"]}} in filters
    assert {"assignee": {"operator": "=", "values": ["20"]}} in filters
    assert {"type": {"operator": "=", "values": ["7"]}} in filters
    assert {"priority": {"operator": "=", "values": ["9"]}} in filters
    assert {"updatedAt": {"operator": "<>d", "values": ["2026-01-01", "2026-02-01"]}} in filters
    assert json.loads(call_[2]["sortBy"]) == [["updatedAt", "desc"]]
    assert (call_[2]["offset"], call_[2]["pageSize"]) == ("2", "50")
    row = data["work_packages"][0]
    assert set(row) == {"key", "id", "subject", "status", "type", "priority", "project", "assignee", "responsible",
                        "parent_key", "parent_id", "author", "start_date", "due_date", "created_at", "updated_at"}
    assert row["parent_id"] == 0 and row["parent_key"] == "" and row["due_date"] == "" and row["project"] == "AIMDS Suite"
    assert "groups" not in data


def test_last_page_has_no_next_offset_and_limit_is_capped(op):
    server, fake = op
    data = ok(server, "pm_list_work_packages", limit=1000)
    assert data["has_more"] is False and data["next_offset"] is None
    assert requests(fake, "GET", "work_packages")[-1][2]["pageSize"] == "200"


def test_select_projects_fields_and_unknown_select_lists_available(op):
    server, _ = op
    data = ok(server, "pm_search_work_packages", query="server", select=["id", "subject"])
    assert data["work_packages"][0] == {"id": 17699, "subject": "Own server"}
    unknown = ok(server, "pm_list_work_packages", select=["nope"])["work_packages"][0]
    assert list(unknown) == ["_available_fields"] and unknown["_available_fields"]["id"] == 17699


def test_search_sends_the_search_filter_and_requires_query(op):
    server, fake = op
    ok(server, "pm_search_work_packages", query="login timeout", assignee="me", open_only=True)
    filters = json.loads(requests(fake, "GET", "work_packages")[-1][2]["filters"])
    assert {"search": {"operator": "**", "values": ["login timeout"]}} in filters
    assert {"assignee": {"operator": "=", "values": ["me"]}} in filters
    assert {"status": {"operator": "=", "values": OPEN_IDS}} in filters
    assert fail(server, "pm_search_work_packages") == "query is required"


def test_unknown_status_names_the_allowed_values(op):
    server, _ = op
    text = fail(server, "pm_list_work_packages", status="Waiting")
    assert text == ('Unknown OpenProject status "Waiting". Allowed: Backlog, In Progress, In Review, Done, Rejected — '
                    'see pm_list_reference_data(kind="statuses").')


def test_unknown_assignee_is_an_error(op):
    server, _ = op
    assert fail(server, "pm_list_work_packages", assignee="nobody") == \
        'Unknown OpenProject user "nobody" — use "me", a numeric id, a login or a name from pm_list_users.'
    # Exact (case-insensitive) login, name or email only, like the Suite (AIS-494).
    assert "Unknown OpenProject user" in fail(server, "pm_list_work_packages", assignee="Tobias")


def test_get_work_package_with_text_limit_and_key(op):
    server, _ = op
    row = ok(server, "pm_get_work_package", id="AIS-408", text_limit=6)
    assert row["description"] == "A long" and row["description_truncated"] is True
    assert row["description_length"] == len("A long description of the work") and row["lock_version"] == 2
    assert (row["key"], row["id"]) == ("AIS-408", 17699)
    full = ok(server, "pm_get_work_package", id=17054)
    assert full["description_truncated"] is False and full["key"] == "17054"


def test_get_work_package_errors(op):
    server, _ = op
    assert fail(server, "pm_get_work_package") == "id is required"
    assert fail(server, "pm_get_work_package", id="abc") == \
        'invalid arguments: "abc" is neither a work package key like "AIS-469" nor a numeric id'
    assert fail(server, "pm_get_work_package", id=1.5) == \
        'invalid arguments: work package id must be a key like "AIS-469" or a number'
    assert fail(server, "pm_get_work_package", id=-3) == "id is required"
    assert fail(server, "pm_get_work_package", id=1).startswith("Not found in OpenProject: openproject: not found:")


def test_activity_limit_and_text_limit(op):
    server, _ = op
    data = ok(server, "pm_list_work_package_activity", id=17699, limit=2, text_limit=6)
    assert data == {"activity": [
        {"id": 2, "user": "Tobias Hehl", "created_at": "2026-01-02T00:00:00Z", "comment": "second", "comment_truncated": True},
        {"id": 3, "user": "Former Colleague", "created_at": "2026-01-03T00:00:00Z", "comment": "third"},
    ]}


def test_relations_list_and_create(op):
    server, fake = op
    rel = ok(server, "pm_list_work_package_relations", id=17699)["relations"][0]
    assert rel == {"id": 5, "type": "blocks", "from_key": "AIS-408", "from_id": 17699, "from_subject": "Own server",
                   "to_key": "EXT-17054", "to_id": 17054, "to_subject": "EVN Ongoing"}
    created = ok(server, "pm_create_work_package_relation", id=17699, related_to_id=17054, relation_type="precedes")
    assert created == {"id": 6, "type": "precedes", "from_key": "AIS-408", "from_id": 17699, "from_subject": "x",
                       "to_key": "EXT-17054", "to_id": 17054, "to_subject": "y"}
    post = requests(fake, "POST", "work_packages/17699/relations")[-1]
    assert post[3] == {"type": "precedes", "_links": {"to": {"href": "/api/v3/work_packages/17054"}}}
    assert "Allowed: relates" in fail(server, "pm_create_work_package_relation", id=17699, related_to_id=17054, relation_type="parent")


# ─── Suite parity: go-mcp-openproject 0.4.0 (AIS-486, AIS-494) ────────────────


WP_REF_PARAMS = {
    "pm_get_work_package": ["id"], "pm_update_work_package": ["id", "parent"], "pm_comment_work_package": ["id"],
    "pm_list_work_package_activity": ["id"], "pm_list_work_package_relations": ["id"],
    "pm_create_work_package_relation": ["id", "related_to_id"], "pm_create_work_package": ["parent"],
    "pm_delete_work_package": ["id"], "pm_add_meeting_agenda_item": ["work_package_id"],
    "pm_list_time_entries": ["work_package_id"], "pm_create_time_entry": ["work_package_id"],
}


def test_every_work_package_id_parameter_takes_a_display_id(op):
    server, _ = op
    schemas = {t["name"]: t["inputSchema"]["properties"] for t in server.tool_definitions()}
    for name, params in WP_REF_PARAMS.items():
        for param in params:
            assert schemas[name][param]["anyOf"] == [{"type": "integer"}, {"type": "string"}], (name, param)


@pytest.mark.parametrize("ref", ["AIS-408", "ais-408", "#AIS-408", "#17699", "17699"])
def test_display_id_spellings_resolve_to_the_numeric_id(op, ref):
    server, fake = op
    assert ok(server, "pm_comment_work_package", id=ref, comment="x") == {"commented": True, "key": "AIS-408", "id": 17699}
    assert requests(fake, "POST", "work_packages/17699/activities")


def test_display_ids_in_relations_activity_agenda_and_time_entries(op):
    server, fake = op
    ok(server, "pm_create_work_package_relation", id="AIS-408", related_to_id="AIS-408", relation_type="relates")
    assert requests(fake, "POST", "work_packages/17699/relations")[-1][3]["_links"]["to"] == {"href": "/api/v3/work_packages/17699"}
    assert ok(server, "pm_list_work_package_relations", id="AIS-408")["relations"][0]["id"] == 5
    assert len(ok(server, "pm_list_work_package_activity", id="AIS-408")["activity"]) == 3
    item = ok(server, "pm_add_meeting_agenda_item", meeting_id=4, title="Topic", work_package_id="AIS-408")
    assert item["agenda_item"]["work_package_id"] == 17699
    ok(server, "pm_list_time_entries", work_package_id="AIS-408")
    filters = json.loads(requests(fake, "GET", "time_entries")[-1][2]["filters"])
    assert {"entity_id": {"operator": "=", "values": ["17699"]}} in filters
    assert fail(server, "pm_get_work_package", id="AIS-1").startswith("Not found in OpenProject")


def test_update_by_display_id_fetches_the_work_package_once(op):
    server, fake = op
    ok(server, "pm_update_work_package", id="AIS-408", subject="Renamed")
    assert len(requests(fake, "GET", "work_packages/AIS-408")) == 1
    assert not requests(fake, "GET", "work_packages/17699")
    assert requests(fake, "PATCH", "work_packages/17699")[-1][3]["lockVersion"] == 2


def test_rows_carry_key_id_parent_key_and_parent_id(op):
    """AIS-499: rows name work packages by key next to the numeric id; the
    0.4.0 display_id/parent_display_id are gone."""
    server, fake = op
    child = _wp(17711, display="AIS-411")
    child["_links"]["parent"] = {"href": "/api/v3/work_packages/17699", "title": "Own server", "displayId": "AIS-408"}
    # An instance without per-project numbering: the key is the id as text.
    plain = _wp(17712)
    del plain["displayId"]
    plain["_links"]["parent"] = {"href": "/api/v3/work_packages/17711", "title": "Child"}
    fake.search_results = [child, plain]
    rows = ok(server, "pm_list_work_packages")["work_packages"]
    assert [(r["key"], r["id"], r["parent_key"], r["parent_id"]) for r in rows] == [
        ("AIS-411", 17711, "AIS-408", 17699), ("17712", 17712, "17711", 17711)]
    assert not {"display_id", "parent_display_id"} & set(rows[0])
    assert ok(server, "pm_list_work_packages", select=["key", "parent_key"])["work_packages"][0] == {
        "key": "AIS-411", "parent_key": "AIS-408"}


def test_group_by_returns_groups_over_the_whole_result(op):
    server, fake = op
    fake.groups = [{"_type": "GroupBy", "value": "In Progress", "count": 12, "_links": {}},
                   {"_type": "GroupBy", "value": None, "count": 3, "_links": {}}]
    data = ok(server, "pm_list_work_packages", group_by="status")
    assert requests(fake, "GET", "work_packages")[-1][2]["groupBy"] == "status"
    assert data["groups"] == [{"value": "In Progress", "count": 12}, {"value": "", "count": 3}]
    fake.groups = None
    assert ok(server, "pm_search_work_packages", query="server", group_by="type")["groups"] == []


def test_search_returns_an_exact_match_for_a_key_or_id(op):
    server, fake = op
    data = ok(server, "pm_search_work_packages", query="ais-408", select=["id", "key"])
    assert data["exact_match"] == {"id": 17699, "key": "AIS-408"}
    assert {"search": {"operator": "**", "values": ["ais-408"]}} in json.loads(requests(fake, "GET", "work_packages")[-1][2]["filters"])
    assert ok(server, "pm_search_work_packages", query="#17054")["exact_match"]["subject"] == "EVN Ongoing"
    assert "exact_match" not in ok(server, "pm_search_work_packages", query="AIS-1")  # 404 -> no exact match
    calls = len(fake.calls)
    assert "exact_match" not in ok(server, "pm_search_work_packages", query="login timeout")
    assert not [c for c in fake.calls[calls:] if c[1].startswith("work_packages/")]  # no lookup for free text


def test_search_exact_match_respects_the_read_list(monkeypatch):
    server = _load(monkeypatch, read="AIS,PRO")
    fake = FakeOpenProject()
    client = httpx.Client(transport=httpx.MockTransport(fake), base_url="https://op.example.com/api/v3/")
    monkeypatch.setattr(server, "_http", lambda: client)
    assert "exact_match" not in ok(server, "pm_search_work_packages", query="17695")  # MOAP
    assert ok(server, "pm_search_work_packages", query="17054")["exact_match"]["id"] == 17054


@pytest.mark.parametrize("status,expected", [
    ("open", {"status": {"operator": "=", "values": OPEN_IDS}}),
    ("Closed", {"status": {"operator": "=", "values": ["12", "14"]}}),
    ("in review", {"status": {"operator": "=", "values": ["18"]}}),
    ("12", {"status": {"operator": "=", "values": ["12"]}}),
])
def test_status_names_and_open_closed_operators(op, status, expected):
    server, fake = op
    ok(server, "pm_list_work_packages", status=status)
    filters = json.loads(requests(fake, "GET", "work_packages")[-1][2]["filters"])
    assert [f for f in filters if "status" in f] == [expected]


def test_status_names_must_match_exactly(op):
    server, _ = op
    assert fail(server, "pm_list_work_packages", status="Progress").startswith('Unknown OpenProject status "Progress".')
    assert fail(server, "pm_create_work_package", project="PRO", type="Bu", subject="x").startswith('Unknown OpenProject type "Bu".')


def test_open_only_never_drops_an_explicit_status(op):
    server, fake = op
    ok(server, "pm_list_work_packages", status="In Review", open_only=True)
    filters = json.loads(requests(fake, "GET", "work_packages")[-1][2]["filters"])
    assert [f for f in filters if "status" in f] == [{"status": {"operator": "=", "values": ["18"]}}]
    ok(server, "pm_list_work_packages", status="open", open_only=True)
    filters = json.loads(requests(fake, "GET", "work_packages")[-1][2]["filters"])
    assert [f for f in filters if "status" in f] == [{"status": {"operator": "=", "values": OPEN_IDS}}]


def test_open_only_excludes_done(op):
    """AIS-518/526 (SUP-20261005-103125): "Done" is finished, not open, even
    though OpenProject does not mark it closed."""
    server, fake = op
    ok(server, "pm_list_work_packages", project="PRO", open_only=True)
    filters = json.loads(requests(fake, "GET", "work_packages")[-1][2]["filters"])
    status_filter = [f for f in filters if "status" in f]
    assert status_filter == [{"status": {"operator": "=", "values": OPEN_IDS}}]
    assert "12" not in status_filter[0]["status"]["values"]


def test_open_falls_back_to_openprojects_operator_without_status_data(op, monkeypatch):
    server, fake = op
    monkeypatch.setattr(server, "_statuses", lambda: [])
    ok(server, "pm_list_work_packages", status="open")
    filters = json.loads(requests(fake, "GET", "work_packages")[-1][2]["filters"])
    assert [f for f in filters if "status" in f] == [{"status": {"operator": "o", "values": []}}]


def test_open_only_contradicts_a_closed_status(op):
    server, fake = op
    calls = len(requests(fake, "GET", "work_packages"))
    assert fail(server, "pm_list_work_packages", status="Rejected", open_only=True) == \
        'status "Rejected" is a closed or finished status, which contradicts open_only=true'
    assert fail(server, "pm_list_work_packages", status="Done", open_only=True) == \
        'status "Done" is a closed or finished status, which contradicts open_only=true'
    assert fail(server, "pm_search_work_packages", query="x", status="closed", open_only=True) == \
        'status "closed" contradicts open_only=true'
    assert len(requests(fake, "GET", "work_packages")) == calls  # rejected before the request


def test_activity_user_names_for_href_only_links(op):
    server, fake = op
    fake.activity_user_titles = False
    data = ok(server, "pm_list_work_package_activity", id=17699)
    assert [a["user"] for a in data["activity"]] == ["Johannes Huchler", "Tobias Hehl", "Former Colleague"]
    assert len(requests(fake, "GET", "users/31")) == 1  # not in the directory -> fetched once
    assert not requests(fake, "GET", "users/14")


# ─── Suite parity: go-mcp-openproject 0.5.1 (AIS-499, AIS-511) ────────────────


def _server_with(monkeypatch, **env):
    server = _load(monkeypatch, **env)
    fake = FakeOpenProject()
    client = httpx.Client(transport=httpx.MockTransport(fake), base_url="https://op.example.com/api/v3/")
    monkeypatch.setattr(server, "_http", lambda: client)
    return server, fake


def key_lookups(fake):
    """The id lists of the batched key lookups (GET work_packages filtered by id)."""
    return [f["id"]["values"] for _, _, query, _ in requests(fake, "GET", "work_packages")
            for f in json.loads(query.get("filters", "[]")) if "id" in f]


@pytest.mark.parametrize("ref", ["46", "PRO", "pro", "Projektmanagement", "PROJEKTMANAGEMENT", " pro ", "PRO-12", "pro-12"])
def test_project_parameter_resolves_id_identifier_name_and_key(op, ref):
    server, fake = op
    assert ok(server, "pm_get_project", project=ref)["project"]["id"] == 46
    assert not [c for c in fake.calls if c[1].startswith("projects/")]  # served from the cached list


def test_identifier_that_looks_like_a_key_wins_over_its_prefix(op):
    server, fake = op
    fake.projects += [{"_type": "Project", "id": 131, "identifier": "release", "name": "Releases", "active": True, "public": False},
                      {"_type": "Project", "id": 130, "identifier": "release-2026", "name": "Release 2026", "active": True, "public": False}]
    assert ok(server, "pm_get_project", project="release-2026")["project"]["id"] == 130
    assert ok(server, "pm_get_project", project="release-7")["project"]["id"] == 131


def test_project_outside_the_cached_list_uses_the_exact_lookup(op):
    server, fake = op
    fake.hidden_projects = [{"_type": "Project", "id": 300, "identifier": "ARCHIVE", "name": "Archive", "active": False, "public": False}]
    assert ok(server, "pm_get_project", project="ARCHIVE")["project"]["id"] == 300
    assert requests(fake, "GET", "projects/ARCHIVE")


def test_unknown_project_names_the_parameter_and_the_closest_projects(op):
    server, _ = op
    assert fail(server, "pm_list_work_packages", project="manage") == (
        'Unknown OpenProject project "manage" (parameter project) — use an identifier or id from pm_list_projects. '
        "Closest: PRO (Projektmanagement, id 46)")
    # An identifier contained in the value counts as close too.
    assert fail(server, "pm_list_time_entries", project="ais-board").endswith("Closest: AIS (AIMDS Suite, id 107)")
    # Nothing shares text: the first projects of the list.
    assert fail(server, "pm_create_meeting", project="NOPE-17", title="x").endswith(
        "Closest: AIS (AIMDS Suite, id 107), PRO (Projektmanagement, id 46), MOAP (Mother of all Projects, id 109)")


def test_unknown_project_without_a_project_list(op):
    server, fake = op
    fake.errors[("GET", "projects")] = (500, {"message": "boom"})
    assert ok(server, "pm_get_project", project="AIS")["project"]["id"] == 107  # the exact lookup still works
    assert fail(server, "pm_get_project", project="nope") == \
        'Unknown OpenProject project "nope" (parameter project) — use an identifier or id from pm_list_projects.'


def test_every_project_parameter_resolves_the_same_way(op):
    server, fake = op

    def last_filters(path):
        return json.loads(requests(fake, "GET", path)[-1][2]["filters"])

    ok(server, "pm_list_work_packages", project="aimds suite")
    assert {"project": {"operator": "=", "values": ["107"]}} in last_filters("work_packages")
    ok(server, "pm_search_work_packages", query="x", project="AIS-408")
    assert {"project": {"operator": "=", "values": ["107"]}} in last_filters("work_packages")
    ok(server, "pm_list_time_entries", project="pro")
    assert {"project": {"operator": "=", "values": ["46"]}} in last_filters("time_entries")
    ok(server, "pm_list_boards", project="mother of all projects")
    assert {"project": {"operator": "=", "values": ["109"]}} in last_filters("queries")
    assert ok(server, "pm_list_users", project="ais")["project"] == "AIS"  # the identifier, not the raw value
    ok(server, "pm_create_work_package", project="projektmanagement", type="Task", subject="x")
    assert requests(fake, "POST", "projects/46/work_packages")
    ok(server, "pm_create_meeting", project="pro-5", title="Retro")
    assert requests(fake, "POST", "meetings")[-1][3]["_links"]["project"] == {"href": "/api/v3/projects/46"}
    ok(server, "pm_create_time_entry", activity="Development", spent_on="2026-09-24", hours="PT1H", project="Aimds Suite")
    assert requests(fake, "POST", "time_entries")[-1][3]["_links"]["project"] == {"href": "/api/v3/projects/107"}
    assert not [c for c in fake.calls if c[0] == "GET" and c[1].startswith("projects/")]


def test_project_resolution_respects_the_allow_lists(monkeypatch):
    server, fake = _server_with(monkeypatch, read="AIS,PRO", write="AIS")
    # Outside the read list: found by any spelling, then refused as before.
    for ref in ("moap", "Mother of all Projects", "MOAP-3", "109"):
        assert "outside the projects this assistant may read" in fail(server, "pm_list_work_packages", project=ref), ref
    # An unknown value never suggests an unreadable project.
    text = fail(server, "pm_get_project", project="mother")
    assert text.endswith("Closest: AIS (AIMDS Suite, id 107), PRO (Projektmanagement, id 46)") and "MOAP" not in text
    # Readable but not writable: the name resolves, the write is refused.
    assert "Writes are disabled for project PRO" in fail(server, "pm_create_work_package", project="projektmanagement",
                                                         type="Task", subject="x")
    assert ok(server, "pm_create_work_package", project="ais", type="Task", subject="x")["id"] == 17710


def test_relations_carry_keys_from_one_batched_lookup(op):
    server, fake = op
    fake.hidden_ids = {17054}  # not visible to the token: the key falls back to the id
    rel = ok(server, "pm_list_work_package_relations", id="AIS-408")["relations"][0]
    assert rel == {"id": 5, "type": "blocks", "from_key": "AIS-408", "from_id": 17699, "from_subject": "Own server",
                   "to_key": "17054", "to_id": 17054, "to_subject": "EVN Ongoing"}
    assert key_lookups(fake) == [["17054", "17699"]]  # both ends, one request
    calls = len(fake.calls)
    row = server._relation_row({"id": 7, "type": "relates", "_links": {
        "from": {"href": "/api/v3/work_packages/17699", "displayId": "AIS-408"}, "to": {"href": None}}})
    assert (row["from_key"], row["to_key"], row["to_id"]) == ("AIS-408", "", 0)
    assert len(fake.calls) == calls  # keys in the links: no lookup


def test_time_entries_carry_work_package_key_from_one_batched_lookup(op):
    server, fake = op
    fake.time_entries = [_time_entry(1, "2026-09-01", "PT1H", wp=17054), _time_entry(2, "2026-09-01", "PT2H", wp=17699, entity=False),
                         _time_entry(3, "2026-09-02", "PT1H", wp=17054), _time_entry(4, "2026-09-02", "PT30M", wp=None)]
    data = ok(server, "pm_list_time_entries")
    assert [(r["work_package_key"], r["work_package_id"]) for r in data["time_entries"]] == [
        ("EXT-17054", 17054), ("AIS-408", 17699), ("EXT-17054", 17054), ("", 0)]
    assert key_lookups(fake) == [["17054", "17699"]]  # the whole page, one request
    assert [(b["work_package_key"], b["hours"]) for b in data["by_work_package"]] == [
        ("EXT-17054", 2.0), ("AIS-408", 2.0), ("", 0.5)]
    # Created and updated entries carry the key as well (the fake books on 17054).
    created = ok(server, "pm_create_time_entry", activity="Development", spent_on="2026-09-24", hours="PT1H", work_package_id="AIS-408")
    assert (created["work_package_key"], created["work_package_id"]) == ("EXT-17054", 17054)
    assert ok(server, "pm_update_time_entry", id=55, hours="PT2H")["work_package_key"] == "EXT-17054"


def test_agenda_items_carry_work_package_key_from_one_batched_lookup(op):
    server, fake = op
    fake.agenda_items = [
        {"id": 27, "title": "Discuss", "notes": {"raw": ""}, "itemType": "simple", "_links": {}},
        {"id": 28, "title": "Ticket", "notes": {"raw": ""}, "itemType": "work_package",
         "_links": {"workPackage": {"href": "/api/v3/work_packages/17699", "title": "Own server"}}},
    ]
    items = ok(server, "pm_get_meeting", id=4)["agenda_items"]
    assert "work_package_key" not in items[0] and items[0]["outcomes"][0]["work_package_key"] == "EXT-17686"
    assert (items[1]["work_package_key"], items[1]["work_package_id"]) == ("AIS-408", 17699)
    assert key_lookups(fake) == [["17686", "17699"]]  # items and outcomes, one request
    plain = ok(server, "pm_add_meeting_agenda_item", meeting_id=4, title="No ticket")["agenda_item"]
    assert (plain["work_package_key"], plain["work_package_id"]) == ("", 0)
    assert len(key_lookups(fake)) == 1  # nothing to look up


@pytest.mark.parametrize("ref,expected", [("AIS", [4]), ("aimds suite", [4]), ("107", [4]), ("AIS-408", [4]), ("moap", [5])])
def test_meetings_filter_by_project_id(op, ref, expected):
    """The Suite compared the project with the link title (the name), so an
    identifier filtered out every meeting (AIS-499); both compare ids now."""
    server, _ = op
    assert [m["id"] for m in ok(server, "pm_list_meetings", project=ref)["meetings"]] == expected


def test_non_admin_directory_and_own_login_via_users_me(op):
    server, fake = op
    fake.users_forbidden = True  # /users is admin-only; principals hide login and e-mail
    for ref, user_id in (("Tobias Hehl", 20), ("jhuchler", 14), ("JH@example.com", 14)):
        ok(server, "pm_list_work_packages", assignee=ref)
        filters = json.loads(requests(fake, "GET", "work_packages")[-1][2]["filters"])
        assert {"assignee": {"operator": "=", "values": [str(user_id)]}} in filters, ref
    assert requests(fake, "GET", "principals") and requests(fake, "GET", "users/me")
    # Someone else's hidden login stays unknown.
    assert fail(server, "pm_list_work_packages", assignee="thehl").startswith('Unknown OpenProject user "thehl"')


def test_own_login_resolves_without_any_directory(op):
    server, fake = op
    fake.users_forbidden = True
    fake.errors[("GET", "principals")] = (403, {"message": "forbidden"})
    ok(server, "pm_create_time_entry", activity="Development", spent_on="2026-09-24", hours="PT1H", project="AIS", user="jhuchler")
    assert requests(fake, "POST", "time_entries")[-1][3]["_links"]["user"] == {"href": "/api/v3/users/14"}
    assert fail(server, "pm_list_work_packages", assignee="Tobias Hehl").startswith('Unknown OpenProject user "Tobias Hehl"')


def test_activities_are_id_name_rows(op):
    """AIS-511: time-entry activities are {id, name}, whatever the API adds."""
    server, fake = op
    fake.global_activities = [{"_type": "TimeEntriesActivity", "id": 3, "name": "Development", "position": 1, "default": True,
                               "_links": {"self": {"href": "/api/v3/time_entries/activities/3"}}}]
    assert ok(server, "pm_list_reference_data", kind="activities") == {"activities": [{"id": 3, "name": "Development"}]}


# ─── Work packages: writes ────────────────────────────────────────────────────


def test_create_work_package_resolves_names_and_writes_directly(op):
    server, fake = op
    row = ok(server, "pm_create_work_package", project="PRO", type="Bug", subject="Doc", description="```bash\necho\n```",
             status="Backlog", priority="High", assignee="me", parent="AIS-408", start_date="2026-10-01", estimated_time="PT8H")
    assert row["id"] == 17710 and row["subject"] == "Doc" and "warning" not in row
    post = requests(fake, "POST", "projects/46/work_packages")[-1][3]
    assert post["description"] == {"raw": "```bash\necho\n```"}
    assert post["_links"] == {
        "type": {"href": "/api/v3/types/7"}, "status": {"href": "/api/v3/statuses/15"},
        "priority": {"href": "/api/v3/priorities/9"}, "assignee": {"href": "/api/v3/users/14"},
        "parent": {"href": "/api/v3/work_packages/17699"},
    }
    assert post["startDate"] == "2026-10-01" and post["estimatedTime"] == "PT8H"


def test_create_requires_project_type_subject(op):
    server, _ = op
    assert fail(server, "pm_create_work_package", project="PRO", subject="x") == "project, type, and subject are required"


def test_create_warns_about_a_recent_duplicate_but_still_creates(op):
    server, fake = op
    recent = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=3)).isoformat()
    fake.search_results = [_wp(17705, 46, subject="Doc", created=recent)]
    row = ok(server, "pm_create_work_package", project="PRO", type="Task", subject="doc")
    assert row["id"] == 17710 and "same subject" in row["warning"]
    assert row["possible_duplicate"]["id"] == 17705
    assert requests(fake, "POST", "projects/46/work_packages")


def test_update_fetches_lock_version_and_retries_one_conflict(op):
    server, fake = op
    fake.conflicts = 1
    row = ok(server, "pm_update_work_package", id=17699, status="In Review", description="")
    assert row["id"] == 17699
    patches = requests(fake, "PATCH", "work_packages/17699")
    assert len(patches) == 2 and patches[0][3]["lockVersion"] == 2
    assert patches[0][3]["_links"]["status"] == {"href": "/api/v3/statuses/18"}
    assert patches[0][3]["description"] == {"raw": ""}  # an explicit empty description clears it


def test_update_conflict_twice_maps_to_retry_message(op):
    server, fake = op
    fake.conflicts = 2
    text = fail(server, "pm_update_work_package", id=17699, subject="x", lock_version=1)
    assert text.startswith("The work package changed concurrently; please retry:")


def test_update_validation_error_names_allowed_next_statuses(op):
    server, fake = op
    fake.errors[("PATCH", "work_packages/17699")] = (422, {"message": "Status is invalid", "_embedded": {"details": {"attribute": "status"}}})
    text = fail(server, "pm_update_work_package", id=17699, status="Done")
    assert text.startswith("OpenProject rejected the request: Status is invalid")
    assert "Allowed next: In Review" in text and text.endswith('(details: {"attribute":"status"})')


def test_move_to_another_project_and_ignored_move(op, monkeypatch):
    server, fake = op
    monkeypatch.setenv("OPENPROJECT_WRITE_PROJECTS", "AIS,PRO,MOAP")
    row = ok(server, "pm_update_work_package", id=17695, project="PRO", assignee="none")
    patch = requests(fake, "PATCH", "work_packages/17695")[-1][3]
    assert patch["_links"]["project"] == {"href": "/api/v3/projects/46"}
    assert patch["_links"]["assignee"] == {"href": None}
    assert row["project"] == "Projektmanagement"
    fake.patch_project = 109
    assert "did not move" in fail(server, "pm_update_work_package", id=17695, project="PRO")


def test_comment_and_delete(op):
    server, fake = op
    assert ok(server, "pm_comment_work_package", id=17699, comment="Looks good", notify=True) == \
        {"commented": True, "key": "AIS-408", "id": 17699}
    post = requests(fake, "POST", "work_packages/17699/activities")[-1]
    assert post[2] == {"notify": "true"} and post[3] == {"comment": {"raw": "Looks good"}}
    assert fail(server, "pm_comment_work_package", id=17699) == "id and comment are required"
    assert ok(server, "pm_delete_work_package", id=17699) == {"deleted": True, "key": "AIS-408", "id": 17699}
    assert requests(fake, "DELETE", "work_packages/17699")


# ─── Allow-lists ──────────────────────────────────────────────────────────────


def test_write_tools_refuse_projects_outside_the_write_list(op):
    server, fake = op
    text = fail(server, "pm_update_work_package", id=17695, subject="x")  # MOAP
    assert "Writes are disabled for project MOAP" in text and "OPENPROJECT_WRITE_PROJECTS" in text
    assert "Settings -> MCP -> OpenProjectMCP" in text
    assert "Writes are disabled for project MOAP" in fail(server, "pm_create_work_package", project="MOAP", type="Task", subject="x")
    assert "Writes are disabled" in fail(server, "pm_comment_work_package", id=17695, comment="x")
    assert "Writes are disabled" in fail(server, "pm_delete_work_package", id=17695)
    assert "Writes are disabled" in fail(server, "pm_create_meeting", project="MOAP", title="x")
    assert "Writes are disabled" in fail(server, "pm_create_time_entry", activity="Development", spent_on="2026-09-24",
                                         hours="PT1H", work_package_id=17695)
    assert not [c for c in fake.calls if c[0] in ("PATCH", "DELETE") or (c[0] == "POST" and "form" not in c[1])]


def test_read_only_setup_keeps_the_tools_but_refuses_writes(monkeypatch):
    server = _load(monkeypatch, write="")
    fake = FakeOpenProject()
    client = httpx.Client(transport=httpx.MockTransport(fake), base_url="https://op.example.com/api/v3/")
    monkeypatch.setattr(server, "_http", lambda: client)
    assert "pm_create_time_entry" in server.TOOLS
    for name, args in (
        ("pm_create_work_package", {"project": "AIS", "type": "Task", "subject": "x"}),
        ("pm_update_work_package", {"id": 17699, "subject": "x"}),
        ("pm_delete_time_entry", {"id": 1}),
        ("pm_create_work_package_relation", {"id": 17699, "related_to_id": 17054, "relation_type": "relates"}),
        ("pm_add_meeting_agenda_item", {"meeting_id": 4, "title": "x"}),
        ("pm_update_time_entry", {"id": 1, "hours": "PT1H"}),
    ):
        text = fail(server, name, **args)
        assert "writes are disabled (OPENPROJECT_WRITE_PROJECTS is empty)" in text, name
    assert not fake.calls  # refused before any request


def test_read_list_scopes_lists_and_refuses_single_reads(monkeypatch):
    server = _load(monkeypatch, read="AIS,PRO")
    fake = FakeOpenProject()
    client = httpx.Client(transport=httpx.MockTransport(fake), base_url="https://op.example.com/api/v3/")
    monkeypatch.setattr(server, "_http", lambda: client)
    assert [p["identifier"] for p in ok(server, "pm_list_projects")["projects"]] == ["AIS", "PRO"]
    ok(server, "pm_list_work_packages")
    filters = json.loads(requests(fake, "GET", "work_packages")[-1][2]["filters"])
    assert {"project": {"operator": "=", "values": ["107", "46"]}} in filters
    assert "outside the projects this assistant may read" in fail(server, "pm_get_work_package", id=17695)
    assert "outside the projects" in fail(server, "pm_list_work_packages", project="MOAP")
    assert [b["id"] for b in ok(server, "pm_list_boards")["boards"]] == [31]
    assert [m["id"] for m in ok(server, "pm_list_meetings")["meetings"]] == [4]
    ok(server, "pm_list_time_entries", spent_on_from="2026-09-01", spent_on_to="2026-09-30")
    filters = json.loads(requests(fake, "GET", "time_entries")[-1][2]["filters"])
    assert {"project": {"operator": "=", "values": ["107", "46"]}} in filters


# ─── Error mapping ────────────────────────────────────────────────────────────


@pytest.mark.parametrize("status,prefix", [
    (403, "OpenProject denied permission: openproject: forbidden: nope"),
    (404, "Not found in OpenProject: openproject: not found: nope"),
    (500, "OpenProject request failed: openproject: unexpected status 500: nope"),
])
def test_http_errors_map_to_the_suite_texts(op, status, prefix):
    server, fake = op
    fake.errors[("GET", "work_packages/17699")] = (status, {"message": "nope"})
    assert fail(server, "pm_get_work_package", id=17699) == prefix


def test_401_points_to_the_hermes_settings(op):
    server, fake = op
    fake.errors[("GET", "work_packages/17699")] = (401, {"message": "Unauthenticated"})
    text = fail(server, "pm_get_work_package", id=17699)
    assert "token was rejected" in text and "pm_link_account" not in text


def test_validation_details_and_nested_errors(op):
    server, fake = op
    fake.errors[("POST", "projects/107/work_packages")] = (
        422, {"message": "Multiple field constraints", "_embedded": {"errors": [{"message": "Subject can't be blank."}]}})
    text = fail(server, "pm_create_work_package", project="AIS", type="Task", subject="x")
    assert text == "OpenProject rejected the request: Multiple field constraints (Subject can't be blank.) (details: null)"


def test_network_errors_are_request_failures(op, monkeypatch):
    server, _ = op

    def boom(*_a, **_k):
        raise httpx.ConnectError("connection refused")

    client = httpx.Client(transport=httpx.MockTransport(boom), base_url="https://op.example.com/api/v3/")
    monkeypatch.setattr(server, "_http", lambda: client)
    text = fail(server, "pm_get_work_package", id=1)
    assert text.startswith("OpenProject request failed: openproject: request failed: ConnectError")


def test_unconfigured_server_says_where_to_configure(monkeypatch):
    """The local "not linked": a structured result without isError (AIS-486)."""
    server = _load(monkeypatch)
    monkeypatch.delenv("OPENPROJECT_BASE_URL")
    for name, args in (("pm_list_projects", {}), ("pm_list_work_packages", {"status": "Done"}),
                       ("pm_get_work_package", {"id": "AIS-408"})):
        data = ok(server, name, **args)
        assert data["linked"] is False and data["action_required"] == "configure_account", name
        assert data["next_step"].startswith("No OpenProject instance is configured yet.")


# ─── Boards and meetings ──────────────────────────────────────────────────────


def test_boards_list_and_get(op):
    server, fake = op
    data = ok(server, "pm_list_boards", project="AIS", search="Sprint")
    assert data["total"] == 2 and data["boards"][0]["columns"] == ["Subject"]
    assert set(data["boards"][0]) == {"id", "name", "filters", "columns", "groupBy", "sortBy", "_links"}
    filters = json.loads(requests(fake, "GET", "queries")[-1][2]["filters"])
    assert filters == [{"boards": {"operator": "=", "values": ["t"]}}, {"project": {"operator": "=", "values": ["107"]}},
                       {"name": {"operator": "~", "values": ["Sprint"]}}]
    board = ok(server, "pm_get_board", id=31, limit=5)
    assert board["board"]["groupBy"] == "Status" and board["work_packages"][0]["id"] == 17699
    assert "work_packages" not in ok(server, "pm_get_board", id=31, include_work_packages=False)


def test_meetings_list_get_create_and_agenda(op):
    server, fake = op
    listed = ok(server, "pm_list_meetings", project="AIS", state="open", **{"from": "2026-09-01", "to": "2026-09-30"})
    assert [m["id"] for m in listed["meetings"]] == [4] and listed["total"] == 2
    assert set(listed["meetings"][0]) == {"id", "title", "startTime", "duration", "location", "state", "_links"}
    got = ok(server, "pm_get_meeting", id=4)
    assert got["agenda_items"] == [{"id": 27, "title": "Discuss", "notes": "Some notes.", "type": "simple",
                                    "outcomes": [{"id": 9, "notes": "", "type": "work_package", "work_package_key": "EXT-17686",
                                                  "work_package_id": 17686}]}]
    assert "attendee" in got["note"]
    created = ok(server, "pm_create_meeting", project="AIS", title="Retro", start_time="2026-10-01T09:00:00Z", duration="PT1H")
    assert created["id"] == 8 and requests(fake, "POST", "meetings")[-1][3]["duration"] == "PT1H"
    item = ok(server, "pm_add_meeting_agenda_item", meeting_id=4, title="Topic", notes="n", work_package_id=17699)
    assert item == {"agenda_item": {"id": 28, "notes": "n", "title": "Topic", "type": "simple", "work_package_key": "AIS-408",
                                    "work_package_id": 17699}}
    outcome = ok(server, "pm_add_meeting_agenda_item", meeting_id=4, agenda_item_id=27, notes="Decided", outcome_kind="information")
    assert outcome == {"outcome": {"id": 10, "notes": "Decided", "type": "information"}}
    assert requests(fake, "POST", "meetings/4/agenda_items/27/outcomes")[-1][3] == {"notes": "Decided", "kind": "information"}
    assert "either title" in fail(server, "pm_add_meeting_agenda_item", meeting_id=4)


# ─── Time entries ─────────────────────────────────────────────────────────────


def test_list_time_entries_contract_fields_and_extras(op):
    server, fake = op
    fake.time_entries = [_time_entry(1, "2026-08-31", "PT4H"), _time_entry(2, "2026-09-01", "PT1H30M", entity=False)]
    data = ok(server, "pm_list_time_entries", project="AIS", user="me", spent_on_from="2026-08-01", spent_on_to="2026-09-30")
    filters = json.loads(requests(fake, "GET", "time_entries")[-1][2]["filters"])
    assert filters == [{"project": {"operator": "=", "values": ["107"]}}, {"user": {"operator": "=", "values": ["me"]}},
                       {"spentOn": {"operator": "<>d", "values": ["2026-08-01", "2026-09-30"]}}]
    assert data["total"] == 2 and data["has_more"] is False and data["next_offset"] is None and data["complete"] is True
    assert data["total_hours"] == 5.5 and data["booked_days"] == 2
    row = data["time_entries"][1]
    assert {k: row[k] for k in ("id", "hours", "spent_on", "work_package_id", "activity", "user", "project", "comment",
                                "ongoing", "start_time", "lock_version", "created_at")} == {
        "id": 2, "hours": "PT1H30M", "spent_on": "2026-09-01", "work_package_id": 17054, "activity": "Development",
        "user": "Johannes Huchler", "project": "AIMDS Suite", "comment": "work", "ongoing": False, "start_time": "",
        "lock_version": 1, "created_at": "2026-09-01T10:00:00Z"}
    assert row["duration_seconds"] == 5400 and row["work_package_subject"] == "EVN Ongoing"
    assert row["work_package_key"] == "EXT-17054" and "work_package_display_id" not in row
    assert data["by_work_package"] == [{"work_package_key": "EXT-17054", "work_package_id": 17054, "subject": "EVN Ongoing",
                                        "hours": 5.5, "booked_days": 2}]


def test_list_time_entries_paginates_by_page_number(op):
    server, fake = op
    fake.time_entries = [_time_entry(i, "2026-09-01", "PT1H") for i in range(1, 6)]
    first = ok(server, "pm_list_time_entries", limit=2)
    assert first["has_more"] is True and first["next_offset"] == 2 and first["complete"] is False and "hint" in first
    last = ok(server, "pm_list_time_entries", limit=2, offset=3)
    assert [r["id"] for r in last["time_entries"]] == [5] and last["has_more"] is False and last["next_offset"] is None


def test_work_package_filter_server_side_then_client_side(op):
    server, fake = op
    fake.time_entries = [_time_entry(1, "2026-09-01", "PT1H", wp=17054), _time_entry(2, "2026-09-01", "PT2H", wp=1)]
    ok(server, "pm_list_time_entries", work_package_id="17054")
    filters = json.loads(requests(fake, "GET", "time_entries")[-1][2]["filters"])
    assert {"entity_id": {"operator": "=", "values": ["17054"]}} in filters
    fake.reject_entity_filter = fake.reject_work_package_filter = True
    data = ok(server, "pm_list_time_entries", work_package_id=17054)
    assert [r["id"] for r in data["time_entries"]] == [1] and data["total"] == 1


def test_create_time_entry_with_hours_and_activity_name(op):
    server, fake = op
    row = ok(server, "pm_create_time_entry", activity="development", spent_on="2026-09-24", hours="1,5",
             work_package_id="AIS-408", comment="Pairing", user="thehl")
    post = requests(fake, "POST", "time_entries")[-1][3]
    assert post["hours"] == "PT1H30M" and post["comment"] == {"raw": "Pairing"}
    assert post["_links"] == {"activity": {"href": "/api/v3/time_entries/activities/3"},
                              "workPackage": {"href": "/api/v3/work_packages/17699"}, "user": {"href": "/api/v3/users/20"}}
    assert row["id"] == 99 and row["hours"] == "PT1H30M" and row["duration_seconds"] == 5400


def test_create_time_entry_validation_mirrors_the_suite(op):
    server, _ = op
    assert fail(server, "pm_create_time_entry", activity="Development") == "activity and spent_on are required"
    assert fail(server, "pm_create_time_entry", activity="Development", spent_on="2026-09-24", hours="PT1H") == \
        "at least one of work_package_id or project is required"
    assert fail(server, "pm_create_time_entry", activity="Development", spent_on="2026-09-24", project="AIS") == \
        "hours is required, or provide both start_time and end_time to compute it"
    assert fail(server, "pm_create_time_entry", activity="Development", spent_on="2026-09-24", project="AIS",
                hours="PT1H", start_time="2026-09-24T09:00:00Z", end_time="2026-09-24T10:00:00Z").startswith("end_time cannot be combined")
    assert fail(server, "pm_create_time_entry", activity="Development", spent_on="2026-09-24", project="AIS",
                start_time="2026-09-24T10:00:00Z", end_time="2026-09-24T09:00:00Z") == "end_time must be strictly after start_time"
    text = fail(server, "pm_create_time_entry", activity="Cooking", spent_on="2026-09-24", project="AIS", hours="PT1H")
    assert text.startswith('OpenProject time entry activity "Cooking" was not found — call pm_list_reference_data(kind="activities")')
    assert "Allowed: Development, Support" in text


@pytest.fixture
def berlin(monkeypatch):
    monkeypatch.setenv("HERMES_TIMEZONE", "Europe/Berlin")


def test_create_time_entry_from_clock_times(op, berlin):
    server, fake = op
    row = ok(server, "pm_create_time_entry", activity="Development", spent_on="2026-09-24", project="AIS",
             start_time="09:00", end_time="10:30")
    post = requests(fake, "POST", "time_entries")[-1][3]
    assert post["hours"] == "PT1H30M" and post["startTime"] == "2026-09-24T09:00:00+02:00"
    assert post["_links"]["project"] == {"href": "/api/v3/projects/107"}
    assert (row["start_time_local"], row["end_time_local"]) == ("09:00", "10:30")
    assert "warning" not in row


def test_create_time_entry_without_exact_time_tracking_warns(op, berlin):
    server, fake = op
    fake.exact_times = False
    row = ok(server, "pm_create_time_entry", activity="Development", spent_on="2026-09-24", project="AIS",
             start_time="2026-09-24T09:00:00+02:00", hours="PT1H")
    assert "startTime" not in requests(fake, "POST", "time_entries")[-1][3]
    assert "Exact time tracking is not enabled" in row["warning"]


def test_update_time_entry_lock_version_and_conflict_retry(op):
    server, fake = op
    fake.conflicts = 1
    row = ok(server, "pm_update_time_entry", id=55, hours="2h", comment="", activity="Support", ongoing=False)
    patches = requests(fake, "PATCH", "time_entries/55")
    assert len(patches) == 2
    assert patches[0][3] == {"lockVersion": 1, "hours": "PT2H", "comment": {"raw": ""}, "ongoing": False,
                             "_links": {"activity": {"href": "/api/v3/time_entries/activities/5"}}}
    assert row["id"] == 55
    assert fail(server, "pm_update_time_entry", id=55, end_time="2026-09-24T10:00:00Z") == \
        "end_time requires start_time in the same call to compute hours"


def test_delete_time_entry(op):
    server, fake = op
    assert ok(server, "pm_delete_time_entry", id=55) == {"deleted": True, "id": 55}
    assert requests(fake, "DELETE", "time_entries/55")
    assert fail(server, "pm_delete_time_entry") == "id is required"


# ─── Downstream: the ingestor reads these rows ────────────────────────────────


def test_time_entry_rows_feed_the_ingestor(op, berlin):
    server, fake = op
    from tools.mcp_json_ingestor import _extract_fields, _extract_items

    fake.time_entries = [_time_entry(7, "2026-09-02", "PT2H", start="2026-09-02T06:30:00Z")]
    items = _extract_items(ok(server, "pm_list_time_entries", project="AIS"))
    fields = _extract_fields(items[0], "mcp_op_pm_list_time_entries", "t1")
    assert fields[:6] == ("7", "mcp_op_pm_list_time_entries", "t1", "EXT-17054", "2026-09-02T08:30", "Johannes Huchler")
    assert fields[6] == 7200 and fields[7] == "Development"
    assert fields[10] == "EVN Ongoing"  # AIS-416: absences are recognised by the booked item's title


@pytest.mark.parametrize(
    "value,iso",
    [(1.5, "PT1H30M"), ("2", "PT2H"), ("0,25", "PT15M"), ("1h 15m", "PT1H15M"), ("90m", "PT1H30M"), ("pt3h", "PT3H")],
)
def test_hours_accept_decimal_and_duration_notation(monkeypatch, value, iso):
    server = _load(monkeypatch)
    assert server._hours_iso(value) == iso
