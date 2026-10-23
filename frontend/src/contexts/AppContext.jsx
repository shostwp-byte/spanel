import React, { createContext, useState, useContext } from 'react';
import { t } from '../i18n.js';
import { formatBytes } from '../utils/helpers.js';

export const AppContext = createContext();

export const CRON_SCHEDULE_PRESETS = [
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

export const BACKUP_SCHEDULE_PRESETS = [
  ['0 2 * * *', 'Every day at 02:00'],
  ['0 3 * * *', 'Every day at 03:00'],
  ['0 */12 * * *', 'Every 12 hours'],
  ['0 2 * * 0', 'Every Sunday at 02:00'],
  ['0 2 1 * *', 'On the 1st of every month at 02:00'],
];

export const APP_TYPE_LABELS = { wordpress: 'WordPress', php: 'PHP', static: 'Static', application: 'App' };

export const HTTP_FLOOD_DEFAULTS = {
  access_limit_requests: 100,
  access_limit_window: 10,
  access_limit_burst: 100,
  connection_limit: 60,
};

export const NGINX_REWRITE_MODES = [
  { value: 'none', label: 'None / static PHP' },
  { value: 'front_controller', label: 'PHP front controller' },
  { value: 'laravel', label: 'Laravel' },
  { value: 'codeigniter', label: 'CodeIgniter' },
  { value: 'seohburl', label: 'SEO HB URL' },
];

export const SITE_APP_KIND_LABELS = { node: 'Node.js', python: 'Python', docker: 'Container', compose: 'Compose' };

export const SITE_APP_KINDS = [
  ['node', 'Node.js', 'spanel installs dependencies and keeps the process running under systemd.'],
  ['python', 'Python (ASGI / WSGI)', 'spanel provisions an isolated .venv, installs requirements.txt/pyproject.toml, and runs uvicorn, gunicorn, or python under systemd.'],
  ['docker', 'Container', 'spanel pulls the image and runs it, published on loopback only.'],
  ['compose', 'Docker Compose', 'Paste your project\u2019s docker-compose.yml. spanel checks it and runs a file it generates from what it accepted.'],
];

export const DNS_TYPES = ['A', 'AAAA', 'CNAME', 'MX', 'TXT', 'NS', 'SRV', 'CAA'];
export const DNS_TTLS = [60, 300, 900, 1800, 3600, 14400, 43200, 86400];
export const DNS_PLACEHOLDERS = {
  A: '203.0.113.10', AAAA: '2001:db8::10', CNAME: 'target.example.com', MX: 'mail.example.com',
  TXT: 'v=spf1 mx -all', NS: 'ns1.example.net', SRV: '5 5060 sip.example.com', CAA: '0 issue letsencrypt.org',
};

export const MAIL_SPF_INCLUDES = [
  'include:spf.smtp2go.com', 'include:mailgun.org', 'include:sendgrid.net', 'include:amazonses.com',
  'include:spf.brevo.com', 'include:spf.mandrillapp.com', 'include:_spf.google.com', 'include:spf.protection.outlook.com',
];

export function WordPressIcon({ size = 14, color = 'currentColor' }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill={color} xmlns="http://www.w3.org/2000/svg" aria-hidden="true" focusable="false" className="lucide">
      <path d="M12 2C6.486 2 2 6.486 2 12s4.486 10 10 10 10-4.486 10-10S17.514 2 12 2zm0 1.545c4.663 0 8.455 3.792 8.455 8.455 0 1.482-.384 2.874-1.056 4.086l-2.61-7.152c.455-.024.865-.072.865-.072.408-.048.36-.648-.048-.624 0 0-1.226.096-2.018.096-.744 0-1.994-.096-1.994-.096-.408-.024-.456.6-.048.624 0 0 .386.048.793.072l1.178 3.226-1.658 4.974-2.763-8.2c.456-.024.865-.072.865-.072.408-.048.36-.648-.048-.624 0 0-1.226.096-2.018.096-.142 0-.309-.004-.486-.011C6.968 5.299 9.313 3.545 12 3.545zM4.365 8.397l4.158 11.404C5.775 18.453 3.545 15.467 3.545 12c0-1.295.297-2.521.82-3.603zm7.801 4.287l2.536 6.949a8.49 8.49 0 0 1-2.702.442c-.798 0-1.569-.113-2.301-.321l2.467-7.07zm6.578-3.829c.039.293.061.608.061.949 0 .937-.175 1.988-.701 3.307l-2.815 8.138c2.739-1.597 4.566-4.568 4.566-7.975 0-1.603-.447-3.101-1.222-4.379l.111-.04z" />
    </svg>
  );
}

export function composeWebPorts(plan, wanted) {
  const name = wanted || plan?.web_service;
  const service = plan?.services?.find(item => item.name === name);
  return service?.container_ports || [];
}

export function isProxiedAppType(appType) {
  return ['application'].includes(appType);
}

