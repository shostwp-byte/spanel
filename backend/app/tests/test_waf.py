import re
from pathlib import Path

import pytest

from app.services import waf

HELPER_SCRIPT = Path(__file__).resolve().parents[3] / "installer" / "files" / "spanel-helper.sh"


def test_default_rules_only_cover_wordpress_laravel_and_php():
    definitions = waf.default_rule_definitions()

    assert {rule["category"] for rule in definitions} == {"Laravel", "PHP", "WordPress"}
    assert all(rule["enabled_default"] for rule in definitions)


def test_legacy_heavy_rule_ids_are_mapped_or_ignored():
    assert waf.validate_enabled_rule_ids([
        "general-sensitive-files",
        "general-path-traversal",
        "general-sqli",
        "general-xss",
        "general-command-injection",
    ]) == ["php-sensitive-files", "php-path-traversal", "php-runtime-probes"]


def test_render_site_rules_only_includes_selected_wordpress_rule():
    content = waf.render_site_rules("example.com", ["wordpress-sensitive-files"])

    assert "id:1001101" in content
    assert "id:1001201" not in content
    assert "id:1001301" not in content


def test_render_site_rules_includes_laravel_and_php_rules():
    content = waf.render_site_rules("example.com", ["laravel-sensitive-files", "php-sensitive-files"])

    assert "id:1001201" in content
    assert "id:1001301" in content


def test_default_rules_do_not_scan_request_body_or_headers():
    content = waf.render_site_rules("example.com", [rule["id"] for rule in waf.DEFAULT_RULES])

    assert "REQUEST_BODY" not in content
    assert "REQUEST_HEADERS" not in content


def test_waf_rules_are_phase_1():
    """A phase:2 rule is silently dead while request bodies are not buffered.

    The nginx connector never runs phase 2 when SecRequestBodyAccess is Off, so
    such a rule loads, reports as enabled in the UI, and never matches anything.
    Two shipped rules sat like that until 2026-09-13. If body access is ever
    turned on - which is what OWASP CRS needs - this test should be revisited
    together with the exclusion tuning, not simply deleted.
    """
    helper = HELPER_SCRIPT.read_text(encoding="utf-8")
    assert "SecRequestBodyAccess Off" in helper, (
        "request bodies are buffered now; revisit the phase of every shipped rule"
    )

    offenders = [
        rule["id"] for rule in waf.DEFAULT_RULES if "phase:1," not in rule["rules"]
    ]
    assert offenders == [], f"these rules would never fire: {offenders}"


def test_removing_site_rules_goes_through_the_helper(monkeypatch):
    calls = []

    def fake_privileged(helper_command, helper_args=None, **kwargs):
        calls.append((helper_command, list(helper_args or [])))
        return waf.CommandResult(command=helper_command, returncode=0,
                                 stdout="Removed WAF rules for example.com", stderr="")

    monkeypatch.setattr(waf.shell, "privileged", fake_privileged)
    note = waf.remove_site_rules("example.com")

    assert calls == [("waf-site-delete", ["example.com"])]
    assert "Removed" in note


def test_removing_site_rules_never_raises(monkeypatch):
    def boom(*args, **kwargs):
        raise OSError("sudo went missing")

    monkeypatch.setattr(waf.shell, "privileged", boom)
    assert "could not remove" in waf.remove_site_rules("example.com")
    # A bogus domain is rejected quietly rather than blocking the deletion.
    assert waf.remove_site_rules("../../etc/nginx") == ""


def test_helper_will_not_delete_rules_a_vhost_still_uses():
    helper = HELPER_SCRIPT.read_text(encoding="utf-8")
    body = helper.split("delete_waf_site_rules()")[1].split("\n}\n")[0]
    # A missing modsecurity_rules_file fails `nginx -t`, so the next reload
    # anywhere would take every site on the box down.
    assert "modsecurity_rules_file" in body
    assert "deny " in body
    assert "require_domain" in body
    assert "waf-site-delete)" in helper
    # The check must read the config nginx actually loads. Grepping conf.d
    # instead matches the .conf.bak copies sitting next to every vhost, which
    # refused every real cleanup.
    assert "nginx -T" in body
    assert "/etc/nginx/conf.d/" not in body


def test_crs_is_absent_unless_it_is_switched_on():
    content = waf.render_site_rules("example.com", ["php-sensitive-files"], crs_mode="off")

    assert "spanel-crs.conf" not in content


