"""Per-website bot blocking: what gets stored, and what nginx ends up running."""

import pytest

from app.services import nginx


def test_a_pasted_list_is_split_on_whatever_separator_it_arrived_with():
    # Operators import these from tools like CPGuard, a blog post, or a
    # spreadsheet column, so the paste is never uniformly formatted.
    bots = nginx.normalize_blocked_bots(
        'AhrefsBot\nSemrushBot,  MJ12bot\n DotBot ;PetalBot\n\n"Bytespider"'
    )

    assert bots == ["AhrefsBot", "SemrushBot", "MJ12bot", "DotBot", "PetalBot", "Bytespider"]


def test_duplicates_are_dropped_case_insensitively_keeping_the_first_spelling():
    # A merged list routinely contains the same bot twice in different case;
    # emitting it twice would only make the regex longer.
    assert nginx.normalize_blocked_bots("AhrefsBot\nahrefsbot\nAHREFSBOT") == ["AhrefsBot"]


def test_names_are_matched_literally_not_as_patterns():
    """`bingbot/2.0` must not also match `bingbotX2Y0`.

    Real bot strings are full of regex metacharacters - "bingbot/2.0",
    "Sogou web spider/4.0" - and an unescaped dot is a wildcard. Verified
    against a running nginx: with the escaping below, `bingbotX2Y0` is served
    normally while `bingbot/2.0` gets 403.
    """
    block = nginx._bot_block(nginx.normalize_blocked_bots("bingbot/2.0"))

    assert r"bingbot/2\.0" in block
    assert "bingbot/2.0" not in block.replace(r"2\.0", "")


def test_one_regex_rather_than_one_if_per_bot():
    # nginx evaluates `if` blocks in order on every request; a few hundred of
    # them would be paid for on all traffic, not just the bots.
    block = nginx._bot_block(nginx.normalize_blocked_bots("A\nB\nC\nD"))

    assert block.count("if (") == 1
    assert "(A|B|C|D)" in block


def test_a_name_that_could_escape_the_nginx_string_is_refused():
    """The block is a double-quoted nginx string and `"` is not a regex
    metacharacter, so re.escape leaves it alone. Left unchecked, a bot name
    could close the string and have the remainder parsed as configuration."""
    with pytest.raises(ValueError):
        nginx.normalize_blocked_bots('evil") { return 200; } if ($host ~ "')
    with pytest.raises(ValueError):
        nginx.normalize_blocked_bots("back\\slash")
    with pytest.raises(ValueError):
        nginx.normalize_blocked_bots("tab\there")


def test_a_dollar_sign_cannot_become_an_nginx_variable():
    # nginx interpolates $variables inside double-quoted strings.
    block = nginx._bot_block(nginx.normalize_blocked_bots("bot$http_host"))

    assert r"\$http_host" in block


def test_the_list_is_capped():
    with pytest.raises(ValueError):
        nginx.normalize_blocked_bots(["bot%d" % i for i in range(nginx.MAX_BLOCKED_BOTS + 1)])
    with pytest.raises(ValueError):
        nginx.normalize_blocked_bots("x" * (nginx.MAX_BOT_NAME_LENGTH + 1))


def test_an_empty_list_removes_the_block_entirely():
    vhost = (
        "server {\n"
        "    server_name example.com;\n"
        "    # spanel BOT BLOCK BEGIN\n"
        '    if ($http_user_agent ~* "(AhrefsBot)") { return 403; }\n'
        "    # spanel BOT BLOCK END\n"
        "}\n"
    )

    cleaned = nginx._replace_bot_block(vhost, "")

    assert "BOT BLOCK" not in cleaned
    assert "server_name example.com;" in cleaned


def test_rewriting_replaces_rather_than_stacks_blocks():
    # Saving the list twice must not leave two blocks behind.
    vhost = "server {\n    server_name example.com;\n}\n"

    once = nginx._replace_bot_block(vhost, "AhrefsBot")
    twice = nginx._replace_bot_block(once, "SemrushBot")

    assert twice.count("# spanel BOT BLOCK BEGIN") == 1
    assert "SemrushBot" in twice
    assert "AhrefsBot" not in twice


def test_a_site_enforces_the_global_list_plus_its_own(tmp_path, monkeypatch):
    """The two lists are stored apart and merged at render time.

    Flattening the global list into each site would work once and then rot:
    every later edit would have to find and update 23 copies, and any site
    added afterwards would silently miss it.
    """
    from app.services import panel_settings, waf

    monkeypatch.setattr(panel_settings, "SETTINGS_DIR", tmp_path)
    monkeypatch.setattr(panel_settings, "SETTINGS_FILE", tmp_path / "panel-settings.json")
    panel_settings.save_global_blocked_bots("AhrefsBot\nSemrushBot")

    class Site:
        domain = "example.com"
        blocked_bots = "Bytespider\nahrefsbot"   # one of these duplicates the global list

    effective = waf.effective_blocked_bots(Site())

    assert effective == ["AhrefsBot", "SemrushBot", "Bytespider"]
    # The site's own list is untouched by the merge.
    assert waf.website_blocked_bots(Site()) == ["Bytespider", "ahrefsbot"]


