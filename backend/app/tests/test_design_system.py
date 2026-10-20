"""The design decisions that a single careless edit undoes.

None of this is about taste. Each test below is a rule that was broken in the
source before the design pass, that nothing complained about, and that shows up
only as a screen somebody has to look at - which is why it is a test and not a
note in a stylesheet.

They read the CSS and the interface source. The panel has no browser test
runner, and every one of these is a textual fact.
"""

import re
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[3]
SRC = PROJECT_ROOT / "frontend" / "src"
THEME = (SRC / "theme.css").read_text(encoding="utf-8")
STYLE = (SRC / "style.css").read_text(encoding="utf-8")
BRAND = (SRC / "brand.css").read_text(encoding="utf-8")
FILES = (SRC / "file-manager.css").read_text(encoding="utf-8")
APP = (SRC / "App.jsx").read_text(encoding="utf-8")
ALL_CSS = {"theme.css": THEME, "style.css": STYLE, "brand.css": BRAND,
           "file-manager.css": FILES}


# --- the two dark themes have to stay one theme ------------------------------

def _dark_blocks():
    """The dark tokens as they are written twice: once for the explicit choice,
    once for the OS preference."""
    chosen = THEME.split(':root[data-theme="dark"]{')[1].split("\n}")[0]
    preferred = THEME.split(':root:not([data-theme="light"]){')[1].split("\n  }")[0]
    parse = lambda block: {  # noqa: E731
        name.strip(): value.strip()
        for name, _, value in (line.partition(":") for line in block.split(";"))
        if name.strip().startswith("--")
    }
    return parse(chosen), parse(preferred)


def test_dark_mode_is_defined_identically_in_both_places():
    """One block applies when the reader picked dark, the other when their
    system did. They are the same theme, and CSS has no way to say so, so a
    token retuned in one and forgotten in the other gives two different dark
    modes depending on how the reader got there - which nobody would think to
    check.
    """
    chosen, preferred = _dark_blocks()
    assert chosen, "no explicit dark block found"
    differences = {
        name: (chosen.get(name), preferred.get(name))
        for name in set(chosen) | set(preferred)
        if chosen.get(name) != preferred.get(name)
    }
    assert not differences, f"dark tokens that differ between the two blocks: {differences}"


def test_dark_surfaces_are_far_enough_apart_to_see():
    """A card on the page background, and a shaded box inside the card, were
    seven points of luminance apart in dark mode - so the structure of a page
    read as a vague grid of rectangles."""
    chosen, _ = _dark_blocks()

    def luminance(token):
        value = chosen[token].lstrip("#")
        r, g, b = (int(value[i:i + 2], 16) for i in (0, 2, 4))
        return 0.2126 * r + 0.7152 * g + 0.0722 * b

    assert luminance("--surface") - luminance("--bg") >= 6
    assert luminance("--surface-alt") - luminance("--surface") >= 6


# --- type and the mono stack -------------------------------------------------

def test_the_type_scale_is_declared_once_and_used():
    for token in ("--text-xs", "--text-sm", "--text-hint", "--text-base",
                  "--text-lg", "--text-xl", "--font-sans", "--font-mono", "--measure"):
        assert token in THEME, token
    assert "font-size:var(--text-base)" in THEME.replace(" ", "")


@pytest.mark.parametrize("name", ["style.css", "brand.css", "file-manager.css"])
def test_no_stylesheet_spells_out_its_own_monospace_stack(name):
    """There were fourteen copies of it in three different orders, so a path
    and the command beside it could render in different faces."""
    body = re.sub(r"/\*.*?\*/", "", ALL_CSS[name], flags=re.S)
    hardcoded = [line.strip()[:90] for line in body.splitlines()
                 if "monospace" in line and "var(--font-mono)" not in line]
    assert not hardcoded, hardcoded[:5]


def test_numbers_line_up():
    """Columns of sizes, ports and percentages, with a figure that changes in
    place every few seconds. Proportional digits make those jump."""
    assert "font-variant-numeric:tabular-nums" in THEME.replace(" ", "")


