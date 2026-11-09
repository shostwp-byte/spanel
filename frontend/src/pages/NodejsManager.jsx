import React, { useState, useEffect, useMemo, useCallback, useRef } from 'react';
import {
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
  KeyRound,
  Code2,
  GitBranch,
  Cpu,
  Activity,
  ChevronDown,
  ChevronUp,
  List,
  LayoutGrid,
  Save,
  Boxes,
  Download,
} from 'lucide-react';
import { useAppContext } from '../contexts/AppContext.jsx';
import { PasswordField, CopyPasswordButton, generateStrongPassword } from '../components/PasswordField.jsx';
import { t } from '../i18n.js';

function NodejsBrandIcon({ size = 18 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" aria-hidden="true" focusable="false" className="lucide">
      <polygon
        points="12,2.4 20.3,7.2 20.3,16.8 12,21.6 3.7,16.8 3.7,7.2"
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinejoin="round"
      />
      <text x="12" y="15.8" textAnchor="middle" fontSize="10.5" fontWeight="800" fontFamily="system-ui, sans-serif" fill="currentColor">
        JS
      </text>
    </svg>
  );
}

const NODE_SUPPORTED_MAJORS = ['22', '20', '18', '24'];
const START_MODES = [
  { value: 'node', label: 'node (Chạy tệp JS trực tiếp)' },
  { value: 'npm', label: 'npm (Chạy script trong package.json)' },
  { value: 'npx', label: 'npx (Thực thi binary gói)' },
  { value: 'yarn', label: 'yarn (Chạy script bằng Yarn)' },
];
const MEMORY_PRESETS = [128, 256, 512, 1024, 2048, 4096];

const NPM_QUICK_ACTIONS = [
  {
    action: 'install',
    title: 'npm install',
    subtitle: 'Cài đặt phụ thuộc',
    desc: 'Đồng bộ và cài đặt các thư viện từ package.json vào node_modules.',
  },
  {
    action: 'build',
    title: 'npm run build',
    subtitle: 'Biên dịch Production',
    desc: 'Chạy kịch bản đóng gói/biên dịch mã nguồn (Next.js, NestJS, Vite, TypeScript).',
  },
  {
    action: 'audit_fix',
    title: 'npm audit fix',
    subtitle: 'Vá lỗ hổng gói',
    desc: 'Quét và tự động nâng cấp các thư viện phụ thuộc có cảnh báo bảo mật.',
  },
  {
    action: 'cache_clean',
    title: 'npm cache clean',
    subtitle: 'Dọn bộ đệm NPM',
    desc: 'Giải phóng bộ nhớ đệm tarballs cũ của trình quản lý gói NPM.',
  },
];

let _nodejsToolkitSessionCache = {
  loaded: false,
  instances: [],
  availableWebsites: [],
  nodeVersions: NODE_SUPPORTED_MAJORS,
  installedNodeVersions: ['22', '20'],
  defaultNodeVersion: '22',
  starterTemplates: [],
  curatedPackages: [],
};

export function clearNodejsToolkitSessionCache() {
  _nodejsToolkitSessionCache = {
    loaded: false,
    instances: [],
    availableWebsites: [],
    nodeVersions: NODE_SUPPORTED_MAJORS,
    installedNodeVersions: ['22', '20'],
    defaultNodeVersion: '22',
    starterTemplates: [],
    curatedPackages: [],
  };
}

function formatMemoryBytes(bytes) {
  const num = Number(bytes || 0);
  if (!num || num <= 0) return '0 MB';
  const mb = num / (1024 * 1024);
  if (mb < 1) return `${Math.max(1, Math.round(num / 1024))} KB`;
  return `${mb.toFixed(mb >= 100 ? 0 : 1)} MB`;
}