def test_update_edits_the_existing_vhost_instead_of_re_rendering_it(tmp_path, monkeypatch):
    """Regression: this went out calling write_vhost(domain, <file content>).

    write_vhost's second parameter is root_path, and it re-renders the whole
    vhost from the template - so every apply failed with "root_path must be the
    managed root for this domain" and nothing was ever written. A dry-run test
    would not have caught it, because dry-run returns before touching the
    filesystem; this exercises the real path.

    Editing in place also matters on its own: a site whose vhost has been
    customised must keep those customisations when its bot list changes.
    """
    vhost = tmp_path / "example.com.conf"
    vhost.write_text(
        "server {\n"
        "    server_name example.com;\n"
        "    # a hand-added directive that must survive\n"
        "    client_max_body_size 64m;\n"
        "}\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(nginx.settings, "command_dry_run", False)
    monkeypatch.setattr(nginx, "_vhost_path", lambda domain: vhost)
    monkeypatch.setattr(nginx, "_write_backup", lambda target, content: None)
    reloaded = []
    monkeypatch.setattr(nginx, "_test_and_reload", lambda target, previous: reloaded.append(target))

    nginx.update_bot_block("example.com", "AhrefsBot\nSemrushBot")

    written = vhost.read_text(encoding="utf-8")
    assert "(AhrefsBot|SemrushBot)" in written
    assert "client_max_body_size 64m;" in written
    assert reloaded, "nginx was never asked to test and reload the new config"


def test_a_full_rewrite_keeps_the_block_that_is_already_there(tmp_path, monkeypatch):
    """Regression, and it reached production.

    Rewriting a vhost re-renders it from the template. Callers that rebuild one
    - a PHP version change, a new alias, the per-site loop an update runs when
    nginx.py changes - do not know about bot lists, so the freshly rendered
    file had no block and every blocked bot was let straight back in. On the
    live server that silently turned off 52 bots across 23 sites; nothing
    failed, nginx -t passed, and the panel still listed the bots as blocked
    because the database was untouched.

    blocked_bots=None now means "keep what this vhost already blocks", the same
    contract preserve_existing_ssl has, so no call site has to remember.
    """
    domain = "example.com"
    vhost = tmp_path / f"{domain}.conf"
    vhost.write_text(
        "server {\n"
        "    server_name example.com;\n"
        + nginx._bot_block(["AhrefsBot", "bingbot/2.0"])
        + "\n}\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(nginx.settings, "command_dry_run", False)
    monkeypatch.setattr(nginx, "_vhost_path", lambda d: vhost)

    captured = {}

    def fake_render(*args, **kwargs):
        captured["blocked_bots"] = kwargs.get("blocked_bots")
        return "server {\n    server_name example.com;\n}\n"

    monkeypatch.setattr(nginx, "render_vhost", fake_render)
    monkeypatch.setattr(nginx, "_custom_include_snapshot", lambda d: None)
    monkeypatch.setattr(nginx, "_write_custom_include", lambda d, c: None)
    monkeypatch.setattr(nginx, "_write_backup", lambda t, c: None)
    monkeypatch.setattr(nginx, "_test_and_reload", lambda *a, **k: None)
    monkeypatch.setattr(nginx, "_append_certbot_redirect_vhosts", lambda c, d, r: c)
    monkeypatch.setattr(nginx, "_append_redirect_vhosts", lambda c, d, r, *a: c)

    nginx.rewrite_vhost(domain, "/home/admin/example.com", "php", "8.3")

    assert captured["blocked_bots"] == ["AhrefsBot", "bingbot/2.0"], (
        "a rewrite dropped the bot list instead of carrying it over"
    )


def test_an_explicit_empty_list_still_clears_the_block(tmp_path, monkeypatch):
    # Preserving must not make the block impossible to remove: [] is a real
    # instruction, only None means "keep what is there".
    vhost = tmp_path / "example.com.conf"
    vhost.write_text(
        "server {\n    server_name example.com;\n" + nginx._bot_block(["AhrefsBot"]) + "\n}\n",
        encoding="utf-8",
    )

    cleared = nginx._replace_bot_block(vhost.read_text(encoding="utf-8"), [])

    assert "BOT BLOCK" not in cleared


def test_the_block_sits_ahead_of_waf_and_flood():
    """Blocked traffic should cost as little as possible.

    Answering 403 on a regex match is cheaper than first running the request
    through ModSecurity's rule set and the rate-limit zones.
    """
    vhost = (
        "server {\n"
        "    server_name example.com;\n"
        "    # spanel HTTP FLOOD BEGIN\n"
        "    limit_req zone=x;\n"
        "    # spanel HTTP FLOOD END\n"
        "\n"
        "    # spanel WAF BEGIN\n"
        "    modsecurity on;\n"
        "    # spanel WAF END\n"
        "}\n"
    )

    result = nginx._replace_bot_block(vhost, "AhrefsBot")

    assert result.index("BOT BLOCK BEGIN") < result.index("HTTP FLOOD BEGIN")
    assert result.index("BOT BLOCK BEGIN") < result.index("WAF BEGIN")