def test_prose_is_capped_at_a_readable_measure():
    assert "max-width:var(--measure)" in STYLE.replace(" ", "")


# --- hierarchy ---------------------------------------------------------------

def test_refresh_is_never_the_loudest_button_on_a_page():
    """Re-reading a list is the one action on any page that cannot go wrong.
    Twenty-three of these were filled brand blue, so on the settings page the
    button that re-reads the form shouted as loud as the one that saves it.
    """
    loud = []
    for match in re.finditer(r"<button([^>]*)>((?:(?!</button>).){0,220}?t\('Refresh'\))", APP, re.S):
        attributes = match.group(1)
        if "className" not in attributes:
            loud.append(match.group(0)[:70])
            continue
        classes = re.search(r'className="([^"]*)"', attributes)
        if classes and "secondary" not in classes.group(1):
            loud.append(match.group(0)[:70])
    assert not loud, f"{len(loud)} primary Refresh buttons: {loud[:3]}"


def test_delete_in_a_list_row_is_a_glyph_not_a_block():
    """Twenty-two sites meant twenty-two filled red squares down the page, each
    at the end of a row of five identical ones - which is where a cursor
    overshoots. Pointing at it still turns it fully red."""
    quiet = BRAND.split(".site-icon-button.danger,")[1].split("}")[0]
    assert "background:var(--surface)" in quiet
    assert "color:var(--danger)" in quiet
    assert ".site-icon-button.danger:hover:not(:disabled)" in BRAND


UI = (SRC / "ui.css").read_text(encoding="utf-8")


def _dashboard():
    return APP.split("function renderDashboard()")[1].split("function renderAdminOnly()")[0]


def test_the_dashboard_says_how_things_stand_not_the_sidebar_again():
    """Its Hosting / Security / System blocks repeated the sidebar item for
    item (operator, 2026-09-25). What is left is what the sidebar cannot say:
    the meters, a card per thing that can be wrong, what needs attention and
    the few things done often - as in OPanel, one brand, one layout."""
    dashboard = _dashboard()
    assert "featureGroups" not in APP and "function FeatureTile(" not in APP
    assert "dash-resources" in dashboard and 'className={`status-card tone-${card.tone}`}' in dashboard
    for key in ("websites", "ssl", "databases", "backups", "firewall", "waf", "malware", "services"):
        assert f"key: '{key}'" in dashboard, key
    assert "<h2>{t('Needs attention')}</h2>" in dashboard and "<h2>{t('Quick actions')}</h2>" in dashboard
    assert "t('Everything looks fine.')" in dashboard
    # "New website" and "New database" arrive with their form open.
    assert "setCreateFormOpen(true); navigateToPage('websites');" in dashboard
    assert "setDbCreateOpen(true); navigateToPage('databases');" in dashboard


def test_the_card_rail_says_how_bad_it_is():
    for tone, colour in (("ok", "success"), ("warn", "warning"), ("bad", "danger")):
        assert f".status-card.tone-{tone},.status-card.tone-{tone}:hover:not(:disabled){{border-left-color:var(--{colour})}}" in UI


def test_a_customer_is_not_shown_the_server():
    """Server state is an administrator's: the endpoint leaves it out for a
    customer, and the cards that read it sit in the admin branch."""
    dashboard = _dashboard()
    index = dashboard.index("cards.push({ key: 'services'")
    before = dashboard[:index]
    assert before.rfind("if (isAdmin) {") > before.rfind("} else {"), "the services card is not inside the admin branch"
    customer = dashboard.split("} else {\n      cards.push(sslCard);", 1)[1].split("\n    }\n", 1)[0]
    assert "key: 'waf'" in customer and "key: 'security'" in customer
    assert "key: 'websites'" not in customer and "key: 'databases'" not in customer, (
        "the plan-usage card above already counts them"
    )


