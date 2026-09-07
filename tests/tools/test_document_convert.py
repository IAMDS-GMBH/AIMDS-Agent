"""tools.document_convert — Office/PDF → Markdown for read_file (AIS-294).

Support case SUP-20260907-073421: after the Teams download the model's
``read_file(saved_path)`` hit the binary guard and it fell back to terminal.
These tests pin the converter chain: the Suite Docling path is gated by
``/uptime/health`` + registered storage tools, every negative gate falls
through to the local libraries, and results are cached by mtime/size.

Run with:  python -m pytest tests/tools/test_document_convert.py -v
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from hermes_cli import iamds_suite as suite
from tools import document_convert as dc


HEALTH_UP = {
    "status": "healthy",
    "details": [
        {"name": "Docling Parser", "slug": "docling", "status": "up", "tier": "components"},
        {"name": "Customer Storage", "slug": "customer-storage", "status": "up", "tier": "mcp"},
    ],
}


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    home = tmp_path / "hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_DOCUMENT_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.delenv("HERMES_DOCUMENT_CONVERTER", raising=False)
    (home / ".env").write_text("", encoding="utf-8")
    for var in ("IAMDS_LITELLM_API_KEY", "IAMDS_LITELLM_BASE_URL", "OPENAI_BASE_URL"):
        monkeypatch.delenv(var, raising=False)
    try:
        from hermes_cli.config import invalidate_env_cache

        invalidate_env_cache()
    except Exception:
        pass
    monkeypatch.setattr(suite, "_flag_path", lambda: home / "state" / "iamds_suite_auth.json")
    monkeypatch.setattr(
        suite, "_mcp_status_for",
        lambda base_url: {"name": "AIMDSSuiteMCP", "url": "", "url_matches": None, "connected": None},
    )
    suite.clear_suite_health_cache()
    dc.reset_suite_cooldown()
    yield
    suite.clear_suite_health_cache()
    dc.reset_suite_cooldown()


def _select_model_provider(provider: str) -> None:
    """Write ``model.provider`` into the isolated HERMES_HOME config.yaml."""
    import os
    from pathlib import Path as _P

    home = _P(os.environ["HERMES_HOME"])
    (home / "config.yaml").write_text(f"model:\n  provider: {provider}\n  default: AIMDS-Suite-Auto\n", encoding="utf-8")
    try:
        from hermes_cli.config import invalidate_env_cache

        invalidate_env_cache()
    except Exception:
        pass


def _prod_key(monkeypatch, *, select_model: bool = True):
    monkeypatch.setenv("IAMDS_LITELLM_API_KEY", "sk-test-key-0123456789")
    monkeypatch.setenv("IAMDS_LITELLM_BASE_URL", "https://suite.example.test/litellm/v1")
    if select_model:
        _select_model_provider("aimds-suite-prod")
    try:
        from hermes_cli.config import invalidate_env_cache

        invalidate_env_cache()
    except Exception:
        pass


def _dev_key(monkeypatch):
    monkeypatch.setenv("IAMDS_LITELLM_DEV_API_KEY", "sk-dev-key-9876543210")
    monkeypatch.setenv("IAMDS_LITELLM_DEV_BASE_URL", "https://dev.suite.example.test/litellm/v1")
    try:
        from hermes_cli.config import invalidate_env_cache

        invalidate_env_cache()
    except Exception:
        pass


def _docx(path: Path, paragraphs=("Hello Docling", "Second paragraph")) -> Path:
    docx = pytest.importorskip("docx")
    document = docx.Document()
    for text in paragraphs:
        document.add_paragraph(text)
    table = document.add_table(rows=2, cols=2)
    table.cell(0, 0).text, table.cell(0, 1).text = "Name", "Days"
    table.cell(1, 0).text, table.cell(1, 1).text = "Anna", "3"
    document.save(str(path))
    return path


# --------------------------------------------------------------------------- classification

class TestClassification:
    @pytest.mark.parametrize("name", ["a.docx", "b.XLSX", "c.pptx", "d.pdf", "e.odt", "f.ods", "g.odp"])
    def test_convertible(self, name):
        assert dc.is_convertible_document(name)

    @pytest.mark.parametrize("name", ["a.doc", "b.xls", "c.ppt"])
    def test_legacy(self, name):
        assert dc.is_legacy_office_document(name)
        assert not dc.is_convertible_document(name)

    @pytest.mark.parametrize("name", ["a.txt", "b.md", "c.png", "noext"])
    def test_plain(self, name):
        assert not dc.is_convertible_document(name)
        assert not dc.is_legacy_office_document(name)

    def test_converter_mode_env_and_config(self, monkeypatch):
        assert dc.converter_mode({}) == "auto"
        assert dc.converter_mode({"documents": {"converter": "LOCAL"}}) == "local"
        assert dc.converter_mode({"documents": {"converter": "bogus"}}) == "auto"
        monkeypatch.setenv("HERMES_DOCUMENT_CONVERTER", "suite")
        assert dc.converter_mode({"documents": {"converter": "local"}}) == "suite"


# --------------------------------------------------------------------------- gate

class TestDoclingAvailability:
    def test_not_configured_without_key(self):
        gate = suite.docling_availability(health_fn=lambda url: (HEALTH_UP, 200, ""))
        assert gate.state == suite.DOCLING_NOT_CONFIGURED
        assert not gate.available

    def test_health_unreachable(self, monkeypatch):
        _prod_key(monkeypatch)
        gate = suite.docling_availability(health_fn=lambda url: (None, None, "Could not reach"))
        assert gate.state == suite.DOCLING_HEALTH_UNREACHABLE
        assert "Could not reach" in gate.reason
        assert gate.health_url == "https://suite.example.test/uptime/health"

    def test_health_404_is_unreachable(self, monkeypatch):
        _prod_key(monkeypatch)
        gate = suite.docling_availability(health_fn=lambda url: (None, 404, "Health endpoint returned HTTP 404."))
        assert gate.state == suite.DOCLING_HEALTH_UNREACHABLE

    def test_docling_down_and_missing(self, monkeypatch):
        _prod_key(monkeypatch)
        down = json.loads(json.dumps(HEALTH_UP))
        down["details"][0]["status"] = "down"
        assert suite.docling_availability(health_fn=lambda url: (down, 200, "")).state == suite.DOCLING_DOWN
        missing = {"status": "healthy", "details": [HEALTH_UP["details"][1]]}
        gate = suite.docling_availability(health_fn=lambda url: (missing, 200, ""))
        assert gate.state == suite.DOCLING_DOWN and gate.reason == "docling_missing"

    def test_storage_down(self, monkeypatch):
        _prod_key(monkeypatch)
        payload = {"status": "degraded", "details": [HEALTH_UP["details"][0], {"slug": "customer-storage", "status": "down"}]}
        assert suite.docling_availability(health_fn=lambda url: (payload, 200, "")).state == suite.DOCLING_STORAGE_DOWN

    def test_tools_missing_then_available(self, monkeypatch):
        _prod_key(monkeypatch)
        gate = suite.docling_availability(health_fn=lambda url: (HEALTH_UP, 200, ""), tools_present=lambda: False)
        assert gate.state == suite.DOCLING_TOOLS_MISSING
        gate = suite.docling_availability(health_fn=lambda url: (HEALTH_UP, 200, ""), tools_present=lambda: True)
        assert gate.state == suite.DOCLING_AVAILABLE and gate.available
        assert gate.to_dict()["available"] is True

    def test_uses_the_active_model_environment_not_prod(self, monkeypatch):
        """dev model → dev key + dev health URL, even though a prod key exists."""
        _prod_key(monkeypatch, select_model=False)
        _dev_key(monkeypatch)
        _select_model_provider("aimds-suite-dev")
        seen = []
        gate = suite.docling_availability(
            health_fn=lambda base: (seen.append(base), (HEALTH_UP, 200, ""))[1], tools_present=lambda: True
        )
        assert gate.available
        assert gate.provider == "aimds-suite-dev" and gate.key_env == "IAMDS_LITELLM_DEV_API_KEY"
        assert gate.health_url == "https://dev.suite.example.test/uptime/health"
        assert seen == ["https://dev.suite.example.test/litellm/v1"]

    def test_no_cross_environment_key_fallback(self, monkeypatch):
        """dev model without a dev key → needs_reauth, never the prod key."""
        _prod_key(monkeypatch, select_model=False)
        monkeypatch.setenv("IAMDS_LITELLM_DEV_BASE_URL", "https://dev.suite.example.test/litellm/v1")
        _select_model_provider("aimds-suite-dev")
        gate = suite.docling_availability(health_fn=lambda base: (HEALTH_UP, 200, ""), tools_present=lambda: True)
        assert gate.state == suite.DOCLING_NEEDS_REAUTH
        assert gate.reason == "key_missing (IAMDS_LITELLM_DEV_API_KEY)"
        assert gate.provider == "aimds-suite-dev"

    def test_non_suite_model_provider_is_not_configured(self, monkeypatch):
        _prod_key(monkeypatch, select_model=False)
        _select_model_provider("openai")
        gate = suite.docling_availability(health_fn=lambda base: (HEALTH_UP, 200, ""), tools_present=lambda: True)
        assert gate.state == suite.DOCLING_NOT_CONFIGURED
        assert "not an AIMDS-Suite environment" in gate.reason

    def test_mcp_pointing_at_another_environment_blocks(self, monkeypatch):
        _prod_key(monkeypatch)
        gate = suite.docling_availability(
            health_fn=lambda base: (HEALTH_UP, 200, ""),
            tools_present=lambda: True,
            mcp_status_fn=lambda base: {"url": "https://staging.suite.example.test/litellm/mcp/", "url_matches": False},
        )
        assert gate.state == suite.DOCLING_MCP_MISMATCH
        assert "staging.suite.example.test" in gate.reason and "aimds-suite-prod" in gate.reason

    def test_runtime_auth_failure_blocks(self, monkeypatch):
        _prod_key(monkeypatch)
        suite.mark_suite_auth_failure("aimds-suite-prod", 401, "token_not_found", source="test")
        gate = suite.docling_availability(health_fn=lambda url: (HEALTH_UP, 200, ""), tools_present=lambda: True)
        assert gate.state == suite.DOCLING_NEEDS_REAUTH and gate.reason == "runtime_401"


class TestHealthHelpers:
    def test_health_url_from_any_base(self):
        for base in ("https://h/litellm/v1", "https://h/litellm/mcp", "https://h/litellm", "https://h"):
            assert suite.suite_health_url(base) == "https://h/uptime/health"
        assert suite.suite_health_url("") == ""

    def test_service_state(self):
        assert suite.suite_service_state(HEALTH_UP, "docling") == "up"
        assert suite.suite_service_state({"details": [{"slug": "docling", "status": "down"}]}, "docling") == "down"
        assert suite.suite_service_state(HEALTH_UP, "nope") == "missing"
        assert suite.suite_service_state(None, "docling") == "missing"

    def test_fetch_suite_health_caches_positive_and_negative(self, monkeypatch):
        calls = []

        def fake(url, *, timeout=5.0):
            calls.append(url)
            return (HEALTH_UP, 200, "") if len(calls) == 1 else (None, None, "boom")

        monkeypatch.setattr(suite, "fetch_health_json", fake)
        payload, status, _ = suite.fetch_suite_health("https://h/litellm/v1")
        assert payload is HEALTH_UP and status == 200
        payload, _, _ = suite.fetch_suite_health("https://h/litellm/v1")
        assert payload is HEALTH_UP and len(calls) == 1  # cached
        suite.clear_suite_health_cache()
        payload, status, err = suite.fetch_suite_health("https://h/litellm/v1")
        assert payload is None and err == "boom" and len(calls) == 2
        suite.fetch_suite_health("https://h/litellm/v1")
        assert len(calls) == 2  # negative result cached too


# --------------------------------------------------------------------------- suite backend

class _FakeRegistry:
    def __init__(self, handlers):
        self._entries = [SimpleNamespace(name=n, handler=h) for n, h in handlers.items()]

    def _snapshot_entries(self):
        return list(self._entries)


def _install_suite_tools(monkeypatch, *, prepare=None, ingest=None, get_document=None, calls=None):
    calls = calls if calls is not None else []

    def ingest_handler(args, **kw):
        calls.append(("ingest", dict(args)))
        if args.get("upload_id"):
            return json.dumps({"result": json.dumps(ingest or {"document_id": f"upload:{args['upload_id']}"})})
        return json.dumps({"result": json.dumps(prepare or {"upload_url": "https://suite.example.test/customer-storage/upload", "max_size_bytes": 1000000})})

    def get_handler(args, **kw):
        calls.append(("get", dict(args)))
        return json.dumps({"result": get_document if get_document is not None else "---\ntitle: x\n---\n# Doc\n\nSuite text\n"})

    registry = _FakeRegistry({
        "mcp_AIMDSSuiteMCP_mcp_customer_storage_ingest_upload": ingest_handler,
        "mcp_AIMDSSuiteMCP_mcp_customer_storage_get_document": get_handler,
        "mcp_AIMDSSuiteMCP_mcp_memory_memory_context": lambda a, **k: "{}",
    })
    import tools.registry as registry_module

    monkeypatch.setattr(registry_module, "registry", registry)
    return calls


class _Response:
    def __init__(self, status_code, body=None, text=""):
        self.status_code = status_code
        self._body = body
        self.text = text

    def json(self):
        if self._body is None:
            raise ValueError("no json")
        return self._body


class TestSuiteBackend:
    def test_tools_present_matches_by_suffix(self, monkeypatch):
        assert not dc.suite_tools_present()
        _install_suite_tools(monkeypatch)
        assert dc.suite_tools_present()

    def test_full_flow(self, monkeypatch, tmp_path):
        _prod_key(monkeypatch)
        monkeypatch.setattr(suite, "fetch_suite_health", lambda base, **kw: (HEALTH_UP, 200, ""))
        calls = _install_suite_tools(monkeypatch)
        posted = {}

        def fake_post(url, files=None, headers=None, timeout=None, follow_redirects=None):
            posted.update(url=url, headers=headers, filename=files["file"][0])
            return _Response(200, {"upload_id": "abc123"})

        import httpx

        monkeypatch.setattr(httpx, "post", fake_post)
        src = _docx(tmp_path / "plan.docx")
        result = dc.convert_document(src)
        assert result.backend == dc.BACKEND_SUITE
        assert result.markdown == "# Doc\n\nSuite text\n"  # frontmatter stripped
        assert posted["url"].endswith("/customer-storage/upload")
        assert posted["headers"]["Authorization"] == "Bearer sk-test-key-0123456789"
        assert posted["filename"] == "plan.docx"
        assert calls[0] == ("ingest", {}) and calls[1] == ("ingest", {"upload_id": "abc123"})
        assert calls[2] == ("get", {"id": "upload:abc123", "format": "markdown"})
        # cached on second call — no further tool calls
        again = dc.convert_document(src)
        assert again.cached and again.backend == dc.BACKEND_SUITE and len(calls) == 3

    def test_full_flow_signs_with_the_active_environment_key(self, monkeypatch, tmp_path):
        _prod_key(monkeypatch, select_model=False)
        _dev_key(monkeypatch)
        _select_model_provider("aimds-suite-dev")
        monkeypatch.setattr(suite, "fetch_suite_health", lambda base, **kw: (HEALTH_UP, 200, ""))
        _install_suite_tools(monkeypatch)
        posted = {}
        import httpx

        monkeypatch.setattr(
            httpx, "post",
            lambda url, files=None, headers=None, timeout=None, follow_redirects=None: (
                posted.update(headers=headers), _Response(200, {"upload_id": "dev1"}))[1],
        )
        result = dc.convert_document(_docx(tmp_path / "plan.docx"))
        assert result.backend == dc.BACKEND_SUITE
        assert posted["headers"]["Authorization"] == "Bearer sk-dev-key-9876543210"

    def test_upload_404_sets_cooldown_and_falls_back(self, monkeypatch, tmp_path):
        _prod_key(monkeypatch)
        monkeypatch.setattr(suite, "fetch_suite_health", lambda base, **kw: (HEALTH_UP, 200, ""))
        _install_suite_tools(monkeypatch)
        import httpx

        monkeypatch.setattr(httpx, "post", lambda *a, **k: _Response(404, text="not found"))
        src = _docx(tmp_path / "plan.docx")
        result = dc.convert_document(src)
        assert result.backend.startswith("local-")
        assert result.suite_state == "endpoint_unavailable"
        assert "Hello Docling" in result.markdown
        gate = dc.suite_availability()
        assert gate.state == suite.DOCLING_ENDPOINT_UNAVAILABLE and "retry in" in gate.reason

    def test_upload_401_marks_reauth(self, monkeypatch, tmp_path):
        _prod_key(monkeypatch)
        monkeypatch.setattr(suite, "fetch_suite_health", lambda base, **kw: (HEALTH_UP, 200, ""))
        _install_suite_tools(monkeypatch)
        import httpx

        monkeypatch.setattr(httpx, "post", lambda *a, **k: _Response(401))
        result = dc.convert_document(_docx(tmp_path / "plan.docx"))
        assert result.backend.startswith("local-") and result.suite_state == "needs_reauth"
        assert "aimds-suite-prod" in suite.suite_auth_failures()

    def test_gate_negative_skips_suite_entirely(self, monkeypatch, tmp_path):
        _prod_key(monkeypatch)
        monkeypatch.setattr(suite, "fetch_suite_health", lambda base, **kw: (None, None, "down"))
        calls = _install_suite_tools(monkeypatch)
        result = dc.convert_document(_docx(tmp_path / "plan.docx"))
        assert result.backend.startswith("local-") and result.suite_state == "health_unreachable"
        assert calls == []

    def test_too_large_falls_back(self, monkeypatch, tmp_path):
        _prod_key(monkeypatch)
        monkeypatch.setattr(suite, "fetch_suite_health", lambda base, **kw: (HEALTH_UP, 200, ""))
        _install_suite_tools(monkeypatch, prepare={"upload_url": "https://x/upload", "max_size_bytes": 10})
        result = dc.convert_document(_docx(tmp_path / "plan.docx"))
        assert result.backend.startswith("local-") and result.suite_state == "too_large"

    def test_suite_mode_only_errors_when_unavailable(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HERMES_DOCUMENT_CONVERTER", "suite")
        with pytest.raises(dc.DocumentConvertError) as exc:
            dc.convert_document(_docx(tmp_path / "plan.docx"))
        assert exc.value.suite_state == "not_configured"
        assert any(r.startswith("suite:") for r in exc.value.reasons)

    def test_parse_tool_output_shapes(self):
        assert dc._parse_tool_output(json.dumps({"result": json.dumps({"a": 1})})) == {"a": 1}
        assert dc._parse_tool_output(json.dumps({"result": "plain text"})) == "plain text"
        assert dc._parse_tool_output({"upload_url": "u"}) == {"upload_url": "u"}
        with pytest.raises(RuntimeError):
            dc._parse_tool_output(json.dumps({"error": "nope"}))

    def test_document_text_variants(self):
        assert dc._document_text("x") == "x"
        assert dc._document_text({"markdown": "m"}) == "m"
        assert dc._document_text({"content": [{"type": "text", "text": "a"}, {"type": "text", "text": "b"}]}) == "a\nb"
        assert dc._document_text([{"text": "only"}]) == "only"
        assert dc._document_text({"other": 1}) == ""


# --------------------------------------------------------------------------- local backend

class TestLocalBackend:
    def test_docx(self, tmp_path):
        result = dc.convert_document(_docx(tmp_path / "a.docx"))
        assert result.backend in (dc.BACKEND_LOCAL_MARKITDOWN, dc.BACKEND_LOCAL_DOCX)
        assert "Hello Docling" in result.markdown and "Anna" in result.markdown
        assert Path(result.cache_path).exists()

    def test_docx_python_docx_fallback(self, tmp_path, monkeypatch):
        monkeypatch.setattr(dc, "_has_module", lambda name: name != "markitdown")
        result = dc.convert_document(_docx(tmp_path / "a.docx"))
        assert result.backend == dc.BACKEND_LOCAL_DOCX
        assert "| Name | Days |" in result.markdown and "| Anna | 3 |" in result.markdown

    def test_xlsx(self, tmp_path):
        openpyxl = pytest.importorskip("openpyxl")
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Urlaub"
        ws.append(["Name", "Von", "Bis"])
        ws.append(["Anna", "01.03.2026", "05.03.2026"])
        wb.save(str(tmp_path / "u.xlsx"))
        result = dc.convert_document(tmp_path / "u.xlsx")
        assert "Anna" in result.markdown and "01.03.2026" in result.markdown

    def test_pptx(self, tmp_path):
        pptx = pytest.importorskip("pptx")
        prs = pptx.Presentation()
        slide = prs.slides.add_slide(prs.slide_layouts[1])
        slide.shapes.title.text = "Kickoff"
        slide.placeholders[1].text = "First bullet"
        prs.save(str(tmp_path / "d.pptx"))
        result = dc.convert_document(tmp_path / "d.pptx")
        assert "Kickoff" in result.markdown and "First bullet" in result.markdown

    def test_pdf(self, tmp_path):
        pytest.importorskip("pypdf")
        fpdf = pytest.importorskip("fpdf")
        pdf = fpdf.FPDF()
        pdf.add_page()
        pdf.set_font("Helvetica", size=12)
        pdf.cell(0, 10, "PDF body text")
        pdf.output(str(tmp_path / "t.pdf"))
        result = dc.convert_document(tmp_path / "t.pdf")
        assert result.backend == dc.BACKEND_LOCAL_PYPDF
        assert "PDF body text" in result.markdown

    def test_odt_needs_suite(self, tmp_path):
        (tmp_path / "x.odt").write_bytes(b"PK\x03\x04junk")
        with pytest.raises(dc.DocumentConvertError) as exc:
            dc.convert_document(tmp_path / "x.odt")
        assert "AIMDS-Suite Docling" in str(exc.value)

    def test_missing_libs_hint(self, tmp_path, monkeypatch):
        monkeypatch.setattr(dc, "_has_module", lambda name: False)
        with pytest.raises(dc.DocumentConvertError) as exc:
            dc.convert_document(_docx(tmp_path / "a.docx"))
        assert "office extra" in str(exc.value)

    def test_legacy_and_missing_files(self, tmp_path):
        (tmp_path / "old.doc").write_bytes(b"\xd0\xcf\x11\xe0")
        with pytest.raises(dc.DocumentConvertError) as exc:
            dc.convert_document(tmp_path / "old.doc")
        assert "legacy" in str(exc.value)
        with pytest.raises(dc.DocumentConvertError):
            dc.convert_document(tmp_path / "nope.docx")
        with pytest.raises(dc.DocumentConvertError):
            dc.convert_document(tmp_path / "plain.txt")


# --------------------------------------------------------------------------- cache

class TestCache:
    def test_cache_invalidated_on_change(self, tmp_path, monkeypatch):
        src = _docx(tmp_path / "a.docx", ("Version one",))
        first = dc.convert_document(src)
        assert not first.cached and "Version one" in first.markdown
        assert dc.convert_document(src).cached
        import os

        _docx(src, ("Version two",))
        os.utime(src, ns=(src.stat().st_atime_ns, src.stat().st_mtime_ns + 5_000_000_000))
        second = dc.convert_document(src)
        assert not second.cached and "Version two" in second.markdown

    def test_cache_files_live_outside_the_vault(self, tmp_path):
        src = _docx(tmp_path / "vault" / "documents" / "a.docx") if (tmp_path / "vault" / "documents").mkdir(parents=True) is None else None
        result = dc.convert_document(src)
        assert Path(result.cache_path).parent == (tmp_path / "cache")
        assert list((tmp_path / "vault" / "documents").glob("*.md")) == []
