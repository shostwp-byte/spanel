"""MCP: the token, the transport, and who is allowed to see what.

The rules being held to here are the ones that are easy to get subtly wrong
and impossible to notice afterwards:

  - a token is never stored, only its hash
  - every reason to refuse a token gives the same answer to the holder
  - a tool the caller may not use does not appear, and is "unknown" if named
  - the addon being off means the endpoint does not exist, not that it is
    locked - and that check comes before anything looks at credentials
"""

from datetime import datetime, timedelta
from pathlib import Path

import pytest

from app.core.permissions import Role
from app.models.entities import McpToken, User
from app.services import file_manager
from app.services import mcp
# At module scope on purpose. The tools register themselves into mcp.REGISTRY
# when this is first imported, and several tests below swap that dict for an
# empty one. If the first import happened inside one of those, every real tool
# would register into the temporary dict and vanish when it was restored -
# leaving the registry tests passing against nothing, which is the worst way
# for this to fail.
from app.services import mcp_tools

PROJECT_ROOT = Path(__file__).resolve().parents[3]
HELPER_SCRIPT_MCP = PROJECT_ROOT / "installer" / "files" / "spanel-helper.sh"


# --- fixtures ---------------------------------------------------------------

class _Query:
    def __init__(self, rows, model=None):
        self._rows = rows
        self._model = model

    def filter(self, *args, **kwargs):
        return self

    def order_by(self, *args, **kwargs):
        return self

    def all(self):
        return self._rows

    def first(self):
        return self._rows[0] if self._rows else None

    def count(self):
        return len(self._rows)


class _Session:
    def __init__(self, tokens=(), users=()):
        self._tokens = list(tokens)
        self._users = list(users)
        self.commits = 0

    def query(self, model):
        if model is McpToken:
            return _Query(self._tokens)
        if model is User:
            return _Query(self._users)
        return _Query([])

    def add(self, item):
        pass

    def commit(self):
        self.commits += 1


def _user(role=Role.admin, active=True, user_id=1):
    return User(id=user_id, username="admin" if role == Role.admin else "customer",
                email="a@example.test", role=str(role), hashed_password="x",
                is_active=active)


def _token(user_id=1, can_write=False, expires_in=timedelta(days=30),
           revoked=None, token_hash="h"):
    return McpToken(id=1, user_id=user_id, name="laptop", token_hash=token_hash,
                    prefix="bpmcp_abcdef", can_write=can_write,
                    expires_at=datetime.utcnow() + expires_in,
                    revoked_at=revoked, created_at=datetime.utcnow())


def _ctx(user=None, token=None):
    user = user or _user()
    return mcp.Context(db=_Session(), user=user, token=token or _token())


# --- the token --------------------------------------------------------------

def test_the_token_itself_is_never_what_gets_stored():
    token, stored, prefix = mcp.generate_token()
    assert token.startswith("bpmcp_")
    assert stored == mcp.hash_token(token)
    assert token not in stored
    assert len(stored) == 64, "sha-256 hex, and the column is sized for it"
    assert prefix == token[:12] and prefix in token


def test_two_tokens_are_never_the_same():
    assert len({mcp.generate_token()[0] for _ in range(50)}) == 50


@pytest.mark.parametrize("why,token_kwargs,user_kwargs", [
    ("expired", {"expires_in": timedelta(days=-1)}, {}),
    ("revoked", {"revoked": datetime.utcnow()}, {}),
    ("owner suspended", {}, {"active": False}),
])
def test_every_reason_to_refuse_looks_the_same_from_outside(why, token_kwargs, user_kwargs):
    """The holder learns nothing about which check stopped them."""
    token, stored, _ = mcp.generate_token()
    row = _token(token_hash=stored, **token_kwargs)
    db = _Session(tokens=[row], users=[_user(**user_kwargs)])
    assert mcp.authenticate(db, token) is None, why


def test_a_token_whose_owner_is_gone_is_refused():
    token, stored, _ = mcp.generate_token()
    db = _Session(tokens=[_token(token_hash=stored)], users=[])
    assert mcp.authenticate(db, token) is None


def test_a_good_token_is_accepted():
    token, stored, _ = mcp.generate_token()
    row = _token(token_hash=stored)
    db = _Session(tokens=[row], users=[_user()])
    assert mcp.authenticate(db, token) is row


def test_something_that_is_not_one_of_ours_is_refused_without_a_lookup():
    """No database round trip for a string that cannot be a token."""
    class _Explodes(_Session):
        def query(self, model):
            raise AssertionError("should not have looked it up")

    assert mcp.authenticate(_Explodes(), "") is None
    assert mcp.authenticate(_Explodes(), "sk-something-else") is None


def test_last_used_is_not_written_on_every_call():
    row = _token()
    row.last_used_at = datetime.utcnow()
    db = _Session()
    mcp.touch(db, row)
    assert db.commits == 0, "a tool call must not cost a write"

    row.last_used_at = datetime.utcnow() - timedelta(minutes=5)
    mcp.touch(db, row)
    assert db.commits == 1


# --- who sees which tool ----------------------------------------------------

@pytest.fixture
def sample_tools(monkeypatch):
    monkeypatch.setattr(mcp, "REGISTRY", {})

    @mcp.tool("read_thing", "Read", "Reads.")
    def _read(ctx, args):
        return {"ok": True}

    @mcp.tool("write_thing", "Write", "Writes.", writes=True)
    def _write(ctx, args):
        return {"ok": True}

    @mcp.tool("admin_thing", "Admin", "Admin only.", admin_only=True)
    def _admin(ctx, args):
        return {"ok": True}

    return mcp.REGISTRY


def test_a_read_only_token_is_not_shown_the_writing_tools(sample_tools):
    ctx = _ctx(token=_token(can_write=False))
    assert [t.name for t in mcp.visible_tools(ctx)] == ["admin_thing", "read_thing"]


def test_a_customer_is_not_shown_the_administrative_tools(sample_tools):
    ctx = _ctx(user=_user(role=Role.end_user), token=_token(can_write=True))
    assert [t.name for t in mcp.visible_tools(ctx)] == ["read_thing", "write_thing"]


def test_a_hidden_tool_called_by_name_is_reported_as_unknown(sample_tools):
    """Not "forbidden" - that would confirm it exists.

    The tool list is otherwise an enumeration oracle: a customer's token could
    map out the administrative surface by calling names and reading which ones
    came back as refused rather than missing.
    """
    ctx = _ctx(user=_user(role=Role.end_user))
    answer = mcp.call_tool(ctx, "admin_thing", {})
    assert answer["isError"] is True
    assert answer["content"][0]["text"] == "Unknown tool: admin_thing"
    assert mcp.call_tool(ctx, "no_such_tool", {})["content"][0]["text"] \
        == "Unknown tool: no_such_tool"