@pytest.mark.parametrize("mode", ["detect", "block"])
def test_crs_include_lands_after_spanel_rules_and_before_custom(mode):
    content = waf.render_site_rules(
        "example.com",
        ["php-sensitive-files"],
        custom_rules="SecRuleRemoveById 942100",
        crs_mode=mode,
    )

    own = content.index("id:1001301")
    crs = content.index("spanel-crs.conf")
    exclusion = content.index("SecRuleRemoveById 942100")
    # spanel's own rules deny on one match and are cheaper, so they run first.
    assert own < crs
    # SecRuleRemoveById only affects rules already loaded, so a per-site
    # exception is worthless before the Include it is meant to act on.
    assert crs < exclusion


def test_an_unknown_crs_mode_never_silently_enables_it():
    assert waf.normalize_crs_mode("paranoid") == "off"
    assert waf.normalize_crs_mode(None) == "off"
    assert waf.normalize_crs_mode("BLOCK") == "block"
    content = waf.render_site_rules("example.com", ["php-sensitive-files"], crs_mode="paranoid")
    assert "spanel-crs.conf" not in content


def test_set_crs_mode_rejects_a_bogus_mode():
    with pytest.raises(ValueError):
        waf.set_crs_mode("paranoid", [])


def test_detect_mode_puts_the_blocking_threshold_out_of_reach():
    """Detect mode must observe without ever refusing a request.

    CRS expresses that as an anomaly threshold no request can reach: the two
    rules that act on the total never fire, while every individual rule still
    matches and still logs through crs-setup's SecDefaultAction.

    SecRuleUpdateActionById is not an alternative here. libmodsecurity rejects
    it for phase, pass and deny alike - "action has not expected to be used with
    UpdateActionByID" - and the failed directive takes the rest of the rule set
    with it, which on a live server left CRS loaded and matching nothing.
    """
    helper = HELPER_SCRIPT.read_text(encoding="utf-8")
    body = helper.split("write_crs_conf()")[1].split("\n}\n")[0]

    # The comments explain why SecRuleUpdateActionById is not used, so check the
    # lines that actually run rather than the whole function.
    code = "\n".join(l for l in body.splitlines() if not l.strip().startswith("#"))

    assert "inbound_anomaly_score_threshold=1000000" in code
    assert "inbound_anomaly_score_threshold=5" in code   # the blocking mode
    assert "SecRuleUpdateActionById" not in code
    # Raising the threshold silences 949110, which on a live server is the only
    # rule that logs at all - a request CRS blocks with a 403 produces exactly
    # one line, from 949110. Detect mode therefore has to report the score
    # itself, or it observes nothing.
    assert "TX:ANOMALY_SCORE" in code
    assert "id:1009001" in code
    assert "SecAuditLog" in code
    assert "blocking_paranoia_level=1" in code
    # CRS without request bodies sees only the URL, which is the state this
    # whole feature exists to leave behind.
    assert "SecRequestBodyAccess On" in code
    # A body over the limit must be inspected as far as it goes, not refused:
    # rejecting turns every large media upload into a 413.
    assert "SecRequestBodyLimitAction ProcessPartial" in code


def test_upgrading_an_older_install_never_switches_crs_on():
    """An existing server must update without CRS appearing on any site.

    CRS costs real memory per site, so a backfill would hand every customer on
    an upgraded box a bill they did not ask for - and on a small server, an
    outage. Migration 0030 deliberately backfills; this one deliberately does
    not, and the difference is easy to erase by copying the wrong template.
    """
    migration = (
        Path(__file__).resolve().parents[3]
        / "backend" / "alembic" / "versions" / "0031_website_crs_enabled.py"
    ).read_text(encoding="utf-8")

    assert 'server_default="0"' in migration
    # 0030 backfills with op.execute("UPDATE users SET terminal_enabled = 1").
    # Its absence here is the whole point, so assert on it directly.
    assert "op.execute" not in migration


def test_a_website_object_without_the_column_is_treated_as_off(monkeypatch):
    """Mid-upgrade, code can meet a Website row loaded before the column existed."""
    class OldRow:
        waf_enabled = True   # the WAF is on, but this predates crs_enabled

    assert waf.site_uses_crs(OldRow()) is False


