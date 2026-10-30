"""Bundled OpenProject MCP server (AIS-408, pm_* contract since AIS-479) against a fake OpenProject API."""

import asyncio
import datetime as dt
import importlib.util
import json
import threading
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
STATUSES = [{"id": 15, "name": "Backlog", "isClosed": False, "isDefault": True, "defaultDoneRatio": 0, "position": 1},
            {"id": 17, "name": "In Progress", "isClosed": False, "isDefault": False, "defaultDoneRatio": 30, "position": 2},
            {"id": 18, "name": "In Review", "isClosed": False, "isDefault": False, "defaultDoneRatio": 70, "position": 3},
            {"id": 12, "name": "Done", "isClosed": False, "isDefault": False, "defaultDoneRatio": 100, "position": 4},
            {"id": 14, "name": "Rejected", "isClosed": True, "isDefault": False, "defaultDoneRatio": 100, "position": 5}]
OPEN_IDS = ["15", "17", "18"]
TYPES = [{"id": 1, "name": "Task", "isDefault": True}, {"id": 7, "name": "Bug", "isDefault": False}]
PRIORITIES = [{"id": 8, "name": "Normal", "isDefault": True}, {"id": 9, "name": "High", "isDefault": False}]
ACTIVITIES = [{"id": 3, "name": "Development"}, {"id": 5, "name": "Support"}]
USERS = [
    {"id": 14, "name": "Johannes Huchler", "login": "jhuchler", "email": "jh@example.com"},
    {"id": 20, "name": "Tobias Hehl", "login": "thehl", "email": "th@example.com"},
]
# Versions and categories of a project (AIS-762).
VERSIONS = [{"id": 10, "name": "2.17.10"}, {"id": 11, "name": "2.17.11"}]
CATEGORIES = [{"id": 5, "name": "Docs"}]
LINK_TITLES = {
    "responsible": {u["id"]: u["name"] for u in USERS},
    "version": {v["id"]: v["name"] for v in VERSIONS},
    "category": {c["id"]: c["name"] for c in CATEGORIES},
}


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
        self.types = list(TYPES)
        self.workflow = None  # {from status id: [allowed status ids]}; None = every transition allowed
        self.status_of = {}  # work package id -> status id saved by a PATCH
        self.saved_fields = {}  # work package id -> fields saved by a PATCH
        self.saved_links = {}  # work package id -> responsible/version/category links saved by a PATCH

    def _status(self, wid):
        if wid in self.status_of:
            return self.status_of[wid]
        wp = self.work_packages.get(str(wid))
        return int(wp["_links"]["status"]["href"].rsplit("/", 1)[-1]) if wp else 17

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
        if method == "GET" and path.startswith("projects/") and path.endswith(("/versions", "/categories")):
            return ok(_collection(VERSIONS if path.endswith("/versions") else CATEGORIES))
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
            return ok(_collection(self.types))
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
                wp = self.work_packages[ref]
                sid = self.status_of.get(wp["id"])
                return ok(_with_status(wp, sid) if sid else wp)
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
            current = self._status(wid)
            status_href = (body.get("_links") or {}).get("status", {}).get("href")
            if status_href:
                target = int(status_href.rsplit("/", 1)[-1])
                if self.workflow is not None and target not in self.workflow.get(current, []):
                    return httpx.Response(422, json={
                        "_type": "Error", "message": "Status is invalid because no valid transition exists from old to new status.",
                        "_embedded": {"details": {"attribute": "status"}}})
                self.status_of[wid] = current = target
            base = self.work_packages.get(str(wid)) or _wp(wid)
            fields = self.saved_fields.setdefault(wid, {})
            fields.update({k: body[k] for k in ("subject", "startDate", "dueDate", "date") if k in body})
            saved = _with_status(_wp(wid, project, subject=base["subject"], display=base["displayId"], lock=body["lockVersion"] + 1), current)
            saved.update(fields)
            # Links set by a PATCH stay, titled like OpenProject does (AIS-762).
            saved_links = self.saved_links.setdefault(wid, {})
            for name, link in (body.get("_links") or {}).items():
                if name in LINK_TITLES:
                    href = link.get("href")
                    title = LINK_TITLES[name].get(int(href.rsplit("/", 1)[-1])) if href else None
                    saved_links[name] = {"href": href, "title": title} if href else {"href": None}
            saved["_links"].update(saved_links)
            return ok(saved)
        if method == "POST" and path.endswith("/form") and path.startswith("work_packages/"):
            wid = int(path.split("/")[1])
            allowed = [STATUSES[2]] if self.workflow is None else [
                s for s in STATUSES if s["id"] in self.workflow.get(self._status(wid), [])]
            return ok({"_embedded": {"payload": body, "schema": {"status": {"_embedded": {"allowedValues": allowed}}},
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
        if method == "GET" and path.startswith("relations/"):
            if path == "relations/5":
                return ok({"id": 5, "type": "blocks", "_links": {
                    "from": {"href": "/api/v3/work_packages/17699", "title": "Own server"},
                    "to": {"href": "/api/v3/work_packages/17054", "title": "EVN Ongoing"}}})
            return err(404, "The requested resource could not be found.")
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


def _with_status(wp, status_id):
    out = json.loads(json.dumps(wp))
    name = next(s["name"] for s in STATUSES if s["id"] == status_id)
    out["_links"]["status"] = {"href": f"/api/v3/statuses/{status_id}", "title": name}
    return out


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
                        "version", "category",  # go-mcp-openproject 0.6.2 (AIS-762)
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
    assert fail(server, "pm_get_work_package") == "id (or ids) is required"
    assert fail(server, "pm_get_work_package", id="abc") == \
        'invalid arguments: "abc" is neither a work package key like "AIS-469" nor a numeric id'
    assert fail(server, "pm_get_work_package", id=1.5) == \
        'invalid arguments: work package id must be a key like "AIS-469" or a number'
    assert fail(server, "pm_get_work_package", id=-3) == "id (or ids) is required"
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


def test_list_takes_the_query_and_search_is_its_deprecated_alias(op):
    """go-mcp-openproject 0.6.0 (AIS-709): free-text search is
    pm_list_work_packages(query); pm_search_work_packages answers the same."""
    server, fake = op
    listed = ok(server, "pm_list_work_packages", query="AIS-408", assignee="me")
    filters = json.loads(requests(fake, "GET", "work_packages")[-1][2]["filters"])
    assert {"search": {"operator": "**", "values": ["AIS-408"]}} in filters
    assert listed["exact_match"]["key"] == "AIS-408"
    assert ok(server, "pm_search_work_packages", query="AIS-408", assignee="me") == listed
    descriptions = {t["name"]: t["description"] for t in server.tool_definitions()}
    assert descriptions["pm_search_work_packages"].startswith("DEPRECATED")
    assert "exact_match" not in ok(server, "pm_list_work_packages")
    assert ok(server, "pm_list_work_packages", select=["*"])["work_packages"][0]["created_at"]


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


def test_status_names_match_exactly_or_one_partial_match(op):
    """go-mcp-openproject 0.6.0 (AIS-709): a unique partial name is taken
    ("review" -> "In Review"), several candidates stay an error naming them."""
    server, fake = op
    ok(server, "pm_list_work_packages", status="Progress")
    filters = json.loads(requests(fake, "GET", "work_packages")[-1][2]["filters"])
    assert {"status": {"operator": "=", "values": ["17"]}} in filters
    assert fail(server, "pm_list_work_packages", status="e").startswith('OpenProject status "e" is ambiguous: In Progress, In Review,')
    ok(server, "pm_create_work_package", project="PRO", type="Bu", subject="x")
    assert requests(fake, "POST", "projects/46/work_packages")[-1][3]["_links"]["type"] == {"href": "/api/v3/types/7"}
    assert fail(server, "pm_create_work_package", project="PRO", type="Feature", subject="x").startswith(
        'Unknown OpenProject type "Feature". Allowed: Task, Bug — see pm_list_reference_data(kind="types").')


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


def test_refused_transition_answers_with_allowed_next_and_retry(op):
    """A workflow that forbids the direct change is an expected answer, not
    an error: updated=false, allowed_next and a ready-made retry (AIS-605/709)."""
    server, fake = op
    fake.workflow = {17: [18], 18: [12]}
    data = ok(server, "pm_update_work_package", id=17699, status="Done", subject="Renamed", description="long text")
    assert data["updated"] is False and data["reason"] == "status_transition_invalid"
    assert data["current_status"] == "In Progress" and data["allowed_next"] == ["In Review"]
    assert data["suggested_path"] == ["In Review", "Done"] and data["path_source"] == "guess"
    assert data["retry"] == {"id": "AIS-408", "status_path": ["In Review", "Done"], "subject": "Renamed"}
    assert "Add your description" in data["note"]
    retried = ok(server, "pm_update_work_package", **data["retry"])
    assert retried["status"] == "Done" and retried["steps"] == ["In Review", "Done"] and retried["subject"] == "Renamed"
    assert "warnings" not in retried


def test_a_non_status_validation_error_stays_an_error(op):
    server, fake = op
    fake.errors[("PATCH", "work_packages/17699")] = (422, {"message": "Subject is too long", "_embedded": {"details": {"attribute": "subject"}}})
    assert fail(server, "pm_update_work_package", id=17699, subject="x" * 300, status="In Review").startswith(
        "OpenProject rejected the request: Subject is too long")


def test_status_path_saves_every_hop_and_the_other_fields_with_the_first(op):
    server, fake = op
    fake.workflow = {17: [18], 18: [12, 17], 12: [18]}
    row = ok(server, "pm_update_work_package", id="AIS-408", status_path=["In Progress", "In Review", "Done"], subject="x")
    patches = [c[3] for c in requests(fake, "PATCH", "work_packages/17699")]
    assert [p["_links"]["status"]["href"] for p in patches] == ["/api/v3/statuses/18", "/api/v3/statuses/12"]
    assert patches[0]["subject"] == "x" and "subject" not in patches[1]
    assert patches[1]["lockVersion"] == patches[0]["lockVersion"] + 1
    assert row["status"] == "Done" and row["steps"] == ["In Review", "Done"] and row["next_statuses"] == ["In Review"]
    assert "disagree" in fail(server, "pm_update_work_package", id=17699, status="Done", status_path=["In Review"])
    assert "at most 4" in fail(server, "pm_update_work_package", id=17699, status_path=["Backlog"] * 5)


def test_a_refused_later_hop_reports_the_saved_steps(op):
    server, fake = op
    fake.workflow = {17: [18], 18: []}
    data = ok(server, "pm_update_work_package", id=17699, status_path=["In Review", "Done"], subject="kept")
    assert data["updated"] is True and data["steps_saved"] == ["In Review"]
    assert data["current_status"] == "In Review" and data["allowed_next"] == []
    assert "retry" not in data and "No known path" in data["note"]


def test_update_several_work_packages_with_ids(op):
    server, _ = op
    data = ok(server, "pm_update_work_package", ids=["AIS-408", 17054, 999], status="In Review")
    assert data["updated"] == 2 and data["failed"] == 1
    by_key = {r["key"]: r for r in data["results"]}
    assert by_key["AIS-408"]["status"] == "In Review" and by_key["17054"]["status"] == "In Review"
    assert by_key["999"]["error"].startswith("Not found in OpenProject")
    assert fail(server, "pm_update_work_package", status="Done") == "id (or ids) is required"
    assert fail(server, "pm_update_work_package", id=1, ids=[2], status="Done") == "pass either id or ids, not both"
    assert "at most 25" in fail(server, "pm_update_work_package", ids=list(range(1, 27)), status="Done")


def test_bulk_update_passes_refused_transitions_through(op):
    server, fake = op
    fake.workflow = {17: [18], 18: [12]}
    data = ok(server, "pm_update_work_package", ids=["AIS-408"], status="Done")
    entry = data["results"][0]
    assert data["updated"] == 0 and entry["updated"] is False
    assert entry["retry"] == {"id": "AIS-408", "status_path": ["In Review", "Done"]}


def test_create_links_relates_to_right_away(op):
    server, fake = op
    row = ok(server, "pm_create_work_package", project="PRO", type="Task", subject="Follow-up", relates_to=["AIS-408", "17054"])
    assert row["relations"] == [{"to": "AIS-408", "id": 6}, {"to": "17054", "id": 6}]
    posts = [c[3] for c in requests(fake, "POST", "work_packages/17710/relations")]
    assert posts == [{"type": "relates", "_links": {"to": {"href": "/api/v3/work_packages/17699"}}},
                     {"type": "relates", "_links": {"to": {"href": "/api/v3/work_packages/17054"}}}]
    # A relation that fails does not undo the create.
    row = ok(server, "pm_create_work_package", project="PRO", type="Task", subject="Other", relates_to=["AIS-1"])
    assert row["id"] == 17710 and row["relations"][0]["error"].startswith("Not found in OpenProject")


def test_milestones_take_one_date_and_cannot_be_a_parent(op):
    server, fake = op
    fake.types.append({"id": 2, "name": "Milestone", "isDefault": False, "isMilestone": True})
    ok(server, "pm_create_work_package", project="AIS", type="Milestone", subject="Week goal", due_date="2026-10-30")
    post = requests(fake, "POST", "projects/107/work_packages")[-1][3]
    assert post["date"] == "2026-10-30" and "dueDate" not in post
    assert "milestone" in fail(server, "pm_create_work_package", project="AIS", type="Milestone", subject="x",
                               start_date="2026-10-01", due_date="2026-10-02")
    assert "only for milestone" in fail(server, "pm_create_work_package", project="AIS", type="Task", subject="x", date="2026-10-02")
    fake.errors[("POST", "projects/107/work_packages")] = (
        422, {"message": "Parent is invalid", "_embedded": {"details": {"attribute": "parent"}}})
    text = fail(server, "pm_create_work_package", project="AIS", type="Task", subject="x", parent="AIS-408")
    assert text.startswith("OpenProject rejected the request: Parent is invalid") and "pass the milestone in relates_to" in text


def test_relation_pairs_are_idempotent_and_deletable(op):
    server, fake = op
    fake.errors[("POST", "work_packages/17699/relations")] = (422, {"message": "Relation already exists"})
    row = ok(server, "pm_create_work_package_relation", id=17699, related_to_id=17054, relation_type="relates")
    assert row["already_exists"] is True and row["id"] == 5
    deleted = ok(server, "pm_delete_work_package_relation", relation_id=5)
    assert deleted["deleted"] is True and deleted["from_key"] == "AIS-408"
    assert requests(fake, "DELETE", "relations/5")
    assert ok(server, "pm_delete_work_package_relation", id="AIS-408", related_to_id=17054)["deleted"] is True
    assert ok(server, "pm_delete_work_package_relation", relation_id=77) == {"deleted": False, "reason": "not_found", "relation_id": 77}
    assert ok(server, "pm_delete_work_package_relation", id=17699, related_to_id=17695)["reason"] == "not_found"
    assert fail(server, "pm_delete_work_package_relation") == "relation_id, or id and related_to_id, are required"

# ─── go-mcp-openproject 0.6.2 / 0.7.0 (AIS-762, AIS-771 → AIS-775) ────────────


def test_get_with_ids_and_include(op):
    """AIS-775: ids replaces a loop of gets, include the activity/relation tools."""
    server, fake = op
    data = ok(server, "pm_get_work_package", ids=["AIS-408", 17054, "AIS-1"])
    rows = data["work_packages"]
    assert [r["key"] for r in rows] == ["AIS-408", "17054", "AIS-1"]
    assert rows[0]["description"] == "A long description of the work" and rows[1]["subject"] == "EVN Ongoing"
    assert rows[2]["error"].startswith("Not found in OpenProject")  # one missing key does not fail the rest
    row = ok(server, "pm_get_work_package", id="AIS-408", include=["activity", "relations"], limit=1, text_limit=4)
    assert row["activity"] == [{"id": 3, "user": "Former Colleague", "created_at": "2026-01-03T00:00:00Z", "comment": "thir",
                                "comment_truncated": True}]
    assert row["relations"][0]["id"] == 5 and row["description"] == "A lo"
    # include answers exactly what the deprecated tools answer.
    assert ok(server, "pm_get_work_package", id=17699, include=["activity"])["activity"] == \
        ok(server, "pm_list_work_package_activity", id=17699)["activity"]
    assert ok(server, "pm_get_work_package", id=17699, include=["relations"])["relations"] == \
        ok(server, "pm_list_work_package_relations", id=17699)["relations"]
    assert fail(server, "pm_get_work_package", id=1, ids=[2]) == "pass either id or ids, not both"
    assert fail(server, "pm_get_work_package", ids=list(range(1, 27))) == "ids has 26 entries; at most 25 per call"
    assert fail(server, "pm_get_work_package", id=1, include=["history"]) == \
        'include "history" is unknown; use "activity" and/or "relations"'


def test_update_comments_and_relates_without_a_field_change(op):
    server, fake = op
    row = ok(server, "pm_update_work_package", id="AIS-408", comment="done", notify=True,
             add_relations=[{"to": 17054, "type": "follows"}], remove_relations=["17054"])
    assert row["commented"] is True and row["key"] == "AIS-408" and row["id"] == 17699
    assert [r.get("type") for r in row["relations"]] == ["follows", "blocks"] and row["relations"][1]["deleted"] is True
    assert not requests(fake, "PATCH", "work_packages/17699")  # nothing but comment and relations is written
    assert requests(fake, "POST", "work_packages/17699/relations")[-1][3] == {
        "type": "follows", "_links": {"to": {"href": "/api/v3/work_packages/17054"}}}
    comment = requests(fake, "POST", "work_packages/17699/activities")[-1]
    assert comment[2] == {"notify": "true"} and comment[3] == {"comment": {"raw": "done"}}
    assert fail(server, "pm_update_work_package", id=17699) == \
        "nothing to update: pass a field, comment, add_relations/remove_relations or project"


def test_update_saves_the_fields_before_comment_and_relations(op):
    server, fake = op
    row = ok(server, "pm_update_work_package", id=17699, subject="New", comment="renamed",
             add_relations=[{"to": "AIS-408", "type": "nonsense"}, {"to": 17054}])
    writes = [c[:2] for c in fake.calls if c[0] in ("PATCH", "POST") and "form" not in c[1]]
    assert writes == [("PATCH", "work_packages/17699"), ("POST", "work_packages/17699/relations"),
                      ("POST", "work_packages/17699/activities")]
    assert row["commented"] is True and row["subject"] == "New"
    assert row["relations"][0] == {"to": "AIS-408", "type": "nonsense",
                                   "error": f"Unknown relation_type 'nonsense'. Allowed: {', '.join(server._RELATION_TYPES)}."}
    assert row["relations"][1]["type"] == "relates"  # the default type
    fake.errors[("POST", "work_packages/17699/activities")] = (403, {"message": "no permission"})
    row = ok(server, "pm_update_work_package", id=17699, subject="Again", comment="x")
    assert "commented" not in row and row["warnings"][0].startswith("comment not added: OpenProject denied permission")


def test_bulk_update_comments_every_work_package(op):
    server, fake = op
    data = ok(server, "pm_update_work_package", ids=["AIS-408", 17054], comment="Released.")
    assert data["updated"] == 2 and all(r["commented"] is True for r in data["results"])
    assert len(requests(fake, "POST", "work_packages/17699/activities")) == 1
    assert len(requests(fake, "POST", "work_packages/17054/activities")) == 1


def test_refused_status_walk_repeats_comment_and_relations_in_retry(op):
    server, fake = op
    fake.workflow = {17: [18], 18: [12]}
    data = ok(server, "pm_update_work_package", id=17699, status="Done", comment="shipped", notify=True,
              add_relations=[{"to": 17054}], remove_relations=[17695], priority="High")
    assert data["updated"] is False
    assert data["retry"] == {"id": "AIS-408", "status_path": ["In Review", "Done"], "comment": "shipped", "notify": True,
                             "add_relations": [{"to": 17054}], "remove_relations": [17695], "priority": "High"}
    assert not requests(fake, "POST", "work_packages/17699/activities")  # the comment waits for the walk


def test_create_adds_relations_of_any_type(op):
    server, fake = op
    row = ok(server, "pm_create_work_package", project="AIS", type="Task", subject="Child",
             relates_to=["AIS-408"], add_relations=[{"to": 17054, "type": "precedes"}])
    assert row["relations"][0] == {"to": "AIS-408", "id": 6}
    assert row["relations"][1]["type"] == "precedes" and row["relations"][1]["to_id"] == 17054
    assert fail(server, "pm_create_work_package", project="AIS", type="Task", subject="x", add_relations=[{"type": "blocks"}]) == \
        "every add_relations entry needs to (a work package key or id)"
    assert not requests(fake, "POST", "projects/107/work_packages")[1:]  # the malformed call created nothing


def test_responsible_version_and_category(op):
    """go-mcp-openproject 0.6.2 (AIS-762): by name, unique part or id within
    the work package's project; "none" clears on update."""
    server, fake = op
    ok(server, "pm_create_work_package", project="AIS", type="Task", subject="Goal", responsible="thehl",
       version="2.17.11", category="docs")
    links = requests(fake, "POST", "projects/107/work_packages")[-1][3]["_links"]
    assert links["responsible"] == {"href": "/api/v3/users/20"}
    assert links["version"] == {"href": "/api/v3/versions/11"} and links["category"] == {"href": "/api/v3/categories/5"}
    row = ok(server, "pm_update_work_package", id=17699, responsible="me", version="none", category="Docs")
    patch = requests(fake, "PATCH", "work_packages/17699")[-1][3]["_links"]
    assert patch == {"responsible": {"href": "/api/v3/users/14"}, "version": {"href": None},
                     "category": {"href": "/api/v3/categories/5"}}
    assert "warnings" not in row and row["version"] == "" and row["category"] == "Docs"
    assert fail(server, "pm_update_work_package", id=17699, version="3.0") == \
        'Unknown version "3.0" in this project. Allowed: 2.17.10, 2.17.11.'
    assert fail(server, "pm_update_work_package", id=17699, version="2.17") == \
        'OpenProject version "2.17" is ambiguous: 2.17.10, 2.17.11.'


def test_unknown_arguments_are_rejected_before_any_request(op):
    server, fake = op
    assert fail(server, "pm_update_work_package", id=17699, projekt="PRO").startswith(
        "pm_update_work_package does not take projekt. Arguments: add_relations, assignee,")
    assert not fake.calls
    # The aliases keep their old optional arguments.
    ok(server, "pm_comment_work_package", id=17699, comment="x", notify=False)
    ok(server, "pm_search_work_packages", query="Own", project="AIS", limit=5)


# A subtree like the Suite's move fixture (consolidate_test.go): epic AIS-700
# in AIS (107) with children; PRO (46) enables Task, Epic and Milestone and
# shares version 2.17.11.
#
#   AIS-700 Epic, category Docs, version 2.17.10 (not shared with PRO)
#   ├─ AIS-701 Task, version 2.17.11 (shared)
#   │  └─ AIS-703 Task
#   ├─ AIS-702 Wochenziel (not enabled in PRO)
#   └─ AIS-704 Task, already in PRO
#
# Like OpenProject (go-mcp-openproject 0.7.1, AIS-780), a PATCH that moves a
# work package takes its subtree along, gives every moved one a PRO key, and
# on the children resets a type PRO does not enable to Task and clears
# categories and unshared versions.


def _tree_wp(wid, type_id, type_name, project, parent=0, **links):
    wp = _wp(wid, project, (15, "Backlog"), f"WP {wid}", display=f"AIS-{wid + 400}", lock=1)
    wp["_links"]["type"] = {"href": f"/api/v3/types/{type_id}", "title": type_name}
    wp["_links"]["category"] = {"href": None}
    wp["_links"]["version"] = {"href": None}
    if parent:
        wp["_links"]["parent"] = {"href": f"/api/v3/work_packages/{parent}", "title": ""}
    wp["_links"].update(links)
    return wp


class MoveFake(FakeOpenProject):
    """FakeOpenProject plus a work package tree, project types/versions/
    categories and PATCHes that keep the links they set."""

    def __init__(self):
        super().__init__()
        self.tree = {wp["id"]: wp for wp in (
            _tree_wp(300, 3, "Epic", 107, category={"href": "/api/v3/categories/5", "title": "Docs"},
                     version={"href": "/api/v3/versions/10", "title": "2.17.10"}),
            _tree_wp(301, 1, "Task", 107, 300, version={"href": "/api/v3/versions/11", "title": "2.17.11"}),
            _tree_wp(302, 4, "Wochenziel", 107, 300),
            _tree_wp(303, 1, "Task", 107, 301),
            _tree_wp(304, 1, "Task", 46, 300),
        )}
        self.fail_status = set()  # work package ids whose PATCH the target workflow refuses
        self.keep_project = set()  # work package ids OpenProject saves without moving them
        self.hold = None  # a threading.Event the epic's PATCH waits for (a slow move)
        self.lock = threading.Lock()

    def move_along(self, parent):
        """OpenProject's descendant handling on a project change."""
        for wid, wp in self.tree.items():
            if wp["_links"]["parent"].get("href") != f"/api/v3/work_packages/{parent}":
                continue
            links = wp["_links"]
            links["project"] = {"href": "/api/v3/projects/46", "title": "Projektmanagement"}
            wp["displayId"] = f"PRO-{wid - 200}"
            if _href_num(links["type"]) not in (1, 3, 6):
                links["type"] = {"href": "/api/v3/types/1", "title": "Task"}
            links["category"] = {"href": None}
            if _href_num(links["version"]) != 11:
                links["version"] = {"href": None}
            self.move_along(wid)

    def __call__(self, request):
        url = urlparse(str(request.url))
        path = url.path.split("/api/v3/", 1)[-1]
        query = {k: v[0] for k, v in parse_qs(url.query).items()}
        method = request.method
        body = json.loads(request.content) if request.content else None
        by_key = {wp["displayId"]: wp for wp in self.tree.values()}
        if (method, path) in self.errors:
            return super().__call__(request)
        if (method, path) == ("GET", "projects/46/types"):
            return self._log(request, _collection([{"id": 1, "name": "Task"}, {"id": 3, "name": "Epic"}, {"id": 6, "name": "Milestone"}]))
        if (method, path) == ("GET", "projects/46/versions"):
            return self._log(request, _collection([{"id": 11, "name": "2.17.11"}]))
        if (method, path) == ("GET", "projects/46/categories"):
            return self._log(request, _collection([{"id": 8, "name": "PRO-Docs"}]))
        if (method, path) == ("GET", "work_packages") and "parent" in query.get("filters", ""):
            filters = json.loads(query["filters"])
            parent = next(f["parent"]["values"][0] for f in filters if "parent" in f)
            assert {"status": {"operator": "*", "values": []}} in filters  # every status
            children = sorted((wp for wp in self.tree.values()
                               if wp["_links"]["parent"].get("href") == f"/api/v3/work_packages/{parent}"), key=lambda w: w["id"])
            offset, size = int(query.get("offset") or 1), int(query.get("pageSize") or 50)
            return self._log(request, _collection(children[(offset - 1) * size: offset * size], offset, size, len(children)))
        if method in ("GET", "PATCH") and path.startswith("work_packages/") and path.count("/") == 1:
            ref = path.split("/", 1)[1]
            wp = self.tree.get(int(ref)) if ref.isdigit() else by_key.get(ref)
            if wp is not None:
                if method == "PATCH" and wp["id"] == 300 and self.hold is not None:
                    assert self.hold.wait(10)
                with self.lock:
                    if method == "PATCH":
                        if wp["id"] in self.fail_status:
                            return self._log(request, {"_type": "Error", "message": "Status is invalid.",
                                                       "_embedded": {"details": {"attribute": "status"}}}, 422)
                        links = dict(body.get("_links") or {})
                        if wp["id"] in self.keep_project:
                            links.pop("project", None)
                        before = self.project_of(wp["id"])
                        wp["_links"].update(links)
                        wp["lockVersion"] += 1
                        if self.project_of(wp["id"]) == 46 and before != 46:
                            wp["displayId"] = f"PRO-{wp['id'] - 200}"
                            self.move_along(wp["id"])
                    return self._log(request, json.loads(json.dumps(wp)))
        return super().__call__(request)

    def _log(self, request, payload, status=200):
        url = urlparse(str(request.url))
        body = json.loads(request.content) if request.content else None
        self.calls.append((request.method, url.path.split("/api/v3/", 1)[-1],
                           {k: v[0] for k, v in parse_qs(url.query).items()}, body))
        return httpx.Response(status, json=payload)

    def project_of(self, wid):
        return _href_num(self.tree[wid]["_links"]["project"])

    def patches(self):
        return [c for c in self.calls if c[0] == "PATCH"]


def _href_num(link):
    href = (link or {}).get("href")
    return int(href.rsplit("/", 1)[-1]) if href else 0


@pytest.fixture
def move(monkeypatch):
    server = _load(monkeypatch)
    fake = MoveFake()
    client = httpx.Client(transport=httpx.MockTransport(fake), base_url="https://op.example.com/api/v3/")
    monkeypatch.setattr(server, "_http", lambda: client)
    monkeypatch.setattr(server.time, "sleep", lambda _s: None)
    yield server, fake
    if fake.hold is not None:
        fake.hold.set()  # never leave a move thread waiting


def test_move_preview_counts_the_whole_subtree(move):
    """go-mcp-openproject 0.7.1 (AIS-780): no flag needed — OpenProject moves
    the children anyway, so the preview counts them (a prod preview said
    to_move 1 while 195 moved)."""
    server, fake = move
    out = ok(server, "pm_update_work_package", id="AIS-700", project="PRO")
    assert not fake.patches()
    assert (out["dry_run"], out["ready"], out["to_move"], out["skipped"]) == (True, False, 4, 1)
    assert out["keys"] == ["AIS-700", "AIS-701", "AIS-702", "AIS-703"]  # top-down: epic first, grandchild last
    assert out["conflicts"] == [{"type": "Wochenziel", "count": 1, "allowed": ["Task", "Epic", "Milestone"]}]
    assert out["clears"] == {"category": 1, "version": 1}  # the epic's; the shared 2.17.11 stays
    assert out["by_type"] == {"Epic": 1, "Task": 2, "Wochenziel": 1} and out["project"] == "Projektmanagement"
    assert out["next_step"].startswith("Ask the user which type") and "batches" not in out
    child = ok(server, "pm_update_work_package", id="AIS-701", project="PRO")
    assert (child["to_move"], child["keys"], child["ready"]) == (2, ["AIS-701", "AIS-703"], True)
    assert child["parent_kept"] == ["AIS-701"] and 'parent="none"' in child["parent_note"]
    # include_descendants left the schema (0.7.1) but is still taken and changes nothing.
    assert ok(server, "pm_update_work_package", id="AIS-700", project="PRO", include_descendants=False) == out


def test_move_apply_refuses_open_conflicts(move):
    server, fake = move
    text = fail(server, "pm_update_work_package", id="AIS-700", project="PRO", dry_run=False)
    assert text.startswith('Not moved: type "Wochenziel" (1×) is not enabled. Map them with type_map')
    assert not fake.patches()


def test_move_apply_patches_the_top_then_fixes_children(move):
    server, fake = move
    out = ok(server, "pm_update_work_package", id="AIS-700", project="PRO", type_map={"wochenziel": "milestone"},
             dry_run=False)
    patches = fake.patches()
    # Only the epic is moved (OpenProject takes the subtree), then AIS-702 gets its mapped type.
    assert [c[1] for c in patches] == ["work_packages/300", "work_packages/302"]
    assert patches[0][3]["_links"] == {"project": {"href": "/api/v3/projects/46"}, "category": {"href": None},
                                       "version": {"href": None}}
    assert patches[1][3]["_links"] == {"type": {"href": "/api/v3/types/6"}}  # no project, no clears
    assert out["results"] == [{"from": "AIS-700", "key": "PRO-100", "cleared": ["category Docs", "version 2.17.10"],
                               "with_children": 3}]
    assert (out["in_target"], out["total"]) == (5, 5) and "not_moved" not in out and "continue" not in out
    assert out["renamed"]["AIS-700"] == "PRO-100" and out["renamed"]["AIS-702"] == "PRO-102"
    assert _href_num(fake.tree[302]["_links"]["type"]) == 6  # Milestone, not OpenProject's default type
    assert _href_num(fake.tree[301]["_links"]["version"]) == 11  # the shared version stays
    assert not server._moves  # a delivered result is forgotten


def test_move_with_an_explicit_version_patches_every_child(move):
    server, fake = move
    out = ok(server, "pm_update_work_package", id="AIS-701", project="PRO", version="2.17.11", dry_run=False)
    patches = fake.patches()
    assert [c[1] for c in patches] == ["work_packages/301", "work_packages/303"]
    assert patches[1][3]["_links"] == {"version": {"href": "/api/v3/versions/11"}}
    assert out["results"][0]["with_children"] == 1 and out["in_target"] == 2


def test_move_reports_a_failed_top(move):
    server, fake = move
    fake.fail_status = {300}
    out = ok(server, "pm_update_work_package", id="AIS-700", project="PRO", type_map={"Wochenziel": "Milestone"},
             dry_run=False)
    row = out["results"][0]
    assert row["key"] == row["from"] == "AIS-700" and "change the status first" in row["error"]
    # No follow-ups for a subtree that did not move; verification shows what stayed.
    assert len(fake.patches()) == 1
    assert (out["in_target"], out["total"]) == (1, 5)
    assert out["not_moved"] == ["AIS-700", "AIS-701", "AIS-702", "AIS-703"]


def test_slow_move_answers_in_progress_and_joins(move, monkeypatch):
    """AIS-782: a slow move answers in_progress instead of running into the
    MCP call's timeout, and calling again joins the same move instead of
    starting it anew."""
    server, fake = move
    monkeypatch.setattr(server, "_MOVE_WAIT_S", 0.05)
    fake.hold = threading.Event()
    args = {"id": "AIS-700", "project": "PRO", "type_map": {"Wochenziel": "Milestone"}, "dry_run": False}
    first = ok(server, "pm_update_work_package", **args)
    assert first["in_progress"] is True and first["project"] == "PRO" and first["running_for_s"] >= 0
    assert first["continue"] == {"project": "PRO", "dry_run": False, "id": "AIS-700",
                                 "type_map": {"Wochenziel": "Milestone"}}
    assert "joins this move" in first["next_step"]
    assert ok(server, "pm_update_work_package", **first["continue"])["in_progress"] is True
    fake.hold.set()
    monkeypatch.setattr(server, "_MOVE_WAIT_S", 5.0)
    out = ok(server, "pm_update_work_package", **first["continue"])
    assert "in_progress" not in out and out["in_target"] == 5
    # The joined calls get the one move's result: epic + one follow-up, nothing twice.
    assert [c[1] for c in fake.patches()] == ["work_packages/300", "work_packages/302"]
    assert not server._moves


def test_an_unfetched_move_result_is_kept_for_a_while(move, monkeypatch):
    server, fake = move
    monkeypatch.setattr(server, "_MOVE_WAIT_S", 0.01)
    fake.hold = threading.Event()
    args = {"id": "AIS-700", "project": "PRO", "type_map": {"Wochenziel": "Task"}, "dry_run": False}
    assert ok(server, "pm_update_work_package", **args)["in_progress"] is True
    fake.hold.set()  # nobody asks again: the result waits
    (key,) = server._moves
    assert server._moves[key].done.wait(5)
    assert server._move_job(key) is not None  # kept for the caller that asks again
    monkeypatch.setattr(server, "_MOVE_JOB_KEEP_S", 0.0)
    assert server._move_job(key) is None and not server._moves


def test_move_job_requests_get_the_slow_timeout_and_a_deadline(move, monkeypatch):
    server, fake = move
    seen = []
    real = server._request

    def spy(method, path, **kwargs):
        if method == "PATCH":
            seen.append((getattr(server._move_context, "request_timeout", None),
                         getattr(server._move_context, "deadline", 0.0) > 0))
        return real(method, path, **kwargs)

    monkeypatch.setattr(server, "_request", spy)
    ok(server, "pm_update_work_package", id="AIS-701", project="PRO", dry_run=False)
    assert seen == [(server._MOVE_REQUEST_TIMEOUT_S, True)]
    assert not getattr(server._move_context, "deadline", 0.0)  # the caller's thread has no deadline
    # Past the job's deadline a request fails instead of running on.
    server._move_context.deadline = 1.0
    try:
        with pytest.raises(server.RequestFailed, match="longer than 15 minutes"):
            real("GET", "work_packages/301")
    finally:
        server._move_context.deadline = 0.0


def test_move_takes_no_other_fields(move):
    server, fake = move
    text = fail(server, "pm_update_work_package", id="AIS-700", project="PRO", status="Done", comment="x", parent="none")
    assert text == ("A move takes only project, type_map, version, category and dry_run; "
                    "change comment, parent, status in a separate call.")
    assert not fake.calls
    # The 0.7.0 continue argument is gone with the batches.
    assert fail(server, "pm_update_work_package", id="AIS-700", project="PRO", move_offset=50).startswith(
        "pm_update_work_package does not take move_offset.")


def test_move_is_bounded_and_honours_the_write_list(move, monkeypatch):
    server, fake = move
    for wid in range(400, 896):  # 496 more children of the epic: 501 in the tree
        fake.tree[wid] = _tree_wp(wid, 1, "Task", 107, 300)
    assert fail(server, "pm_update_work_package", id="AIS-700", project="PRO").startswith(
        "More than 500 work packages; move part of the tree per call")
    del fake.tree[895]  # exactly 500
    assert ok(server, "pm_update_work_package", id="AIS-700", project="PRO")["to_move"] == 499
    assert not fake.patches()
    monkeypatch.setenv("OPENPROJECT_WRITE_PROJECTS", "AIS")
    assert "Writes are disabled for project PRO" in fail(server, "pm_update_work_package", id="AIS-702", project="PRO")


def test_move_without_permission_is_reported_per_work_package(move):
    server, fake = move
    fake.errors[("PATCH", "work_packages/301")] = (403, {"message": "You are not authorized"})
    out = ok(server, "pm_update_work_package", ids=["AIS-701", "AIS-702"], project="PRO",
             type_map={"Wochenziel": "Task"}, dry_run=False)
    assert out["results"][0]["key"] == "AIS-701"
    assert out["results"][0]["error"].endswith('Moving needs the permission "Move work packages" in both projects.')
    assert out["results"][1] == {"from": "AIS-702", "key": "PRO-102", "type": "Task"}
    assert (out["in_target"], out["total"], out["not_moved"]) == (1, 3, ["AIS-701", "AIS-703"])


def test_a_move_openproject_saves_in_place_is_not_reported_as_moved(move):
    server, fake = move
    fake.keep_project = {302}
    out = ok(server, "pm_update_work_package", id="AIS-702", project="PRO", type_map={"Wochenziel": "Task"}, dry_run=False)
    assert "kept it in its project" in out["results"][0]["error"] and out["not_moved"] == ["AIS-702"]


def test_update_sets_and_removes_parent(move):
    """go-mcp-openproject 0.7.1 (AIS-780): parent is part of the contract."""
    server, fake = move
    out = ok(server, "pm_update_work_package", id="AIS-703", parent="AIS-700")
    assert fake.patches()[-1][3]["_links"] == {"parent": {"href": "/api/v3/work_packages/300"}}
    assert out["parent_id"] == 300 and "warnings" not in out
    out = ok(server, "pm_update_work_package", id="AIS-703", parent="none")
    assert fake.patches()[-1][3]["_links"] == {"parent": {"href": None}} and out["parent_id"] == 0
    assert fail(server, "pm_update_work_package", id="AIS-703", parent=True) == \
        'parent must be a work package key or id, or "none" to remove it'
    # A refused parent (e.g. a milestone) names the milestone rule.
    fake.errors[("PATCH", "work_packages/303")] = (422, {"message": "Parent is invalid",
                                                         "_embedded": {"details": {"attribute": "parent"}}})
    assert "cannot be a parent" in fail(server, "pm_update_work_package", id="AIS-703", parent="AIS-702")


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


def test_list_time_entries_group_by_sums_every_project_in_one_call(op):
    """A day/week overview is one grouped call across all projects, not one
    listing per project (go-mcp-openproject 0.6.0, AIS-709)."""
    server, fake = op
    fake.time_entries = [_time_entry(1, "2026-10-26", "PT4H"), _time_entry(2, "2026-10-26", "PT1H30M", project=46),
                         _time_entry(3, "2026-10-27", "PT2H", wp=17699)]
    data = ok(server, "pm_list_time_entries", user="me", spent_on_from="2026-10-26", spent_on_to="2026-10-27", group_by="spent_on")
    assert len(requests(fake, "GET", "time_entries")) == 1
    filters = json.loads(requests(fake, "GET", "time_entries")[-1][2]["filters"])
    assert not [f for f in filters if "project" in f]  # read "*": no project scope at all
    assert data == {"total_hours": 7.5, "count": 3, "group_by": "spent_on",
                    "groups": [{"value": "2026-10-26", "hours": 5.5, "count": 2}, {"value": "2026-10-27", "hours": 2.0, "count": 1}]}
    by_wp = ok(server, "pm_list_time_entries", group_by="work_package")["groups"]
    assert by_wp[0] == {"value": "EXT-17054", "subject": "EVN Ongoing", "hours": 5.5, "count": 2}
    assert by_wp[1]["value"] == "AIS-408"
    assert ok(server, "pm_list_time_entries", group_by="project")["groups"][0] == {"value": "AIMDS Suite", "hours": 7.5, "count": 3}
    assert fail(server, "pm_list_time_entries", group_by="month").startswith("group_by must be one of project")


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