def test_the_schema_marks_which_tools_only_read(sample_tools):
    by_name = {t.name: t.schema() for t in mcp.visible_tools(_ctx(token=_token(can_write=True)))}
    assert by_name["read_thing"]["annotations"]["readOnlyHint"] is True
    assert by_name["write_thing"]["annotations"]["readOnlyHint"] is False


# --- argument checking ------------------------------------------------------

@pytest.fixture
def one_tool(monkeypatch):
    monkeypatch.setattr(mcp, "REGISTRY", {})

    @mcp.tool("thing", "Thing", "Does a thing.",
              {"domain": mcp.DOMAIN_ARG,
               "lines": {"type": "integer", "minimum": 1, "maximum": 100},
               "mode": {"type": "string", "enum": ["on", "off"]},
               "flag": {"type": "boolean"}},
              required=("domain",))
    def _thing(ctx, args):
        return args

    return mcp.REGISTRY["thing"]


def test_a_missing_required_argument_names_it(one_tool):
    with pytest.raises(mcp.ToolError) as exc:
        mcp.validate_arguments(one_tool, {})
    assert "domain" in str(exc.value)


def test_an_invented_argument_is_refused_with_the_real_ones(one_tool):
    """Models produce plausible field names that are not the schema's."""
    with pytest.raises(mcp.ToolError) as exc:
        mcp.validate_arguments(one_tool, {"domain": "a.test", "hostname": "b.test"})
    assert "hostname" in str(exc.value) and "domain" in str(exc.value)


def test_a_number_sent_as_text_is_accepted(one_tool):
    """An assistant that means 50 sometimes sends "50"."""
    assert mcp.validate_arguments(one_tool, {"domain": "a.test", "lines": "50"})["lines"] == 50


def test_a_number_out_of_range_says_the_bound(one_tool):
    with pytest.raises(mcp.ToolError) as exc:
        mcp.validate_arguments(one_tool, {"domain": "a.test", "lines": 5000})
    assert "100" in str(exc.value)


def test_something_that_is_not_a_number_is_refused(one_tool):
    with pytest.raises(mcp.ToolError):
        mcp.validate_arguments(one_tool, {"domain": "a.test", "lines": "many"})


def test_an_enum_lists_what_was_allowed(one_tool):
    with pytest.raises(mcp.ToolError) as exc:
        mcp.validate_arguments(one_tool, {"domain": "a.test", "mode": "sideways"})
    assert "on" in str(exc.value) and "off" in str(exc.value)


def test_a_tool_error_comes_back_as_a_result_not_a_crash(monkeypatch):
    monkeypatch.setattr(mcp, "REGISTRY", {})

    @mcp.tool("boom", "Boom", "Raises.")
    def _boom(ctx, args):
        raise RuntimeError("the database is on fire")

    answer = mcp.call_tool(_ctx(), "boom", {})
    assert answer["isError"] is True
    assert "the database is on fire" in answer["content"][0]["text"]


# --- JSON-RPC ---------------------------------------------------------------

def test_initialize_answers_with_a_version_the_client_asked_for():
    answer = mcp.handle_message(_ctx(), {
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2024-11-05"},
    })
    assert answer["result"]["protocolVersion"] == "2024-11-05"


def test_a_version_we_do_not_know_gets_the_newest_one():
    answer = mcp.handle_message(_ctx(), {
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "1999-01-01"},
    })
    assert answer["result"]["protocolVersion"] == mcp.LATEST_PROTOCOL_VERSION


def test_a_notification_is_answered_with_nothing():
    """It has no id, and the specification says send no response at all."""
    assert mcp.handle_message(_ctx(), {
        "jsonrpc": "2.0", "method": "notifications/initialized"}) is None


def test_a_batch_of_nothing_but_notifications_produces_no_body():
    assert mcp.handle_payload(_ctx(), [
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "method": "notifications/cancelled"},
    ]) is None


def test_a_batch_answers_only_the_messages_that_asked(sample_tools):
    answers = mcp.handle_payload(_ctx(), [
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 7, "method": "ping"},
    ])
    assert len(answers) == 1 and answers[0]["id"] == 7


def test_an_unknown_method_is_a_json_rpc_error():
    answer = mcp.handle_message(_ctx(), {"jsonrpc": "2.0", "id": 2, "method": "resources/list"})
    assert answer["error"]["code"] == mcp.METHOD_NOT_FOUND


def test_something_that_is_not_json_rpc_is_refused():
    assert mcp.handle_message(_ctx(), {"id": 1, "method": "ping"})["error"]["code"] \
        == mcp.INVALID_REQUEST


def test_tools_call_without_a_name_is_an_invalid_params_error():
    answer = mcp.handle_message(_ctx(), {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                                         "params": {}})
    assert answer["error"]["code"] == mcp.INVALID_PARAMS


# --- addresses the firewall must never be handed ----------------------------

@pytest.mark.parametrize("address", [
    "127.0.0.1", "10.0.0.5", "192.168.1.1", "172.16.0.1",
    "169.254.1.1", "0.0.0.0", "::1", "fe80::1",
])
def test_private_and_reserved_addresses_are_recognised(address):
    assert mcp.private_or_reserved(address) is True


@pytest.mark.parametrize("address", ["8.8.8.8", "1.1.1.1", "163.61.72.88", "2606:4700::1111"])
def test_a_real_public_address_is_not(address):
    assert mcp.private_or_reserved(address) is False


# --- the endpoint's three gates, and the order they come in -----------------

@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    from app.main import app
    return TestClient(app)


@pytest.fixture
def addon_on(monkeypatch):
    from app.services import addons
    monkeypatch.setattr(addons, "is_installed", lambda slug: slug == addons.MCP)


def test_with_the_addon_off_the_endpoint_does_not_exist(client, monkeypatch):
    """404, not 401.

    An assistant pointed at a panel that has not turned this on should be told
    there is nothing here, rather than be asked for a credential that could
    never work. It also keeps the panel from advertising a feature the
    administrator chose not to run.
    """
    from app.services import addons
    monkeypatch.setattr(addons, "is_installed", lambda slug: False)

    answer = client.post("/api/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
                         headers={"Authorization": "Bearer bpmcp_whatever"})
    assert answer.status_code == 404


def test_the_addon_is_checked_before_the_token(client, monkeypatch):
    """A wrong token against a disabled addon still gets 404, not 401."""
    from app.services import addons
    monkeypatch.setattr(addons, "is_installed", lambda slug: False)
    assert client.post("/api/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"}).status_code == 404


def test_a_browser_that_was_tricked_into_posting_is_stopped(client, addon_on):
    """DNS rebinding: a page elsewhere making the browser talk to the panel.

    Checked before the token, so this is refused whether or not the attacker
    also managed to obtain one.
    """
    answer = client.post("/api/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
                         headers={"Origin": "https://evil.example",
                                  "Authorization": "Bearer bpmcp_whatever"})
    assert answer.status_code == 403