def test_crs_applies_only_where_both_toggles_agree():
    class Row:
        def __init__(self, waf, crs):
            self.waf_enabled, self.crs_enabled = waf, crs

    assert waf.site_uses_crs(Row(True, True)) is True
    assert waf.site_uses_crs(Row(True, False)) is False
    # A site with the WAF switched off gets no CRS even if opted in, or turning
    # the WAF off would quietly leave the payload rules running.
    assert waf.site_uses_crs(Row(False, True)) is False
    assert waf.site_uses_crs(Row(False, False)) is False


def test_sync_site_rules_defaults_to_no_crs(monkeypatch):
    """A caller that says nothing about CRS must not switch it on.

    sync_site_rules used to fall back to the server-wide mode, so creating a
    website on a server set to block gave the new site CRS while its own
    crs_enabled flag said off - the opt-in, and the memory budget behind it,
    quietly stopped meaning anything.
    """
    written = {}

    def fake_privileged(helper_command, helper_args=None, **kwargs):
        written["content"] = kwargs.get("input", "")
        return waf.CommandResult(command=helper_command, returncode=0, stdout="saved", stderr="")

    monkeypatch.setattr(waf.shell, "privileged", fake_privileged)
    # Server-wide mode is block, and it must not leak into a caller that did not ask.
    monkeypatch.setattr(waf, "active_crs_mode", lambda: "block")

    waf.sync_site_rules("newsite.test", ["php-sensitive-files"])

    assert "spanel-crs.conf" not in written["content"]


def test_saving_a_sites_rules_does_not_change_its_crs_state(monkeypatch):
    written = []

    def fake_privileged(helper_command, helper_args=None, **kwargs):
        written.append(kwargs.get("input", ""))
        return waf.CommandResult(command=helper_command, returncode=0, stdout="saved", stderr="")

    monkeypatch.setattr(waf.shell, "privileged", fake_privileged)
    monkeypatch.setattr(waf, "active_crs_mode", lambda: "block")

    class Site:
        domain = "editme.test"
        waf_enabled = True
        crs_enabled = False
        waf_default_rules = ""
        waf_custom_rules = ""

    site = Site()
    waf.save_website_config(site, ["php-sensitive-files"], "")
    assert "spanel-crs.conf" not in written[-1]

    site.crs_enabled = True
    waf.save_website_config(site, ["php-sensitive-files"], "")
    assert "spanel-crs.conf" in written[-1]


def test_helper_exposes_the_crs_verbs():
    helper = HELPER_SCRIPT.read_text(encoding="utf-8")
    for verb in ("waf-crs-install)", "waf-crs-mode)", "waf-crs-status)"):
        assert verb in helper


def test_shipped_rules_match_the_helper_copy():
    """The rule text lives twice: in DEFAULT_RULES and in the installer helper.

    waf.py drives what a site gets and what the UI lists; the helper writes
    /etc/nginx/modsec/spanel-default.conf for the server-wide config. They drift
    apart silently - a fix applied to one is invisible in the other.
    """
    helper = HELPER_SCRIPT.read_text(encoding="utf-8")
    block = helper.split("cat >/etc/nginx/modsec/spanel-default.conf <<'RULES'")[1].split("\nRULES\n")[0]

    def rule_ids(text):
        return sorted(re.findall(r"id:(\d+)", text))

    def phases(text):
        return sorted(re.findall(r"id:(\d+),phase:(\d)", text))

    shipped = "\n".join(rule["rules"] for rule in waf.DEFAULT_RULES)
    assert rule_ids(block) == rule_ids(shipped)
    assert phases(block) == phases(shipped)


def test_unknown_rule_ids_are_rejected():
    with pytest.raises(ValueError, match="Unknown WAF rule"):
        waf.validate_enabled_rule_ids(["joomla-sensitive-files"])



def test_the_waf_switch_leaves_the_crs_opt_in_alone():
    """CRS is a per-site opt-in with its own switch on the site's WAF page
    (operator, 2026-09-25); the WAF switch does not touch it, and the CRS
    switch does not touch the WAF.

    The WAF switch sets waf_enabled before the rule file is rendered. The
    renderer reads it to decide whether the opt-in loads, and rendering first,
    as the endpoints used to, wrote the old state: turning the WAF back on
    left CRS out until some later sync.
    """
    api = Path(__file__).resolve().parents[1] / "api"
    source = (api / "websites.py").read_text(encoding="utf-8")
    assert ".crs_enabled =" not in source, "a WAF endpoint writes the CRS opt-in again"

    switches = [fn for fn in source.split("\ndef ") if "nginx.update_waf_block(website.domain, payload.waf_enabled)" in fn]
    assert len(switches) == 2, "both WAF endpoints are checked"
    for fn in switches:
        # update_website renders for other settings too; the render that
        # matters is the one between this assignment and the vhost switch.
        set_at = fn.index("website.waf_enabled = bool(payload.waf_enabled)")
        render_at = fn.index("waf.sync_website_rules(website)", set_at)
        switch_at = fn.index("nginx.update_waf_block(website.domain, payload.waf_enabled)")
        assert render_at < switch_at, "the rules are rendered from the old state"

    crs = (api / "waf.py").read_text(encoding="utf-8")
    crs = crs.split('@router.put("/websites/{website_id}/crs")', 1)[1].split("\n@router", 1)[0]
    assert "waf_enabled" not in crs, "the CRS switch moves the WAF again"


