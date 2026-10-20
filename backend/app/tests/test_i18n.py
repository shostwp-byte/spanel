"""Translation: the dictionary, the fallback, and the switch.

The design rests on one property - a string with no translation renders in
English rather than blank or as a key - so most of what is worth testing is
that the property holds and that nothing has drifted out of sync with it.

These read the source rather than running a browser. The panel has no
JavaScript test runner, and the things that break here are textual: a key that
no longer matches any string, a Vietnamese word left in the English source, a
t() wrapped around a page key.
"""

import json
import re
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[3]
SRC = PROJECT_ROOT / "frontend" / "src"
APP = (SRC / "App.jsx").read_text(encoding="utf-8")
I18N = (SRC / "i18n.js").read_text(encoding="utf-8")
VI = (SRC / "locales" / "vi.js").read_text(encoding="utf-8")


def _dictionary() -> dict:
    """The vi.js entries, parsed out of the generated file.

    Generated with one entry per line and JSON-compatible escaping, which is
    what makes this safe to read with a regex.
    """
    entries = {}
    for line in VI.splitlines():
        match = re.match(r'^  ("(?:[^"\\]|\\.)*"): ("(?:[^"\\]|\\.)*"),$', line.strip().join(("  ", "")))
        if not match:
            match = re.match(r'^\s+("(?:[^"\\]|\\.)*"): ("(?:[^"\\]|\\.)*"),$', line)
        if match:
            entries[json.loads(match.group(1))] = json.loads(match.group(2))
    return entries


DICTIONARY = _dictionary()


# --- the dictionary itself ---------------------------------------------------

def test_the_dictionary_parses_and_is_not_empty():
    assert len(DICTIONARY) > 500, f"only parsed {len(DICTIONARY)} entries"


def test_nothing_is_translated_to_itself():
    """An entry equal to its key is dead weight: it says the translator looked
    at it and changed nothing, which the fallback already does for free."""
    same = [k for k, v in DICTIONARY.items() if k == v]
    # Proper nouns and terms that genuinely do not change are left out of the
    # file entirely rather than mapped to themselves.
    assert not same, f"entries that translate to themselves: {same[:10]}"


def test_no_entry_is_empty():
    """An empty translation renders as a blank label, which is worse than
    English - the fallback exists precisely to avoid that."""
    empty = [k for k, v in DICTIONARY.items() if not v.strip()]
    assert not empty, empty[:10]


def test_every_key_is_a_string_something_actually_shows():
    """A key matching nothing is a translation nobody will ever see.

    Checked against the interface source and against the messages the API
    returns, which are the two places a key can legitimately come from. The
    API's messages include the ones a service writes and the API passes on -
    a malware scan's stage, which the job carries and the page translates -
    and the ones a request model's validator raises, which reach the page as
    the error of a refused form.
    """
    api = ""
    for folder in ("api", "services", "schemas"):
        for path in sorted((PROJECT_ROOT / "backend" / "app" / folder).glob("*.py")):
            api += path.read_text(encoding="utf-8")

    # t('Everyone else\'s tokens') in the source does not contain the raw
    # sentence, so compare against a copy with the escaping removed. Getting
    # this wrong reports five healthy entries as orphans, which is how it was
    # found. A confirm() dialog with a blank line in it is the same problem one
    # character further on: the source spells the newline \n, the string the
    # panel shows - and so the key - has a real one.
    unescaped = (APP.replace("\\'", "'").replace('\\"', '"')
                 .replace("\\n", "\n").replace("\\t", "\t"))
    orphans = [k for k in DICTIONARY if k not in unescaped and k not in api]
    assert not orphans, f"{len(orphans)} keys match nothing: {orphans[:10]}"