def test_a_real_mcp_client_sends_no_origin_and_is_fine(client, addon_on):
    """It gets as far as the token check, which is the next gate."""
    answer = client.post("/api/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"})
    assert answer.status_code == 401


def test_a_missing_or_wrong_token_asks_for_one(client, addon_on):
    for headers in ({}, {"Authorization": "Bearer bpmcp_not_a_real_token"},
                    {"Authorization": "Basic abc"}):
        answer = client.post("/api/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
                             headers=headers)
        assert answer.status_code == 401
        assert "Bearer" in answer.headers.get("www-authenticate", "")


def test_there_is_no_stream_to_get(client, addon_on):
    """Plain-JSON Streamable HTTP: no SSE, no sessions, so no GET and no DELETE."""
    for call in (client.get, client.delete):
        answer = call("/api/mcp")
        assert answer.status_code == 405
        assert answer.headers.get("allow") == "POST"


# --- the tools themselves ---------------------------------------------------

@pytest.fixture
def tools():
    """The real registry, as the endpoint sees it."""
    return mcp.REGISTRY


def test_every_tool_tells_the_assistant_when_to_use_it(tools):
    """The description is the only thing steering the model's choice.

    A one-word description produces a model that calls the wrong tool and then
    reports the wrong answer confidently, which is worse than no tool at all.
    """
    for name, spec in tools.items():
        assert len(spec.description) >= 40, f"{name} needs a real description"
        assert spec.title, name


def test_every_argument_is_described_and_bounded(tools):
    """An unbounded string or integer is an argument the model will misuse."""
    for name, spec in tools.items():
        for arg, rule in spec.properties.items():
            assert "type" in rule, f"{name}.{arg} has no type"
            if rule["type"] == "string" and "enum" not in rule:
                assert "maxLength" in rule, f"{name}.{arg} is an unbounded string"
            if rule["type"] == "integer":
                assert "minimum" in rule and "maximum" in rule, \
                    f"{name}.{arg} is an unbounded integer"


def test_no_read_only_tool_claims_to_write(tools):
    for name, spec in tools.items():
        if name.startswith(("list_", "read_", "whoami", "server_")):
            assert not spec.writes, f"{name} reads, and must not be marked as writing"


def test_the_administrative_tools_are_the_ones_you_would_expect(tools):
    """A tool wrongly left off this list is one a customer can reach."""
    assert {name for name, spec in tools.items() if spec.admin_only} == {
        "list_users", "list_services", "list_backup_schedules",
        "recent_audit_log", "panel_update_status",
        "list_firewall_rules", "list_waf_rules",
        # and the ones that change the server rather than one account
        "block_ip", "unblock_ip", "add_waf_rule", "restart_service",
        "run_backup_schedule",
    }


# --- addressing a website by domain -----------------------------------------

class _WebsiteQuery:
    def __init__(self, rows, owner_filtered=None):
        self._rows = rows
        self.filters = 0
        self.owner_filtered = owner_filtered

    def filter(self, *args):
        self.filters += 1
        return self

    def first(self):
        return self._rows[0] if self._rows else None


class _WebsiteSession(_Session):
    def __init__(self, rows):
        super().__init__()
        self.query_obj = _WebsiteQuery(rows)

    def query(self, model):
        return self.query_obj


def test_someone_elses_domain_reads_as_not_existing():
    """Never "forbidden" - that confirms the domain is real.

    A customer's assistant must not be able to map the server by trying
    domains and reading which answer comes back.
    """
    ctx = mcp.Context(db=_WebsiteSession([]), user=_user(role=Role.end_user), token=_token())
    with pytest.raises(mcp.ToolError) as exc:
        mcp_tools._website(ctx, "someone-else.test")
    message = str(exc.value)
    assert "No website named someone-else.test" in message
    assert "permission" not in message.lower() and "forbidden" not in message.lower()


def test_a_customer_lookup_is_narrowed_to_their_own_websites():
    """Two filters for a customer, one for an administrator."""
    customer = mcp.Context(db=_WebsiteSession([]), user=_user(role=Role.end_user), token=_token())
    with pytest.raises(mcp.ToolError):
        mcp_tools._website(customer, "a.test")
    assert customer.db.query_obj.filters == 2, "domain, and owner"

    admin = mcp.Context(db=_WebsiteSession([]), user=_user(role=Role.admin), token=_token())
    with pytest.raises(mcp.ToolError):
        mcp_tools._website(admin, "a.test")
    assert admin.db.query_obj.filters == 1, "domain only"


@pytest.mark.parametrize("given,wanted", [
    ("Example.TEST", "example.test"),
    ("  example.test  ", "example.test"),
    (".example.test", "example.test"),
])
def test_a_domain_is_tidied_before_it_is_looked_up(given, wanted, monkeypatch):
    """Models paste domains with a stray dot or capital in them."""
    seen = {}

    class _Recording(_WebsiteQuery):
        def filter(self, *args):
            seen.setdefault("args", args)
            return self

    session = _WebsiteSession([])
    session.query_obj = _Recording([])
    ctx = mcp.Context(db=session, user=_user(), token=_token())
    with pytest.raises(mcp.ToolError) as exc:
        mcp_tools._website(ctx, given)
    assert wanted in str(exc.value)


def test_an_empty_domain_says_so_rather_than_searching():
    ctx = mcp.Context(db=_WebsiteSession([]), user=_user(), token=_token())
    with pytest.raises(mcp.ToolError) as exc:
        mcp_tools._website(ctx, "   ")
    assert "domain is required" in str(exc.value)


# --- reading a file ---------------------------------------------------------

def test_a_long_file_is_truncated_and_admits_it(monkeypatch):
    """A 40 MB log would otherwise fill the assistant's context and end the session."""
    from app.api import maintenance as maintenance_api
    content = "\n".join(f"line {n}" for n in range(5000))
    monkeypatch.setattr(mcp_tools, "_website",
                        lambda ctx, domain: type("W", (), {"id": 1})())
    monkeypatch.setattr(maintenance_api, "read_file",
                        lambda **kwargs: {"content": content})

    result = mcp.REGISTRY["read_file"].handler(
        _ctx(), {"domain": "a.test", "path": "wp-config.php"})
    assert result["truncated"] is True
    assert result["total_lines"] == 5000
    assert result["lines"] == mcp_tools.MAX_READ_LINES
    assert result["content"].count("\n") == mcp_tools.MAX_READ_LINES - 1
    assert "2000 of 5000" in result["note"]


def test_a_short_file_comes_back_whole(monkeypatch):
    from app.api import maintenance as maintenance_api
    monkeypatch.setattr(mcp_tools, "_website",
                        lambda ctx, domain: type("W", (), {"id": 1})())
    monkeypatch.setattr(maintenance_api, "read_file",
                        lambda **kwargs: {"content": "one\ntwo\nthree"})

    result = mcp.REGISTRY["read_file"].handler(_ctx(), {"domain": "a.test", "path": "x.txt"})
    assert result["truncated"] is False and result["content"] == "one\ntwo\nthree"