# --- the global custom rules (reported through MCP, 2026-09-27) ---------------------
# add_waf_rule wrote to spanel-custom.conf, which no vhost had loaded since the
# per-site rule files arrived on 2026-06-03: every global rule did nothing.

def test_every_site_rule_file_loads_the_global_custom_rules():
    content = waf.render_site_rules("example.test", [], "SecRuleRemoveById 1090001", crs_mode="block")
    include = f"Include {waf.GLOBAL_CUSTOM_PATH}"
    assert include in content
    # After CRS, before the site's own rules - so a site can still excuse
    # itself from a global rule with SecRuleRemoveById.
    assert content.index(f"Include {waf.CRS_CONF_PATH}") < content.index(include)
    assert content.index(include) < content.index("SecRuleRemoveById 1090001")


@pytest.mark.parametrize("match,value", [
    ("user_agent", "10_15_7"),            # every Mac browser
    ("user_agent", "Mozilla"),
    ("user_agent", "facebookexternalhit"),
    ("user_agent", "Googlebot"),
    ("user_agent", "coc_coc"),
    ("path", "/wp-json/batch/v1"),        # the block editor's own endpoint
    ("path", "wp-login"),
    ("path", "/"),
    ("ip", "0.0.0.0/0"),
    ("ip", "10.0.0.0/8"),
    ("ip", "127.0.0.1"),
])
def test_a_rule_that_would_block_ordinary_visitors_is_refused(match, value):
    assert waf.ordinary_traffic_conflict(match, value)
    with pytest.raises(ValueError, match="Refused"):
        waf.render_mcp_rule(match, value, rule_id=waf.MCP_RULE_ID_FIRST, who="admin", when="2026-09-27")


@pytest.mark.parametrize("match,value", [
    ("user_agent", "python-requests"), ("user_agent", "tphotobot"), ("user_agent", "MJ12bot"),
    ("path", "/wp-admin/install.php"), ("path", "/icecoder/"), ("path", "sftp-config.json"),
    ("ip", "45.13.0.0/24"), ("ip", "185.220.101.7"), ("ip", "2a0d:5600::/32"), ("query", "union+select"),
])
def test_a_narrow_rule_still_goes_through(match, value):
    assert waf.ordinary_traffic_conflict(match, value) == ""


def test_a_global_file_nginx_rejects_is_put_back():
    """Every site includes it now, so a bad rule left in place would fail the
    next reload for all of them."""
    helper = (Path(__file__).resolve().parents[3] / "installer" / "files" / "spanel-helper.sh").read_text(encoding="utf-8")
    body = helper.split("save_waf_custom_rules() {", 1)[1].split("\n}\n", 1)[0]
    assert 'cp "$target" "$backup"' in body and "if ! nginx -t; then" in body
    assert 'mv -f "$backup" "$target"' in body
    site = helper.split("save_waf_site_rules() {", 1)[1].split("\n}\n", 1)[0]
    assert "[[ -f /etc/nginx/modsec/spanel-custom.conf ]] || install -m 0644" in site, "an Include of a missing file fails nginx -t"


def test_the_global_rules_can_be_read_and_removed_on_the_waf_page():
    """add_waf_rule tells the assistant a wrong rule can be removed on the WAF
    page; until now the page had nowhere to see one."""
    app_jsx = (Path(__file__).resolve().parents[3] / "frontend" / "src" / "App.jsx").read_text(encoding="utf-8")
    assert "request('/waf/rules/custom', { method: 'PUT', body: JSON.stringify({ content: wafGlobalRules }) }" in app_jsx
    assert "<h2>{t('Global custom rules')}</h2>" in app_jsx