def test_the_sidebar_holds_what_is_used_every_day():
    """The operator's list (2026-09-27): Dashboard, Website, Application, SSL,
    Database, Cron, File manager, SFTP, Backups, Panel users - then the addons
    that are on, then Settings. Everything else is one click further, on the
    Settings page, which is a page and not a collapsed submenu: a folded
    Settings once hid the MCP page from the operator."""
    sidebar = APP.split("const navSections = [")[1].split("].filter(section")[0]
    keys = re.findall(r"\['([a-z-]+)', '", sidebar)
    assert keys == ["dashboard", "websites", "applications", "ssl", "databases", "cron", "files",
                    "sftp", "backups", "users", "dns", "mail", "mcp", "notifications", "malware", "settings"]
    assert "sidebar-subnav" not in APP and "settingsMenuOpen" not in APP
    hub = APP.split("const settingsGroups = [")[1].split("const settingsItems = ")[0]
    hub_keys = re.findall(r"\['([a-z-]+)', '", hub)
    # Panel settings heads its group (operator, 2026-09-27); the malware
    # scanner is an addon now and sits in the sidebar once it is on.
    assert hub_keys == ["firewall", "waf", "access-logs", "security",
                        "panel-settings", "services", "php", "updates", "addons"]
    assert not set(keys) & set(hub_keys), "a page in both places"
    assert "if (page === 'settings') return renderSettingsHub();" in APP


def test_a_settings_page_keeps_its_own_name_and_lights_up_settings():
    assert "const navPage = settingsItems.some(([key]) => key === basePage) ? 'settings' : basePage;" in APP
    assert "<h1>{pageItem?.[1] ? t(pageItem[1])" in APP


def test_panel_settings_is_four_tabs():
    """Operator, 2026-09-27: "Phần panel setting: Chia làm 4 tab nhé". One
    underlined row, like Backups and Panel users, one form showing at a time."""
    page = APP.split("  function renderPanelSettings() {")[1].split("  function renderUsers() {")[0]
    tabs = re.findall(r"\['([a-z]+)', '([^']+)', \w+\],", page.split("const tabs = [")[1].split("];")[0])
    assert tabs == [("general", "General"), ("brand", "Brand assets"), ("account", "Admin account"), ("api", "API Tokens")]
    assert 'className="segmented-control backup-tabs" role="tablist"' in page
    for key, _label in tabs:
        assert f"{{taspanel('{key}', <>" in page, key
    assert page.count('<section className="section') == 2, "the no-permission answer and the page: the tabs are not separate sections"
    # The tokens used to be their own page; an old link still opens their tab.
    assert "/^\\/api-tokens?\\/?$/i.test(window.location.pathname) ? 'api' : 'general'" in APP


def test_sign_out_lives_in_the_account_menu():
    """The top bar is the page title and one account menu, as in OPanel."""
    assert 'className="user-menu-panel"' in APP
    menu = APP.split('className="user-menu-panel"')[1].split("</div>}")[0]
    assert "logout()" in menu and "navigateToPage('security')" in menu
    assert "account-pill" not in APP


def test_a_full_disk_does_not_look_like_an_empty_one():
    """The bar changes colour before anyone reads the number."""
    card = APP.split("function ResourceCard(")[1].split("function renderDashboard()")[0]
    assert "safePercent >= 90 ? ' level-critical' : safePercent >= 80 ? ' level-warn'" in card
    assert ".resource-card.level-warn .resource-track span{background:var(--warning)}" in UI
    assert ".resource-card.level-critical .resource-track span{background:var(--danger)}" in UI
    # .danger is the delete button: on a card it painted the whole box red.
    assert "' danger'" not in card and "' warn'" not in card


def test_eight_cards_never_leave_two_over():
    """Four and four, then two columns - never 3 + 3 + 2."""
    assert "@media(max-width:1100px){.status-grid.many{grid-template-columns:repeat(2,minmax(0,1fr))}}" in UI
    phone = UI.split("/* ---------- Phones ---------- */")[1]
    assert ".status-grid,.status-grid.many{grid-template-columns:repeat(2,minmax(0,1fr));" in phone