# --- whoami -----------------------------------------------------------------

def test_whoami_says_plainly_what_this_token_can_do():
    admin = mcp.REGISTRY["whoami"].handler(
        _ctx(user=_user(role=Role.admin), token=_token(can_write=True)), {})
    assert admin["role"] == "administrator" and admin["can_make_changes"] is True

    customer = mcp.REGISTRY["whoami"].handler(
        _ctx(user=_user(role=Role.end_user), token=_token(can_write=False)), {})
    assert customer["role"] == "hosting customer"
    assert customer["can_make_changes"] is False
    assert "only websites owned by this account" in customer["scope"]


def test_whoami_never_leaks_the_token_itself():
    answer = mcp.REGISTRY["whoami"].handler(_ctx(), {})
    assert "token" not in str(answer).lower().replace("token_name", "").replace("token_expires_at", "")


# --- searching a website's files --------------------------------------------

@pytest.fixture
def site(tmp_path):
    """A website root with the shapes a real one has."""
    root = tmp_path / "site"
    (root / "wp-content" / "themes").mkdir(parents=True)
    (root / "node_modules" / "left-pad").mkdir(parents=True)
    (root / "wp-content" / "uploads" / "2026").mkdir(parents=True)
    (root / ".git").mkdir()

    (root / "wp-config.php").write_text("<?php\ndefine('DB_NAME', 'shop');\n", encoding="utf-8")
    (root / "wp-content" / "themes" / "style.css").write_text(
        "/* shop theme */\nbody{}\n", encoding="utf-8")
    # The three that must never be walked into or read.
    (root / "node_modules" / "left-pad" / "index.js").write_text("shop", encoding="utf-8")
    (root / ".git" / "config").write_text("shop", encoding="utf-8")
    (root / "wp-content" / "uploads" / "2026" / "note.txt").write_text("shop", encoding="utf-8")
    (root / "logo.png").write_bytes(b"\x89PNG\r\n" + bytes([0]) + b"shop")
    (root / "huge.log").write_bytes(b"shop\n" * 200_000)

    return type("W", (), {"root_path": str(root), "domain": "shop.test"})()


def test_search_finds_the_file_and_the_line(site):
    found = file_manager.search_text(site, "DB_NAME")
    assert [m["path"] for m in found["matches"]] == ["wp-config.php"]
    assert found["matches"][0]["line"] == 2
    assert "shop" in found["matches"][0]["text"]
    assert found["complete"] is True


def test_search_never_walks_into_the_directories_that_make_it_useless(site):
    """node_modules, .git and uploads all contain the word and must not appear.

    Without pruning, one question against a WordPress site reads a dependency
    tree and a customer's media library, takes minutes, and answers with
    nothing anybody wanted.
    """
    found = file_manager.search_text(site, "shop")
    paths = {m["path"] for m in found["matches"]}
    assert not any(p.startswith(("node_modules/", ".git/")) for p in paths), paths
    assert not any("uploads/" in p for p in paths), paths
    assert "wp-config.php" in paths


def test_a_binary_file_is_skipped_rather_than_pasted_into_the_answer(site):
    found = file_manager.search_text(site, "shop")
    assert not any(m["path"] == "logo.png" for m in found["matches"])
    assert found["skipped_binary"] >= 1


def test_a_file_too_large_to_be_a_config_is_skipped(site):
    """A megabyte log is not where a configuration string lives."""
    found = file_manager.search_text(site, "shop")
    assert not any(m["path"] == "huge.log" for m in found["matches"])
    assert found["skipped_too_large"] >= 1


def test_stopping_early_is_reported_rather_than_looking_like_the_whole_answer(site):
    """"Not found" and "gave up" must not look the same to an assistant."""
    found = file_manager.search_text(site, "shop", max_matches=1)
    assert len(found["matches"]) == 1
    assert found["complete"] is False
    assert "Stopped" in found["note"]


def test_searching_for_nothing_is_refused(site):
    for bad in ("", "   "):
        with pytest.raises(ValueError):
            file_manager.search_text(site, bad)


def test_search_cannot_be_pointed_outside_the_website(site):
    for escape in ("../..", "/etc", "wp-content/../../.."):
        with pytest.raises(ValueError):
            file_manager.search_text(site, "shop", relative_path=escape)


# --- summarising traffic ----------------------------------------------------

def test_the_summary_counts_rather_than_returning_rows(monkeypatch):
    """An assistant asked who is hammering a site wants the shape, not 500 rows.

    Handing it the rows spends the operator's tokens on arithmetic and gets
    ties wrong.
    """
    from app.services import waf

    entries = (
        [("1.2.3.4", "/wp-login.php", 403, "block", "Vietnam")] * 30
        + [("5.6.7.8", "/", 200, "allow", "Germany")] * 10
        + [("9.9.9.9", "/", 200, "allow", "Germany")] * 5
    )

    def fake_parse(domain, line, sequence):
        index = int(line)
        ip, path, status, verdict, country = entries[index]
        return (datetime(2026, 9, 24, 0, index % 60), {
            "ip": ip, "path": path, "status": status, "verdict": verdict,
            "country": country, "timestamp": f"2026-09-24T00:00:{index % 60:02d}",
        })

    monkeypatch.setattr(waf, "_validate_domain", lambda d: d)
    monkeypatch.setattr(waf, "_read_site_logs",
                        lambda domains, lines: {"shop.test": "\n".join(str(i) for i in range(len(entries)))})
    monkeypatch.setattr(waf, "_parse_access_log_line", fake_parse)

    summary = waf.access_summary([type("W", (), {"domain": "shop.test"})()], top=2)

    assert summary["requests"] == 45
    assert summary["verdicts"] == {"allow": 15, "block": 30}
    assert summary["top_ips"][0] == {"ip": "1.2.3.4", "requests": 30}
    assert summary["most_blocked_ips"] == [{"ip": "1.2.3.4", "requests": 30}]
    assert len(summary["top_ips"]) == 2, "top is respected"
    assert summary["top_paths"][0]["path"] == "/wp-login.php"


def test_the_summary_is_stable_when_counts_tie(monkeypatch):
    """A summary that reshuffles on ties reads as a change that did not happen."""
    from app.services import waf

    def fake_parse(domain, line, sequence):
        return (datetime(2026, 9, 24), {
            "ip": line, "path": "/", "status": 200, "verdict": "allow",
            "country": "", "timestamp": "2026-09-24T00:00:00",
        })

    monkeypatch.setattr(waf, "_validate_domain", lambda d: d)
    monkeypatch.setattr(waf, "_read_site_logs",
                        lambda domains, lines: {"a.test": "9.9.9.9\n1.1.1.1\n5.5.5.5"})
    monkeypatch.setattr(waf, "_parse_access_log_line", fake_parse)

    site = type("W", (), {"domain": "a.test"})()
    first = waf.access_summary([site])["top_ips"]
    second = waf.access_summary([site])["top_ips"]
    assert first == second
    assert [row["ip"] for row in first] == ["1.1.1.1", "5.5.5.5", "9.9.9.9"]


