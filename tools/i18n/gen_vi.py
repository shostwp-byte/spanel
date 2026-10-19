"""Generate frontend/src/locales/vi.js from the Python translation parts.

The parts are the source of truth: a Python dict is easier to review and to
diff than 600 lines of JavaScript, and generating the JS means the escaping is
done once, correctly, rather than by hand 600 times.
"""

import pathlib
import runpy
import sys

HERE = pathlib.Path(__file__).parent
OUT = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "frontend/src/locales/vi.js")

# Whatever parts are on disk, in order. A hard-coded range means adding
# vi_part7.py silently generates a dictionary without it, and the only symptom
# is a screen that stays English.
merged = {}
for part in sorted(HERE.glob("vi_part*.py")):
    n = part.stem.removeprefix("vi_part")
    merged.update(runpy.run_path(str(part))[f"PART{n}"])

HEADER = '''/* Vietnamese. Keys are the English strings exactly as they appear in the
 * interface or come back from the API; see i18n.js for why the English is the
 * key rather than a name like nav.websites.
 *
 * Hosting words that Vietnamese administrators already say in English stay in
 * English - website, database, backup, SSL, token, plugin, port, cron. A panel
 * that translates those reads as machine output and is harder to use, not
 * easier. What is translated is the ordinary language around them.
 *
 * Anything absent here renders in English, which is the designed fallback, so
 * this file is never in a half-broken state and a string added tomorrow does
 * not have to wait for a translator.
 *
 * Generated from tools/i18n/vi_part*.py. Edit those and run
 *
 *     python tools/i18n/gen_vi.py frontend/src/locales/vi.js
 *
 * rather than editing here, or the next regeneration will overwrite you. The
 * parts are Python because a dict reviews and diffs better than 650 lines of
 * JavaScript, and because generating means the escaping is done once.
 */

export const vi = {
'''


def js(value):
    # Newlines and tabs are escaped, not emitted raw: a confirm() message with
    # a blank line in it is a perfectly ordinary key, and a raw newline inside
    # a double-quoted JS string is a syntax error that takes the whole panel
    # down rather than just that one string.
    escaped = (value.replace("\\", "\\\\").replace('"', '\\"')
               .replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t"))
    return f'"{escaped}"'


# A term whose Vietnamese is the English word does not belong in the file. The
# fallback already returns the English, so the entry is dead weight that makes
# the dictionary look more complete than it is. The decision to leave those
# terms alone is recorded in the header instead, where the next translator
# reads it before "fixing" them.
kept_in_english = sorted(key for key, value in merged.items() if key == value)
merged = {key: value for key, value in merged.items() if key != value}

note = [
    " *",
    " * The terms deliberately left in English are exported below as",
    " * keptInEnglish, one per line. They are a list rather than a sentence in",
    " * this comment because two of them contain commas, and a reader cannot",
    " * tell a term from a separator in prose. A test reads that list to decide",
    " * whether a string with no translation was a decision or an oversight.",
]

header = HEADER.rstrip("\n")
header = header.replace("\n */", "\n" + "\n".join(note) + "\n */", 1)

lines = [header]
for key in sorted(merged):
    lines.append(f"  {js(key)}: {js(merged[key])},")
lines += ["};", ""]

lines += [
    "/* Said in English by the people who run these servers. Translating them",
    " * makes the panel harder to read, not easier, so they carry no entry above",
    " * - the fallback returns the English - and are recorded here instead. */",
    "export const keptInEnglish = [",
]
lines += [f"  {js(term)}," for term in kept_in_english]
lines += ["];", ""]

OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text("\n".join(lines), encoding="utf-8")
print(f"{OUT}: {len(merged)} entries, {OUT.stat().st_size} bytes")