def test_every_placeholder_survives_translation():
    """A count sentence is translated whole, with {n} standing in for the
    number. Drop the placeholder and the number vanishes from the screen;
    invent one and it renders as the literal text {ammount}. Both are silent,
    so they are checked here."""
    for key, value in DICTIONARY.items():
        assert set(re.findall(r"\{(\w+)\}", key)) == set(re.findall(r"\{(\w+)\}", value)), key


def test_the_values_argument_is_used_where_a_sentence_has_a_hole():
    """t() filling placeholders is the whole reason the count sentences could be
    translated at all - as bare fragments either side of an interpolation they
    were untranslatable."""
    body = I18N.split("export function t(text")[1].split("\n}")[0]
    assert "if (!values) return out;" in body
    # An unknown name is left standing rather than blanked: a typo should be
    # visible on screen, not swallowed.
    assert "hasOwnProperty.call(values, name) ? String(values[name]) : whole" in body
    assert "{n} website(s)" in DICTIONARY


def test_the_vietnamese_addresses_the_reader_as_ban():
    """Vietnamese second-person pronouns carry age and sex.

    The first draft said "anh", which addresses a man older than the speaker -
    so every woman using the panel, and every customer younger than whoever
    wrote the string, was addressed wrongly by software they pay for. "bạn"
    assumes neither and is what Vietnamese software says. One new string is all
    it takes to reintroduce the others, which is why this is a test and not a
    note in a header.
    """
    gendered = ("anh", "chị", "em", "ông", "bà", "cô", "chú", "bác")
    offenders = []
    for key, value in DICTIONARY.items():
        words = re.findall(r"\w+", value.lower(), flags=re.UNICODE)
        for pronoun in gendered:
            if pronoun in words:
                offenders.append(f"{pronoun!r} in {key!r}")
    assert not offenders, "gendered address:\n" + "\n".join(offenders[:10])


def test_the_placeholders_and_code_samples_were_left_alone():
    """Example domains and code are not language, and translating them would
    make the example wrong."""
    for literal in ("domain.com", "user@domain.com", "s3.wasabisys.com",
                    "php -q cron.php >/dev/null 2>&1", "-----BEGIN CERTIFICATE-----"):
        assert literal not in DICTIONARY, literal


# --- the fallback, which is the whole design ---------------------------------

def test_an_unknown_string_falls_back_to_english():
    body = I18N.split("export function t(text")[1].split("\n}")[0]
    # Returns the input when there is no hit, rather than '' or the key name.
    assert "typeof hit === 'string' && hit ? hit : text" in body


def test_anything_that_is_not_a_string_passes_straight_through():
    """Render code hands null, numbers and elements around freely."""
    body = I18N.split("export function t(text")[1].split("\n}")[0]
    assert "if (typeof text !== 'string' || !text) return text;" in body


def test_an_unknown_language_code_is_refused():
    body = I18N.split("export function setLanguage(code)")[1].split("\n}")[0]
    assert "if (!isKnown(code)" in body


def test_storage_failures_do_not_break_the_panel():
    """localStorage throws in a private window and in some embedded browsers.
    A panel that will not load because it could not remember a preference is
    a worse panel."""
    assert I18N.count("try {") >= 4
    assert "catch { return null; }" in I18N


# --- the switch --------------------------------------------------------------

def test_the_switch_exists_and_offers_both_languages():
    assert "function LanguageToggle(" in APP
    assert "['vi', 'Tiếng Việt']" in I18N
    assert "['en', 'English']" in I18N


def test_the_switch_is_one_button_not_a_menu():
    """Two languages need one button, not a list to open and choose from. It
    says where it goes - EN or VI - rather than where you are, so there is
    nothing to read twice."""
    toggle = APP.split("function LanguageToggle(")[1].split("\nfunction ")[0]
    assert "<select" not in toggle and "LANGUAGES.map(" not in toggle
    assert "const next = language === 'vi' ? 'en' : 'vi';" in toggle
    # Labelled in the language it switches to, so a Vietnamese reader who has
    # the panel in English can still recognise it.
    assert "'Chuyển sang Tiếng Việt' : 'Switch to English'" in toggle