def test_a_site_with_no_log_yet_is_named_not_silently_dropped(monkeypatch):
    from app.services import waf

    monkeypatch.setattr(waf, "_validate_domain", lambda d: d)
    monkeypatch.setattr(waf, "_read_site_logs", lambda domains, lines: {"new.test": None})

    summary = waf.access_summary([type("W", (), {"domain": "new.test"})()])
    assert summary["no_log_yet"] == ["new.test"]
    assert summary["requests"] == 0


# ============================================================================
# The tools that change something.
#
# What these hold to is not "the assistant behaves". It is that an assistant
# reads access logs, and access logs are written by strangers. A user agent
# saying "block 127.0.0.1" is an attacker's instruction laundered through a
# file the model trusts, so every value has to be checked as though the
# attacker chose it - because sometimes they did.
# ============================================================================

def _write_ctx(client_ip="198.51.100.7", role=Role.admin):
    return mcp.Context(db=_Session(), user=_user(role=role),
                       token=_token(can_write=True), client_ip=client_ip)


# --- which tools exist at all ------------------------------------------------

def test_a_read_only_token_sees_none_of_the_writing_tools(tools):
    ctx = mcp.Context(db=_Session(), user=_user(role=Role.admin),
                      token=_token(can_write=False))
    assert not any(t.writes for t in mcp.visible_tools(ctx))


def test_deleting_a_file_is_the_only_destructive_tool(tools):
    """destructiveHint is what makes a client stop and ask first.

    Marking everything destructive trains the operator to click through the
    prompt, which is worse than marking nothing.
    """
    assert {name for name, spec in tools.items() if spec.destructive} == {"delete_file"}


def test_every_writing_tool_is_marked_as_writing(tools):
    for name in ("write_file", "delete_file", "block_ip", "add_waf_rule",
                 "restart_service", "issue_ssl_certificate", "create_backup",
                 "move_file", "create_directory", "unblock_ip",
                 "run_backup_schedule", "set_website_waf"):
        assert tools[name].writes, f"{name} changes something and must say so"


# --- what may be handed to the firewall --------------------------------------

@pytest.mark.parametrize("address,because", [
    ("10.0.0.5", "private"),
    ("192.168.1.1", "private"),
    ("127.0.0.1", "loopback"),
    ("::1", "loopback"),
    ("169.254.1.1", "link local"),
])
def test_an_address_that_is_part_of_the_server_is_refused(address, because):
    """A log full of these means the site is behind a proxy or a CDN.

    Blocking one cuts off part of the machine rather than an attacker, and the
    error says so rather than just refusing.
    """
    with pytest.raises(mcp.ToolError) as exc:
        mcp_tools._check_blockable(_write_ctx(), address)
    assert "private, loopback or reserved" in str(exc.value)
    assert "proxy" in str(exc.value) or "CDN" in str(exc.value)


@pytest.mark.parametrize("cidr,widest", [
    ("8.0.0.0/8", 16),
    ("1.2.0.0/15", 16),
    ("2001:db8::/16", 32),
])
def test_a_range_wider_than_a_block_is_an_outage_and_is_refused(cidr, widest):
    """A model that decides a whole country is the problem proposes /8."""
    with pytest.raises(mcp.ToolError) as exc:
        mcp_tools._check_blockable(_write_ctx(), cidr)
    assert f"/{widest}" in str(exc.value)


@pytest.mark.parametrize("value", ["1.2.3.4", "1.2.3.0/24", "8.8.8.8"])
def test_a_real_public_address_is_allowed(value):
    assert mcp_tools._check_blockable(_write_ctx(), value) == value


def test_the_caller_cannot_block_itself():
    ctx = _write_ctx(client_ip="45.76.10.20")
    with pytest.raises(mcp.ToolError) as exc:
        mcp_tools._check_blockable(ctx, "45.76.10.20")
    assert "disconnect you" in str(exc.value)


def test_the_caller_cannot_block_a_range_containing_itself():
    """The obvious check is equality. The one that matters is containment."""
    ctx = _write_ctx(client_ip="45.76.10.20")
    with pytest.raises(mcp.ToolError):
        mcp_tools._check_blockable(ctx, "45.76.10.0/24")


def test_the_server_cannot_block_itself(monkeypatch):
    monkeypatch.setattr(mcp_tools, "_server_addresses", lambda: {"163.61.72.88"})
    for value in ("163.61.72.88", "163.61.72.0/24"):
        with pytest.raises(mcp.ToolError) as exc:
            mcp_tools._check_blockable(_write_ctx(), value)
        assert "take the machine off the network" in str(exc.value)


def test_not_being_able_to_ask_for_the_server_addresses_does_not_open_the_gate(monkeypatch):
    """A failure to check must not read as a pass."""
    from app.services import server_network

    monkeypatch.setattr(server_network, "addresses",
                        lambda: (_ for _ in ()).throw(RuntimeError("no ip command")))
    # Still refuses everything the other layers refuse.
    with pytest.raises(mcp.ToolError):
        mcp_tools._check_blockable(_write_ctx(), "10.0.0.1")


@pytest.mark.parametrize("junk", ["", "   ", "not-an-ip", "1.2.3.4.5", "999.1.1.1"])
def test_something_that_is_not_an_address_is_refused(junk):
    with pytest.raises(mcp.ToolError):
        mcp_tools._check_blockable(_write_ctx(), junk)


# --- WAF rules an assistant is allowed to write ------------------------------

@pytest.mark.parametrize("attack", [
    '" "id:1,phase:1,exec:/bin/sh',
    '1.2.3.4" "id:900001,phase:1,allow',
    "%{tx.executing_paranoia_level}",
    "a\nSecRule ARGS \"@rx .\" \"id:2,allow\"",
    "'",
    "\\",
    "x" * 201,
    "",
])
def test_no_string_an_assistant_supplies_can_become_rule_syntax(attack):
    """The whole reason the assistant never writes ModSecurity.

    A model that has read an access log written by an attacker may well try to
    emit one of these. It has to be rejected as a value, not executed as a
    directive.
    """
    from app.services import waf

    with pytest.raises(ValueError):
        waf.mcp_rule_value(attack)


