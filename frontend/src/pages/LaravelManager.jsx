import React, { useState, useEffect, useMemo, useCallback } from 'react';
import {
  Zap,
  RefreshCw,
  Plus,
  Search,
  ExternalLink,
  FolderOpen,
  Shield,
  ShieldCheck,
  ShieldAlert,
  AlertTriangle,
  Database,
  Check,
  X,
  Trash2,
  Settings,
  Wrench,
  PackageOpen,
  Lock,
  Unlock,
  Eye,
  EyeOff,
  Copy,
  CheckCircle2,
  XCircle,
  Clock,
  Server,
  Globe,
  FileText,
  Sparkles,
  Layers,
  Terminal,
  Play,
  Square,
  RotateCcw,
  Sliders,
  KeyRound,
  HardDrive,
  Code2,
  GitBranch,
  Cpu,
  Activity,
  ChevronDown,
  ChevronUp,
  ListFilter,
  LayoutGrid,
  Save,
  Boxes,
} from 'lucide-react';
import { useAppContext } from '../contexts/AppContext.jsx';
import { t } from '../i18n.js';

function LaravelBrandIcon({ size = 18 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" aria-hidden="true" focusable="false" className="lucide">
      <rect x="3" y="3" width="18" height="18" rx="4" fill="none" stroke="currentColor" strokeWidth="2" />
      <text x="12" y="16" textAnchor="middle" fontSize="11" fontWeight="700" fontFamily="Georgia, serif" fill="currentColor">L</text>
    </svg>
  );
}

const PHP_VERSIONS = ['8.4', '8.3', '8.2', '8.1'];
const LARAVEL_VERSIONS = ['11.x', '10.x'];
const CACHE_DRIVERS = ['file', 'redis', 'database', 'memcached', 'array'];
const QUEUE_CONNECTIONS = ['database', 'redis', 'sync'];
const SESSION_DRIVERS = ['file', 'cookie', 'database', 'redis'];

const ARTISAN_QUICK_ACTIONS = [
  {
    cmd: 'migrate',
    title: 'artisan migrate',
    subtitle: 'Cập nhật bảng DB',
    desc: 'Chạy các bản cập nhật cấu trúc bảng Database mới nhất (--force).',
  },
  {
    cmd: 'optimize:clear',
    title: 'optimize:clear',
    subtitle: 'Xóa toàn bộ Cache',
    desc: 'Xóa sạch bộ đệm Config, Route, View, Event và Application Cache.',
  },
  {
    cmd: 'storage:link',
    title: 'storage:link',
    subtitle: 'Gắn symlink storage',
    desc: 'Tạo liên kết public/storage trỏ tới storage/app/public.',
  },
  {
    cmd: 'config:cache',
    title: 'config:cache',
    subtitle: 'Tối ưu cấu hình',
    desc: 'Biên dịch toàn bộ file cấu hình vào một tệp bộ đệm tăng tốc độ tải.',
  },
];

const ARTISAN_EXTENDED_COMMANDS = [
  { cmd: 'migrate', label: 'artisan migrate --force', group: 'Database' },
  { cmd: 'migrate:status', label: 'artisan migrate:status', group: 'Database' },
  { cmd: 'db:seed', label: 'artisan db:seed --force', group: 'Database' },
  { cmd: 'optimize', label: 'artisan optimize', group: 'Cache & Tối ưu' },
  { cmd: 'optimize:clear', label: 'artisan optimize:clear', group: 'Cache & Tối ưu' },
  { cmd: 'config:cache', label: 'artisan config:cache', group: 'Cache & Tối ưu' },
  { cmd: 'route:cache', label: 'artisan route:cache', group: 'Cache & Tối ưu' },
  { cmd: 'view:cache', label: 'artisan view:cache', group: 'Cache & Tối ưu' },
  { cmd: 'cache:clear', label: 'artisan cache:clear', group: 'Cache & Tối ưu' },
  { cmd: 'storage:link', label: 'artisan storage:link', group: 'Hệ thống' },
  { cmd: 'queue:restart', label: 'artisan queue:restart', group: 'Queue & Cron' },
  { cmd: 'schedule:run', label: 'artisan schedule:run', group: 'Queue & Cron' },
];

