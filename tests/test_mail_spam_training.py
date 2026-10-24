"""Spam training on move (IMAPSieve -> Rspamd) and quota warnings.

Rspamd only learnt from its own autolearn: a message a person moved into
Junk taught it nothing, and a mailbox filled up without anyone being told.
"""
import io
import os
import time

import pytest

from mail_fixtures import helper_function, load_tool

tool = load_tool()


# --- the learn queue -----------------------------------------------------------------------------

def test_messages_are_queued_whole_and_handed_to_rspamd(tmp_path):
    name = tool.learn_enqueue("spam", io.BytesIO(b"Subject: buy now\n\nspam spam"), spool=str(tmp_path))
    assert name.startswith("spam.") and (tmp_path / name).read_bytes().endswith(b"spam spam")
    tool.learn_enqueue("ham", io.BytesIO(b"Subject: hello\n\nhi"), spool=str(tmp_path))
    # Nothing is queued for an empty message.
    assert tool.learn_enqueue("ham", io.BytesIO(b""), spool=str(tmp_path)) == ""
    with pytest.raises(tool.ToolError):
        tool.learn_enqueue("virus", io.BytesIO(b"x"), spool=str(tmp_path))

    learnt = []
    counts = tool.learn_process(str(tmp_path), lambda kind, data: learnt.append((kind, data)) or True)
    assert counts == {"spam": 1, "ham": 1, "failed": 0, "removed": 0}
    assert sorted(kind for kind, _ in learnt) == ["ham", "spam"]
    assert os.listdir(tmp_path) == []


def test_the_processor_reads_only_its_own_plain_files(tmp_path):
    (tmp_path / "evil.txt").write_text("not ours")
    stale = tmp_path / "tmp.abc"
    stale.write_text("half written")
    old = time.time() - 7200
    os.utime(stale, (old, old))
    fresh = tmp_path / "tmp.def"
    fresh.write_text("being written")
    (tmp_path / "spam.dir").mkdir()
    sent = []
    counts = tool.learn_process(str(tmp_path), lambda kind, data: sent.append(kind) or True)
    assert sent == []
    assert counts["removed"] == 2 and counts["failed"] == 1
    # A message still being written is left for the next pass; what a mailbox
    # owner made of a name the processor reads is gone.
    assert fresh.exists() and not (tmp_path / "spam.dir").exists()


@pytest.mark.skipif(os.name == "nt", reason="symlinks need privileges on Windows")
def test_a_link_in_the_queue_is_never_followed(tmp_path):
    secret = tmp_path / "secret"
    secret.write_text("root only")
    spool = tmp_path / "spool"
    spool.mkdir()
    os.symlink(secret, spool / "spam.link")
    sent = []
    counts = tool.learn_process(str(spool), lambda kind, data: sent.append(data) or True)
    assert sent == [] and counts["failed"] == 1
    assert secret.exists()


def test_a_failed_hand_over_is_counted(tmp_path):
    tool.learn_enqueue("spam", io.BytesIO(b"x"), spool=str(tmp_path))
    assert tool.learn_process(str(tmp_path), lambda kind, data: False)["failed"] == 1


def test_dovecot_runs_the_learn_scripts_on_move():
    conf = helper_function("mail_write_dovecot_conf")
    for line in ("sieve_plugins = sieve_imapsieve sieve_extprograms",
                 "imapsieve_mailbox1_name = Junk",
                 "imapsieve_mailbox1_before = file:${MAIL_SIEVE_DIR}/learn-spam.sieve",
                 "imapsieve_mailbox2_from = Junk",
                 "imapsieve_mailbox2_before = file:${MAIL_SIEVE_DIR}/learn-ham.sieve",
                 "mail_plugins = \\$mail_plugins imap_quota imap_sieve"):
        assert line in conf
    sieve = helper_function("mail_write_sieve")
    assert 'pipe :copy "spanel-learn-spam";' in sieve
    assert 'if string "${mailbox}" "Trash" {' in sieve
    service = helper_function("mail_write_learn_service")
    assert "-m 1733" in service and "OnUnitActiveSec=1min" in service
    assert "learn-process ${MAIL_LEARN_SPOOL} ${MAIL_RSPAMD_KEY_FILE}" in service


# --- quota warnings ------------------------------------------------------------------------------

def test_quota_warning_message():
    message = tool.render_quota_warning(95, "Info@Example.com", "Sat, 24 Oct 2026 12:00:00 +0000").decode()
    assert "To: <info@example.com>" in message and "From: Mail server <postmaster@example.com>" in message
    assert "Subject: Your mailbox is 95% full - mail will soon be refused" in message
    assert "Auto-Submitted: auto-generated" in message
    assert "Subject: Your mailbox is 80% full\n" in tool.render_quota_warning(80, "info@example.com").decode()


@pytest.mark.parametrize("level, address", [(50, "info@example.com"), ("x", "info@example.com"),
                                            (80, "info@example.com\nBcc: x@y.z"), (95, "../x@example.com")])
def test_quota_warning_input_is_checked(level, address):
    with pytest.raises(tool.ToolError):
        tool.check_quota_warning(level, address)


def test_only_mailboxes_of_this_server_are_warned():
    assert tool.check_quota_warning("80", "info@example.com", {"info@example.com"}) == (80, "info@example.com")
    with pytest.raises(tool.ToolError, match="not a mailbox"):
        tool.check_quota_warning("80", "other@example.com", {"info@example.com"})


def test_a_mailbox_is_warned_once_a_day_per_level(tmp_path):
    now = 1_800_000_000.0
    assert tool.quota_warning_due(str(tmp_path), 80, "info@example.com", now) is True
    assert tool.quota_warning_due(str(tmp_path), 80, "info@example.com", now + 3600) is False
    assert tool.quota_warning_due(str(tmp_path), 95, "info@example.com", now + 3600) is True
    assert tool.quota_warning_due(str(tmp_path), 80, "info@example.com", now + 90000) is True


def test_dovecot_warns_at_80_and_95_percent():
    conf = helper_function("mail_write_dovecot_conf")
    assert "quota_warning = storage=95%% quota-warning 95 %u" in conf
    assert "quota_warning2 = storage=80%% quota-warning 80 %u" in conf
    assert "executable = script ${MAIL_QUOTA_WARNING}" in conf