export function normalizeHttpFloodConfig(config = {}) {
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

export function generateRandomPassword(length = 20) {
  const chars = 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789!@#%^*_+-';
  const arr = new Uint8Array(length);
  crypto.getRandomValues(arr);
  return Array.from(arr, b => chars[b % chars.length]).join('');
}

export function readCookie(name) {
  const match = document.cookie.match(new RegExp('(?:^|; )' + name.replace(/[$()*+./?[\\\]^{|}]/g, '\\$&') + '=([^;]*)'));
  return match ? decodeURIComponent(match[1]) : '';
}

export function formatFileTime(seconds, withSeconds = false) {
  const d = new Date(Number(seconds) * 1000);
  if (!seconds || Number.isNaN(d.getTime())) return '';
  const two = n => String(n).padStart(2, '0');
  const stamp = `${two(d.getDate())}/${two(d.getMonth() + 1)}/${d.getFullYear()} ${two(d.getHours())}:${two(d.getMinutes())}`;
  return withSeconds ? `${stamp}:${two(d.getSeconds())}` : stamp;
}

export function storageLimitBytes(user) {
  if (!user) return null;
  if (user.storage_limit_bytes === null) return null;
  if (user.storage_limit_bytes !== undefined) return user.storage_limit_bytes;
  return Number(user.storage_limit_mb || 0) * 1024 * 1024;
}

export function storageUsageText(user) {
  if (Number(user?.storage_used_bytes) < 0) return t('measuring...');
  const used = Number(user?.storage_used_bytes || 0);
  const limit = storageLimitBytes(user);
  if (limit === null) return formatBytes(used);
  return `${formatBytes(used)} / ${formatBytes(limit)}`;
}

export function dnsTtlLabel(seconds) {
  const n = Number(seconds);
  if (!Number.isFinite(n) || n <= 0) return String(seconds ?? '');
  if (n % 86400 === 0) return t('{n} d', { n: n / 86400 });
  if (n % 3600 === 0) return t('{n} h', { n: n / 3600 });
  if (n % 60 === 0) return t('{n} min', { n: n / 60 });
  return t('{n} s', { n });
}

export function splitAddresses(text) {
  return String(text || '').split(/[\s,;]+/).map(item => item.trim()).filter(Boolean);
}

export const AppProvider = ({ children }) => {
  const [currentUser, setCurrentUser] = useState(null);
  const [token, setToken] = useState(localStorage.getItem('spanel_token') || '');
  const [loading, setLoading] = useState('');
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [packages, setPackages] = useState([]);
  const [users, setUsers] = useState([]);
  const [panelSettings, setPanelSettings] = useState({ app_name: 'spanel', panel_url: '', panel_hostname: '', panel_port: 2222, logo_url: '', favicon_url: '/favicon.png', ssl_enabled: false });
  const [appVersion, setAppVersion] = useState('');
  const [databases, setDatabases] = useState([]);
  const [dbSearch, setDbSearch] = useState('');
  const [dbCreateOpen, setDbCreateOpen] = useState(false);
  const [phpVersion, setPhpVersion] = useState('8.4');
  const [phpVersions, setPhpVersions] = useState({ installed: ['8.4'], supported: ['5.6', '7.4', '8.0', '8.1', '8.2', '8.3', '8.4', '8.5'] });
  const [fileAppId, setFileAppId] = useState('');
  const [filePath, setFilePath] = useState('public_html/index.html');
  const [fileListPath, setFileListPath] = useState('public_html');
  const [websites, setWebsites] = useState([]);
  const [siteApps, setSiteApps] = useState({ items: [], limit: 0, used: 0, memory_ceiling_mb: 512, port_range: [21000, 21999] });
  const [selectedWebsiteId, setSelectedWebsiteId] = useState('');
  const [copiedField, setCopiedField] = useState(null);
  const [cronRuntime, setCronRuntime] = useState(null);
  const [cronPreset, setCronPreset] = useState('every_5_minutes');
  const [cronParts, setCronParts] = useState({ minute: '*/5', hour: '*', day: '*', month: '*', weekday: '*' });
  const [cronCommand, setCronCommand] = useState('');
  const [cronJobs, setCronJobs] = useState([]);

  const value = {
    currentUser, setCurrentUser,
    token, setToken,
    loading, setLoading,
    error, setError,
    notice, setNotice,
    packages, setPackages,
    users, setUsers,
    panelSettings, setPanelSettings,
    appVersion, setAppVersion,
    databases, setDatabases,
    dbSearch, setDbSearch,
    dbCreateOpen, setDbCreateOpen,
    phpVersion, setPhpVersion,
    phpVersions, setPhpVersions,
    fileAppId, setFileAppId,
    filePath, setFilePath,
    fileListPath, setFileListPath,
    websites, setWebsites, siteApps, setSiteApps,
    selectedWebsiteId, setSelectedWebsiteId,
    copiedField, setCopiedField,
    cronRuntime, setCronRuntime,
    cronPreset, setCronPreset,
    cronParts, setCronParts,
    cronCommand, setCronCommand,
    cronJobs, setCronJobs,
    generateRandomPassword,
    readCookie,
    formatFileTime,
    storageLimitBytes,
    storageUsageText,
    dnsTtlLabel,
    splitAddresses,
    normalizeHttpFloodConfig,
    isProxiedAppType,
    composeWebPorts,
    APP_TYPE_LABELS,
    SITE_APP_KINDS,
    SITE_APP_KIND_LABELS,
    NGINX_REWRITE_MODES,
    DNS_TYPES,
    DNS_TTLS,
    DNS_PLACEHOLDERS,
    MAIL_SPF_INCLUDES,
    CRON_SCHEDULE_PRESETS,
    BACKUP_SCHEDULE_PRESETS,
    HTTP_FLOOD_DEFAULTS,
    WordPressIcon,
  };

  return (
    <AppContext.Provider value={value}>
      {children}
    </AppContext.Provider>
  );
};

export const useAppContext = () => {
  const context = useContext(AppContext);
  if (!context) {
    throw new Error('useAppContext must be used within an AppProvider');
  }
  return context;
};