export function LaravelManagerPage({
  request: propRequest,
  websites: propWebsites,
  openWebsiteFileManager: propOpenFileManager,
  navigateToPage: propNavigate,
}) {
  const ctx = useAppContext() || {};
  const request = propRequest || ctx.request;
  const websites = propWebsites || ctx.websites || [];
  const openWebsiteFileManager = propOpenFileManager || ctx.openWebsiteFileManager;
  const navigateToPage = propNavigate || ctx.navigateToPage;
  const setNotice = ctx.setNotice || (() => {});
  const setError = ctx.setError || (() => {});
  const copyText =
    ctx.copyText ||
    ((text, msg) => {
      navigator.clipboard?.writeText(text);
      if (msg) setNotice(msg);
    });

  // Core data state
  const [instances, setInstances] = useState([]);
  const [availableWebsites, setAvailableWebsites] = useState([]);
  const [quotaInfo, setQuotaInfo] = useState(null);
  const [curatedPackages, setCuratedPackages] = useState([]);
  const [initialLoading, setInitialLoading] = useState(true);
  const [busyMap, setBusyMap] = useState({});

  // View & Filter state
  const [viewMode, setViewMode] = useState('cards'); // 'cards' (Mockup #3 + Right Drawer) | 'expanded' (WP-style 2-col) | 'list' (Table)
  const [searchQuery, setSearchQuery] = useState('');
  const [statusFilter, setStatusFilter] = useState('all');
  const [selectedId, setSelectedId] = useState(null);
  const [expandedIds, setExpandedIds] = useState({});
  const [activeSubTabs, setActiveSubTabs] = useState({});

  // Modals state
  const [installModalOpen, setInstallModalOpen] = useState(false);
  const [envModalSite, setEnvModalSite] = useState(null);
  const [envModalTab, setEnvModalTab] = useState('visual'); // 'visual' | 'raw'
  const [envDraft, setEnvDraft] = useState({ raw_env: '', parsed: {} });
  const [envLoading, setEnvLoading] = useState(false);

  const [logModalSite, setLogModalSite] = useState(null);
  const [logContent, setLogContent] = useState('');
  const [logPath, setLogPath] = useState('');
  const [logLoading, setLogLoading] = useState(false);

  const [removeModalSite, setRemoveModalSite] = useState(null);
  const [removeDropDb, setRemoveDropDb] = useState(false);
  const [installedCredentials, setInstalledCredentials] = useState(null);

  // Per-instance drafts for Queue, Composer, Security
  const [queueDrafts, setQueueDrafts] = useState({});
  const [composerInputs, setComposerInputs] = useState({});
  const [gitInputs, setGitInputs] = useState({});
  const [securityChecksSel, setSecurityChecksSel] = useState({});
  const [maintenanceSecrets, setMaintenanceSecrets] = useState({});

  // Install Form State
  const [installForm, setInstallForm] = useState({
    website_id: '',
    subpath: '',
    app_name: 'My Laravel Application',
    laravel_version: '11.x',
    php_version: '8.3',
    install_source: 'composer',
    git_repo: '',
    git_branch: 'main',
    db_mode: 'auto',
    db_name: '',
    db_user: '',
    db_password: '',
    cache_driver: 'file',
    queue_connection: 'database',
    session_driver: 'file',
    enable_scheduler: true,
    enable_queue_worker: true,
    queue_workers: 2,
  });

  const markBusy = (key, val) => setBusyMap(prev => ({ ...prev, [key]: val }));
  const isBusy = key => !!busyMap[key];

  const syncInstanceDrafts = useCallback(list => {
    setQueueDrafts(prev => {
      const next = { ...prev };
      list.forEach(inst => {
        if (!next[inst.id]) {
          const qw = inst.queue_worker || {};
          next[inst.id] = {
            workers: qw.workers || 2,
            queue: qw.queue || 'default',
            tries: qw.tries || 3,
            timeout: qw.timeout || 90,
            memory: qw.memory || 128,
          };
        }
      });
      return next;
    });
    setGitInputs(prev => {
      const next = { ...prev };
      list.forEach(inst => {
        if (!next[inst.id]) {
          next[inst.id] = {
            git_repo: inst.git_repo || '',
            git_branch: inst.git_branch || 'main',
          };
        }
      });
      return next;
    });
    setSecurityChecksSel(prev => {
      const next = { ...prev };
      list.forEach(inst => {
        if (!next[inst.id]) {
          const map = {};
          (inst.security_checks || []).forEach(c => {
            map[c.id] = true;
          });
          next[inst.id] = map;
        }
      });
      return next;
    });
  }, []);

  const loadInstances = useCallback(
    async (silent = false) => {
      if (!request) return;
      if (!silent) setInitialLoading(true);
      try {
        const data = await request('/laravel-manager/instances', { silent: true });
        if (data) {
          const list = Array.isArray(data.instances) ? data.instances : [];
          setInstances(list);
          if (data.quota) setQuotaInfo(data.quota);
          if (Array.isArray(data.websites)) setAvailableWebsites(data.websites);
          if (Array.isArray(data.curated_packages)) setCuratedPackages(data.curated_packages);
          syncInstanceDrafts(list);
          setSelectedId(prev => {
            if (prev && list.some(i => i.id === prev)) return prev;
            return list[0]?.id || null;
          });
          setExpandedIds(prev => {
            if (Object.keys(prev).length > 0 || list.length === 0) return prev;
            return { [list[0].id]: true };
          });
        }
      } finally {
        if (!silent) setInitialLoading(false);
      }
    },
    [request, syncInstanceDrafts]
  );

  useEffect(() => {
    loadInstances(false);
  }, [loadInstances]);

  const updateSingleInstance = useCallback(
    updated => {
      if (!updated || !updated.id) return;
      setInstances(prev => prev.map(item => (item.id === updated.id ? updated : item)));
      syncInstanceDrafts([updated]);
    },
    [syncInstanceDrafts]
  );

  // Filtered instances
  const filteredInstances = useMemo(() => {
    const q = searchQuery.trim().toLowerCase();
    return instances.filter(inst => {
      const matchQuery =
        !q ||
        (inst.domain || '').toLowerCase().includes(q) ||
        (inst.app_name || '').toLowerCase().includes(q) ||
        (inst.db_name || '').toLowerCase().includes(q) ||
        (inst.laravel_version || '').toLowerCase().includes(q);
      if (!matchQuery) return false;
      if (statusFilter === 'healthy') {
        return !inst.maintenance_mode && !inst.app_debug && inst.security_status !== 'critical';
      }
      if (statusFilter === 'queue_active') return !!inst.queue_worker?.enabled;
      if (statusFilter === 'debug_on') return !!inst.app_debug;
      if (statusFilter === 'maintenance') return !!inst.maintenance_mode;
      if (statusFilter === 'security_warn') return inst.security_status !== 'ok';
      return true;
    });
  }, [instances, searchQuery, statusFilter]);

  const selectedInstance = useMemo(() => {
    return (
      filteredInstances.find(i => i.id === selectedId) ||
      instances.find(i => i.id === selectedId) ||
      filteredInstances[0] ||
      instances[0] ||
      null
    );
  }, [filteredInstances, instances, selectedId]);

  const allWebsitesList = useMemo(() => {
    return availableWebsites.length > 0 ? availableWebsites : websites;
  }, [availableWebsites, websites]);

  const installableWebsites = useMemo(() => {
    const installedIds = new Set(instances.map(inst => Number(inst.website_id || inst.id)));
    return (allWebsitesList || []).filter(
      w => !w.occupied && w.app_type !== 'wordpress' && !installedIds.has(Number(w.id))
    );
  }, [allWebsitesList, instances]);

  const userPkg = useMemo(
    () =>
      (ctx.packages || []).find(
        p =>
          p.id === ctx.currentUser?.package_id ||
          (ctx.currentUser?.package_name && p.name === ctx.currentUser.package_name)
      ),
    [ctx.packages, ctx.currentUser]
  );

  const canInstallLaravel = useMemo(() => {
    if (quotaInfo && typeof quotaInfo.can_install === 'boolean') {
      return quotaInfo.can_install && installableWebsites.length > 0;
    }
    if (installableWebsites.length === 0) return false;
    if (ctx.currentUser?.role === 'admin') return true;
    const wLimit = Number(ctx.currentUser?.website_limit ?? userPkg?.website_limit ?? -1);
    const sLimit = Number(ctx.currentUser?.subdomain_limit ?? userPkg?.subdomain_limit ?? 0);
    if (wLimit >= 0 && sLimit >= 0) {
      const maxTotal = wLimit + sLimit;
      const occupiedCount =
        (allWebsitesList || []).filter(w => w.occupied || w.app_type === 'wordpress').length ||
        instances.length;
      if (maxTotal === 0 || occupiedCount >= maxTotal) return false;
    }
    return true;
  }, [quotaInfo, installableWebsites, ctx.currentUser, userPkg, allWebsitesList, instances]);

  // ── Actions ────────────────────────────────────────────────────────────────

  async function handleScanSites() {
    markBusy('scan', true);
    try {
      const res = await request(
        '/laravel-manager/scan',
        { method: 'POST' },
        t('Scanning domains for Laravel projects...')
      );
      if (res) {
        setNotice(res.message || 'Đã quét các dự án Laravel.');
        await loadInstances(true);
      }
    } finally {
      markBusy('scan', false);
    }
  }

  function openInstallModal() {
    if (!canInstallLaravel) return;
    const firstSite = installableWebsites[0];
    if (!firstSite) return;
    setInstallForm(prev => ({
      ...prev,
      website_id: firstSite ? String(firstSite.id) : '',
      app_name: firstSite ? `${firstSite.domain} App` : 'My Laravel Application',
      php_version: firstSite?.php_version || '8.3',
    }));
    setInstallModalOpen(true);
  }

  async function handleInstallLaravel(e) {
    e?.preventDefault?.();
    if (!installForm.website_id) {
      setError('Vui lòng chọn tên miền để cài đặt dự án Laravel.');
      return;
    }
    markBusy('install', true);
    try {
      const payload = {
        ...installForm,
        website_id: Number(installForm.website_id),
        queue_workers: Number(installForm.queue_workers) || 2,
      };
      const res = await request(
        '/laravel-manager/install',
        { method: 'POST', body: JSON.stringify(payload) },
        'Đang cài đặt dự án Laravel & cấu hình /public...'
      );
      if (res) {
        setNotice(res.message || 'Đã cài đặt dự án Laravel thành công!');
        setInstallModalOpen(false);
        if (res.credentials) {
          setInstalledCredentials({
            domain: res.instance?.domain || '',
            url: res.instance?.url || '',
            ...res.credentials,
          });
        }
        await loadInstances(true);
        if (res.instance?.id) {
          setSelectedId(res.instance.id);
          setExpandedIds(prev => ({ ...prev, [res.instance.id]: true }));
        }
      }
    } finally {
      markBusy('install', false);
    }
  }

  async function handleRunArtisan(siteId, command, extra = {}) {
    const key = `artisan-${siteId}-${command}`;
    markBusy(key, true);
    try {
      const res = await request(
        `/laravel-manager/instances/${siteId}/artisan`,
        {
          method: 'POST',
          body: JSON.stringify({ command, ...extra }),
        },
        `Đang chạy php artisan ${command}...`
      );
      if (res) {
        setNotice(res.message || `Đã chạy artisan ${command}.`);
        if (res.instance) updateSingleInstance(res.instance);
      }
    } finally {
      markBusy(key, false);
    }
  }

  async function handleQueueAction(siteId, action, extra = {}) {
    const key = `queue-${siteId}-${action}`;
    markBusy(key, true);
    try {
      const draft = queueDrafts[siteId] || {};
      const res = await request(
        `/laravel-manager/instances/${siteId}/queue`,
        {
          method: 'POST',
          body: JSON.stringify({
            action,
            ...draft,
            ...extra,
          }),
        },
        'Đang cập nhật Laravel Queue Worker...'
      );
      if (res) {
        setNotice(res.message || 'Đã cập nhật Queue Worker.');
        if (res.instance) updateSingleInstance(res.instance);
      }
    } finally {
      markBusy(key, false);
    }
  }

  async function handleToggleScheduler(siteId, enabled) {
    const key = `scheduler-${siteId}`;
    markBusy(key, true);
    try {
      const res = await request(
        `/laravel-manager/instances/${siteId}/scheduler`,
        {
          method: 'POST',
          body: JSON.stringify({ enabled }),
        },
        'Đang cập nhật Laravel Scheduler (Cron)...'
      );
      if (res) {
        setNotice(res.message || 'Đã cập nhật Laravel Scheduler.');
        if (res.instance) updateSingleInstance(res.instance);
      }
    } finally {
      markBusy(key, false);
    }
  }

  async function handleQuickToggleDebug(inst) {
    const key = `debug-${inst.id}`;
    markBusy(key, true);
    try {
      const nextDebug = !inst.app_debug;
      const res = await request(
        `/laravel-manager/instances/${inst.id}/env`,
        {
          method: 'PUT',
          body: JSON.stringify({ app_debug: nextDebug }),
        },
        'Đang cập nhật chế độ APP_DEBUG...'
      );
      if (res) {
        setNotice(
          nextDebug
            ? `Đã bật chế độ gỡ lỗi (APP_DEBUG=true) cho ${inst.domain}.`
            : `Đã tắt chế độ gỡ lỗi (APP_DEBUG=false) để bảo mật Production cho ${inst.domain}.`
        );
        if (res.instance) updateSingleInstance(res.instance);
      }
    } finally {
      markBusy(key, false);
    }
  }

  async function openEnvEditor(inst, initialTab = 'visual') {
    setEnvModalSite(inst);
    setEnvModalTab(initialTab);
    setEnvLoading(true);
    try {
      const res = await request(`/laravel-manager/instances/${inst.id}/env`, { silent: true });
      if (res) {
        setEnvDraft({
          raw_env: res.raw_env || '',
          parsed: res.parsed || {},
          php_version: inst.php_version || '8.3',
        });
      }
    } finally {
      setEnvLoading(false);
    }
  }

  async function handleSaveEnv(siteId, mode = 'visual') {
    const key = `env-save-${siteId}`;
    markBusy(key, true);
    try {
      const p = envDraft.parsed || {};
      const body =
        mode === 'raw'
          ? { raw_env: envDraft.raw_env, php_version: envDraft.php_version }
          : {
              app_name: p.app_name,
              app_env: p.app_env,
              app_debug: !!p.app_debug,
              app_url: p.app_url,
              cache_driver: p.cache_driver,
              queue_connection: p.queue_connection,
              session_driver: p.session_driver,
              db_host: p.db_host,
              db_port: p.db_port,
              db_name: p.db_name,
              db_user: p.db_user,
              db_password: p.db_password,
              redis_host: p.redis_host,
              redis_port: p.redis_port,
              mail_mailer: p.mail_mailer,
              mail_host: p.mail_host,
              mail_port: p.mail_port,
              mail_username: p.mail_username,
              mail_encryption: p.mail_encryption,
              mail_from_address: p.mail_from_address,
              php_version: envDraft.php_version,
            };
      const res = await request(
        `/laravel-manager/instances/${siteId}/env`,
        { method: 'PUT', body: JSON.stringify(body) },
        'Đang lưu tệp .env và xóa cache cấu hình...'
      );
      if (res) {
        setNotice(res.message || 'Đã lưu tệp .env thành công.');
        setEnvDraft(prev => ({
          ...prev,
          raw_env: res.raw_env || prev.raw_env,
          parsed: res.parsed || prev.parsed,
        }));
        if (res.instance) updateSingleInstance(res.instance);
        setEnvModalSite(null);
      }
    } finally {
      markBusy(key, false);
    }
  }

  async function handleComposerAction(siteId, action, extra = {}) {
    const key = `composer-${siteId}-${action}-${extra.package || ''}`;
    markBusy(key, true);
    try {
      const res = await request(
        `/laravel-manager/instances/${siteId}/composer`,
        {
          method: 'POST',
          body: JSON.stringify({ action, ...extra }),
        },
        `Đang thực thi Composer (${action})...`
      );
      if (res) {
        setNotice(res.message || 'Đã thực thi lệnh Composer.');
        if (res.instance) updateSingleInstance(res.instance);
        if (action === 'require') {
          setComposerInputs(prev => ({ ...prev, [siteId]: '' }));
        }
      }
    } finally {
      markBusy(key, false);
    }
  }

  async function handleSecurityAction(siteId, action = 'secure', checkIds = null) {
    const key = `sec-${siteId}-${action}`;
    markBusy(key, true);
    try {
      const selectedMap = securityChecksSel[siteId] || {};
      const ids =
        checkIds ||
        Object.entries(selectedMap)
          .filter(([, checked]) => checked)
          .map(([id]) => id);
      const res = await request(
        `/laravel-manager/instances/${siteId}/security`,
        {
          method: 'POST',
          body: JSON.stringify({ action, check_ids: ids }),
        },
        'Đang áp dụng cấu hình bảo mật Laravel...'
      );
      if (res) {
        setNotice(res.message || 'Đã cập nhật bảo mật Laravel.');
        if (res.instance) updateSingleInstance(res.instance);
      }
    } finally {
      markBusy(key, false);
    }
  }

  async function openLogsModal(inst) {
    setLogModalSite(inst);
    setLogLoading(true);
    try {
      const res = await request(`/laravel-manager/instances/${inst.id}/logs?lines=200`, {
        silent: true,
      });
      if (res) {
        setLogContent(res.content || '');
        setLogPath(res.log_path || 'storage/logs/laravel.log');
      }
    } finally {
      setLogLoading(false);
    }
  }

  async function handleClearLogs(siteId) {
    markBusy(`clear-log-${siteId}`, true);
    try {
      const res = await request(
        `/laravel-manager/instances/${siteId}/logs`,
        { method: 'DELETE' },
        'Đang dọn sạch tệp laravel.log...'
      );
      if (res) {
        setNotice(res.message || 'Đã xóa nội dung laravel.log.');
        setLogContent('');
      }
    } finally {
      markBusy(`clear-log-${siteId}`, false);
    }
  }

  async function handleDetachSite(inst) {
    if (
      !window.confirm(
        `Ngắt liên kết ${inst.domain} khỏi Laravel Manager? (Toàn bộ mã nguồn và Database vẫn được giữ nguyên)`
      )
    ) {
      return;
    }
    markBusy(`detach-${inst.id}`, true);
    try {
      const res = await request(
        `/laravel-manager/instances/${inst.id}/detach`,
        { method: 'POST' },
        'Đang ngắt liên kết...'
      );
      if (res) {
        setNotice(res.message);
        await loadInstances(true);
      }
    } finally {
      markBusy(`detach-${inst.id}`, false);
    }
  }

  async function handleRemoveSite() {
    if (!removeModalSite) return;
    const siteId = removeModalSite.id;
    markBusy(`remove-${siteId}`, true);
    try {
      const res = await request(
        `/laravel-manager/instances/${siteId}/remove?drop_database=${removeDropDb ? 'true' : 'false'}`,
        { method: 'POST' },
        'Đang gỡ cài đặt dự án Laravel...'
      );
      if (res) {
        setNotice(res.message);
        setRemoveModalSite(null);
        setRemoveDropDb(false);
        await loadInstances(true);
      }
    } finally {
      markBusy(`remove-${siteId}`, false);
    }
  }

  // ── Sub-renderers ──────────────────────────────────────────────────────────

  function renderProjectMinimalCard(inst) {
    const isSelected = selectedInstance?.id === inst.id;
    const isDown = !!inst.maintenance_mode;
    const isDebug = !!inst.app_debug;
    const statusDotColor = isDown
      ? '#f59e0b'
      : inst.security_status === 'critical'
      ? '#ef4444'
      : '#10b981';
    const badgeLabel = isDown
      ? 'Maintenance'
      : isDebug
      ? 'APP_DEBUG: ON'
      : 'Healthy';
    const badgeBg = isDown
      ? 'rgba(245, 158, 11, 0.14)'
      : isDebug
      ? 'rgba(239, 68, 68, 0.12)'
      : 'var(--surface-alt)';
    const badgeColor = isDown ? '#d97706' : isDebug ? '#dc2626' : '#059669';
    const qw = inst.queue_worker || {};
    const queueSummary = qw.enabled
      ? `${qw.workers || 1} Worker${(qw.workers || 1) > 1 ? 's' : ''}`
      : 'Off';
    const cronSummary = inst.scheduler_enabled ? 'ON' : 'OFF';
    const shortRoot = `.../${(inst.document_root || 'public_html/public').replace(/^\/+/, '')}`;

    return (
      <div
        key={inst.id}
        className={`lv-minimal-card ${isSelected ? 'selected' : ''}`}
        onClick={() => setSelectedId(inst.id)}
        style={{
          position: 'relative',
          borderRadius: '14px',
          padding: '16px 18px',
          background: 'var(--surface)',
          border: isSelected ? '1.5px solid #0f172a' : '1px solid var(--border)',
          boxShadow: isSelected
            ? '0 8px 24px rgba(15, 23, 42, 0.08)'
            : '0 1px 3px rgba(15, 23, 42, 0.03)',
          cursor: 'pointer',
          transition: 'all 0.16s ease',
          display: 'flex',
          flexDirection: 'column',
          gap: '8px',
        }}
      >
        {/* Top Row: Status Dot + Domain + Status Badge / Hover Icon Bar */}
        <div
          style={{
            display: 'flex',
            alignItems: 'flex-start',
            justifyContent: 'space-between',
            gap: '10px',
          }}
        >
          <div style={{ minWidth: 0 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
              <span
                style={{
                  width: '9px',
                  height: '9px',
                  borderRadius: '999px',
                  background: statusDotColor,
                  flexShrink: 0,
                }}
              />
              <strong
                style={{
                  fontSize: '15px',
                  fontWeight: 700,
                  color: 'var(--text)',
                  overflow: 'hidden',
                  textOverflow: 'ellipsis',
                  whiteSpace: 'nowrap',
                }}
              >
                {inst.domain}
                {inst.subpath || ''}
              </strong>
            </div>
            <div
              style={{
                fontSize: '12px',
                color: 'var(--text-muted)',
                marginTop: '3px',
                paddingLeft: '17px',
              }}
            >
              Laravel {inst.laravel_version || '11.x'} · PHP {inst.php_version || '8.3'}-FPM
            </div>
          </div>

          {/* Right side: Default Status Pill OR Hover Action Bar */}
          <div style={{ position: 'relative', flexShrink: 0 }}>
            <span
              className="lv-card-status-pill"
              style={{
                display: 'inline-flex',
                alignItems: 'center',
                padding: '4px 10px',
                borderRadius: '7px',
                fontSize: '11.5px',
                fontWeight: 600,
                background: badgeBg,
                color: badgeColor,
                border: '1px solid var(--border)',
              }}
            >
              {badgeLabel}
            </span>

            {/* Hover Action Toolbar matching Mockup #3 */}
            <div
              className="lv-card-hover-toolbar"
              onClick={e => e.stopPropagation()}
              style={{
                display: 'none',
                alignItems: 'center',
                gap: '3px',
                padding: '3px 5px',
                borderRadius: '8px',
                background: 'var(--surface-alt)',
                border: '1px solid var(--border)',
              }}
            >
              <button
                type="button"
                className="lv-icon-action-btn primary-dark"
                title="1-Click Artisan & Queue Worker"
                onClick={() => setSelectedId(inst.id)}
              >
                <Zap size={13} />
              </button>
              <button
                type="button"
                className="lv-icon-action-btn"
                title="Xóa toàn bộ Cache (artisan optimize:clear)"
                disabled={isBusy(`artisan-${inst.id}-optimize:clear`)}
                onClick={() => handleRunArtisan(inst.id, 'optimize:clear')}
              >
                <RefreshCw
                  size={13}
                  className={isBusy(`artisan-${inst.id}-optimize:clear`) ? 'spin' : ''}
                />
              </button>
              <button
                type="button"
                className="lv-icon-action-btn"
                title="Mở trình sửa file .env đầy đủ"
                onClick={() => openEnvEditor(inst, 'visual')}
              >
                <Code2 size={13} />
              </button>
              <button
                type="button"
                className="lv-icon-action-btn"
                title="Mở Bảng điều khiển chi tiết 5 Tab"
                onClick={() => {
                  setSelectedId(inst.id);
                  setExpandedIds(prev => ({ ...prev, [inst.id]: true }));
                  setViewMode('expanded');
                }}
              >
                <Terminal size={13} />
              </button>
              <button
                type="button"
                className="lv-icon-action-btn"
                title="Xem nhật ký lỗi (storage/logs/laravel.log)"
                onClick={() => openLogsModal(inst)}
              >
                <FileText size={13} />
              </button>
              <button
                type="button"
                className="lv-icon-action-btn"
                title="Mở File Manager tại thư mục dự án"
                onClick={() =>
                  openWebsiteFileManager &&
                  openWebsiteFileManager({ id: inst.website_id, domain: inst.domain })
                }
              >
                <FolderOpen size={13} />
              </button>
            </div>
          </div>
        </div>

        {/* Middle Row: Security / Driver Info */}
        <div
          style={{
            fontSize: '11.5px',
            color: 'var(--text-muted)',
            paddingLeft: '17px',
            display: 'flex',
            alignItems: 'center',
            gap: '6px',
            flexWrap: 'wrap',
          }}
        >
          <span>
            WAF Laravel Protection: {inst.waf_enabled ? 'ON' : 'OFF'} · APP_DEBUG:{' '}
            {inst.app_debug ? 'true' : 'false'}
          </span>
          <span>·</span>
          <span>
            Composer {inst.composer_version || '2.8'} · Cache: {inst.cache_driver || 'file'}
          </span>
        </div>

        {/* Footer Divider + Root & Queue/Cron summary */}
        <div
          style={{
            marginTop: '4px',
            paddingTop: '10px',
            borderTop: '1px solid var(--border)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            gap: '8px',
            fontSize: '12px',
          }}
        >
          <strong
            style={{
              color: inst.public_docroot_active ? 'var(--text)' : '#d97706',
              fontWeight: 700,
              fontSize: '12px',
            }}
            title={`Document Root: /${inst.document_root}`}
          >
            Root: {shortRoot}
          </strong>
          <span
            style={{
              fontFamily: 'var(--font-mono, monospace)',
              fontSize: '11.5px',
              color: 'var(--text-muted)',
            }}
          >
            Queue: {queueSummary} · Cron: {cronSummary}
          </span>
        </div>
      </div>
    );
  }

  function renderRightArtisanDrawer(inst) {
    if (!inst) return null;
    const qw = inst.queue_worker || {};
    const phpLabel = `php${inst.php_version || '8.3'}`;
    const logs = (inst.last_artisan_logs || []).slice(-3);

    return (
      <aside
        style={{
          borderRadius: '18px',
          padding: '20px',
          background: 'var(--surface)',
          border: '1.5px solid var(--border-strong, #cbd5e1)',
          boxShadow: '0 10px 30px rgba(15, 23, 42, 0.06)',
          display: 'flex',
          flexDirection: 'column',
          gap: '16px',
          position: 'sticky',
          top: '16px',
        }}
      >
        {/* Drawer Header */}
        <div style={{ borderBottom: '1px solid var(--border)', paddingBottom: '12px' }}>
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
              gap: '8px',
            }}
          >
            <h3 style={{ margin: 0, fontSize: '15.5px', fontWeight: 800, color: 'var(--text)' }}>
              Laravel Artisan Toolkit & Queue Worker
            </h3>
            <a
              href={inst.url}
              target="_blank"
              rel="noopener noreferrer"
              title="Mở website trong tab mới"
              style={{
                display: 'inline-flex',
                alignItems: 'center',
                gap: '4px',
                fontSize: '12px',
                fontWeight: 600,
                color: 'var(--primary)',
                textDecoration: 'none',
              }}
            >
              <ExternalLink size={13} />
            </a>
          </div>
          <div style={{ fontSize: '12px', color: 'var(--text-muted)', marginTop: '3px' }}>
            Website: <strong style={{ color: 'var(--text)' }}>{inst.domain}</strong> (Khi bấm Icon
            Tia chớp Artisan)
          </div>
        </div>

        {/* 1. THAO TÁC NHANH ARTISAN (1-CLICK GUI) */}
        <div>
          <div
            style={{
              fontSize: '11px',
              fontWeight: 800,
              letterSpacing: '0.04em',
              textTransform: 'uppercase',
              color: 'var(--text-muted)',
              marginBottom: '8px',
            }}
          >
            1. THAO TÁC NHANH ARTISAN (1-CLICK GUI)
          </div>
          <div
            style={{
              display: 'grid',
              gridTemplateColumns: 'repeat(2, minmax(0, 1fr))',
              gap: '8px',
            }}
          >
            {ARTISAN_QUICK_ACTIONS.map(act => {
              const busy = isBusy(`artisan-${inst.id}-${act.cmd}`);
              return (
                <button
                  key={act.cmd}
                  type="button"
                  disabled={busy}
                  onClick={() => handleRunArtisan(inst.id, act.cmd)}
                  title={act.desc}
                  style={{
                    height: 'auto',
                    minHeight: '54px',
                    padding: '10px 12px',
                    borderRadius: '10px',
                    background: 'var(--surface-alt)',
                    border: '1px solid var(--border)',
                    display: 'flex',
                    flexDirection: 'column',
                    alignItems: 'flex-start',
                    justifyContent: 'center',
                    gap: '2px',
                    textAlign: 'left',
                    cursor: busy ? 'wait' : 'pointer',
                  }}
                >
                  <strong style={{ fontSize: '13px', fontWeight: 700, color: 'var(--text)' }}>
                    {busy ? 'Đang chạy...' : act.title}
                  </strong>
                  <span style={{ fontSize: '11.5px', color: 'var(--text-muted)', fontWeight: 500 }}>
                    {act.subtitle}
                  </span>
                </button>
              );
            })}
          </div>
          {/* Extra Quick Pills */}
          <div style={{ display: 'flex', gap: '6px', flexWrap: 'wrap', marginTop: '8px' }}>
            {['route:cache', 'view:cache', 'queue:restart'].map(cmd => (
              <button
                key={cmd}
                type="button"
                className="secondary-light mini"
                disabled={isBusy(`artisan-${inst.id}-${cmd}`)}
                onClick={() => handleRunArtisan(inst.id, cmd)}
                style={{
                  height: '26px',
                  minHeight: '26px',
                  padding: '0 9px',
                  fontSize: '11.5px',
                  borderRadius: '6px',
                }}
              >
                {cmd}
              </button>
            ))}
            <button
              type="button"
              className="secondary-light mini"
              disabled={isBusy(`artisan-${inst.id}-${inst.maintenance_mode ? 'up' : 'down'}`)}
              onClick={() =>
                handleRunArtisan(inst.id, inst.maintenance_mode ? 'up' : 'down')
              }
              style={{
                height: '26px',
                minHeight: '26px',
                padding: '0 9px',
                fontSize: '11.5px',
                borderRadius: '6px',
                color: inst.maintenance_mode ? '#059669' : '#d97706',
              }}
            >
              {inst.maintenance_mode ? 'Tắt Bảo trì (artisan up)' : 'Bật Bảo trì (artisan down)'}
            </button>
          </div>
        </div>

        {/* 2. QUẢN LÝ LARAVEL QUEUE WORKER (SYSTEMD) */}
        <div>
          <div
            style={{
              fontSize: '11px',
              fontWeight: 800,
              letterSpacing: '0.04em',
              textTransform: 'uppercase',
              color: 'var(--text-muted)',
              marginBottom: '8px',
            }}
          >
            2. QUẢN LÝ LARAVEL QUEUE WORKER (SYSTEMD)
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
            {/* Queue Worker Row */}
            <div
              style={{
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'space-between',
                gap: '10px',
                padding: '10px 12px',
                borderRadius: '10px',
                background: 'var(--surface)',
                border: '1px solid var(--border)',
              }}
            >
              <div style={{ minWidth: 0 }}>
                <strong
                  style={{
                    display: 'block',
                    fontSize: '13px',
                    fontWeight: 700,
                    color: 'var(--text)',
                  }}
                >
                  {phpLabel} artisan queue:work
                </strong>
                <span style={{ fontSize: '11.5px', color: 'var(--text-muted)' }}>
                  {qw.workers || 2} tiến trình chạy nền · Tự bật lại khi lỗi
                </span>
              </div>
              <button
                type="button"
                disabled={isBusy(`queue-${inst.id}-toggle`)}
                onClick={() =>
                  handleQueueAction(inst.id, 'toggle', { enabled: !qw.enabled })
                }
                style={{
                  height: '30px',
                  minHeight: '30px',
                  padding: '0 12px',
                  borderRadius: '7px',
                  fontSize: '12px',
                  fontWeight: 700,
                  background: qw.enabled ? '#0f172a' : 'var(--surface-alt)',
                  color: qw.enabled ? '#ffffff' : 'var(--text-muted)',
                  border: qw.enabled ? '1px solid #0f172a' : '1px solid var(--border)',
                  flexShrink: 0,
                }}
              >
                {qw.enabled ? 'Đang Bật' : 'Đang Tắt'}
              </button>
            </div>

            {/* Scheduler Row */}
            <div
              style={{
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'space-between',
                gap: '10px',
                padding: '10px 12px',
                borderRadius: '10px',
                background: 'var(--surface)',
                border: '1px solid var(--border)',
              }}
            >
              <div style={{ minWidth: 0 }}>
                <strong
                  style={{
                    display: 'block',
                    fontSize: '13px',
                    fontWeight: 700,
                    color: 'var(--text)',
                  }}
                >
                  Laravel Scheduler (Cron)
                </strong>
                <span
                  style={{
                    fontSize: '11.5px',
                    color: 'var(--text-muted)',
                    fontFamily: 'var(--font-mono, monospace)',
                  }}
                >
                  * * * * * {phpLabel} artisan schedule:run
                </span>
              </div>
              <button
                type="button"
                disabled={isBusy(`scheduler-${inst.id}`)}
                onClick={() => handleToggleScheduler(inst.id, !inst.scheduler_enabled)}
                style={{
                  height: '30px',
                  minHeight: '30px',
                  padding: '0 12px',
                  borderRadius: '7px',
                  fontSize: '12px',
                  fontWeight: 700,
                  background: inst.scheduler_enabled ? '#0f172a' : 'var(--surface-alt)',
                  color: inst.scheduler_enabled ? '#ffffff' : 'var(--text-muted)',
                  border: inst.scheduler_enabled ? '1px solid #0f172a' : '1px solid var(--border)',
                  flexShrink: 0,
                }}
              >
                {inst.scheduler_enabled ? 'Đang Bật' : 'Đang Tắt'}
              </button>
            </div>
          </div>
        </div>

        {/* 3. CẤU HÌNH NHANH MÔI TRƯỜNG (.ENV) */}
        <div>
          <div
            style={{
              fontSize: '11px',
              fontWeight: 800,
              letterSpacing: '0.04em',
              textTransform: 'uppercase',
              color: 'var(--text-muted)',
              marginBottom: '8px',
            }}
          >
            3. CẤU HÌNH NHANH MÔI TRƯỜNG (.ENV)
          </div>

          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
              gap: '10px',
              padding: '10px 12px',
              borderRadius: '10px',
              background: 'var(--surface)',
              border: '1px solid var(--border)',
              marginBottom: '10px',
            }}
          >
            <div style={{ minWidth: 0 }}>
              <strong
                style={{
                  display: 'block',
                  fontSize: '13px',
                  fontWeight: 700,
                  color: 'var(--text)',
                }}
              >
                Chế độ Gỡ lỗi (APP_DEBUG)
              </strong>
              <span style={{ fontSize: '11.5px', color: 'var(--text-muted)' }}>
                Tắt trên môi trường Production để bảo mật
              </span>
            </div>
            <button
              type="button"
              disabled={isBusy(`debug-${inst.id}`)}
              onClick={() => handleQuickToggleDebug(inst)}
              style={{
                height: '30px',
                minHeight: '30px',
                padding: '0 14px',
                borderRadius: '7px',
                fontSize: '12px',
                fontWeight: 700,
                background: inst.app_debug ? 'rgba(239, 68, 68, 0.14)' : 'var(--surface-alt)',
                color: inst.app_debug ? '#dc2626' : 'var(--text)',
                border: inst.app_debug
                  ? '1px solid rgba(239, 68, 68, 0.35)'
                  : '1px solid var(--border)',
                flexShrink: 0,
              }}
            >
              {inst.app_debug ? 'ON' : 'OFF'}
            </button>
          </div>

          {/* Terminal Output Box */}
          <div
            style={{
              borderRadius: '10px',
              padding: '10px 12px',
              background: 'var(--surface-alt)',
              border: '1px solid var(--border)',
              fontFamily: 'var(--font-mono, monospace)',
              fontSize: '11.5px',
              lineHeight: 1.55,
              color: 'var(--text)',
              maxHeight: '130px',
              overflowY: 'auto',
              marginBottom: '12px',
            }}
          >
            {logs.map((entry, idx) => (
              <div key={idx} style={{ marginBottom: idx < logs.length - 1 ? '6px' : 0 }}>
                <div style={{ fontWeight: 700, color: 'var(--text)' }}>$ {entry.cmd}</div>
                <div
                  style={{
                    color: entry.status === 'error' ? '#dc2626' : '#059669',
                    whiteSpace: 'pre-wrap',
                    wordBreak: 'break-word',
                  }}
                >
                  {entry.output}
                </div>
              </div>
            ))}
          </div>

          {/* Full .env Editor Button */}
          <button
            type="button"
            onClick={() => openEnvEditor(inst, 'visual')}
            style={{
              width: '100%',
              height: '40px',
              minHeight: '40px',
              borderRadius: '10px',
              background: '#0f172a',
              color: '#ffffff',
              border: '1px solid #0f172a',
              fontSize: '13px',
              fontWeight: 700,
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              gap: '8px',
            }}
          >
            <Code2 size={15} />
            <span>Mở trình sửa file .env đầy đủ</span>
          </button>

          <button
            type="button"
            className="secondary-light"
            onClick={() => {
              setExpandedIds(prev => ({ ...prev, [inst.id]: true }));
              setViewMode('expanded');
            }}
            style={{
              width: '100%',
              height: '34px',
              minHeight: '34px',
              marginTop: '8px',
              borderRadius: '9px',
              fontSize: '12px',
              fontWeight: 600,
            }}
          >
            <Sliders size={13} />
            <span>Mở Bảng điều khiển chuyên sâu (5 Tabs: Composer, Bảo mật, Logs...)</span>
          </button>
        </div>
      </aside>
    );
  }

  // ── Expanded 2-Column Workbench (WordPress Manager Style) ──────────────────

  function renderExpandedInstanceCard(inst) {
    const isExpanded = !!expandedIds[inst.id];
    const activeTab = activeSubTabs[inst.id] || 'overview';
    const qw = inst.queue_worker || {};
    const qDraft = queueDrafts[inst.id] || {
      workers: qw.workers || 2,
      queue: qw.queue || 'default',
      tries: qw.tries || 3,
      timeout: qw.timeout || 90,
      memory: qw.memory || 128,
    };
    const secSel = securityChecksSel[inst.id] || {};
    const gitDraft = gitInputs[inst.id] || {
      git_repo: inst.git_repo || '',
      git_branch: inst.git_branch || 'main',
    };

    const subTabs = [
      { id: 'overview', label: 'Tổng quan & Artisan', icon: Zap },
      { id: 'queue', label: `Queue & Scheduler (${qw.enabled ? 'ON' : 'OFF'})`, icon: Cpu },
      { id: 'composer', label: `Composer & Git (${inst.packages_count || 0})`, icon: PackageOpen },
      {
        id: 'security',
        label: `Bảo mật & Logs (${inst.security_passed}/${inst.security_total})`,
        icon: ShieldCheck,
      },
    ];

    return (
      <div
        key={inst.id}
        style={{
          borderRadius: '16px',
          background: 'var(--surface)',
          border: '1px solid var(--border)',
          boxShadow: '0 2px 10px rgba(15, 23, 42, 0.04)',
          overflow: 'hidden',
        }}
      >
        {/* Card Header Bar */}
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            flexWrap: 'wrap',
            gap: '12px',
            padding: '14px 20px',
            background: 'var(--surface-alt)',
            borderBottom: isExpanded ? '1px solid var(--border)' : 'none',
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: '12px', minWidth: 0 }}>
            <button
              type="button"
              className="secondary-light mini"
              onClick={() =>
                setExpandedIds(prev => ({ ...prev, [inst.id]: !prev[inst.id] }))
              }
              style={{
                width: '30px',
                minWidth: '30px',
                height: '30px',
                minHeight: '30px',
                padding: 0,
                borderRadius: '8px',
              }}
            >
              {isExpanded ? <ChevronUp size={15} /> : <ChevronDown size={15} />}
            </button>
            <div>
              <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap' }}>
                <strong style={{ fontSize: '15.5px', fontWeight: 800, color: 'var(--text)' }}>
                  {inst.domain}
                  {inst.subpath || ''}
                </strong>
                <span className="badge" style={{ fontSize: '11px' }}>
                  Laravel {inst.laravel_version || '11.x'}
                </span>
                <span className="badge" style={{ fontSize: '11px' }}>
                  PHP {inst.php_version}-FPM
                </span>
                <span
                  className="badge"
                  style={{
                    fontSize: '11px',
                    background: inst.public_docroot_active
                      ? 'rgba(16, 185, 129, 0.14)'
                      : 'rgba(245, 158, 11, 0.16)',
                    color: inst.public_docroot_active ? '#059669' : '#d97706',
                  }}
                >
                  Root: /{inst.document_root}
                </span>
              </div>
              <div style={{ fontSize: '12px', color: 'var(--text-muted)', marginTop: '2px' }}>
                {inst.app_name} · ENV: <strong>{inst.app_env}</strong> · DB:{' '}
                <code>{inst.db_name || 'N/A'}</code> ({inst.database?.table_count || 0} bảng ·{' '}
                {inst.db_size_mb || 0} MB)
              </div>
            </div>
          </div>

          <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap' }}>
            <a
              href={inst.url}
              target="_blank"
              rel="noopener noreferrer"
              className="button secondary-light mini"
              style={{
                height: '32px',
                minHeight: '32px',
                padding: '0 12px',
                borderRadius: '8px',
                fontSize: '12px',
                fontWeight: 600,
                textDecoration: 'none',
                display: 'inline-flex',
                alignItems: 'center',
                gap: '5px',
              }}
            >
              <ExternalLink size={13} />
              <span>Mở Website</span>
            </a>
            <button
              type="button"
              className="secondary-light mini"
              onClick={() => openEnvEditor(inst, 'visual')}
              style={{ height: '32px', minHeight: '32px', padding: '0 12px', fontSize: '12px' }}
            >
              <Code2 size={13} />
              <span>Sửa .env</span>
            </button>
            <button
              type="button"
              className="secondary-light mini"
              onClick={() => openLogsModal(inst)}
              style={{ height: '32px', minHeight: '32px', padding: '0 12px', fontSize: '12px' }}
            >
              <FileText size={13} />
              <span>laravel.log</span>
            </button>
            <button
              type="button"
              className="secondary-light mini"
              onClick={() =>
                openWebsiteFileManager &&
                openWebsiteFileManager({ id: inst.website_id, domain: inst.domain })
              }
              style={{ height: '32px', minHeight: '32px', padding: '0 12px', fontSize: '12px' }}
            >
              <FolderOpen size={13} />
              <span>File Manager</span>
            </button>
          </div>
        </div>

        {/* Expanded Body */}
        {isExpanded && (
          <div style={{ padding: '18px 20px' }}>
            {/* Sub-Tab Navigation */}
            <div
              style={{
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'space-between',
                flexWrap: 'wrap',
                gap: '10px',
                paddingBottom: '14px',
                marginBottom: '18px',
                borderBottom: '1px solid var(--border)',
              }}
            >
              <div style={{ display: 'flex', gap: '6px', flexWrap: 'wrap' }}>
                {subTabs.map(tab => {
                  const Icon = tab.icon;
                  const active = activeTab === tab.id;
                  return (
                    <button
                      key={tab.id}
                      type="button"
                      onClick={() =>
                        setActiveSubTabs(prev => ({ ...prev, [inst.id]: tab.id }))
                      }
                      style={{
                        height: '34px',
                        minHeight: '34px',
                        padding: '0 14px',
                        borderRadius: '8px',
                        fontSize: '12.5px',
                        fontWeight: active ? 700 : 600,
                        background: active ? '#0f172a' : 'var(--surface-alt)',
                        color: active ? '#ffffff' : 'var(--text)',
                        border: active ? '1px solid #0f172a' : '1px solid var(--border)',
                        display: 'inline-flex',
                        alignItems: 'center',
                        gap: '6px',
                      }}
                    >
                      <Icon size={14} />
                      <span>{tab.label}</span>
                    </button>
                  );
                })}
              </div>

              <div style={{ display: 'flex', gap: '8px' }}>
                <button
                  type="button"
                  className="secondary-light mini"
                  onClick={() => handleDetachSite(inst)}
                  style={{ height: '30px', minHeight: '30px', fontSize: '11.5px' }}
                >
                  Ngắt liên kết
                </button>
                <button
                  type="button"
                  className="danger mini"
                  onClick={() => {
                    setRemoveModalSite(inst);
                    setRemoveDropDb(false);
                  }}
                  style={{ height: '30px', minHeight: '30px', fontSize: '11.5px' }}
                >
                  <Trash2 size={13} />
                  <span>Gỡ cài đặt</span>
                </button>
              </div>
            </div>

            {/* Sub-Tab 1: Overview & Artisan */}
            {activeTab === 'overview' && (
              <div
                style={{
                  display: 'grid',
                  gridTemplateColumns: 'minmax(260px, 320px) minmax(0, 1fr)',
                  gap: '20px',
                }}
              >
                {/* Left Column: Project Metadata & Quick Toggles */}
                <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
                  <div
                    style={{
                      padding: '14px',
                      borderRadius: '12px',
                      background: 'var(--surface-alt)',
                      border: '1px solid var(--border)',
                      display: 'flex',
                      flexDirection: 'column',
                      gap: '8px',
                      fontSize: '12.5px',
                    }}
                  >
                    <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                      <span style={{ color: 'var(--text-muted)' }}>Phiên bản Laravel:</span>
                      <strong>{inst.laravel_version || '11.x'}</strong>
                    </div>
                    <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                      <span style={{ color: 'var(--text-muted)' }}>PHP Runtime:</span>
                      <strong>PHP {inst.php_version}-FPM</strong>
                    </div>
                    <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                      <span style={{ color: 'var(--text-muted)' }}>Cache / Queue / Session:</span>
                      <strong>
                        {inst.cache_driver} / {inst.queue_connection} / {inst.session_driver}
                      </strong>
                    </div>
                    <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                      <span style={{ color: 'var(--text-muted)' }}>Symlink Storage:</span>
                      <strong style={{ color: inst.storage_linked ? '#059669' : '#d97706' }}>
                        {inst.storage_linked ? 'Đã kết nối (/public/storage)' : 'Chưa gắn'}
                      </strong>
                    </div>
                    <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                      <span style={{ color: 'var(--text-muted)' }}>Khóa mã hóa APP_KEY:</span>
                      <strong style={{ color: inst.app_key_set ? '#059669' : '#dc2626' }}>
                        {inst.app_key_set ? 'Hợp lệ (AES-256-CBC)' : 'Chưa khởi tạo'}
                      </strong>
                    </div>
                  </div>

                  {/* Maintenance Mode Box */}
                  <div
                    style={{
                      padding: '14px',
                      borderRadius: '12px',
                      background: 'var(--surface)',
                      border: '1px solid var(--border)',
                      display: 'flex',
                      flexDirection: 'column',
                      gap: '8px',
                    }}
                  >
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                      <strong style={{ fontSize: '13px' }}>Chế độ Bảo trì (artisan down/up)</strong>
                      <span
                        className="badge"
                        style={{
                          background: inst.maintenance_mode
                            ? 'rgba(245, 158, 11, 0.16)'
                            : 'rgba(16, 185, 129, 0.14)',
                          color: inst.maintenance_mode ? '#d97706' : '#059669',
                        }}
                      >
                        {inst.maintenance_mode ? 'Đang Bảo trì' : 'Hoạt động'}
                      </span>
                    </div>
                    <input
                      type="text"
                      value={maintenanceSecrets[inst.id] ?? inst.maintenance_secret ?? ''}
                      onChange={e =>
                        setMaintenanceSecrets(prev => ({ ...prev, [inst.id]: e.target.value }))
                      }
                      placeholder="Secret bypass (vd: shost-preview-2026)"
                      style={{ height: '34px', fontSize: '12px' }}
                    />
                    <div style={{ display: 'flex', gap: '8px' }}>
                      <button
                        type="button"
                        className="secondary-light mini"
                        disabled={isBusy(`artisan-${inst.id}-down`)}
                        onClick={() =>
                          handleRunArtisan(inst.id, 'down', {
                            maintenance_secret:
                              maintenanceSecrets[inst.id] ?? inst.maintenance_secret ?? '',
                          })
                        }
                        style={{ flex: 1, height: '32px' }}
                      >
                        Bật Bảo trì (down)
                      </button>
                      <button
                        type="button"
                        className="secondary-light mini"
                        disabled={isBusy(`artisan-${inst.id}-up`)}
                        onClick={() => handleRunArtisan(inst.id, 'up')}
                        style={{ flex: 1, height: '32px' }}
                      >
                        Mở lại (up)
                      </button>
                    </div>
                  </div>
                </div>

                {/* Right Column: Full Artisan Command Matrix & Terminal History */}
                <div style={{ display: 'flex', flexDirection: 'column', gap: '14px' }}>
                  <div>
                    <h4 style={{ margin: '0 0 10px', fontSize: '13.5px', fontWeight: 700 }}>
                      Bảng điều khiển Lệnh Artisan 1-Click
                    </h4>
                    <div
                      style={{
                        display: 'grid',
                        gridTemplateColumns: 'repeat(auto-fill, minmax(195px, 1fr))',
                        gap: '8px',
                      }}
                    >
                      {ARTISAN_EXTENDED_COMMANDS.map(item => {
                        const busy = isBusy(`artisan-${inst.id}-${item.cmd}`);
                        return (
                          <button
                            key={item.cmd}
                            type="button"
                            className="secondary-light"
                            disabled={busy}
                            onClick={() => handleRunArtisan(inst.id, item.cmd)}
                            style={{
                              height: '38px',
                              minHeight: '38px',
                              padding: '0 12px',
                              borderRadius: '9px',
                              fontSize: '12px',
                              fontWeight: 600,
                              justifyContent: 'flex-start',
                              gap: '8px',
                            }}
                          >
                            <Terminal size={13} />
                            <span>{busy ? 'Đang chạy...' : item.label}</span>
                          </button>
                        );
                      })}
                    </div>
                  </div>

                  {/* Artisan Console Output */}
                  <div
                    style={{
                      borderRadius: '12px',
                      padding: '14px',
                      background: '#0f172a',
                      color: '#e2e8f0',
                      fontFamily: 'var(--font-mono, monospace)',
                      fontSize: '12px',
                      lineHeight: 1.6,
                      maxHeight: '220px',
                      overflowY: 'auto',
                    }}
                  >
                    {(inst.last_artisan_logs || []).map((entry, idx) => (
                      <div key={idx} style={{ marginBottom: '8px' }}>
                        <div style={{ color: '#93c5fd', fontWeight: 700 }}>$ {entry.cmd}</div>
                        <div
                          style={{
                            color: entry.status === 'error' ? '#fca5a5' : '#86efac',
                            whiteSpace: 'pre-wrap',
                          }}
                        >
                          {entry.output}
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              </div>
            )}

            {/* Sub-Tab 2: Queue Worker & Task Scheduler */}
            {activeTab === 'queue' && (
              <div
                style={{
                  display: 'grid',
                  gridTemplateColumns: 'repeat(auto-fit, minmax(320px, 1fr))',
                  gap: '18px',
                }}
              >
                <div
                  style={{
                    padding: '16px',
                    borderRadius: '12px',
                    border: '1px solid var(--border)',
                    display: 'flex',
                    flexDirection: 'column',
                    gap: '12px',
                  }}
                >
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                    <div>
                      <h4 style={{ margin: 0, fontSize: '14px', fontWeight: 700 }}>
                        Laravel Queue Worker (`artisan queue:work`)
                      </h4>
                      <small style={{ color: 'var(--text-muted)' }}>
                        Chạy tiến trình xử lý hàng đợi nền liên tục và tự động khởi động lại khi lỗi
                      </small>
                    </div>
                    <span
                      className="badge"
                      style={{
                        background: qw.enabled
                          ? 'rgba(16, 185, 129, 0.15)'
                          : 'var(--surface-alt)',
                        color: qw.enabled ? '#059669' : 'var(--text-muted)',
                      }}
                    >
                      {qw.enabled ? `Đang chạy (${qw.workers} workers)` : 'Đang dừng'}
                    </span>
                  </div>

                  <div
                    style={{
                      display: 'grid',
                      gridTemplateColumns: 'repeat(2, minmax(0, 1fr))',
                      gap: '10px',
                    }}
                  >
                    <label>
                      <span style={{ fontSize: '12px' }}>Số tiến trình (Workers 1-8)</span>
                      <input
                        type="number"
                        min="1"
                        max="8"
                        value={qDraft.workers}
                        onChange={e =>
                          setQueueDrafts(prev => ({
                            ...prev,
                            [inst.id]: { ...qDraft, workers: Number(e.target.value) || 1 },
                          }))
                        }
                      />
                    </label>
                    <label>
                      <span style={{ fontSize: '12px' }}>Tên hàng đợi (--queue)</span>
                      <input
                        type="text"
                        value={qDraft.queue}
                        onChange={e =>
                          setQueueDrafts(prev => ({
                            ...prev,
                            [inst.id]: { ...qDraft, queue: e.target.value },
                          }))
                        }
                        placeholder="default,emails"
                      />
                    </label>
                    <label>
                      <span style={{ fontSize: '12px' }}>Số lần thử tối đa (--tries)</span>
                      <input
                        type="number"
                        min="1"
                        max="10"
                        value={qDraft.tries}
                        onChange={e =>
                          setQueueDrafts(prev => ({
                            ...prev,
                            [inst.id]: { ...qDraft, tries: Number(e.target.value) || 3 },
                          }))
                        }
                      />
                    </label>
                    <label>
                      <span style={{ fontSize: '12px' }}>Giới hạn RAM (--memory MB)</span>
                      <input
                        type="number"
                        min="64"
                        max="1024"
                        value={qDraft.memory}
                        onChange={e =>
                          setQueueDrafts(prev => ({
                            ...prev,
                            [inst.id]: { ...qDraft, memory: Number(e.target.value) || 128 },
                          }))
                        }
                      />
                    </label>
                  </div>

                  <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
                    <button
                      type="button"
                      onClick={() =>
                        handleQueueAction(inst.id, qw.enabled ? 'restart' : 'start', {
                          enabled: true,
                        })
                      }
                      style={{ height: '36px', padding: '0 14px' }}
                    >
                      <Play size={14} />
                      <span>{qw.enabled ? 'Lưu & Khởi động lại Worker' : 'Bật Queue Worker'}</span>
                    </button>
                    {qw.enabled && (
                      <button
                        type="button"
                        className="secondary-light"
                        onClick={() => handleQueueAction(inst.id, 'stop', { enabled: false })}
                        style={{ height: '36px', padding: '0 14px' }}
                      >
                        <Square size={14} />
                        <span>Dừng Worker</span>
                      </button>
                    )}
                    <button
                      type="button"
                      className="secondary-light"
                      onClick={() => handleQueueAction(inst.id, 'retry_failed')}
                      style={{ height: '36px', padding: '0 12px' }}
                    >
                      <RotateCcw size={14} />
                      <span>Chạy lại Failed Jobs</span>
                    </button>
                  </div>
                </div>

                {/* Task Scheduler Card */}
                <div
                  style={{
                    padding: '16px',
                    borderRadius: '12px',
                    border: '1px solid var(--border)',
                    display: 'flex',
                    flexDirection: 'column',
                    justifyContent: 'space-between',
                    gap: '12px',
                  }}
                >
                  <div>
                    <div
                      style={{
                        display: 'flex',
                        justifyContent: 'space-between',
                        alignItems: 'center',
                        marginBottom: '6px',
                      }}
                    >
                      <h4 style={{ margin: 0, fontSize: '14px', fontWeight: 700 }}>
                        Laravel Task Scheduler (`schedule:run`)
                      </h4>
                      <span
                        className="badge"
                        style={{
                          background: inst.scheduler_enabled
                            ? 'rgba(16, 185, 129, 0.15)'
                            : 'var(--surface-alt)',
                          color: inst.scheduler_enabled ? '#059669' : 'var(--text-muted)',
                        }}
                      >
                        {inst.scheduler_enabled ? 'Cron: Đang Bật' : 'Cron: Đang Tắt'}
                      </span>
                    </div>
                    <p style={{ margin: '0 0 10px', fontSize: '12.5px', color: 'var(--text-muted)' }}>
                      Tự động đăng ký Cron Job chạy mỗi phút trong môi trường cô lập{' '}
                      <code>spanel-cron-jail</code> để kích hoạt tất cả tác vụ định kỳ trong{' '}
                      <code>routes/console.php</code> hoặc <code>app/Console/Kernel.php</code>.
                    </p>
                    <div
                      style={{
                        padding: '10px 12px',
                        borderRadius: '8px',
                        background: 'var(--surface-alt)',
                        border: '1px solid var(--border)',
                        fontFamily: 'var(--font-mono, monospace)',
                        fontSize: '12px',
                      }}
                    >
                      * * * * * php{inst.php_version} {inst.app_path}/artisan schedule:run &gt;&gt;
                      /dev/null 2&gt;&amp;1
                    </div>
                  </div>

                  <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
                    <button
                      type="button"
                      onClick={() => handleToggleScheduler(inst.id, !inst.scheduler_enabled)}
                      style={{ height: '36px', padding: '0 14px' }}
                    >
                      <Clock size={14} />
                      <span>
                        {inst.scheduler_enabled
                          ? 'Tắt Laravel Scheduler Cron'
                          : 'Bật Laravel Scheduler Cron'}
                      </span>
                    </button>
                    <button
                      type="button"
                      className="secondary-light"
                      onClick={() => handleRunArtisan(inst.id, 'schedule:run')}
                      style={{ height: '36px', padding: '0 14px' }}
                    >
                      <Play size={14} />
                      <span>Chạy thử `schedule:run` ngay</span>
                    </button>
                  </div>
                </div>
              </div>
            )}

            {/* Sub-Tab 3: Composer & Git */}
            {activeTab === 'composer' && (
              <div style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
                <div
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'space-between',
                    flexWrap: 'wrap',
                    gap: '10px',
                  }}
                >
                  <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
                    <button
                      type="button"
                      className="secondary-light"
                      disabled={isBusy(`composer-${inst.id}-install-`)}
                      onClick={() => handleComposerAction(inst.id, 'install')}
                      style={{ height: '34px', fontSize: '12px' }}
                    >
                      <PackageOpen size={14} />
                      <span>composer install --no-dev</span>
                    </button>
                    <button
                      type="button"
                      className="secondary-light"
                      disabled={isBusy(`composer-${inst.id}-dump_autoload-`)}
                      onClick={() => handleComposerAction(inst.id, 'dump_autoload')}
                      style={{ height: '34px', fontSize: '12px' }}
                    >
                      <RefreshCw size={14} />
                      <span>composer dump-autoload -o</span>
                    </button>
                    <button
                      type="button"
                      className="secondary-light"
                      disabled={isBusy(`composer-${inst.id}-update-`)}
                      onClick={() => handleComposerAction(inst.id, 'update')}
                      style={{ height: '34px', fontSize: '12px' }}
                    >
                      <Zap size={14} />
                      <span>composer update</span>
                    </button>
                  </div>

                  {/* Require package input */}
                  <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
                    <input
                      type="text"
                      value={composerInputs[inst.id] || ''}
                      onChange={e =>
                        setComposerInputs(prev => ({ ...prev, [inst.id]: e.target.value }))
                      }
                      placeholder="vendor/package (vd: laravel/sanctum)"
                      style={{ width: '250px', height: '34px', fontSize: '12.5px' }}
                    />
                    <button
                      type="button"
                      disabled={
                        !composerInputs[inst.id]?.trim() ||
                        isBusy(`composer-${inst.id}-require-${composerInputs[inst.id]?.trim()}`)
                      }
                      onClick={() =>
                        handleComposerAction(inst.id, 'require', {
                          package: composerInputs[inst.id]?.trim(),
                        })
                      }
                      style={{ height: '34px', padding: '0 14px', fontSize: '12px' }}
                    >
                      <Plus size={14} />
                      <span>Cài gói (require)</span>
                    </button>
                  </div>
                </div>

                {/* Curated Laravel Ecosystem Packages */}
                <div>
                  <h4 style={{ margin: '0 0 8px', fontSize: '13px', fontWeight: 700 }}>
                    Thư viện Hệ sinh thái Laravel Phổ biến (1-Click Cài đặt)
                  </h4>
                  <div
                    style={{
                      display: 'grid',
                      gridTemplateColumns: 'repeat(auto-fill, minmax(230px, 1fr))',
                      gap: '10px',
                    }}
                  >
                    {curatedPackages.map(pkg => {
                      const installed = (inst.packages || []).some(p => p.name === pkg.name);
                      return (
                        <div
                          key={pkg.name}
                          style={{
                            padding: '12px',
                            borderRadius: '10px',
                            border: '1px solid var(--border)',
                            background: 'var(--surface-alt)',
                            display: 'flex',
                            flexDirection: 'column',
                            justifyContent: 'space-between',
                            gap: '8px',
                          }}
                        >
                          <div>
                            <div
                              style={{
                                display: 'flex',
                                justifyContent: 'space-between',
                                alignItems: 'center',
                                gap: '6px',
                              }}
                            >
                              <strong style={{ fontSize: '13px' }}>{pkg.title}</strong>
                              <span className="badge" style={{ fontSize: '10.5px' }}>
                                {pkg.category}
                              </span>
                            </div>
                            <code
                              style={{
                                display: 'block',
                                fontSize: '11px',
                                color: 'var(--primary)',
                                marginTop: '2px',
                              }}
                            >
                              {pkg.name} ({pkg.version})
                            </code>
                            <p
                              style={{
                                margin: '6px 0 0',
                                fontSize: '11.5px',
                                color: 'var(--text-muted)',
                                lineHeight: 1.4,
                              }}
                            >
                              {pkg.description}
                            </p>
                          </div>
                          <button
                            type="button"
                            className={installed ? 'secondary-light mini' : 'mini'}
                            disabled={isBusy(`composer-${inst.id}-${installed ? 'remove' : 'require'}-${pkg.name}`)}
                            onClick={() =>
                              handleComposerAction(inst.id, installed ? 'remove' : 'require', {
                                package: pkg.name,
                              })
                            }
                            style={{ height: '30px', width: '100%', fontSize: '12px' }}
                          >
                            {installed ? 'Đã cài · Gỡ bỏ' : '+ Cài đặt ngay'}
                          </button>
                        </div>
                      );
                    })}
                  </div>
                </div>

                {/* Git Pull & Repository Sync */}
                <div
                  style={{
                    padding: '14px',
                    borderRadius: '12px',
                    border: '1px solid var(--border)',
                    display: 'flex',
                    alignItems: 'flex-end',
                    flexWrap: 'wrap',
                    gap: '10px',
                  }}
                >
                  <label style={{ flex: '2 1 260px' }}>
                    <span style={{ fontSize: '12px' }}>Git Repository URL (HTTPS / SSH)</span>
                    <input
                      type="text"
                      value={gitDraft.git_repo}
                      onChange={e =>
                        setGitInputs(prev => ({
                          ...prev,
                          [inst.id]: { ...gitDraft, git_repo: e.target.value },
                        }))
                      }
                      placeholder="https://github.com/org/laravel-project.git"
                    />
                  </label>
                  <label style={{ flex: '1 1 130px' }}>
                    <span style={{ fontSize: '12px' }}>Nhánh (Branch)</span>
                    <input
                      type="text"
                      value={gitDraft.git_branch}
                      onChange={e =>
                        setGitInputs(prev => ({
                          ...prev,
                          [inst.id]: { ...gitDraft, git_branch: e.target.value },
                        }))
                      }
                      placeholder="main"
                    />
                  </label>
                  <button
                    type="button"
                    onClick={() =>
                      handleComposerAction(inst.id, 'git_pull', {
                        git_repo: gitDraft.git_repo,
                        git_branch: gitDraft.git_branch,
                      })
                    }
                    style={{ height: '38px', padding: '0 16px' }}
                  >
                    <GitBranch size={14} />
                    <span>Git Pull & Đồng bộ</span>
                  </button>
                </div>
              </div>
            )}

            {/* Sub-Tab 4: Security Hardening & Logs */}
            {activeTab === 'security' && (
              <div style={{ display: 'flex', flexDirection: 'column', gap: '14px' }}>
                <div
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'space-between',
                    flexWrap: 'wrap',
                    gap: '10px',
                  }}
                >
                  <div>
                    <h4 style={{ margin: 0, fontSize: '14px', fontWeight: 700 }}>
                      Danh mục Bảo mật & Gia cố Laravel ({inst.security_passed}/
                      {inst.security_total} đạt chuẩn)
                    </h4>
                    <small style={{ color: 'var(--text-muted)' }}>
                      Kiểm tra Document Root `/public`, khóa `.env`, tắt `APP_DEBUG` và chặn thực
                      thi PHP trong thư mục `storage`
                    </small>
                  </div>
                  <div style={{ display: 'flex', gap: '8px' }}>
                    <button
                      type="button"
                      onClick={() => handleSecurityAction(inst.id, 'secure')}
                      style={{ height: '34px', padding: '0 14px', fontSize: '12px' }}
                    >
                      <ShieldCheck size={14} />
                      <span>Áp dụng Bảo mật Đã chọn</span>
                    </button>
                    <button
                      type="button"
                      className="secondary-light"
                      onClick={() => openLogsModal(inst)}
                      style={{ height: '34px', padding: '0 14px', fontSize: '12px' }}
                    >
                      <FileText size={14} />
                      <span>Xem storage/logs/laravel.log</span>
                    </button>
                  </div>
                </div>

                <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
                  {(inst.security_checks || []).map(chk => (
                    <label
                      key={chk.id}
                      style={{
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'space-between',
                        gap: '12px',
                        padding: '10px 14px',
                        borderRadius: '10px',
                        border: '1px solid var(--border)',
                        background: chk.passed ? 'var(--surface)' : 'rgba(245, 158, 11, 0.05)',
                      }}
                    >
                      <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                        <input
                          type="checkbox"
                          checked={!!secSel[chk.id]}
                          onChange={e =>
                            setSecurityChecksSel(prev => ({
                              ...prev,
                              [inst.id]: { ...secSel, [chk.id]: e.target.checked },
                            }))
                          }
                        />
                        <div>
                          <div style={{ fontWeight: 700, fontSize: '13px', color: 'var(--text)' }}>
                            {chk.title}
                          </div>
                          <div style={{ fontSize: '11.5px', color: 'var(--text-muted)' }}>
                            {chk.description}
                          </div>
                        </div>
                      </div>
                      <span
                        className="badge"
                        style={{
                          flexShrink: 0,
                          background: chk.passed
                            ? 'rgba(16, 185, 129, 0.14)'
                            : 'rgba(239, 68, 68, 0.14)',
                          color: chk.passed ? '#059669' : '#dc2626',
                        }}
                      >
                        {chk.passed ? 'An toàn' : 'Cần xử lý'}
                      </span>
                    </label>
                  ))}
                </div>
              </div>
            )}
          </div>
        )}
      </div>
    );
  }

  return (
    <div className="section laravel-manager-page" style={{ padding: '20px 24px' }}>
      <style>{`
        .lv-minimal-card:hover {
          border-color: #0f172a !important;
          box-shadow: 0 8px 22px rgba(15, 23, 42, 0.08) !important;
        }
        .lv-minimal-card:hover .lv-card-status-pill {
          display: none !important;
        }
        .lv-minimal-card:hover .lv-card-hover-toolbar {
          display: inline-flex !important;
        }
        .lv-icon-action-btn {
          width: 28px !important;
          min-width: 28px !important;
          height: 26px !important;
          min-height: 26px !important;
          padding: 0 !important;
          border-radius: 6px !important;
          background: transparent !important;
          color: var(--text) !important;
          border: 1px solid transparent !important;
          display: inline-flex !important;
          align-items: center !important;
          justify-content: center !important;
        }
        .lv-icon-action-btn:hover {
          background: var(--surface) !important;
          border-color: var(--border) !important;
        }
        .lv-icon-action-btn.primary-dark {
          background: #0f172a !important;
          color: #ffffff !important;
        }
        @media (max-width: 1080px) {
          .lv-cards-drawer-layout {
            grid-template-columns: 1fr !important;
          }
        }
      `}</style>

      {/* 1. Page Header & Breadcrumb (matching WordPress Manager) */}
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          flexWrap: 'wrap',
          gap: '12px',
          marginBottom: '16px',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
          <div
            style={{
              width: '38px',
              height: '38px',
              borderRadius: '10px',
              background: 'color-mix(in srgb, var(--primary) 12%, transparent)',
              color: 'var(--primary)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
            }}
          >
            <LaravelBrandIcon size={22} />
          </div>
          <div>
            <h2 style={{ margin: 0, fontSize: '18px', fontWeight: 700, color: 'var(--text)' }}>
              Laravel Manager
            </h2>
            <p className="hint" style={{ margin: '2px 0 0', fontSize: '12.5px' }}>
              Quản lý tập trung toàn bộ vòng đời Dự án Laravel: Cài đặt Composer 1-Click, tự động trỏ{' '}
              <code>/public</code>, tích hợp Artisan Toolkit, Queue Worker &amp; Bảo mật.
            </p>
          </div>
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          <button
            type="button"
            className="secondary-light"
            disabled={isBusy('refresh')}
            onClick={() => loadInstances(true)}
            style={{ height: '36px', padding: '0 14px', fontSize: '13px' }}
          >
            <RefreshCw size={14} className={isBusy('refresh') ? 'spin' : ''} /> Làm mới
          </button>
        </div>
      </div>

      {/* 2. Global Top Tabs + Search Bar (matching WordPress Manager) */}
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'flex-end',
          flexWrap: 'wrap',
          gap: '12px',
          borderBottom: '1px solid var(--border)',
          marginBottom: '16px',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: '4px', flexWrap: 'wrap' }}>
          <button
            type="button"
            style={{
              height: '40px',
              minHeight: '40px',
              padding: '0 18px',
              marginBottom: '-1px',
              borderRadius: '8px 8px 0 0',
              border: '1px solid var(--border)',
              borderBottom: '2px solid var(--primary)',
              background: 'var(--surface)',
              color: 'var(--primary)',
              fontWeight: 600,
              fontSize: '13.5px',
              cursor: 'pointer',
              display: 'inline-flex',
              alignItems: 'center',
              gap: '7px',
              boxShadow: 'none',
            }}
          >
            <span>Instances</span>
            <span
              className="badge"
              style={{
                fontSize: '11px',
                padding: '1px 6px',
                background: 'color-mix(in srgb, var(--primary) 12%, transparent)',
                color: 'var(--primary)',
              }}
            >
              {instances.length}
            </span>
          </button>
        </div>

        <div style={{ position: 'relative', width: '280px', marginBottom: '6px' }}>
          <Search
            size={15}
            style={{
              position: 'absolute',
              left: '11px',
              top: '50%',
              transform: 'translateY(-50%)',
              color: 'var(--text-muted)',
            }}
          />
          <input
            type="text"
            value={searchQuery}
            onChange={e => setSearchQuery(e.target.value)}
            placeholder="Tìm kiếm tên miền, DB, phiên bản..."
            style={{
              height: '34px',
              paddingLeft: '34px',
              paddingRight: '10px',
              fontSize: '13px',
              width: '100%',
              borderRadius: '8px',
            }}
          />
        </div>
      </div>

      {/* 3. Global Action Toolbar (matching WordPress Manager) */}
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          flexWrap: 'wrap',
          gap: '10px',
          marginBottom: '16px',
          padding: '10px 14px',
          background: 'var(--surface-alt)',
          border: '1px solid var(--border)',
          borderRadius: '10px',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap' }}>
          <button
            type="button"
            disabled={!canInstallLaravel || isBusy('install')}
            onClick={openInstallModal}
            style={{
              height: '34px',
              minHeight: '34px',
              padding: '0 14px',
              fontSize: '13px',
              fontWeight: 600,
              background: 'var(--primary)',
              color: '#fff',
              border: 'none',
              borderRadius: '7px',
              opacity: !canInstallLaravel || isBusy('install') ? 0.5 : 1,
              cursor: !canInstallLaravel || isBusy('install') ? 'not-allowed' : 'pointer',
            }}
          >
            <Plus size={14} /> Install
          </button>

          <button
            type="button"
            className="secondary-light"
            disabled={isBusy('scan')}
            onClick={handleScanSites}
            style={{ height: '34px', minHeight: '34px', padding: '0 12px', fontSize: '13px' }}
            title="Quét tự động các dự án Laravel có sẵn trên tài khoản"
          >
            <Search size={14} /> Scan
          </button>

          <select
            value={statusFilter}
            onChange={e => setStatusFilter(e.target.value)}
            style={{ height: '34px', fontSize: '12.5px', borderRadius: '7px', width: 'auto' }}
          >
            <option value="all">Tất cả dự án ({instances.length})</option>
            <option value="healthy">Healthy (Hoạt động tốt)</option>
            <option value="queue_active">Queue Worker đang chạy</option>
            <option value="debug_on">APP_DEBUG: ON</option>
            <option value="maintenance">Đang Bảo trì (Down)</option>
            <option value="security_warn">Cần gia cố bảo mật</option>
          </select>
        </div>

        <div
          style={{
            display: 'inline-flex',
            alignItems: 'center',
            background: 'var(--surface)',
            border: '1px solid var(--border)',
            borderRadius: '7px',
            padding: '2px',
            gap: '2px',
          }}
        >
          <button
            type="button"
            onClick={() => setViewMode('cards')}
            style={{
              height: '28px',
              minHeight: '28px',
              padding: '0 10px',
              fontSize: '11.5px',
              borderRadius: '5px',
              background: viewMode === 'cards' ? 'var(--primary)' : 'transparent',
              color: viewMode === 'cards' ? '#fff' : 'var(--text-secondary)',
              border: 'none',
              fontWeight: viewMode === 'cards' ? 600 : 500,
              boxShadow: 'none',
            }}
          >
            Thẻ &amp; Toolkit
          </button>
          <button
            type="button"
            onClick={() => setViewMode('expanded')}
            style={{
              height: '28px',
              minHeight: '28px',
              padding: '0 10px',
              fontSize: '11.5px',
              borderRadius: '5px',
              background: viewMode === 'expanded' ? 'var(--primary)' : 'transparent',
              color: viewMode === 'expanded' ? '#fff' : 'var(--text-secondary)',
              border: 'none',
              fontWeight: viewMode === 'expanded' ? 600 : 500,
              boxShadow: 'none',
            }}
          >
            Chi tiết 5 Tab
          </button>
          <button
            type="button"
            onClick={() => setViewMode('list')}
            style={{
              height: '28px',
              minHeight: '28px',
              padding: '0 10px',
              fontSize: '11.5px',
              borderRadius: '5px',
              background: viewMode === 'list' ? 'var(--primary)' : 'transparent',
              color: viewMode === 'list' ? '#fff' : 'var(--text-secondary)',
              border: 'none',
              fontWeight: viewMode === 'list' ? 600 : 500,
              boxShadow: 'none',
            }}
          >
            Dạng Bảng
          </button>
        </div>
      </div>

      {/* Newly Installed Credentials Banner */}
      {installedCredentials && (
        <div
          style={{
            padding: '14px 18px',
            borderRadius: '12px',
            background: 'rgba(16, 185, 129, 0.08)',
            border: '1px solid rgba(16, 185, 129, 0.3)',
            marginBottom: '18px',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            flexWrap: 'wrap',
            gap: '12px',
          }}
        >
          <div style={{ fontSize: '12.5px' }}>
            <strong style={{ color: '#059669', display: 'block', marginBottom: '4px' }}>
              ✓ Dự án Laravel trên {installedCredentials.domain} đã sẵn sàng!
            </strong>
            <span>
              Database: <code>{installedCredentials.db_name}</code> · User:{' '}
              <code>{installedCredentials.db_user}</code> · Document Root:{' '}
              <code>/{installedCredentials.document_root}</code>
            </span>
          </div>
          <div style={{ display: 'flex', gap: '8px' }}>
            <button
              type="button"
              className="secondary-light mini"
              onClick={() =>
                copyText(
                  `DB_DATABASE=${installedCredentials.db_name}\nDB_USERNAME=${installedCredentials.db_user}\nDB_PASSWORD=${installedCredentials.db_password}`,
                  'Đã sao chép thông tin kết nối Database.'
                )
              }
            >
              <Copy size={13} />
              <span>Copy thông tin DB</span>
            </button>
            <button
              type="button"
              className="secondary-light mini"
              onClick={() => setInstalledCredentials(null)}
            >
              <X size={13} />
            </button>
          </div>
        </div>
      )}

      {/* Main Content Area */}
      {initialLoading ? (
        <div style={{ padding: '48px 20px', textAlign: 'center', color: 'var(--text-muted)' }}>
          Đang tải danh sách dự án Laravel...
        </div>
      ) : filteredInstances.length === 0 ? (
        <div
          style={{
            border: '1px solid var(--border)',
            borderRadius: '14px',
            background: 'var(--surface)',
            padding: '48px 24px',
            textAlign: 'center',
          }}
        >
          <div
            style={{
              width: '54px',
              height: '54px',
              borderRadius: '14px',
              background: 'color-mix(in srgb, var(--primary) 10%, transparent)',
              color: 'var(--primary)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              margin: '0 auto 14px',
            }}
          >
            <LaravelBrandIcon size={28} />
          </div>
          <h3 style={{ margin: '0 0 6px', fontSize: '16px', color: 'var(--text)' }}>
            Chưa có dự án Laravel nào được quản lý
          </h3>
          <p
            className="hint"
            style={{
              margin: '0 auto 18px',
              maxWidth: '500px',
              fontSize: '13px',
              lineHeight: 1.55,
            }}
          >
            Bấm <strong>Install</strong> để khởi tạo dự án Laravel 11.x / 10.x mới bằng Composer chỉ
            với 1 cú nhấp chuột (tự động tạo Database, sinh khóa <code>APP_KEY</code> và trỏ Document
            Root vào <code>/public</code>), hoặc bấm <strong>Scan</strong> để nhận diện các dự án có
            sẵn.
          </p>
          <div style={{ display: 'inline-flex', gap: '10px', flexWrap: 'wrap' }}>
            <button
              type="button"
              disabled={!canInstallLaravel || isBusy('install')}
              onClick={openInstallModal}
              style={{
                height: '38px',
                padding: '0 18px',
                background: 'var(--primary)',
                color: '#fff',
                border: 'none',
                borderRadius: '8px',
                fontWeight: 600,
                opacity: !canInstallLaravel || isBusy('install') ? 0.5 : 1,
                cursor: !canInstallLaravel || isBusy('install') ? 'not-allowed' : 'pointer',
              }}
            >
              <Plus size={15} /> Cài đặt Laravel ngay
            </button>
            <button
              type="button"
              className="secondary-light"
              onClick={handleScanSites}
              style={{ height: '38px', padding: '0 16px' }}
            >
              <Search size={15} /> Quét nhận diện (Scan)
            </button>
          </div>
        </div>
      ) : viewMode === 'cards' ? (
        /* Mockup #3 Layout: Left Minimalist Cards Grid + Right Artisan & Queue Drawer */
        <div
          className="lv-cards-drawer-layout"
          style={{
            display: 'grid',
            gridTemplateColumns: 'minmax(0, 1fr) 390px',
            gap: '22px',
            alignItems: 'start',
          }}
        >
          <div
            style={{
              display: 'grid',
              gridTemplateColumns: 'repeat(auto-fill, minmax(310px, 1fr))',
              gap: '14px',
            }}
          >
            {filteredInstances.map(inst => renderProjectMinimalCard(inst))}
          </div>

          {renderRightArtisanDrawer(selectedInstance)}
        </div>
      ) : viewMode === 'expanded' ? (
        /* WordPress Manager Style 2-Column Expanded Cards */
        <div style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
          {filteredInstances.map(inst => renderExpandedInstanceCard(inst))}
        </div>
      ) : (
        /* Compact Table List View */
        <div
          style={{
            borderRadius: '14px',
            border: '1px solid var(--border)',
            background: 'var(--surface)',
            overflowX: 'auto',
          }}
        >
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '13px' }}>
            <thead>
              <tr
                style={{
                  borderBottom: '1px solid var(--border)',
                  background: 'var(--surface-alt)',
                  textAlign: 'left',
                  fontSize: '11.5px',
                  textTransform: 'uppercase',
                  color: 'var(--text-muted)',
                }}
              >
                <th style={{ padding: '12px 16px' }}>Dự án / Tên miền</th>
                <th style={{ padding: '12px 14px' }}>Laravel / PHP</th>
                <th style={{ padding: '12px 14px' }}>Document Root</th>
                <th style={{ padding: '12px 14px' }}>Queue &amp; Cron</th>
                <th style={{ padding: '12px 14px' }}>Môi trường</th>
                <th style={{ padding: '12px 16px', textAlign: 'right' }}>Thao tác nhanh</th>
              </tr>
            </thead>
            <tbody>
              {filteredInstances.map(inst => (
                <tr key={inst.id} style={{ borderBottom: '1px solid var(--border)' }}>
                  <td style={{ padding: '12px 16px' }}>
                    <strong style={{ display: 'block', color: 'var(--text)' }}>
                      {inst.domain}
                      {inst.subpath || ''}
                    </strong>
                    <small style={{ color: 'var(--text-muted)' }}>
                      DB: {inst.db_name || 'N/A'} ({inst.db_size_mb || 0} MB)
                    </small>
                  </td>
                  <td style={{ padding: '12px 14px' }}>
                    <span className="badge">Laravel {inst.laravel_version}</span>{' '}
                    <span className="badge">PHP {inst.php_version}</span>
                  </td>
                  <td style={{ padding: '12px 14px', fontFamily: 'var(--font-mono, monospace)', fontSize: '12px' }}>
                    /{inst.document_root}
                  </td>
                  <td style={{ padding: '12px 14px', fontSize: '12px' }}>
                    Queue: <strong>{inst.queue_worker?.enabled ? `${inst.queue_worker.workers}W` : 'Off'}</strong> · Cron:{' '}
                    <strong>{inst.scheduler_enabled ? 'ON' : 'OFF'}</strong>
                  </td>
                  <td style={{ padding: '12px 14px' }}>
                    <span
                      className="badge"
                      style={{
                        background: inst.app_debug
                          ? 'rgba(239, 68, 68, 0.14)'
                          : 'rgba(16, 185, 129, 0.14)',
                        color: inst.app_debug ? '#dc2626' : '#059669',
                      }}
                    >
                      {inst.app_env} · DEBUG={inst.app_debug ? 'true' : 'false'}
                    </span>
                  </td>
                  <td style={{ padding: '12px 16px', textAlign: 'right' }}>
                    <div style={{ display: 'inline-flex', gap: '6px' }}>
                      <button
                        type="button"
                        className="secondary-light mini"
                        onClick={() => handleRunArtisan(inst.id, 'migrate')}
                      >
                        migrate
                      </button>
                      <button
                        type="button"
                        className="secondary-light mini"
                        onClick={() => handleRunArtisan(inst.id, 'optimize:clear')}
                      >
                        optimize:clear
                      </button>
                      <button
                        type="button"
                        className="secondary-light mini"
                        onClick={() => openEnvEditor(inst, 'visual')}
                      >
                        .env
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* ── MODAL 1: 1-Click Laravel Installer ────────────────────────────── */}
      {installModalOpen && (
        <div
          className="modal-backdrop"
          onClick={() => setInstallModalOpen(false)}
          style={{
            position: 'fixed',
            inset: 0,
            zIndex: 1250,
            background: 'rgba(15, 23, 42, 0.6)',
            backdropFilter: 'blur(4px)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            padding: '16px',
          }}
        >
          <div
            className="modal"
            onClick={e => e.stopPropagation()}
            style={{
              width: '100%',
              maxWidth: '640px',
              maxHeight: '90vh',
              overflowY: 'auto',
              borderRadius: '16px',
              background: 'var(--bg-elevated, #ffffff)',
              border: '1px solid var(--border-strong, var(--border))',
              padding: '22px 24px',
            }}
          >
            <div
              style={{
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'space-between',
                paddingBottom: '14px',
                marginBottom: '16px',
                borderBottom: '1px solid var(--border)',
              }}
            >
              <div>
                <h3 style={{ margin: 0, fontSize: '17px', fontWeight: 800 }}>
                  Cài đặt Dự án Laravel 1-Click
                </h3>
                <small style={{ color: 'var(--text-muted)' }}>
                  Tự động tạo Database, sinh khóa APP_KEY, chạy migrate và trỏ Nginx vào /public
                </small>
              </div>
              <button
                type="button"
                className="secondary-light mini"
                onClick={() => setInstallModalOpen(false)}
              >
                <X size={15} />
              </button>
            </div>

            <form
              onSubmit={handleInstallLaravel}
              style={{ display: 'flex', flexDirection: 'column', gap: '14px' }}
            >
              <div
                style={{
                  display: 'grid',
                  gridTemplateColumns: '2fr 1fr',
                  gap: '12px',
                }}
              >
                <label>
                  <span>Tên miền cài đặt (Website)</span>
                  <select
                    value={installForm.website_id}
                    onChange={e =>
                      setInstallForm(prev => ({ ...prev, website_id: e.target.value }))
                    }
                    required
                  >
                    <option value="">-- Chọn tên miền --</option>
                    {installableWebsites.map(w => (
                      <option key={w.id} value={w.id}>
                        {w.domain} (PHP {w.php_version || '8.3'})
                      </option>
                    ))}
                  </select>
                </label>
                <label>
                  <span>Thư mục con (Tuỳ chọn)</span>
                  <input
                    type="text"
                    value={installForm.subpath}
                    onChange={e =>
                      setInstallForm(prev => ({ ...prev, subpath: e.target.value }))
                    }
                    placeholder="Để trống = gốc"
                  />
                </label>
              </div>

              <div
                style={{
                  display: 'grid',
                  gridTemplateColumns: '2fr 1fr 1fr',
                  gap: '12px',
                }}
              >
                <label>
                  <span>Tên ứng dụng (APP_NAME)</span>
                  <input
                    type="text"
                    value={installForm.app_name}
                    onChange={e =>
                      setInstallForm(prev => ({ ...prev, app_name: e.target.value }))
                    }
                  />
                </label>
                <label>
                  <span>Phiên bản Laravel</span>
                  <select
                    value={installForm.laravel_version}
                    onChange={e =>
                      setInstallForm(prev => ({ ...prev, laravel_version: e.target.value }))
                    }
                  >
                    {LARAVEL_VERSIONS.map(v => (
                      <option key={v} value={v}>
                        Laravel {v}
                      </option>
                    ))}
                  </select>
                </label>
                <label>
                  <span>Phiên bản PHP-FPM</span>
                  <select
                    value={installForm.php_version}
                    onChange={e =>
                      setInstallForm(prev => ({ ...prev, php_version: e.target.value }))
                    }
                  >
                    {PHP_VERSIONS.map(v => (
                      <option key={v} value={v}>
                        PHP {v}
                      </option>
                    ))}
                  </select>
                </label>
              </div>

              {/* Source Picker: Composer vs Git */}
              <div
                style={{
                  display: 'grid',
                  gridTemplateColumns: '1fr 1fr',
                  gap: '10px',
                }}
              >
                <button
                  type="button"
                  className={
                    installForm.install_source === 'composer' ? '' : 'secondary-light'
                  }
                  onClick={() =>
                    setInstallForm(prev => ({ ...prev, install_source: 'composer' }))
                  }
                  style={{ height: '36px', fontSize: '12.5px' }}
                >
                  <Boxes size={14} />
                  <span>Cài mới bằng Composer (laravel/laravel)</span>
                </button>
                <button
                  type="button"
                  className={installForm.install_source === 'git' ? '' : 'secondary-light'}
                  onClick={() =>
                    setInstallForm(prev => ({ ...prev, install_source: 'git' }))
                  }
                  style={{ height: '36px', fontSize: '12.5px' }}
                >
                  <GitBranch size={14} />
                  <span>Clone dự án từ Git Repository</span>
                </button>
              </div>

              {installForm.install_source === 'git' && (
                <div style={{ display: 'grid', gridTemplateColumns: '2fr 1fr', gap: '12px' }}>
                  <label>
                    <span>Địa chỉ Git Repository</span>
                    <input
                      type="text"
                      value={installForm.git_repo}
                      onChange={e =>
                        setInstallForm(prev => ({ ...prev, git_repo: e.target.value }))
                      }
                      placeholder="https://github.com/username/laravel-app.git"
                      required
                    />
                  </label>
                  <label>
                    <span>Nhánh (Branch)</span>
                    <input
                      type="text"
                      value={installForm.git_branch}
                      onChange={e =>
                        setInstallForm(prev => ({ ...prev, git_branch: e.target.value }))
                      }
                      placeholder="main"
                    />
                  </label>
                </div>
              )}

              {/* Drivers */}
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: '12px' }}>
                <label>
                  <span>Cache Driver (CACHE_STORE)</span>
                  <select
                    value={installForm.cache_driver}
                    onChange={e =>
                      setInstallForm(prev => ({ ...prev, cache_driver: e.target.value }))
                    }
                  >
                    {CACHE_DRIVERS.map(d => (
                      <option key={d} value={d}>
                        {d}
                      </option>
                    ))}
                  </select>
                </label>
                <label>
                  <span>Queue Connection</span>
                  <select
                    value={installForm.queue_connection}
                    onChange={e =>
                      setInstallForm(prev => ({ ...prev, queue_connection: e.target.value }))
                    }
                  >
                    {QUEUE_CONNECTIONS.map(d => (
                      <option key={d} value={d}>
                        {d}
                      </option>
                    ))}
                  </select>
                </label>
                <label>
                  <span>Session Driver</span>
                  <select
                    value={installForm.session_driver}
                    onChange={e =>
                      setInstallForm(prev => ({ ...prev, session_driver: e.target.value }))
                    }
                  >
                    {SESSION_DRIVERS.map(d => (
                      <option key={d} value={d}>
                        {d}
                      </option>
                    ))}
                  </select>
                </label>
              </div>

              {/* Checkboxes */}
              <div
                style={{
                  padding: '12px 14px',
                  borderRadius: '10px',
                  background: 'var(--surface-alt)',
                  border: '1px solid var(--border)',
                  display: 'flex',
                  flexDirection: 'column',
                  gap: '8px',
                }}
              >
                <label className="check-line" style={{ fontSize: '12.5px' }}>
                  <input
                    type="checkbox"
                    checked={installForm.enable_queue_worker}
                    onChange={e =>
                      setInstallForm(prev => ({
                        ...prev,
                        enable_queue_worker: e.target.checked,
                      }))
                    }
                  />
                  <span>
                    Tự động bật <strong>Laravel Queue Worker</strong> (2 tiến trình chạy nền)
                  </span>
                </label>
                <label className="check-line" style={{ fontSize: '12.5px' }}>
                  <input
                    type="checkbox"
                    checked={installForm.enable_scheduler}
                    onChange={e =>
                      setInstallForm(prev => ({
                        ...prev,
                        enable_scheduler: e.target.checked,
                      }))
                    }
                  />
                  <span>
                    Tự động bật <strong>Laravel Task Scheduler Cron</strong> (
                    <code>* * * * * artisan schedule:run</code>)
                  </span>
                </label>
              </div>

              <div
                style={{
                  display: 'flex',
                  justifyContent: 'flex-end',
                  gap: '10px',
                  marginTop: '6px',
                }}
              >
                <button
                  type="button"
                  className="secondary-light"
                  onClick={() => setInstallModalOpen(false)}
                >
                  Hủy
                </button>
                <button type="submit" disabled={isBusy('install')}>
                  <Zap size={14} />
                  <span>{isBusy('install') ? 'Đang cài đặt...' : 'Cài đặt Laravel ngay'}</span>
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* ── MODAL 2: Full .env Editor (Visual + Raw) ──────────────────────── */}
      {envModalSite && (
        <div
          className="modal-backdrop"
          onClick={() => setEnvModalSite(null)}
          style={{
            position: 'fixed',
            inset: 0,
            zIndex: 1250,
            background: 'rgba(15, 23, 42, 0.6)',
            backdropFilter: 'blur(4px)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            padding: '16px',
          }}
        >
          <div
            className="modal"
            onClick={e => e.stopPropagation()}
            style={{
              width: '100%',
              maxWidth: '720px',
              maxHeight: '90vh',
              overflowY: 'auto',
              borderRadius: '16px',
              background: 'var(--bg-elevated, #ffffff)',
              border: '1px solid var(--border-strong, var(--border))',
              padding: '20px 24px',
            }}
          >
            <div
              style={{
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'space-between',
                paddingBottom: '12px',
                marginBottom: '14px',
                borderBottom: '1px solid var(--border)',
              }}
            >
              <div>
                <h3 style={{ margin: 0, fontSize: '16.5px', fontWeight: 800 }}>
                  Trình quản lý Biến môi trường `.env` — {envModalSite.domain}
                </h3>
                <small style={{ color: 'var(--text-muted)' }}>
                  Tự động sao lưu <code>.env.bak</code> và chạy <code>artisan config:clear</code>{' '}
                  sau khi lưu
                </small>
              </div>
              <div style={{ display: 'flex', gap: '6px' }}>
                <button
                  type="button"
                  className={envModalTab === 'visual' ? 'mini' : 'secondary-light mini'}
                  onClick={() => setEnvModalTab('visual')}
                >
                  Chế độ Form Trực quan
                </button>
                <button
                  type="button"
                  className={envModalTab === 'raw' ? 'mini' : 'secondary-light mini'}
                  onClick={() => setEnvModalTab('raw')}
                >
                  Sửa Raw `.env`
                </button>
                <button
                  type="button"
                  className="secondary-light mini"
                  onClick={() => setEnvModalSite(null)}
                >
                  <X size={14} />
                </button>
              </div>
            </div>

            {envLoading ? (
              <div style={{ padding: '32px', textAlign: 'center', color: 'var(--text-muted)' }}>
                Đang đọc tệp .env...
              </div>
            ) : envModalTab === 'visual' ? (
              <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
                <div style={{ display: 'grid', gridTemplateColumns: '2fr 1fr 1fr', gap: '10px' }}>
                  <label>
                    <span>APP_NAME</span>
                    <input
                      type="text"
                      value={envDraft.parsed?.app_name || ''}
                      onChange={e =>
                        setEnvDraft(prev => ({
                          ...prev,
                          parsed: { ...prev.parsed, app_name: e.target.value },
                        }))
                      }
                    />
                  </label>
                  <label>
                    <span>APP_ENV</span>
                    <select
                      value={envDraft.parsed?.app_env || 'production'}
                      onChange={e =>
                        setEnvDraft(prev => ({
                          ...prev,
                          parsed: { ...prev.parsed, app_env: e.target.value },
                        }))
                      }
                    >
                      <option value="production">production</option>
                      <option value="staging">staging</option>
                      <option value="local">local</option>
                    </select>
                  </label>
                  <label>
                    <span>APP_DEBUG</span>
                    <select
                      value={envDraft.parsed?.app_debug ? 'true' : 'false'}
                      onChange={e =>
                        setEnvDraft(prev => ({
                          ...prev,
                          parsed: { ...prev.parsed, app_debug: e.target.value === 'true' },
                        }))
                      }
                    >
                      <option value="false">false (Khuyên dùng)</option>
                      <option value="true">true (Debug)</option>
                    </select>
                  </label>
                </div>

                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: '10px' }}>
                  <label>
                    <span>CACHE_STORE / DRIVER</span>
                    <select
                      value={envDraft.parsed?.cache_driver || 'file'}
                      onChange={e =>
                        setEnvDraft(prev => ({
                          ...prev,
                          parsed: { ...prev.parsed, cache_driver: e.target.value },
                        }))
                      }
                    >
                      {CACHE_DRIVERS.map(d => (
                        <option key={d} value={d}>
                          {d}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label>
                    <span>QUEUE_CONNECTION</span>
                    <select
                      value={envDraft.parsed?.queue_connection || 'database'}
                      onChange={e =>
                        setEnvDraft(prev => ({
                          ...prev,
                          parsed: { ...prev.parsed, queue_connection: e.target.value },
                        }))
                      }
                    >
                      {QUEUE_CONNECTIONS.map(d => (
                        <option key={d} value={d}>
                          {d}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label>
                    <span>SESSION_DRIVER</span>
                    <select
                      value={envDraft.parsed?.session_driver || 'file'}
                      onChange={e =>
                        setEnvDraft(prev => ({
                          ...prev,
                          parsed: { ...prev.parsed, session_driver: e.target.value },
                        }))
                      }
                    >
                      {SESSION_DRIVERS.map(d => (
                        <option key={d} value={d}>
                          {d}
                        </option>
                      ))}
                    </select>
                  </label>
                </div>

                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: '10px' }}>
                  <label>
                    <span>DB_DATABASE</span>
                    <input
                      type="text"
                      value={envDraft.parsed?.db_name || ''}
                      onChange={e =>
                        setEnvDraft(prev => ({
                          ...prev,
                          parsed: { ...prev.parsed, db_name: e.target.value },
                        }))
                      }
                    />
                  </label>
                  <label>
                    <span>DB_USERNAME</span>
                    <input
                      type="text"
                      value={envDraft.parsed?.db_user || ''}
                      onChange={e =>
                        setEnvDraft(prev => ({
                          ...prev,
                          parsed: { ...prev.parsed, db_user: e.target.value },
                        }))
                      }
                    />
                  </label>
                  <label>
                    <span>DB_PASSWORD</span>
                    <input
                      type="password"
                      value={envDraft.parsed?.db_password || ''}
                      onChange={e =>
                        setEnvDraft(prev => ({
                          ...prev,
                          parsed: { ...prev.parsed, db_password: e.target.value },
                        }))
                      }
                    />
                  </label>
                </div>

                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: '10px' }}>
                  <label>
                    <span>REDIS_HOST</span>
                    <input
                      type="text"
                      value={envDraft.parsed?.redis_host || '127.0.0.1'}
                      onChange={e =>
                        setEnvDraft(prev => ({
                          ...prev,
                          parsed: { ...prev.parsed, redis_host: e.target.value },
                        }))
                      }
                    />
                  </label>
                  <label>
                    <span>MAIL_HOST</span>
                    <input
                      type="text"
                      value={envDraft.parsed?.mail_host || ''}
                      onChange={e =>
                        setEnvDraft(prev => ({
                          ...prev,
                          parsed: { ...prev.parsed, mail_host: e.target.value },
                        }))
                      }
                    />
                  </label>
                  <label>
                    <span>MAIL_FROM_ADDRESS</span>
                    <input
                      type="text"
                      value={envDraft.parsed?.mail_from_address || ''}
                      onChange={e =>
                        setEnvDraft(prev => ({
                          ...prev,
                          parsed: { ...prev.parsed, mail_from_address: e.target.value },
                        }))
                      }
                    />
                  </label>
                </div>

                <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: '8px' }}>
                  <button
                    type="button"
                    className="secondary-light"
                    onClick={() => handleRunArtisan(envModalSite.id, 'key:generate')}
                  >
                    <KeyRound size={14} />
                    <span>Sinh lại APP_KEY mới (key:generate)</span>
                  </button>
                  <div style={{ display: 'flex', gap: '8px' }}>
                    <button
                      type="button"
                      className="secondary-light"
                      onClick={() => setEnvModalSite(null)}
                    >
                      Đóng
                    </button>
                    <button
                      type="button"
                      disabled={isBusy(`env-save-${envModalSite.id}`)}
                      onClick={() => handleSaveEnv(envModalSite.id, 'visual')}
                    >
                      <Save size={14} />
                      <span>Lưu cấu hình .env</span>
                    </button>
                  </div>
                </div>
              </div>
            ) : (
              <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
                <textarea
                  rows={16}
                  value={envDraft.raw_env}
                  onChange={e => setEnvDraft(prev => ({ ...prev, raw_env: e.target.value }))}
                  style={{
                    width: '100%',
                    fontFamily: 'var(--font-mono, monospace)',
                    fontSize: '12.5px',
                    lineHeight: 1.55,
                    padding: '12px',
                    borderRadius: '10px',
                  }}
                />
                <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '8px' }}>
                  <button
                    type="button"
                    className="secondary-light"
                    onClick={() => setEnvModalSite(null)}
                  >
                    Đóng
                  </button>
                  <button
                    type="button"
                    disabled={isBusy(`env-save-${envModalSite.id}`)}
                    onClick={() => handleSaveEnv(envModalSite.id, 'raw')}
                  >
                    <Save size={14} />
                    <span>Lưu tệp Raw .env</span>
                  </button>
                </div>
              </div>
            )}
          </div>
        </div>
      )}

      {/* ── MODAL 3: Laravel Log Viewer (`storage/logs/laravel.log`) ───────── */}
      {logModalSite && (
        <div
          className="modal-backdrop"
          onClick={() => setLogModalSite(null)}
          style={{
            position: 'fixed',
            inset: 0,
            zIndex: 1250,
            background: 'rgba(15, 23, 42, 0.6)',
            backdropFilter: 'blur(4px)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            padding: '16px',
          }}
        >
          <div
            className="modal"
            onClick={e => e.stopPropagation()}
            style={{
              width: '100%',
              maxWidth: '760px',
              borderRadius: '16px',
              background: 'var(--bg-elevated, #ffffff)',
              border: '1px solid var(--border-strong, var(--border))',
              padding: '20px 24px',
            }}
          >
            <div
              style={{
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'space-between',
                paddingBottom: '12px',
                marginBottom: '12px',
                borderBottom: '1px solid var(--border)',
              }}
            >
              <div>
                <h3 style={{ margin: 0, fontSize: '16px', fontWeight: 800 }}>
                  Nhật ký Ứng dụng Laravel — {logModalSite.domain}
                </h3>
                <code style={{ fontSize: '11.5px', color: 'var(--text-muted)' }}>{logPath}</code>
              </div>
              <div style={{ display: 'flex', gap: '8px' }}>
                <button
                  type="button"
                  className="secondary-light mini"
                  onClick={() => openLogsModal(logModalSite)}
                >
                  <RefreshCw size={13} />
                  <span>Làm mới</span>
                </button>
                <button
                  type="button"
                  className="danger mini"
                  disabled={isBusy(`clear-log-${logModalSite.id}`)}
                  onClick={() => handleClearLogs(logModalSite.id)}
                >
                  <Trash2 size={13} />
                  <span>Dọn sạch Log</span>
                </button>
                <button
                  type="button"
                  className="secondary-light mini"
                  onClick={() => setLogModalSite(null)}
                >
                  <X size={14} />
                </button>
              </div>
            </div>

            <pre
              style={{
                margin: 0,
                padding: '14px',
                borderRadius: '10px',
                background: '#0f172a',
                color: '#e2e8f0',
                fontFamily: 'var(--font-mono, monospace)',
                fontSize: '12px',
                lineHeight: 1.55,
                maxHeight: '420px',
                overflowY: 'auto',
                whiteSpace: 'pre-wrap',
                wordBreak: 'break-word',
              }}
            >
              {logLoading
                ? 'Đang tải nhật ký...'
                : logContent || 'Tệp storage/logs/laravel.log hiện đang trống (Không có lỗi).'}
            </pre>
          </div>
        </div>
      )}

      {/* ── MODAL 4: Remove Confirmation ───────────────────────────────────── */}
      {removeModalSite && (
        <div
          className="modal-backdrop"
          onClick={() => setRemoveModalSite(null)}
          style={{
            position: 'fixed',
            inset: 0,
            zIndex: 1250,
            background: 'rgba(15, 23, 42, 0.6)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            padding: '16px',
          }}
        >
          <div
            className="modal"
            onClick={e => e.stopPropagation()}
            style={{
              width: '100%',
              maxWidth: '460px',
              borderRadius: '16px',
              background: 'var(--bg-elevated, #ffffff)',
              border: '1px solid var(--border-strong, var(--border))',
              padding: '20px 24px',
            }}
          >
            <h3 style={{ margin: '0 0 8px', fontSize: '16px', fontWeight: 800 }}>
              Gỡ cài đặt dự án Laravel — {removeModalSite.domain}
            </h3>
            <p style={{ margin: '0 0 12px', fontSize: '13px', color: 'var(--text-muted)' }}>
              Thao tác này sẽ gỡ cấu hình Laravel và dừng các tiến trình Scheduler/Queue của dự án.
            </p>
            <label className="check-line" style={{ marginBottom: '16px', fontSize: '12.5px' }}>
              <input
                type="checkbox"
                checked={removeDropDb}
                onChange={e => setRemoveDropDb(e.target.checked)}
              />
              <span>
                Xóa luôn cơ sở dữ liệu MySQL đính kèm (<code>{removeModalSite.db_name}</code>)
              </span>
            </label>
            <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '8px' }}>
              <button
                type="button"
                className="secondary-light"
                onClick={() => setRemoveModalSite(null)}
              >
                Hủy
              </button>
              <button
                type="button"
                className="danger"
                disabled={isBusy(`remove-${removeModalSite.id}`)}
                onClick={handleRemoveSite}
              >
                <Trash2 size={14} />
                <span>Xác nhận Gỡ cài đặt</span>
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export default LaravelManagerPage;
