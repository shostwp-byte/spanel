"""The night unattended-upgrades deleted a customer's MySQL extension.

On 2026-09-23 at 06:16, unattended-upgrades removed php8.3-mysql from a
customer VPS. Three WordPress sites answered "Your PHP installation appears to
be missing the MySQL extension" until someone noticed.

It was not a distribution accident. apt's autoremove takes any package that is
marked auto-installed and that nothing depends on, and every PHP extension is
exactly that: php8.3-mysql is a leaf, installed as a dependency of nothing.
Ubuntu ships Remove-Unused-Dependencies as false for this reason. spanel's own
apt config set it true, so we asked for the deletion.

Two independent guards, because either one alone leaves a hole: the setting
can be turned back on from the panel, and `apt autoremove` typed by hand
ignores the setting entirely. Marking the packages manual survives both.
"""

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
HELPER_SCRIPT = PROJECT_ROOT / "installer" / "files" / "spanel-helper.sh"
INSTALL_SCRIPT = PROJECT_ROOT / "installer" / "install.sh"
UPDATE_SCRIPT = PROJECT_ROOT / "installer" / "update.sh"


def test_the_panel_never_asks_apt_to_remove_unused_packages():
    """The setting that did it. On a web server "unused" is not "unwanted"."""
    helper = HELPER_SCRIPT.read_text(encoding="utf-8")
    assert 'Remove-Unused-Dependencies "true"' not in helper, (
        "this removed php8.3-mysql overnight and took three sites down"
    )
    assert 'Remove-Unused-Dependencies "false"' in helper, (
        "state it explicitly rather than relying on the apt default"
    )


def test_the_setting_is_written_for_both_update_modes():
    """The file is written whichever origins the operator picked."""
    helper = HELPER_SCRIPT.read_text(encoding="utf-8")
    assert helper.count('Unattended-Upgrade::Remove-Unused-Dependencies') == 1, (
        "one config template, one setting - a second copy would drift"
    )


def test_php_packages_are_marked_manual_at_install():
    """So autoremove cannot take them even where the setting is on."""
    install = INSTALL_SCRIPT.read_text(encoding="utf-8")
    body = install[install.index("install_php() {"):]
    body = body[:body.index("install_ioncube_loader")]
    assert "apt-mark manual" in body
    assert body.index('apt_get install -y "${available_packages[@]}"') < body.index("apt-mark manual"), (
        "mark them after they exist, not before"
    )


def test_existing_installs_are_repaired_on_update():
    """Every VPS already out there has the setting we shipped."""
    update = UPDATE_SCRIPT.read_text(encoding="utf-8")
    assert "migrate_protect_php_from_autoremove() {" in update
    body = update[update.index("migrate_protect_php_from_autoremove() {"):]
    body = body[:body.index("\nmigrate_site_cron_php_binary")]

    # It has to fix the config...
    assert 'Remove-Unused-Dependencies "false"' in body
    # ...and hold the packages, because the config can be turned back on and a
    # hand-typed `apt autoremove` never consults it at all.
    assert "apt-mark manual" in body
    # Only packages that are really installed: dpkg-query reports a removed
    # package as "deinstall ok config-files", and apt-mark on that is noise.
    assert 'dpkg-query -W' in body and '"installed"' in body


def test_the_repair_actually_runs():
    """A migration nobody calls fixes nothing."""
    update = UPDATE_SCRIPT.read_text(encoding="utf-8")
    calls = [line.strip() for line in update.splitlines()
             if line.strip() == "migrate_protect_php_from_autoremove"]
    assert len(calls) == 1, "the migration is defined but never invoked"