def test_the_switch_is_two_letters_and_no_icon():
    """EN and VI are already the picture. A globe or a speech bubble beside them
    says nothing the letters do not, and a flag would name a country."""
    toggle = APP.split("function LanguageToggle(")[1].split("\nfunction ")[0]
    assert "<Languages" not in toggle and "<svg" not in toggle
    assert "{next === 'vi' ? 'VI' : 'EN'}" in toggle
    # The icon import left with it rather than sitting unused in the bundle.
    assert "Languages," not in APP


def test_the_two_toggles_are_one_control_in_css():
    """They sit side by side and do the same kind of thing, so they share the
    box outright. A second copy of it drifts the first time either is
    adjusted - which is the whole reason for the shared selector."""
    theme_css = (SRC / "theme.css").read_text(encoding="utf-8")
    assert ".theme-toggle,.language-toggle{" in theme_css
    assert ".theme-toggle:hover:not(:disabled),.language-toggle:hover:not(:disabled)" in theme_css


def test_the_switch_sits_with_the_theme_toggle():
    """Two controls that do the same kind of thing belong together; a person
    who found one will look for the other in the same place."""
    assert APP.count("<LanguageToggle language={language} onChange={changeLanguage}/>") == 3
    assert APP.count("<ThemeToggle theme={theme} onToggle={toggleTheme}/>") == 3


def test_changing_the_language_re_renders():
    """t() is read during render, so the tree has to render again. The hook
    subscribes to the event setLanguage fires."""
    assert "const [language, changeLanguage] = useLanguage();" in APP
    hook = I18N.split("export function useLanguage()")[1].split("\n}")[0]
    assert "addEventListener(LANGUAGE_EVENT" in hook


def test_the_page_language_is_announced():
    """Screen readers and the browser's own translation prompt both read it."""
    assert 'setAttribute(\'lang\'' in I18N


# --- what the sweep must not have touched ------------------------------------

@pytest.mark.parametrize("page_key", [
    "navigateToPage('websites')", "navigateToPage('files')",
    "routeForPage('files')",
])
def test_page_keys_were_not_translated(page_key):
    """The keys are lowercase and the dictionary is not, which is what kept
    them safe - but it is worth holding, because a future entry could collide."""
    assert page_key in APP


def test_no_class_name_was_wrapped():
    assert "className={t(" not in APP


# --- the dashboard map, where a miss is invisible ----------------------------

def _kept_in_english() -> set:
    block = VI.split("export const keptInEnglish = [")[1].split("];")[0]
    return {json.loads(m) for m in re.findall(r'"(?:[^"\\]|\\.)*"', block)}


KEPT_IN_ENGLISH = _kept_in_english()


def test_the_terms_left_in_english_are_declared_not_guessed():
    """The list is what separates a decision from an oversight, so it has to be
    there and it has to be a list."""
    assert len(KEPT_IN_ENGLISH) > 20
    for term in ("WAF", "Cron", "SSL", "Fail2ban"):
        assert term in KEPT_IN_ENGLISH, term


def test_every_string_the_overview_draws_reads_in_vietnamese():
    """Every t('...') on the dashboard, not only the headings.

    Its status words sit beside a coloured dot - "Running", "Failed" - and one
    left in English is the kind of gap nobody reports, because the dot still
    says enough to get by."""
    body = APP.split("function renderDashboard()")[1].split("function renderAdminOnly()")[0]
    drawn = set(re.findall(r"t\('((?:[^'\\]|\\.)*)'", body))
    assert len(drawn) > 25, f"only matched {len(drawn)} strings - the pattern missed some"
    missing = sorted(text for text in drawn if text not in DICTIONARY and text not in KEPT_IN_ENGLISH)
    assert not missing, f"overview strings still in English: {missing}"