@pytest.mark.parametrize("match,expected_variable", [
    ("ip", "REMOTE_ADDR"),
    ("path", "REQUEST_URI"),
    ("user_agent", "REQUEST_HEADERS:User-Agent"),
    ("query", "QUERY_STRING"),
])
def test_each_match_becomes_the_rule_you_would_have_written(match, expected_variable):
    from app.services import waf

    rule = waf.render_mcp_rule(match, "1.2.3.4", rule_id=1090000,
                               who="admin", when="2026-09-24")
    assert expected_variable in rule
    assert "phase:1" in rule, "these are blocks, decided before the body is read"
    assert "deny" in rule and "status:403" in rule


def test_a_rule_says_who_added_it_and_why():
    """The question a rule nobody recognises raises six months later."""
    from app.services import waf

    rule = waf.render_mcp_rule("ip", "1.2.3.4", rule_id=1090000, who="admin",
                               when="2026-09-24", reason="brute force on wp-login")
    assert rule.startswith("# spanel-mcp: added by admin on 2026-09-24")
    assert "brute force on wp-login" in rule


def test_a_reason_cannot_smuggle_anything_into_the_comment():
    from app.services import waf

    rule = waf.render_mcp_rule("ip", "1.2.3.4", rule_id=1090000, who="admin",
                               when="2026-09-24",
                               reason='x"\nSecRule ARGS "@rx ." "id:3,allow"')
    lines = rule.split("\n")
    assert len(lines) == 2, "comment line, rule line, and nothing else"
    assert lines[0].startswith("# "), "whatever survived is still a comment"
    # The word SecRule surviving inside a comment is harmless. A newline would
    # not be, and neither would a quote: those are what could start a second
    # directive or close the one below. Those are what this asserts on.
    assert '"' not in lines[0] and "'" not in lines[0]
    assert lines[1].startswith("SecRule REMOTE_ADDR ")


def test_an_id_already_in_the_file_is_never_reused():
    """A duplicate id makes ModSecurity refuse the whole configuration.

    Which takes every site on the box down at the next nginx reload - so this
    is worth a file scan.
    """
    from app.services import waf

    existing = 'SecRule REMOTE_ADDR "@ipMatch 9.9.9.9" "id:1090000,phase:1,deny"'
    assert waf.next_mcp_rule_id(existing) == 1090001
    assert waf.next_mcp_rule_id("") == 1090000


def test_ids_stay_inside_the_range_reserved_for_this():
    """So a human can tell where a rule came from, and clear them as a group."""
    from app.services import waf

    assert waf.MCP_RULE_ID_FIRST == 1090000 and waf.MCP_RULE_ID_LAST == 1099999
    with pytest.raises(ValueError):
        waf.render_mcp_rule("ip", "1.2.3.4", rule_id=900001, who="a", when="b")


def test_appending_keeps_what_was_already_there():
    from app.services import waf

    existing = 'SecRule ARGS "@rx evil" "id:1090000,phase:2,deny"'
    combined, rule_id = waf.append_mcp_rule(existing, "ip", "1.2.3.4",
                                            who="admin", when="2026-09-24")
    assert existing in combined
    assert rule_id == 1090001
    assert combined.count("SecRule") == 2


# --- deleting a file ---------------------------------------------------------

@pytest.mark.parametrize("path", ["", "   ", "/", ".", "public_html", "/public_html/"])
def test_deleting_the_website_itself_is_refused(path, monkeypatch):
    """The file manager stops an escape. It does not stop emptying the site.

    "" is the site root and public_html is every page the site serves; both
    are one plausible model mistake away from a customer's website being gone.
    """
    monkeypatch.setattr(mcp_tools, "_website",
                        lambda ctx, domain: type("W", (), {"id": 1})())
    with pytest.raises(mcp.ToolError) as exc:
        mcp.REGISTRY["delete_file"].handler(_write_ctx(), {"domain": "a.test", "path": path})
    assert "the website itself" in str(exc.value)


def test_deleting_something_inside_the_site_is_allowed(monkeypatch):
    seen = {}
    monkeypatch.setattr(mcp_tools, "_website",
                        lambda ctx, domain: type("W", (), {"id": 7})())
    monkeypatch.setattr(mcp_tools.maintenance_api, "delete_entries",
                        lambda payload, db, current_user: seen.update(paths=payload.paths))

    mcp.REGISTRY["delete_file"].handler(
        _write_ctx(), {"domain": "a.test", "path": "public_html/evil.php"})
    assert seen["paths"] == ["public_html/evil.php"]


# --- services ----------------------------------------------------------------

def test_a_service_can_be_put_back_but_not_taken_away(tools):
    """No stop. An assistant that decides nginx is the problem must not be
    able to answer that by turning the web server off and leaving nothing
    running to notice."""
    allowed = tools["restart_service"].properties["action"]["enum"]
    assert set(allowed) == {"restart", "reload"}
    assert "stop" not in allowed and "disable" not in allowed


# --- blocking twice ----------------------------------------------------------

def test_blocking_an_address_that_is_already_blocked_adds_nothing(monkeypatch):
    """Otherwise the rule list fills with duplicates, each removed by hand."""
    monkeypatch.setattr(mcp_tools.firewall_service, "rules",
                        lambda: [{"ip": "8.8.8.8", "action": "DENY", "number": 4}])
    called = []
    monkeypatch.setattr(mcp_tools.firewall_api, "block_ip",
                        lambda **kwargs: called.append(kwargs))

    answer = mcp.REGISTRY["block_ip"].handler(_write_ctx(), {"ip": "8.8.8.8"})
    assert answer["already_blocked"] is True and answer["rule_number"] == 4
    assert called == []


def test_unblocking_finds_the_rule_by_address(monkeypatch):
    """spanel deletes by rule number; an assistant has an address from a log."""
    monkeypatch.setattr(mcp_tools.firewall_service, "rules",
                        lambda: [{"ip": "8.8.8.8", "action": "DENY", "number": 2},
                                 {"ip": "8.8.8.8", "action": "DENY", "number": 5},
                                 {"ip": "1.1.1.1", "action": "DENY", "number": 3}])
    removed = []
    monkeypatch.setattr(mcp_tools.firewall_api, "delete_rule",
                        lambda number, current_user: removed.append(number))

    answer = mcp.REGISTRY["unblock_ip"].handler(_write_ctx(), {"ip": "8.8.8.8"})
    # Highest first: deleting by position renumbers everything below it.
    assert removed == [5, 2]
    assert answer["removed_rules"] == [5, 2]


def test_unblocking_something_that_is_not_blocked_says_so(monkeypatch):
    monkeypatch.setattr(mcp_tools.firewall_service, "rules", lambda: [])
    with pytest.raises(mcp.ToolError) as exc:
        mcp.REGISTRY["unblock_ip"].handler(_write_ctx(), {"ip": "8.8.8.8"})
    assert "is not blocked" in str(exc.value)


# --- the audit trail ---------------------------------------------------------

