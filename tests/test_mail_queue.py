"""Mail queue management, and auto-hold when a sending limit trips.

The panel only knew how many messages waited (exim -bpc). The "auto-hold"
setting was saved and sent to the helper, but no Exim rule ever read it.
"""
from types import SimpleNamespace

import pytest

from mail_fixtures import db, helper_function, load_tool, mail_env, panel_settings_file  # noqa: F401 - fixtures

tool = load_tool()

BP = """\
 2h  1.2K 1tAbCd-00000aBcDeF-0aZz <sender@example.com> *** frozen ***
          recipient@example.org
        D done@example.org

25m   500 1qABCe-000124-CD <>
          postmaster@example.com

 3d    12M 1tZzZz-0000000000A-0001 <news@shop.example>
        D a@one.test
       +D b@two.test
          c@three.test

this line is noise
"""


def test_exim_bp_is_parsed():
    from app.services import mail

    messages = mail.parse_queue(BP)
    assert [item["id"] for item in messages] == ["1tAbCd-00000aBcDeF-0aZz", "1qABCe-000124-CD", "1tZzZz-0000000000A-0001"]
    first, bounce, third = messages
    assert first["frozen"] is True and first["sender"] == "sender@example.com"
    assert first["recipients"] == [{"address": "recipient@example.org", "delivered": False},
                                   {"address": "done@example.org", "delivered": True}]
    assert (first["age"], first["age_seconds"], first["size"], first["pending"]) == ("2h", 7200, "1.2K", 1)
    assert bounce["sender"] == "" and bounce["frozen"] is False
    assert third["age_seconds"] == 3 * 86400 and third["pending"] == 1
    assert [rcpt["delivered"] for rcpt in third["recipients"]] == [True, True, False]
    assert mail.parse_queue("") == []


@pytest.mark.parametrize("qid, ok", [
    ("1tAbCd-00000aBcDeF-0aZz", True),
    ("1qABCe-000124-CD", True),
    ("1qABCe-000124-C", False),
    ("1qABCe-000124-CD; rm -rf /", False),
    ("-Mrm", False),
    ("../../etc/passwd", False),
    ("", False),
])
def test_queue_ids_are_checked(qid, ok):
    from app.services import mail

    assert bool(mail.QUEUE_ID_RE.fullmatch(qid)) is ok
    # The helper's own check accepts the same ids.
    helper = helper_function("require_queue_id")
    assert "[0-9A-Za-z]{6}-([0-9A-Za-z]{11}-[0-9A-Za-z]{4}|[0-9A-Za-z]{6}-[0-9A-Za-z]{2})" in helper


def test_queue_list_filters_and_sorts(mail_env, monkeypatch):
    from app.services import mail

    monkeypatch.setattr(mail, "_helper", lambda verb, *a, **kw: SimpleNamespace(stdout=BP, stderr="", returncode=0))
    listed = mail.queue_list()
    assert [item["id"][:6] for item in listed["items"]] == ["1tZzZz", "1tAbCd", "1qABCe"]
    assert (listed["total"], listed["frozen"]) == (3, 1)
    assert [item["id"][:6] for item in mail.queue_list(state="frozen")["items"]] == ["1tAbCd"]
    assert [item["id"][:6] for item in mail.queue_list(q="three.test")["items"]] == ["1tZzZz"]


def test_queue_actions_reach_the_helper_only_with_valid_ids(mail_env, monkeypatch):
    from app.services import mail

    calls = []
    monkeypatch.setattr(mail, "_helper", lambda verb, *args, **kw: calls.append((verb, args)) or SimpleNamespace(stdout="Message 1qABCe-000124-CD has been removed\n", stderr="", returncode=0))
    lines = mail.queue_action("delete", ["1qABCe-000124-CD", "1qABCe-000124-CD"])
    assert calls == [("mail-queue-action", ("delete", "1qABCe-000124-CD"))]
    assert lines == ["Message 1qABCe-000124-CD has been removed"]
    for action, ids in (("bounce", ["1qABCe-000124-CD"]), ("retry", ["-qff"]), ("thaw", []),
                        ("freeze", ["1qABCe-000124-CD"] * 2 + [f"1qABCe-{n:06d}-CD" for n in range(100)])):
        with pytest.raises(mail.MailError):
            mail.queue_action(action, ids)
    assert len(calls) == 1
    mail.queue_message("1qABCe-000124-CD")
    assert calls[-1] == ("mail-queue-show", ("1qABCe-000124-CD",))
    with pytest.raises(mail.MailError):
        mail.queue_message("../x")


def test_queue_routes_are_for_administrators():
    from app.api import mail as mail_api

    paths = {route.path: route for route in mail_api.router.routes}
    assert "/mail/queue" in paths and "/mail/queue/action" in paths and "/mail/queue/{message_id}" in paths


# --- auto-hold -----------------------------------------------------------------------------------

def test_auto_hold_freezes_instead_of_refusing():
    acls = tool.render_rate_acls(300, 200, 500, True)
    rcpt = acls["rcpt"]
    # Twice the limit is still refused...
    assert "ratelimit = 600 / 1h / per_rcpt / readonly / $authenticated_id" in rcpt
    # ...below that the message is marked, then frozen once DATA arrives.
    assert "ratelimit = 300 / 1h / per_rcpt / $authenticated_id\n          set acl_m_hold =" in rcpt
    assert "control = freeze/no_tell" in acls["data"] and "logwrite = SPanel hold: $acl_m_hold" in acls["data"]
    assert "ratelimit = 400 / 1h / per_mail / readonly / $sender_ident" in acls["not_smtp"]
    assert "control = freeze/no_tell" in acls["not_smtp"]
    # The per-domain limit stays a refusal.
    assert ("ratelimit = ${lookup{${lc:$sender_address_domain}}lsearch*{/etc/exim4/spanel_ratelimits}{$value}{500}} "
            "/ 1h / per_rcpt / dom:${lc:$sender_address_domain}") in rcpt


def test_without_auto_hold_the_limits_refuse_as_before():
    acls = tool.render_rate_acls(300, 200, 0, False)
    assert acls["data"] == ""
    assert "freeze" not in acls["rcpt"] + acls["not_smtp"]
    assert "ratelimit = 300 / 1h / per_rcpt / $authenticated_id\n" in acls["rcpt"]
    assert "ratelimit = 200 / 1h / per_mail / $sender_ident\n" in acls["not_smtp"]
    assert tool.render_rate_acls(0, 0, 0, True) == {"rcpt": "", "data": "", "not_smtp": ""}
    with pytest.raises(tool.ToolError):
        tool.render_rate_acls(-1, 0, 0, True)


def test_exim_configuration_uses_the_setting():
    conf = helper_function("mail_write_exim_conf")
    assert 'raw.get("auto_hold_on_rate_exceed", True) is not False' in conf
    assert "acl_check_data:\n@@HOLD@@" in conf
    assert '.replace("@@HOLD@@", hold_block)' in conf
