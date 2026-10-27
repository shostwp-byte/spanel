import React, { useEffect, useLayoutEffect, useState, useCallback, useRef } from 'react';
import { createRoot } from 'react-dom/client';
import ace from 'ace-builds/src-noconflict/ace';
import 'ace-builds/src-noconflict/ext-language_tools';
import 'ace-builds/src-noconflict/ext-searchbox';
import 'ace-builds/src-noconflict/mode-css';
import 'ace-builds/src-noconflict/mode-html';
import 'ace-builds/src-noconflict/mode-ini';
import 'ace-builds/src-noconflict/mode-javascript';
import 'ace-builds/src-noconflict/mode-json';
import 'ace-builds/src-noconflict/mode-php';
import 'ace-builds/src-noconflict/mode-text';
import 'ace-builds/src-noconflict/mode-yaml';
import 'ace-builds/src-noconflict/theme-textmate';
import 'ace-builds/src-noconflict/theme-tomorrow_night';
import { Sparkles, UserPlus, ArrowRightLeft, Wrench, PenTool as Tool, BarChart, MessageSquare, Key, AlertTriangle, LayoutTemplate, Archive, ArchiveRestore, ArrowLeft, Ban, Bot, Boxes, Check, ChevronDown, Clock, Code2, Copy, Cpu, Database, Dices, ExternalLink, Eye, FileText, FolderOpen, Globe, HardDrive, Heart, Home, Image, KeyRound, Lock, LogIn, LogOut, MemoryStick, Menu, Moon, MoveRight, Network, Pencil, Save, Search, Server, Settings as SettingsIcon, Shield, Sun, Trash2, TerminalIcon, Users, X, RefreshCw, Plus, Download, Upload, Play, Square, RotateCcw, AlertCircle, Activity, BrickWall, Bug, LockKeyhole, PackageOpen, ScrollText, ShieldAlert, CheckCircle, Zap, Bell, Mail, Send, Cloud, Inbox, Forward, ShieldCheck } from 'lucide-react';
import { Terminal } from './components/Terminal';
import { AIAssistant } from './components/AIAssistant.jsx';
import { LANGUAGES, t, useLanguage } from './i18n.js';
import './style.css';
import './brand.css';
import './file-manager.css';
import './theme.css';
// OPanel's layout layer, loaded last so it wins ties: navy sidebar in
// groups, one account menu, one blue button per task.
import './ui.css';
import { formatBytes, roleLabel } from './utils/helpers.js';
import { AppContext, AppProvider, useAppContext, dnsTtlLabel } from './contexts/AppContext.jsx';
import { WebsiteSelect } from './components/WebsiteSelect.jsx';

import { Migration } from './pages/Migration.jsx';
import { Packages } from './pages/Packages.jsx';
import { IpManager } from './pages/IpManager.jsx';
import { AppInstallerPage } from './pages/AppInstaller.jsx';
import { WordPressManagerPage } from './pages/WordPressManager.jsx';
import { ResellerSettingsPage } from './pages/ResellerSettings.jsx';
import { UsersPage } from './pages/Users.jsx';
import { MoveUsersPage } from './pages/MoveUsers.jsx';
import { ChangePasswords } from './pages/ChangePasswords.jsx';
import { SettingsPage } from './pages/Settings.jsx';
import { FirewallPage } from './pages/Firewall.jsx';
import { SecurityPage } from './pages/Security.jsx';
import { UpdatesPage } from './pages/Updates.jsx';
import { ServicesPage } from './pages/Services.jsx';
import { DatabasesPage } from './pages/Databases.jsx';
import { BackupsPage } from './pages/Backups.jsx';
import { CronPage } from './pages/Cron.jsx';
import { PhpConfigPage } from './pages/PhpConfig.jsx';
import { SslPage } from './pages/Ssl.jsx';
import { FilesPage } from './pages/Files.jsx';
import { SftpPage } from './pages/Sftp.jsx';
import { ApplicationsPage } from './pages/Applications.jsx';
import { DnsPage } from './pages/Dns.jsx';
import { MailPage } from './pages/Mail.jsx';
import { WebsitesPage } from './pages/Websites.jsx';
import { ServerTlsPage } from './pages/ServerTls.jsx';
import { BruteForceMonitorPage } from './pages/BruteForceMonitor.jsx';
import { ProcessMonitorPage } from './pages/ProcessMonitor.jsx';
import { NameServersPage } from './pages/NameServers.jsx';
import { MessageAllPage } from './pages/MessageAll.jsx';
import { WafPage } from './pages/Waf.jsx';
import { MalwarePage } from './pages/Malware.jsx';
import { AddonsPage } from './pages/Addons.jsx';
import { MailForwardersPage } from './pages/MailForwarders.jsx';
import { MailAutorespondersPage } from './pages/MailAutoresponders.jsx';
import { McpPage } from './pages/Mcp.jsx';
import MessageTemplates from './pages/MessageTemplates.jsx';
import { CustomHttpdPage } from './pages/CustomHttpd.jsx';
import { CustomBuildPage } from './pages/CustomBuild.jsx';
import { MaintenancePage } from './pages/Maintenance.jsx';
import { ServerStatisticsPage } from './pages/ServerStatistics.jsx';
import { RedirectsPage } from './pages/Redirects.jsx';
import { ApacheHandlersPage } from './pages/ApacheHandlers.jsx';
import { ResourceMonitorPage } from './pages/ResourceMonitor.jsx';
import { MultiServerPage } from './pages/MultiServer.jsx';
import { LicensingPage } from './pages/Licensing.jsx';

















import { passkeySupported, b64urlToBytes, bytesToB64url, decodeCreationOptions, decodeRequestOptions, encodeRegistration, encodeAssertion } from './utils/passkey.js';






import { EmptyState } from './components/EmptyState.jsx';



const API = import.meta.env.VITE_API_URL || '/api';
// Schedules offered by name; anything else is "Custom". A cron expression is
// the one field most people get wrong, so it is picked rather than typed.
const CRON_SCHEDULE_PRESETS = [
  ['* * * * *', 'Every minute'],
  ['*/5 * * * *', 'Every 5 minutes'],
  ['*/15 * * * *', 'Every 15 minutes'],
  ['*/30 * * * *', 'Every 30 minutes'],
  ['0 * * * *', 'Every hour'],
  ['0 */6 * * *', 'Every 6 hours'],
  ['0 0 * * *', 'Every day at 00:00'],
  ['0 2 * * *', 'Every day at 02:00'],
  ['0 0 * * 0', 'Every Sunday at 00:00'],
  ['0 0 1 * *', 'On the 1st of every month'],
];
const BACKUP_SCHEDULE_PRESETS = [
  ['0 2 * * *', 'Every day at 02:00'],
  ['0 3 * * *', 'Every day at 03:00'],
  ['0 */12 * * *', 'Every 12 hours'],
  ['0 2 * * 0', 'Every Sunday at 02:00'],
  ['0 2 1 * *', 'On the 1st of every month at 02:00'],
];
// How a site's app_type is written wherever a person reads it.
const APP_TYPE_LABELS = { wordpress: 'WordPress', php: 'PHP', static: 'Static', application: 'App' };
const HTTP_FLOOD_DEFAULTS = {
  access_limit_requests: 100,
  access_limit_window: 10,
  access_limit_burst: 100,
  connection_limit: 60,
};
const PHP_VERSION_ORDER = ['5.6', '7.4', '8.0', '8.1', '8.2', '8.3', '8.4', '8.5'];
const NGINX_REWRITE_MODES = [
  { value: 'none', label: 'None / static PHP' },
  { value: 'front_controller', label: 'PHP front controller' },
  { value: 'laravel', label: 'Laravel' },
  { value: 'codeigniter', label: 'CodeIgniter' },
  { value: 'seohburl', label: 'SEO HB URL' },
];
function composeWebPorts(plan, wanted) {
  // Which ports the service behind the domain listens on. More than one means
  // the customer has to say which, rather than the panel guessing.
  const name = wanted || plan?.web_service;
  const service = plan?.services?.find(item => item.name === name);
  return service?.container_ports || [];
}

// Pages opened from inside another page instead of the sidebar. They have no
// nav entry of their own, so without this the header falls back to the first
// item and titles the page "Dashboard".
const NAV_PARENT_PAGE = { 'waf-site': 'waf', 'malware-scan': 'malware', 'panel-settings': 'settings' };

// 'waf-site' is reached from the WAF overview rather than the sidebar, but it
// still belongs to Settings so the menu stays open and WAF stays highlighted.
const PAGE_ROUTES = {
  dashboard: '/',
  websites: '/website',
  subdomains: '/subdomains',
  applications: '/applications',
  ssl: '/ssl',
  databases: '/database',
  sftp: '/sftp',
  cron: '/cron',
  all_crons: '/all-crons',
  server_statistics: '/server-statistics',
  site_stats: '/site-stats',
  redirects: '/redirects',
  apache_handlers: '/apache-handlers',
  files: '/filemanager',
  backups: '/backups',
  users: '/users',
  my_users: '/my-users',
  resource_monitor: '/resource-monitor',
  move_users: '/move-users',
  message_templates: '/message-templates',
  change_passwords: '/change-passwords',
  resellers: '/resellers',
  reseller_packages: '/reseller-packages',
  administrators: '/administrators',
  settings: '/settings',
  'panel-settings': '/panel-settings',
  name_servers: '/name-servers',
  message_all: '/message-all',
  security: '/security',
  php: '/php',
  firewall: '/firewall',
  waf: '/waf',
  'waf-site': '/waf-site',
  'malware-scan': '/malware-scan',
  malware: '/malware',
  'access-logs': '/access-logs',
  updates: '/updates',
  services: '/services',
  custom_httpd: '/custom-httpd',
  custombuild: '/custombuild',
  maintenance: '/maintenance',
  server_tls: '/server-tls',
  brute_force: '/brute-force-monitor',
  process_monitor: '/process-monitor',
  addons: '/addons',
  mcp: '/ai-assistants',
  notifications: '/notifications',
  dns: '/dns',
  mail: '/email',
  mail_forwarders: '/mail-forwarders',
  mail_auto: '/mail-autoresponders',
  app_installer: '/app-installer',
  wordpress_manager: '/wordpress-manager',
  reseller_settings: '/reseller-settings',
  ipmanager: '/ipmanager',
  packages: '/packages',
  migration: '/migration',
  multi_server: '/multi-server',
  licensing: '/licensing',
};

const ADMIN_ALLOWED_PAGES = new Set([
  'dashboard',
  'users',
  'my_users',
  'resource_monitor',
  'packages',
  'move_users',
  'message_templates',
  'change_passwords',
  'resellers',
  'reseller_packages',
  'administrators',
  'settings',
  'panel-settings',
  'custom_httpd',
  'dns',
  'ipmanager',
  'name_servers',
  'multi_server',
  'php',
  'server_tls',
  'notifications',
  'custombuild',
  'backups',
  'brute_force',
  'process_monitor',
  'mail',
  'services',
  'migration',
  'message_all',
  'reseller_settings',
  'access-logs',
  'maintenance',
  'all_crons',
  'server_statistics',
  'addons',
  'waf',
  'waf-site',
  'waf-logs',
  'malware',
  'malware-scan',
  'firewall',
  'applications',
  'mcp',
  'updates',
  'licensing',
  'security',
]);

const RESELLER_ALLOWED_PAGES = new Set([
  'dashboard',
  'users',
  'my_users',
  'suspend',
  'resource_monitor',
  'packages',
  'change_passwords',
  'reseller_settings',
  'backups',
  'migration',
  'ipmanager',
  'server_statistics',
  'access-logs',
  'waf',
  'waf-site',
  'waf-logs',
  'applications',
  'mcp',
  'security',
]);

const USER_ALLOWED_PAGES = new Set([
  'dashboard',
  'websites',
  'subdomains',
  'dns',
  'ssl',
  'sftp',
  'databases',
  'redirects',
  'mail',
  'mail_forwarders',
  'mail_auto',
  'applications',
  'wordpress_manager',
  'waf',
  'waf-site',
  'waf-logs',
  'cron',
  'backups',
  'security',
  'mcp',
  'apache_handlers',
  'files',
  'access-logs',
  'site_stats',
]);

function isPageAllowedForRole(targetPage, role) {
  const normalizedPage = targetPage || 'dashboard';
  if (role === 'admin') return ADMIN_ALLOWED_PAGES.has(normalizedPage);
  if (role === 'reseller') return RESELLER_ALLOWED_PAGES.has(normalizedPage);
  return USER_ALLOWED_PAGES.has(normalizedPage);
}

/* ---------------------------------------------------------------
   Theme (light / dark)
   The initial value is applied by the inline script in index.html,
   so React only has to keep it in sync from here on.
--------------------------------------------------------------- */
const THEME_STORAGE_KEY = 'spanel-theme';
const THEME_EVENT = 'spanel-theme-change';

function readStoredTheme() {
  try {
    const stored = localStorage.getItem(THEME_STORAGE_KEY);
    return stored === 'dark' || stored === 'light' ? stored : null;
  } catch { return null; }
}