def test_the_create_website_form_is_not_in_front_of_the_list():
    """Making a site is occasional; finding one is why the page gets opened.
    The form was four hundred pixels of it above the list."""
    assert "const [createFormOpen, setCreateFormOpen] = useState(false);" in APP
    assert "const createOpen = createFormOpen || websites.length === 0;" in APP
    # An empty panel is the exception: there the form is the only thing to do.
    assert "{createOpen && <section className=\"section create-site-section\">" in APP


def test_the_file_list_says_what_its_columns_are():
    """755 and 4.1 KB floated between the name and the buttons under no
    heading at all."""
    header = APP.split('<div className="file-list-header">')[1].split("</div>")[0]
    for column in ("t('Name')", "t('Mode')", "t('Size')", "t('Modified')"):
        assert column in header, column
    grid = FILES.split(".file-list-header {")[1].split("}")[0]
    row = FILES.split(".file-item {")[1].split("}")[0]
    columns = lambda block: re.search(r"grid-template-columns:([^;]+);", block).group(1).strip()  # noqa: E731
    assert columns(grid) == columns(row), "header and row columns must line up"


def test_a_label_from_an_array_goes_through_the_dictionary():
    """The same mistake as the headings, five more times.

    A list of [id, label, Icon] rows drawn by one shared line of JSX: the
    labels are ordinary English sentences, but nothing wraps them, so the
    backup tabs, the website-type options, the CRS switch and the chmod presets
    all stayed English while the page around them was Vietnamese. One raw
    {label} in a render site is the whole bug, so that is what this looks for.
    """
    # {t(label)} does not contain "{label}", so a plain search finds exactly
    # the unwrapped ones. Requiring a > in front keeps it to JSX text: it steps
    # over key={label} on an attribute and ${label} inside a template string.
    drawn_raw = {
        APP[match.start():match.end() + 20].split("\n")[0]
        for match in re.finditer(r"(?<=>)\{label\}", APP)
    }
    allowed = {
        # ResourceCard is handed an already-translated string by its caller.
        "{label}</span></div>",
        # Product and file names: "Claude Code", "Cursor - .cursor/mcp.json".
        "{label}</strong>",
    }
    unexpected = sorted(text for text in drawn_raw if text not in allowed)
    assert not unexpected, f"labels drawn without t(): {unexpected}"


def test_the_dashboard_headings_are_translated_where_they_are_drawn():
    """Having the words in the dictionary is not the same as using them.

    Every group heading was a key with a Vietnamese translation, and the
    dictionary check passed - while the page drew {group.title} raw, so the
    sidebar read "Tổng quan" beside a heading that read "Dashboard". The
    launcher's headings and tile names go through t() where drawn, and the
    nav array still feeds the page title in the topbar.
    """
    dashboard = _dashboard()
    assert "<h2>{t('Server resources')}</h2>" in dashboard
    # Card labels are translated where they are built; the one left raw is
    # WAF, which reads the same in both languages.
    assert re.findall(r"label: '([^']+)'", dashboard) == ["WAF", "WAF"]
    assert "<p className=\"sidebar-section-title\">{t(section.title)}</p>" in APP
    assert "{pageItem?.[1] ? t(pageItem[1])" in APP


# --- the operator's list, 2026-09-25 -----------------------------------------

def test_a_filter_box_does_not_grow_tall_on_a_phone():
    """flex-basis 200px was the filter's width in a row; when the list head
    stacked on a phone it became its height - a 200px-tall text box. The raw
    firewall output beside it was squeezed to a single 20px line."""
    phone = UI.split("/* ---------- Phones ---------- */")[1]
    assert ".detail-head input{flex:0 0 auto}" in phone
    assert ".detail-body > pre{flex:0 0 auto;" in UI