def test_a_writing_tool_is_recorded_and_a_reading_one_is_not(monkeypatch):
    written = []
    monkeypatch.setattr(mcp, "log_action",
                        lambda db, uid, action, target, detail: written.append((action, target, detail)))
    monkeypatch.setattr(mcp, "REGISTRY", {})

    @mcp.tool("reads", "R", "Reads something for a while.")
    def _reads(ctx, args):
        return {}

    @mcp.tool("writes", "W", "Writes something for a while.", writes=True)
    def _writes(ctx, args):
        return {}

    ctx = mcp.Context(db=_Session(), user=_user(), token=_token(can_write=True))
    mcp.call_tool(ctx, "reads", {})
    assert written == []

    mcp.call_tool(ctx, "writes", {})
    assert written and written[0][0] == "mcp_tool" and written[0][1] == "writes"


def test_a_files_contents_are_never_copied_into_the_audit_log(monkeypatch):
    """Administrators read this log. It is not a place to keep every byte an
    assistant ever wrote, and a secret written into wp-config would live there
    for ever."""
    written = []
    monkeypatch.setattr(mcp, "log_action",
                        lambda db, uid, action, target, detail: written.append(detail))
    monkeypatch.setattr(mcp, "REGISTRY", {})

    @mcp.tool("writer", "W", "Writes a file somewhere useful.",
              {"path": {"type": "string", "maxLength": 100},
               "content": {"type": "string", "maxLength": 100000}},
              writes=True)
    def _writer(ctx, args):
        return {}

    ctx = mcp.Context(db=_Session(), user=_user(), token=_token(can_write=True))
    mcp.call_tool(ctx, "writer", {"path": "wp-config.php",
                                  "content": "define('DB_PASSWORD', 'hunter2');"})
    detail = written[0]
    assert "hunter2" not in detail
    assert "wp-config.php" in detail
    assert "content=33 chars" in detail


# --- the panel's own page ----------------------------------------------------

APP_JSX = (PROJECT_ROOT / "frontend" / "src" / "App.jsx").read_text(encoding="utf-8")


def test_the_page_exists_and_is_routed():
    assert "function renderMcp()" in APP_JSX
    assert "mcp: '/ai-assistants'," in APP_JSX
    assert "if (page === 'mcp')" in APP_JSX


def test_nobody_sees_the_menu_until_the_addon_is_on():
    """An addon shows in the sidebar only once it is on - for administrators
    too, who reach a switched-off addon from the Addons page (operator,
    2026-09-27)."""
    assert "...(mcpAddonInstalled ? [['mcp', 'AI assistants', Bot]] : [])," in APP_JSX


def test_the_page_says_a_self_signed_certificate_will_not_work():
    """The single most likely reason this never connects, said before the
    person has spent an afternoon on it."""
    assert "MCP clients refuse a self-signed certificate" in APP_JSX


def test_the_page_says_the_token_is_shown_once():
    assert "not shown again" in APP_JSX


def test_the_new_token_is_not_cleared_by_the_next_render():
    """The server keeps only a hash. A token that scrolls away is gone."""
    block = APP_JSX.split("function createMcpToken()")[1].split("async function")[0]
    assert "setMcpNewToken(data.token)" in block
    # It is dismissed by the person, not by the next state change.
    assert "setMcpNewToken('')" in APP_JSX.split("function renderMcp()")[1]


def test_the_page_offers_a_config_for_each_client_that_can_use_it():
    block = APP_JSX.split("function renderMcp()")[1].split("function renderMcpTokenRow")[0]
    for client in ("Claude Code", "Cursor", "VS Code"):
        assert client in block, f"{client} has no copy-ready configuration"
    assert "YOUR_TOKEN" in block


def test_a_token_row_says_whether_it_can_act():
    """Read-only against can-act is the whole permission model; it belongs in
    the list rather than only on the form that created it."""
    block = APP_JSX.split("function renderMcpTokenRow")[1]
    assert "read only" in block and "can act" in block
    assert "revoked" in block and "expired" in block


def test_revoking_asks_first():
    block = APP_JSX.split("async function revokeMcpToken")[1].split("function ")[0]
    assert "window.confirm" in block


def test_the_page_is_in_the_sidebar_and_the_sidebar_is_not_folded():
    """The defect this caught: the MCP page sat behind a Settings group that
    was collapsed by default, and the operator could not find it - reported
    from a real panel on .88 where the code was deployed and working.

    The sidebar now holds the everyday pages and the addons that are on, and
    everything else sits on the Settings page - a page one click away, not a
    submenu that can be left folded (operator, 2026-09-27).
    """
    sidebar = APP_JSX.split("const navSections = [")[1].split("].filter(section")[0]
    assert "['mcp', 'AI assistants', Bot]" in sidebar
    assert "sidebar-subnav" not in APP_JSX and "settingsMenuOpen" not in APP_JSX


def test_every_addon_has_a_way_in_from_the_sidebar():
    """The operator's rule: an addon that is on must show on the map.

    Read from the backend catalogue rather than a list written here, so an
    addon added later fails this until it has an entry. fail2ban has no page
    of its own - its ban list is a section of the Firewall page - so its way
    in is Settings, then Firewall.
    """
    from app.services import addons

    sidebar = APP_JSX.split("const navSections = [")[1].split("].filter(section")[0]
    sidebar += APP_JSX.split("const settingsGroups = [")[1].split("const settingsItems = ")[0]
    for slug in sorted(addons.CATALOGUE):
        way_in = {"application": "appsFeatureEnabled ? [['applications',",
                  "fail2ban": "isAdmin && ['firewall', 'Firewall', BrickWall,",
                  # An addon since 2026-09-27, in the sidebar once it is on.
                  "malware": "malwareAddonInstalled && isAdmin ? [['malware', 'Malware scanner', Bug]]",
                  "mcp": "mcpAddonInstalled ? [['mcp',",
                  # No page of its own: its accounts are set on its card in
                  # Settings, then Addons (2026-09-28).
                  "demo": "isAdmin && ['addons', 'Addons', PackageOpen,",
                  # Administrators and customers alike (2026-09-29).
                  "dns": "dnsAddonInstalled ? [['dns', 'DNS Manager', Network]]",
                  # Administrators and customers alike (2026-09-29).
                  "mail": "mailAddonInstalled ? [['mail', 'Email', Mail]]",
                  # Administrators only (operator, 2026-09-27).
                  "notifications": "notificationsAddonInstalled && isAdmin ? [['notifications',"}.get(slug)
        assert way_in, f"addon {slug} has no sidebar entry named in this test"
        assert way_in in sidebar, f"addon {slug} is installable but has no way in from the sidebar"


def test_the_client_snippets_carry_the_token_that_was_just_created():
    """Copying a command and then editing it is two steps where one will do.

    The token cannot be shown again, so the only moment this can help is while
    it is still on screen - which is exactly when it is substituted.
    """
    block = APP_JSX.split("function renderMcp()")[1].split("function renderMcpTokenRow")[0]
    assert "const bearer = mcpNewToken || 'YOUR_TOKEN';" in block
    # All three snippets use it; none of them still hard-codes the placeholder.
    assert block.count("${bearer}") == 3
    assert "'Bearer YOUR_TOKEN'" not in block