function systemTheme() {
  try { return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'; }
  catch { return 'light'; }
}

function currentTheme() {
  const attr = document.documentElement.getAttribute('data-theme');
  if (attr === 'dark' || attr === 'light') return attr;
  return readStoredTheme() || systemTheme();
}

function applyTheme(theme) {
  const root = document.documentElement;
  root.setAttribute('data-theme', theme);
  root.style.colorScheme = theme;
  document.dispatchEvent(new CustomEvent(THEME_EVENT, { detail: theme }));
}

/* Subscribe to the active theme without owning it. */
function useThemeName() {
  const [theme, setTheme] = useState(currentTheme);
  useEffect(() => {
    const handler = event => setTheme(event.detail);
    document.addEventListener(THEME_EVENT, handler);
    return () => document.removeEventListener(THEME_EVENT, handler);
  }, []);
  return theme;
}

/* Owns the theme: persists the user's choice, follows the OS until they pick one. */
function useTheme() {
  const [theme, setTheme] = useState(currentTheme);

  useEffect(() => { applyTheme(theme); }, [theme]);

  useEffect(() => {
    let media;
    try { media = window.matchMedia('(prefers-color-scheme: dark)'); } catch { return undefined; }
    const onChange = () => { if (!readStoredTheme()) setTheme(systemTheme()); };
    media.addEventListener('change', onChange);
    return () => media.removeEventListener('change', onChange);
  }, []);

  const toggleTheme = useCallback(() => {
    setTheme(prev => {
      const next = prev === 'dark' ? 'light' : 'dark';
      try { localStorage.setItem(THEME_STORAGE_KEY, next); } catch { }
      return next;
    });
  }, []);

  return [theme, toggleTheme];
}

function ThemeToggle({ theme, onToggle, className = '' }) {
  const isDark = theme === 'dark';
  const label = isDark ? 'Switch to light mode' : 'Switch to dark mode';
  return <button
    type="button"
    className={`theme-toggle ${className}`.trim()}
    onClick={onToggle}
    title={label}
    aria-label={label}
    aria-pressed={isDark}
  >{isDark ? <Sun size={16} /> : <Moon size={16} />}</button>;
}

// Chrome's password manager writes the saved panel username into text fields
// that are not login fields at all - the website search opened as "admin",
// and so did the passkey name beside a password box - whatever autocomplete
// says. It never fills a read-only field, so this one stays read-only until a
// pointer or the keyboard actually lands on it. Unlocking on pointerdown,
// before focus, keeps a phone's keyboard coming up on the first tap.


function LanguageToggle({ language, onChange, className = '' }) {
  /* A button, not a select. There are two languages and English is the one the
   * panel is written in, so the only thing anybody wants is to flip to the
   * other - a dropdown asks them to open a list to choose between two items.
   *
   * It shows the language you get by pressing it, which is how the theme
   * toggle next to it behaves: in dark mode that button shows a sun.
   *
   * Two letters and no icon. EN and VI already are the picture - a globe or
   * a speech bubble beside them says nothing the letters do not, and any
   * flag would name a country instead of a language. */
  const next = language === 'vi' ? 'en' : 'vi';
  const label = next === 'vi' ? 'Chuyển sang Tiếng Việt' : 'Switch to English';
  return <button
    type="button"
    className={`language-toggle ${className}`.trim()}
    onClick={() => onChange(next)}
    title={label}
    aria-label={label}
  >
    {next === 'vi' ? 'VI' : 'EN'}
  </button>;
}

function WordPressIcon({ size = 14 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" aria-hidden="true" focusable="false" className="lucide">
      <circle cx="12" cy="12" r="9" fill="none" stroke="currentColor" strokeWidth="2" />
      <text x="12" y="16" textAnchor="middle" fontSize="11" fontWeight="700" fontFamily="Georgia, serif" fill="currentColor">W</text>
    </svg>
  );
}
const EDITOR_FONT_FAMILY = "Consolas, 'SFMono-Regular', 'Liberation Mono', Menlo, monospace";
const WAF_ACCESS_LOG_DEFAULTS = {
  websiteId: '',
  verdict: 'all',
  query: '',
  limit: 50,
  refresh: 5,
};
const ROUTE_PAGES = new Map([
  ...Object.entries(PAGE_ROUTES).map(([pageName, path]) => [path, pageName]),
  ['/dashboard', 'dashboard'],
  ['/websites', 'websites'],
  ['/databases', 'databases'],
  ['/sftp-accounts', 'sftp'],
  ['/files', 'files'],
  ['/file-manager', 'files'],
  ['/website', 'websites'],
  // API tokens moved into Panel settings. Old links still land somewhere real.
  ['/api-token', 'panel-settings'],
  ['/api-tokens', 'panel-settings'],
]);

function pageFromPathname(pathname) {
  const normalized = `/${String(pathname || '').replace(/^\/+|\/+$/g, '')}`.toLowerCase();
  return ROUTE_PAGES.get(normalized) || 'dashboard';
}

function routeForPage(pageName) {
  return PAGE_ROUTES[pageName] || PAGE_ROUTES.dashboard;
}

function sortPhpVersions(versions = []) {
  return [...versions].sort((a, b) => {
    const ai = PHP_VERSION_ORDER.indexOf(a);
    const bi = PHP_VERSION_ORDER.indexOf(b);
    if (ai !== -1 || bi !== -1) return (ai === -1 ? 999 : ai) - (bi === -1 ? 999 : bi);
    return String(a).localeCompare(String(b), undefined, { numeric: true });
  });
}

function normalizeHttpFloodConfig(config = {}) {
  let value = config;
  if (typeof value === 'string') {
    try { value = value.trim() ? JSON.parse(value) : {}; } catch { value = {}; }
  }
  if (!value || typeof value !== 'object') value = {};
  return Object.fromEntries(Object.entries(HTTP_FLOOD_DEFAULTS).map(([key, fallback]) => {
    const number = value[key] === '' ? NaN : Number(value[key]);
    return [key, Number.isFinite(number) ? number : fallback];
  }));
}

// The one website mode served by proxying to an installed application.
const PROXIED_APP_TYPES = ['application'];
const EMPTY_SITE_APP_DRAFT = {
  name: 'app',
  kind: 'node',
  port: '',
  memory_limit_mb: '',
  compose_source: '',
  web_service: '',
  start_kind: 'npm',
  start_arg: 'start',
  node_major: '22',
  image: '',
  container_port: '3000',
  cpu_limit: '1',
  env: '',
};
const SITE_APP_KIND_LABELS = { node: 'Node.js', docker: 'Container', compose: 'Compose' };
// DNS Manager addon, laid out like OPanel's: the record types, the TTLs the
// form offers, and an example value for each type.
const DNS_TYPES = ['A', 'AAAA', 'CNAME', 'MX', 'TXT', 'NS', 'SRV', 'CAA'];
const DNS_TTLS = [60, 300, 900, 1800, 3600, 14400, 43200, 86400];
const DNS_PLACEHOLDERS = {
  A: '203.0.113.10', AAAA: '2001:db8::10', CNAME: 'target.example.com', MX: 'mail.example.com',
  TXT: 'v=spf1 mx -all', NS: 'ns1.example.net', SRV: '5 5060 sip.example.com', CAA: '0 issue letsencrypt.org',
};
const emptyDnsRecord = () => ({ type: 'A', name: '', value: '', priority: '', ttl: '' });
// The SPF part of the smarthosts customers use most.
const MAIL_SPF_INCLUDES = ['include:spf.smtp2go.com', 'include:mailgun.org', 'include:sendgrid.net', 'include:amazonses.com',
  'include:spf.brevo.com', 'include:spf.mandrillapp.com', 'include:_spf.google.com', 'include:spf.protection.outlook.com'];


const SITE_APP_KINDS = [
  ['node', 'Node.js', 'spanel installs dependencies and keeps the process running under systemd.'],
  ['docker', 'Container', 'spanel pulls the image and runs it, published on loopback only.'],
  ['compose', 'Docker Compose', 'Paste your project\u2019s docker-compose.yml. spanel checks it and runs a file it generates from what it accepted.'],
];
const WEBSITE_MODES = [
  ['wordpress', 'WordPress'],
  ['php', 'PHP'],
  ['static', 'Static'],
  ['application', 'Application'],
];

function isProxiedAppType(appType) {
  return PROXIED_APP_TYPES.includes(appType);
}

function websiteConfigForm(site = {}) {
  const appType = site.app_type || 'wordpress';
  return {
    app_type: appType,
    php_version: site.php_version || '8.4',
    app_id: site.app_id ? String(site.app_id) : '',
    nginx_rewrite_mode: appType === 'wordpress'
      ? 'front_controller'
      : appType === 'static' || isProxiedAppType(appType)
        ? 'none'
        : site.nginx_rewrite_mode || 'none',
  };
}

function formatAccessLogTime(value = '') {
  if (!value) return '';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString(undefined, {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  });
}

function accessLogBadgeClass(verdict = '') {
  if (verdict === 'allow') return 'access-log-verdict allow';
  if (verdict === 'error') return 'access-log-verdict error';
  return 'access-log-verdict block';
}

function accessLogVerdictLabel(verdict = '') {
  if (verdict === 'allow') return 'Allow';
  if (verdict === 'error') return 'Error';
  return 'Block';
}

function accessLogCountryLabel(item = {}) {
  const country = item.country || '';
  const code = item.country_code || '';
  if (country && code && country !== code) return `${country} (${code})`;
  return country || code || '-';
}

function csvCell(value) {
  const text = String(value ?? '');
  return `"${text.replace(/"/g, '""')}"`;
}



function editorParamsFromLocation() {
  const params = new URLSearchParams(window.location.search);
  if (params.get('view') !== 'editor') return null;
  const websiteId = params.get('website_id');
  const appId = params.get('app_id');
  const path = params.get('path') || 'public_html/index.html';
  if (!websiteId && !appId) return null;
  return { websiteId: websiteId ? String(websiteId) : '', appId: appId ? String(appId) : '', path };
}

function aceModeName(mode) {
  if (mode === 'PHP') return 'php';
  if (mode === 'JavaScript') return 'javascript';
  if (mode === 'CSS') return 'css';
  if (mode === 'HTML') return 'html';
  if (mode === 'JSON') return 'json';
  if (mode === 'YAML') return 'yaml';
  if (mode === 'Config') return 'ini'; // .env, .htaccess, .ini, .conf -> Ace's ini mode
  return 'text';
}

// --- File permissions (chmod) ------------------------------------------------
// The listing reports POSIX modes as octal strings ("644", and "2755" or the
// like when a folder carries a special bit), so the dialog works on the same
// representation.
// Monday first, matching datetime.weekday() on the server.
const WEEKDAY_LABELS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
const MALWARE_SCHEDULES_DEFAULT = {
  websites: { enabled: false, weekday: 6, hour: 3, weekday_label: '', next_run_at: '', last_run_at: '', last_status: '' },
  server: { enabled: false, weekday: 6, hour: 4, weekday_label: '', next_run_at: '', last_run_at: '', last_status: '' },
};
const MALWARE_SCHEDULE_LABELS = {
  websites: 'All websites',
  server: 'Whole server',
};
// The malware scan schedule is stored and sent to the API as UTC weekday/hour
// (matching datetime.weekday() on the server) - nobody running a Vietnamese
// host should have to do +7 math to pick a quiet hour. These convert only
// for display/input; malwareSchedulesForm itself always stays in UTC.
const VN_UTC_OFFSET_HOURS = 7;
function utcScheduleToVn(weekday, hour) {
  const vnHour = (hour + VN_UTC_OFFSET_HOURS) % 24;
  const dayShift = hour + VN_UTC_OFFSET_HOURS >= 24 ? 1 : 0;
  return { weekday: (weekday + dayShift) % 7, hour: vnHour };
}
function vnScheduleToUtc(weekday, hour) {
  const utcHour = (hour - VN_UTC_OFFSET_HOURS + 24) % 24;
  const dayShift = hour - VN_UTC_OFFSET_HOURS < 0 ? -1 : 0;
  return { weekday: (weekday + dayShift + 7) % 7, hour: utcHour };
}

const PERMISSION_CLASSES = [
  { key: 'owner', label: 'Owner' },
  { key: 'group', label: 'Group' },
  { key: 'other', label: 'Public' },
];
const PERMISSION_BITS = [
  { key: 'read', label: 'Read', value: 4 },
  { key: 'write', label: 'Write', value: 2 },
  { key: 'execute', label: 'Execute', value: 1 },
];
const PERMISSION_PRESETS = {
  file: [['644', 'Default'], ['755', 'Executable'], ['600', 'Private'], ['444', 'Read-only']],
  dir: [['755', 'Default'], ['750', 'Group read'], ['775', 'Group write'], ['700', 'Private']],
};

function normalizeOctalMode(mode) {
  const value = String(mode ?? '').trim();
  return /^[0-7]{3,4}$/.test(value) ? value : '';
}

function octalToPermissionBits(mode) {
  const padded = (normalizeOctalMode(mode) || '0644').padStart(4, '0');
  return {
    special: Number(padded[0]),
    owner: Number(padded[1]),
    group: Number(padded[2]),
    other: Number(padded[3]),
  };
}

function permissionBitsToOctal({ special, owner, group, other }) {
  const body = `${owner}${group}${other}`;
  return special ? `${special}${body}` : body;
}

function permissionSymbols(mode) {
  const bits = octalToPermissionBits(mode);
  return PERMISSION_CLASSES
    .map(({ key }) => PERMISSION_BITS.map(bit => (bits[key] & bit.value ? bit.key[0] : '-')).join(''))
    .join('');
}

function formatApiError(detail, fallback = 'Request failed.') {
  /* Backend messages pass through t() on their way to the screen.
   *
   * The server does not know what language the reader wants, and teaching it
   * would mean a header, a dependency and a second dictionary. Translating on
   * display costs a lookup: a sentence the dictionary knows is shown in
   * Vietnamese, and anything it does not know is shown in the English the
   * server wrote - which is the same fallback the rest of the interface uses.
   */
  if (detail === null || detail === undefined || detail === '') return t(fallback);
  if (typeof detail === 'string') {
    const cleaned = detail.replace(/^Value error,\s*/i, '');
    // "Line 3: <a sentence the dictionary knows>" - the DNS template's check.
    const line = cleaned.match(/^Line (\d+): (.+)$/s);
    if (line) return t('Line {n}: {reason}', { n: line[1], reason: t(line[2]) });
    return cleaned ? t(cleaned) : t(fallback);
  }
  if (typeof detail === 'number' || typeof detail === 'boolean') return String(detail);

  if (Array.isArray(detail)) {
    const messages = detail.map(item => formatApiErrorItem(item)).filter(Boolean);
    return messages.length ? messages.join('\n') : fallback;
  }

  if (typeof detail === 'object') {
    if (detail.detail !== undefined) return formatApiError(detail.detail, fallback);
    if (detail.message !== undefined) return formatApiError(detail.message, fallback);
    if (detail.msg !== undefined) return formatApiError(detail.msg, fallback);
    try { return JSON.stringify(detail); } catch { return fallback; }
  }

  return fallback;
}

function formatApiErrorItem(item) {
  if (!item || typeof item !== 'object') return formatApiError(item, '');
  const message = formatApiError(item.msg ?? item.message ?? item.detail, 'Invalid value');
  const loc = Array.isArray(item.loc)
    ? item.loc.filter(part => part !== 'body' && part !== 'query' && part !== 'path').join('.')
    : '';
  return loc ? `${loc}: ${message}` : message;
}

function NotificationToast({ type, message, onClose }) {
  if (!message) return null;
  const isError = type === 'error';
  const Icon = isError ? AlertCircle : Check;
  return <div className={`app-toast ${isError ? 'app-toast-error' : 'app-toast-success'}`} role={isError ? 'alert' : 'status'} aria-live={isError ? 'assertive' : 'polite'}>
    <Icon className="app-toast-icon" size={18} />
    <div className="app-toast-content">
      <strong>{isError ? t('Action failed') : t('Completed')}</strong>
      <span>{message}</span>
    </div>
    <button className="app-toast-close" onClick={onClose} aria-label={t('Dismiss notification')} title={t('Dismiss notification')}><X size={16} /></button>
  </div>;
}

const ACE_THEMES = { light: 'ace/theme/textmate', dark: 'ace/theme/tomorrow_night' };
const aceThemeFor = theme => ACE_THEMES[theme] || ACE_THEMES.light;

function CodeEditor({ value, mode, disabled, onChange, onCursorChange }) {
  const hostRef = useRef(null);
  const editorRef = useRef(null);
  const suppressChangeRef = useRef(false);
  const onChangeRef = useRef(onChange);
  const onCursorChangeRef = useRef(onCursorChange);
  const themeName = useThemeName();
  const themeRef = useRef(themeName);

  useEffect(() => { onChangeRef.current = onChange; }, [onChange]);
  useEffect(() => { onCursorChangeRef.current = onCursorChange; }, [onCursorChange]);
  useEffect(() => {
    themeRef.current = themeName;
    editorRef.current?.setTheme(aceThemeFor(themeName));
  }, [themeName]);

  useEffect(() => {
    if (!hostRef.current) return undefined;
    const editor = ace.edit(hostRef.current, {
      mode: `ace/mode/${aceModeName(mode)}`,
      theme: aceThemeFor(themeRef.current),
      value: value || '',
      readOnly: !!disabled,
      showPrintMargin: false,
      highlightActiveLine: true,
      fontSize: 13,
      tabSize: 2,
      useSoftTabs: true,
      wrap: false,
      selectionStyle: 'text',
    });

    editor.setOptions({
      enableBasicAutocompletion: true,
      enableLiveAutocompletion: true,
      enableMatchBrackets: true,
      enableSnippets: false,
      fontFamily: EDITOR_FONT_FAMILY,
    });
    editor.session.setUseWorker(false);
    editor.session.setNewLineMode('unix');

    let destroyed = false;
    const reportCursor = () => {
      if (destroyed || !editorRef.current || !onCursorChangeRef.current) return;
      const pos = editorRef.current.getCursorPosition();
      onCursorChangeRef.current({ line: pos.row + 1, column: pos.column + 1 });
    };
    const handleChange = () => {
      if (destroyed || !editorRef.current) return;
      if (!suppressChangeRef.current) {
        if (onChangeRef.current) onChangeRef.current(editorRef.current.getValue());
      }
      // Only report cursor on explicit cursor moves, not on every content change
    };

    editor.session.on('change', handleChange);
    editor.selection.on('changeCursor', reportCursor);
    editorRef.current = editor;
    reportCursor();

    return () => {
      destroyed = true;
      editor.session.off('change', handleChange);
      editor.selection.off('changeCursor', reportCursor);
      editor.destroy();
      editorRef.current = null;
      if (hostRef.current) hostRef.current.textContent = '';
    };
  }, []);

  useEffect(() => {
    const editor = editorRef.current;
    if (!editor) return;
    const nextValue = value || '';
    if (nextValue === editor.getValue()) return;
    const cursor = editor.getCursorPosition();
    suppressChangeRef.current = true;
    editor.setValue(nextValue, -1);
    const newRow = Math.max(0, Math.min(cursor.row, editor.session.getLength() - 1));
    editor.moveCursorTo(newRow, cursor.column);
    suppressChangeRef.current = false;
  }, [value]);

  useEffect(() => {
    const editor = editorRef.current;
    if (!editor) return;
    editor.session.setMode(`ace/mode/${aceModeName(mode)}`);
  }, [mode]);

  useEffect(() => {
    const editor = editorRef.current;
    if (!editor) return;
    editor.setReadOnly(!!disabled);
  }, [disabled]);

  return <div className="code-editor-host" ref={hostRef}></div>;
}

function AppContent() {
  const baseContext = useAppContext();
  const { loading, setLoading, error, setError, notice, setNotice, currentUser, setCurrentUser, packages, setPackages, users, setUsers, appVersion, setAppVersion, databases, setDatabases, dbSearch, setDbSearch, dbCreateOpen, setDbCreateOpen, phpVersion, setPhpVersion, phpVersions, setPhpVersions, fileAppId, setFileAppId, filePath, setFilePath, fileListPath, setFileListPath, websites, setWebsites, selectedWebsiteId, setSelectedWebsiteId, siteApps, setSiteApps, panelSettings, setPanelSettings, copiedField, setCopiedField } = baseContext;
  // Auth is now cookie-based (HttpOnly spanel_session). The SPA does not see
  // the JWT at all. We track only whether the user is authenticated in memory.
  const [isAuthenticated, setIsAuthenticated] = useState(false);
  const [theme, toggleTheme] = useTheme();
  // Subscribed, not owned - the same shape as the theme. Reading it here
  // is what makes the whole tree render again when the language changes,
  // because every t() call is evaluated during render.
  const [language, changeLanguage] = useLanguage();

  const [bootstrapping, setBootstrapping] = useState(true);
  const [standaloneEditor] = useState(() => editorParamsFromLocation());
  const [username, setUsername] = useState('admin');
  const [password, setPassword] = useState('');
  const [otpCode, setOtpCode] = useState('');
  // The passkey challenge the server handed back with the password check, and
  // whether the customer has asked for the authenticator app instead.
  const [passkeyPrompt, setPasskeyPrompt] = useState(null);
  // The step-up for adding a passkey. A masked field rather than prompt(),
  // which shows a password in clear text in a browser dialog.
  const [needsTwoFactor, setNeedsTwoFactor] = useState(false);
  const [rememberMe, setRememberMe] = useState(false);
  const VIEW_LEVEL_KEY = 'spanel-view-level';
  const [viewLevel, setViewLevel] = useState(() => {
    try { return localStorage.getItem(VIEW_LEVEL_KEY) || null; } catch { return null; }
  });

  useEffect(() => {
    try {
      if (viewLevel) localStorage.setItem(VIEW_LEVEL_KEY, viewLevel);
      else localStorage.removeItem(VIEW_LEVEL_KEY);
    } catch { }
  }, [viewLevel]);

  const rawRole = currentUser?.role || 'user';
  const actualRole = rawRole === 'end_user' ? 'user' : rawRole;
  const canSwitchToAdmin = actualRole === 'admin';
  const canSwitchToReseller = actualRole === 'admin' || actualRole === 'reseller';
  const validViewLevel = (
    actualRole === 'admin' ? ['admin', 'reseller', 'user'].includes(viewLevel) :
    actualRole === 'reseller' ? ['reseller', 'user'].includes(viewLevel) :
    false
  ) ? viewLevel : null;
  const effectiveRole = validViewLevel || actualRole;
  const isAdmin = effectiveRole === 'admin';
  const isReseller = effectiveRole === 'reseller';
  const isUser = effectiveRole === 'user' || effectiveRole === 'end_user';
  const [page, setPage] = useState(() => pageFromPathname(window.location.pathname));
  const [domain, setDomain] = useState('');
  const [adminEmail, setAdminEmail] = useState('');
  const [wpAdminUser, setWpAdminUser] = useState('admin');
  const [wpAdminPassword, setWpAdminPassword] = useState('');
  const [siteType, setSiteType] = useState('wordpress');
  const [installWordPress, setInstallWordPress] = useState(true);
  /* The create form starts folded away. Making a website is something an
     operator does now and then; looking one up is what they came for, and
     the form was four hundred pixels of it between them and the list. A
     panel with no sites yet is the exception - there the form is the only
     thing to do, so it opens itself. */
  const [createFormOpen, setCreateFormOpen] = useState(false);
  const [createSslMode, setCreateSslMode] = useState('letsencrypt');
  const [createSslToken, setCreateSslToken] = useState('');
  const [wordpressInstaller, setWordpressInstaller] = useState(null);
  const [aliasDrafts, setAliasDrafts] = useState({});
  const [aliasModes, setAliasModes] = useState({});


  const [resourceUsage, setResourceUsage] = useState(null);
  const [dashSummary, setDashSummary] = useState(null);
  // Notifications addon: my own settings, and (administrators) the server's.
  const [notifyMe, setNotifyMe] = useState(null);
  const [notifySettings, setNotifySettings] = useState(null);
  const systemNotifications = React.useMemo(() => {
    const attention = [];
    const sum = dashSummary || {};
    const tenants = sum.tenants || {};
    const sites = sum.websites || {
      total: websites.length,
      suspended: websites.filter(site => site.status === 'suspended').length,
      waf_on: websites.filter(site => site.waf_enabled).length,
    };
    const ssl = sum.ssl || {
      total: websites.length,
      secured: websites.filter(site => site.ssl_enabled).length,
      unsecured: websites.filter(site => !site.ssl_enabled).map(site => site.domain).slice(0, 5),
      unsecured_count: websites.filter(site => !site.ssl_enabled).length,
    };
    const myWebsites = currentUser ? websites.filter(site => site.owner_id === currentUser.id) : websites;
    const ownSites = sum.own_websites || {
      total: myWebsites.length,
      suspended: myWebsites.filter(site => site.status === 'suspended').length,
      waf_on: myWebsites.filter(site => site.waf_enabled).length,
    };
    const ownSsl = sum.own_ssl || {
      total: myWebsites.length,
      secured: myWebsites.filter(site => site.ssl_enabled).length,
      unsecured: myWebsites.filter(site => !site.ssl_enabled).map(site => site.domain).slice(0, 5),
      unsecured_count: myWebsites.filter(site => !site.ssl_enabled).length,
    };

    const flag = (tone, category, text, target, action = t('Open'), icon = AlertCircle) =>
      attention.push({ tone, category, text, target, action, icon });

    // 1. ADMIN NOTIFICATIONS: Server, Security, Infrastructure, Tenant Alerts, Backups & Updates
    if (isAdmin) {
      if (sum.services?.stopped?.length) {
        flag('bad', t('System'), t('Stopped: {names}', { names: sum.services.stopped.join(', ') }), 'services', t('Services'), Server);
      }
      if (sum.firewall?.enabled === false) {
        flag('bad', t('Security'), t('The firewall is off.'), 'firewall', t('Firewall'), BrickWall);
      }
      if (sum.waf?.engine === 'off') {
        flag('warn', t('Security'), t('The WAF engine is not installed.'), 'waf', t('Configure'), ShieldAlert);
      }
      if (sum.malware?.last_scan?.infected) {
        flag('bad', t('Security'), t('The last malware scan found {n} threat(s).', { n: sum.malware.last_scan.infected }), 'malware', t('View'), Bug);
      }
      if (sum.malware?.addon && !sum.malware.installed) {
        flag('warn', t('Security'), t('The malware scanner is not installed.'), 'malware', t('Install'), Bug);
      }
      if (tenants.under_attack_count > 0) {
        flag('bad', t('Security'), t('{n} user account(s) under attack protection: {users}', {
          n: tenants.under_attack_count,
          users: (tenants.under_attack_users || []).join(', '),
        }), 'resource_monitor', t('Monitor'), ShieldAlert);
      }
      const diskPct = Number(resourceUsage?.disk?.percent || 0);
      if (diskPct >= 85) {
        flag(diskPct >= 95 ? 'bad' : 'warn', t('Resource'), t('Server disk usage is at {pct}%.', { pct: Math.round(diskPct) }), 'server_statistics', t('Inspect'), HardDrive);
      }
      const memPct = Number(resourceUsage?.memory?.percent || 0);
      if (memPct >= 85) {
        flag(memPct >= 95 ? 'bad' : 'warn', t('Resource'), t('Server RAM usage is at {pct}%.', { pct: Math.round(memPct) }), 'process_monitor', t('Processes'), MemoryStick);
      }
      if (tenants.locked_databases_count > 0) {
        flag('bad', t('Resource'), t('{n} MySQL database(s) write-locked due to quota.', { n: tenants.locked_databases_count }), 'resource_monitor', t('Manage'), Database);
      }
      if (tenants.over_quota_count > 0) {
        flag('warn', t('Resource'), t('{n} user account(s) near or over disk quota: {users}', {
          n: tenants.over_quota_count,
          users: (tenants.over_quota_users || []).join(', '),
        }), 'resource_monitor', t('Inspect'), HardDrive);
      }
      if (tenants.suspended_users > 0) {
        flag('info', t('Customer'), t('{n} user account(s) currently suspended.', { n: tenants.suspended_users }), 'users', t('Users'), Users);
      }
      if (sum.backups?.failed) {
        flag('bad', t('Backup'), t('{n} scheduled backup(s) failed on their last run.', { n: sum.backups.failed }), 'backups', t('Backups'), Archive);
      }
      if (sum.backups && !sum.backups.schedules) {
        flag('warn', t('Backup'), t('No scheduled backup is set up.'), 'backups', t('Set up'), Archive);
      }
      if (sum.updates?.update_available) {
        flag('info', t('Update'), t('Panel update {version} is available.', { version: sum.updates.latest_version }), 'updates', t('Update'), RefreshCw);
      }
      if (sum.php_updates?.updates_available_count > 0) {
        const verList = (sum.php_updates.updatable_versions || []).join(', ');
        flag(
          'warn',
          t('Security'),
          t('Phát hiện {n} bản vá bảo mật PHP mới ({versions}) chống lỗ hổng RCE / PHP2SHELL.', {
            n: sum.php_updates.updates_available_count,
            versions: verList || 'PHP',
          }),
          'updates',
          t('Cập nhật'),
          ShieldAlert
        );
      }
      if (sum.php_updates?.unacknowledged_count > 0) {
        const latestRec = (sum.php_updates.unacknowledged_updates || [])[0];
        flag(
          'info',
          t('Smart Guard'),
          latestRec?.summary || t('Smart Guard vừa tự động cập nhật bản vá bảo mật PHP an toàn (Zero-Downtime).'),
          'updates',
          t('Xem chi tiết'),
          RefreshCw
        );
      }
      return attention;
    }

    // 2. RESELLER NOTIFICATIONS: Managed Customers, Quotas, Reseller Limits & Account Security
    if (isReseller) {
      if (tenants.under_attack_count > 0) {
        flag('bad', t('Security'), t('{n} customer account(s) under attack protection: {users}', {
          n: tenants.under_attack_count,
          users: (tenants.under_attack_users || []).join(', '),
        }), 'resource_monitor', t('Monitor'), ShieldAlert);
      }
      if (tenants.locked_databases_count > 0) {
        flag('bad', t('Resource'), t('{n} customer database(s) write-locked due to quota.', { n: tenants.locked_databases_count }), 'resource_monitor', t('Manage'), Database);
      }
      if (tenants.over_quota_count > 0) {
        flag('warn', t('Quota'), t('{n} customer account(s) near or over storage quota.', { n: tenants.over_quota_count }), 'resource_monitor', t('Inspect'), HardDrive);
      }
      if (tenants.reseller_user_limit > 0 && tenants.managed_users_total >= Math.ceil(tenants.reseller_user_limit * 0.85)) {
        flag(
          tenants.managed_users_total >= tenants.reseller_user_limit ? 'bad' : 'warn',
          t('Quota'),
          t('Reseller account limit: {used} / {limit} customers created.', {
            used: tenants.managed_users_total || 0,
            limit: tenants.reseller_user_limit,
          }),
          'users',
          t('Users'),
          Users
        );
      }
      if (tenants.suspended_users > 0) {
        flag('warn', t('Customer'), t('{n} customer account(s) suspended.', { n: tenants.suspended_users }), 'users', t('Users'), Users);
      }
      if (sites.suspended > 0) {
        flag('warn', t('Customer'), t('{n} customer website(s) suspended.', { n: sites.suspended }), 'users', t('Manage'), Globe);
      }
      if (ssl.unsecured_count > 0) {
        flag('info', t('SSL'), t('{n} customer website(s) without SSL: {domains}', {
          n: ssl.unsecured_count,
          domains: ssl.unsecured.join(', ') + (ssl.unsecured_count > ssl.unsecured.length ? ', ...' : ''),
        }), 'server_statistics', t('Details'), Lock);
      }
      if (currentUser && !currentUser.totp_enabled) {
        flag('info', t('Account'), t('Two-factor sign-in is off for your reseller account.'), 'security', t('Turn on'), LockKeyhole);
      }
      return attention;
    }

    // 3. END USER NOTIFICATIONS: Strictly User's Own Websites, SSL, Databases, Quota & Account Security
    if (tenants.own_under_attack) {
      flag('bad', t('Security'), t('Your account is currently under automated attack protection.'), 'waf', t('WAF'), ShieldAlert);
    }
    if (tenants.own_locked_databases_count > 0) {
      flag('bad', t('Quota'), t('{n} of your MySQL database(s) are write-locked due to quota.', { n: tenants.own_locked_databases_count }), 'databases', t('Databases'), Database);
    }
    if (currentUser) {
      const limitBytes = Number(currentUser.storage_limit_bytes ?? (Number(currentUser.storage_limit_mb || 0) * 1024 * 1024));
      const usedBytes = Number(currentUser.storage_used_bytes || 0);
      if (limitBytes > 0 && usedBytes > 0) {
        const storagePct = (usedBytes / limitBytes) * 100;
        if (storagePct >= 85) {
          flag(
            storagePct >= 100 ? 'bad' : 'warn',
            t('Quota'),
            t('Your disk storage is at {pct}% ({used} / {limit}).', {
              pct: Math.round(storagePct),
              used: formatBytes(usedBytes),
              limit: formatBytes(limitBytes),
            }),
            'files',
            t('Clean up'),
            HardDrive
          );
        }
      }
      const siteLimit = Number(currentUser.website_limit || 0);
      if (siteLimit > 0 && ownSites.total >= siteLimit) {
        flag('info', t('Quota'), t('You have reached your website limit ({used} / {limit}).', { used: ownSites.total, limit: siteLimit }), 'websites', t('Websites'), Globe);
      }
    }
    if (ownSsl.unsecured_count > 0) {
      flag('warn', t('SSL'), t('{n} website(s) without SSL: {domains}', {
        n: ownSsl.unsecured_count,
        domains: ownSsl.unsecured.join(', ') + (ownSsl.unsecured_count > ownSsl.unsecured.length ? ', ...' : ''),
      }), 'ssl', t('Install SSL'), Lock);
    }
    if (ownSites.total > 0 && ownSites.waf_on < ownSites.total) {
      flag('info', t('Security'), t('{n} website(s) have WAF protection turned off.', { n: ownSites.total - ownSites.waf_on }), 'waf', t('Enable WAF'), Shield);
    }
    if (ownSites.suspended > 0) {
      flag('bad', t('Website'), t('{n} of your website(s) are suspended.', { n: ownSites.suspended }), 'websites', t('Websites'), Globe);
    }
    if (currentUser && !currentUser.totp_enabled) {
      flag('info', t('Account'), t('Two-factor sign-in is off for your account.'), 'security', t('Turn on'), LockKeyhole);
    }
    return attention;
  }, [isAdmin, isReseller, dashSummary, resourceUsage, websites, currentUser, t]);

  const [notifyLog, setNotifyLog] = useState([]);
  const [telegramLink, setTelegramLink] = useState(null);
  const [smtpForm, setSmtpForm] = useState({ host: '', port: 587, security: 'starttls', username: '', password: '', from_email: '', from_name: 'spanel' });
  const [botTokenInput, setBotTokenInput] = useState('');
  const [adminChatInput, setAdminChatInput] = useState('');
  const [telegramChats, setTelegramChats] = useState(null);
  const [myChatInput, setMyChatInput] = useState('');
  const [notifyLimits, setNotifyLimits] = useState({ disk_percent: 90, ssl_days: 7, language: 'vi' });
  // An old /api-tokens link opens the tab the tokens now live on.
  const [panelSettingsTab, setPanelSettingsTab] = useState(() => (/^\/api-tokens?\/?$/i.test(window.location.pathname) ? 'api' : 'general'));
  // The restore catalogue: local archives and whatever is in each S3 bucket,
  // in one list, because "what can I restore" is not answered by this disk alone.
  // Restore, DirectAdmin's way: a source ('local', 'target:<id>' or
  // 'remote'), another server's details when that is the source, what the
  // source holds, which users are ticked, and the job restoring them.
  const [restoreRemote, setRestoreRemote] = useState({ protocol: 'sftp', host: '', port: '', username: '', password: '', path: '/' });
  const [migrateMethod, setMigrateMethod] = useState('url');

  const [migrateForm, setMigrateForm] = useState({
    panel_type: 'directadmin',
    method: 'url',
    source_url: '',
    ftp_host: '',
    ftp_user: '',
    ftp_pass: '',
    ftp_port: 21,
    ftp_path: '',
    import_websites: true,
    import_databases: true,
    import_emails: true
  });

  const [migratePanelType, setMigratePanelType] = useState('directadmin');
  const [daImportJob, setDaImportJob] = useState(null);
  const daFileInputRef = React.useRef(null);
  const [wildcardToken, setWildcardToken] = useState('');
  const [cfZone, setCfZone] = useState({ zone: null, has_token: false });
  const [sharedSource, setSharedSource] = useState('');
  // Shown once, right after creation. A password the user typed is never
  // echoed back by the API, so this only ever holds a generated one.
  // The main account's own SFTP password, shown once after it is set.
  // Optional features. Until this has loaded nothing addon-owned is offered, so
  // a slow first request cannot flash a section that turns out not to be there.
  const [addons, setAddons] = useState({ items: [], can_manage: false, loaded: false });
  // Demo mode addon: the accounts the login page offers, and the form that picks them.
  const [demoAccess, setDemoAccess] = useState({ enabled: false, accounts: [] });
  const [demoSettings, setDemoSettings] = useState(null);
  // DNS Manager addon: the zones, one zone's records, the settings.
  // Email: domains, mailboxes and forwarders, and for administrators the
  // relays, the spam filter and the mail server.
  const [catchAllDraft, setCatchAllDraft] = useState({});

  const [websiteList, setWebsiteList] = useState([]);

  const [websiteSearch, setWebsiteSearch] = useState('');
  const [websiteSearching, setWebsiteSearching] = useState(false);


  // DNS, Mail, WAF states
  const [dnsPage, setDnsPage] = useState(1);
  const [mailTab, setMailTab] = useState('mailboxes');
  const [mailPage, setMailPage] = useState(1);
  const [mailFilter, setMailFilter] = useState({ domain_id: '' });
  const [wafAccessLogFilters, setWafAccessLogFilters] = useState({ websiteId: '', q: '', page: 1, action: '', refresh: 0 });
  const [selectedWafWebsiteId, setSelectedWafWebsiteId] = useState('');

  // File Manager states that were missing
  const [files, setFiles] = useState([]);
  const [fileJobs, setFileJobs] = useState([]);
  const [fileUploadDir, setFileUploadDir] = useState('public_html');
  const [selectedFilePaths, setSelectedFilePaths] = useState([]);
  const [fileContent, setFileContent] = useState('');
  const [fileSearch, setFileSearch] = useState('');

  const [relayForm, setRelayForm] = useState(null);
  const [relayTestTo, setRelayTestTo] = useState('');
  const [relayTestLines, setRelayTestLines] = useState(null);
  const [rspamdView, setRspamdView] = useState('history');
  const [rspamdStat, setRspamdStat] = useState(null);
  const [rspamdHistory, setRspamdHistory] = useState(null);
  const [rspamdFilter, setRspamdFilter] = useState({ q: '', action: '', page: 1 });
  const [rspamdLog, setRspamdLog] = useState(null);
  const [rspamdLogQuery, setRspamdLogQuery] = useState({ q: '', lines: 500 });
  const [eximLog, setEximLog] = useState(null);
  const [eximLogQuery, setEximLogQuery] = useState({ q: '', lines: 500 });
  const [demoDraft, setDemoDraft] = useState({ admin: { username: '', password: '' }, reseller: { username: '', password: '' }, customer: { username: '', password: '' } });
  const [f2b, setF2b] = useState(null);
  // Which list the operator asked to see. Null keeps the page one screen tall
  // however many rules, banned addresses or blocklist URLs there are.
  // File manager target: empty means the selected website, otherwise an app.
  const [composePlan, setComposePlan] = useState(null);
  const [siteRuntimes, setSiteRuntimes] = useState({ docker: { installed: false }, node_majors: [], allowed_registries: [] });
  const [chmodTarget, setChmodTarget] = useState(null);
  const [chmodMode, setChmodMode] = useState('644');
  const [archiveFormat, setArchiveFormat] = useState('zip');
  const [editorCursor, setEditorCursor] = useState({ line: 1, column: 1 });

  // Backup states
  const [userBackups, setUserBackups] = useState([]);
  const [restoreBackups, setRestoreBackups] = useState([]);
  const [restoreBackupDir, setRestoreBackupDir] = useState('');

  const [backupSchedules, setBackupSchedules] = useState([]);
  const [sftpTargets, setSftpTargets] = useState([]);
  const [selectedSftpTargetId, setSelectedSftpTargetId] = useState('');
  const [newBackupSchedule, setNewBackupSchedule] = useState({ user_ids: [], all_users: false, websites: { enabled: false, weekday: 6, hour: 3, weekday_label: '', next_run_at: '', last_run_at: '', last_status: '' }, databases: { enabled: false, weekday: 6, hour: 3, weekday_label: '', next_run_at: '', last_run_at: '', last_status: '' }, mail: { enabled: false, weekday: 6, hour: 3, weekday_label: '', next_run_at: '', last_run_at: '', last_status: '' } });
  const [backupTab, setBackupTab] = useState('archives');
  const [backupJobs, setBackupJobs] = useState([]);

  // WAF and Security states
  const [botBlocks, setBotBlocks] = useState({ requests: [], known_bots: [] });
  const [wafRules, setWafRules] = useState({ status: null, default_rules: '', custom_rules: '' });
  const [wafCustomRules, setWafCustomRules] = useState('');
  const [wafAccessLogs, setWafAccessLogs] = useState({ items: [], total: 0 });
  const [firewallBlocklists, setFirewallBlocklists] = useState(null);
  const [globalBots, setGlobalBots] = useState([]);
  const [wafSiteConfig, setWafSiteConfig] = useState(null);

  // Misc missing states
  const [createdSftpInfo, setCreatedSftpInfo] = useState(null);
  const [createSiteAppId, setCreateSiteAppId] = useState('');

  const [adminAccountForm, setAdminAccountForm] = useState(null);
  const [mailDns, setMailDns] = useState(null);
  const [mailRelays, setMailRelays] = useState([]);
  const [dnsTab, setDnsTab] = useState('zones');
  const [dnsZone, setDnsZone] = useState('');
  const [malwareScanStatus, setMalwareScanStatus] = useState(null);
  const [manualSslFiles, setManualSslFiles] = useState({ certificate: null, private_key: null, ca_bundle: null });
  const [manualSslForm, setManualSslForm] = useState({ certificate: '', private_key: '', ca_bundle: '' });
  const [restoreSource, setRestoreSource] = useState('local');
  const [restoreTargetId, setRestoreTargetId] = useState('');
  const [scanTargetWebsiteId, setScanTargetWebsiteId] = useState('');
  const [selectedDaBackups, setSelectedDaBackups] = useState([]);
  const [serviceNames, setServiceNames] = useState([]);
  const [serviceStates, setServiceStates] = useState([]);
  const [sslMode, setSslMode] = useState('letsencrypt');
  const [sslSources, setSslSources] = useState([]);
  const [twoFactorCode, setTwoFactorCode] = useState('');
  const [twoFactorSetup, setTwoFactorSetup] = useState(null);
  const [twoFactorStatus, setTwoFactorStatus] = useState(null);
  const [updatesStatus, setUpdatesStatus] = useState(null);
  const [userTab, setUserTab] = useState('list');
  const [panelSettingsForm, setPanelSettingsForm] = useState({});

  const [newUser, setNewUser] = useState({ username: '', email: '', password: '', role: 'user', package_id: '', website_limit: 5, storage_limit_mb: 1024, sftp_accounts_limit: 0, mail_accounts_limit: 10 });
  const [editingUserForm, setEditingUserForm] = useState({ email: '', role: 'user', package_id: '', website_limit: 5, storage_limit_mb: 1024, sftp_accounts_limit: 0, mail_accounts_limit: 10, new_password: '', confirm_password: '' });
  const [editingPackageId, setEditingPackageId] = useState('');
  const [editingPackageForm, setEditingPackageForm] = useState({ name: '', website_limit: 5, storage_limit_mb: 1024, sftp_accounts_limit: 0, mail_accounts_limit: 10 });
  // Bot blocking. The list is free text so a whole blocklist can be pasted in
  // one go; the backend splits and cleans it. Targets are the websites the
  // paste is applied to - it is normally the same list on many sites.
  // The global list as an array so each entry can be removed on its own; the
  // paste box is only for adding several at once.
  // The list for the one site being configured, kept apart from the bulk
  // import above so editing one site cannot disturb a pending bulk paste.
  const [assignUserId, setAssignUserId] = useState('');
  const [assignWebsiteId, setAssignWebsiteId] = useState('');
  const [scanResults, setScanResults] = useState(null);
  const [scanJob, setScanJob] = useState(null);
  const [scanJobs, setScanJobs] = useState([]);
  const [malwareSchedules, setMalwareSchedules] = useState(MALWARE_SCHEDULES_DEFAULT);
  const [malwareSchedulesForm, setMalwareSchedulesForm] = useState(MALWARE_SCHEDULES_DEFAULT);
  const [scanLoading, setScanLoading] = useState(false);
  const [incrementalDays, setIncrementalDays] = useState(2);



  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);
  const [userMenuOpen, setUserMenuOpen] = useState(false);
  const [notificationsOpen, setNotificationsOpen] = useState(false);
  const [donateModalOpen, setDonateModalOpen] = useState(false);
  const [donateCopiedField, setDonateCopiedField] = useState("");
  const notificationsRef = useRef();
  const userMenuRef = useRef(null);
  // Set while signing out on purpose. Requests already in flight come back
  // 401 once the server drops the session; they are this sign-out, not an
  // expiry, and must not replace "Logged out." with "Your session expired".
  const signedOutRef = useRef(false);
  const noticeTimer = useRef(null);
  const activeLoadingStackRef = useRef([]);
  const mcpAddonInstalled = !!addons.items.find(item => item.slug === 'mcp')?.installed;
  const notificationsAddonInstalled = !!addons.items.find(item => item.slug === 'notifications')?.installed;
  const malwareAddonInstalled = !!addons.items.find(item => item.slug === 'malware')?.installed;
  const dnsAddonInstalled = !!addons.items.find(item => item.slug === 'dns')?.installed;
  const mailAddonInstalled = !!addons.items.find(item => item.slug === 'mail')?.installed;
  const [mcpDraft, setMcpDraft] = useState({ name: '', expires_in_days: 90, can_write: false });
  // Held until dismissed rather than cleared on the next render: the server
  // keeps only a hash, so a token that scrolls away is gone for good.
  const [mcpNewToken, setMcpNewToken] = useState('');
  const applicationAddon = addons.items.find(item => item.slug === 'application');
  const applicationAddonInstalled = !!applicationAddon?.installed;
  // Two locks, and both have to be open: the server has to have the addon
  // installed at all, and the customer's package has to include it. Admins skip
  // the second one, never the first.
  const appsFeatureEnabled = applicationAddonInstalled && (actualRole === 'admin' || siteApps.limit > 0);
  const roleScopedWebsites = React.useMemo(() => {
    if (isUser && currentUser) {
      return (websites || []).filter(site => site.owner_id === currentUser.id);
    }
    return websites || [];
  }, [websites, isUser, currentUser]);
  const currentSite = roleScopedWebsites.find(site => String(site.id) === String(selectedWebsiteId));
  const accountLabel = currentUser?.package_name
    ? `${currentUser?.username || username} - ${currentUser.package_name}`
    : (currentUser?.username || username);

  const navigateToPage = useCallback((nextPage, options = {}) => {
    const resolvedPage = currentUser && !isPageAllowedForRole(nextPage, effectiveRole)
      ? 'dashboard'
      : nextPage;
    const route = routeForPage(resolvedPage);
    if (!route) return;
    const nextUrl = route;
    const shouldReplace = Boolean(options.replace || resolvedPage !== nextPage);
    if (!shouldReplace && window.location.pathname !== route) {
      window.history.pushState({}, '', nextUrl);
    } else if (shouldReplace && window.location.pathname !== route) {
      window.history.replaceState({}, '', nextUrl);
    }
    // The sidebar's DNS Manager opens the list, not the zone last looked at.
    if (resolvedPage === 'dns') { setDnsZone(null); setDnsTab('zones'); }
    setPage(resolvedPage);
  }, [currentUser, effectiveRole]);

  // Every page opens at its top. The window is the scroller and nothing
  // reset it, so the next page opened at the last one's offset - part-way
  // down, or in empty space until its content loaded. Before paint, and
  // instant: html has scroll-behavior:smooth, which would slide there from
  // the old offset. Back and Forward land at the top as well; the browser's
  // own restoration ran before the page had content and restored nothing.
  useLayoutEffect(() => {
    window.scrollTo({ top: 0, left: 0, behavior: 'instant' });
  }, [page]);
  useEffect(() => {
    if ('scrollRestoration' in window.history) window.history.scrollRestoration = 'manual';
  }, []);

  // Auto-dismiss notices after 6 seconds
  useEffect(() => {
    if (notice) {
      if (noticeTimer.current) clearTimeout(noticeTimer.current);
      noticeTimer.current = setTimeout(() => setNotice(''), 6000);
    }
    return () => { if (noticeTimer.current) clearTimeout(noticeTimer.current); };
  }, [notice]);

  function readCookie(name) {
    const match = document.cookie.match(new RegExp('(?:^|; )' + name.replace(/[$()*+./?[\\\]^{|}]/g, '\\$&') + '=([^;]*)'));
    return match ? decodeURIComponent(match[1]) : '';
  }

  function clearReadableSessionCookies() {
    try {
      document.cookie = 'spanel_csrf=; Max-Age=0; path=/; SameSite=Lax';
      if (window.location.protocol === 'https:') {
        document.cookie = 'spanel_csrf=; Max-Age=0; path=/; SameSite=Lax; Secure';
      }
    } catch { }
  }

  function currentPanelHost() {
    return window.location.hostname || '';
  }

  function currentPanelPort() {
    const port = Number(window.location.port || 2222);
    return Number.isFinite(port) && port > 0 ? port : 2222;
  }



  function clearSession(message = 'Your session expired. Please log in again.') {
    // Old localStorage token from a previous deploy: nuke it for safety.
    try { localStorage.removeItem('token'); } catch { }
    try { localStorage.removeItem('spanel-view-level'); } catch { }
    setViewLevel(null);
    clearReadableSessionCookies();
    setIsAuthenticated(false);
    setCurrentUser(null);
    setNeedsTwoFactor(false);
    setOtpCode('');
    setWebsites([]);
    setDatabases([]);
    setUsers([]);
    setPackages([]);
    setUserTab('list');
    setAdminAccountForm({ email: '', current_password: '', password: '', confirm_password: '', code: '' });
    setResourceUsage(null);
    setServiceStates({});
    setBackupTab('website');
    setBackupJobs([]);
    setChmodTarget(null);
    setFileAppId('');
    setUserBackups([]);
    setRestoreBackups([]);
    setRestoreBackupDir('');

    setBackupSchedules([]);
    setSftpTargets([]);
    setSelectedSftpTargetId('');
    setTwoFactorStatus(null);
    setTwoFactorSetup(null);
    setTwoFactorCode('');
    setMalwareScanStatus(null);
    setScanTargetWebsiteId('');
    setScanResults(null);
    setScanJob(null);
    setScanJobs([]);
    setScanLoading(false);
    setWebsiteList([]);
    setWebsiteSearch('');
    setWebsiteSearching(false);
    setUpdatesStatus(null);
    setFirewallBlocklists(null);
    setWafRules({ status: null, default_rules: '', custom_rules: '' });
    setWafCustomRules('');
    setSelectedWafWebsiteId('');
    setWafSiteConfig(null);
    setWafAccessLogFilters(WAF_ACCESS_LOG_DEFAULTS);
    setWafAccessLogs({ items: [], total: 0, scanned: 0, missing: [], generated_at: '' });
    setSelectedWebsiteId('');
    setMobileMenuOpen(false);
    navigateToPage('dashboard', { replace: true });
    setError('');
    setNotice(message);
  }

  function handleAuthExpired(status, detail = '') {
    if (status === 401 || detail === 'Could not validate credentials' || detail === 'Not authenticated') {
      if (!signedOutRef.current) clearSession();
      return true;
    }
    return false;
  }

  async function request(path, options = {}, label = '') {
    const reqToken = label ? Symbol(label) : null;
    try {
      setError('');
      if (label && reqToken) {
        activeLoadingStackRef.current.push({ token: reqToken, label });
        setLoading(label);
      }
      const { silent, ...fetchOptions } = options;
      const method = (fetchOptions.method || 'GET').toUpperCase();
      const isFormData = typeof FormData !== 'undefined' && fetchOptions.body instanceof FormData;
      const headers = isFormData ? { ...(fetchOptions.headers || {}) } : {
        'Content-Type': 'application/json',
        ...(fetchOptions.headers || {}),
      };
      if (effectiveRole && !headers['X-Access-Role']) {
        headers['X-Access-Role'] = effectiveRole;
      }
      // CSRF: echo the spanel_csrf cookie back in a header for mutating
      // requests. The backend rejects mismatches when the request was
      // authenticated via cookie.
      if (['POST', 'PUT', 'PATCH', 'DELETE'].includes(method)) {
        const csrf = readCookie('spanel_csrf');
        if (csrf) headers['X-CSRF-Token'] = csrf;
      }
      const res = await fetch(`${API}${path}`, {
        ...fetchOptions,
        credentials: 'include',
        headers,
      });
      const text = await res.text();
      let data;
      try { data = text ? JSON.parse(text) : {}; } catch { data = { detail: text || `HTTP ${res.status}` }; }
      if (!res.ok && handleAuthExpired(res.status, data.detail)) return null;
      if (!res.ok && !silent) setError(formatApiError(data.detail, `Request failed with status ${res.status}`));
      if (res.ok && data?.message && !silent) setNotice(data.message);
      return res.ok ? data : null;
    } catch (err) {
      setError(`Cannot connect to the ${panelSettings.app_name || 'spanel'} API at ${API}. Check spanel-api and the panel port.`);
      return null;
    } finally {
      if (label && reqToken) {
        activeLoadingStackRef.current = activeLoadingStackRef.current.filter(item => item.token !== reqToken);
        const remaining = activeLoadingStackRef.current;
        setLoading(remaining.length > 0 ? remaining[remaining.length - 1].label : '');
      }
    }
  }

  async function login(passkeyAssertion = '', credentials = null) {
    try {
      setError('');
      setLoading('Logging in...');
      // credentials: a demo account's button on the login page, which signs in
      // without the visitor typing what the button already shows.
      if (credentials) setUsername(credentials.username);
      const body = new URLSearchParams(credentials || { username, password });
      // An otp and a passkey are never sent together: the customer either used
      // the key or chose the app.
      if (otpCode && !credentials) body.set('otp', otpCode);
      else if (passkeyAssertion) body.set('passkey', passkeyAssertion);
      if (rememberMe) body.set('remember', 'true');
      const res = await fetch(`${API}/auth/login`, {
        method: 'POST',
        body,
        credentials: 'include',
      });
      const data = await res.json().catch(() => ({}));
      if (res.ok && data.requires_passkey) {
        // The account has a passkey for this hostname. Offer it, and remember
        // whether an authenticator app is available as the way out - a passkey
        // is bound to one hostname, so there has to be one.
        setPasskeyPrompt({ options: data.passkey_options, canUseOtp: !!data.requires_2fa });
        setNotice(t('Signed in with a passkey.'));
        await usePasskey(data.passkey_options);
      } else if (res.ok && data.requires_2fa) {
        setNeedsTwoFactor(true);
        setPasskeyPrompt(null);
        setNotice(t('Enter your authentication code.'));
      } else if (res.ok && data.access_token) {
        // Don't keep the token anywhere: the HttpOnly cookie just got set by
        // the response. JS code MUST NOT touch the JWT.
        signedOutRef.current = false;
        setViewLevel(null);
        setIsAuthenticated(true);
        setNeedsTwoFactor(false);
        setPasskeyPrompt(null);
        setOtpCode('');
        setNotice(t('Login successful.'));
        await loadCurrentUser();
      } else {
        setError(formatApiError(data.detail, `Login failed with status ${res.status}`));
      }
    } catch (err) {
      setError(`Cannot connect to the ${panelSettings.app_name || 'spanel'} API at ${API}. Check spanel-api and the panel port.`);
    } finally {
      setLoading('');
    }
  }

  async function logout() {
    signedOutRef.current = true;
    try {
      // Best-effort server logout: clears cookies and bumps token_version.
      await fetch(`${API}/auth/logout`, {
        method: 'POST',
        credentials: 'include',
        headers: (() => {
          const csrf = readCookie('spanel_csrf');
          return csrf ? { 'X-CSRF-Token': csrf } : {};
        })(),
      });
    } catch { }
    clearSession('Logged out.');
  }

  async function usePasskey(optionsJson) {
    if (!passkeySupported()) {
      setError(t('This browser does not support passkeys. Use the code from your authenticator app.'));
      return;
    }
    try {
      const parsed = JSON.parse(optionsJson);
      const credential = await navigator.credentials.get({
        publicKey: decodeRequestOptions(parsed),
      });
      if (!credential) return;
      await login(encodeAssertion(credential));
    } catch (err) {
      // A cancel and a hardware failure look the same here, and neither is
      // worth an alarming message: the authenticator app is still available.
      setError(t('That passkey did not work. Try again, or use the code from your authenticator app.'));
    }
  }







  async function loadCurrentUser({ clearOnUnauthorized = true } = {}) {
    try {
      const res = await fetch(`${API}/auth/session`, { credentials: 'include' });
      if (!res.ok) {
        if (res.status === 401) {
          if (clearOnUnauthorized && !signedOutRef.current) clearSession('Session expired.');
          else {
            clearReadableSessionCookies();
            setCurrentUser(null);
            setIsAuthenticated(false);
          }
        }
        return null;
      }
      const data = await res.json();
      if (!data.authenticated || !data.user) {
        if (clearOnUnauthorized && !signedOutRef.current) clearSession('Session expired.');
        else {
          clearReadableSessionCookies();
          setCurrentUser(null);
          setIsAuthenticated(false);
        }
        return null;
      }
      setCurrentUser(data.user);
      setAdminAccountForm(prev => ({ ...prev, email: data.user?.email || '' }));
      signedOutRef.current = false;
      setIsAuthenticated(true);
      return data.user;
    } catch {
      setCurrentUser(null);
      return null;
    }
  }

  async function loadPanelSettings() {
    // Signed in, the panel tells us more than the login page is allowed to
    // know: the hostnames it answers for and the certificates on this server
    // only come back from the authenticated route.
    const isAdminUser = currentUser?.role === 'admin';
    const path = isAdminUser ? '/panel-settings' : '/panel-settings/public';
    try {
      const res = await fetch(`${API}${path}`, { credentials: 'include' });
      if (!res.ok) return null;
      const data = await res.json();

      try {
        const wlRes = await fetch(`${API}/resellers/whitelabel/public`);
        if (wlRes.ok) {
          const wlData = await wlRes.json();
          if (wlData.company_name && wlData.company_name !== 'SPanel') data.app_name = wlData.company_name;
          if (wlData.logo_url) data.logo_url = wlData.logo_url;
          if (wlData.support_url) data.ticket_url = wlData.support_url;
          if (wlData.primary_color) {
            data.primary_color = wlData.primary_color;
            document.documentElement.style.setProperty('--primary', wlData.primary_color);
            document.documentElement.style.setProperty('--primary-hover', wlData.primary_color);
          }
        }
      } catch (e) { }

      setPanelSettings(data);
      return data;
    } catch {
      return null;
    }
  }

  async function loadAppVersion() {
    try {
      const res = await fetch(`${API}/health`, { credentials: 'include' });
      if (!res.ok) return;
      const data = await res.json();
      setAppVersion(data.version || '');
    } catch { }
  }







  function brandInitials(value = panelSettings.app_name) {
    const words = String(value || 'spanel').trim().split(/\s+/).filter(Boolean);
    const initials = words.length > 1 ? `${words[0][0]}${words[1][0]}` : words[0]?.slice(0, 2);
    return (initials || 'BP').toUpperCase();
  }

  function renderBrandMark(extraClass = '') {
    const classes = ['brand-mark', panelSettings.logo_url ? 'has-logo' : '', extraClass].filter(Boolean).join(' ');
    return <span className={classes}>{panelSettings.logo_url ? <img src={panelSettings.logo_url} alt="" /> : brandInitials()}</span>;
  }

  // Bootstrap: ask for session state without turning an anonymous visit into
  // a console-level 401.
  useEffect(() => {
    (async () => {
      try {
        await loadCurrentUser({ clearOnUnauthorized: false });
      } catch { }
      finally {
        setBootstrapping(false);
        // SSO redirect may carry an error param (e.g. suspended account).
        const urlError = new URLSearchParams(window.location.search).get('error');
        if (urlError) {
          const messages = {
            account_suspended: 'This account is suspended. Contact an administrator.',
          };
          setError(messages[urlError] || urlError);
          window.history.replaceState({}, '', window.location.pathname);
        }
      }
    })();
  }, []);

  useEffect(() => { loadPanelSettings(); loadAppVersion(); }, []);

  useEffect(() => {
    if (currentUser) {
      loadPanelSettings();
    }
  }, [currentUser]);

  useEffect(() => {
    const appName = panelSettings.app_name || 'spanel';
    document.title = appName;
    const configuredFaviconUrl = panelSettings.favicon_url || '/favicon.png';
    const faviconUrl = configuredFaviconUrl.includes('?')
      ? configuredFaviconUrl
      : `${configuredFaviconUrl}?v=${encodeURIComponent(appVersion || 'current')}`;
    const pathname = faviconUrl.split('?', 1)[0].toLowerCase();
    const faviconType = pathname.endsWith('.ico') ? 'image/x-icon'
      : pathname.endsWith('.jpg') || pathname.endsWith('.jpeg') ? 'image/jpeg'
        : pathname.endsWith('.webp') ? 'image/webp'
          : 'image/png';

    const pColor = panelSettings.primary_color || '#4f46e5';
    document.documentElement.style.setProperty('--primary', pColor);
    document.documentElement.style.setProperty('--primary-hover', pColor);
    document.querySelectorAll('link[rel~="icon"]').forEach(link => link.remove());
    const link = document.createElement('link');
    link.rel = 'icon';
    link.type = faviconType;
    link.href = faviconUrl;
    document.head.appendChild(link);
  }, [panelSettings, appVersion]);

  async function loadSiteApps(options = { silent: true }) {
    const data = await request('/site-apps', options, '');
    if (data) setSiteApps({ items: data.items || [], limit: data.limit || 0 });
  }

  async function loadSiteRuntimes() {
    const data = await request('/site-runtimes/status', { silent: true });
    if (data) setSiteRuntimes(data);
  }

  async function loadDatabases(search = dbSearch) {
    const query = String(search || '').trim() ? `?q=${encodeURIComponent(String(search).trim())}` : '';
    const data = await request(`/databases${query}`);
    if (data) setDatabases(data);
  }

  async function refreshAll() {
    const [refreshedUser, siteData] = await Promise.all([
      loadCurrentUser(),
      request('/websites'),
      loadPhpVersions({ silent: true }),
    ]);
    if (siteData) {
      setWebsites(siteData);
      const stillExists = selectedWebsiteId && siteData.some(s => String(s.id) === String(selectedWebsiteId));
      if (!stillExists) {
        setSelectedWebsiteId(siteData[0] ? String(siteData[0].id) : '');
      }
    }
    if (refreshedUser?.role === 'admin' || refreshedUser?.role === 'reseller') {
      await Promise.all([loadPackages(), loadUsers()]);
    } else {
      setUsers([]);
    }
  }

  async function exitImpersonation() {
    const targetName = currentUser?.impersonated_by || 'previous account';
    const data = await request('/auth/impersonate-exit', { method: 'POST' }, `Returning to ${targetName}...`);
    if (data?.access_token) {
      setNotice(`Returned to ${targetName}.`);
      setViewLevel(null);
      await loadCurrentUser();
      navigateToPage('users');
      await refreshAll();
    }
  }





  async function loadPackages() {
    const data = await request('/packages');
    if (data) setPackages(data);
  }









  // Disk usage costs a filesystem walk per account, so the list arrives first
  // and the figures follow. Rows come back with storage_used_bytes = -1 when
  // the panel has not measured them recently; this fills those in.
  async function loadUserStorageUsage() {
    const usage = await request('/users/storage-usage', { silent: true });
    if (!usage) return;
    setUsers(prev => prev.map(user => {
      const found = usage[String(user.id)];
      return found ? { ...user, ...found } : user;
    }));
  }

  async function loadUsers() {
    const data = await request('/users');
    if (data) {
      setUsers(data);
      if (data.some(user => Number(user.storage_used_bytes) < 0)) loadUserStorageUsage();

      setNewBackupSchedule(prev => (!prev.all_users && (!prev.user_ids || prev.user_ids.length === 0) && data[0]) ? ({ ...prev, user_ids: [String(data[0].id)] }) : prev);
    }
  }

  // One request for the dashboard's cards and its "Needs attention" list;
  // each part is best effort on the server, so a failing probe is a card
  // that says "—", not a dashboard that fails.
  async function loadNotifyMe() {
    const data = await request('/notifications/me', { silent: true });
    if (data) setNotifyMe(data);
  }

  // The switch moves when it is clicked, not when the server answers; a
  // refusal puts back what the server holds.
  async function saveNotifyMe(changes) {
    setNotifyMe(prev => (prev ? { ...prev, ...changes } : prev));
    const data = await request('/notifications/me', { method: 'PUT', body: JSON.stringify(changes) });
    if (data) setNotifyMe(data); else loadNotifyMe();
  }

  function toggleNotifyEvent(key, on) {
    const muted = new Set(notifyMe?.muted || []);
    if (on) muted.delete(key); else muted.add(key);
    saveNotifyMe({ muted: [...muted] });
  }

  async function startTelegramLink() {
    const data = await request('/notifications/me/telegram/link', { method: 'POST' });
    if (data) setTelegramLink(data);
  }

  async function verifyTelegramLink() {
    const data = await request('/notifications/me/telegram/verify', { method: 'POST' }, t('Checking...'));
    if (!data) return;
    setNotifyMe(data);
    if (data.telegram_linked) {
      setTelegramLink(null);
      setNotice(t('Telegram connected.'));
    } else {
      setError(t('The bot has not received your /start message yet. Send it, then press Check again.'));
    }
  }

  async function unlinkTelegram() {
    const data = await request('/notifications/me/telegram', { method: 'DELETE' });
    if (data) { setNotifyMe(data); setNotice(t('Telegram disconnected.')); }
  }

  async function sendNotifyTest(channel) {
    const data = await request('/notifications/test', { method: 'POST', body: JSON.stringify({ channel }) }, t('Sending...'));
    if (data) setNotice(t('Test sent to {target}.', { target: data.sent_to }));
    if (isAdmin) loadNotifyLog();
  }

  function applyNotifySettings(data) {
    setNotifySettings(data);
    setSmtpForm({ ...data.smtp, password: '' });
    setBotTokenInput('');
    setAdminChatInput(data.telegram.chat_id || '');
    setTelegramChats(null);
    setNotifyLimits({ ...data.thresholds, language: data.language });
  }

  async function loadNotifySettings() {
    const data = await request('/notifications/settings', { silent: true });
    if (data) applyNotifySettings(data);
  }

  async function saveNotifySettings(body, done) {
    const data = await request('/notifications/settings', { method: 'PUT', body: JSON.stringify(body) }, t('Saving...'));
    if (!data) return;
    applyNotifySettings(data);
    setNotice(done);
    loadNotifyMe();
  }

  async function findTelegramChats() {
    const data = await request('/notifications/telegram/chats', {}, t('Checking...'));
    if (Array.isArray(data)) setTelegramChats(data);
  }

  async function loadNotifyLog() {
    const data = await request('/notifications/log', { silent: true });
    if (Array.isArray(data)) setNotifyLog(data);
  }

  async function loadDashboardSummary() {
    const data = await request('/dashboard/summary', { silent: true });
    if (data) setDashSummary(data);
  }

  async function loadResourceUsage() {
    const data = await request('/services/resource-usage');
    if (data) setResourceUsage(data);
  }













  async function submitPasswordChange(user) {
    if (!user) return;
    const pw = editingUserForm.new_password;
    if (pw.length < 12) { setError(t('Password must be at least 12 characters.')); return; }
    if (pw !== editingUserForm.confirm_password) { setError(t('Passwords do not match.')); return; }
    const payload = { password: pw };
    if (user.id === currentUser?.id) {
      const currentPassword = prompt('Enter your current password to confirm this change:');
      if (!currentPassword) return;
      payload.current_password = currentPassword;
      if (currentUser?.totp_enabled) {
        const code = prompt('Enter the 6-digit code from your authenticator:');
        if (!code) return;
        payload.code = code.trim();
      }
    }
    const data = await request(`/users/${user.id}/password`, { method: 'POST', body: JSON.stringify(payload) }, `Changing password for ${user.username}...`);
    if (data?.message) {
      setNotice(data.message);
      setEditingUserForm(prev => ({ ...prev, new_password: '', confirm_password: '' }));
    }
  }




































  async function installLmd() {
    const data = await request('/malware/lmd/install', { method: 'POST' }, t('Installing...'));
    if (data) { setMalwareScanStatus(data); setNotice(t('Installing the scanner in the background (1-3 minutes). Press Refresh for an update.')); }
  }









  function showMalwareScanJob(job) {
    setScanJob(job);
    setScanResults(job);
    setScanLoading(['queued', 'running'].includes(job?.status));
  }



  async function startClamavDaemon() {
    const data = await request('/malware/start-daemon', { method: 'POST' }, t('Starting ClamAV daemon...'));
    if (data) {
      setNotice(data.message || 'ClamAV daemon started.');
      await loadMalwareScanStatus();
    }
  }

  async function assignDomainToUser() {
    if (!assignWebsiteId || !assignUserId) return;
    const data = await request(`/websites/${assignWebsiteId}`, { method: 'PATCH', body: JSON.stringify({ owner_id: Number(assignUserId) }) }, t('Assigning domain to user...'));
    if (data) { setNotice(`Assigned domain ${data.domain} to user ID ${assignUserId}`); await refreshAll(); }
  }


  async function enableSsl(id) {
    const data = await request(`/websites/${id}/ssl`, { method: 'POST' }, "Installing Let's Encrypt SSL...");
    if (data) refreshAll();
  }

  async function applyCreateSsl(id, siteDomain) {
    if (createSslMode === 'letsencrypt') {
      await enableSsl(id);
      return;
    }
    if (createSslMode === 'wildcard') {
      const body = {};
      if (createSslToken.trim()) body.cloudflare_api_token = createSslToken.trim();
      const data = await request(`/websites/${id}/ssl/wildcard`,
        { method: 'POST', body: JSON.stringify(body) },
        'Issuing wildcard certificate via Cloudflare...');
      if (data) {
        setCreateSslToken('');
        setNotice(`Created ${siteDomain}. Wildcard SSL active — *.${data.ssl_source_domain} covers it.`);
      }
      return;
    }
    if (createSslMode === 'shared') {
      const sources = await request(`/websites/${id}/ssl/sources`, { silent: true });
      const list = Array.isArray(sources) ? sources : [];
      if (!list.length) {
        setError(`Created ${siteDomain}, but no existing certificate covers it. Enable SSL from the SSL page.`);
        return;
      }
      const existing = list.find(cert => cert.covers_domain || cert.covers_wildcard);
      if (existing) {
        const data = await request(`/websites/${id}/ssl/shared`,
          { method: 'POST', body: JSON.stringify({ source_id: existing.id }) },
          `Attaching ${existing.domain} certificate...`);
        if (data) setNotice(`Created ${siteDomain}, secured with ${existing.domain} certificate.`);
      } else {
        setError(`Created ${siteDomain}, but no existing certificate covers it. Enable SSL from the SSL page.`);
      }
      return;
    }
    if (createSslMode === 'manual') {
      setSelectedWebsiteId(String(id));
      setNotice(`Created ${siteDomain}. Open the Manual tab on the SSL page to paste its certificate.`);
      navigateToPage('ssl');
    }
  }

  async function createWordPress() {
    const cleanDomain = domain.trim().toLowerCase();
    if (!cleanDomain) { setError(t('Please enter a domain name.')); return; }
    const body = {
      domain: cleanDomain,
      php_version: phpVersion,
      app_type: 'php',
      install_wordpress: false,
      title: cleanDomain,
    };
    const data = await request('/websites', { method: 'POST', body: JSON.stringify(body) },
      t('Adding domain...'));
    if (data) {
      setDomain('');
      setCreateFormOpen(false);
      setNotice(t('Added domain {domain}. Root folder: /domains/{domain}/public_html', { domain: cleanDomain }));
      setWebsites(prev => {
        const list = Array.isArray(prev) ? prev : [];
        const exists = list.some(s => s.id === data.id);
        return exists ? list.map(s => (s.id === data.id ? data : s)) : [data, ...list];
      });
      await refreshAll();
    }
  }





  // Run the SSL step chosen in the "Create website" form, on the site that was
  // just created. The site already exists at this point, so a failure here is
  // reported but never rolls the site back — the operator can retry from the
  // SSL page.








  async function loadAddons() {
    const data = await request('/addons', { silent: true });
    if (data) setAddons({ ...data, loaded: true });
  }

  async function loadCfZone(id) {
    const data = await request(`/websites/${id}/ssl/cloudflare-zone`, { silent: true });
    if (data) setCfZone({ zone: data.zone || null, has_token: !!data.has_token });
  }













  async function loadDemoAccess() {
    // Public: an ordinary panel answers "not enabled" and the login page shows nothing.
    try {
      const res = await fetch(`${API}/demo-mode/public`, { credentials: 'include' });
      if (res.ok) setDemoAccess(await res.json());
    } catch { }
  }

  async function loadDemoSettings() {
    const data = await request('/demo-mode', { silent: true });
    if (!data) return;
    setDemoSettings(data);
    setDemoDraft({
      admin: data.accounts?.admin || { username: '', password: '' },
      reseller: data.accounts?.reseller || { username: '', password: '' },
      customer: data.accounts?.customer || data.accounts?.end_user || { username: '', password: '' },
    });
  }

  // --- DNS Manager, laid out like OPanel's: the zones, one zone, the settings ---
  const dnsPath = zone => `/dns/zones/${encodeURIComponent(zone.name)}`;











  // A record as the server listed it: how the server finds it again.




















  // --- Email (OPanel's structure): domains, mailboxes, forwarders ---












  function splitAddresses(text) {
    return String(text || '').split(/[\s,;]+/).map(item => item.trim()).filter(Boolean);
  }





  async function saveCatchAll(domain) {
    const value = String(catchAllDraft[domain.id] ?? domain.catch_all ?? '').trim();
    const data = await request(`/mail/domains/${domain.id}`, { method: 'PUT', body: JSON.stringify({ catch_all: value }) }, t('Saving...'));
    if (data) {
      setNotice(value
        ? t('Mail to unknown addresses at {domain} now goes to {target}.', { domain: domain.domain, target: value })
        : t('Mail to unknown addresses at {domain} is now refused.', { domain: domain.domain }));
      setCatchAllDraft(prev => { const next = { ...prev }; delete next[domain.id]; return next; });
      loadMailInfo();
    }
  }

























  function dnsRecordsBody(rows) {
    return rows
      .filter(r => String(r.value || '').trim())
      .map(r => ({
        type: r.type,
        name: String(r.name || '@').trim() || '@',
        value: String(r.value).trim(),
        ...(r.type === 'MX' && String(r.priority ?? '').trim() !== '' ? { priority: Number(r.priority) } : {}),
      }));
  }

  async function saveDomainRelay(value) {
    const data = await request(`/mail/domains/${mailDns.domain.id}/relay`, { method: 'PUT', body: JSON.stringify({ relay: value }) }, t('Saving...'));
    if (data) { setNotice(t('Outgoing mail for {domain} saved.', { domain: mailDns.domain.domain })); applyMailDnsView(mailDns.domain, data); }
  }

  // --- Email: relays (administrators) ---


  function editRelay(relay) {
    setRelayForm(relay
      ? { ...relay, password: '', dns_records: (relay.dns_records || []).map(r => ({ ...r, priority: r.priority ?? '' })), make_default: false }
      : {
        name: '', host: '', port: 587, tls: 'starttls', username: '', password: '', spf_include: '', dns_records: [],
        make_default: !(mailRelays?.relays || []).length
      });
  }

  async function saveRelay() {
    const f = relayForm;
    const body = {
      name: f.name.trim(), host: f.host.trim(), port: Number(f.port) || 587, tls: f.tls,
      username: f.username.trim(), password: f.password, spf_include: f.spf_include.trim(),
      dns_records: dnsRecordsBody(f.dns_records), make_default: !!f.make_default,
    };
    const data = await request(f.id ? `/mail/relays/${f.id}` : '/mail/relays', { method: f.id ? 'PUT' : 'POST', body: JSON.stringify(body) }, t('Saving relay...'));
    if (data) { setMailRelays(data); setRelayForm(null); setNotice(t('Relay saved. Domains that use it need its DNS records.')); }
  }

  async function deleteRelay(relay) {
    if (!confirm(t('Delete the relay {name}?\n\nDomains that use it go back to the default relay.', { name: relay.name }))) return;
    const data = await request(`/mail/relays/${relay.id}`, { method: 'DELETE' }, t('Deleting...'));
    if (data) { setMailRelays(data); setNotice(t('{name} deleted.', { name: relay.name })); }
  }

  async function setDefaultRelay(relayId) {
    const data = await request('/mail/default-relay', { method: 'PUT', body: JSON.stringify({ relay_id: relayId }) }, t('Saving...'));
    if (data) { setMailRelays(data); setNotice(relayId ? t('Default relay saved.') : t('Mail now leaves directly, except for domains with a relay of their own.')); }
  }

  async function testRelay() {
    setRelayTestLines(null);
    const data = await request('/mail/relays/test', { method: 'POST', body: JSON.stringify({ to: relayTestTo.trim() }) }, t('Sending a test message...'));
    if (data) setRelayTestLines(data.lines || []);
  }

  // --- Email: Rspamd and logs (administrators) ---
  async function loadRspamdStat() {
    const data = await request('/mail/rspamd/stat', { silent: true });
    setRspamdStat(data || null);
  }

  async function loadRspamdHistory(page = rspamdFilter.page, action = rspamdFilter.action) {
    const params = new URLSearchParams({ page: String(page), per_page: '50' });
    if (rspamdFilter.q.trim()) params.set('q', rspamdFilter.q.trim());
    if (action) params.set('action', action);
    const data = await request(`/mail/rspamd/history?${params}`, { silent: true });
    setRspamdHistory(data || { items: [], total: 0, page: 1, per_page: 50 });
    setRspamdFilter(prev => ({ ...prev, page, action }));
  }

  async function loadRspamdLog(query = rspamdLogQuery) {
    const params = new URLSearchParams({ lines: String(query.lines) });
    if (query.q.trim()) params.set('q', query.q.trim());
    const data = await request(`/mail/rspamd/log?${params}`, { silent: true });
    setRspamdLog(data?.lines || []);
  }

  async function loadEximLog(query = eximLogQuery) {
    const params = new URLSearchParams({ lines: String(query.lines) });
    if (query.q.trim()) params.set('q', query.q.trim());
    const data = await request(`/mail/log?${params}`, { silent: true });
    setEximLog(data?.lines || []);
  }









  async function saveDemoAccounts() {
    const slot = entry => (entry && entry.username ? entry : null);
    const data = await request('/demo-mode', {
      method: 'PUT',
      body: JSON.stringify({
        admin: slot(demoDraft.admin),
        reseller: slot(demoDraft.reseller),
        customer: slot(demoDraft.customer),
      }),
    }, t('Saving...'));
    if (data) {
      setDemoSettings(data);
      setNotice(t(data.message || 'Demo accounts saved.'));
    }
  }

  async function validateCompose(source, webService, env, webPort) {
    return await request('/site-apps/compose/validate', {
      method: 'POST',
      body: JSON.stringify({
        compose_source: source,
        web_service: webService || null,
        env: env || '',
        web_port: Number(webPort) || null,
      }),
    }, t('Checking the compose file...'));
  }

















  async function installDockerEngine() {
    const data = await request('/site-runtimes/docker-install', { method: 'POST' }, t('Installing Docker, this takes a few minutes...'));
    if (data) {
      setNotice(data.message || 'Docker is ready.');
      await loadSiteRuntimes();
    }
  }

  async function pruneDocker() {
    const data = await request('/site-runtimes/docker-prune', { method: 'POST' }, t('Pruning unused Docker layers...'));
    if (data) {
      setNotice(data.message || 'Pruned.');
      await loadSiteRuntimes();
    }
  }

  async function installNodeMajor(major) {
    const data = await request('/site-runtimes/node-install', { method: 'POST', body: JSON.stringify({ major }) }, `Installing Node ${major}...`);
    if (data) {
      setNotice(data.message || `Node ${major} is ready.`);
      await loadSiteRuntimes();
    }
  }





















  function openWebsiteTerminal(site) {
    setWordpressInstaller(null);
  }











  async function fixWordPressPermissions(id) {
    const data = await request(`/maintenance/wordpress/${id}/fix-permissions`, { method: 'POST' }, t('Fixing permissions...'));
    if (data?.message) setNotice(data.message);
  }







  function generateRandomPassword(length = 20) {
    const chars = 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789!@#%^*_+-';
    const arr = new Uint8Array(length);
    crypto.getRandomValues(arr);
    return Array.from(arr, b => chars[b % chars.length]).join('');
  }



























  function fileTargetQuery() {
    return fileAppId ? `app_id=${encodeURIComponent(fileAppId)}` : `website_id=${encodeURIComponent(selectedWebsiteId)}`;
  }

  async function listFiles(path = fileListPath) {
    if (!hasFileTarget()) return;
    const data = await request(`/maintenance/files/list?${fileTargetQuery()}&path=${encodeURIComponent(path || '')}`, {}, t('Loading files...'));
    if (Array.isArray(data)) {
      setFiles(data);
      setFileListPath(path || '');
      setFileUploadDir(path || '');
      setSelectedFilePaths([]);
    }
  }

  async function readFile(nextPath = filePath) {
    if (!hasFileTarget() || !nextPath) return;
    const data = await request(`${fileTargetBase()}?path=${encodeURIComponent(nextPath)}`, {}, t('Reading file...'));
    if (data && data.content !== undefined) {
      setFilePath(nextPath);
      setFileContent(data.content);
    }
  }

  async function writeFile() {
    if (!hasFileTarget() || !filePath) return;
    const data = await request('/maintenance/files/write', {
      method: 'POST',
      body: JSON.stringify({ ...fileTargetBody(), path: filePath, content: fileContent }),
    }, t('Saving file...'));
    if (data) { setNotice(`Saved ${data.path}`); await listFiles(fileListPath); }
  }

  async function downloadFile(path) {
    if (!hasFileTarget()) return;
    try {
      setError('');
      setLoading('Downloading file...');
      const res = await fetch(`${API}/maintenance/files/download?${fileTargetQuery()}&path=${encodeURIComponent(path)}`, {
        credentials: 'include',
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        if (handleAuthExpired(res.status, data.detail)) return;
        setError(formatApiError(data.detail, `Download failed with status ${res.status}`));
        return;
      }
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = path.split('/').pop() || 'download';
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch {
      setError(t('Download failed.'));
    } finally {
      setLoading('');
    }
  }

  function fileEditorUrl(path) {
    const url = new URL(window.location.href);
    url.pathname = routeForPage('files');
    url.search = '';
    url.hash = '';
    url.searchParams.set('view', 'editor');
    if (fileAppId) url.searchParams.set('app_id', String(fileAppId));
    else url.searchParams.set('website_id', String(selectedWebsiteId));
    url.searchParams.set('path', path);
    return url.toString();
  }

  function openFileEditorTab(path) {
    if (!hasFileTarget() || !path) return;
    window.open(fileEditorUrl(path), '_blank', 'noopener,noreferrer');
  }







  function openChmodDialog(items) {
    const targets = (Array.isArray(items) ? items : [items]).filter(Boolean);
    if (targets.length === 0) return;
    setChmodTarget(targets);
    if (targets.length === 1 && normalizeOctalMode(targets[0].mode)) {
      setChmodMode(normalizeOctalMode(targets[0].mode));
      return;
    }
    // With a mixed selection, keep the special bits only when every target
    // already agrees on them, so a bulk chmod never silently drops setgid.
    const specials = targets.map(item => octalToPermissionBits(item.mode).special);
    const special = specials.every(value => value === specials[0]) ? specials[0] : 0;
    const base = targets.every(item => item.is_dir)
      ? { owner: 7, group: 5, other: 5 }
      : { special: 0, owner: 6, group: 4, other: 4 };
    setChmodMode(permissionBitsToOctal({ special, ...base }));
  }

  async function applyChmod() {
    const targets = chmodTarget || [];
    const mode = chmodMode.trim();
    if (targets.length === 0) return;
    if (!/^[0-7]{3,4}$/.test(mode)) { setError(t('Mode must be octal, for example 644 or 755.')); return; }
    for (const item of targets) {
      const data = await request('/maintenance/files/chmod', {
        method: 'POST',
        body: JSON.stringify({ ...fileTargetBody(), path: item.path, mode }),
      }, `Setting permissions on ${item.name}...`);
      // request() already surfaced the reason; stop so the dialog keeps the mode.
      if (!data) return;
    }
    setChmodTarget(null);
    setNotice(t('Permissions set to {mode} on {n} item(s).', { mode, n: targets.length }));
    await listFiles(fileListPath);
  }













  function upsertFileJob(job) {
    if (!job?.job_id) return;
    setFileJobs(prev => [job, ...prev.filter(item => item.job_id !== job.job_id)].slice(0, 6));
  }

  function dismissFileJob(jobId) {
    setFileJobs(prev => prev.filter(item => item.job_id !== jobId));
  }






  useEffect(() => {
  }, [page, selectedWebsiteId, fileAppId]);

  async function openWebsiteFileManager(site) {
    setWordpressInstaller(null);
    setFileAppId('');
    setSelectedWebsiteId(String(site.id));
    navigateToPage('files');
    setFileListPath('public_html');
    setFileUploadDir('public_html');
    setFiles([]);
    setSelectedFilePaths([]);
  }





































  async function loadPhpVersions(options = { silent: true }) {
    const silent = options?.silent !== false;
    const data = await request('/maintenance/php-versions', silent ? { silent: true } : {}, silent ? '' : t('Loading PHP versions...'));
    if (data) {
      const installedList = sortPhpVersions(data.installed?.length ? data.installed : ['8.4']);
      const supportedList = sortPhpVersions(data.supported?.length ? data.supported : ['5.6', '7.4', '8.0', '8.1', '8.2', '8.3', '8.4', '8.5']);
      setPhpVersions({
        installed: installedList,
        supported: supportedList,
      });
      if (!installedList.includes(phpVersion)) {
        setPhpVersion(installedList.includes('8.4') ? '8.4' : installedList[installedList.length - 1]);
      }
    }
  }



  // One version at a time, so "every version" is several short apt runs and
  // the table fills in as each one lands.











































  function toggleWafDefaultRule(ruleId, enabled) {
    setWafSiteConfig(prev => {
      if (!prev) return prev;
      const current = new Set(prev.enabled_rule_ids || []);
      if (enabled) current.add(ruleId); else current.delete(ruleId);
      return {
        ...prev,
        enabled_rule_ids: Array.from(current),
        default_rules: (prev.default_rules || []).map(rule => rule.id === ruleId ? { ...rule, enabled } : rule),
      };
    });
  }

  async function loadBotBlocks() {
    const data = await request('/waf/bots', {}, t('Loading blocked bots...'));
    if (data) {
      setBotBlocks(data);
      setGlobalBots(data.global_blocked_bots || []);
    }
  }

  async function saveGlobalBots(nextList) {
    const data = await request('/waf/bots/global', {
      method: 'PUT',
      body: JSON.stringify({ blocked_bots: nextList.join('\n') }),
    }, t('Saving global bad bots...'));
    if (data) {
      setGlobalBots(data.global_blocked_bots || []);
      setNotice(data.failed?.length
        ? `${data.message} Failed: ${data.failed.map(f => `${f.domain} (${f.error})`).join('; ')}`
        : (data.message || 'Global bad bots saved.'));
      await loadBotBlocks();
    }
  }









  function addGlobalBots(text) {
    const incoming = String(text || '').split(/[\n,;]+/).map(s => s.trim()).filter(Boolean);
    if (incoming.length === 0) return;
    const seen = new Set(globalBots.map(s => s.toLowerCase()));
    const merged = [...globalBots];
    for (const name of incoming) {
      if (!seen.has(name.toLowerCase())) { seen.add(name.toLowerCase()); merged.push(name); }
    }
    setGlobalBots(merged);
  }









  function updateWafAccessLogFilters(patch, shouldLoad = false) {
    setWafAccessLogFilters(prev => {
      const next = { ...prev, ...patch };
      if (shouldLoad) loadWafAccessLogs(next, false);
      return next;
    });
  }



  function exportWafAccessLogs() {
    const rows = wafAccessLogs.items || [];
    const header = ['verdict', 'time', 'domain', 'method', 'path', 'ip', 'country', 'country_code', 'reason', 'status', 'duration_ms', 'user_agent'];
    const csv = [
      header.join(','),
      ...rows.map(item => header.map(key => csvCell(key === 'time' ? item.timestamp : item[key])).join(',')),
    ].join('\n');
    const blob = new Blob([csv], { type: 'text/csv;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    const site = websites.find(item => String(item.id) === String(wafAccessLogFilters.websiteId));
    link.href = url;
    link.download = `spanel-access-logs-${site?.domain || 'all'}.csv`;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
  }














  useEffect(() => {
    if (isAuthenticated) {
      refreshAll();
    }
  }, [isAuthenticated, effectiveRole]);

  useEffect(() => {
    if (standaloneEditor) return undefined;
    const syncPageFromLocation = () => setPage(pageFromPathname(window.location.pathname));
    syncPageFromLocation();
    window.addEventListener('popstate', syncPageFromLocation);
    return () => window.removeEventListener('popstate', syncPageFromLocation);
  }, [standaloneEditor]);

  useEffect(() => {
    if (!isAuthenticated || !currentUser) return;
    if (!isPageAllowedForRole(page, effectiveRole)) {
      navigateToPage('dashboard', { replace: true });
    }
  }, [isAuthenticated, currentUser, page, effectiveRole, navigateToPage]);

  useEffect(() => {
    if (!isAuthenticated || !standaloneEditor) return;
    setSelectedWebsiteId(standaloneEditor.websiteId);
    setFileAppId(standaloneEditor.appId || '');
    setFilePath(standaloneEditor.path);
    readFile(standaloneEditor.path);
  }, [isAuthenticated, standaloneEditor]);

  useEffect(() => {
    if (!standaloneEditor || !isAuthenticated) return undefined;
    const handler = event => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 's') {
        event.preventDefault();
        writeFile();
      }
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [standaloneEditor, isAuthenticated, selectedWebsiteId, filePath, fileContent]);

  useEffect(() => {
    if (isAuthenticated) loadDashboardSummary();
  }, [isAuthenticated, page, effectiveRole]);


  useEffect(() => {
    if (!isAuthenticated || page !== 'notifications' || !notificationsAddonInstalled || !isAdmin) return;
    loadNotifyMe();
    loadNotifySettings();
    loadNotifyLog();
  }, [isAuthenticated, page, notificationsAddonInstalled, isAdmin]);

  useEffect(() => {
    if (!isAuthenticated || page !== 'dashboard' || !isAdmin) return undefined;
    loadResourceUsage();
    const timer = setInterval(loadResourceUsage, 5000);
    return () => clearInterval(timer);
  }, [isAuthenticated, page, isAdmin]);






  useEffect(() => {
    if (!currentSite) return;
    const modeMap = { manual: 'manual', cloudflare: 'wildcard', shared: 'shared' };
    setSslMode(modeMap[currentSite.ssl_mode] || 'letsencrypt');
    setManualSslForm({ certificate: '', private_key: '', ca_bundle: '' });
    setManualSslFiles({ certificate: null, private_key: null, ca_bundle: null });
    setWildcardToken('');
    setSharedSource('');
    setCfZone({ zone: null, has_token: false });
    setSslSources([]);
  }, [currentSite?.id]);

  useEffect(() => {
    if (page === 'ssl' && selectedWebsiteId) { loadCfZone(selectedWebsiteId); }
  }, [page, selectedWebsiteId]);


  // Which optional features exist decides what the nav shows, so this is asked
  // once per session rather than per page.
  useEffect(() => { if (currentUser) loadAddons(); }, [currentUser]);
  useEffect(() => { if (!isAuthenticated) loadDemoAccess(); }, [isAuthenticated]);
  const demoAddonInstalled = addons.items.some(item => item.slug === 'demo' && item.installed);
  useEffect(() => { if (isAdmin && demoAddonInstalled) loadDemoSettings(); }, [isAdmin, demoAddonInstalled]);

  // The websites page needs the list too, for the Application picker on create.
  useEffect(() => { if (isAuthenticated && applicationAddonInstalled) loadSiteApps({ silent: true }); }, [isAuthenticated, applicationAddonInstalled]);





  useEffect(() => {
    if (isAuthenticated && (page === 'settings' || page === 'panel-settings')) { loadPanelSettings(); }
  }, [isAuthenticated, page]);




  useEffect(() => { setMobileMenuOpen(false); }, [page]);

  // The account menu closes on a click anywhere else, on Escape, and on navigation.
  useEffect(() => {
    if (!userMenuOpen) return undefined;
    const onPointer = event => { if (!userMenuRef.current?.contains(event.target)) setUserMenuOpen(false); };
    const onKey = event => { if (event.key === 'Escape') setUserMenuOpen(false); };
    document.addEventListener('mousedown', onPointer);
    document.addEventListener('keydown', onKey);
    return () => { document.removeEventListener('mousedown', onPointer); document.removeEventListener('keydown', onKey); };
  }, [userMenuOpen]);

  useEffect(() => { setUserMenuOpen(false); }, [page]);

  // Redirect non-admin users away from disabled Addon routes
  useEffect(() => {
    if (!addons.loaded || isAdmin) return;
    const isMailPage = ['mail', 'mail_forwarders', 'mail_auto', 'mail_spam'].includes(page);
    const isMalwarePage = page === 'malware' || page === 'malware-scan';
    if (
      (page === 'dns' && !dnsAddonInstalled) ||
      (isMailPage && !mailAddonInstalled) ||
      (page === 'applications' && !appsFeatureEnabled) ||
      (page === 'mcp' && !mcpAddonInstalled) ||
      (isMalwarePage && !malwareAddonInstalled) ||
      (page === 'notifications')
    ) {
      navigateToPage('dashboard', { replace: true });
    }
  }, [
    addons.loaded,
    isAdmin,
    page,
    dnsAddonInstalled,
    mailAddonInstalled,
    appsFeatureEnabled,
    mcpAddonInstalled,
    malwareAddonInstalled,
    navigateToPage,
  ]);

  // The sidebar in three labelled groups, as OPanel has it - what a site
  // needs, what guards it, and the server itself - so nothing hides behind a
  // collapsed "Settings". Labels are English and go through t() when drawn.
  // The sidebar holds what is used every day (operator, 2026-09-27); the rest
  // is one click further, on the Settings page. An addon shows only once it
  // is on - Application, AI assistants, Notifications.


  let navSections = [];
  if (isAdmin) {
    navSections = [
      { key: 'account_manager', title: 'Account Manager', items: [
        ['users', 'Show All Users', Users],
        ['my_users', 'My Users', Users],
        ['resource_monitor', 'Resource Monitor', Cpu],
        ['packages', 'Manage User Packages', Boxes],
        ['move_users', 'Move Users Between Resellers', ArrowRightLeft],
        ['message_templates', 'Message templates', FileText],
        ['change_passwords', 'Change Passwords', KeyRound],
        ['resellers', 'Resellers', Users],
        ['reseller_packages', 'Manage Reseller Packages', Boxes],
        ['administrators', 'Administrators', Shield],
      ]},
      { key: 'server_manager', title: 'Server Manager', items: [
        ['settings', 'Administrator Settings', SettingsIcon],
        ['custom_httpd', 'Custom HTTPD Configurations', Server],
        ['dns', 'DNS Administration', Network],
        ['ipmanager', 'IP Management', Network],
        ['name_servers', 'Name Servers', Globe],
        ['multi_server', 'Multi Server Setup', Server],
        ['php', 'PHP Settings', Code2],
        ['server_tls', 'Server TLS Certificate', Lock],
        ...(notificationsAddonInstalled ? [['notifications', 'Alerting & Webhooks', Bell]] : []),
        ['custombuild', 'CustomBuild', Wrench],
      ]},
      { key: 'admin_tools', title: 'Admin Tools', items: [
        ['backups', 'Backup and Restore', Archive],
        ['brute_force', 'Brute Force Monitor', ShieldAlert],
        ['process_monitor', 'Process Monitor', Activity],
        ['mail', 'Mail Queue Administration', Mail],
        ['services', 'System Services', Activity],
        ['migration', 'cPanel Import', Copy],
        ['message_all', 'Message All Users', Mail],
        ['reseller_settings', 'Customize Skin', SettingsIcon],
        ['access-logs', 'Access & Error Logs', FileText],
        ['maintenance', 'Maintenance', Wrench],
      ]},
      { key: 'system_info', title: 'System Info & Files', items: [
        ['log_viewer', 'Log Viewer', FileText],
        ['all_crons', 'All User Cron Jobs', Clock],
        ['server_statistics', 'Server Statistics', BarChart],
      ]},
      { key: 'extra_features', title: 'Extra Features', items: [
        ['addons', 'Addon Manager', Boxes],
        ['waf', 'Web Application Firewall', ShieldAlert],
        ['malware', 'Imunify360 Plugin', Bug],
        ['firewall', 'ConfigServer Security & Firewall', Shield],
        ...(appsFeatureEnabled ? [['applications', 'Applications (Node/Python/Docker)', Cpu]] : []),
        ...(mcpAddonInstalled ? [['mcp', 'AI Assistants (MCP)', Bot]] : []),
      ]},
      { key: 'support', title: 'Support', items: [
        ['create_ticket', 'Support Ticket', MessageSquare],
        ['updates', 'Updates', RotateCcw],
        ['licensing', 'Licensing', Key],
      ]}
    ];
  } else if (isReseller) {
    navSections = [
      { key: 'account_manager', title: 'Account Manager', items: [
        ['users', 'List Users', Users],
        ['resource_monitor', 'Resource Monitor', Cpu],
        ['packages', 'Manage User Packages', Boxes],
        ['change_passwords', 'Change Passwords', KeyRound],
        ['suspend', 'Suspend / Unsuspend', AlertTriangle],
      ]},
      { key: 'reseller_tools', title: 'Reseller Tools', items: [
        ['reseller_settings', 'Customize Skin', SettingsIcon],
        ['backups', 'Backup and Restore', Archive],
        ['migration', 'cPanel Import', Copy],
        ['ipmanager', 'IP Management', Network],
        ['server_statistics', 'System/Service Info', BarChart],
      ]},
      { key: 'system_info', title: 'System Info & Files', items: [
        ['access-logs', 'Access & Error Logs', FileText],
      ]},
      { key: 'extra_features', title: 'Extra Features', items: [
        ['waf', 'Web Application Firewall', ShieldAlert],
        ...(appsFeatureEnabled ? [['applications', 'Applications (Node/Python/Docker)', Cpu]] : []),
        ...(mcpAddonInstalled ? [['mcp', 'AI Assistants (MCP)', Bot]] : []),
      ]},
      { key: 'support', title: 'Support', items: [
        ['create_ticket', 'Support Ticket', MessageSquare],
      ]}
    ];
  } else {
    navSections = [
      { key: 'account_manager', title: 'Account Manager', items: [
        ['websites', 'Domains', Globe],
        ['subdomains', 'Subdomain Management', Globe],
        ...(dnsAddonInstalled ? [['dns', 'DNS Management', Network]] : []),
        ['ssl', 'SSL Certificates', Lock],
        ['sftp', 'FTP Management', Upload],
        ['databases', 'MySQL Management', Database],
        ['redirects', 'Site Redirection', MoveRight],
      ]},
      ...(mailAddonInstalled ? [{ key: 'email_manager', title: 'Email Manager', items: [
        ['mail', 'E-mail Accounts', Mail],
        ['mail_forwarders', 'Forwarders', Mail],
        ['mail_auto', 'Autoresponders', Mail],
        ['mail_spam', 'Spam Filters', Shield],
      ]}] : []),
      { key: 'advanced_features', title: 'Advanced Features', items: [
        ...(appsFeatureEnabled ? [['applications', 'Applications (Node/Python/Docker)', Cpu]] : []),
        ['wordpress_manager', 'WordPress Manager', WordPressIcon],
        ['waf', 'Web Application Firewall', ShieldAlert],
        ['cron', 'Cron Jobs', Clock],
        ['backups', 'Create/Restore Backups', Archive],
        ['security', 'Two-Step Authentication', Key],
        ...(mcpAddonInstalled ? [['mcp', 'AI Assistants (MCP)', Bot]] : []),
        ['apache_handlers', 'Apache Handlers', Server],
      ]},
      { key: 'system_info', title: 'System Info & Files', items: [
        ['files', 'File Manager', FolderOpen],
        ['access-logs', 'Access & Error Logs', FileText],
        ['site_stats', 'Site Summary / Statistics', BarChart],
      ]},
      { key: 'support', title: 'Support', items: [
        ['create_ticket', 'Support Ticket', MessageSquare],
      ]}
    ];
  }
  navSections = navSections.filter(section => Array.isArray(section.items) && section.items.length > 0);

  const UNIMPLEMENTED_NAV_KEYS = [];

  function handleNavSelect(key) {
    if (UNIMPLEMENTED_NAV_KEYS.includes(key)) {
      setNotice(t('This feature is currently under development.'));
      return;
    }
    if (key === 'subdomains') {
      navigateToPage('subdomains');
      return;
    }
    if (key === 'mail_spam') {
      setMailTab('domains');
      navigateToPage('mail');
      return;
    }
    if (key === 'suspend') {
      setUserTab('list');
      navigateToPage('users');
      setNotice(t('Use the Suspend / Unsuspend action on any user account below.'));
      return;
    }
    if (key === 'log_viewer') {
      navigateToPage('access-logs');
      return;
    }
    navigateToPage(key);
  }

  const allNavItems = navSections.flatMap(section => section.items);
  const effectivePage = isPageAllowedForRole(page, effectiveRole) ? page : 'dashboard';
  const basePage = NAV_PARENT_PAGE[effectivePage] || effectivePage;
  const navPage = basePage;
  const activeNavItem = effectivePage === 'dashboard'
    ? ['dashboard', 'Dashboard', Home]
    : effectivePage === 'security'
    ? ['security', 'Account security', LockKeyhole]
    : (allNavItems.find(([key]) => key === navPage) || ['dashboard', 'Dashboard', Home]);
  const pageItem = effectivePage === 'dashboard'
    ? ['dashboard', 'Dashboard', Home]
    : effectivePage === 'security'
    ? ['security', 'Account security', LockKeyhole]
    : (allNavItems.find(([key]) => key === basePage) || activeNavItem);

  const [cmdPaletteOpen, setCmdPaletteOpen] = useState(false);
  const [cmdPaletteQuery, setCmdPaletteQuery] = useState('');
  const [cmdPaletteIndex, setCmdPaletteIndex] = useState(0);

  useEffect(() => {
    if (!isAuthenticated || standaloneEditor) return undefined;
    const onKeyDown = e => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault();
        setCmdPaletteOpen(prev => {
          const next = !prev;
          if (next) {
            setCmdPaletteQuery('');
            setCmdPaletteIndex(0);
          }
          return next;
        });
      } else if (e.key === 'Escape' && cmdPaletteOpen) {
        setCmdPaletteOpen(false);
      }
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [isAuthenticated, standaloneEditor, cmdPaletteOpen]);

  const cmdPaletteItems = React.useMemo(() => {
    const list = [
      {
        id: 'nav-dashboard',
        category: t('Navigation'),
        title: t('Dashboard'),
        subtitle: '/',
        icon: Home,
        run: () => navigateToPage('dashboard'),
      },
    ];
    navSections.forEach(sec => {
      (sec.items || []).forEach(([key, label, Icon]) => {
        if (key === 'create_ticket') return;
        list.push({
          id: `nav-${key}`,
          category: t(sec.title || 'Menu'),
          title: t(label),
          subtitle: PAGE_ROUTES[key] || `/${key}`,
          icon: Icon || Globe,
          run: () => handleNavSelect(key),
        });
      });
    });
    if (isUser) {
      (websites || []).forEach(site => {
        list.push({
          id: `site-${site.id}`,
          category: t('Website'),
          title: site.domain,
          subtitle: `PHP ${site.php_version || '8.4'} · ${site.root_path || ''}`,
          icon: Globe,
          run: () => {
            setSelectedWebsiteId(String(site.id));
            navigateToPage('websites');
          },
        });
        list.push({
          id: `site-files-${site.id}`,
          category: t('File Manager'),
          title: `${site.domain} — ${t('Files')}`,
          subtitle: site.root_path || '/public_html',
          icon: FolderOpen,
          run: () => openWebsiteFileManager(site),
        });
      });
      (databases || []).forEach(db => {
        list.push({
          id: `db-${db.id || db.name}`,
          category: t('Database'),
          title: db.name,
          subtitle: db.username ? `User: ${db.username}` : 'MariaDB / MySQL',
          icon: Database,
          run: () => navigateToPage('databases'),
        });
      });
    }
    (siteApps?.items || []).forEach(app => {
      list.push({
        id: `app-${app.id}`,
        category: t('Application'),
        title: app.name,
        subtitle: `${app.kind} · 127.0.0.1:${app.port}`,
        icon: Cpu,
        run: () => navigateToPage('applications'),
      });
    });
    if (isAdmin || isReseller) {
      (users || []).forEach(u => {
        list.push({
          id: `user-${u.id}`,
          category: t('Account'),
          title: u.username,
          subtitle: `${u.role || 'user'}${u.email ? ` · ${u.email}` : ''}`,
          icon: Users,
          run: () => navigateToPage('users'),
        });
      });
    }
    const q = cmdPaletteQuery.trim().toLowerCase();
    if (!q) return list.slice(0, 25);
    return list
      .filter(
        item =>
          item.title.toLowerCase().includes(q) ||
          item.category.toLowerCase().includes(q) ||
          (item.subtitle || '').toLowerCase().includes(q)
      )
      .slice(0, 25);
  }, [navSections, websites, databases, siteApps, users, isAdmin, isReseller, cmdPaletteQuery, t]);

  function renderNotifications() {
    const errorMessage = formatApiError(error, '').trim();
    const noticeMessage = formatApiError(notice, '').trim();
    if (!errorMessage && !noticeMessage) return null;
    return <div className="app-toast-stack" aria-label={t('Notifications')}>
      <NotificationToast type="error" message={errorMessage} onClose={() => setError('')} />
      <NotificationToast type="success" message={noticeMessage} onClose={() => setNotice('')} />
    </div>;
  }

  function websiteUrl(site) {
    const value = (site?.domain || '').trim();
    if (/^https?:\/\//i.test(value)) return value;
    return `${site?.ssl_enabled ? 'https' : 'http'}://${value}`;
  }

  // The file manager browses either a website root or an application root.
  function fileTargetBody() {
    return fileAppId ? { app_id: Number(fileAppId) } : { website_id: Number(selectedWebsiteId) };
  }

  function fileTargetBase() {
    return fileAppId ? `/maintenance/app-files/${fileAppId}` : `/maintenance/files/${selectedWebsiteId}`;
  }

  function fileTargetKey() {
    return fileAppId ? `app:${fileAppId}` : (selectedWebsiteId ? `site:${selectedWebsiteId}` : '');
  }

  function hasFileTarget() {
    return !!(fileAppId || selectedWebsiteId);
  }

  function currentFileApp() {
    return siteApps.items.find(app => String(app.id) === String(fileAppId)) || null;
  }

  function FileTargetSelect() {
    return <select
      value={fileAppId ? `app:${fileAppId}` : selectedWebsiteId}
      onChange={e => {
        const value = e.target.value;
        if (value.startsWith('app:')) setFileAppId(value.slice(4));
        else { setFileAppId(''); setSelectedWebsiteId(value); }
        setFileListPath(value.startsWith('app:') ? '' : 'public_html');
        setFiles([]);
        setSelectedFilePaths([]);
      }}
    >
      <option value="">-- Select website or application --</option>
      {websites.map(site => <option key={`site-${site.id}`} value={site.id}>{site.domain}</option>)}
      {appsFeatureEnabled && siteApps.items.map(app => <option key={`app-${app.id}`} value={`app:${app.id}`}>{t('App:')} {app.name}</option>)}
    </select>;
  }

  function parentFilePath(path) {
    const parts = String(path || '').split('/').filter(Boolean);
    parts.pop();
    return parts.join('/');
  }

  function fileBreadcrumbs(path) {
    const parts = String(path || '').split('/').filter(Boolean);
    let current = '';
    return parts.map(part => {
      current = current ? `${current}/${part}` : part;
      return { label: part, path: current };
    });
  }

  function isTextEditable(item) {
    if (!item || item.is_dir) return false;
    const name = (item.name || '').toLowerCase();
    const editableDotfiles = new Set(['.env', '.env.example', '.htaccess', '.user.ini', '.gitignore', '.gitattributes']);
    return editableDotfiles.has(name) || /\.(txt|md|json|css|js|jsx|ts|tsx|html|htm|xml|yml|yaml|ini|conf|log|php|env|htaccess)$/.test(name) || !name.includes('.');
  }

  function isArchiveFile(item) {
    if (!item || item.is_dir) return false;
    const name = (item.name || '').toLowerCase();
    return name.endsWith('.zip') || name.endsWith('.tar.gz') || name.endsWith('.tgz');
  }

  function toggleFileSelection(path) {
    setSelectedFilePaths(prev => prev.includes(path) ? prev.filter(item => item !== path) : [...prev, path]);
  }

  function toggleAllFiles() {
    setSelectedFilePaths(prev => prev.length === files.length ? [] : files.map(item => item.path));
  }

  function editorLanguage(path) {
    const name = String(path || '').toLowerCase();
    if (/\.php\d?$/.test(name) || name.endsWith('.phtml')) return 'PHP';
    if (/\.(js|jsx|ts|tsx)$/.test(name)) return 'JavaScript';
    if (/\.css$/.test(name)) return 'CSS';
    if (/\.html?$/.test(name)) return 'HTML';
    if (/\.json$/.test(name)) return 'JSON';
    if (/\.ya?ml$/.test(name)) return 'YAML';
    if (/\.(conf|ini|env|htaccess)$/.test(name)) return 'Config';
    return 'Text';
  }

  // dd/mm/yyyy hh:mm in the reader's clock; with seconds for a tooltip.
  // The API sends Unix seconds.
  function formatFileTime(seconds, withSeconds = false) {
    const d = new Date(Number(seconds) * 1000);
    if (!seconds || Number.isNaN(d.getTime())) return '';
    const two = n => String(n).padStart(2, '0');
    const stamp = `${two(d.getDate())}/${two(d.getMonth() + 1)}/${d.getFullYear()} ${two(d.getHours())}:${two(d.getMinutes())}`;
    return withSeconds ? `${stamp}:${two(d.getSeconds())}` : stamp;
  }

  function formatPercent(value) {
    const amount = Number(value);
    if (!Number.isFinite(amount)) return '--';
    return `${Math.round(amount)}%`;
  }

  function clampPercent(value) {
    const amount = Number(value);
    if (!Number.isFinite(amount)) return 0;
    return Math.max(0, Math.min(100, amount));
  }

  function storageLimitBytes(user) {
    if (!user) return null;
    if (user.storage_limit_bytes === null) return null;
    if (user.storage_limit_bytes !== undefined) return user.storage_limit_bytes;
    return Number(user.storage_limit_mb || 0) * 1024 * 1024;
  }

  function storageUsageText(user) {
    // -1 means the panel has not measured this account yet and did not hold
    // the list up to do it. Saying so beats drawing a zero that reads as fact.
    if (Number(user?.storage_used_bytes) < 0) return t('measuring...');
    const used = Number(user?.storage_used_bytes || 0);
    const limit = storageLimitBytes(user);
    if (limit === null) return formatBytes(used);
    return `${formatBytes(used)} / ${formatBytes(limit)}`;
  }

  function ResourceCard({ icon: Icon, label, value, detail, percent }) {
    const safePercent = percent == null ? null : Math.min(100, Math.max(0, percent));
    const color = safePercent >= 90 ? 'var(--danger, #ef4444)' : safePercent >= 80 ? 'var(--warn, #f59e0b)' : 'var(--success, #10b981)';

    return <article className="aapanel-resource-card">
      <div className="aapanel-res-text">
        <span className="aapanel-res-label">{label}</span>
        <strong className="aapanel-res-detail">{detail}</strong>
      </div>
      {safePercent !== null ? (
        <div className="aapanel-res-donut" style={{ '--percent': safePercent + '%', '--chart-color': color }}>
          <div className="aapanel-res-donut-inner">{Math.round(safePercent)}%</div>
        </div>
      ) : (
        <div className="aapanel-res-value">{value}</div>
      )}
    </article>;
  }

  function renderDashboard() {
    const cpu = resourceUsage?.cpu || {};
    const memory = resourceUsage?.memory || {};
    const disk = resourceUsage?.disk || {};
    const network = resourceUsage?.network || {};
    const networkTotal = (Number(network.rx_per_sec) || 0) + (Number(network.tx_per_sec) || 0);

    const sum = dashSummary || {};
    const sites = sum.websites || { total: websites.length, suspended: 0, waf_on: websites.filter(site => site.waf_enabled).length };
    const ssl = sum.ssl || { total: websites.length, secured: websites.filter(site => site.ssl_enabled).length, unsecured: [], unsecured_count: 0 };
    const dbCount = sum.databases?.total ?? databases.length;

    const storageLimit = currentUser ? storageLimitBytes(currentUser) : 0;
    const siteLimit = currentUser ? (Number(currentUser.website_limit) || 0) : 0;
    const usedBytes = currentUser ? (Number(currentUser.storage_used_bytes) || 0) : 0;
    const pct = (used, limit) => limit > 0 ? (used / limit) * 100 : null;

    let quickActions = [];
    if (isReseller) {
      quickActions = [
        { key: 'add-user', icon: Users, label: t('List / Modify Users'), primary: true, run: () => navigateToPage('users') },
        { key: 'packages', icon: Boxes, label: t('Manage User Packages'), run: () => navigateToPage('packages') },
        { key: 'backup', icon: Archive, label: t('Backup and Restore'), run: () => navigateToPage('backups') },
        { key: 'waf', icon: Shield, label: t('Web Application Firewall'), run: () => navigateToPage('waf') },
        { key: 'stats', icon: Activity, label: t('System Info'), run: () => navigateToPage('server_statistics') },
      ];
    } else if (!isAdmin) {
      quickActions = [
        { key: 'site', icon: Plus, label: t('Add Domain'), primary: true, run: () => { setCreateFormOpen(true); navigateToPage('websites'); } },
        { key: 'db', icon: Database, label: t('New database'), run: () => { setDbCreateOpen(true); navigateToPage('databases'); } },
        { key: 'ssl', icon: Lock, label: t('Install SSL'), run: () => navigateToPage('ssl') },
        { key: 'backup', icon: Archive, label: t('Back up a website'), run: () => navigateToPage('backups') },
        {
          key: 'sftp', icon: KeyRound, label: t('New SFTP account'), run: () => {
            navigateToPage('sftp');
            window.setTimeout(() => document.querySelector('.sftp-form input')?.focus(), 150);
          }
        }
      ];
    }

    const iconColors = [
      '#3b82f6', '#10b981', '#f59e0b', '#8b5cf6', '#ec4899', '#14b8a6', '#6366f1', '#f43f5e', '#84cc16'
    ];
    let colorIndex = 0;

    const gridSections = navSections.map(section => ({
      title: section.title,
      items: section.items.map(([key, label, Icon]) => {
        const color = iconColors[colorIndex % iconColors.length];
        colorIndex++;
        return { label, icon: Icon, key, color };
      })
    }));

    return <div className="dashboard user-grid-dashboard">
      <div className="dashboard-hero">
        <div>
          <h2>{t('Welcome back,')} {currentUser?.username || username || 'Admin'}</h2>
          <p>{t('Your control panel overview and quick actions.')}</p>
        </div>
        <div className="hero-quick-actions">
          {quickActions.slice(0, 3).map(action => <button type="button" key={action.key} className={action.primary ? 'primary' : 'secondary'} onClick={action.run}><action.icon size={15} />{action.label}</button>)}
        </div>
      </div>

      <div className="aapanel-resources-grid" style={{ '--meter-cols': isAdmin ? 4 : 3 }}>
        {isAdmin ? <>
          <ResourceCard icon={Cpu} label="CPU" value={formatPercent(cpu.percent)} percent={cpu.percent} detail={cpu.load?.length ? t('Load {load}', { load: cpu.load.join(' / ') }) : t('{count} cores', { count: cpu.cores || '--' })} />
          <ResourceCard icon={MemoryStick} label="RAM" value={formatPercent(memory.percent)} percent={memory.percent} detail={`${formatBytes(memory.used)} / ${formatBytes(memory.total)}`} />
          <ResourceCard icon={HardDrive} label={t('Disk')} value={formatPercent(disk.percent)} percent={disk.percent} detail={`${formatBytes(disk.used)} / ${formatBytes(disk.total)}`} />
          <ResourceCard icon={Network} label={t('Network')} value={`${formatBytes(networkTotal)}/s`} detail={t('Down {down}/s · Up {up}/s', { down: formatBytes(network.rx_per_sec), up: formatBytes(network.tx_per_sec) })} />
        </> : currentUser ? <>
          <ResourceCard icon={HardDrive} label={t('Storage')} value={storageLimit ? formatPercent(pct(usedBytes, storageLimit)) : formatBytes(usedBytes)} percent={storageLimit ? pct(usedBytes, storageLimit) : null} detail={storageLimit ? t('{used} of {limit}', { used: formatBytes(usedBytes), limit: formatBytes(storageLimit) }) : t('unlimited')} />
          <ResourceCard icon={Globe} label={t('Websites')} value={siteLimit ? `${websites.length} / ${siteLimit}` : String(websites.length)} percent={pct(websites.length, siteLimit)} detail={siteLimit ? t('{used} of {limit}', { used: websites.length, limit: siteLimit }) : t('unlimited')} />
          <ResourceCard icon={Database} label={t('Databases')} value={String(databases.length)} percent={null} detail={t('in your account')} />
        </> : null}
      </div>

      <div className="da-grids-wrapper">
        {gridSections.map((section, i) => (
          <div key={i} className="da-grid-section">
            <h3 className="da-grid-title">{t(section.title)}</h3>
            <div className="da-grid-items">
              {section.items.map(item => {
                const isUnimplemented = UNIMPLEMENTED_NAV_KEYS.includes(item.key);
                if (item.key === 'create_ticket') {
                  const ticketUrl = (panelSettings?.ticket_url || '').trim();
                  const ticketHref = ticketUrl ? (/^https?:\/\//i.test(ticketUrl) ? ticketUrl : `https://${ticketUrl}`) : '';
                  if (ticketHref) {
                    return (
                      <a
                        key={item.key}
                        href={ticketHref}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="da-grid-card"
                        style={{ textDecoration: 'none' }}
                        title={ticketHref}
                      >
                        <div className="da-grid-icon" style={{ color: item.color, background: `${item.color}15` }}>
                          <item.icon size={28} strokeWidth={1.5} />
                        </div>
                        <span className="da-grid-label">{t(item.label)}</span>
                      </a>
                    );
                  }
                  return (
                    <button
                      type="button"
                      key={item.key}
                      className="da-grid-card"
                      onClick={() => setNotice(t('Support Ticket URL is not configured. Administrators can set it in Settings.'))}
                      style={{ opacity: 0.5 }}
                      title={t('Support Ticket URL is not configured. Administrators can set it in Settings.')}
                    >
                      <div className="da-grid-icon" style={{ color: item.color, background: `${item.color}15` }}>
                        <item.icon size={28} strokeWidth={1.5} />
                      </div>
                      <span className="da-grid-label">{t(item.label)}</span>
                    </button>
                  );
                }
                return (
                  <button
                    type="button"
                    key={item.key}
                    className="da-grid-card"
                    onClick={() => handleNavSelect(item.key)}
                    style={isUnimplemented ? { opacity: 0.5 } : {}}
                    title={isUnimplemented ? t('Coming soon') : ''}
                  >
                    <div className="da-grid-icon" style={{ color: item.color, background: `${item.color}15` }}>
                      <item.icon size={28} strokeWidth={1.5} />
                    </div>
                    <span className="da-grid-label">{t(item.label)}</span>
                  </button>
                );
              })}
            </div>
          </div>
        ))}
      </div>
    </div>;
  }

  function renderAdminOnly() {
    // For pages that describe the machine rather than a customer's slice of it.
    // They stay reachable by URL, so they say so plainly instead of rendering
    // and firing a page full of requests the server will refuse.
    return <section className="section">
      <div className="section-title"><div><h2>{t('Administrators only')}</h2></div></div>
      <EmptyState
        icon={Server}
        message={t('This page reports on the server itself, so only administrators can see it.')}
      />
    </section>;
  }

  function renderNotificationsPage() {
    const me = notifyMe;
    const s = notifySettings;
    const events = me?.events || [];
    const muted = new Set(me?.muted || []);
    const eventRow = ev => <label className="notify-event" key={ev.key}>
      <input type="checkbox" checked={!muted.has(ev.key)} disabled={!!loading} onChange={e => toggleNotifyEvent(ev.key, e.target.checked)} />
      <span><strong>{t(ev.label)}</strong><small>{t(ev.hint)}</small></span>
    </label>;
    const fmtTime = value => { const d = new Date(value.endsWith('Z') ? value : `${value}Z`); return Number.isNaN(d.getTime()) ? value : d.toLocaleString('vi-VN', { hour12: false }); };
    return <>
      <section className="section">
        <div className="section-title">
          <div>
            <h2>{t('My notifications')}</h2>
            <p className="hint">{t('Where the panel reaches you, and about what.')}</p>
          </div>
        </div>
        {!me ? <p className="hint">{t('Loading...')}</p> : <>
          <div className="notify-channels">
            <div className="notify-channel">
              <div className="notify-channel-head"><Mail size={16} /><strong>Email</strong>
                <span className={me.channels.email && me.email_enabled ? 'badge ok' : 'badge'}>{me.channels.email ? (me.email_enabled ? t('On') : t('Off')) : t('Not set up')}</span>
              </div>
              <p className="hint">{me.channels.email
                ? t('Sent to {email}, the address on your account.', { email: me.email || '—' })
                : t('Email is not set up on this server yet.')}</p>
              {me.channels.email && <label className="switch-line"><input type="checkbox" checked={me.email_enabled} disabled={!!loading} onChange={e => saveNotifyMe({ email_enabled: e.target.checked })} />{t('Send me email')}</label>}
            </div>
            <div className="notify-channel">
              <div className="notify-channel-head"><Send size={16} /><strong>Telegram</strong>
                <span className={me.telegram_linked && me.telegram_enabled ? 'badge ok' : 'badge'}>{!me.channels.telegram ? t('Not set up') : me.telegram_linked ? (me.telegram_enabled ? t('On') : t('Off')) : t('Not connected')}</span>
              </div>
              {!me.channels.telegram && <p className="hint">{t('Telegram is not set up on this server yet.')}</p>}
              {me.channels.telegram && me.telegram_linked && <>
                <p className="hint">Chat ID: <code>{me.telegram_chat_id}</code></p>
                <label className="switch-line"><input type="checkbox" checked={me.telegram_enabled} disabled={!!loading} onChange={e => saveNotifyMe({ telegram_enabled: e.target.checked })} />{t('Send me Telegram messages')}</label>
                <div className="notify-actions">
                  <button type="button" className="secondary" disabled={!!loading} onClick={() => sendNotifyTest('telegram')}><Send size={14} />{t('Send a test')}</button>
                  <button type="button" className="secondary-light" disabled={!!loading} onClick={unlinkTelegram}>{t('Disconnect')}</button>
                </div>
              </>}
              {me.channels.telegram && !me.telegram_linked && me.telegram_admin_chat && <p className="hint">{t('You hear in the admin chat ({chat}). Connect a chat of your own to hear there instead.', { chat: me.telegram_admin_chat })}</p>}
              {me.channels.telegram && !me.telegram_linked && <div className="notify-own-chat">
                <input value={myChatInput} placeholder={t('Your chat ID')} aria-label={t('Your chat ID')} onChange={e => setMyChatInput(e.target.value)} />
                <button type="button" className="secondary" disabled={!!loading || !myChatInput.trim()} onClick={() => saveNotifyMe({ telegram_chat_id: myChatInput.trim() })}>{t('Save')}</button>
              </div>}
              {me.channels.telegram && !me.telegram_linked && (telegramLink
                ? <div className="notify-link">
                  <ol>
                    <li>{t('Open the bot and press Start:')}{' '}{telegramLink.link ? <a href={telegramLink.link} target="_blank" rel="noreferrer">@{telegramLink.bot_username}</a> : <code>@{telegramLink.bot_username}</code>}</li>
                    <li>{t('Or send the bot this message:')}{' '}<code>/start {telegramLink.code}</code></li>
                    <li>{t('Then press Check. The code works for {n} minutes.', { n: telegramLink.expires_minutes })}</li>
                  </ol>
                  <div className="notify-actions"><button type="button" disabled={!!loading} onClick={verifyTelegramLink}><Check size={14} />{t('Check')}</button></div>
                </div>
                : <div className="notify-actions"><button type="button" className="secondary" disabled={!!loading} onClick={startTelegramLink}><Send size={14} />{t('Find my chat ID for me')}</button></div>)}
            </div>
          </div>
          <div className="notify-events">
            <h3>{t('About the server')}</h3>
            <div className="notify-event-list">{events.filter(ev => ev.audience === 'admin').map(eventRow)}</div>
            <h3>{t('About your account')}</h3>
            <div className="notify-event-list">{events.filter(ev => ev.audience === 'user').map(eventRow)}</div>
          </div>
        </>}
      </section>

      {s && <section className="section">
        <div className="section-title">
          <div>
            <h2>{t('Server channels')}</h2>
            <p className="hint">{t('What every notification on this server goes out through.')}</p>
          </div>
        </div>
        <div className="notify-settings">
          <div className="notify-card">
            <h3><Mail size={16} />{t('Email (SMTP)')}</h3>
            <div className="notify-form">
              <label><span>{t('SMTP server')}</span><input value={smtpForm.host} placeholder="smtp.example.com" onChange={e => setSmtpForm(f => ({ ...f, host: e.target.value }))} /></label>
              <div className="notify-form-pair">
                <label><span>{t('Encryption')}</span><select value={smtpForm.security} onChange={e => setSmtpForm(f => ({ ...f, security: e.target.value, port: { starttls: 587, ssl: 465, none: 25 }[e.target.value] }))}>
                  <option value="starttls">STARTTLS</option><option value="ssl">SSL/TLS</option><option value="none">{t('None')}</option>
                </select></label>
                <label><span>{t('Port')}</span><input type="number" value={smtpForm.port} onChange={e => setSmtpForm(f => ({ ...f, port: e.target.value }))} /></label>
              </div>
              <label><span>{t('Username')}</span><input value={smtpForm.username} autoComplete="off" onChange={e => setSmtpForm(f => ({ ...f, username: e.target.value }))} /></label>
              <label><span>{t('Password')}</span><input type="password" value={smtpForm.password} autoComplete="new-password" placeholder={s.smtp.password_set ? t('Saved - leave empty to keep it') : ''} onChange={e => setSmtpForm(f => ({ ...f, password: e.target.value }))} /></label>
              <label><span>{t('Sender address')}</span><input value={smtpForm.from_email} placeholder="noreply@example.com" onChange={e => setSmtpForm(f => ({ ...f, from_email: e.target.value }))} /></label>
              <label><span>{t('Sender name')}</span><input value={smtpForm.from_name} onChange={e => setSmtpForm(f => ({ ...f, from_name: e.target.value }))} /></label>
            </div>
            <div className="notify-actions">
              <button type="button" disabled={!!loading} onClick={() => saveNotifySettings({ smtp: { ...smtpForm, port: Number(smtpForm.port) || 587, password: smtpForm.password || null } }, t('Email settings saved.'))}>{t('Save')}</button>
              <button type="button" className="secondary" disabled={!!loading || !s.smtp_ready} onClick={() => sendNotifyTest('email')}><Mail size={14} />{t('Send me a test email')}</button>
            </div>
          </div>
          <div className="notify-card">
            <h3><Send size={16} />{t('Telegram bot')}</h3>
            <p className="hint">{t('Create a bot with @BotFather (/newbot) and paste its token. Then send the bot any message - or add it to a group - press Find chat ID and pick the chat.')}</p>
            {s.telegram.token_set && <p className="notify-bot"><span className="badge ok">@{s.telegram.bot_username}</span></p>}
            <label className="notify-form"><span>{t('Bot token')}</span><input type="password" value={botTokenInput} autoComplete="off" placeholder={s.telegram.token_set ? t('Saved - paste a new one to replace it') : '123456789:AA...'} onChange={e => setBotTokenInput(e.target.value)} /></label>
            <div className="notify-form">
              <label><span>Chat ID</span><input value={adminChatInput} placeholder="-1001234567890" onChange={e => setAdminChatInput(e.target.value)} /></label>
              <div className="notify-actions"><button type="button" className="secondary" disabled={!!loading || !s.telegram.token_set} onClick={findTelegramChats}><Search size={14} />{t('Find chat ID')}</button></div>
              {telegramChats && (telegramChats.length > 0
                ? <div className="notify-chats">{telegramChats.map(chat => <button type="button" key={chat.id} className={adminChatInput === chat.id ? 'mini' : 'mini secondary'} onClick={() => setAdminChatInput(chat.id)}>{chat.name || chat.type}<code>{chat.id}</code></button>)}</div>
                : <p className="hint">{t('No messages yet. Send the bot a message first, then press Find chat ID again.')}</p>)}
            </div>
            <p className="hint">{t('Server events go to this chat once. An administrator with no chat of their own hears about their account here too.')}</p>
            <div className="notify-actions">
              <button type="button" disabled={!!loading || (!s.telegram.token_set && !botTokenInput.trim())} onClick={() => saveNotifySettings({ telegram: { bot_token: botTokenInput.trim() || null, chat_id: adminChatInput.trim() } }, t('Telegram bot saved.'))}>{t('Save')}</button>
              <button type="button" className="secondary" disabled={!!loading || !s.telegram.token_set || !s.telegram.chat_id} onClick={() => sendNotifyTest('telegram_admin')}><Send size={14} />{t('Send a test')}</button>
              {s.telegram.token_set && <button type="button" className="secondary-light" disabled={!!loading} onClick={() => saveNotifySettings({ telegram: { clear_bot_token: true } }, t('Telegram bot removed.'))}>{t('Remove the bot')}</button>}
            </div>
          </div>
          <div className="notify-card">
            <h3><AlertCircle size={16} />{t('When to warn')}</h3>
            <div className="notify-form">
              <label><span>{t('Disk usage (%)')}</span><input type="number" min="50" max="99" value={notifyLimits.disk_percent} onChange={e => setNotifyLimits(v => ({ ...v, disk_percent: e.target.value }))} /></label>
              <label><span>{t('Certificate expiry (days)')}</span><input type="number" min="1" max="60" value={notifyLimits.ssl_days} onChange={e => setNotifyLimits(v => ({ ...v, ssl_days: e.target.value }))} /></label>
              <label><span>{t('Language of the messages')}</span><select value={notifyLimits.language} onChange={e => setNotifyLimits(v => ({ ...v, language: e.target.value }))}>
                <option value="vi">Tiếng Việt</option><option value="en">English</option>
              </select></label>
            </div>
            <div className="notify-actions">
              <button type="button" disabled={!!loading} onClick={() => saveNotifySettings({
                language: notifyLimits.language, thresholds: {
                  disk_percent: Number(notifyLimits.disk_percent), ssl_days: Number(notifyLimits.ssl_days),
                }
              }, t('Saved.'))}>{t('Save')}</button>
            </div>
          </div>
        </div>
      </section>}


      <section className="section">
        <div className="section-title">
          <div><h2>{t('Delivery log')}</h2><p className="hint">{t('The last 100 messages sent, and the ones that failed.')}</p></div>
          <div className="actions"><button type="button" className="secondary" disabled={!!loading} onClick={loadNotifyLog}><RefreshCw size={14} />{t('Refresh')}</button></div>
        </div>

        {notifyLog.length === 0
          ? <EmptyState icon={Bell} message="Nothing sent yet." />
          : <div className="notify-log">
            {notifyLog.map(row => <div className="notify-log-row" key={row.id}>
              <span className="notify-log-time">{fmtTime(row.created_at)}</span>
              <span className="notify-log-user">{row.username || '—'}</span>
              <span className="notify-log-title">{row.title}</span>
              <span className="notify-log-channel">{row.channel === 'email' ? 'Email' : 'Telegram'}</span>
              <span className={row.status === 'sent' ? 'badge ok' : 'badge danger'}>{row.status === 'sent' ? t('Sent') : t('Failed')}</span>
              {row.status !== 'sent' && row.detail && <small className="notify-log-detail">{row.detail}</small>}
            </div>)}
          </div>
        }
      </section>
    </>;
  }

  function renderSettingsHub() {
    const settingsGroups = [
      {
        title: 'System Settings',
        items: [
          ['panel-settings', 'Panel Settings', SettingsIcon, 'Configure core panel options'],
          ['updates', 'Updates & Maintenance', RotateCcw, 'Check for panel updates'],
          ['addons', 'Addons', Boxes, 'Manage extensions and plugins'],
        ]
      },
      {
        title: 'Installed Extensions',
        items: [
          ...(dnsAddonInstalled ? [['dns', 'DNS Manager', Network, 'Configure DNS records']] : []),
          ...(mailAddonInstalled ? [['mail', 'Email Server', Mail, 'Manage email accounts']] : []),
          ...(mcpAddonInstalled ? [['mcp', 'AI Assistants', Bot, 'Configure AI and MCP']] : []),
          ...(notificationsAddonInstalled ? [['notifications', 'Notifications', Bell, 'Set up alerts and notifications']] : []),
        ]
      }
    ];

    return <div className="settings-hub">
      {settingsGroups.map(group => group.items.length > 0 && <section className="section settings-group" key={group.title}>
        <h2>{t(group.title)}</h2>
        <div className="settings-links">
          {group.items.map(([key, label, Icon, hint]) => <button type="button" className="settings-link" key={key} onClick={() => navigateToPage(key)}>
            <span className="settings-link-icon"><Icon size={18} /></span>
            <span className="settings-link-text"><strong>{t(label)}</strong><small>{t(hint)}</small></span>
          </button>)}
        </div>
      </section>)}
    </div>;
  }

  // Which addon a page belongs to decides the words: this said
  // "Applications" on the Malware, MCP and Notifications pages too.
  function renderAddonMissing(slug = 'application') {
    const addon = addons.items.find(item => item.slug === slug);
    const name = addon?.name || 'Application';
    const installed = !!addon?.installed;
    return <section className="section">
      <div className="section-title"><div><h2>{t(name)}</h2></div></div>
      <EmptyState
        icon={Boxes}
        message={slug === 'application' && installed
          ? t('Your package does not include Applications. Contact an administrator to upgrade.')
          : t('The {name} addon is not installed on this server.', { name: t(name) })}
      />
      {isAdmin && !installed && <div className="site-app-form-actions">
        <button disabled={!!loading} onClick={() => navigateToPage('addons')}><Boxes size={14} />{t('Go to Addons')}</button>
      </div>}
    </section>;
  }

  // One line: the nameservers a domain's registrar is given.












  function renderCopyBlock(title, text, { multiline = false } = {}) {
    return <div className="copy-block">
      <div className="copy-block-head">
        <span>{title}</span>
        <button type="button" className="mini secondary-light" onClick={() => copyText(text, t('Copied.'))}><Copy size={13} /> {t('Copy')}</button>
      </div>
      {multiline ? <pre className="copy-block-code">{text}</pre> : <code className="copy-block-code">{text}</code>}
    </div>;
  }













  // A relay's DNS template: what every domain sending through it publishes.
  function renderDnsRecordEditor(rows, onChange) {
    const setRow = (index, patch) => onChange(rows.map((row, i) => i === index ? { ...row, ...patch } : row));
    return <div className="dns-editor">
      {rows.map((row, index) => <div className={`dns-editor-row${row.type === 'MX' ? ' with-priority' : ''}`} key={index}>
        <select value={row.type} aria-label={t('Type')} onChange={e => setRow(index, { type: e.target.value })}>
          {['TXT', 'CNAME', 'MX', 'A', 'AAAA'].map(type => <option key={type} value={type}>{type}</option>)}
        </select>
        <input value={row.name} placeholder="@" aria-label={t('Name')} spellCheck={false} onChange={e => setRow(index, { name: e.target.value })} />
        {row.type === 'MX' && <input type="number" min="0" max="65535" value={row.priority ?? ''} placeholder="10" aria-label={t('Priority')} onChange={e => setRow(index, { priority: e.target.value })} />}
        <input className="dns-editor-value" value={row.value} placeholder={t('Value ({domain} = the domain)')} aria-label={t('Value')} spellCheck={false} onChange={e => setRow(index, { value: e.target.value })} />
        <button type="button" className="mini danger icon-only" aria-label={t('Remove')} title={t('Remove')} onClick={() => onChange(rows.filter((_, i) => i !== index))}><Trash2 size={13} /></button>
      </div>)}
      <div className="actions dns-editor-actions">
        <button type="button" className="mini secondary-light" disabled={rows.length >= 10} onClick={() => onChange([...rows, { type: 'TXT', name: '@', value: '', priority: '' }])}><Plus size={13} /> {t('Add a record')}</button>
      </div>
      <p className="hint">{t('Names are relative to each domain that uses the relay: @ is the domain itself, brevo1._domainkey a name under it. {domain} in a value becomes the domain name.')}</p>
    </div>;
  }



  function relayTlsLabel(tls) {
    return { starttls: 'STARTTLS', ssl: 'SSL/TLS', none: t('no TLS') }[tls] || tls;
  }

  function renderRelayForm() {
    const f = relayForm;
    const set = patch => setRelayForm(prev => ({ ...prev, ...patch }));
    const canSave = f.name.trim() && f.host.trim() && (!f.username.trim() || f.password || f.password_set);
    return <div className="mail-tab">
      <div className="create-inline mail-relay-form">
        <div className="create-inline-head">
          <strong>{f.id ? t('Edit relay {name}', { name: f.name }) : t('New relay')}</strong>
          <button type="button" className="secondary-light icon-only mini" onClick={() => setRelayForm(null)} aria-label={t('Close')} title={t('Close')}><X size={15} /></button>
        </div>

        <div className="mail-settings-grid">
          <label className="field"><span className="field-label">{t('Name')}</span><input value={f.name} placeholder="Brevo" onChange={e => set({ name: e.target.value })} /></label>
          <label className="field"><span className="field-label">{t('Relay host')}</span><input value={f.host} placeholder="smtp-relay.brevo.com" spellCheck={false} onChange={e => set({ host: e.target.value })} /></label>
          <label className="field"><span className="field-label">{t('Port')}</span><input type="number" min="1" max="65535" value={f.port}
            onChange={e => set({ port: e.target.value, ...(e.target.value === '465' ? { tls: 'ssl' } : f.tls === 'ssl' ? { tls: 'starttls' } : {}) })} /></label>
          <label className="field"><span className="field-label">TLS</span>
            <select value={f.tls} onChange={e => set({ tls: e.target.value })}>
              <option value="starttls">STARTTLS</option>
              <option value="ssl">SSL/TLS</option>
              <option value="none">{t('None (private network only)')}</option>
            </select></label>
          <label className="field"><span className="field-label">{t('Username')}</span><input value={f.username} autoComplete="off" spellCheck={false} onChange={e => set({ username: e.target.value })} data-lpignore="true" data-1p-ignore="true" /></label>
          <label className="field"><span className="field-label">{t('Password')}</span><input type="password" value={f.password} autoComplete="new-password"
            placeholder={f.password_set ? t('Saved — leave empty to keep') : ''} onChange={e => set({ password: e.target.value })} data-lpignore="true" data-1p-ignore="true" /></label>
        </div>

        <div className="mail-relay-template">
          <strong>{t('Mail DNS template')}</strong>
          <p className="hint">{t("What every domain that sends through this relay must publish. Customers see it on their domain's DNS records page and set up their domain from it.")}</p>
          <label className="field"><span className="field-label">{t('SPF for this relay')} <em>{t('added to the SPF record of every domain that uses it')}</em></span>
            <input value={f.spf_include} placeholder="include:spf.brevo.com" spellCheck={false} list="mail-spf-includes" onChange={e => set({ spf_include: e.target.value })} />
            <datalist id="mail-spf-includes">{MAIL_SPF_INCLUDES.map(item => <option key={item} value={item} />)}</datalist></label>
          <div className="field"><span className="field-label">{t('DNS records the relay asks for')}</span>
            {renderDnsRecordEditor(f.dns_records, rows => set({ dns_records: rows }))}</div>
        </div>

        {!f.id && <label className="check-line"><input type="checkbox" checked={!!f.make_default} onChange={e => set({ make_default: e.target.checked })} /> {t('Use it as the default relay')}</label>}
        <p className="hint">{t("587 uses STARTTLS and 465 SSL/TLS, and the relay's certificate must be valid. Leave the username empty for a relay that knows this server by its address.")}</p>
        <div className="actions">
          <button type="button" className="secondary-light" onClick={() => setRelayForm(null)}>{t('Cancel')}</button>
          <button type="button" disabled={!canSave || !!loading} onClick={saveRelay}><Save size={14} /> {t('Save relay')}</button>
        </div>
      </div>
    </div>;
  }



  function rspamdActionLabel(action) {
    return {
      'no action': t('Delivered'),
      'add header': t('Marked as spam'),
      'rewrite subject': t('Subject marked'),
      greylist: t('Greylisted'),
      'soft reject': t('Deferred'),
      reject: t('Rejected'),
    }[action] || action;
  }

  function renderLogViewer(lines, query, setQuery, reload) {
    return <>
      <div className="mail-toolbar mail-log-toolbar">
        <form className="mail-search" onSubmit={e => { e.preventDefault(); reload(query); }}>
          <input value={query.q} placeholder={t('Filter, e.g. an address or a message ID')} aria-label={t('Filter')} onChange={e => setQuery(prev => ({ ...prev, q: e.target.value }))} />
          <button type="submit" className="secondary-light icon-only" aria-label={t('Search')} title={t('Search')}><Search size={14} /></button>
        </form>
        <select value={query.lines} aria-label={t('Lines')} onChange={e => { const next = { ...query, lines: Number(e.target.value) }; setQuery(next); reload(next); }}>
          {[200, 500, 1000, 3000].map(n => <option key={n} value={n}>{t('Last {n} lines', { n })}</option>)}
        </select>
        <button type="button" className="secondary-light" disabled={!!loading} onClick={() => reload(query)}><RefreshCw size={14} /> {t('Refresh')}</button>
      </div>
      {lines === null ? <p className="hint">{t('Loading…')}</p>
        : lines.length ? <pre className="mail-log">{lines.join('\n')}</pre>
          : <p className="hint">{query.q ? t('No line matches.') : t('The log is empty.')}</p>}
    </>;
  }











  function renderDemoAccounts() {
    const candidates = demoSettings?.candidates || { admin: [], reseller: [], customer: [] };
    const defaultSlot = { username: '', password: '' };
    const setSlot = (slot, field, value) => setDemoDraft(prev => ({
      ...prev,
      [slot]: { ...(prev[slot] || defaultSlot), [field]: value },
    }));
    const demoSlots = [
      ['admin', 'Admin', candidates.admin || []],
      ['reseller', 'Reseller', candidates.reseller || []],
      ['customer', 'End User', candidates.end_user || candidates.customer || []],
    ];
    return <div className="demo-accounts">
      <strong>{t('Demo accounts')}</strong>
      <p className="hint">{t('Visitors sign in with these from the login page across 3 roles (Admin, Reseller, End User). The passwords are shown to everyone, so pick accounts that exist only for the demo.')}</p>
      {demoSlots.map(([slot, label, slotCandidates]) => {
        const draftSlot = demoDraft[slot] || defaultSlot;
        return <div className="demo-account-row" key={slot}>
          <label><span>{t(label)}</span>
            <select value={draftSlot.username} disabled={!!loading} onChange={e => setSlot(slot, 'username', e.target.value)}>
              <option value="">{t('Not offered')}</option>
              {/* Not yourself - the server refuses it - unless you are looking at it from the
                  demo account itself, which must still show what is chosen. */}
              {slotCandidates.filter(name => name !== currentUser?.username || name === draftSlot.username).map(name => <option key={name} value={name}>{name}</option>)}
            </select>
          </label>
          <label><span>{t('Password shown on the login page')}</span>
            <input value={draftSlot.password} disabled={!!loading || !draftSlot.username} onChange={e => setSlot(slot, 'password', e.target.value)} placeholder={t('At least 6 characters')} autoComplete="off" spellCheck={false} data-lpignore="true" data-1p-ignore="true"/>
          </label>
        </div>;
      })}
      <div className="addon-actions">
        <button disabled={!!loading} onClick={saveDemoAccounts}><Save size={14}/>{t('Save demo accounts')}</button>
      </div>
    </div>;
  }

  function renderChmodDialog() {
    const targets = chmodTarget || [];
    if (targets.length === 0) return null;
    const bits = octalToPermissionBits(chmodMode);
    const onlyDirs = targets.every(item => item.is_dir);
    const hasFiles = targets.some(item => !item.is_dir);
    const worldWritable = !!(bits.other & 2);
    const setBit = (classKey, bitValue) => setChmodMode(permissionBitsToOctal({
      ...bits,
      [classKey]: bits[classKey] ^ bitValue,
    }));
    const title = targets.length === 1 ? targets[0].name : `${targets.length} selected items`;
    return <div className="chmod-backdrop" role="presentation" onClick={() => setChmodTarget(null)}>
      <div className="chmod-dialog" role="dialog" aria-modal="true" aria-label={t('Change permissions')} onClick={e => e.stopPropagation()}>
        <div className="chmod-head">
          <div>
            <h3><Lock size={15} />{t('Permissions')}</h3>
            <p>{title}</p>
          </div>
          <button className="mini secondary-light" onClick={() => setChmodTarget(null)} aria-label={t('Close')}><X size={14} /></button>
        </div>

        <table className="chmod-grid">
          <thead>
            <tr><th scope="col"></th>{PERMISSION_BITS.map(bit => <th scope="col" key={bit.key}>{bit.label}</th>)}</tr>
          </thead>
          <tbody>
            {PERMISSION_CLASSES.map(group => <tr key={group.key}>
              <th scope="row">{group.label}</th>
              {PERMISSION_BITS.map(bit => <td key={bit.key}>
                <input
                  type="checkbox"
                  aria-label={`${group.label} ${bit.label}`}
                  checked={!!(bits[group.key] & bit.value)}
                  onChange={() => setBit(group.key, bit.value)}
                />
              </td>)}
            </tr>)}
          </tbody>
        </table>
        <div className="chmod-value">
          <label>
            <span>{t('Octal')}</span>
            <input value={chmodMode} inputMode="numeric" maxLength={4} onChange={e => setChmodMode(e.target.value.replace(/[^0-7]/g, '').slice(0, 4))} />
          </label>
          <code>{permissionSymbols(chmodMode)}</code>
        </div>

        <div className="chmod-presets">
          {(onlyDirs ? PERMISSION_PRESETS.dir : PERMISSION_PRESETS.file).map(([preset, label]) => <button
            key={preset}
            type="button"
            className={`mini ${chmodMode === preset ? '' : 'secondary-light'}`}
            onClick={() => setChmodMode(preset)}
          >{preset} <small>{t(label)}</small></button>)}
        </div>

        {onlyDirs && <label className="chmod-setgid">
          <input
            type="checkbox"
            checked={bits.special === 2}
            onChange={() => setChmodMode(permissionBitsToOctal({ ...bits, special: bits.special === 2 ? 0 : 2 }))}
          />
          <span>{t('Setgid — new files inside keep the folder\'s group. spanel sets this on site folders; leave it on unless you know otherwise.')}</span>
        </label>}
        {worldWritable && <p className="chmod-note warn">
          <AlertCircle size={13} /> {hasFiles
            ? t('World-writable: anyone with an account on the server can change these files. Use 755 unless something really needs it.')
            : t('World-writable: anyone with an account on the server can change what is inside these folders. Use 755 unless something really needs it.')}
        </p>}
        <p className="chmod-note">
          {t('Any permission combination is allowed. The setuid and sticky bits are not — setgid on a folder is the only special bit the panel sets.')}
        </p>
        <div className="chmod-actions">
          <button className="secondary-light" disabled={!!loading} onClick={() => setChmodTarget(null)}>{t('Cancel')}</button>
          <button disabled={!!loading} onClick={applyChmod}><Check size={14} /> Apply {chmodMode}</button>
        </div>
      </div>
    </div>;
  }


























  function renderPackages() {
    if (!isAdmin && !isReseller) return <section className="section"><h2>{t('Packages')}</h2><p className="hint">{t('No permission.')}</p></section>;
    return <section className="section packages-page">
      <div className="section-title">
        <div><h2>{t('Packages')}</h2><p className="hint">{t('Create, edit, delete, and review reusable user limits.')}</p></div>
        <button className="secondary-light" disabled={!!loading} onClick={loadPackages}><RefreshCw size={14} />{t('Refresh')}</button>
      </div>
      <div className="user-create-card package-create-card">
        <label><span>{t('Package name')}</span><input value={newPackage.name} onChange={e => setNewPackage(prev => ({ ...prev, name: e.target.value }))} placeholder={t('Starter')} /></label>
        <label><span>{t('Site limit')}</span><input type="number" min="0" max="1000" value={newPackage.website_limit} onChange={e => setNewPackage(prev => ({ ...prev, website_limit: e.target.value }))} /></label>
        <label><span>{t('Storage MB')}</span><input type="number" min="0" max="1048576" value={newPackage.storage_limit_mb} onChange={e => setNewPackage(prev => ({ ...prev, storage_limit_mb: e.target.value }))} /></label>
        <label><span>{t('SFTP accounts')}</span><input type="number" min="0" max="100" value={newPackage.sftp_accounts_limit} onChange={e => setNewPackage(prev => ({ ...prev, sftp_accounts_limit: e.target.value }))} /></label>
        {mailAddonInstalled && <label><span>{t('Mailboxes')}</span><input type="number" min="0" max="1000" value={newPackage.mail_accounts_limit} onChange={e => setNewPackage(prev => ({ ...prev, mail_accounts_limit: e.target.value }))} /></label>}
        {isAdmin && <label className="check-line"><input type="checkbox" checked={newPackage.is_reseller_package} onChange={e => setNewPackage(prev => ({ ...prev, is_reseller_package: e.target.checked }))} />{t('Is Reseller Package')}</label>}
        {isAdmin && newPackage.is_reseller_package && <>
          <label><span>{t('Reseller User Limit')}</span><input type="number" min="0" max="10000" value={newPackage.reseller_user_limit} onChange={e => setNewPackage(prev => ({ ...prev, reseller_user_limit: e.target.value }))} /></label>
          <label className="check-line"><input type="checkbox" checked={newPackage.reseller_overselling_enabled} onChange={e => setNewPackage(prev => ({ ...prev, reseller_overselling_enabled: e.target.checked }))} />{t('Allow Overselling')}</label>
        </>}
        <button disabled={!!loading || !newPackage.name.trim()} onClick={createPackage}><Plus size={14} />{t('Create package')}</button>
      </div>
      <div className="package-list">
        {packages.length === 0 && <EmptyState icon={HardDrive} message={t('No packages found.')} />}
        {packages.map(item => <div className="package-row" key={item.id}>
          {String(editingPackageId) === String(item.id) ? <>
            <label><span>{t('Name')}</span><input value={editingPackageForm.name} onChange={e => setEditingPackageForm(prev => ({ ...prev, name: e.target.value }))} /></label>
            <label><span>{t('Site limit')}</span><input type="number" min="0" max="1000" value={editingPackageForm.website_limit} onChange={e => setEditingPackageForm(prev => ({ ...prev, website_limit: e.target.value }))} /></label>
            <label><span>{t('Storage MB')}</span><input type="number" min="0" max="1048576" value={editingPackageForm.storage_limit_mb} onChange={e => setEditingPackageForm(prev => ({ ...prev, storage_limit_mb: e.target.value }))} /></label>
            <label><span>{t('SFTP accounts')}</span><input type="number" min="0" max="100" value={editingPackageForm.sftp_accounts_limit} onChange={e => setEditingPackageForm(prev => ({ ...prev, sftp_accounts_limit: e.target.value }))} /></label>
            {mailAddonInstalled && <label><span>{t('Mailboxes')}</span><input type="number" min="0" max="1000" value={editingPackageForm.mail_accounts_limit} onChange={e => setEditingPackageForm(prev => ({ ...prev, mail_accounts_limit: e.target.value }))} /></label>}
            {isAdmin && <label className="check-line"><input type="checkbox" checked={editingPackageForm.is_reseller_package} onChange={e => setEditingPackageForm(prev => ({ ...prev, is_reseller_package: e.target.checked }))} />{t('Is Reseller Package')}</label>}
            {isAdmin && editingPackageForm.is_reseller_package && <>
              <label><span>{t('Reseller User Limit')}</span><input type="number" min="0" max="10000" value={editingPackageForm.reseller_user_limit} onChange={e => setEditingPackageForm(prev => ({ ...prev, reseller_user_limit: e.target.value }))} /></label>
              <label className="check-line"><input type="checkbox" checked={editingPackageForm.reseller_overselling_enabled} onChange={e => setEditingPackageForm(prev => ({ ...prev, reseller_overselling_enabled: e.target.checked }))} />{t('Allow Overselling')}</label>
            </>}
            <div className="row-actions">
              <button className="mini secondary-light" onClick={cancelEditingPackage}>{t('Cancel')}</button>
              <button className="mini" disabled={!!loading || !editingPackageForm.name.trim()} onClick={() => updatePackage(item.id)}><Save size={14} />{t('Save')}</button>
            </div>
          </> : <>
            <div className="user-main"><strong>{item.name}</strong><small>{item.website_limit} sites - {item.storage_limit_mb} MB {item.is_reseller_package ? '- Reseller' : ''}</small></div>
            <span className="user-metric"><Globe size={13} />{item.website_limit} sites</span>
            <span className="user-metric"><HardDrive size={13} />{item.storage_limit_mb} MB</span>
            {mailAddonInstalled && <span className="user-metric"><Mail size={13} />{t('{n} mailboxes', { n: item.mail_accounts_limit ?? 10 })}</span>}
            <div className="row-actions">
              <button className="mini secondary-light" disabled={!!loading} onClick={() => startEditingPackage(item)}><Pencil size={14} />{t('Edit')}</button>
              <button className="mini danger" disabled={!!loading || users.some(user => user.package_id === item.id)} onClick={() => deletePackage(item)}><Trash2 size={14} /></button>
            </div>
          </>}
        </div>)}
      </div>
    </section>;
  }



  function renderStandaloneEditor() {
    const editorLineCount = Math.max(1, String(fileContent || '').split('\n').length);
    const editorMode = editorLanguage(filePath);
    const siteLabel = currentSite?.domain || (selectedWebsiteId ? `Website #${selectedWebsiteId}` : 'Website');
    return <main className="standalone-editor-page">
      <header className="standalone-editor-top">
        <div className="standalone-editor-title">
          <strong>{filePath || 'No file selected'}</strong>
          <span>{siteLabel}</span>
        </div>
        <div className="standalone-editor-actions">
          <span className="editor-chip">{editorMode}</span>
          <span className="editor-chip">{t('{n} line(s)', { n: editorLineCount })}</span>
          <span className="editor-chip">Ln {editorCursor.line}, Col {editorCursor.column}</span>
          <button className="secondary" disabled={!selectedWebsiteId || !!loading} onClick={() => readFile(filePath)}><RefreshCw size={14} />{t('Reload')}</button>
          <button disabled={!selectedWebsiteId || !!loading} onClick={writeFile}>{t('Save')}</button>
          <button disabled={!selectedWebsiteId || !filePath || !!loading} onClick={() => downloadFile(filePath)}><Download size={14} /></button>
          <button className="secondary-light" onClick={() => window.close()}><X size={14} />{t('Close')}</button>
        </div>

      </header>
      {loading && <div className="loading">{t(loading)}</div>}
      {renderNotifications()}
      <section className="standalone-editor-body">
        <CodeEditor
          value={fileContent}
          mode={editorMode}
          disabled={!selectedWebsiteId}
          onChange={setFileContent}
          onCursorChange={setEditorCursor}
        />
      </section>
    </main>;
  }

  // --- AI assistants (MCP) --------------------------------------------------

  async function copyText(text, message) {
    try {
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(text);
      } else {
        const holder = document.createElement('textarea');
        holder.value = text;
        holder.style.position = 'fixed';
        holder.style.opacity = '0';
        document.body.appendChild(holder);
        holder.select();
        document.execCommand('copy');
        document.body.removeChild(holder);
      }
      setNotice(message || 'Copied.');
    } catch {
      setError(t('Copy failed. Select the text and press Ctrl+C.'));
    }
  }












  const sharedContextValue = {
    ...baseContext,
    websites: roleScopedWebsites,
    request, API, clearSession, handleAuthExpired, formatApiError, readCookie,
    copyText, copiedField, setCopiedField, generateRandomPassword,
    loadCurrentUser, loadUsers, loadPackages, loadDatabases, loadPanelSettings, loadSiteApps, loadSiteRuntimes, loadPhpVersions, loadAddons, loadDemoSettings, loadCfZone, refreshAll,
    navigateToPage, page, setPage, isAdmin, isReseller, isUser, effectiveRole, actualRole, viewLevel, setViewLevel,
    storageUsageText, formatFileTime, dnsTtlLabel, splitAddresses, normalizeHttpFloodConfig, isProxiedAppType, composeWebPorts,
    openWebsiteTerminal, openWebsiteFileManager, websiteUrl, websiteConfigForm, enableSsl, assignDomainToUser, submitPasswordChange,
    validateCompose, installDockerEngine, pruneDocker, installNodeMajor,
    installLmd, showMalwareScanJob, startClamavDaemon,
    toggleWafDefaultRule, loadBotBlocks, saveGlobalBots, addGlobalBots, updateWafAccessLogFilters, exportWafAccessLogs, accessLogBadgeClass, accessLogVerdictLabel, formatAccessLogTime, accessLogCountryLabel, csvCell,
    saveCatchAll, editRelay, saveRelay, deleteRelay, setDefaultRelay, testRelay, relayTlsLabel, renderRelayForm, loadRspamdStat, loadRspamdHistory, loadRspamdLog, loadEximLog, rspamdActionLabel, renderLogViewer, saveDomainRelay,
    renderCopyBlock, renderDnsRecordEditor, renderDemoAccounts, dnsPath, emptyDnsRecord, sortPhpVersions, currentPanelHost, currentPanelPort, upsertFileJob, dismissFileJob,
    listFiles, readFile, writeFile, downloadFile, openFileEditorTab, openChmodDialog,
    WEBSITE_MODES, WAF_ACCESS_LOG_DEFAULTS, EMPTY_SITE_APP_DRAFT,
    appsFeatureEnabled, dnsAddonInstalled, mailAddonInstalled, malwareAddonInstalled, mcpAddonInstalled, notificationsAddonInstalled,
    websiteList, setWebsiteList, websiteSearch, setWebsiteSearch, websiteSearching, setWebsiteSearching, appVersion, standaloneEditor,
    fileAppId, setFileAppId, fileListPath, setFileListPath, files, setFiles, fileJobs, setFileJobs, fileUploadDir, setFileUploadDir, selectedFilePaths, setSelectedFilePaths, fileContent, setFileContent, fileSearch, setFileSearch,
    domain, setDomain, adminEmail, setAdminEmail, wpAdminUser, setWpAdminUser, wpAdminPassword, setWpAdminPassword, siteType, setSiteType, installWordPress, setInstallWordPress, createFormOpen, setCreateFormOpen, createSslMode, setCreateSslMode, createSslToken, setCreateSslToken, createSiteAppId, setCreateSiteAppId, wordpressInstaller, setWordpressInstaller, aliasDrafts, setAliasDrafts, aliasModes, setAliasModes, createWordPress,
    assignWebsiteId, setAssignWebsiteId, assignUserId, setAssignUserId,
    panelSettings, setPanelSettings, panelSettingsTab, setPanelSettingsTab,
    cfZone, setCfZone, wildcardToken, setWildcardToken, sharedSource, setSharedSource, sslMode, setSslMode, sslSources, setSslSources, manualSslForm, setManualSslForm, manualSslFiles, setManualSslFiles,
    catchAllDraft, setCatchAllDraft, relayForm, setRelayForm, relayTestTo, setRelayTestTo, relayTestLines, setRelayTestLines, rspamdView, setRspamdView, rspamdStat, setRspamdStat, rspamdHistory, setRspamdHistory, rspamdFilter, setRspamdFilter, rspamdLog, setRspamdLog, rspamdLogQuery, setRspamdLogQuery, eximLog, setEximLog, eximLogQuery, setEximLogQuery, mailDns, setMailDns, mailRelays, setMailRelays, mailTab, setMailTab, mailPage, setMailPage, mailFilter, setMailFilter,
    wafRules, setWafRules, wafCustomRules, setWafCustomRules, selectedWafWebsiteId, setSelectedWafWebsiteId, wafSiteConfig, setWafSiteConfig, botBlocks, setBotBlocks, globalBots, setGlobalBots, wafAccessLogFilters, setWafAccessLogFilters, wafAccessLogs, setWafAccessLogs,
    scanTargetWebsiteId, setScanTargetWebsiteId, malwareScanStatus, setMalwareScanStatus, malwareSchedules, setMalwareSchedules, malwareSchedulesForm, setMalwareSchedulesForm, incrementalDays, setIncrementalDays, scanJob, setScanJob, scanJobs, setScanJobs, scanLoading, setScanLoading, scanResults, setScanResults,
    mcpDraft, setMcpDraft, mcpNewToken, setMcpNewToken,
    composePlan, setComposePlan, siteRuntimes, setSiteRuntimes,
    daFileInputRef, migratePanelType, setMigratePanelType,
    editingUserForm, setEditingUserForm, newUser, setNewUser, userTab, setUserTab,
  };

  function renderPage() {
    if (!isPageAllowedForRole(page, effectiveRole)) {
      return renderDashboard();
    }
    if (page === 'websites' || page === 'subdomains') return <WebsitesPage
      mode={page === 'subdomains' ? 'subdomains' : 'domains'}
      request={request} API={API} setPage={setPage} phpVersions={phpVersions} isReseller={isReseller}
      fileAppId={fileAppId} setFileAppId={setFileAppId} setFileListPath={setFileListPath}
      websiteConfigForm={websiteConfigForm} standaloneEditor={standaloneEditor}
      domain={domain} setDomain={setDomain}
      adminEmail={adminEmail} setAdminEmail={setAdminEmail}
      siteType={siteType} setSiteType={setSiteType}
      installWordPress={installWordPress} setInstallWordPress={setInstallWordPress}
      createFormOpen={createFormOpen} setCreateFormOpen={setCreateFormOpen}
      createSslMode={createSslMode} setCreateSslMode={setCreateSslMode}
      createSslToken={createSslToken} setCreateSslToken={setCreateSslToken}
      createSiteAppId={createSiteAppId} setCreateSiteAppId={setCreateSiteAppId}
      wordpressInstaller={wordpressInstaller} setWordpressInstaller={setWordpressInstaller}
      aliasDrafts={aliasDrafts} setAliasDrafts={setAliasDrafts}
      aliasModes={aliasModes} setAliasModes={setAliasModes}
      createWordPress={createWordPress}
      refreshAll={refreshAll}
      navigateToPage={navigateToPage}
      WEBSITE_MODES={WEBSITE_MODES}
      appsFeatureEnabled={appsFeatureEnabled}
      siteApps={siteApps}
      websiteUrl={websiteUrl}
      openWebsiteFileManager={openWebsiteFileManager}
      loadSiteApps={loadSiteApps}
    />;
    if (page === 'addons') return <AddonsPage request={request} isAdmin={isAdmin} addons={addons} setAddons={setAddons} loadAddons={loadAddons} />;
    // DNS Manager (2026-09-29): administrators and customers, once installed.
    if (page === 'dns') return dnsAddonInstalled ? <DnsPage request={request} isAdmin={isAdmin} users={users} emptyDnsRecord={emptyDnsRecord} renderCopyBlock={renderCopyBlock} /> : renderAddonMissing('dns');
    if (page === 'server_tls') return <ServerTlsPage request={request} isAdmin={isAdmin} />;
    if (page === 'brute_force') return <BruteForceMonitorPage request={request} isAdmin={isAdmin} />;
    if (page === 'process_monitor') return <ProcessMonitorPage request={request} isAdmin={isAdmin} />;
    if (page === 'name_servers') return <NameServersPage request={request} isAdmin={isAdmin} />;
    if (page === 'message_all') return <MessageAllPage request={request} isAdmin={isAdmin} />;
    if (page === 'mail_forwarders') return mailAddonInstalled ? <MailForwardersPage request={request} isAdmin={isAdmin} /> : renderAddonMissing('mail');
    if (page === 'mail_auto') return mailAddonInstalled ? <MailAutorespondersPage request={request} isAdmin={isAdmin} /> : renderAddonMissing('mail');
    if (page === 'mail') return mailAddonInstalled ? <MailPage request={request} API={API} isAdmin={isAdmin} users={users} isReseller={isReseller} renderCopyBlock={renderCopyBlock} renderDnsRecordEditor={renderDnsRecordEditor} dnsTtlLabel={dnsTtlLabel} setPanelSettingsTab={setPanelSettingsTab} setPage={setPage} /> : renderAddonMissing('mail');
    // Reachable by URL after the addon is removed, so it answers for itself
    // rather than rendering a page whose every request would be refused.
    if (page === 'wordpress_manager') return <WordPressManagerPage request={request} websites={roleScopedWebsites} openWebsiteFileManager={openWebsiteFileManager} navigateToPage={navigateToPage} />;
    if (page === 'app_installer') return <AppInstallerPage request={request} websites={roleScopedWebsites} navigateToPage={navigateToPage} />;
    if (page === 'applications') return appsFeatureEnabled ? <ApplicationsPage request={request} API={API} setPage={setPage} isReseller={isReseller} fileAppId={fileAppId} setFileAppId={setFileAppId} setFileListPath={setFileListPath} EMPTY_SITE_APP_DRAFT={EMPTY_SITE_APP_DRAFT} loadSiteApps={loadSiteApps} /> : renderAddonMissing('application');
    if (page === 'ssl') return <SslPage request={request} loadCfZone={loadCfZone} refreshAll={refreshAll} enableSsl={enableSsl} />;
    if (page === 'databases') return <DatabasesPage request={request} API={API} navigateToPage={navigateToPage} refreshAll={refreshAll} />;
    if (page === 'sftp') return <SftpPage request={request} currentUser={currentUser} appVersion={appVersion} />;
    if (page === 'cron') return <CronPage request={request} isAdmin={isAdmin} mode="site" />;
    if (page === 'all_crons') return isAdmin ? <CronPage request={request} isAdmin={isAdmin} mode="all" /> : renderAdminOnly();
    if (page === 'server_statistics' || page === 'site_stats') return <ServerStatisticsPage request={request} isAdmin={isAdmin} isReseller={isReseller} page={page} />;
    if (page === 'redirects') return <RedirectsPage request={request} />;
    if (page === 'apache_handlers') return <ApacheHandlersPage request={request} phpVersions={phpVersions} />;
    if (page === 'files') return <FilesPage request={request} API={API} navigateToPage={navigateToPage} standaloneEditor={standaloneEditor} />;
    if (page === 'backups') return <BackupsPage request={request} isAdmin={isAdmin} isReseller={isReseller} API={API} csvCell={csvCell} />;
    if (page === 'security') return <SecurityPage request={request} currentUser={currentUser} setCurrentUser={setCurrentUser} />;
    if (page === 'php') return <PhpConfigPage request={request} isAdmin={isAdmin} loadPhpVersions={loadPhpVersions} refreshAll={refreshAll} sortPhpVersions={sortPhpVersions} />;
    if (page === 'firewall') return <FirewallPage request={request} isAdmin={isAdmin} addons={addons} />;
    if (page === 'waf' || page === 'waf-site' || page === 'waf-logs' || page === 'access-logs') return <WafPage page={page} request={request} API={API} setPage={setPage} isReseller={isReseller} isAdmin={isAdmin} WAF_ACCESS_LOG_DEFAULTS={WAF_ACCESS_LOG_DEFAULTS} HTTP_FLOOD_DEFAULTS={HTTP_FLOOD_DEFAULTS} websites={websiteList.length ? websiteList : websites} addons={addons} />;

    // An addon since 2026-09-27: the page is there once it is installed.
    if (page === 'malware' || page === 'malware-scan') return malwareAddonInstalled ? <MalwarePage page={page} request={request} API={API} setPage={setPage} isAdmin={isAdmin} isReseller={isReseller} /> : renderAddonMissing('malware');
    if (page === 'updates') return <UpdatesPage request={request} isAdmin={isAdmin} />;
    // Reachable by URL, so it answers for itself rather than firing a
    // page full of requests that will every one be refused.
    if (page === 'services') return isAdmin ? <ServicesPage request={request} isAdmin={isAdmin} /> : renderAdminOnly();
    if (page === 'mcp') return (mcpAddonInstalled || isAdmin) ? <McpPage request={request} API={API} isAdmin={isAdmin} currentUser={currentUser} setNotice={setNotice} mcpAddonInstalled={mcpAddonInstalled} /> : renderAddonMissing('mcp');
    if (page === 'move_users') return isAdmin ? <MoveUsersPage request={request} isAdmin={isAdmin} /> : renderAdminOnly();
    if (page === 'message_templates') return <MessageTemplates request={request} />;
    if (page === 'change_passwords') return <ChangePasswords request={request} />;
    if (page === 'custom_httpd') return isAdmin ? <CustomHttpdPage request={request} websites={websites} /> : renderAdminOnly();
    if (page === 'custombuild') return isAdmin ? <CustomBuildPage request={request} isAdmin={isAdmin} loadPhpVersions={loadPhpVersions} loadAddons={loadAddons} /> : renderAdminOnly();
    if (page === 'maintenance') return isAdmin ? <MaintenancePage request={request} isAdmin={isAdmin} websites={websites} users={users} loadUsers={loadUsers} refreshAll={refreshAll} /> : renderAdminOnly();
    // Administrators only (operator, 2026-09-27): customers get no notifications.
    if (page === 'notifications') return !isAdmin ? renderAdminOnly() : notificationsAddonInstalled ? renderNotificationsPage() : renderAddonMissing('notifications');
    if (page === 'settings' || page === 'panel-settings') return <SettingsPage request={request} isAdmin={isAdmin} loadPanelSettings={loadPanelSettings} currentPanelHost={currentPanelHost} />;
    const usersProps = { request, isAdmin, isReseller, mailAddonInstalled, loadUsers, loadPackages, websites, refreshAll, assignWebsiteId, setAssignWebsiteId, assignUserId, setAssignUserId, assignDomainToUser };
    if (page === 'users') return <UsersPage {...usersProps} mode="users" />;
    if (page === 'my_users' || page === 'suspend') return <UsersPage {...usersProps} mode="my_users" />;
    if (page === 'resource_monitor') return <ResourceMonitorPage request={request} isAdmin={isAdmin} isReseller={isReseller} />;
    if (page === 'resellers') return <UsersPage {...usersProps} mode="resellers" />;
    if (page === 'administrators') return <UsersPage {...usersProps} mode="administrators" />;


    if (page === 'reseller_settings') return <ResellerSettingsPage request={request} API={API} panelSettings={panelSettings} setPanelSettings={setPanelSettings} loadPanelSettings={loadPanelSettings} />;
    if (page === 'ipmanager') return <IpManager request={request} isAdmin={isAdmin} isReseller={isReseller} users={users} />;
    if (page === 'packages') return <Packages request={request} isAdmin={isAdmin} isReseller={isReseller} mailAddonInstalled={mailAddonInstalled} users={users} mode="users" />;
    if (page === 'reseller_packages') return <Packages request={request} isAdmin={isAdmin} isReseller={isReseller} mailAddonInstalled={mailAddonInstalled} users={users} mode="resellers" />;
    if (page === 'migration') return <Migration request={request} setError={setError} setNotice={setNotice} loading={loading} />;
    if (page === 'multi_server') return <MultiServerPage request={request} isAdmin={isAdmin} />;
    if (page === 'licensing') return <LicensingPage request={request} isAdmin={isAdmin} appVersion={appVersion} />;
    if (page !== 'dashboard') {
      return <div className="section" style={{ textAlign: 'center', padding: '100px 20px', color: 'var(--text-secondary)' }}>
        <Tool size={48} style={{ opacity: 0.2, margin: '0 auto 20px' }} />
        <h2>Under Construction</h2>
        <p>This DirectAdmin module is currently being ported to SPanel.</p>
        <button className="primary" style={{ marginTop: '20px' }} onClick={() => navigateToPage('dashboard')}>Return to Dashboard</button>
      </div>;
    }
    return renderDashboard();
  }

  // Login screen
  if (bootstrapping) {
    return <main className="login-page" style={panelSettings?.login_background_url ? { backgroundImage: `url(${panelSettings.login_background_url})`, backgroundSize: "cover", backgroundPosition: "center" } : {}}>
      <section className="login-card">
        <div className="login-brand">{renderBrandMark('login-brand-mark')}<div><p className="eyebrow">{panelSettings.app_name || 'spanel'}</p><h1>{t('Loading…')}</h1></div></div>
      </section>
    </main>;
  }

  if (!isAuthenticated) {
    return <main className="login-page" style={panelSettings?.login_background_url ? { backgroundImage: `url(${panelSettings.login_background_url})`, backgroundSize: "cover", backgroundPosition: "center" } : {}}>
      <section className="login-card">
        <div className="login-card-head">
          <div className="login-brand">
            {renderBrandMark('login-brand-mark')}
            <div>
              <p className="eyebrow">{t('Server Management Panel')}</p>
              <h1>{panelSettings.app_name || 'spanel'}</h1>
            </div>
          </div>
          <LanguageToggle language={language} onChange={changeLanguage} />
          <ThemeToggle theme={theme} onToggle={toggleTheme} />
        </div>
        <div className="login-form">
          <input value={username} onChange={e => setUsername(e.target.value)} placeholder={t('Username')} autoComplete="username" />
          <input value={password} onChange={e => setPassword(e.target.value)} placeholder={t('Password')} type="password" autoComplete="current-password" onKeyDown={e => { if (e.key === 'Enter') login(); }} />
          {passkeyPrompt && !needsTwoFactor && <div className="info-box">
            <p className="hint">{t('Touch your passkey to sign in.')}</p>
            <div className="site-app-form-actions">
              <button disabled={!!loading} onClick={() => usePasskey(passkeyPrompt.options)}><KeyRound size={14} />{t('Try the passkey again')}</button>
              {/* A passkey belongs to one hostname. Reaching the panel by
                  another name offers nothing, so the app has to stay in reach. */}
              {passkeyPrompt.canUseOtp && <button className="secondary-light" disabled={!!loading} onClick={() => { setNeedsTwoFactor(true); setPasskeyPrompt(null); setNotice(t('Enter the code from your authenticator app.')); }}>{t('Use a code instead')}</button>}
            </div>
          </div>}
          {needsTwoFactor && <input value={otpCode} onChange={e => setOtpCode(e.target.value)} placeholder={t('Authentication code')} inputMode="numeric" autoComplete="one-time-code" onKeyDown={e => { if (e.key === 'Enter') login(); }} />}
          <label className="login-remember">
            <input type="checkbox" checked={rememberMe} onChange={e => setRememberMe(e.target.checked)} />{t('Keep me signed in for 30 days')}</label>
          {/* Not onClick={login}: that hands login() the click event as its
              passkey argument, so every click sent passkey=[object Object]
              and an account without two-factor was refused with "no second
              factor configured". Only Enter in a field ever worked. */}
          <button disabled={!!loading || !username || !password} onClick={() => login()}>{loading ? t('Logging in...') : t('Login')}</button>
        </div>
        {demoAccess.enabled && <div className="demo-login">
          <p className="demo-login-title"><Eye size={15} /><strong>{t('Demo')}</strong><span>{t('Look around without changing anything.')}</span></p>
          {demoAccess.accounts.map(account => <div className="demo-login-row" key={account.slot}>
            <div className="demo-login-account">
              <strong>{t(account.slot === 'admin' ? 'Admin' : account.slot === 'reseller' ? 'Reseller' : 'End User')}</strong>
              <code>{account.username} / {account.password}</code>
            </div>
            <button className="secondary-light" disabled={!!loading} onClick={() => { setViewLevel(null); login('', { username: account.username, password: account.password }); }}><LogIn size={14}/>{t('Sign in')}</button>
          </div>)}
        </div>}
      </section>
      {renderNotifications()}
    </main>;
  }

  if (standaloneEditor && isUser) return <AppContext.Provider value={sharedContextValue}>{renderStandaloneEditor()}</AppContext.Provider>;

  const ActiveIcon = pageItem?.[2] || Home;

  return <AppContext.Provider value={sharedContextValue}><main className="app-shell">
    <section className="layout">
      {mobileMenuOpen && <div className="mobile-nav-backdrop" onClick={() => setMobileMenuOpen(false)} aria-hidden="true"></div>}
      <aside className={`sidebar ${mobileMenuOpen ? 'open' : ''}`} role="navigation" aria-label={t('Main navigation')}>
        <div className="sidebar-head">
          <div className="sidebar-brand" onClick={() => navigateToPage("dashboard")} style={{ cursor: "pointer" }} title="Dashboard">
            {renderBrandMark()}
            <div>
              <strong>{panelSettings.app_name || 'spanel'}</strong>
              <small>{t('Server Panel')}</small>
            </div>
          </div>
          <button className="sidebar-close" onClick={() => setMobileMenuOpen(false)} aria-label={t('Close menu')}><X size={18} /></button>
        </div>
        <nav className="sidebar-nav">
          {navSections.map(section => <div className="sidebar-section" key={section.key}>
            {section.title && <p className="sidebar-section-title">{t(section.title)}</p>}
            {section.items.map(([key, label, Icon]) => {
              const isUnimplemented = UNIMPLEMENTED_NAV_KEYS.includes(key);

              if (key === 'create_ticket') {
                const ticketUrl = (panelSettings?.ticket_url || '').trim();
                const ticketHref = ticketUrl ? (/^https?:\/\//i.test(ticketUrl) ? ticketUrl : `https://${ticketUrl}`) : '';
                if (ticketHref) {
                  return (
                    <a
                      key={key}
                      href={ticketHref}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="sidebar-link-btn"
                      title={ticketHref}
                    >
                      <Icon size={16} /><span>{t(label)}</span>
                      <ExternalLink size={13} style={{ marginLeft: 'auto', opacity: 0.6 }} />
                    </a>
                  );
                } else {
                  return (
                    <button
                      key={key}
                      type="button"
                      className={navPage === key ? 'active' : ''}
                      onClick={() => setNotice(t('Support Ticket URL is not configured. Administrators can set it in Settings.'))}
                      aria-current={navPage === key ? 'page' : undefined}
                      style={{ opacity: 0.5 }}
                      title={t('Support Ticket URL is not configured. Administrators can set it in Settings.')}
                    >
                      <Icon size={16} /><span>{t(label)}</span>
                    </button>
                  );
                }
              }

              return (
                <button
                  key={key}
                  type="button"
                  className={navPage === key ? 'active' : ''}
                  onClick={() => handleNavSelect(key)}
                  aria-current={navPage === key ? 'page' : undefined}
                  style={isUnimplemented ? { opacity: 0.5 } : {}}
                  title={isUnimplemented ? t('Coming soon') : ''}
                >
                  <Icon size={16} /><span>{t(label)}</span>
                </button>
              );
            })}
          </div>)}
        </nav>
        <div className="sidebar-footer-row">
          {appVersion && <div className="sidebar-version">v{appVersion}</div>}
          <button
            type="button"
            className="sidebar-donate-btn"
            onClick={() => {
              setMobileMenuOpen(false);
              setDonateModalOpen(true);
            }}
            title={t('Donate to SPanel')}
            aria-label={t('Donate to SPanel')}
          >
            <Heart size={14} fill="currentColor" className="donate-heart-icon" />
          </button>
        </div>
      </aside>
      <div className="content">
        {currentUser?.impersonated && currentUser?.impersonated_by ? (
          <div className="impersonation-banner">
            <div className="banner-text">
              <span className="banner-icon">⚠️</span>
              <span>{t('You are currently logged in as')} <strong>{currentUser.username}</strong> ({t(roleLabel(actualRole))}).</span>
            </div>
            <button type="button" onClick={() => exitImpersonation()}>{t('Return to')} {currentUser.impersonated_by}</button>
          </div>
        ) : effectiveRole !== actualRole && (
          <div className="impersonation-banner">
            <div className="banner-text">
              <span className="banner-icon">⚠️</span>
              <span>You are currently viewing the panel as <strong>{effectiveRole.toUpperCase()}</strong>.</span>
            </div>
            <button type="button" onClick={() => { setViewLevel(actualRole); navigateToPage('dashboard'); }}>Return to {actualRole.toUpperCase()}</button>
          </div>
        )}
        <section className="topbar">
          <div className="topbar-left">
            <button className="mobile-nav-toggle" onClick={() => setMobileMenuOpen(o => !o)} aria-expanded={mobileMenuOpen} aria-label={t('Toggle navigation')}>
              <Menu size={20} /><span><ActiveIcon size={17} />{t(pageItem?.[1] || 'Menu')}</span>
            </button>
            <div className="page-title">
              <h1>{pageItem?.[1] ? t(pageItem[1]) : (panelSettings.app_name || 'spanel')}</h1>
            </div>
          </div>
          {/* The page title, then one account menu - profile, account security
              and sign out live in it, as they do in OPanel. */}
          <div className="top-actions">
            {(canSwitchToAdmin || canSwitchToReseller) && (
              <div className="segmented role-switcher-seg" aria-label="Access Level">
                {canSwitchToAdmin && <button className={effectiveRole === 'admin' ? 'active' : ''} onClick={() => { setViewLevel('admin'); navigateToPage('dashboard'); }}>Admin</button>}
                {canSwitchToReseller && <button className={effectiveRole === 'reseller' ? 'active' : ''} onClick={() => { setViewLevel('reseller'); navigateToPage('dashboard'); }}>Reseller</button>}
                <button className={effectiveRole === 'user' ? 'active' : ''} onClick={() => { setViewLevel('user'); navigateToPage('dashboard'); }}>User</button>
              </div>
            )}

            <div className="top-utility-group">
              <button
                type="button"
                className="secondary-light mini"
                onClick={() => {
                  setCmdPaletteQuery('');
                  setCmdPaletteIndex(0);
                  setCmdPaletteOpen(true);
                }}
                title={`${t('Quick Search & Command Palette')} (Ctrl+K)`}
                aria-label={t('Quick Search & Command Palette')}
                style={{
                  display: 'inline-flex',
                  alignItems: 'center',
                  gap: '6px',
                  height: '32px',
                  minHeight: '32px',
                  padding: '0 10px',
                  borderRadius: '8px',
                  fontSize: '12px',
                  fontWeight: 600,
                }}
              >
                <Search size={14} />
                <span className="donate-trigger-label">{t('Quick Jump')}</span>
                <kbd
                  style={{
                    fontSize: '10.5px',
                    padding: '1px 5px',
                    borderRadius: '4px',
                    background: 'var(--surface-alt)',
                    border: '1px solid var(--border)',
                    color: 'var(--text-muted)',
                    fontFamily: 'monospace',
                  }}
                >
                  Ctrl+K
                </kbd>
              </button>
              <button
                type="button"
                className="donate-trigger-btn"
                onClick={() => setDonateModalOpen(true)}
                title={t('Donate to SPanel')}
                aria-label={t('Donate to SPanel')}
              >
                <Heart size={16} fill="currentColor" className="donate-heart-icon" />
              </button>

              <div className="user-menu" ref={notificationsRef}>
              <a href="#" className="theme-toggle" onClick={(e) => { e.preventDefault(); setNotificationsOpen(!notificationsOpen); }} title={t('Notifications')} style={{ position: 'relative' }}>
                <Bell size={16} />
                {systemNotifications.length > 0 && (
                  <span style={{ position: 'absolute', top: '-4px', right: '-4px', background: '#ef4444', color: '#fff', fontSize: '10px', fontWeight: 'bold', padding: '0 4px', height: '16px', minWidth: '16px', display: 'flex', alignItems: 'center', justifyContent: 'center', borderRadius: '99px', boxShadow: '0 1px 2px rgba(0,0,0,0.1)' }}>{systemNotifications.length}</span>
                )}
              </a>
              {notificationsOpen && (
                <div
                  className="user-menu-panel notifications-dropdown-panel"
                  style={{
                    width: '380px',
                    maxWidth: 'calc(100vw - 24px)',
                    padding: '0',
                    maxHeight: '440px',
                    overflowY: 'auto',
                    overflowX: 'hidden',
                    display: 'block',
                    background: theme === 'dark' ? '#1e293b' : '#ffffff',
                    boxShadow: '0 14px 34px rgba(15, 23, 42, 0.22)',
                  }}
                >
                  <div
                    className="user-menu-head"
                    style={{
                      borderBottom: '1px solid var(--border)',
                      padding: '14px 16px 12px',
                      margin: 0,
                      display: 'block',
                    }}
                  >
                    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '8px' }}>
                      <strong style={{ fontSize: '14px' }}>{t('Notifications')}</strong>
                      <span
                        style={{
                          fontSize: '10.5px',
                          fontWeight: 700,
                          textTransform: 'uppercase',
                          letterSpacing: '0.04em',
                          padding: '2px 8px',
                          borderRadius: '999px',
                          whiteSpace: 'nowrap',
                          background: isAdmin
                            ? 'rgba(239, 68, 68, 0.14)'
                            : isReseller
                            ? 'rgba(139, 92, 246, 0.14)'
                            : 'rgba(59, 130, 246, 0.14)',
                          color: isAdmin ? '#ef4444' : isReseller ? '#8b5cf6' : '#3b82f6',
                          border: `1px solid ${isAdmin ? 'rgba(239, 68, 68, 0.28)' : isReseller ? 'rgba(139, 92, 246, 0.28)' : 'rgba(59, 130, 246, 0.28)'}`,
                        }}
                      >
                        {isAdmin ? t('Admin Level') : isReseller ? t('Reseller Level') : t('User Level')}
                      </span>
                    </div>
                    <small
                      style={{
                        display: 'block',
                        marginTop: '4px',
                        color: 'var(--text-muted)',
                        whiteSpace: 'normal',
                        overflow: 'visible',
                        textOverflow: 'clip',
                        lineHeight: 1.4,
                      }}
                    >
                      {isAdmin
                        ? t('Server infrastructure, security & tenant alerts ({n})', { n: systemNotifications.length })
                        : isReseller
                        ? t('Customer accounts, quotas & reseller alerts ({n})', { n: systemNotifications.length })
                        : t('Your websites, SSL, quota & account alerts ({n})', { n: systemNotifications.length })}
                    </small>
                  </div>
                  {systemNotifications.length === 0 ? (
                    <div style={{ padding: '28px 20px', textAlign: 'center', color: 'var(--text-muted)' }}>
                      <CheckCircle size={24} style={{ color: '#10b981', margin: '0 auto 8px', opacity: 0.9 }} />
                      <div style={{ fontSize: '13px', fontWeight: 500 }}>
                        {isAdmin
                          ? t('All server systems & services are healthy.')
                          : isReseller
                          ? t('All managed customer accounts are healthy.')
                          : t('No new notifications for your account.')}
                      </div>
                    </div>
                  ) : (
                    <div style={{ padding: '10px 12px', display: 'flex', flexDirection: 'column', gap: '8px' }}>
                      {systemNotifications.map(item => {
                        const toneColor = item.tone === 'bad' ? '#ef4444' : item.tone === 'warn' ? '#f59e0b' : '#3b82f6';
                        const toneBg =
                          item.tone === 'bad'
                            ? theme === 'dark' ? 'rgba(239, 68, 68, 0.12)' : '#fef2f2'
                            : item.tone === 'warn'
                            ? theme === 'dark' ? 'rgba(245, 158, 11, 0.12)' : '#fffbeb'
                            : theme === 'dark' ? 'rgba(59, 130, 246, 0.12)' : '#eff6ff';
                        const toneBorder =
                          item.tone === 'bad'
                            ? theme === 'dark' ? 'rgba(239, 68, 68, 0.28)' : '#fecaca'
                            : item.tone === 'warn'
                            ? theme === 'dark' ? 'rgba(245, 158, 11, 0.3)' : '#fde68a'
                            : theme === 'dark' ? 'rgba(59, 130, 246, 0.28)' : '#bfdbfe';
                        return (
                          <div
                            key={`${item.category}-${item.text}`}
                            style={{
                              display: 'grid',
                              gridTemplateColumns: '28px minmax(0, 1fr) auto',
                              alignItems: 'center',
                              gap: '10px',
                              padding: '10px 12px',
                              borderRadius: '10px',
                              background: toneBg,
                              border: `1px solid ${toneBorder}`,
                              boxSizing: 'border-box',
                              width: '100%',
                            }}
                          >
                            <div
                              style={{
                                width: '28px',
                                height: '28px',
                                borderRadius: '8px',
                                display: 'flex',
                                alignItems: 'center',
                                justifyContent: 'center',
                                background: theme === 'dark' ? 'rgba(255, 255, 255, 0.06)' : 'rgba(255, 255, 255, 0.8)',
                                color: toneColor,
                                flexShrink: 0,
                              }}
                            >
                              <item.icon size={15} />
                            </div>
                            <div style={{ minWidth: 0, overflow: 'hidden' }}>
                              {item.category && (
                                <div style={{ marginBottom: '3px' }}>
                                  <span
                                    style={{
                                      display: 'inline-block',
                                      fontSize: '10px',
                                      fontWeight: 700,
                                      textTransform: 'uppercase',
                                      letterSpacing: '0.03em',
                                      padding: '1px 6px',
                                      borderRadius: '4px',
                                      background:
                                        item.tone === 'bad'
                                          ? 'rgba(239, 68, 68, 0.16)'
                                          : item.tone === 'warn'
                                          ? 'rgba(245, 158, 11, 0.18)'
                                          : 'rgba(59, 130, 246, 0.16)',
                                      color: toneColor,
                                    }}
                                  >
                                    {item.category}
                                  </span>
                                </div>
                              )}
                              <div
                                style={{
                                  fontSize: '12.5px',
                                  lineHeight: 1.4,
                                  color: 'var(--text)',
                                  overflowWrap: 'anywhere',
                                  wordBreak: 'normal',
                                  whiteSpace: 'normal',
                                }}
                              >
                                {item.text}
                              </div>
                            </div>
                            <button
                              type="button"
                              className="mini secondary"
                              style={{
                                width: 'auto',
                                minWidth: 'unset',
                                height: '28px',
                                minHeight: '28px',
                                padding: '0 10px',
                                fontSize: '12px',
                                fontWeight: 600,
                                whiteSpace: 'nowrap',
                                flexShrink: 0,
                                display: 'inline-flex',
                                alignItems: 'center',
                                justifyContent: 'center',
                                borderRadius: '6px',
                                background: theme === 'dark' ? '#0f172a' : '#ffffff',
                                color: 'var(--text)',
                                border: '1px solid var(--border)',
                                boxShadow: '0 1px 2px rgba(0, 0, 0, 0.05)',
                              }}
                              onClick={() => {
                                setNotificationsOpen(false);
                                handleNavSelect(item.target);
                              }}
                            >
                              {item.action}
                            </button>
                          </div>
                        );
                      })}
                    </div>
                  )}
                </div>
              )}
            </div>
            <LanguageToggle language={language} onChange={changeLanguage} />
            <ThemeToggle theme={theme} onToggle={toggleTheme} />
            <div className="user-menu" ref={userMenuRef}>
              <button type="button" className="user-menu-trigger" onClick={() => setUserMenuOpen(open => !open)} aria-haspopup="menu" aria-expanded={userMenuOpen} title={t('Logged in as')}>
                <span className="user-avatar" aria-hidden="true">{(currentUser?.username || username || '?').slice(0, 1).toUpperCase()}</span>
                <span className="user-menu-name">{currentUser?.username || username}</span>
                <ChevronDown size={14} className="user-menu-chevron" />
              </button>
              {userMenuOpen && <div className="user-menu-panel" role="menu">
                <div className="user-menu-head">
                  <strong>{accountLabel}</strong>
                  <small>{currentUser?.email || t(roleLabel(currentUser?.role))}</small>
                </div>
                {currentUser?.impersonated && currentUser?.impersonated_by && (
                  <button type="button" role="menuitem" onClick={() => { setUserMenuOpen(false); exitImpersonation(); }}><ArrowRightLeft size={15} />{t('Return to')} {currentUser.impersonated_by}</button>
                )}
                <button type="button" role="menuitem" onClick={() => { setUserMenuOpen(false); navigateToPage('security'); }}><LockKeyhole size={15} />{t('Account security')}</button>
                <button type="button" role="menuitem" className="user-menu-logout" onClick={() => { setUserMenuOpen(false); logout(); }}><LogOut size={15} />{t('Logout')}</button>
              </div>}
            </div>
            </div>
          </div>
        </section>
        {currentUser?.demo && <div className="demo-strip" role="status"><Eye size={15} /><span>{t('This is a demo: you can look at everything, but changes are not saved.')}</span></div>}
        <div className="content-body">
          {renderPage()}
          {loading && <div className="loading"><span></span>{t(loading)}</div>}
        </div>
      </div>
    </section>

    {cmdPaletteOpen && (
      <div
        className="modal-backdrop"
        onClick={() => setCmdPaletteOpen(false)}
        style={{
          position: 'fixed',
          inset: 0,
          zIndex: 1300,
          background: 'rgba(15, 23, 42, 0.58)',
          backdropFilter: 'blur(4px)',
          display: 'flex',
          alignItems: 'flex-start',
          justifyContent: 'center',
          padding: '8vh 14px 14px',
        }}
      >
        <div
          className="modal"
          onClick={e => e.stopPropagation()}
          style={{
            width: '100%',
            maxWidth: '580px',
            borderRadius: '14px',
            background: 'var(--bg-elevated, var(--surface))',
            border: '1px solid var(--border-strong, var(--border))',
            boxShadow: '0 24px 48px rgba(15, 23, 42, 0.32)',
            overflow: 'hidden',
          }}
        >
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: '10px',
              padding: '12px 16px',
              borderBottom: '1px solid var(--border)',
            }}
          >
            <Search size={17} style={{ color: 'var(--text-muted)', flexShrink: 0 }} />
            <input
              type="text"
              autoFocus
              value={cmdPaletteQuery}
              onChange={e => {
                setCmdPaletteQuery(e.target.value);
                setCmdPaletteIndex(0);
              }}
              onKeyDown={e => {
                if (e.key === 'ArrowDown') {
                  e.preventDefault();
                  setCmdPaletteIndex(i => Math.min(cmdPaletteItems.length - 1, i + 1));
                } else if (e.key === 'ArrowUp') {
                  e.preventDefault();
                  setCmdPaletteIndex(i => Math.max(0, i - 1));
                } else if (e.key === 'Enter' && cmdPaletteItems[cmdPaletteIndex]) {
                  e.preventDefault();
                  const chosen = cmdPaletteItems[cmdPaletteIndex];
                  setCmdPaletteOpen(false);
                  chosen.run();
                }
              }}
              placeholder={t('Type a domain, user, database, app, or panel tool...')}
              style={{
                border: 'none',
                background: 'transparent',
                outline: 'none',
                width: '100%',
                fontSize: '14px',
                color: 'var(--text)',
                boxShadow: 'none',
                padding: '4px 0',
              }}
            />
            <kbd
              style={{
                fontSize: '11px',
                padding: '2px 6px',
                borderRadius: '5px',
                background: 'var(--surface-alt)',
                border: '1px solid var(--border)',
                color: 'var(--text-muted)',
                cursor: 'pointer',
              }}
              onClick={() => setCmdPaletteOpen(false)}
            >
              ESC
            </kbd>
          </div>
          <div style={{ maxHeight: '360px', overflowY: 'auto', padding: '8px' }}>
            {cmdPaletteItems.length === 0 ? (
              <div style={{ padding: '24px', textAlign: 'center' }} className="hint">
                {t('No matching domains, accounts, databases, or tools found.')}
              </div>
            ) : (
              cmdPaletteItems.map((item, idx) => {
                const ItemIcon = item.icon || Globe;
                const active = idx === cmdPaletteIndex;
                return (
                  <div
                    key={item.id}
                    onMouseEnter={() => setCmdPaletteIndex(idx)}
                    onClick={() => {
                      setCmdPaletteOpen(false);
                      item.run();
                    }}
                    style={{
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'space-between',
                      gap: '12px',
                      padding: '9px 12px',
                      borderRadius: '9px',
                      cursor: 'pointer',
                      background: active ? 'var(--surface-alt)' : 'transparent',
                    }}
                  >
                    <div style={{ display: 'flex', alignItems: 'center', gap: '10px', minWidth: 0 }}>
                      <ItemIcon size={16} style={{ color: active ? 'var(--primary)' : 'var(--text-muted)', flexShrink: 0 }} />
                      <div style={{ minWidth: 0 }}>
                        <div style={{ fontWeight: 600, fontSize: '13.5px', color: 'var(--text)' }}>{item.title}</div>
                        {item.subtitle && (
                          <div style={{ fontSize: '11.5px', color: 'var(--text-muted)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                            {item.subtitle}
                          </div>
                        )}
                      </div>
                    </div>
                    <span className="badge" style={{ flexShrink: 0 }}>{item.category}</span>
                  </div>
                );
              })
            )}
          </div>
        </div>
      </div>
    )}

    {donateModalOpen && (
      <div
        className="modal-backdrop donate-modal-backdrop"
        onClick={() => setDonateModalOpen(false)}
        style={{
          position: 'fixed',
          inset: 0,
          zIndex: 1200,
          background: 'rgba(15, 23, 42, 0.62)',
          backdropFilter: 'blur(5px)',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          padding: '14px',
        }}
      >
        <div
          className="modal donate-modal-card"
          onClick={e => e.stopPropagation()}
          style={{
            width: '100%',
            maxWidth: '430px',
            borderRadius: '18px',
            background: 'var(--bg-elevated, var(--surface))',
            border: '1px solid var(--border-strong, var(--border))',
            boxShadow: '0 24px 48px rgba(15, 23, 42, 0.28)',
            overflow: 'hidden',
          }}
        >
          <div
            className="modal-header"
            style={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
              gap: '12px',
              padding: '16px 20px',
              borderBottom: '1px solid var(--border)',
              background: 'linear-gradient(135deg, rgba(225, 29, 72, 0.08) 0%, rgba(244, 63, 94, 0.03) 100%)',
            }}
          >
            <div style={{ display: 'flex', alignItems: 'center', gap: '10px', minWidth: 0 }}>
              <div
                style={{
                  width: '36px',
                  height: '36px',
                  borderRadius: '10px',
                  background: 'rgba(225, 29, 72, 0.14)',
                  color: '#e11d48',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  flexShrink: 0,
                }}
              >
                <Heart size={18} fill="currentColor" />
              </div>
              <div style={{ minWidth: 0 }}>
                <h3 style={{ margin: 0, fontSize: '16px', fontWeight: 700, color: 'var(--text)' }}>
                  {t('Donate to SPanel')}
                </h3>
                <small style={{ display: 'block', fontSize: '12px', color: 'var(--text-muted)', marginTop: '1px' }}>
                  {t('Support SPanel open-source development')}
                </small>
              </div>
            </div>
            <button
              type="button"
              className="secondary-light"
              onClick={() => setDonateModalOpen(false)}
              style={{ width: '32px', minWidth: '32px', height: '32px', minHeight: '32px', padding: 0, borderRadius: '8px' }}
              aria-label={t('Close')}
            >
              <X size={16} />
            </button>
          </div>

          <div
            className="modal-body"
            style={{
              padding: '18px 20px',
              display: 'flex',
              flexDirection: 'column',
              gap: '14px',
            }}
          >
            {/* VietQR Frame */}
            <div
              style={{
                background: '#ffffff',
                border: '1px solid #e2e8f0',
                borderRadius: '14px',
                padding: '14px 16px 12px',
                display: 'flex',
                flexDirection: 'column',
                alignItems: 'center',
                boxShadow: '0 4px 14px rgba(15, 23, 42, 0.05)',
              }}
            >
              <div
                style={{
                  width: '100%',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'space-between',
                  marginBottom: '10px',
                  paddingBottom: '8px',
                  borderBottom: '1px dashed #e2e8f0',
                }}
              >
                <span
                  style={{
                    display: 'inline-flex',
                    alignItems: 'center',
                    gap: '6px',
                    fontSize: '12.5px',
                    fontWeight: 800,
                    color: '#1e3a8a',
                    letterSpacing: '0.02em',
                  }}
                >
                  <span
                    style={{
                      display: 'inline-block',
                      width: '8px',
                      height: '8px',
                      borderRadius: '999px',
                      background: '#dc2626',
                    }}
                  />
                  MB BANK
                </span>
                <span
                  style={{
                    fontSize: '11px',
                    fontWeight: 700,
                    color: '#0284c7',
                    background: '#f0f9ff',
                    border: '1px solid #bae6fd',
                    padding: '2px 8px',
                    borderRadius: '999px',
                  }}
                >
                  VietQR • Napas247
                </span>
              </div>

              <div
                style={{
                  width: '204px',
                  height: '204px',
                  padding: '6px',
                  background: '#ffffff',
                  borderRadius: '10px',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                }}
              >
                <svg
                  viewBox="-1 -1 39 39"
                  width="192"
                  height="192"
                  shapeRendering="crispEdges"
                  role="img"
                  aria-label="VietQR MB Bank 978579798888 Tran Quang Quoc Dung"
                  style={{ width: '100%', height: '100%', display: 'block' }}
                >
                  <rect x="-1" y="-1" width="39" height="39" fill="#ffffff" />
                  <path d="M0 0h7v1h-7zM10 0h3v1h-3zM15 0h1v1h-1zM21 0h1v1h-1zM25 0h1v1h-1zM28 0h1v1h-1zM30 0h7v1h-7zM0 1h1v1h-1zM6 1h1v1h-1zM10 1h2v1h-2zM20 1h3v1h-3zM24 1h1v1h-1zM26 1h1v1h-1zM30 1h1v1h-1zM36 1h1v1h-1zM0 2h1v1h-1zM2 2h3v1h-3zM6 2h1v1h-1zM8 2h1v1h-1zM10 2h3v1h-3zM14 2h2v1h-2zM17 2h1v1h-1zM24 2h1v1h-1zM26 2h1v1h-1zM30 2h1v1h-1zM32 2h3v1h-3zM36 2h1v1h-1zM0 3h1v1h-1zM2 3h3v1h-3zM6 3h1v1h-1zM8 3h1v1h-1zM12 3h2v1h-2zM15 3h1v1h-1zM18 3h1v1h-1zM20 3h1v1h-1zM23 3h1v1h-1zM25 3h3v1h-3zM30 3h1v1h-1zM32 3h3v1h-3zM36 3h1v1h-1zM0 4h1v1h-1zM2 4h3v1h-3zM6 4h1v1h-1zM8 4h2v1h-2zM12 4h6v1h-6zM19 4h1v1h-1zM21 4h1v1h-1zM23 4h1v1h-1zM25 4h2v1h-2zM30 4h1v1h-1zM32 4h3v1h-3zM36 4h1v1h-1zM0 5h1v1h-1zM6 5h1v1h-1zM8 5h5v1h-5zM14 5h1v1h-1zM16 5h2v1h-2zM19 5h1v1h-1zM21 5h1v1h-1zM23 5h2v1h-2zM27 5h1v1h-1zM30 5h1v1h-1zM36 5h1v1h-1zM0 6h7v1h-7zM8 6h1v1h-1zM10 6h1v1h-1zM12 6h1v1h-1zM14 6h1v1h-1zM16 6h1v1h-1zM18 6h1v1h-1zM20 6h1v1h-1zM22 6h1v1h-1zM24 6h1v1h-1zM26 6h1v1h-1zM28 6h1v1h-1zM30 6h7v1h-7zM8 7h1v1h-1zM10 7h1v1h-1zM12 7h1v1h-1zM15 7h5v1h-5zM24 7h2v1h-2zM27 7h1v1h-1zM0 8h1v1h-1zM2 8h5v1h-5zM9 8h4v1h-4zM15 8h2v1h-2zM21 8h1v1h-1zM26 8h2v1h-2zM30 8h5v1h-5zM1 9h1v1h-1zM7 9h3v1h-3zM12 9h1v1h-1zM14 9h2v1h-2zM17 9h2v1h-2zM20 9h2v1h-2zM27 9h2v1h-2zM30 9h2v1h-2zM33 9h2v1h-2zM0 10h7v1h-7zM8 10h2v1h-2zM12 10h1v1h-1zM15 10h5v1h-5zM21 10h4v1h-4zM30 10h1v1h-1zM33 10h1v1h-1zM36 10h1v1h-1zM1 11h1v1h-1zM3 11h2v1h-2zM8 11h2v1h-2zM13 11h1v1h-1zM15 11h1v1h-1zM17 11h5v1h-5zM23 11h2v1h-2zM27 11h7v1h-7zM35 11h1v1h-1zM3 12h1v1h-1zM6 12h2v1h-2zM10 12h1v1h-1zM14 12h7v1h-7zM23 12h1v1h-1zM25 12h1v1h-1zM30 12h2v1h-2zM33 12h1v1h-1zM36 12h1v1h-1zM0 13h2v1h-2zM5 13h1v1h-1zM8 13h4v1h-4zM14 13h1v1h-1zM18 13h1v1h-1zM21 13h3v1h-3zM25 13h3v1h-3zM30 13h2v1h-2zM36 13h1v1h-1zM0 14h3v1h-3zM5 14h3v1h-3zM9 14h3v1h-3zM14 14h1v1h-1zM18 14h1v1h-1zM20 14h1v1h-1zM24 14h2v1h-2zM27 14h1v1h-1zM30 14h1v1h-1zM33 14h1v1h-1zM0 15h4v1h-4zM5 15h1v1h-1zM8 15h5v1h-5zM14 15h3v1h-3zM21 15h1v1h-1zM24 15h1v1h-1zM27 15h1v1h-1zM29 15h2v1h-2zM33 15h1v1h-1zM36 15h1v1h-1zM0 16h4v1h-4zM6 16h1v1h-1zM8 16h3v1h-3zM12 16h4v1h-4zM18 16h1v1h-1zM24 16h1v1h-1zM26 16h3v1h-3zM32 16h2v1h-2zM36 16h1v1h-1zM0 17h1v1h-1zM4 17h1v1h-1zM7 17h4v1h-4zM12 17h1v1h-1zM14 17h4v1h-4zM27 17h2v1h-2zM31 17h1v1h-1zM36 17h1v1h-1zM2 18h6v1h-6zM14 18h1v1h-1zM20 18h6v1h-6zM30 18h3v1h-3zM36 18h1v1h-1zM0 19h1v1h-1zM2 19h4v1h-4zM7 19h2v1h-2zM10 19h1v1h-1zM12 19h1v1h-1zM16 19h1v1h-1zM18 19h2v1h-2zM21 19h1v1h-1zM28 19h1v1h-1zM30 19h1v1h-1zM32 19h1v1h-1zM36 19h1v1h-1zM0 20h1v1h-1zM5 20h2v1h-2zM8 20h1v1h-1zM10 20h1v1h-1zM12 20h1v1h-1zM14 20h1v1h-1zM18 20h1v1h-1zM22 20h2v1h-2zM26 20h1v1h-1zM29 20h2v1h-2zM32 20h3v1h-3zM36 20h1v1h-1zM0 21h2v1h-2zM3 21h2v1h-2zM7 21h1v1h-1zM17 21h2v1h-2zM21 21h3v1h-3zM26 21h2v1h-2zM30 21h2v1h-2zM33 21h2v1h-2zM36 21h1v1h-1zM1 22h1v1h-1zM4 22h1v1h-1zM6 22h3v1h-3zM10 22h3v1h-3zM15 22h1v1h-1zM17 22h1v1h-1zM19 22h1v1h-1zM27 22h1v1h-1zM31 22h1v1h-1zM33 22h4v1h-4zM0 23h1v1h-1zM2 23h4v1h-4zM8 23h1v1h-1zM10 23h1v1h-1zM12 23h7v1h-7zM21 23h1v1h-1zM23 23h3v1h-3zM27 23h2v1h-2zM30 23h1v1h-1zM32 23h2v1h-2zM35 23h2v1h-2zM1 24h1v1h-1zM5 24h7v1h-7zM14 24h1v1h-1zM16 24h1v1h-1zM21 24h1v1h-1zM25 24h1v1h-1zM28 24h1v1h-1zM30 24h2v1h-2zM34 24h1v1h-1zM0 25h1v1h-1zM4 25h1v1h-1zM7 25h2v1h-2zM11 25h3v1h-3zM15 25h1v1h-1zM17 25h2v1h-2zM25 25h1v1h-1zM27 25h4v1h-4zM32 25h2v1h-2zM36 25h1v1h-1zM0 26h1v1h-1zM2 26h2v1h-2zM6 26h1v1h-1zM13 26h1v1h-1zM15 26h5v1h-5zM23 26h2v1h-2zM33 26h2v1h-2zM36 26h1v1h-1zM0 27h1v1h-1zM2 27h1v1h-1zM4 27h2v1h-2zM8 27h2v1h-2zM11 27h2v1h-2zM14 27h2v1h-2zM18 27h2v1h-2zM21 27h4v1h-4zM27 27h1v1h-1zM32 27h5v1h-5zM0 28h1v1h-1zM2 28h1v1h-1zM6 28h1v1h-1zM9 28h1v1h-1zM11 28h1v1h-1zM13 28h1v1h-1zM15 28h2v1h-2zM22 28h1v1h-1zM24 28h1v1h-1zM28 28h6v1h-6zM35 28h2v1h-2zM8 29h1v1h-1zM11 29h1v1h-1zM13 29h4v1h-4zM18 29h2v1h-2zM21 29h2v1h-2zM24 29h1v1h-1zM26 29h3v1h-3zM32 29h1v1h-1zM35 29h1v1h-1zM0 30h7v1h-7zM9 30h3v1h-3zM13 30h3v1h-3zM18 30h1v1h-1zM20 30h2v1h-2zM24 30h3v1h-3zM28 30h1v1h-1zM30 30h1v1h-1zM32 30h1v1h-1zM36 30h1v1h-1zM0 31h1v1h-1zM6 31h1v1h-1zM8 31h1v1h-1zM10 31h1v1h-1zM14 31h2v1h-2zM20 31h3v1h-3zM25 31h1v1h-1zM27 31h2v1h-2zM32 31h2v1h-2zM35 31h2v1h-2zM0 32h1v1h-1zM2 32h3v1h-3zM6 32h1v1h-1zM8 32h4v1h-4zM15 32h1v1h-1zM18 32h1v1h-1zM20 32h2v1h-2zM23 32h4v1h-4zM28 32h5v1h-5zM0 33h1v1h-1zM2 33h3v1h-3zM6 33h1v1h-1zM8 33h5v1h-5zM15 33h2v1h-2zM27 33h1v1h-1zM30 33h2v1h-2zM36 33h1v1h-1zM0 34h1v1h-1zM2 34h3v1h-3zM6 34h1v1h-1zM8 34h3v1h-3zM12 34h1v1h-1zM14 34h1v1h-1zM17 34h1v1h-1zM21 34h1v1h-1zM23 34h2v1h-2zM27 34h1v1h-1zM29 34h2v1h-2zM33 34h1v1h-1zM36 34h1v1h-1zM0 35h1v1h-1zM6 35h1v1h-1zM9 35h1v1h-1zM13 35h1v1h-1zM16 35h1v1h-1zM18 35h2v1h-2zM21 35h1v1h-1zM24 35h1v1h-1zM26 35h2v1h-2zM33 35h1v1h-1zM35 35h2v1h-2zM0 36h7v1h-7zM8 36h2v1h-2zM12 36h1v1h-1zM17 36h3v1h-3zM21 36h1v1h-1zM24 36h5v1h-5zM32 36h2v1h-2zM36 36h1v1h-1z" fill="#0f172a" />
                </svg>
              </div>

              <div
                style={{
                  marginTop: '8px',
                  fontSize: '11.5px',
                  fontWeight: 500,
                  color: '#64748b',
                  textAlign: 'center',
                }}
              >
                {t('Scan QR with any Banking or E-Wallet app')}
              </div>
            </div>

            {/* Bank Account Details List */}
            <div
              style={{
                display: 'flex',
                flexDirection: 'column',
                gap: '8px',
                background: 'var(--surface-alt)',
                border: '1px solid var(--border)',
                borderRadius: '12px',
                padding: '12px 14px',
              }}
            >
              {[
                { key: 'bank', label: t('Bank'), value: 'MB Bank', mono: false },
                { key: 'stk', label: t('Account Number'), value: '978579798888', mono: true, highlight: true },
                { key: 'holder', label: t('Account Holder'), value: 'Tran Quang Quoc Dung', mono: false },
                { key: 'msg', label: t('Transfer Message'), value: 'Donate Spanel', mono: true, badge: true },
              ].map(row => (
                <div
                  key={row.key}
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'space-between',
                    gap: '10px',
                    padding: '4px 0',
                    borderBottom: row.key !== 'msg' ? '1px dashed var(--border)' : 'none',
                  }}
                >
                  <div style={{ minWidth: 0, flex: '1 1 auto' }}>
                    <div style={{ fontSize: '11.5px', color: 'var(--text-muted)', fontWeight: 500 }}>
                      {row.label}
                    </div>
                    <div
                      style={{
                        fontSize: row.highlight ? '15px' : '13.5px',
                        fontWeight: 700,
                        color: row.highlight ? 'var(--primary)' : 'var(--text)',
                        fontFamily: row.mono ? 'var(--font-mono)' : 'inherit',
                        letterSpacing: row.highlight ? '0.03em' : 'normal',
                        wordBreak: 'break-word',
                      }}
                    >
                      {row.value}
                    </div>
                  </div>
                  <button
                    type="button"
                    className="secondary-light mini"
                    onClick={() => {
                      navigator.clipboard?.writeText(row.value);
                      setDonateCopiedField(row.key);
                      setTimeout(() => setDonateCopiedField(''), 2000);
                    }}
                    style={{
                      width: 'auto',
                      minWidth: '78px',
                      height: '28px',
                      minHeight: '28px',
                      padding: '0 10px',
                      fontSize: '11.5px',
                      fontWeight: 600,
                      borderRadius: '7px',
                      flexShrink: 0,
                      color: donateCopiedField === row.key ? '#16a34a' : 'var(--text-secondary)',
                      borderColor: donateCopiedField === row.key ? 'rgba(22, 163, 74, 0.4)' : 'var(--border)',
                    }}
                  >
                    {donateCopiedField === row.key ? (
                      <>
                        <Check size={13} />
                        <span>{t('Copied')}</span>
                      </>
                    ) : (
                      <>
                        <Copy size={12} />
                        <span>{t('Copy')}</span>
                      </>
                    )}
                  </button>
                </div>
              ))}
            </div>
          </div>

          <div
            className="modal-footer"
            style={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
              gap: '10px',
              padding: '12px 20px',
              borderTop: '1px solid var(--border)',
              background: 'var(--surface-alt)',
            }}
          >
            <span style={{ fontSize: '12px', color: 'var(--text-muted)', display: 'inline-flex', alignItems: 'center', gap: '5px' }}>
              <Heart size={13} fill="#e11d48" color="#e11d48" />
              {t('Thank you for supporting SPanel!')}
            </span>
            <button
              type="button"
              className="secondary-light"
              onClick={() => setDonateModalOpen(false)}
              style={{ height: '34px', minHeight: '34px', padding: '0 16px', width: 'auto' }}
            >
              {t('Close')}
            </button>
          </div>
        </div>
      </div>
    )}
    {renderNotifications()}
  </main></AppContext.Provider>;
}


function App() {
  return (
    <AppProvider>
      <AppContent />
    </AppProvider>
  );
}

createRoot(document.getElementById('root')).render(<App />);