def test_every_sidebar_entry_reads_in_vietnamese():
    body = APP.split("const navSections = [")[1].split("].filter(section")[0]
    labels = re.findall(r"\['[a-z-]+', '([^']+)'", body) + re.findall(r"title: '([^']+)'", body)
    # Thirteen since the sidebar was cut to the everyday pages (2026-09-27);
    # the rest are checked on the Settings page.
    assert len(labels) >= 12, labels
    missing = [label for label in labels if label not in DICTIONARY and label not in KEPT_IN_ENGLISH]
    assert not missing, f"sidebar still in English: {missing}"


def test_the_interface_source_has_no_vietnamese_left_in_it():
    """English is the source language; Vietnamese belongs in the dictionary.

    A "Sau" button survived the sweep that made the interface English and was
    only found when the translation work went looking for strings to extract.
    Its sibling said "Previous".
    """
    vietnamese_only = "ăâđêôơưĂÂĐÊÔƠƯáàảãạéèẻẽẹíìỉĩịóòỏõọúùủũụýỳỷỹỵ"
    offenders = []
    for number, line in enumerate(APP.splitlines(), start=1):
        if line.lstrip().startswith(("//", "*", "/*")):
            continue
        if any(character in line for character in vietnamese_only):
            offenders.append(f"{number}: {line.strip()[:80]}")
    assert not offenders, "Vietnamese in App.jsx:\n" + "\n".join(offenders[:5])


# --- the API messages --------------------------------------------------------

def test_api_errors_are_translated_where_they_are_shown():
    """The server does not know the reader's language; the panel does."""
    body = APP.split("function formatApiError(")[1].split("\n}")[0]
    assert "return t(fallback)" in body
    assert "t(cleaned)" in body


def test_a_handful_of_api_messages_have_translations():
    for message in ("Website not found", "Invalid username or password",
                    "Cannot delete yourself", "Request failed."):
        assert message in DICTIONARY, message


def test_a_translated_label_is_not_glued_to_its_value():
    """"Hiện tạiv1.0.162", "Bộ nhớ server:7941 MB", "Trạng thái hiện tại:Disabled".

    Wrapping "Current <strong>" in t() turned it into {t('Current')}<strong>,
    and the space that used to sit in the JSX text went with it - on 32 lines,
    in both languages. Outside a flex row nothing puts it back.
    """
    glued = re.findall(r"\{t\('(?:[^'\\]|\\.){1,80}'\)\}<(?:strong|b|code)>", APP)
    assert not glued, f"{len(glued)} labels run into their value: {glued[:3]}"


def test_every_settings_page_entry_reads_in_vietnamese():
    """The Settings page's names and one-line hints live in an array, like the
    sidebar's, and are translated where they are drawn."""
    hub = APP.split("const settingsGroups = [")[1].split("const settingsItems = ")[0]
    rows = re.findall(r"\['[a-z-]+', '([^']+)', \w+, '([^']+)'\]", hub)
    # Nine since the malware scanner became an addon with a sidebar entry.
    assert len(rows) >= 9, rows
    texts = [text for row in rows for text in row] + re.findall(r"title: '([^']+)'", hub)
    missing = [text for text in texts if text not in DICTIONARY and text not in KEPT_IN_ENGLISH]
    assert not missing, f"Settings page still in English: {missing}"


def test_every_addon_card_is_in_vietnamese():
    """Seen 2026-09-28: the Application, Fail2ban and MCP cards on the Addons
    page were still English in the Vietnamese UI - their catalogue text had
    never been added to the dictionary. Read from the catalogue itself, so an
    addon added later fails here until its card is translated."""
    from app.services import addons

    texts = [text for entry in addons.CATALOGUE.values()
             for text in (entry["name"], entry["summary"], *entry["details"], *entry["notes"])]
    missing = [text for text in texts if text not in DICTIONARY and text not in KEPT_IN_ENGLISH]
    assert not missing, f"Addon cards still in English: {missing}"