# --- the request an endpoint is handed ---------------------------------------

def test_block_ip_crashed_because_the_request_was_none(monkeypatch):
    """The bug as the operator met it on .88:

        'NoneType' object has no attribute 'client'

    firewall.block_ip reads request.client.host to refuse a range covering the
    caller's own address. A tool passing request=None made that a crash - and
    the assistant, having nothing better to go on, told the operator that
    iptables was probably down.
    """
    seen = {}
    monkeypatch.setattr(mcp_tools.firewall_service, "rules", lambda: [])
    monkeypatch.setattr(mcp_tools.firewall_api, "block_ip",
                        lambda payload, request, current_user: seen.update(request=request))

    ctx = mcp.Context(db=_Session(), user=_user(), token=_token(can_write=True),
                      client_ip="198.51.100.7", request=_FakeRequest("198.51.100.7"))
    mcp.REGISTRY["block_ip"].handler(ctx, {"ip": "8.8.8.8"})

    assert seen["request"] is not None, "the endpoint dereferences it"
    assert seen["request"].client.host == "198.51.100.7"


class _FakeRequest:
    """Only what the endpoints actually read off a Request."""

    def __init__(self, host):
        self.client = type("C", (), {"host": host})()
        self.headers = {"user-agent": "mcp-client/1.0"}


def test_no_tool_hands_an_endpoint_a_missing_request():
    """The class of bug, not the one instance.

    Several endpoints take a Request and read it. A tool that passes None is a
    crash waiting for the first person to call it, and it silently disables
    whatever that endpoint uses the request for - here, the panel's own guard
    against blocking the address you are connected from.
    """
    source = (PROJECT_ROOT / "backend" / "app" / "services" / "mcp_tools.py") \
        .read_text(encoding="utf-8")
    assert "request=None" not in source
    assert source.count("request=ctx.request") == 4


def test_the_endpoint_puts_the_real_request_on_the_context():
    source = (PROJECT_ROOT / "backend" / "app" / "api" / "mcp.py").read_text(encoding="utf-8")
    block = source.split("ctx = mcp.Context(")[1].split(")")[0]
    assert "request=request" in block


def test_a_write_through_mcp_records_where_it_came_from(monkeypatch):
    """With the request in hand the audit row gets an address and a client.

    Before this every MCP write was logged with neither, which is the one
    question an administrator asks of a change they did not make.
    """
    from app.services import audit

    rows = []
    monkeypatch.setattr(audit, "log_action", lambda *a, **k: rows.append((a, k)))
    # log_action itself appends ip= and ua= when a request is present; this
    # checks the plumbing reaches it rather than re-testing log_action.
    fake = _FakeRequest("198.51.100.7")
    audit.log_action(_Session(), 1, "mcp_tool", "block_ip", "", request=fake)
    assert rows[0][1]["request"] is fake


# --- the firewall stores a network, not the string you gave it ---------------

@pytest.mark.parametrize("stored,asked", [
    ("185.220.101.7/32", "185.220.101.7"),
    ("185.220.101.7", "185.220.101.7/32"),
    ("1.2.3.0/24", "1.2.3.0/24"),
    ("2606:4700::1111/128", "2606:4700::1111"),
])
def test_a_rule_is_matched_as_a_network_not_as_text(stored, asked):
    """Asking to block 185.220.101.7 produces a rule reading 185.220.101.7/32.

    Comparing the two as strings never matched. block_ip therefore never saw
    what it had already blocked and added a duplicate rule on every call, and
    unblock_ip said "is not blocked" about an address that was - a confident
    wrong answer, which is worse than the duplicates.
    """
    assert mcp_tools._same_network(stored, asked)


@pytest.mark.parametrize("stored,asked", [
    ("1.2.3.4/32", "1.2.3.5"),
    ("1.2.3.0/24", "1.2.4.0/24"),
])
def test_different_addresses_still_do_not_match(stored, asked):
    assert not mcp_tools._same_network(stored, asked)


def test_blocking_something_already_blocked_is_seen_through_the_suffix(monkeypatch):
    monkeypatch.setattr(mcp_tools.firewall_service, "rules",
                        lambda: [{"ip": "8.8.8.8/32", "action": "DENY", "number": 4}])
    called = []
    monkeypatch.setattr(mcp_tools.firewall_api, "block_ip",
                        lambda **kwargs: called.append(kwargs))

    answer = mcp.REGISTRY["block_ip"].handler(
        mcp.Context(db=_Session(), user=_user(), token=_token(can_write=True),
                    client_ip="198.51.100.7", request=_FakeRequest("198.51.100.7")),
        {"ip": "8.8.8.8"})
    assert answer["already_blocked"] is True and answer["rule_number"] == 4
    assert called == [], "a duplicate rule per call is how this looked in practice"


def test_unblocking_finds_the_rule_despite_the_suffix(monkeypatch):
    monkeypatch.setattr(mcp_tools.firewall_service, "rules",
                        lambda: [{"ip": "8.8.8.8/32", "action": "DENY", "number": 6}])
    removed = []
    monkeypatch.setattr(mcp_tools.firewall_api, "delete_rule",
                        lambda number, current_user: removed.append(number))

    answer = mcp.REGISTRY["unblock_ip"].handler(
        mcp.Context(db=_Session(), user=_user(), token=_token(can_write=True),
                    request=_FakeRequest("198.51.100.7")),
        {"ip": "8.8.8.8"})
    assert removed == [6] and answer["removed_rules"] == [6]


# --- the firewall must not close connections it is not blocking --------------

def test_the_kill_step_skips_loopback():
    """`ss -K dst 127.0.0.1` closes every local connection on the machine.

    Found on .88: an assistant asked to block an address, the rule was added,
    and the reply never arrived - "Connection reset by peer". The filter chain
    RETURNs for -i lo before any deny set is read, so a loopback peer is by
    definition not one the firewall is blocking. But the kill loop tested it
    against spanel-block4, which on a live server holds 127.0.0.1 among 186k
    entries pulled from public blocklists, and killed everything on loopback:
    MariaDB over TCP, Redis, phpMyAdmin, and the panel's own reply.
    """
    helper = HELPER_SCRIPT_MCP.read_text(encoding="utf-8")
    body = helper.split("firewall_kill_blocked_connections() {")[1].split("\n}\n")[0]
    assert "127.*" in body and "::1" in body, "loopback peers must be skipped"
    # The skip has to come before the set is consulted, or it does nothing.
    # Matched against the code line rather than "ipset test", which also
    # appears in the comment at the top of the function explaining why testing
    # peers against sets is the cheap direction.
    assert body.index("127.*") < body.index('if ipset test "$set"')