def test_mode_size_and_date_have_cells_of_their_own_when_narrow():
    """Mode and size were placed in grid column 2, row 2, and drew over each
    other on a phone; the date shares their line, in a column of its own."""
    narrow = UI.split("/* ---------- File list, below the six-column width ----------")[1]
    assert narrow.split("*/", 1)[1].lstrip().startswith("@media(max-width:1240px){")
    assert ".file-item > .file-mode{grid-column:2;grid-row:2;" in narrow
    assert ".file-item > .file-size{grid-column:3;grid-row:2}" in narrow
    assert ".file-item > .file-modified{grid-column:4;grid-row:2;" in narrow


def test_the_file_list_says_when_each_file_was_changed():
    """The API has sent every entry's mtime all along; nothing drew it."""
    row = APP.split('<div className="file-list">')[1].split("</div>)}")[0]
    assert '<span className="file-modified" title={formatFileTime(item.modified, true)}>{formatFileTime(item.modified)}</span>' in row


def test_a_scan_from_the_history_opens_its_own_page():
    """Clicking a past run used to unfold its details at the foot of a long page."""
    assert "'malware-scan': '/malware-scan'" in APP
    assert "onClick={() => { showMalwareScanJob(job); navigateToPage('malware-scan'); }}" in APP
    assert "if (page === 'malware-scan') {" in APP


def test_a_cron_schedule_is_picked_rather_than_typed():
    assert "const CRON_SCHEDULE_PRESETS = [" in APP
    assert "{CRON_SCHEDULE_PRESETS.map(([value, label]) => <option key={value} value={value}>{t(label)}</option>)}" in APP
    assert "<option value=\"custom\">{t('Custom...')}</option>" in APP


def test_a_new_page_opens_at_its_top():
    """The window scrolls, and nothing reset it on a page change: the next page
    opened at the last one's offset, part-way down or in empty space. Instant,
    because html{scroll-behavior:smooth} would otherwise slide it there."""
    effect = APP.split("useLayoutEffect(() => {", 1)[1].split("}, [page]);", 1)[0]
    assert "window.scrollTo({ top: 0, left: 0, behavior: 'instant' })" in effect
    assert "window.history.scrollRestoration = 'manual'" in APP


def test_scan_history_rows_are_as_tall_as_their_content():
    """The list's max-height was shared out among auto rows and cut each run to
    its 50px minimum; the wrapped title and badge spilled over the next one."""
    assert ".scan-history-list{grid-auto-rows:max-content}" in UI
    phone = UI.split("/* ---------- Phones ---------- */")[1]
    assert ".scan-history-list{max-height:none;overflow:visible;" in phone


def test_every_row_puts_its_columns_where_the_header_does():
    """Each row is a grid of its own. With an auto actions column every row
    sized it to its own buttons - two for a folder, four for an archive - and
    mode, size and date moved up to 120px from row to row, under no heading."""
    row = FILES.split(".file-item {")[1].split("}")[0]
    assert re.search(r"grid-template-columns:[^;]* 264px;", row), "the actions column is not a fixed width"
    for block in (".file-list {", ".file-list-header {"):
        assert "scrollbar-gutter: stable;" in FILES.split(block)[1].split("}")[0], block
    # A three-line row was cut to ~40px by the list's max-height otherwise.
    assert ".file-list{grid-auto-rows:max-content}" in UI


def test_no_function_in_the_app_is_declared_twice():
    """A second `function renderNotifications()` - the Notifications page -
    silently replaced the toast renderer of the same name: every toast in the
    panel drew the Notifications page instead, and nothing failed. Inside one
    component the later declaration simply wins."""
    names = re.findall(r"^  (?:async )?function (\w+)\(", APP, flags=re.M)
    duplicated = sorted({name for name in names if names.count(name) > 1})
    assert not duplicated, f"declared more than once: {duplicated}"


def test_the_updates_page_prints_no_stray_zero():
    """A panel update at 0% made `{(updating || (progress_percent && ...)) && ...}`
    evaluate to 0, and React printed "0" under the buttons (seen on the demo
    server, 2026-09-27)."""
    page = APP.split("  function renderUpdates() {")[1].split("\n  function ")[0]
    assert "(panelUpdate.progress_percent &&" not in page
    assert "Number(panelUpdate.progress_percent) > 0 &&" in page
