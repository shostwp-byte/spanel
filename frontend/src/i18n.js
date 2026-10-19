/* Translation, keyed by the English sentence itself.
 *
 * `t('Websites')` looks up 'Websites' in the active dictionary and returns the
 * English back when there is no entry. Three things follow from that, and they
 * are the reason it is built this way rather than with named keys:
 *
 *   - A missing translation is invisible. The customer sees English, never an
 *     empty label or a raw key like `nav.websites`. So a half-finished
 *     dictionary is shippable, and a string added tomorrow does not have to
 *     wait for a translator before the feature goes out.
 *   - The code still reads as the interface. `t('Delete this website?')` in a
 *     diff says what it does; `t('sites.delete.confirm')` sends the reviewer
 *     to another file.
 *   - There is no key to get wrong. Renaming a key and forgetting one call
 *     site is the usual way named-key systems break, and it cannot happen
 *     here.
 *
 * The cost is that editing the English text orphans its translation, silently
 * - the string falls back to English and nobody is told. That is the right
 * trade for a panel whose English is already written and stable, and it is not
 * left to chance: test_i18n.py fails on any dictionary entry that no longer
 * matches a string in App.jsx or in an API message, so CI catches the orphan
 * on the commit that made it.
 *
 * No dependency. react-i18next and its ecosystem are a lot of machinery for a
 * lookup in an object, and this panel already prefers writing the small thing:
 * its own JSON-RPC rather than the mcp package, minio rather than boto3.
 */

import { useCallback, useEffect, useState } from 'react';

import { vi } from './locales/vi.js';

export const LANGUAGE_STORAGE_KEY = 'spanel-language';
export const LANGUAGE_EVENT = 'spanel-language-change';

export const LANGUAGES = [
  ['en', 'English'],
  ['vi', 'Tiếng Việt'],
];

const DICTIONARIES = { en: {}, vi };

function isKnown(code) {
  return Object.prototype.hasOwnProperty.call(DICTIONARIES, code);
}

function readStoredLanguage() {
  try {
    const stored = localStorage.getItem(LANGUAGE_STORAGE_KEY);
    return isKnown(stored) ? stored : null;
  } catch { return null; }
}

/* What the browser asks for, if we happen to speak it. A Vietnamese hosting
 * customer opening the panel for the first time should not have to find the
 * switch. */
function browserLanguage() {
  try {
    for (const tag of navigator.languages || [navigator.language]) {
      const code = String(tag || '').toLowerCase().split('-')[0];
      if (isKnown(code)) return code;
    }
  } catch { /* no navigator, or it threw: English is a fine answer */ }
  return 'en';
}

/* Module-level on purpose. t() is called from render functions all over the
 * tree, including ones that are not components and cannot hold a hook. The
 * value only ever changes through setLanguage, which fires the event that
 * makes React render again, so nothing can read a stale dictionary and keep
 * it on screen. */
let current = readStoredLanguage() || browserLanguage();

export function getLanguage() {
  return current;
}

export function setLanguage(code) {
  if (!isKnown(code) || code === current) return;
  current = code;
  try { localStorage.setItem(LANGUAGE_STORAGE_KEY, code); } catch {}
  try { document.documentElement.setAttribute('lang', code); } catch {}
  document.dispatchEvent(new CustomEvent(LANGUAGE_EVENT, { detail: code }));
}

/* Translate one string, filling in {placeholders} from values.
 *
 * Anything that is not a non-empty string comes straight back: render code
 * passes null, numbers and elements around freely, and t() is not the place to
 * discover that.
 *
 * The second argument exists because a count sentence cannot be translated in
 * pieces. `{n} website(s)` is one sentence to a translator; as the two
 * fragments JSX splits it into - '' and ' website(s)' - it is untranslatable,
 * since Vietnamese orders and pluralises them differently. Passing the whole
 * sentence with a hole in it keeps the translator's unit the same as the
 * reader's. An unknown placeholder is left standing rather than blanked, so a
 * typo shows up on screen as `{ammount}` instead of disappearing. */
export function t(text, values) {
  if (typeof text !== 'string' || !text) return text;
  const dictionary = DICTIONARIES[current];
  const hit = dictionary && dictionary[text];
  const out = typeof hit === 'string' && hit ? hit : text;
  if (!values) return out;
  return out.replace(/\{(\w+)\}/g, (whole, name) =>
    Object.prototype.hasOwnProperty.call(values, name) ? String(values[name]) : whole);
}

/* Re-render when the language changes, and hand back the switcher.
 *
 * Mirrors useTheme: the component owns nothing, it subscribes. */
export function useLanguage() {
  const [language, setLanguageState] = useState(getLanguage);

  useEffect(() => {
    try { document.documentElement.setAttribute('lang', getLanguage()); } catch {}
    const handler = event => setLanguageState(event.detail);
    document.addEventListener(LANGUAGE_EVENT, handler);
    return () => document.removeEventListener(LANGUAGE_EVENT, handler);
  }, []);

  const changeLanguage = useCallback(code => setLanguage(code), []);
  return [language, changeLanguage];
}

/* How complete a dictionary is, for the check script and for tests. */
export function coverage(code) {
  const dictionary = DICTIONARIES[code] || {};
  return Object.keys(dictionary).length;
}