export function NodejsManagerPage({
  request: propRequest,
  websites: propWebsites,
  openWebsiteFileManager: propOpenFileManager,
  navigateToPage: propNavigate,
}) {
  const ctx = useAppContext() || {};
  const request = propRequest || ctx.request;
  const websites = propWebsites || ctx.websites || [];
  const requestRef = useRef(request);
  requestRef.current = request;
  const hasLoadedOnceRef = useRef(_nodejsToolkitSessionCache.loaded);
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

  // Core data state (hydrated immediately from session cache for 0ms navigation)
  const [instances, setInstances] = useState(() => _nodejsToolkitSessionCache.instances);
  const [availableWebsites, setAvailableWebsites] = useState(
    () => _nodejsToolkitSessionCache.availableWebsites
  );
  const [nodeVersions, setNodeVersions] = useState(
    () => _nodejsToolkitSessionCache.nodeVersions
  );
  const [installedNodeVersions, setInstalledNodeVersions] = useState(
    () => _nodejsToolkitSessionCache.installedNodeVersions
  );
  const [starterTemplates, setStarterTemplates] = useState(
    () => _nodejsToolkitSessionCache.starterTemplates
  );
  const [curatedPackages, setCuratedPackages] = useState(
    () => _nodejsToolkitSessionCache.curatedPackages
  );
  const [initialLoading, setInitialLoading] = useState(() => !_nodejsToolkitSessionCache.loaded);
  const [busyMap, setBusyMap] = useState({});

  // View & Filter state
  const [viewMode, setViewMode] = useState('expanded');
  const [searchQuery, setSearchQuery] = useState('');
  const [statusFilter, setStatusFilter] = useState('all');
  const [selectedId, setSelectedId] = useState(null);
  const [expandedIds, setExpandedIds] = useState({});
  const [activeSubTabs, setActiveSubTabs] = useState({});

  // Runtime process form drafts keyed by instance id
  const [processDrafts, setProcessDrafts] = useState({});
  // Custom npm package inputs keyed by instance id
  const [customPkgInputs, setCustomPkgInputs] = useState({});
  // Git deploy form drafts keyed by instance id
  const [gitDrafts, setGitDrafts] = useState({});

  // Modals state
  const [showInstallModal, setShowInstallModal] = useState(false);
  const [installSuccessCreds, setInstallSuccessCreds] = useState(null);
  const [envModalInstance, setEnvModalInstance] = useState(null);
  const [envModalData, setEnvModalData] = useState({
    mode: 'structured',
    raw: '',
    parsed: {},
    loading: false,
    restartAfterSave: true,
  });
  const [logsModalInstance, setLogsModalInstance] = useState(null);
  const [logsModalData, setLogsModalData] = useState({
    kind: 'systemd',
    lines: 120,
    content: '',
    loading: false,
  });
  const [showRuntimeModal, setShowRuntimeModal] = useState(false);
  const [removeModalInstance, setRemoveModalInstance] = useState(null);
  const [removeOptions, setRemoveOptions] = useState({
    mode: 'detach',
    deleteFiles: false,
    dropDatabase: false,
    confirmDomain: '',
  });

  // Install form state
  const [installForm, setInstallForm] = useState({
    website_id: '',
    subpath: '',
    app_name: 'Node.js Application',
    install_source: 'template',
    template: 'express-api',
    node_major: '22',
    start_mode: 'node',
    start_arg: 'server.js',
    port: '',
    memory_mb: 512,
    node_env: 'production',
    create_database: false,
    db_name: '',
    db_user: '',
    db_password: '',
    git_repo: '',
    git_branch: 'main',
    run_npm_install: true,
    harden_security: true,
  });

  const markBusy = useCallback((key, val) => {
    setBusyMap(prev => ({ ...prev, [key]: val }));
  }, []);

  const syncInstanceDrafts = useCallback(instList => {
    setProcessDrafts(prev => {
      const next = { ...prev };
      for (const inst of instList) {
        if (!next[inst.id]) {
          next[inst.id] = {
            node_major: String(inst.node_major || '22'),
            start_mode: inst.start_mode || 'node',
            start_arg: inst.start_arg || 'server.js',
            port: String(inst.port || 21000),
            memory_mb: Number(inst.memory_mb || 512),
            node_env: inst.node_env || 'production',
          };
        }
      }
      return next;
    });
    setGitDrafts(prev => {
      const next = { ...prev };
      for (const inst of instList) {
        if (!next[inst.id]) {
          next[inst.id] = {
            git_repo: inst.git_repo || '',
            git_branch: inst.git_branch || 'main',
            build_script: inst.git_build_script || '',
            run_npm_install: true,
            restart_process: true,
          };
        }
      }
      return next;
    });
  }, []);

  const loadInstances = useCallback(
    async ({ silent = false, refresh = false } = {}) => {
      const req = requestRef.current;
      if (!req) return;
      if (!silent) setInitialLoading(true);
      try {
        const qs = refresh ? '?refresh=true' : '';
        const data = await req(`/nodejs-manager/instances${qs}`);
        const list = Array.isArray(data?.instances) ? data.instances : [];
        const avail = Array.isArray(data?.available_websites) ? data.available_websites : [];
        const nVers = Array.isArray(data?.node_versions) ? data.node_versions : NODE_SUPPORTED_MAJORS;
        const instVers = Array.isArray(data?.installed_node_versions)
          ? data.installed_node_versions
          : ['22'];
        const templates = Array.isArray(data?.starter_templates) ? data.starter_templates : [];
        const pkgs = Array.isArray(data?.curated_packages) ? data.curated_packages : [];

        setInstances(list);
        setAvailableWebsites(avail);
        setNodeVersions(nVers);
        setInstalledNodeVersions(instVers);
        if (templates.length) setStarterTemplates(templates);
        if (pkgs.length) setCuratedPackages(pkgs);
        syncInstanceDrafts(list);

        _nodejsToolkitSessionCache = {
          loaded: true,
          instances: list,
          availableWebsites: avail,
          nodeVersions: nVers,
          installedNodeVersions: instVers,
          defaultNodeVersion: data?.default_node_version || instVers[0] || '22',
          starterTemplates: templates,
          curatedPackages: pkgs,
        };

        setExpandedIds(prev => {
          if (Object.keys(prev).length > 0) return prev;
          const init = {};
          list.forEach((it, idx) => {
            init[it.id] = idx === 0;
          });
          return init;
        });
        setSelectedId(prev => {
          if (prev && list.some(i => i.id === prev)) return prev;
          return list[0]?.id || null;
        });
      } catch (err) {
        if (!silent) setError(err?.message || 'Không thể tải danh sách ứng dụng Node.js.');
      } finally {
        if (!silent) setInitialLoading(false);
      }
    },
    [setError, syncInstanceDrafts]
  );

  useEffect(() => {
    if (hasLoadedOnceRef.current) {
      syncInstanceDrafts(_nodejsToolkitSessionCache.instances);
      return;
    }
    hasLoadedOnceRef.current = true;
    loadInstances();
  }, [loadInstances, syncInstanceDrafts]);

  const replaceSingleInstance = useCallback(
    updated => {
      if (!updated || !updated.id) return;
      setInstances(prev => {
        const next = prev.some(i => i.id === updated.id)
          ? prev.map(i => (i.id === updated.id ? updated : i))
          : [updated, ...prev];
        _nodejsToolkitSessionCache.instances = next;
        return next;
      });
      setProcessDrafts(prev => ({
        ...prev,
        [updated.id]: {
          node_major: String(updated.node_major || '22'),
          start_mode: updated.start_mode || 'node',
          start_arg: updated.start_arg || 'server.js',
          port: String(updated.port || 21000),
          memory_mb: Number(updated.memory_mb || 512),
          node_env: updated.node_env || 'production',
        },
      }));
    },
    []
  );

  // Filtered instances
  const filteredInstances = useMemo(() => {
    const q = searchQuery.trim().toLowerCase();
    return instances.filter(inst => {
      if (statusFilter === 'running' && inst.status !== 'running') return false;
      if (statusFilter === 'stopped' && inst.status === 'running') return false;
      if (statusFilter === 'security' && (inst.security_score || 0) >= 100) return false;
      if (!q) return true;
      return (
        String(inst.domain || '').toLowerCase().includes(q) ||
        String(inst.app_name || '').toLowerCase().includes(q) ||
        String(inst.framework || '').toLowerCase().includes(q) ||
        String(inst.port || '').includes(q)
      );
    });
  }, [instances, searchQuery, statusFilter]);

  const websiteCandidates = useMemo(() => {
    if (availableWebsites.length > 0) return availableWebsites;
    return (websites || []).map(w => ({
      id: w.id,
      domain: w.domain,
      document_root: w.document_root || 'public_html',
      ssl_enabled: Boolean(w.ssl_enabled),
      has_nodejs: instances.some(i => i.website_id === w.id),
    }));
  }, [availableWebsites, websites, instances]);

  const openInstallModal = useCallback(() => {
    const firstSite =
      websiteCandidates.find(w => !w.has_nodejs) || websiteCandidates[0] || null;
    const slug = firstSite
      ? String(firstSite.domain || 'node')
          .toLowerCase()
          .replace(/[^a-z0-9]/g, '')
          .slice(0, 10)
      : 'node';
    const suffix = Math.random().toString(16).slice(2, 6);
    setInstallForm({
      website_id: firstSite ? String(firstSite.id) : '',
      subpath: '',
      app_name: firstSite ? `${firstSite.domain}` : 'Node.js Application',
      install_source: 'template',
      template: 'express-api',
      node_major: installedNodeVersions[0] || '22',
      start_mode: 'node',
      start_arg: 'server.js',
      port: '',
      memory_mb: 512,
      node_env: 'production',
      create_database: false,
      db_name: `nd_${slug}_${suffix}`,
      db_user: `u_nd_${slug}_${suffix}`.slice(0, 32),
      db_password: generateStrongPassword(16),
      git_repo: '',
      git_branch: 'main',
      run_npm_install: true,
      harden_security: true,
    });
    setShowInstallModal(true);
  }, [websiteCandidates, installedNodeVersions]);

  const handleScan = async () => {
    markBusy('scan', true);
    try {
      const res = await request('/nodejs-manager/scan', { method: 'POST' });
      if (Array.isArray(res?.instances)) {
        setInstances(res.instances);
        _nodejsToolkitSessionCache.instances = res.instances;
        syncInstanceDrafts(res.instances);
      }
      setNotice(res?.message || 'Đã quét và đồng bộ danh sách ứng dụng Node.js.');
    } catch (err) {
      setError(err?.message || 'Quét ứng dụng Node.js thất bại.');
    } finally {
      markBusy('scan', false);
    }
  };

  const handleInstallSubmit = async e => {
    e.preventDefault();
    if (!installForm.website_id) {
      setError('Vui lòng chọn Website / Tên miền để triển khai ứng dụng Node.js.');
      return;
    }
    markBusy('install', true);
    try {
      const payload = {
        website_id: Number(installForm.website_id),
        subpath: installForm.subpath.trim(),
        app_name: installForm.app_name.trim() || 'Node.js Application',
        install_source: installForm.install_source,
        template: installForm.template,
        node_major: installForm.node_major,
        start_mode: installForm.start_mode,
        start_arg: installForm.start_arg.trim() || 'server.js',
        port: installForm.port ? Number(installForm.port) : null,
        memory_mb: Number(installForm.memory_mb || 512),
        node_env: installForm.node_env,
        create_database: Boolean(installForm.create_database),
        db_name: installForm.create_database ? installForm.db_name.trim() : null,
        db_user: installForm.create_database ? installForm.db_user.trim() : null,
        db_password: installForm.create_database ? installForm.db_password : null,
        git_repo: installForm.install_source === 'git' ? installForm.git_repo.trim() : null,
        git_branch: installForm.git_branch.trim() || 'main',
        run_npm_install: Boolean(installForm.run_npm_install),
        harden_security: Boolean(installForm.harden_security),
      };
      const res = await request('/nodejs-manager/install', {
        method: 'POST',
        body: JSON.stringify(payload),
      });
      setShowInstallModal(false);
      if (res?.instance) {
        replaceSingleInstance(res.instance);
        setExpandedIds(prev => ({ ...prev, [res.instance.id]: true }));
        setSelectedId(res.instance.id);
      } else {
        await loadInstances({ silent: true, refresh: true });
      }
      if (res?.credentials) {
        setInstallSuccessCreds(res.credentials);
      }
      setNotice(res?.message || 'Đã triển khai ứng dụng Node.js thành công.');
    } catch (err) {
      setError(err?.message || 'Không thể cài đặt ứng dụng Node.js.');
    } finally {
      markBusy('install', false);
    }
  };

  const handleProcessAction = async (inst, action, customDraft = null) => {
    const draft = customDraft || processDrafts[inst.id] || {};
    const busyKey = `proc_${inst.id}_${action}`;
    markBusy(busyKey, true);
    try {
      const payload = {
        action,
        node_major: draft.node_major || String(inst.node_major || '22'),
        start_mode: draft.start_mode || inst.start_mode || 'node',
        start_arg: draft.start_arg || inst.start_arg || 'server.js',
        port: draft.port ? Number(draft.port) : Number(inst.port || 21000),
        memory_mb: Number(draft.memory_mb || inst.memory_mb || 512),
        node_env: draft.node_env || inst.node_env || 'production',
      };
      const res = await request(`/nodejs-manager/instances/${inst.id}/process`, {
        method: 'POST',
        body: JSON.stringify(payload),
      });
      if (res?.instance) replaceSingleInstance(res.instance);
      setNotice(res?.message || 'Đã cập nhật trạng thái tiến trình Node.js.');
    } catch (err) {
      setError(err?.message || 'Thao tác tiến trình Node.js thất bại.');
    } finally {
      markBusy(busyKey, false);
    }
  };

  const handleNpmAction = async (inst, action, extra = {}) => {
    const busyKey = `npm_${inst.id}_${action}_${extra.package || extra.script || ''}`;
    markBusy(busyKey, true);
    try {
      const res = await request(`/nodejs-manager/instances/${inst.id}/npm`, {
        method: 'POST',
        body: JSON.stringify({ action, ...extra }),
      });
      if (res?.instance) replaceSingleInstance(res.instance);
      setNotice(res?.message || `Đã thực thi thao tác NPM (${action}).`);
    } catch (err) {
      setError(err?.message || 'Thực thi lệnh NPM thất bại.');
    } finally {
      markBusy(busyKey, false);
    }
  };

  const handleSecurityAction = async (inst, { checkId = null, enabled = true, applyAll = false }) => {
    const busyKey = `sec_${inst.id}_${applyAll ? 'all' : checkId}`;
    markBusy(busyKey, true);
    try {
      const res = await request(`/nodejs-manager/instances/${inst.id}/security`, {
        method: 'POST',
        body: JSON.stringify({
          check_id: checkId,
          enabled,
          apply_all: applyAll,
        }),
      });
      if (res?.instance) replaceSingleInstance(res.instance);
      setNotice(res?.message || 'Đã cập nhật cấu hình bảo mật Node.js.');
    } catch (err) {
      setError(err?.message || 'Không thể áp dụng cấu hình bảo mật.');
    } finally {
      markBusy(busyKey, false);
    }
  };

  const handleGitDeploy = async inst => {
    const draft = gitDrafts[inst.id] || {};
    const busyKey = `git_${inst.id}`;
    markBusy(busyKey, true);
    try {
      const res = await request(`/nodejs-manager/instances/${inst.id}/git-deploy`, {
        method: 'POST',
        body: JSON.stringify({
          git_repo: draft.git_repo ?? inst.git_repo ?? '',
          git_branch: draft.git_branch || inst.git_branch || 'main',
          build_script: draft.build_script ?? inst.git_build_script ?? '',
          run_npm_install: draft.run_npm_install !== false,
          restart_process: draft.restart_process !== false,
        }),
      });
      if (res?.instance) replaceSingleInstance(res.instance);
      setNotice(res?.message || 'Đã triển khai Git Auto-Deploy thành công.');
    } catch (err) {
      setError(err?.message || 'Triển khai từ Git thất bại.');
    } finally {
      markBusy(busyKey, false);
    }
  };

  const handleWebhookConfig = async (inst, { enabled = true, rotateToken = false }) => {
    const busyKey = `wh_${inst.id}`;
    markBusy(busyKey, true);
    try {
      const res = await request(`/nodejs-manager/instances/${inst.id}/webhook`, {
        method: 'POST',
        body: JSON.stringify({ enabled, rotate_token: rotateToken }),
      });
      if (res?.instance) replaceSingleInstance(res.instance);
      setNotice(res?.message || 'Đã cập nhật Webhook.');
    } catch (err) {
      setError(err?.message || 'Không thể cập nhật Webhook.');
    } finally {
      markBusy(busyKey, false);
    }
  };

  const openEnvModal = async inst => {
    setEnvModalInstance(inst);
    setEnvModalData({
      mode: 'structured',
      raw: '',
      parsed: {},
      loading: true,
      restartAfterSave: true,
    });
    try {
      const res = await request(`/nodejs-manager/instances/${inst.id}/env`);
      setEnvModalData({
        mode: 'structured',
        raw: res?.raw || '',
        parsed: res?.parsed || {},
        loading: false,
        restartAfterSave: true,
      });
    } catch (err) {
      setError(err?.message || 'Không thể đọc tệp .env.');
      setEnvModalInstance(null);
    }
  };

  const handleSaveEnv = async e => {
    e.preventDefault();
    if (!envModalInstance) return;
    markBusy('save_env', true);
    try {
      const p = envModalData.parsed || {};
      const payload =
        envModalData.mode === 'raw'
          ? {
              mode: 'raw',
              raw_content: envModalData.raw,
              restart_after_save: envModalData.restartAfterSave,
            }
          : {
              mode: 'structured',
              restart_after_save: envModalData.restartAfterSave,
              updates: {
                APP_NAME: p.app_name || '',
                NODE_ENV: p.node_env || 'production',
                PORT: String(p.port || envModalInstance.port || 21000),
                HOST: p.host || '127.0.0.1',
                APP_URL: p.app_url || '',
                TZ: p.tz || 'Asia/Ho_Chi_Minh',
                JWT_SECRET: p.jwt_secret || '',
                LOG_LEVEL: p.log_level || 'info',
                DB_HOST: p.db_host || 'localhost',
                DB_PORT: String(p.db_port || '3306'),
                DB_DATABASE: p.db_name || '',
                DB_USERNAME: p.db_user || '',
                DB_PASSWORD: p.db_password || '',
              },
            };
      const res = await request(`/nodejs-manager/instances/${envModalInstance.id}/env`, {
        method: 'PUT',
        body: JSON.stringify(payload),
      });
      if (res?.instance) replaceSingleInstance(res.instance);
      setNotice(res?.message || 'Đã lưu tệp biến môi trường .env.');
      setEnvModalInstance(null);
    } catch (err) {
      setError(err?.message || 'Không thể lưu tệp .env.');
    } finally {
      markBusy('save_env', false);
    }
  };

  const openLogsModal = async (inst, kind = 'systemd', lines = 120) => {
    setLogsModalInstance(inst);
    setLogsModalData(prev => ({ ...prev, kind, lines, loading: true }));
    try {
      const res = await request(
        `/nodejs-manager/instances/${inst.id}/logs?kind=${encodeURIComponent(kind)}&lines=${lines}`
      );
      setLogsModalData({
        kind,
        lines,
        content: res?.content || '',
        loading: false,
      });
    } catch (err) {
      setLogsModalData({
        kind,
        lines,
        content: `Không thể tải nhật ký: ${err?.message || err}`,
        loading: false,
      });
    }
  };

  const handleClearLogs = async () => {
    if (!logsModalInstance) return;
    markBusy('clear_logs', true);
    try {
      const res = await request(`/nodejs-manager/instances/${logsModalInstance.id}/logs`, {
        method: 'DELETE',
      });
      setNotice(res?.message || 'Đã xóa bộ đệm nhật ký ứng dụng.');
      await openLogsModal(logsModalInstance, logsModalData.kind, logsModalData.lines);
    } catch (err) {
      setError(err?.message || 'Không thể xóa nhật ký.');
    } finally {
      markBusy('clear_logs', false);
    }
  };

  const handleInstallNodeMajor = async major => {
    const busyKey = `node_ver_${major}`;
    markBusy(busyKey, true);
    try {
      const res = await request('/nodejs-manager/node-versions/install', {
        method: 'POST',
        body: JSON.stringify({ node_major: String(major) }),
      });
      if (Array.isArray(res?.installed_versions)) {
        setInstalledNodeVersions(res.installed_versions);
        _nodejsToolkitSessionCache.installedNodeVersions = res.installed_versions;
      }
      setNotice(res?.message || `Đã cài đặt Node.js v${major} LTS.`);
    } catch (err) {
      setError(err?.message || `Không thể cài đặt Node.js v${major}.`);
    } finally {
      markBusy(busyKey, false);
    }
  };

  const handleConfirmRemoveOrDetach = async e => {
    e.preventDefault();
    if (!removeModalInstance) return;
    const isUninstall = removeOptions.mode === 'uninstall';
    if (
      isUninstall &&
      removeOptions.deleteFiles &&
      removeOptions.confirmDomain.trim().toLowerCase() !==
        String(removeModalInstance.domain || '').toLowerCase()
    ) {
      setError(`Vui lòng nhập chính xác tên miền "${removeModalInstance.domain}" để xác nhận xóa mã nguồn.`);
      return;
    }
    markBusy('remove_instance', true);
    try {
      if (!isUninstall) {
        const res = await request(`/nodejs-manager/instances/${removeModalInstance.id}/detach`, {
          method: 'POST',
        });
        setNotice(res?.message || 'Đã gỡ ứng dụng khỏi danh sách quản lý.');
      } else {
        const res = await request(`/nodejs-manager/instances/${removeModalInstance.id}/remove`, {
          method: 'POST',
          body: JSON.stringify({
            delete_files: Boolean(removeOptions.deleteFiles),
            drop_database: Boolean(removeOptions.dropDatabase),
          }),
        });
        setNotice(res?.message || 'Đã gỡ cài đặt ứng dụng Node.js.');
      }
      const removedId = removeModalInstance.id;
      setRemoveModalInstance(null);
      setInstances(prev => {
        const next = prev.filter(i => i.id !== removedId);
        _nodejsToolkitSessionCache.instances = next;
        return next;
      });
    } catch (err) {
      setError(err?.message || 'Thao tác gỡ ứng dụng thất bại.');
    } finally {
      markBusy('remove_instance', false);
    }
  };

  // Render workspace tabs inside an expanded card or drawer
  const renderInstanceWorkspace = inst => {
    const activeTab = activeSubTabs[inst.id] || 'process';
    const setTab = tabName =>
      setActiveSubTabs(prev => ({ ...prev, [inst.id]: tabName }));
    const pDraft = processDrafts[inst.id] || {
      node_major: String(inst.node_major || '22'),
      start_mode: inst.start_mode || 'node',
      start_arg: inst.start_arg || 'server.js',
      port: String(inst.port || 21000),
      memory_mb: Number(inst.memory_mb || 512),
      node_env: inst.node_env || 'production',
    };
    const updatePDraft = patch =>
      setProcessDrafts(prev => ({
        ...prev,
        [inst.id]: { ...pDraft, ...patch },
      }));
    const gDraft = gitDrafts[inst.id] || {
      git_repo: inst.git_repo || '',
      git_branch: inst.git_branch || 'main',
      build_script: inst.git_build_script || '',
      run_npm_install: true,
      restart_process: true,
    };
    const updateGDraft = patch =>
      setGitDrafts(prev => ({
        ...prev,
        [inst.id]: { ...gDraft, ...patch },
      }));

    const isRunning = inst.status === 'running';
    const scriptsMap = inst.scripts || {};
    const scriptKeys = Object.keys(scriptsMap);
    const installedPkgSet = new Set((inst.packages || []).map(p => p.name));

    return (
      <div className="wp-instance-body" style={{ borderTop: '1px solid var(--border-subtle)', padding: '18px 20px' }}>
        {/* Sub-navigation bar */}
        <div
          style={{
            display: 'flex',
            flexWrap: 'wrap',
            alignItems: 'center',
            justifyContent: 'space-between',
            gap: '10px',
            marginBottom: '16px',
            paddingBottom: '12px',
            borderBottom: '1px solid var(--border-subtle)',
          }}
        >
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px' }}>
            {[
              { id: 'process', label: 'Tiến trình & Runtime', icon: Cpu },
              {
                id: 'npm',
                label: `Gói NPM & Scripts (${(inst.packages || []).length})`,
                icon: PackageOpen,
              },
              {
                id: 'security',
                label: `Bảo mật Production (${inst.security_score || 0}%)`,
                icon: ShieldCheck,
              },
              { id: 'git', label: 'Git Auto-Deploy & Webhook', icon: GitBranch },
            ].map(tab => {
              const IconComp = tab.icon;
              const isActive = activeTab === tab.id;
              return (
                <button
                  key={tab.id}
                  type="button"
                  className={isActive ? 'btn-primary' : 'btn-secondary'}
                  style={{
                    display: 'inline-flex',
                    alignItems: 'center',
                    gap: '6px',
                    padding: '6px 12px',
                    fontSize: '12.5px',
                  }}
                  onClick={() => setTab(tab.id)}
                >
                  <IconComp size={14} />
                  {tab.label}
                </button>
              );
            })}
          </div>

          <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px' }}>
            <button
              type="button"
              className="btn-secondary"
              style={{ fontSize: '12px', padding: '5px 10px' }}
              onClick={() => openEnvModal(inst)}
            >
              <Settings size={13} />
              Biến .env
            </button>
            <button
              type="button"
              className="btn-secondary"
              style={{ fontSize: '12px', padding: '5px 10px' }}
              onClick={() => openLogsModal(inst, 'systemd', 120)}
            >
              <FileText size={13} />
              Nhật ký (Logs)
            </button>
            {openWebsiteFileManager && (
              <button
                type="button"
                className="btn-secondary"
                style={{ fontSize: '12px', padding: '5px 10px' }}
                onClick={() =>
                  openWebsiteFileManager({
                    id: inst.website_id,
                    domain: inst.domain,
                    document_root: inst.document_root,
                  })
                }
              >
                <FolderOpen size={13} />
                File Manager
              </button>
            )}
            <button
              type="button"
              className="btn-secondary"
              style={{
                fontSize: '12px',
                padding: '5px 10px',
                color: 'var(--danger, #ef4444)',
              }}
              onClick={() => {
                setRemoveOptions({
                  mode: 'detach',
                  deleteFiles: false,
                  dropDatabase: false,
                  confirmDomain: '',
                });
                setRemoveModalInstance(inst);
              }}
            >
              <Trash2 size={13} />
              Gỡ bỏ
            </button>
          </div>
        </div>

        {/* TAB 1: Process & Runtime Control */}
        {activeTab === 'process' && (
          <div style={{ display: 'grid', gap: '16px' }}>
            {/* Live daemon telemetry strip */}
            <div
              style={{
                display: 'grid',
                gridTemplateColumns: 'repeat(auto-fit, minmax(170px, 1fr))',
                gap: '12px',
                padding: '14px',
                borderRadius: '10px',
                background: 'var(--surface-muted, rgba(148,163,184,0.08))',
                border: '1px solid var(--border-subtle)',
              }}
            >
              <div>
                <div className="hint" style={{ fontSize: '11.5px' }}>Trạng thái Daemon</div>
                <div style={{ display: 'flex', alignItems: 'center', gap: '6px', marginTop: '4px', fontWeight: 600 }}>
                  <span
                    style={{
                      width: '8px',
                      height: '8px',
                      borderRadius: '50%',
                      background: isRunning ? '#22c55e' : '#94a3b8',
                    }}
                  />
                  <span>{isRunning ? 'Đang chạy (Active)' : 'Đã dừng (Stopped)'}</span>
                </div>
              </div>
              <div>
                <div className="hint" style={{ fontSize: '11.5px' }}>Cổng Reverse Proxy</div>
                <div style={{ marginTop: '4px', fontWeight: 600, fontFamily: 'monospace' }}>
                  127.0.0.1:{inst.port}
                </div>
              </div>
              <div>
                <div className="hint" style={{ fontSize: '11.5px' }}>PID / Bộ nhớ hiện tại</div>
                <div style={{ marginTop: '4px', fontWeight: 600 }}>
                  {inst.process?.main_pid ? `PID ${inst.process.main_pid}` : '—'} ·{' '}
                  {formatMemoryBytes(inst.process?.memory_current_bytes)} / {inst.memory_mb} MB
                </div>
              </div>
              <div>
                <div className="hint" style={{ fontSize: '11.5px' }}>Lệnh khởi chạy</div>
                <div style={{ marginTop: '4px', fontWeight: 600, fontFamily: 'monospace', fontSize: '12.5px' }}>
                  {inst.start_command_display}
                </div>
              </div>
            </div>

            {/* Process control buttons */}
            <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: '8px' }}>
              <button
                type="button"
                className="btn-primary"
                disabled={busyMap[`proc_${inst.id}_start`] || isRunning}
                onClick={() => handleProcessAction(inst, 'start')}
              >
                <Play size={14} />
                {busyMap[`proc_${inst.id}_start`] ? 'Đang khởi động...' : 'Khởi động (Start)'}
              </button>
              <button
                type="button"
                className="btn-secondary"
                disabled={busyMap[`proc_${inst.id}_restart`]}
                onClick={() => handleProcessAction(inst, 'restart')}
              >
                <RotateCcw size={14} />
                {busyMap[`proc_${inst.id}_restart`] ? 'Đang khởi động lại...' : 'Khởi động lại (Restart)'}
              </button>
              <button
                type="button"
                className="btn-secondary"
                disabled={busyMap[`proc_${inst.id}_stop`] || !isRunning}
                onClick={() => handleProcessAction(inst, 'stop')}
              >
                <Square size={14} />
                {busyMap[`proc_${inst.id}_stop`] ? 'Đang dừng...' : 'Dừng tiến trình (Stop)'}
              </button>
              <a
                href={inst.url}
                target="_blank"
                rel="noopener noreferrer"
                className="btn-secondary"
                style={{ textDecoration: 'none', display: 'inline-flex', alignItems: 'center', gap: '6px' }}
              >
                <ExternalLink size={14} />
                Mở Website
              </a>
            </div>

            {/* Runtime configuration form */}
            <div
              style={{
                display: 'grid',
                gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))',
                gap: '12px',
                paddingTop: '8px',
              }}
            >
              <label style={{ display: 'grid', gap: '5px', fontSize: '12.5px' }}>
                <span>Phiên bản Node.js</span>
                <select
                  value={pDraft.node_major}
                  onChange={e => updatePDraft({ node_major: e.target.value })}
                >
                  {nodeVersions.map(v => (
                    <option key={v} value={v}>
                      Node.js v{v} {v === '24' ? '(Current)' : '(LTS)'}{' '}
                      {installedNodeVersions.includes(v) ? '✓' : '(Tự động cài)'}
                    </option>
                  ))}
                </select>
              </label>

              <label style={{ display: 'grid', gap: '5px', fontSize: '12.5px' }}>
                <span>Chế độ khởi chạy</span>
                <select
                  value={pDraft.start_mode}
                  onChange={e => {
                    const mode = e.target.value;
                    const defaultArg = mode === 'npm' || mode === 'yarn' ? 'start' : 'server.js';
                    updatePDraft({ start_mode: mode, start_arg: defaultArg });
                  }}
                >
                  {START_MODES.map(m => (
                    <option key={m.value} value={m.value}>
                      {m.label}
                    </option>
                  ))}
                </select>
              </label>

              <label style={{ display: 'grid', gap: '5px', fontSize: '12.5px' }}>
                <span>
                  {pDraft.start_mode === 'npm' || pDraft.start_mode === 'yarn'
                    ? 'Tên Script (package.json)'
                    : 'Tệp Entrypoint (.js / .mjs)'}
                </span>
                <input
                  type="text"
                  value={pDraft.start_arg}
                  onChange={e => updatePDraft({ start_arg: e.target.value })}
                  placeholder={pDraft.start_mode === 'npm' ? 'start' : 'server.js'}
                />
              </label>

              <label style={{ display: 'grid', gap: '5px', fontSize: '12.5px' }}>
                <span>Cổng nội bộ (21000–21999)</span>
                <input
                  type="number"
                  min={21000}
                  max={21999}
                  value={pDraft.port}
                  onChange={e => updatePDraft({ port: e.target.value })}
                />
              </label>

              <label style={{ display: 'grid', gap: '5px', fontSize: '12.5px' }}>
                <span>Giới hạn RAM (MemoryMax)</span>
                <select
                  value={pDraft.memory_mb}
                  onChange={e => updatePDraft({ memory_mb: Number(e.target.value) })}
                >
                  {MEMORY_PRESETS.map(mb => (
                    <option key={mb} value={mb}>
                      {mb} MB {mb >= 1024 ? `(${(mb / 1024).toFixed(0)} GB)` : ''}
                    </option>
                  ))}
                </select>
              </label>

              <label style={{ display: 'grid', gap: '5px', fontSize: '12.5px' }}>
                <span>Môi trường (NODE_ENV)</span>
                <select
                  value={pDraft.node_env}
                  onChange={e => updatePDraft({ node_env: e.target.value })}
                >
                  <option value="production">production (Khuyên dùng)</option>
                  <option value="development">development</option>
                  <option value="staging">staging</option>
                </select>
              </label>
            </div>

            <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
              <button
                type="button"
                className="btn-primary"
                disabled={busyMap[`proc_${inst.id}_configure`]}
                onClick={() => handleProcessAction(inst, 'configure', pDraft)}
              >
                <Save size={14} />
                {busyMap[`proc_${inst.id}_configure`]
                  ? 'Đang đồng bộ systemd & Nginx...'
                  : 'Lưu & Đồng bộ cấu hình Runtime'}
              </button>
            </div>
          </div>
        )}

        {/* TAB 2: NPM Packages & Scripts */}
        {activeTab === 'npm' && (
          <div style={{ display: 'grid', gap: '16px' }}>
            {/* Quick NPM actions */}
            <div
              style={{
                display: 'grid',
                gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))',
                gap: '10px',
              }}
            >
              {NPM_QUICK_ACTIONS.map(item => {
                const isBusy = busyMap[`npm_${inst.id}_${item.action}_`];
                return (
                  <div
                    key={item.action}
                    style={{
                      padding: '12px',
                      borderRadius: '10px',
                      border: '1px solid var(--border-subtle)',
                      display: 'flex',
                      flexDirection: 'column',
                      justifyContent: 'space-between',
                      gap: '10px',
                    }}
                  >
                    <div>
                      <div style={{ fontWeight: 600, fontFamily: 'monospace', fontSize: '13px' }}>
                        {item.title}
                      </div>
                      <div className="hint" style={{ fontSize: '11.5px', marginTop: '3px' }}>
                        {item.desc}
                      </div>
                    </div>
                    <button
                      type="button"
                      className="btn-secondary"
                      disabled={isBusy}
                      onClick={() => handleNpmAction(inst, item.action)}
                    >
                      <Terminal size={13} />
                      {isBusy ? 'Đang chạy...' : item.subtitle}
                    </button>
                  </div>
                );
              })}
            </div>

            {/* Scripts from package.json */}
            {scriptKeys.length > 0 && (
              <div
                style={{
                  padding: '12px 14px',
                  borderRadius: '10px',
                  border: '1px solid var(--border-subtle)',
                }}
              >
                <div style={{ fontWeight: 600, fontSize: '13px', marginBottom: '8px' }}>
                  Kịch bản trong package.json (`npm run ...`)
                </div>
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: '8px' }}>
                  {scriptKeys.map(sName => {
                    const busyKey = `npm_${inst.id}_run_script_${sName}`;
                    return (
                      <button
                        key={sName}
                        type="button"
                        className="btn-secondary"
                        style={{ fontSize: '12px', padding: '5px 10px' }}
                        disabled={busyMap[busyKey]}
                        title={scriptsMap[sName]}
                        onClick={() => handleNpmAction(inst, 'run_script', { script: sName })}
                      >
                        <Play size={12} />
                        {busyMap[busyKey] ? `Đang chạy ${sName}...` : `npm run ${sName}`}
                      </button>
                    );
                  })}
                </div>
              </div>
            )}

            {/* Install custom package */}
            <div
              style={{
                display: 'flex',
                flexWrap: 'wrap',
                gap: '8px',
                alignItems: 'center',
              }}
            >
              <input
                type="text"
                style={{ flex: '1 1 240px' }}
                placeholder="Nhập tên gói NPM (VD: express, prisma, socket.io@^4.8.0)..."
                value={customPkgInputs[inst.id] || ''}
                onChange={e =>
                  setCustomPkgInputs(prev => ({ ...prev, [inst.id]: e.target.value }))
                }
              />
              <button
                type="button"
                className="btn-primary"
                disabled={
                  !String(customPkgInputs[inst.id] || '').trim() ||
                  busyMap[`npm_${inst.id}_add_${String(customPkgInputs[inst.id] || '').trim()}`]
                }
                onClick={() => {
                  const pkg = String(customPkgInputs[inst.id] || '').trim();
                  if (!pkg) return;
                  handleNpmAction(inst, 'add', { package: pkg, dev: false });
                  setCustomPkgInputs(prev => ({ ...prev, [inst.id]: '' }));
                }}
              >
                <Plus size={14} />
                Cài gói (dependencies)
              </button>
            </div>

            {/* Curated NPM packages */}
            <div>
              <div style={{ fontWeight: 600, fontSize: '13px', marginBottom: '8px' }}>
                Thư viện Node.js phổ biến khuyên dùng
              </div>
              <div
                style={{
                  display: 'grid',
                  gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))',
                  gap: '10px',
                }}
              >
                {curatedPackages.map(pkg => {
                  const isInstalled = installedPkgSet.has(pkg.name);
                  const busyAdd = busyMap[`npm_${inst.id}_add_${pkg.name}`];
                  const busyRem = busyMap[`npm_${inst.id}_remove_${pkg.name}`];
                  return (
                    <div
                      key={pkg.name}
                      style={{
                        padding: '11px 13px',
                        borderRadius: '10px',
                        border: '1px solid var(--border-subtle)',
                        display: 'flex',
                        flexDirection: 'column',
                        justifyContent: 'space-between',
                        gap: '8px',
                      }}
                    >
                      <div>
                        <div style={{ display: 'flex', justifyContent: 'space-between', gap: '6px' }}>
                          <strong style={{ fontSize: '13px' }}>{pkg.title}</strong>
                          <span className="pill" style={{ fontSize: '11px' }}>
                            {pkg.category}
                          </span>
                        </div>
                        <div style={{ fontFamily: 'monospace', fontSize: '11.5px', opacity: 0.75 }}>
                          {pkg.name} ({pkg.version})
                        </div>
                        <div className="hint" style={{ fontSize: '11.5px', marginTop: '4px' }}>
                          {pkg.description}
                        </div>
                      </div>
                      <div>
                        {isInstalled ? (
                          <button
                            type="button"
                            className="btn-secondary"
                            style={{ width: '100%', fontSize: '12px' }}
                            disabled={busyRem}
                            onClick={() => handleNpmAction(inst, 'remove', { package: pkg.name })}
                          >
                            {busyRem ? 'Đang gỡ...' : 'Đã cài · Gỡ gói'}
                          </button>
                        ) : (
                          <button
                            type="button"
                            className="btn-secondary"
                            style={{ width: '100%', fontSize: '12px' }}
                            disabled={busyAdd}
                            onClick={() => handleNpmAction(inst, 'add', { package: pkg.name })}
                          >
                            <Plus size={12} />
                            {busyAdd ? 'Đang cài đặt...' : `Cài đặt ${pkg.name}`}
                          </button>
                        )}
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>

            {/* Recent NPM console output */}
            {Array.isArray(inst.last_npm_logs) && inst.last_npm_logs.length > 0 && (
              <div
                style={{
                  background: '#0f172a',
                  color: '#e2e8f0',
                  borderRadius: '10px',
                  padding: '12px 14px',
                  fontFamily: 'monospace',
                  fontSize: '12px',
                  maxHeight: '220px',
                  overflowY: 'auto',
                }}
              >
                <div style={{ color: '#38bdf8', marginBottom: '6px', fontWeight: 600 }}>
                  $ {inst.last_npm_logs[inst.last_npm_logs.length - 1].cmd}
                </div>
                <pre style={{ margin: 0, whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
                  {inst.last_npm_logs[inst.last_npm_logs.length - 1].output}
                </pre>
              </div>
            )}
          </div>
        )}

        {/* TAB 3: Production Security Hardening */}
        {activeTab === 'security' && (
          <div style={{ display: 'grid', gap: '14px' }}>
            <div
              style={{
                display: 'flex',
                flexWrap: 'wrap',
                alignItems: 'center',
                justifyContent: 'space-between',
                gap: '12px',
                padding: '12px 14px',
                borderRadius: '10px',
                background: 'var(--surface-muted, rgba(148,163,184,0.08))',
                border: '1px solid var(--border-subtle)',
              }}
            >
              <div>
                <div style={{ fontWeight: 600, fontSize: '14px' }}>
                  Điểm an toàn vận hành Node.js: {inst.security_score || 0}%
                </div>
                <div className="hint" style={{ fontSize: '12px' }}>
                  Kiểm tra cô lập cổng Loopback, bảo vệ tệp nhạy cảm trên Nginx, quyền hạn .env và systemd cgroup.
                </div>
              </div>
              <button
                type="button"
                className="btn-primary"
                disabled={busyMap[`sec_${inst.id}_all`]}
                onClick={() => handleSecurityAction(inst, { applyAll: true, enabled: true })}
              >
                <ShieldCheck size={14} />
                {busyMap[`sec_${inst.id}_all`]
                  ? 'Đang tối ưu bảo mật...'
                  : 'Tối ưu toàn bộ bảo mật (1-Click)'}
              </button>
            </div>

            <div style={{ display: 'grid', gap: '8px' }}>
              {(inst.security_checks || []).map(chk => {
                const isBusy = busyMap[`sec_${inst.id}_${chk.id}`];
                return (
                  <div
                    key={chk.id}
                    style={{
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'space-between',
                      gap: '12px',
                      padding: '10px 14px',
                      borderRadius: '10px',
                      border: '1px solid var(--border-subtle)',
                    }}
                  >
                    <div style={{ display: 'flex', alignItems: 'flex-start', gap: '10px' }}>
                      {chk.passed ? (
                        <CheckCircle2 size={17} style={{ color: '#22c55e', marginTop: '2px', flexShrink: 0 }} />
                      ) : (
                        <AlertTriangle size={17} style={{ color: '#f59e0b', marginTop: '2px', flexShrink: 0 }} />
                      )}
                      <div>
                        <div style={{ fontWeight: 600, fontSize: '13px' }}>{chk.title}</div>
                        <div className="hint" style={{ fontSize: '12px' }}>
                          {chk.description}
                        </div>
                      </div>
                    </div>
                    <div>
                      {chk.id === 'ssl_https' ? (
                        !chk.passed &&
                        navigateToPage && (
                          <button
                            type="button"
                            className="btn-secondary"
                            style={{ fontSize: '12px' }}
                            onClick={() => navigateToPage('ssl')}
                          >
                            Cấp SSL
                          </button>
                        )
                      ) : chk.id === 'lockfile_present' ? (
                        !chk.passed && (
                          <button
                            type="button"
                            className="btn-secondary"
                            style={{ fontSize: '12px' }}
                            onClick={() => handleNpmAction(inst, 'install')}
                          >
                            Chạy npm install
                          </button>
                        )
                      ) : (
                        <button
                          type="button"
                          className={chk.passed ? 'btn-secondary' : 'btn-primary'}
                          style={{ fontSize: '12px' }}
                          disabled={isBusy}
                          onClick={() =>
                            handleSecurityAction(inst, {
                              checkId: chk.id,
                              enabled: !chk.passed,
                            })
                          }
                        >
                          {isBusy ? 'Đang xử lý...' : chk.passed ? 'Đã bật ✓' : 'Kích hoạt'}
                        </button>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        )}

        {/* TAB 4: Git Auto-Deploy & Webhook */}
        {activeTab === 'git' && (
          <div style={{ display: 'grid', gap: '14px' }}>
            <div
              style={{
                display: 'grid',
                gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))',
                gap: '12px',
              }}
            >
              <label style={{ display: 'grid', gap: '5px', fontSize: '12.5px' }}>
                <span>Git Repository URL (HTTPS / SSH)</span>
                <input
                  type="text"
                  placeholder="https://github.com/org/nodejs-app.git"
                  value={gDraft.git_repo}
                  onChange={e => updateGDraft({ git_repo: e.target.value })}
                />
              </label>
              <label style={{ display: 'grid', gap: '5px', fontSize: '12.5px' }}>
                <span>Nhánh triển khai (Branch)</span>
                <input
                  type="text"
                  placeholder="main"
                  value={gDraft.git_branch}
                  onChange={e => updateGDraft({ git_branch: e.target.value })}
                />
              </label>
              <label style={{ display: 'grid', gap: '5px', fontSize: '12.5px' }}>
                <span>Script Build sau khi Pull (Tùy chọn)</span>
                <input
                  type="text"
                  placeholder="VD: build (để trống nếu không cần)"
                  value={gDraft.build_script}
                  onChange={e => updateGDraft({ build_script: e.target.value })}
                />
              </label>
            </div>

            <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', justifyContent: 'space-between', gap: '10px' }}>
              <div style={{ display: 'flex', gap: '16px', fontSize: '12.5px' }}>
                <label style={{ display: 'inline-flex', alignItems: 'center', gap: '6px' }}>
                  <input
                    type="checkbox"
                    checked={gDraft.run_npm_install !== false}
                    onChange={e => updateGDraft({ run_npm_install: e.target.checked })}
                  />
                  Tự động chạy `npm install`
                </label>
                <label style={{ display: 'inline-flex', alignItems: 'center', gap: '6px' }}>
                  <input
                    type="checkbox"
                    checked={gDraft.restart_process !== false}
                    onChange={e => updateGDraft({ restart_process: e.target.checked })}
                  />
                  Khởi động lại tiến trình sau khi Deploy
                </label>
              </div>

              <button
                type="button"
                className="btn-primary"
                disabled={busyMap[`git_${inst.id}`]}
                onClick={() => handleGitDeploy(inst)}
              >
                <GitBranch size={14} />
                {busyMap[`git_${inst.id}`]
                  ? 'Đang Pull & Triển khai...'
                  : 'Triển khai ngay (Git Pull + NPM + Restart)'}
              </button>
            </div>

            {/* Webhook section */}
            <div
              style={{
                padding: '12px 14px',
                borderRadius: '10px',
                border: '1px solid var(--border-subtle)',
                display: 'grid',
                gap: '8px',
              }}
            >
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                <strong style={{ fontSize: '13px' }}>Webhook CI/CD (GitHub / GitLab Push)</strong>
                <div style={{ display: 'flex', gap: '6px' }}>
                  <button
                    type="button"
                    className="btn-secondary"
                    style={{ fontSize: '12px', padding: '4px 10px' }}
                    onClick={() =>
                      copyText(
                        `${window.location.origin}${inst.webhook_path}`,
                        'Đã sao chép URL Webhook Auto-Deploy.'
                      )
                    }
                  >
                    <Copy size={12} />
                    Sao chép Webhook URL
                  </button>
                  <button
                    type="button"
                    className="btn-secondary"
                    style={{ fontSize: '12px', padding: '4px 10px' }}
                    disabled={busyMap[`wh_${inst.id}`]}
                    onClick={() => handleWebhookConfig(inst, { enabled: true, rotateToken: true })}
                  >
                    <RotateCcw size={12} />
                    Đổi khóa Token
                  </button>
                </div>
              </div>
              <div
                style={{
                  fontFamily: 'monospace',
                  fontSize: '12px',
                  padding: '8px 10px',
                  borderRadius: '6px',
                  background: 'var(--surface-muted, rgba(148,163,184,0.08))',
                  wordBreak: 'break-all',
                }}
              >
                POST {window.location.origin}
                {inst.webhook_path}
              </div>
            </div>
          </div>
        )}
      </div>
    );
  };

  return (
    <div className="page-container" style={{ display: 'grid', gap: '18px' }}>
      {/* Top Header & Action Bar */}
      <div
        className="section"
        style={{
          display: 'flex',
          flexWrap: 'wrap',
          alignItems: 'center',
          justifyContent: 'space-between',
          gap: '14px',
          padding: '18px 20px',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
          <div
            style={{
              width: '42px',
              height: '42px',
              borderRadius: '11px',
              background: 'rgba(34, 197, 94, 0.14)',
              color: '#16a34a',
              display: 'grid',
              placeItems: 'center',
            }}
          >
            <NodejsBrandIcon size={22} />
          </div>
          <div>
            <h2 style={{ margin: 0, fontSize: '18px', display: 'flex', alignItems: 'center', gap: '8px' }}>
              Node.js Manager
              <span className="pill" style={{ fontSize: '11.5px' }}>
                {instances.length} ứng dụng
              </span>
            </h2>
            <p className="hint" style={{ margin: '3px 0 0', fontSize: '13px' }}>
              Quản lý trọn vòng đời ứng dụng Node.js (Express, Next.js, NestJS, Socket.IO), dịch vụ systemd, Reverse Proxy Nginx và gói NPM.
            </p>
          </div>
        </div>

        <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: '8px' }}>
          <button
            type="button"
            className="btn-secondary"
            onClick={() => setShowRuntimeModal(true)}
          >
            <Cpu size={14} />
            Node.js Runtimes ({installedNodeVersions.map(v => `v${v}`).join(', ') || 'v22'})
          </button>
          <button
            type="button"
            className="btn-secondary"
            disabled={busyMap.scan}
            onClick={handleScan}
          >
            <Search size={14} />
            {busyMap.scan ? 'Đang quét...' : 'Quét ứng dụng'}
          </button>
          <button
            type="button"
            className="btn-secondary"
            disabled={initialLoading}
            onClick={() => loadInstances({ refresh: true })}
          >
            <RefreshCw size={14} />
            Làm mới
          </button>
          <button type="button" className="btn-primary" onClick={openInstallModal}>
            <Plus size={15} />
            Cài đặt Node.js
          </button>
        </div>
      </div>

      {/* Filter & View Switcher Toolbar */}
      {instances.length > 0 && (
        <div
          style={{
            display: 'flex',
            flexWrap: 'wrap',
            alignItems: 'center',
            justifyContent: 'space-between',
            gap: '10px',
          }}
        >
          <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: '8px', flex: '1 1 320px' }}>
            <div style={{ position: 'relative', flex: '1 1 240px', maxWidth: '360px' }}>
              <input
                type="text"
                placeholder="Tìm kiếm tên miền, ứng dụng, framework, cổng..."
                value={searchQuery}
                onChange={e => setSearchQuery(e.target.value)}
                style={{ width: '100%' }}
              />
            </div>
            <select
              value={statusFilter}
              onChange={e => setStatusFilter(e.target.value)}
              style={{ width: 'auto' }}
            >
              <option value="all">Tất cả trạng thái ({instances.length})</option>
              <option value="running">Đang chạy (Running)</option>
              <option value="stopped">Đã dừng (Stopped)</option>
              <option value="security">Cần tối ưu bảo mật</option>
            </select>
          </div>

          <div style={{ display: 'flex', gap: '6px' }}>
            {[
              { id: 'expanded', label: 'Chi tiết', icon: Layers },
              { id: 'cards', label: 'Lưới', icon: LayoutGrid },
              { id: 'list', label: 'Danh sách', icon: List },
            ].map(m => {
              const IconComp = m.icon;
              return (
                <button
                  key={m.id}
                  type="button"
                  className={viewMode === m.id ? 'btn-primary' : 'btn-secondary'}
                  style={{ padding: '6px 10px', fontSize: '12px' }}
                  onClick={() => setViewMode(m.id)}
                >
                  <IconComp size={14} />
                  {m.label}
                </button>
              );
            })}
          </div>
        </div>
      )}

      {/* Main Content Area */}
      {initialLoading && instances.length === 0 ? (
        <div className="section" style={{ padding: '42px 20px', textAlign: 'center' }}>
          <RefreshCw size={24} className="spin" style={{ marginBottom: '10px', opacity: 0.7 }} />
          <div>Đang tải danh sách ứng dụng Node.js...</div>
        </div>
      ) : filteredInstances.length === 0 ? (
        <div className="section" style={{ padding: '44px 24px', textAlign: 'center' }}>
          <div
            style={{
              width: '54px',
              height: '54px',
              borderRadius: '14px',
              background: 'rgba(34, 197, 94, 0.12)',
              color: '#16a34a',
              display: 'grid',
              placeItems: 'center',
              margin: '0 auto 14px',
            }}
          >
            <NodejsBrandIcon size={28} />
          </div>
          <h3 style={{ margin: '0 0 6px' }}>
            {instances.length === 0
              ? 'Chưa có ứng dụng Node.js nào được triển khai'
              : 'Không tìm thấy ứng dụng Node.js phù hợp bộ lọc'}
          </h3>
          <p className="hint" style={{ maxWidth: '520px', margin: '0 auto 18px' }}>
            Khởi tạo nhanh ứng dụng Express.js, Next.js SSR, NestJS hoặc triển khai từ Git Repository với dịch vụ systemd tự động phục hồi và Nginx Reverse Proxy hỗ trợ sẵn WebSocket.
          </p>
          <div style={{ display: 'flex', justifyContent: 'center', gap: '10px' }}>
            <button type="button" className="btn-primary" onClick={openInstallModal}>
              <Plus size={15} />
              Khởi tạo ứng dụng Node.js đầu tiên
            </button>
            <button type="button" className="btn-secondary" onClick={handleScan}>
              <Search size={14} />
              Quét thư mục Website hiện có
            </button>
          </div>
        </div>
      ) : (
        <div style={{ display: 'grid', gap: '14px' }}>
          {filteredInstances.map(inst => {
            const isExpanded =
              viewMode === 'expanded' ? expandedIds[inst.id] !== false : selectedId === inst.id;
            const isRunning = inst.status === 'running';
            return (
              <div
                key={inst.id}
                className="section"
                style={{
                  padding: 0,
                  overflow: 'hidden',
                  border: '1px solid var(--border-subtle)',
                }}
              >
                {/* Instance Header Bar */}
                <div
                  style={{
                    padding: '15px 20px',
                    display: 'flex',
                    flexWrap: 'wrap',
                    alignItems: 'center',
                    justifyContent: 'space-between',
                    gap: '12px',
                    cursor: 'pointer',
                  }}
                  onClick={() => {
                    if (viewMode === 'expanded') {
                      setExpandedIds(prev => ({
                        ...prev,
                        [inst.id]: prev[inst.id] === false,
                      }));
                    } else {
                      setSelectedId(prev => (prev === inst.id ? null : inst.id));
                    }
                  }}
                >
                  <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
                    <div
                      style={{
                        width: '38px',
                        height: '38px',
                        borderRadius: '10px',
                        background: isRunning
                          ? 'rgba(34, 197, 94, 0.14)'
                          : 'rgba(148, 163, 184, 0.15)',
                        color: isRunning ? '#16a34a' : '#64748b',
                        display: 'grid',
                        placeItems: 'center',
                        flexShrink: 0,
                      }}
                    >
                      <NodejsBrandIcon size={20} />
                    </div>
                    <div>
                      <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: '8px' }}>
                        <strong style={{ fontSize: '15px' }}>{inst.domain}</strong>
                        <span
                          className="pill"
                          style={{
                            background: isRunning
                              ? 'rgba(34, 197, 94, 0.15)'
                              : 'rgba(148, 163, 184, 0.18)',
                            color: isRunning ? '#16a34a' : 'inherit',
                            fontSize: '11.5px',
                          }}
                        >
                          ● {isRunning ? 'Running' : 'Stopped'}
                        </span>
                        <span className="pill" style={{ fontSize: '11.5px' }}>
                          {inst.framework || 'Node.js'}
                        </span>
                        <span className="pill" style={{ fontSize: '11.5px' }}>
                          Node v{inst.node_major}
                        </span>
                        <span className="pill" style={{ fontSize: '11.5px', fontFamily: 'monospace' }}>
                          :{inst.port}
                        </span>
                      </div>
                      <div className="hint" style={{ fontSize: '12px', marginTop: '3px' }}>
                        {inst.app_name} · {inst.path}
                        {inst.db_name ? ` · DB: ${inst.db_name}` : ''}
                      </div>
                    </div>
                  </div>

                  <div
                    style={{ display: 'flex', alignItems: 'center', gap: '8px' }}
                    onClick={e => e.stopPropagation()}
                  >
                    <button
                      type="button"
                      className="btn-secondary"
                      style={{ padding: '5px 10px', fontSize: '12px' }}
                      disabled={busyMap[`proc_${inst.id}_restart`]}
                      onClick={() => handleProcessAction(inst, 'restart')}
                    >
                      <RotateCcw size={13} />
                      Restart
                    </button>
                    <button
                      type="button"
                      className="btn-secondary"
                      style={{ padding: '5px 10px', fontSize: '12px' }}
                      onClick={() => openEnvModal(inst)}
                    >
                      <Settings size={13} />
                      .env
                    </button>
                    <button
                      type="button"
                      className="btn-secondary"
                      style={{ padding: '5px 10px', fontSize: '12px' }}
                      onClick={() => openLogsModal(inst, 'systemd', 120)}
                    >
                      <FileText size={13} />
                      Logs
                    </button>
                    <button
                      type="button"
                      className="btn-secondary"
                      style={{ padding: '5px 8px' }}
                      onClick={() => {
                        if (viewMode === 'expanded') {
                          setExpandedIds(prev => ({
                            ...prev,
                            [inst.id]: prev[inst.id] === false,
                          }));
                        } else {
                          setSelectedId(prev => (prev === inst.id ? null : inst.id));
                        }
                      }}
                    >
                      {isExpanded ? <ChevronUp size={15} /> : <ChevronDown size={15} />}
                    </button>
                  </div>
                </div>

                {/* Expanded Workspace */}
                {isExpanded && renderInstanceWorkspace(inst)}
              </div>
            );
          })}
        </div>
      )}

      {/* MODAL 1: Install Node.js Application */}
      {showInstallModal && (
        <div className="modal-backdrop" onClick={() => !busyMap.install && setShowInstallModal(false)}>
          <div
            className="modal-content"
            style={{ maxWidth: '680px', width: '95%' }}
            onClick={e => e.stopPropagation()}
          >
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '14px' }}>
              <h3 style={{ margin: 0, display: 'flex', alignItems: 'center', gap: '8px' }}>
                <NodejsBrandIcon size={20} />
                Triển khai ứng dụng Node.js mới
              </h3>
              <button
                type="button"
                className="btn-secondary"
                disabled={busyMap.install}
                onClick={() => setShowInstallModal(false)}
              >
                <X size={15} />
              </button>
            </div>

            <form onSubmit={handleInstallSubmit} style={{ display: 'grid', gap: '14px' }}>
              <div style={{ display: 'grid', gridTemplateColumns: '2fr 1fr', gap: '12px' }}>
                <label style={{ display: 'grid', gap: '5px', fontSize: '13px' }}>
                  <span>Website / Tên miền đích *</span>
                  <select
                    required
                    value={installForm.website_id}
                    onChange={e => {
                      const wid = e.target.value;
                      const found = websiteCandidates.find(w => String(w.id) === String(wid));
                      const slug = found
                        ? String(found.domain || 'node')
                            .toLowerCase()
                            .replace(/[^a-z0-9]/g, '')
                            .slice(0, 10)
                        : 'node';
                      const suffix = Math.random().toString(16).slice(2, 6);
                      setInstallForm(prev => ({
                        ...prev,
                        website_id: wid,
                        app_name: found ? found.domain : prev.app_name,
                        db_name: `nd_${slug}_${suffix}`,
                        db_user: `u_nd_${slug}_${suffix}`.slice(0, 32),
                      }));
                    }}
                  >
                    <option value="">-- Chọn tên miền --</option>
                    {websiteCandidates.map(w => (
                      <option key={w.id} value={w.id}>
                        {w.domain} {w.has_nodejs ? '(Đã có Node.js)' : ''}
                      </option>
                    ))}
                  </select>
                </label>

                <label style={{ display: 'grid', gap: '5px', fontSize: '13px' }}>
                  <span>Tên hiển thị ứng dụng</span>
                  <input
                    type="text"
                    value={installForm.app_name}
                    onChange={e => setInstallForm(prev => ({ ...prev, app_name: e.target.value }))}
                    placeholder="My Node.js App"
                  />
                </label>
              </div>

              {/* Install source selector */}
              <div style={{ display: 'flex', gap: '8px' }}>
                <button
                  type="button"
                  className={installForm.install_source === 'template' ? 'btn-primary' : 'btn-secondary'}
                  onClick={() => setInstallForm(prev => ({ ...prev, install_source: 'template' }))}
                >
                  <Sparkles size={14} />
                  Khởi tạo từ Mẫu Starter (1-Click)
                </button>
                <button
                  type="button"
                  className={installForm.install_source === 'git' ? 'btn-primary' : 'btn-secondary'}
                  onClick={() => setInstallForm(prev => ({ ...prev, install_source: 'git' }))}
                >
                  <GitBranch size={14} />
                  Sao chép từ Git Repository
                </button>
              </div>

              {installForm.install_source === 'template' ? (
                <div style={{ display: 'grid', gap: '8px' }}>
                  <span style={{ fontSize: '13px', fontWeight: 600 }}>Chọn kiến trúc mẫu (Starter Template)</span>
                  <div
                    style={{
                      display: 'grid',
                      gridTemplateColumns: 'repeat(auto-fit, minmax(195px, 1fr))',
                      gap: '8px',
                    }}
                  >
                    {(starterTemplates.length ? starterTemplates : [
                      {
                        id: 'express-api',
                        name: 'Express.js Web & REST API',
                        badge: 'Phổ biến nhất',
                        description: 'REST API + Health check (/api/health) + Trang trạng thái.',
                        start_mode: 'node',
                        start_arg: 'server.js',
                      },
                      {
                        id: 'nextjs-ssr',
                        name: 'Next.js SSR Ready Starter',
                        badge: 'Full-stack',
                        description: 'Cấu trúc SSR chạy qua Nginx Reverse Proxy.',
                        start_mode: 'node',
                        start_arg: 'server.js',
                      },
                      {
                        id: 'minimal-http',
                        name: 'Node.js Native HTTP',
                        badge: 'Siêu nhẹ',
                        description: 'Máy chủ node:http thuần không phụ thuộc gói ngoài.',
                        start_mode: 'node',
                        start_arg: 'server.js',
                      },
                    ]).map(tpl => {
                      const active = installForm.template === tpl.id;
                      return (
                        <div
                          key={tpl.id}
                          onClick={() =>
                            setInstallForm(prev => ({
                              ...prev,
                              template: tpl.id,
                              start_mode: tpl.start_mode || 'node',
                              start_arg: tpl.start_arg || 'server.js',
                            }))
                          }
                          style={{
                            padding: '10px 12px',
                            borderRadius: '10px',
                            border: active
                              ? '2px solid var(--accent, #2563eb)'
                              : '1px solid var(--border-subtle)',
                            cursor: 'pointer',
                          }}
                        >
                          <div style={{ display: 'flex', justifyContent: 'space-between', gap: '4px' }}>
                            <strong style={{ fontSize: '12.5px' }}>{tpl.name}</strong>
                          </div>
                          <div className="hint" style={{ fontSize: '11.5px', marginTop: '4px' }}>
                            {tpl.description}
                          </div>
                        </div>
                      );
                    })}
                  </div>
                </div>
              ) : (
                <div style={{ display: 'grid', gridTemplateColumns: '2fr 1fr', gap: '12px' }}>
                  <label style={{ display: 'grid', gap: '5px', fontSize: '13px' }}>
                    <span>Git Repository URL *</span>
                    <input
                      type="text"
                      required
                      placeholder="https://github.com/username/repo.git"
                      value={installForm.git_repo}
                      onChange={e => setInstallForm(prev => ({ ...prev, git_repo: e.target.value }))}
                    />
                  </label>
                  <label style={{ display: 'grid', gap: '5px', fontSize: '13px' }}>
                    <span>Nhánh (Branch)</span>
                    <input
                      type="text"
                      value={installForm.git_branch}
                      onChange={e => setInstallForm(prev => ({ ...prev, git_branch: e.target.value }))}
                    />
                  </label>
                </div>
              )}

              {/* Runtime parameters */}
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(145px, 1fr))', gap: '10px' }}>
                <label style={{ display: 'grid', gap: '5px', fontSize: '12.5px' }}>
                  <span>Phiên bản Node.js</span>
                  <select
                    value={installForm.node_major}
                    onChange={e => setInstallForm(prev => ({ ...prev, node_major: e.target.value }))}
                  >
                    {nodeVersions.map(v => (
                      <option key={v} value={v}>
                        Node.js v{v} {v === '24' ? '(Current)' : '(LTS)'}
                      </option>
                    ))}
                  </select>
                </label>

                <label style={{ display: 'grid', gap: '5px', fontSize: '12.5px' }}>
                  <span>Chế độ khởi chạy</span>
                  <select
                    value={installForm.start_mode}
                    onChange={e => {
                      const m = e.target.value;
                      setInstallForm(prev => ({
                        ...prev,
                        start_mode: m,
                        start_arg: m === 'npm' || m === 'yarn' ? 'start' : 'server.js',
                      }));
                    }}
                  >
                    <option value="node">node (File JS)</option>
                    <option value="npm">npm (Script)</option>
                    <option value="npx">npx</option>
                    <option value="yarn">yarn</option>
                  </select>
                </label>

                <label style={{ display: 'grid', gap: '5px', fontSize: '12.5px' }}>
                  <span>Entry File / Script</span>
                  <input
                    type="text"
                    value={installForm.start_arg}
                    onChange={e => setInstallForm(prev => ({ ...prev, start_arg: e.target.value }))}
                  />
                </label>

                <label style={{ display: 'grid', gap: '5px', fontSize: '12.5px' }}>
                  <span>Giới hạn RAM</span>
                  <select
                    value={installForm.memory_mb}
                    onChange={e =>
                      setInstallForm(prev => ({ ...prev, memory_mb: Number(e.target.value) }))
                    }
                  >
                    {MEMORY_PRESETS.map(mb => (
                      <option key={mb} value={mb}>
                        {mb} MB
                      </option>
                    ))}
                  </select>
                </label>
              </div>

              {/* Optional MySQL Database provisioning */}
              <div
                style={{
                  padding: '12px 14px',
                  borderRadius: '10px',
                  border: '1px solid var(--border-subtle)',
                  display: 'grid',
                  gap: '10px',
                }}
              >
                <label style={{ display: 'inline-flex', alignItems: 'center', gap: '8px', fontWeight: 600, fontSize: '13px' }}>
                  <input
                    type="checkbox"
                    checked={installForm.create_database}
                    onChange={e =>
                      setInstallForm(prev => ({ ...prev, create_database: e.target.checked }))
                    }
                  />
                  <Database size={14} />
                  Khởi tạo kèm Cơ sở dữ liệu MySQL / MariaDB và tự động cấu hình vào `.env`
                </label>

                {installForm.create_database && (
                  <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1.2fr', gap: '10px' }}>
                    <label style={{ display: 'grid', gap: '4px', fontSize: '12px' }}>
                      <span>Tên Database</span>
                      <input
                        type="text"
                        value={installForm.db_name}
                        onChange={e => setInstallForm(prev => ({ ...prev, db_name: e.target.value }))}
                      />
                    </label>
                    <label style={{ display: 'grid', gap: '4px', fontSize: '12px' }}>
                      <span>Database User</span>
                      <input
                        type="text"
                        value={installForm.db_user}
                        onChange={e => setInstallForm(prev => ({ ...prev, db_user: e.target.value }))}
                      />
                    </label>
                    <div style={{ display: 'grid', gap: '4px', fontSize: '12px' }}>
                      <span>Mật khẩu Database</span>
                      <PasswordField
                        value={installForm.db_password}
                        onChange={val => setInstallForm(prev => ({ ...prev, db_password: val }))}
                      />
                    </div>
                  </div>
                )}
              </div>

              <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '8px', marginTop: '4px' }}>
                <button
                  type="button"
                  className="btn-secondary"
                  disabled={busyMap.install}
                  onClick={() => setShowInstallModal(false)}
                >
                  Hủy
                </button>
                <button type="submit" className="btn-primary" disabled={busyMap.install}>
                  <Plus size={15} />
                  {busyMap.install ? 'Đang triển khai Node.js...' : 'Triển khai ứng dụng'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* MODAL 2: Post-Install Credentials Summary */}
      {installSuccessCreds && (
        <div className="modal-backdrop" onClick={() => setInstallSuccessCreds(null)}>
          <div
            className="modal-content"
            style={{ maxWidth: '520px', width: '95%' }}
            onClick={e => e.stopPropagation()}
          >
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '12px' }}>
              <h3 style={{ margin: 0, display: 'flex', alignItems: 'center', gap: '8px' }}>
                <CheckCircle2 size={20} style={{ color: '#22c55e' }} />
                Ứng dụng Node.js đã sẵn sàng!
              </h3>
              <button type="button" className="btn-secondary" onClick={() => setInstallSuccessCreds(null)}>
                <X size={15} />
              </button>
            </div>
            <div style={{ display: 'grid', gap: '8px', fontSize: '13px' }}>
              <div>
                <strong>Tên miền:</strong>{' '}
                <a href={installSuccessCreds.url} target="_blank" rel="noopener noreferrer">
                  {installSuccessCreds.url}
                </a>
              </div>
              <div>
                <strong>Node.js Runtime:</strong> v{installSuccessCreds.node_major} LTS (127.0.0.1:
                {installSuccessCreds.port})
              </div>
              <div>
                <strong>Lệnh khởi chạy:</strong> <code>{installSuccessCreds.start_command}</code>
              </div>
              {installSuccessCreds.db_created && (
                <div
                  style={{
                    marginTop: '6px',
                    padding: '10px 12px',
                    borderRadius: '8px',
                    border: '1px solid var(--border-subtle)',
                  }}
                >
                  <div style={{ fontWeight: 600, marginBottom: '4px' }}>Thông tin kết nối MySQL</div>
                  <div>Database: <code>{installSuccessCreds.db_name}</code></div>
                  <div>Username: <code>{installSuccessCreds.db_user}</code></div>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                    <span>Password: <code>{installSuccessCreds.db_password}</code></span>
                    <CopyPasswordButton value={installSuccessCreds.db_password} />
                  </div>
                </div>
              )}
            </div>
            <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: '16px' }}>
              <button type="button" className="btn-primary" onClick={() => setInstallSuccessCreds(null)}>
                Hoàn tất
              </button>
            </div>
          </div>
        </div>
      )}

      {/* MODAL 3: Visual & Raw .env Editor */}
      {envModalInstance && (
        <div className="modal-backdrop" onClick={() => !busyMap.save_env && setEnvModalInstance(null)}>
          <div
            className="modal-content"
            style={{ maxWidth: '680px', width: '95%' }}
            onClick={e => e.stopPropagation()}
          >
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '12px' }}>
              <h3 style={{ margin: 0 }}>
                Cấu hình Biến môi trường (.env) — {envModalInstance.domain}
              </h3>
              <button
                type="button"
                className="btn-secondary"
                onClick={() => setEnvModalInstance(null)}
              >
                <X size={15} />
              </button>
            </div>

            <div style={{ display: 'flex', gap: '8px', marginBottom: '12px' }}>
              <button
                type="button"
                className={envModalData.mode === 'structured' ? 'btn-primary' : 'btn-secondary'}
                onClick={() => setEnvModalData(prev => ({ ...prev, mode: 'structured' }))}
              >
                Chế độ Trực quan (Visual)
              </button>
              <button
                type="button"
                className={envModalData.mode === 'raw' ? 'btn-primary' : 'btn-secondary'}
                onClick={() => setEnvModalData(prev => ({ ...prev, mode: 'raw' }))}
              >
                Chế độ Văn bản gốc (Raw .env)
              </button>
            </div>

            {envModalData.loading ? (
              <div style={{ padding: '24px', textAlign: 'center' }}>Đang đọc tệp .env...</div>
            ) : (
              <form onSubmit={handleSaveEnv} style={{ display: 'grid', gap: '12px' }}>
                {envModalData.mode === 'raw' ? (
                  <textarea
                    rows={14}
                    style={{ fontFamily: 'monospace', fontSize: '12.5px', width: '100%' }}
                    value={envModalData.raw}
                    onChange={e => setEnvModalData(prev => ({ ...prev, raw: e.target.value }))}
                  />
                ) : (
                  <div
                    style={{
                      display: 'grid',
                      gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))',
                      gap: '10px',
                    }}
                  >
                    {[
                      ['app_name', 'APP_NAME'],
                      ['node_env', 'NODE_ENV'],
                      ['port', 'PORT'],
                      ['host', 'HOST'],
                      ['app_url', 'APP_URL'],
                      ['tz', 'TZ'],
                      ['jwt_secret', 'JWT_SECRET'],
                      ['log_level', 'LOG_LEVEL'],
                      ['db_host', 'DB_HOST'],
                      ['db_port', 'DB_PORT'],
                      ['db_name', 'DB_DATABASE'],
                      ['db_user', 'DB_USERNAME'],
                      ['db_password', 'DB_PASSWORD'],
                    ].map(([fieldKey, label]) => (
                      <label key={fieldKey} style={{ display: 'grid', gap: '4px', fontSize: '12px' }}>
                        <span style={{ fontFamily: 'monospace', fontWeight: 600 }}>{label}</span>
                        <input
                          type={fieldKey.includes('password') ? 'password' : 'text'}
                          value={envModalData.parsed?.[fieldKey] || ''}
                          onChange={e =>
                            setEnvModalData(prev => ({
                              ...prev,
                              parsed: { ...(prev.parsed || {}), [fieldKey]: e.target.value },
                            }))
                          }
                        />
                      </label>
                    ))}
                  </div>
                )}

                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginTop: '6px' }}>
                  <label style={{ display: 'inline-flex', alignItems: 'center', gap: '6px', fontSize: '12.5px' }}>
                    <input
                      type="checkbox"
                      checked={envModalData.restartAfterSave}
                      onChange={e =>
                        setEnvModalData(prev => ({ ...prev, restartAfterSave: e.target.checked }))
                      }
                    />
                    Tự động khởi động lại tiến trình Node.js sau khi lưu
                  </label>

                  <div style={{ display: 'flex', gap: '8px' }}>
                    <button
                      type="button"
                      className="btn-secondary"
                      onClick={() => setEnvModalInstance(null)}
                    >
                      Đóng
                    </button>
                    <button type="submit" className="btn-primary" disabled={busyMap.save_env}>
                      <Save size={14} />
                      {busyMap.save_env ? 'Đang lưu...' : 'Lưu cấu hình .env'}
                    </button>
                  </div>
                </div>
              </form>
            )}
          </div>
        </div>
      )}

      {/* MODAL 4: Logs Viewer */}
      {logsModalInstance && (
        <div className="modal-backdrop" onClick={() => setLogsModalInstance(null)}>
          <div
            className="modal-content"
            style={{ maxWidth: '760px', width: '95%' }}
            onClick={e => e.stopPropagation()}
          >
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '12px' }}>
              <h3 style={{ margin: 0 }}>
                Nhật ký vận hành Node.js — {logsModalInstance.domain}
              </h3>
              <button
                type="button"
                className="btn-secondary"
                onClick={() => setLogsModalInstance(null)}
              >
                <X size={15} />
              </button>
            </div>

            <div style={{ display: 'flex', flexWrap: 'wrap', justifyContent: 'space-between', gap: '8px', marginBottom: '10px' }}>
              <div style={{ display: 'flex', gap: '6px' }}>
                {[
                  { id: 'systemd', label: 'Systemd Journal' },
                  { id: 'nginx_error', label: 'Nginx Error Log' },
                  { id: 'app', label: 'NPM / App Log' },
                ].map(k => (
                  <button
                    key={k.id}
                    type="button"
                    className={logsModalData.kind === k.id ? 'btn-primary' : 'btn-secondary'}
                    style={{ fontSize: '12px', padding: '5px 10px' }}
                    onClick={() => openLogsModal(logsModalInstance, k.id, logsModalData.lines)}
                  >
                    {k.label}
                  </button>
                ))}
              </div>

              <div style={{ display: 'flex', gap: '6px' }}>
                <button
                  type="button"
                  className="btn-secondary"
                  style={{ fontSize: '12px', padding: '5px 10px' }}
                  onClick={() =>
                    openLogsModal(logsModalInstance, logsModalData.kind, logsModalData.lines)
                  }
                >
                  <RefreshCw size={12} />
                  Làm mới
                </button>
                <button
                  type="button"
                  className="btn-secondary"
                  style={{ fontSize: '12px', padding: '5px 10px' }}
                  onClick={() => copyText(logsModalData.content, 'Đã sao chép nội dung nhật ký.')}
                >
                  <Copy size={12} />
                  Sao chép
                </button>
                <button
                  type="button"
                  className="btn-secondary"
                  style={{ fontSize: '12px', padding: '5px 10px' }}
                  disabled={busyMap.clear_logs}
                  onClick={handleClearLogs}
                >
                  <Trash2 size={12} />
                  Xóa Log
                </button>
              </div>
            </div>

            <div
              style={{
                background: '#0f172a',
                color: '#e2e8f0',
                borderRadius: '10px',
                padding: '14px',
                fontFamily: 'monospace',
                fontSize: '12px',
                maxHeight: '400px',
                overflowY: 'auto',
              }}
            >
              <pre style={{ margin: 0, whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
                {logsModalData.loading ? 'Đang tải nhật ký...' : logsModalData.content}
              </pre>
            </div>
          </div>
        </div>
      )}

      {/* MODAL 5: Node.js Runtime Versions Manager */}
      {showRuntimeModal && (
        <div className="modal-backdrop" onClick={() => setShowRuntimeModal(false)}>
          <div
            className="modal-content"
            style={{ maxWidth: '540px', width: '95%' }}
            onClick={e => e.stopPropagation()}
          >
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '12px' }}>
              <h3 style={{ margin: 0 }}>Quản lý Phiên bản Node.js Runtime</h3>
              <button
                type="button"
                className="btn-secondary"
                onClick={() => setShowRuntimeModal(false)}
              >
                <X size={15} />
              </button>
            </div>
            <p className="hint" style={{ fontSize: '12.5px', marginBottom: '12px' }}>
              Các phiên bản Node.js được cài đặt độc lập tại <code>/opt/spanel/node/&lt;major&gt;</code>, cho phép mỗi website chạy một phiên bản Node.js riêng biệt.
            </p>
            <div style={{ display: 'grid', gap: '8px' }}>
              {nodeVersions.map(ver => {
                const isInstalled = installedNodeVersions.includes(ver);
                const isBusy = busyMap[`node_ver_${ver}`];
                return (
                  <div
                    key={ver}
                    style={{
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'space-between',
                      padding: '10px 14px',
                      borderRadius: '10px',
                      border: '1px solid var(--border-subtle)',
                    }}
                  >
                    <div>
                      <strong>Node.js v{ver}.x</strong>{' '}
                      <span className="pill" style={{ fontSize: '11px' }}>
                        {ver === '24' ? 'Current' : 'LTS'}
                      </span>
                      <div className="hint" style={{ fontSize: '11.5px' }}>
                        /opt/spanel/node/{ver}/bin/node
                      </div>
                    </div>
                    <button
                      type="button"
                      className={isInstalled ? 'btn-secondary' : 'btn-primary'}
                      style={{ fontSize: '12px' }}
                      disabled={isBusy}
                      onClick={() => handleInstallNodeMajor(ver)}
                    >
                      <Download size={13} />
                      {isBusy
                        ? `Đang tải v${ver}...`
                        : isInstalled
                        ? 'Đã cài đặt · Cập nhật lại'
                        : `Cài đặt Node v${ver}`}
                    </button>
                  </div>
                );
              })}
            </div>
          </div>
        </div>
      )}

      {/* MODAL 6: Detach / Uninstall Confirmation */}
      {removeModalInstance && (
        <div className="modal-backdrop" onClick={() => !busyMap.remove_instance && setRemoveModalInstance(null)}>
          <div
            className="modal-content"
            style={{ maxWidth: '520px', width: '95%' }}
            onClick={e => e.stopPropagation()}
          >
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '12px' }}>
              <h3 style={{ margin: 0 }}>Gỡ ứng dụng Node.js — {removeModalInstance.domain}</h3>
              <button
                type="button"
                className="btn-secondary"
                onClick={() => setRemoveModalInstance(null)}
              >
                <X size={15} />
              </button>
            </div>

            <form onSubmit={handleConfirmRemoveOrDetach} style={{ display: 'grid', gap: '12px' }}>
              <div style={{ display: 'flex', gap: '8px' }}>
                <button
                  type="button"
                  className={removeOptions.mode === 'detach' ? 'btn-primary' : 'btn-secondary'}
                  onClick={() => setRemoveOptions(prev => ({ ...prev, mode: 'detach' }))}
                >
                  Chỉ ngắt khỏi danh sách (Giữ nguyên file)
                </button>
                <button
                  type="button"
                  className={removeOptions.mode === 'uninstall' ? 'btn-primary' : 'btn-secondary'}
                  onClick={() => setRemoveOptions(prev => ({ ...prev, mode: 'uninstall' }))}
                >
                  Gỡ cài đặt & Dừng Daemon
                </button>
              </div>

              {removeOptions.mode === 'uninstall' && (
                <div
                  style={{
                    padding: '12px',
                    borderRadius: '10px',
                    border: '1px solid var(--border-subtle)',
                    display: 'grid',
                    gap: '8px',
                    fontSize: '13px',
                  }}
                >
                  <label style={{ display: 'inline-flex', alignItems: 'center', gap: '8px' }}>
                    <input
                      type="checkbox"
                      checked={removeOptions.deleteFiles}
                      onChange={e =>
                        setRemoveOptions(prev => ({ ...prev, deleteFiles: e.target.checked }))
                      }
                    />
                    Xóa toàn bộ tệp mã nguồn và `node_modules` trong thư mục ứng dụng
                  </label>
                  {removeModalInstance.db_name && (
                    <label style={{ display: 'inline-flex', alignItems: 'center', gap: '8px' }}>
                      <input
                        type="checkbox"
                        checked={removeOptions.dropDatabase}
                        onChange={e =>
                          setRemoveOptions(prev => ({ ...prev, dropDatabase: e.target.checked }))
                        }
                      />
                      Xóa cơ sở dữ liệu MySQL đi kèm ({removeModalInstance.db_name})
                    </label>
                  )}
                  {removeOptions.deleteFiles && (
                    <label style={{ display: 'grid', gap: '4px', marginTop: '4px' }}>
                      <span>
                        Nhập lại tên miền <strong>{removeModalInstance.domain}</strong> để xác nhận xóa tệp:
                      </span>
                      <input
                        type="text"
                        required
                        value={removeOptions.confirmDomain}
                        onChange={e =>
                          setRemoveOptions(prev => ({ ...prev, confirmDomain: e.target.value }))
                        }
                      />
                    </label>
                  )}
                </div>
              )}

              <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '8px' }}>
                <button
                  type="button"
                  className="btn-secondary"
                  onClick={() => setRemoveModalInstance(null)}
                >
                  Hủy
                </button>
                <button type="submit" className="btn-primary" disabled={busyMap.remove_instance}>
                  {busyMap.remove_instance ? 'Đang xử lý...' : 'Xác nhận thực hiện'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}

export default NodejsManagerPage;
