import React, { useState, useEffect, useMemo } from 'react';
import {
  AlertTriangle,
  Archive,
  ArchiveRestore,
  Bug,
  Check,
  CheckCircle,
  ChevronDown,
  ChevronUp,
  Clock,
  Code2,
  Copy,
  Database,
  Download,
  ExternalLink,
  Eye,
  FileText,
  FolderOpen,
  Globe,
  Image as ImageIcon,
  KeyRound,
  LayoutGrid,
  List,
  Lock,
  LogIn,
  PackageOpen,
  Pencil,
  Plus,
  RefreshCw,
  RotateCcw,
  Search,
  Server,
  Settings as SettingsIcon,
  Shield,
  ShieldAlert,
  ShieldCheck,
  Sparkles,
  Trash2,
  Unlink,
  Upload,
  Wrench,
  X,
  Zap,
} from 'lucide-react';
import { useAppContext, WordPressIcon } from '../contexts/AppContext.jsx';
import { t } from '../i18n.js';

const DEFAULT_MAINTENANCE_TITLE = 'Website đang được bảo trì & nâng cấp';
const DEFAULT_MAINTENANCE_MESSAGE =
  'Website đang được bảo trì và nâng cấp định kỳ. Vui lòng quay lại sau ít phút!';

const MAINTENANCE_PRESET_OPTIONS = [
  {
    id: 'emerald_ocean',
    label: 'Ngọc lục bảo SHOST',
    gradient: 'radial-gradient(circle at 18% 20%, #059669 0%, #0f172a 55%, #020617 100%)',
    accent: '#10b981',
  },
  {
    id: 'midnight_nebula',
    label: 'Tinh vân Midnight',
    gradient: 'radial-gradient(circle at 80% 18%, #4f46e5 0%, #1e1b4b 52%, #090d16 100%)',
    accent: '#818cf8',
  },
  {
    id: 'warm_sunrise',
    label: 'Bình minh Ấm',
    gradient: 'radial-gradient(circle at 22% 18%, #ea580c 0%, #431407 55%, #0c0a09 100%)',
    accent: '#fb923c',
  },
];

export function WordPressManagerPage({ request, websites = [], openWebsiteFileManager, navigateToPage }) {
  const { loading, setError, setNotice, phpVersions, loadPhpVersions, currentUser, packages } = useAppContext();
  const installedPhpList = phpVersions?.installed?.length ? phpVersions.installed : ['8.4', '8.3', '8.2', '8.1'];

  // Global tabs: 'instances' | 'plugins' | 'themes' | 'sets'
  const [globalTab, setGlobalTab] = useState('instances');
  // View mode for instances: 'expanded' | 'grid' | 'list'
  const [viewMode, setViewMode] = useState('expanded');
  const [searchQuery, setSearchQuery] = useState('');

  const [instances, setInstances] = useState([]);
  const [packageSets, setPackageSets] = useState([]);
  const [allWebsites, setAllWebsites] = useState(websites || []);
  const [quotaInfo, setQuotaInfo] = useState(null);
  const [busyAction, setBusyAction] = useState('');

  // Selection & expansion states
  const [selectedIds, setSelectedIds] = useState([]);
  const [collapsedMap, setCollapsedMap] = useState({});
  // Active sub-tab per instance ID: 'overview' | 'plugins' | 'themes' | 'security' | 'database'
  const [instanceTabMap, setInstanceTabMap] = useState({});
  const [instancePluginsMap, setInstancePluginsMap] = useState({});
  const [instanceThemesMap, setInstanceThemesMap] = useState({});
  const [securityCheckedMap, setSecurityCheckedMap] = useState({});

  // Modals state
  const [showInstallModal, setShowInstallModal] = useState(false);
  const [installForm, setInstallForm] = useState({
    website_id: '',
    subpath: '',
    site_title: 'My WordPress Site',
    site_description: 'Powered by SPanel WordPress Toolkit',
    language: 'vi',
    php_version: installedPhpList[0] || '8.4',
    admin_user: 'wpadmin',
    admin_password: '',
    admin_email: '',
    db_mode: 'auto',
    db_name: '',
    db_user: '',
    db_password: '',
    table_prefix: 'wp_sp_',
    starter_set: 'essential_speed_seo',
  });
  const [installedCredsModal, setInstalledCredsModal] = useState(null);

  // Contextual modals for a specific instance
  const [generalModalInst, setGeneralModalInst] = useState(null);
  const [generalForm, setGeneralForm] = useState({
    site_title: '',
    site_description: '',
    php_version: '8.4',
    admin_user: '',
    admin_email: '',
    admin_password: '',
  });

  const [debugModalInst, setDebugModalInst] = useState(null);
  const [debugLogText, setDebugLogText] = useState('');

  const [maintenanceModalInst, setMaintenanceModalInst] = useState(null);
  const [maintenanceForm, setMaintenanceForm] = useState({
    title: DEFAULT_MAINTENANCE_TITLE,
    message: DEFAULT_MAINTENANCE_MESSAGE,
    bg_url: '',
    bg_preset: 'emerald_ocean',
    bg_overlay: 55,
  });
  const [maintenanceLocalPreviewUrl, setMaintenanceLocalPreviewUrl] = useState('');
  const [uploadingMaintBg, setUploadingMaintBg] = useState(false);

  const [removeModalTargets, setRemoveModalTargets] = useState(null);
  const [removeDropDb, setRemoveDropDb] = useState(true);

  const [checksumResultModal, setChecksumResultModal] = useState(null);

  const [passwordAuthModalInst, setPasswordAuthModalInst] = useState(null);
  const [basicAuthForm, setBasicAuthForm] = useState({ user: 'preview', password: '' });

  const [cloneSyncModal, setCloneSyncModal] = useState(null); // { mode: 'clone' | 'sync', instance }
  const [cloneSyncForm, setCloneSyncForm] = useState({
    target_website_id: '',
    target_subpath: '',
    sync_files: true,
    sync_database: true,
  });

  const [snapshotModalInst, setSnapshotModalInst] = useState(null);
  const [snapshotLabelDraft, setSnapshotLabelDraft] = useState('');

  const [searchReplaceModalInst, setSearchReplaceModalInst] = useState(null);
  const [searchReplaceForm, setSearchReplaceForm] = useState({
    search_from: '',
    replace_to: '',
    dry_run: false,
  });
  const [searchReplaceOutput, setSearchReplaceOutput] = useState('');

  // WordPress.org Plugin / Theme catalog modal
  const [wporgModal, setWporgModal] = useState(null); // { kind: 'plugins' | 'themes', instance }
  const [wporgQuery, setWporgQuery] = useState('');
  const [wporgResults, setWporgResults] = useState([]);
  const [wporgSearching, setWporgSearching] = useState(false);

  // Create / Apply Package Set modals
  const [showCreateSetModal, setShowCreateSetModal] = useState(false);
  const [newSetForm, setNewSetForm] = useState({
    name: '',
    description: '',
    pluginsText: 'classic-editor, wordpress-seo, litespeed-cache',
    themesText: 'astra',
  });
  const [applySetModal, setApplySetModal] = useState(null); // { pkg, websiteIds: [] }

  useEffect(() => {
    if (loadPhpVersions) loadPhpVersions({ silent: true });
    loadToolkitData(false);
  }, []);

  function generateStrongPassword(len = 16) {
    const chars = 'abcdefghijkmnopqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789!@#%^&*_-+=';
    let out = '';
    for (let i = 0; i < len; i += 1) {
      out += chars.charAt(Math.floor(Math.random() * chars.length));
    }
    return out;
  }

  async function loadToolkitData(silent = true) {
    if (!silent) setBusyAction('loading');
    try {
      const data = await request('/wordpress-manager/instances', silent ? { silent: true } : {});
      if (data) {
        const list = Array.isArray(data.instances) ? data.instances : [];
        setInstances(list);
        const nextPlugins = {};
        const nextThemes = {};
        for (const inst of list) {
          if (Array.isArray(inst.plugins)) nextPlugins[inst.id] = inst.plugins;
          if (Array.isArray(inst.themes)) nextThemes[inst.id] = inst.themes;
        }
        setInstancePluginsMap(nextPlugins);
        setInstanceThemesMap(nextThemes);
        if (Array.isArray(data.sets)) setPackageSets(data.sets);
        if (data.quota) setQuotaInfo(data.quota);
        if (Array.isArray(data.websites)) {
          setAllWebsites(data.websites);
        }
      }
    } finally {
      if (!silent) setBusyAction('');
    }
  }

  function updateSingleInstanceState(updated) {
    if (!updated || !updated.id) return;
    setInstances(prev => prev.map(item => (item.id === updated.id ? updated : item)));
    if (Array.isArray(updated.plugins)) {
      setInstancePluginsMap(prev => ({ ...prev, [updated.id]: updated.plugins }));
    }
    if (Array.isArray(updated.themes)) {
      setInstanceThemesMap(prev => ({ ...prev, [updated.id]: updated.themes }));
    }
    if (snapshotModalInst && snapshotModalInst.id === updated.id) {
      setSnapshotModalInst(updated);
    }
    if (maintenanceModalInst && maintenanceModalInst.id === updated.id) {
      setMaintenanceModalInst(updated);
    }
    if (debugModalInst && debugModalInst.id === updated.id) {
      setDebugModalInst(updated);
    }
  }

  function getSubTab(instId) {
    return instanceTabMap[instId] || 'overview';
  }

  async function selectSubTab(inst, tabKey) {
    setInstanceTabMap(prev => ({ ...prev, [inst.id]: tabKey }));
    if (tabKey === 'plugins') {
      const res = await request(`/wordpress-manager/instances/${inst.id}/plugins`, { silent: true });
      if (Array.isArray(res?.plugins)) {
        setInstancePluginsMap(prev => ({ ...prev, [inst.id]: res.plugins }));
        setInstances(prev =>
          prev.map(item =>
            item.id === inst.id
              ? {
                  ...item,
                  plugins: res.plugins,
                  plugins_count: res.plugins.length,
                  plugins_active_count: res.plugins.filter(p => p.status === 'active').length,
                  plugins_updatable_count: res.plugins.filter(p => p.update_available).length,
                }
              : item
          )
        );
      }
    }
    if (tabKey === 'themes') {
      const res = await request(`/wordpress-manager/instances/${inst.id}/themes`, { silent: true });
      if (Array.isArray(res?.themes)) {
        setInstanceThemesMap(prev => ({ ...prev, [inst.id]: res.themes }));
        const activeTh = res.themes.find(th => th.status === 'active');
        setInstances(prev =>
          prev.map(item =>
            item.id === inst.id
              ? {
                  ...item,
                  themes: res.themes,
                  themes_count: res.themes.length,
                  themes_updatable_count: res.themes.filter(th => th.update_available).length,
                  active_theme: activeTh ? activeTh.name : item.active_theme,
                }
              : item
          )
        );
      }
    }
    if (tabKey === 'security' && !securityCheckedMap[inst.id]) {
      const allIds = (inst.security_checks || []).map(c => c.id);
      setSecurityCheckedMap(prev => ({ ...prev, [inst.id]: allIds }));
    }
  }

  const installableWebsites = useMemo(() => {
    const installedIds = new Set(instances.map(inst => Number(inst.website_id || inst.id)));
    return (allWebsites || []).filter(w => !w.occupied && !installedIds.has(Number(w.id)));
  }, [allWebsites, instances]);

  const userPkg = useMemo(
    () =>
      (packages || []).find(
        p => p.id === currentUser?.package_id || (currentUser?.package_name && p.name === currentUser.package_name)
      ),
    [packages, currentUser]
  );

  const canInstallWp = useMemo(() => {
    if (quotaInfo && typeof quotaInfo.can_install === 'boolean') {
      return quotaInfo.can_install && installableWebsites.length > 0;
    }
    if (installableWebsites.length === 0) return false;
    if (currentUser?.role === 'admin') return true;
    const wLimit = Number(currentUser?.website_limit ?? userPkg?.website_limit ?? -1);
    const sLimit = Number(currentUser?.subdomain_limit ?? userPkg?.subdomain_limit ?? 0);
    if (wLimit >= 0 && sLimit >= 0) {
      const maxTotal = wLimit + sLimit;
      const occupiedCount = (allWebsites || []).filter(w => w.occupied).length || instances.length;
      if (maxTotal === 0 || occupiedCount >= maxTotal) return false;
    }
    return true;
  }, [quotaInfo, installableWebsites, currentUser, userPkg, allWebsites, instances]);

  function openInstallDialog() {
    if (!canInstallWp) return;
    const firstSite = installableWebsites[0];
    if (!firstSite) return;
    const randSuffix = Math.random().toString(36).substring(2, 6);
    setInstallForm({
      website_id: firstSite ? String(firstSite.id) : '',
      subpath: '',
      site_title: firstSite ? `${firstSite.domain} CMS` : 'My WordPress Site',
      site_description: 'Powered by SPanel WordPress Manager',
      language: 'vi',
      php_version: firstSite?.php_version || installedPhpList[0] || '8.4',
      admin_user: 'wpadmin',
      admin_password: generateStrongPassword(16),
      admin_email: firstSite ? `admin@${firstSite.domain}` : 'admin@example.com',
      db_mode: 'auto',
      db_name: '',
      db_user: '',
      db_password: '',
      table_prefix: `wp_${randSuffix}_`,
      starter_set: 'essential_speed_seo',
    });
    setShowInstallModal(true);
  }

  async function handleInstallSubmit(e) {
    e.preventDefault();
    if (!installForm.website_id) {
      setError('Vui lòng chọn tên miền đích để cài đặt WordPress.');
      return;
    }
    setBusyAction('installing');
    try {
      const res = await request(
        '/wordpress-manager/install',
        {
          method: 'POST',
          body: JSON.stringify({
            ...installForm,
            website_id: Number(installForm.website_id),
          }),
        },
        'Đang tải mã nguồn WordPress, khởi tạo Database và cấu hình bảo mật...'
      );
      if (res) {
        setNotice(res.message || 'Cài đặt WordPress thành công!');
        setShowInstallModal(false);
        if (res.credentials) {
          setInstalledCredsModal({
            domain: res.instance?.domain || '',
            url: res.instance?.url || '',
            website_id: res.instance?.id,
            ...res.credentials,
          });
        }
        await loadToolkitData(true);
      }
    } finally {
      setBusyAction('');
    }
  }

  async function handleScanInstances() {
    setBusyAction('scanning');
    try {
      const res = await request('/wordpress-manager/scan', { method: 'POST' }, 'Đang quét các thư mục website...');
      if (res) {
        setNotice(res.message);
        await loadToolkitData(true);
      }
    } finally {
      setBusyAction('');
    }
  }

  async function handleSsoLogin(inst) {
    setBusyAction(`sso-${inst.id}`);
    try {
      const res = await request(
        `/wordpress-manager/instances/${inst.id}/sso-login`,
        { method: 'POST' },
        `Đang khởi tạo phiên đăng nhập 1-Click wp-admin cho ${inst.domain}...`
      );
      if (res?.sso_url) {
        window.open(res.sso_url, '_blank', 'noopener,noreferrer');
        setNotice(`Đã mở trang quản trị wp-admin cho ${inst.domain} (Token tự hủy sau 1 lần dùng).`);
      }
    } finally {
      setBusyAction('');
    }
  }

  async function handleToggleSwitch(inst, key, nextVal, extra = {}) {
    setBusyAction(`toggle-${inst.id}-${key}`);
    try {
      const res = await request(
        `/wordpress-manager/instances/${inst.id}/toggles`,
        {
          method: 'PUT',
          body: JSON.stringify({ [key]: nextVal, ...extra }),
        },
        'Đang áp dụng thiết lập...'
      );
      if (res?.instance) {
        updateSingleInstanceState(res.instance);
        setNotice(res.message || 'Đã cập nhật thiết lập.');
      }
    } finally {
      setBusyAction('');
    }
  }

  function openGeneralSetupModal(inst) {
    setGeneralModalInst(inst);
    setGeneralForm({
      site_title: inst.site_title || inst.domain,
      site_description: inst.site_description || '',
      php_version: inst.php_version || '8.4',
      admin_user: inst.admin_user || 'admin',
      admin_email: inst.admin_email || `admin@${inst.domain}`,
      admin_password: '',
    });
  }

  async function handleSaveGeneralSetup(e) {
    e.preventDefault();
    if (!generalModalInst) return;
    setBusyAction('saving-general');
    try {
      const payload = {
        site_title: generalForm.site_title,
        site_description: generalForm.site_description,
        php_version: generalForm.php_version,
        admin_user: generalForm.admin_user,
        admin_email: generalForm.admin_email,
      };
      if (generalForm.admin_password.trim()) {
        payload.admin_password = generalForm.admin_password.trim();
      }
      const res = await request(
        `/wordpress-manager/instances/${generalModalInst.id}/general`,
        {
          method: 'PUT',
          body: JSON.stringify(payload),
        },
        'Đang lưu cấu hình WordPress...'
      );
      if (res?.instance) {
        updateSingleInstanceState(res.instance);
        setNotice(res.message);
        setGeneralModalInst(null);
      }
    } finally {
      setBusyAction('');
    }
  }

  async function openDebugModal(inst) {
    setDebugModalInst(inst);
    setDebugLogText('Đang tải wp-content/debug.log...');
    const res = await request(`/wordpress-manager/instances/${inst.id}/debug-log`, { silent: true });
    if (res) {
      setDebugLogText(res.log || 'Không có dữ liệu log.');
    }
  }

  async function handleRefreshDebugLog() {
    if (!debugModalInst) return;
    setDebugLogText('Đang làm mới wp-content/debug.log...');
    const res = await request(`/wordpress-manager/instances/${debugModalInst.id}/debug-log`, { silent: true });
    if (res) {
      setDebugLogText(res.log || 'Không có dữ liệu log.');
    }
  }

  async function handleClearDebugLog() {
    if (!debugModalInst) return;
    const res = await request(`/wordpress-manager/instances/${debugModalInst.id}/debug-log`, {
      method: 'DELETE',
    });
    if (res) {
      setDebugLogText('Không có lỗi nào được ghi nhận trong wp-content/debug.log.');
      setNotice(res.message);
    }
  }

  function openMaintenanceSetupModal(inst) {
    setMaintenanceModalInst(inst);
    setMaintenanceLocalPreviewUrl('');
    setMaintenanceForm({
      title: inst.maintenance_title || DEFAULT_MAINTENANCE_TITLE,
      message: inst.maintenance_message || DEFAULT_MAINTENANCE_MESSAGE,
      bg_url: inst.maintenance_bg_url || '',
      bg_preset: inst.maintenance_bg_preset || 'emerald_ocean',
      bg_overlay: Number.isFinite(Number(inst.maintenance_bg_overlay))
        ? Number(inst.maintenance_bg_overlay)
        : 55,
    });
  }

  async function handleUploadMaintenanceBackground(e) {
    const file = e.target.files?.[0];
    e.target.value = '';
    if (!file || !maintenanceModalInst) return;
    if (file.size > 5 * 1024 * 1024) {
      setError('Ảnh nền bảo trì vượt quá giới hạn tối đa 5MB.');
      return;
    }
    setUploadingMaintBg(true);
    try {
      const formData = new FormData();
      formData.append('file', file);
      const res = await request(
        `/wordpress-manager/instances/${maintenanceModalInst.id}/maintenance-background`,
        {
          method: 'POST',
          body: formData,
        },
        'Đang tải lên và xác thực hình nền trang bảo trì...'
      );
      if (res) {
        try {
          const reader = new FileReader();
          reader.onload = ev => {
            if (typeof ev.target?.result === 'string') {
              setMaintenanceLocalPreviewUrl(ev.target.result);
            }
          };
          reader.readAsDataURL(file);
        } catch {
          setMaintenanceLocalPreviewUrl('');
        }
        if (res.bg_url) {
          setMaintenanceForm(prev => ({ ...prev, bg_url: res.bg_url }));
        }
        if (res.instance) {
          updateSingleInstanceState(res.instance);
        }
        setNotice(res.message || 'Đã tải ảnh nền bảo trì thành công.');
      }
    } finally {
      setUploadingMaintBg(false);
    }
  }

  async function handleRemoveMaintenanceBackground() {
    if (!maintenanceModalInst) return;
    const currentBg = String(maintenanceForm.bg_url || '').trim();
    if (currentBg.includes('/wp-content/maintenance-assets/')) {
      setUploadingMaintBg(true);
      try {
        const res = await request(
          `/wordpress-manager/instances/${maintenanceModalInst.id}/maintenance-background`,
          { method: 'DELETE' },
          'Đang gỡ bỏ hình nền bảo trì...'
        );
        if (res?.instance) {
          updateSingleInstanceState(res.instance);
        }
        if (res?.message) setNotice(res.message);
      } finally {
        setUploadingMaintBg(false);
      }
    }
    setMaintenanceLocalPreviewUrl('');
    setMaintenanceForm(prev => ({ ...prev, bg_url: '' }));
  }

  async function handleSaveMaintenanceSetup(enableNow) {
    if (!maintenanceModalInst) return;
    const payload = {
      maintenance_title: maintenanceForm.title,
      maintenance_message: maintenanceForm.message,
      maintenance_bg_url: maintenanceForm.bg_url,
      maintenance_bg_preset: maintenanceForm.bg_preset,
      maintenance_bg_overlay: Number(maintenanceForm.bg_overlay),
    };
    if (typeof enableNow === 'boolean') {
      payload.maintenance_mode = enableNow;
    }
    setBusyAction(`maint-${maintenanceModalInst.id}`);
    try {
      const res = await request(
        `/wordpress-manager/instances/${maintenanceModalInst.id}/toggles`,
        {
          method: 'PUT',
          body: JSON.stringify(payload),
        },
        enableNow
          ? 'Đang lưu cấu hình và kích hoạt Trang bảo trì...'
          : 'Đang lưu cấu hình giao diện Trang bảo trì...'
      );
      if (res?.instance) {
        updateSingleInstanceState(res.instance);
        setNotice(res.message || 'Đã cập nhật cấu hình Trang bảo trì.');
        setMaintenanceModalInst(null);
      }
    } finally {
      setBusyAction('');
    }
  }

  async function handlePluginAction(inst, action, slug = null, activateAfterInstall = true, skipConfirm = false) {
    if (
      action === 'delete' &&
      !skipConfirm &&
      !window.confirm(`Xác nhận xóa vĩnh viễn Plugin "${slug}" khỏi ${inst.domain}${inst.subpath || ''}?`)
    ) {
      return null;
    }
    setBusyAction(`plugin-${inst.id}-${slug || action}`);
    try {
      const res = await request(
        `/wordpress-manager/instances/${inst.id}/plugins`,
        {
          method: 'POST',
          body: JSON.stringify({
            action,
            slug,
            activate_after_install: activateAfterInstall,
          }),
        },
        'Đang xử lý Plugin...'
      );
      if (res) {
        if (res.plugins) {
          setInstancePluginsMap(prev => ({ ...prev, [inst.id]: res.plugins }));
        }
        if (res.instance) {
          updateSingleInstanceState(res.instance);
        }
        setNotice(res.message);
      }
      return res;
    } finally {
      setBusyAction('');
    }
  }

  async function handleThemeAction(inst, action, slug = null, activateAfterInstall = false, skipConfirm = false) {
    if (
      action === 'delete' &&
      !skipConfirm &&
      !window.confirm(`Xác nhận xóa vĩnh viễn Theme "${slug}" khỏi ${inst.domain}${inst.subpath || ''}?`)
    ) {
      return null;
    }
    setBusyAction(`theme-${inst.id}-${slug || action}`);
    try {
      const res = await request(
        `/wordpress-manager/instances/${inst.id}/themes`,
        {
          method: 'POST',
          body: JSON.stringify({
            action,
            slug,
            activate_after_install: activateAfterInstall,
          }),
        },
        'Đang xử lý giao diện Theme...'
      );
      if (res) {
        if (res.themes) {
          setInstanceThemesMap(prev => ({ ...prev, [inst.id]: res.themes }));
        }
        if (res.instance) {
          updateSingleInstanceState(res.instance);
        }
        setNotice(res.message);
      }
      return res;
    } finally {
      setBusyAction('');
    }
  }

  async function openWporgCatalog(kind, inst) {
    setWporgModal({ kind, instance: inst });
    setWporgQuery('');
    setWporgSearching(true);
    try {
      const res = await request(`/wordpress-manager/wporg/search?kind=${kind}&q=`, { silent: true });
      if (res?.items) setWporgResults(res.items);
    } finally {
      setWporgSearching(false);
    }
  }

  async function searchWporgCatalog(e) {
    if (e) e.preventDefault();
    if (!wporgModal) return;
    setWporgSearching(true);
    try {
      const res = await request(
        `/wordpress-manager/wporg/search?kind=${wporgModal.kind}&q=${encodeURIComponent(wporgQuery)}`,
        { silent: true }
      );
      if (res?.items) setWporgResults(res.items);
    } finally {
      setWporgSearching(false);
    }
  }

  async function handleSecurityAction(inst, action, checkIds = [], skipConfirm = false) {
    if (!skipConfirm) {
      if (
        action === 'reinstall_core' &&
        !window.confirm(
          `Xác nhận tải lại bộ mã nguồn WordPress Core sạch (v${inst.wp_version || 'mới nhất'}) cho ${inst.domain}${inst.subpath || ''}?\n\nThư mục wp-content và Database MySQL được giữ nguyên.`
        )
      ) {
        return null;
      }
      if (
        action === 'revert' &&
        checkIds.length > 1 &&
        !window.confirm(`Xác nhận tắt toàn bộ ${checkIds.length} lớp bảo vệ Hardening trên ${inst.domain}${inst.subpath || ''}?`)
      ) {
        return null;
      }
    }
    setBusyAction(`sec-${inst.id}-${action}`);
    try {
      const res = await request(
        `/wordpress-manager/instances/${inst.id}/security`,
        {
          method: 'POST',
          body: JSON.stringify({
            action,
            check_ids: checkIds,
          }),
        },
        'Đang thực thi bảo mật WordPress...'
      );
      if (res) {
        if (res.instance) updateSingleInstanceState(res.instance);
        if (action === 'verify_checksums') {
          const verifiedOk = res.verified !== false && res.ok !== false;
          setChecksumResultModal({
            instance: inst,
            verified: verifiedOk,
            message: res.message || '',
            output: res.output || '',
          });
          if (!verifiedOk) {
            setError(res.message || 'Phát hiện tệp tin WordPress Core không khớp chữ ký MD5!');
          } else {
            setNotice(res.message || 'Chữ ký MD5 của WordPress Core hợp lệ 100%.');
          }
        } else {
          setNotice(res.message);
        }
      }
      return res;
    } finally {
      setBusyAction('');
    }
  }

  async function handleToolAction(inst, payload) {
    setBusyAction(`tool-${inst.id}-${payload.action}`);
    try {
      const res = await request(
        `/wordpress-manager/instances/${inst.id}/tools`,
        {
          method: 'POST',
          body: JSON.stringify(payload),
        },
        'Đang thực thi công cụ WordPress...'
      );
      if (res) {
        if (res.instance) updateSingleInstanceState(res.instance);
        setNotice(res.message);
        if (payload.action === 'clone' || payload.action === 'sync') {
          setCloneSyncModal(null);
          await loadToolkitData(true);
        }
        if (payload.action === 'search_replace') {
          setSearchReplaceOutput(res.output || res.message || 'Đã hoàn tất truy vấn Search & Replace.');
        }
      }
      return res;
    } finally {
      setBusyAction('');
    }
  }

  async function handleDetachInstance(inst) {
    if (
      !window.confirm(
        `Ngắt liên kết ${inst.domain}${inst.subpath || ''} khỏi WordPress Manager?\n\nLưu ý: Detach chỉ ẩn website khỏi danh sách quản lý — toàn bộ mã nguồn trong public_html và Database vẫn giữ nguyên 100% (có thể bấm Scan để nhận lại). Nếu muốn xóa hẳn mã nguồn & Database, hãy dùng nút Remove.`
      )
    ) {
      return;
    }
    const res = await request(`/wordpress-manager/instances/${inst.id}/detach`, { method: 'POST' }, 'Đang ngắt liên kết...');
    if (res) {
      setSelectedIds(prev => prev.filter(id => id !== inst.id));
      setNotice(res.message);
      await loadToolkitData(true);
    }
  }

  function openRemoveModal(targets) {
    const arr = (Array.isArray(targets) ? targets : [targets]).filter(Boolean);
    if (arr.length === 0) return;
    setRemoveDropDb(true);
    setRemoveModalTargets(arr);
  }

  async function handleConfirmRemoveInstances() {
    if (!removeModalTargets || removeModalTargets.length === 0) return;
    setBusyAction('removing-instances');
    try {
      const removedIds = [];
      let lastMessage = '';
      for (const inst of removeModalTargets) {
        const res = await request(
          `/wordpress-manager/instances/${inst.id}/remove?drop_database=${removeDropDb ? 'true' : 'false'}`,
          { method: 'POST' },
          `Đang xóa vĩnh viễn mã nguồn WordPress${removeDropDb ? ' & Database' : ''} của ${inst.domain}${inst.subpath || ''}...`
        );
        if (res) {
          removedIds.push(inst.id);
          lastMessage = res.message || lastMessage;
        }
      }
      if (removedIds.length > 0) {
        setSelectedIds(prev => prev.filter(id => !removedIds.includes(id)));
        setRemoveModalTargets(null);
        setNotice(
          removedIds.length === 1
            ? lastMessage || 'Đã xóa hoàn toàn cài đặt WordPress.'
            : `Đã xóa hoàn toàn ${removedIds.length} cài đặt WordPress${removeDropDb ? ' cùng Database MySQL đi kèm' : ''}.`
        );
        await loadToolkitData(true);
      }
    } finally {
      setBusyAction('');
    }
  }

  async function handleBulkAction(kind) {
    const targets = selectedIds.length > 0 ? instances.filter(i => selectedIds.includes(i.id)) : instances;
    if (targets.length === 0) return;

    if (kind === 'updates') {
      if (
        !window.confirm(
          `Xác nhận kiểm tra và cập nhật đồng loạt WordPress Core, Plugins và Themes cho ${targets.length} website${selectedIds.length > 0 ? ' đã chọn' : ''}?`
        )
      ) {
        return;
      }
      for (const inst of targets) {
        await handleToolAction(inst, { action: 'update_core' });
        await handlePluginAction(inst, 'update_all', null, true, true);
        await handleThemeAction(inst, 'update_all', null, false, true);
      }
      setNotice(`Đã hoàn tất cập nhật Core, Plugins & Themes cho ${targets.length} website WordPress.`);
    } else if (kind === 'security') {
      if (
        !window.confirm(
          `Xác nhận áp dụng toàn bộ 9 lớp bảo mật Hardening cho ${targets.length} website${selectedIds.length > 0 ? ' đã chọn' : ''}?`
        )
      ) {
        return;
      }
      for (const inst of targets) {
        await handleSecurityAction(inst, 'secure', [], true);
      }
      setNotice(`Đã áp dụng 9 lớp bảo mật Hardening cho ${targets.length} website WordPress.`);
    } else if (kind === 'detach') {
      if (selectedIds.length === 0) {
        setError('Vui lòng tích chọn ít nhất 1 website để Detach.');
        return;
      }
      if (
        !window.confirm(
          `Ngắt liên kết ${targets.length} website đã chọn khỏi WordPress Manager? (Mã nguồn public_html và Database vẫn giữ nguyên 100%)`
        )
      ) {
        return;
      }
      for (const inst of targets) {
        await request(`/wordpress-manager/instances/${inst.id}/detach`, { method: 'POST' }, 'Đang ngắt liên kết...');
      }
      setSelectedIds([]);
      setNotice(`Đã ngắt liên kết ${targets.length} website khỏi WordPress Manager.`);
      await loadToolkitData(true);
    } else if (kind === 'remove') {
      if (selectedIds.length === 0) {
        setError('Vui lòng tích chọn ít nhất 1 website để xóa.');
        return;
      }
      openRemoveModal(targets);
    }
  }

  async function handleCreatePackageSet(e) {
    e.preventDefault();
    const plugins = newSetForm.pluginsText
      .split(',')
      .map(s => s.trim())
      .filter(Boolean);
    const themes = newSetForm.themesText
      .split(',')
      .map(s => s.trim())
      .filter(Boolean);
    const res = await request('/wordpress-manager/sets', {
      method: 'POST',
      body: JSON.stringify({
        name: newSetForm.name,
        description: newSetForm.description,
        plugins,
        themes,
      }),
    });
    if (res?.sets) {
      setPackageSets(res.sets);
      setNotice(res.message);
      setShowCreateSetModal(false);
      setNewSetForm({ name: '', description: '', pluginsText: '', themesText: '' });
    }
  }

  async function handleDeletePackageSet(pkgSet) {
    if (!pkgSet || pkgSet.is_builtin) return;
    if (!window.confirm(`Xác nhận xóa bộ cài mẫu "${pkgSet.name}"?`)) return;
    setBusyAction(`del-set-${pkgSet.id}`);
    try {
      const res = await request(`/wordpress-manager/sets/${pkgSet.id}`, { method: 'DELETE' }, 'Đang xóa bộ cài mẫu...');
      if (res?.sets) {
        setPackageSets(res.sets);
        setNotice(res.message || `Đã xóa bộ cài mẫu "${pkgSet.name}".`);
      }
    } finally {
      setBusyAction('');
    }
  }

  function openApplySetModal(pkgSet) {
    if (instances.length === 0) {
      setError('Chưa có website WordPress nào để áp dụng bộ cài.');
      return;
    }
    const defaultIds = selectedIds.length > 0 ? [...selectedIds] : instances.map(i => i.id);
    setApplySetModal({
      pkg: pkgSet,
      websiteIds: defaultIds,
    });
  }

  async function handleConfirmApplySet() {
    if (!applySetModal || !applySetModal.pkg) return;
    if (!applySetModal.websiteIds || applySetModal.websiteIds.length === 0) {
      setError('Vui lòng chọn ít nhất 1 website WordPress để cài đặt bộ gói này.');
      return;
    }
    setBusyAction(`apply-set-${applySetModal.pkg.id}`);
    try {
      const res = await request(
        '/wordpress-manager/sets/apply',
        {
          method: 'POST',
          body: JSON.stringify({
            set_id: applySetModal.pkg.id,
            website_ids: applySetModal.websiteIds,
          }),
        },
        `Đang cài đặt bộ gói '${applySetModal.pkg.name}' cho ${applySetModal.websiteIds.length} website...`
      );
      if (res) {
        setNotice(res.message);
        setApplySetModal(null);
        await loadToolkitData(true);
      }
    } finally {
      setBusyAction('');
    }
  }

  const filteredInstances = instances.filter(inst => {
    if (!searchQuery.trim()) return true;
    const q = searchQuery.toLowerCase();
    return (
      inst.domain.toLowerCase().includes(q) ||
      (inst.site_title || '').toLowerCase().includes(q) ||
      (inst.wp_version || '').toLowerCase().includes(q)
    );
  });

  const allSelected =
    filteredInstances.length > 0 && filteredInstances.every(inst => selectedIds.includes(inst.id));

  function toggleSelectAll() {
    if (allSelected) {
      setSelectedIds([]);
    } else {
      setSelectedIds(filteredInstances.map(i => i.id));
    }
  }

  function toggleSelectOne(id) {
    setSelectedIds(prev => (prev.includes(id) ? prev.filter(x => x !== id) : [...prev, id]));
  }

  // Reusable Toggle Switch component matching Plesk's clean pill switch + Customize Skin primary color
  function renderSwitch(checked, onChange, disabled = false) {
    return (
      <button
        type="button"
        role="switch"
        className={`sp-switch${checked ? ' is-on' : ''}`}
        aria-checked={checked}
        disabled={disabled}
        onClick={onChange}
        style={{
          position: 'relative',
          display: 'inline-block',
          width: '42px',
          minWidth: '42px',
          height: '24px',
          minHeight: '24px',
          borderRadius: '999px',
          padding: 0,
          margin: 0,
          border: checked ? '1px solid var(--primary)' : '1px solid rgba(15, 23, 42, 0.14)',
          background: checked ? 'var(--primary)' : '#cbd5e1',
          cursor: disabled ? 'not-allowed' : 'pointer',
          transition: 'background-color 0.2s cubic-bezier(0.4, 0, 0.2, 1), border-color 0.2s cubic-bezier(0.4, 0, 0.2, 1)',
          flexShrink: 0,
          verticalAlign: 'middle',
          boxSizing: 'border-box',
          boxShadow: checked ? 'none' : 'inset 0 1px 2px rgba(15, 23, 42, 0.08)',
        }}
      >
        <span
          className="sp-switch-thumb"
          style={{
            position: 'absolute',
            top: '2px',
            left: '2px',
            width: '18px',
            height: '18px',
            borderRadius: '50%',
            background: '#ffffff',
            boxShadow: '0 1px 3px rgba(15, 23, 42, 0.28), 0 1px 1px rgba(15, 23, 42, 0.14)',
            transform: checked ? 'translateX(18px)' : 'translateX(0)',
            transition: 'transform 0.2s cubic-bezier(0.4, 0, 0.2, 1)',
            pointerEvents: 'none',
            display: 'block',
          }}
        />
      </button>
    );
  }

  return (
    <div className="section" style={{ padding: '20px 24px' }}>
      {/* 1. Page Header & Breadcrumb */}
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
            <WordPressIcon size={22} />
          </div>
          <div>
            <h2 style={{ margin: 0, fontSize: '18px', fontWeight: 700, color: 'var(--text)' }}>
              WordPress Manager
            </h2>
            <p className="hint" style={{ margin: '2px 0 0', fontSize: '12.5px' }}>
              Quản lý tập trung toàn bộ vòng đời Website WordPress: Cài đặt 1-Click, Đăng nhập SSO, Plugins, Themes, Clone/Staging & Bảo mật.
            </p>
          </div>
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          <button
            type="button"
            className="secondary-light"
            disabled={!!busyAction}
            onClick={() => loadToolkitData(false)}
            style={{ height: '36px', padding: '0 14px', fontSize: '13px' }}
          >
            <RefreshCw size={14} className={busyAction === 'loading' ? 'spin' : ''} /> Làm mới
          </button>
        </div>
      </div>

      {/* 2. Plesk-Inspired Global Top Tabs + Search Bar */}
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
          {[
            { id: 'instances', label: 'Instances', count: instances.length },
            {
              id: 'plugins',
              label: 'Plugins',
              count: instances.reduce((acc, i) => acc + (i.plugins_count || 0), 0),
            },
            {
              id: 'themes',
              label: 'Themes',
              count: instances.reduce((acc, i) => acc + (i.themes_count || 0), 0),
            },
            { id: 'sets', label: 'Sets', count: packageSets.length },
          ].map(tab => {
            const active = globalTab === tab.id;
            return (
              <button
                key={tab.id}
                type="button"
                onClick={() => setGlobalTab(tab.id)}
                style={{
                  height: '40px',
                  minHeight: '40px',
                  padding: '0 18px',
                  marginBottom: '-1px',
                  borderRadius: '8px 8px 0 0',
                  border: active ? '1px solid var(--border)' : '1px solid transparent',
                  borderBottom: active ? '2px solid var(--primary)' : '1px solid transparent',
                  background: active ? 'var(--surface)' : 'transparent',
                  color: active ? 'var(--primary)' : 'var(--text-secondary)',
                  fontWeight: active ? 600 : 500,
                  fontSize: '13.5px',
                  cursor: 'pointer',
                  display: 'inline-flex',
                  alignItems: 'center',
                  gap: '7px',
                  boxShadow: 'none',
                }}
              >
                <span>{tab.label}</span>
                <span
                  className="badge"
                  style={{
                    fontSize: '11px',
                    padding: '1px 6px',
                    background: active
                      ? 'color-mix(in srgb, var(--primary) 12%, transparent)'
                      : 'var(--surface-alt)',
                    color: active ? 'var(--primary)' : 'var(--text-muted)',
                  }}
                >
                  {tab.count}
                </span>
              </button>
            );
          })}
        </div>

        {/* Search Input */}
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
            placeholder="Tìm kiếm tên miền, tiêu đề site..."
            value={searchQuery}
            onChange={e => setSearchQuery(e.target.value)}
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

      {/* 3. Global Action Toolbar (When on Instances Tab) */}
      {globalTab === 'instances' && (
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
            <div
              onClick={toggleSelectAll}
              style={{
                display: 'inline-flex',
                alignItems: 'center',
                gap: '7px',
                fontSize: '13px',
                cursor: 'pointer',
                marginRight: '4px',
                userSelect: 'none',
              }}
            >
              <input
                type="checkbox"
                checked={allSelected}
                onChange={toggleSelectAll}
                onClick={e => e.stopPropagation()}
                style={{ width: '15px', height: '15px', margin: 0, flexShrink: 0, cursor: 'pointer' }}
              />
              <span style={{ color: 'var(--text-secondary)', fontWeight: 500, whiteSpace: 'nowrap' }}>
                {selectedIds.length > 0 ? `Đã chọn (${selectedIds.length})` : 'Chọn tất cả'}
              </span>
            </div>

            <button
              type="button"
              disabled={!canInstallWp || !!busyAction}
              onClick={openInstallDialog}
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
                opacity: !canInstallWp || !!busyAction ? 0.5 : 1,
                cursor: !canInstallWp || !!busyAction ? 'not-allowed' : 'pointer',
              }}
            >
              <Plus size={14} /> Install
            </button>

            <button
              type="button"
              className="secondary-light"
              disabled={!!busyAction}
              onClick={handleScanInstances}
              style={{ height: '34px', minHeight: '34px', padding: '0 12px', fontSize: '13px' }}
              title="Quét tự động các website WordPress đã tải lên qua FTP hoặc File Manager"
            >
              <Search size={14} /> Scan
            </button>

            <button
              type="button"
              className="secondary-light"
              disabled={!!busyAction || instances.length === 0}
              onClick={() => handleBulkAction('updates')}
              style={{ height: '34px', minHeight: '34px', padding: '0 12px', fontSize: '13px' }}
            >
              <RefreshCw size={13} /> Updates
            </button>

            <button
              type="button"
              className="secondary-light"
              disabled={!!busyAction || instances.length === 0}
              onClick={() => handleBulkAction('security')}
              style={{ height: '34px', minHeight: '34px', padding: '0 12px', fontSize: '13px' }}
            >
              <ShieldCheck size={14} /> Security
            </button>

            <button
              type="button"
              className="secondary-light"
              disabled={!!busyAction || selectedIds.length === 0}
              onClick={() => handleBulkAction('detach')}
              style={{ height: '34px', minHeight: '34px', padding: '0 12px', fontSize: '13px' }}
            >
              <Unlink size={13} /> Detach
            </button>

            <button
              type="button"
              className="secondary-light"
              disabled={!!busyAction || selectedIds.length === 0}
              onClick={() => handleBulkAction('remove')}
              style={{ height: '34px', minHeight: '34px', padding: '0 12px', fontSize: '13px' }}
            >
              <Trash2 size={13} /> Remove
            </button>
          </div>

          {/* Right View Switcher (Expanded Card / Grid Tile / Table List) */}
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
            {[
              { id: 'expanded', title: 'Card View', icon: Server },
              { id: 'grid', title: 'Grid View', icon: LayoutGrid },
              { id: 'list', title: 'List View', icon: List },
            ].map(v => {
              const VIcon = v.icon;
              const active = viewMode === v.id;
              return (
                <button
                  key={v.id}
                  type="button"
                  title={v.title}
                  onClick={() => setViewMode(v.id)}
                  style={{
                    height: '28px',
                    minHeight: '28px',
                    width: '32px',
                    padding: 0,
                    border: 'none',
                    borderRadius: '5px',
                    background: active ? 'var(--primary)' : 'transparent',
                    color: active ? '#fff' : 'var(--text-secondary)',
                    display: 'inline-flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    boxShadow: 'none',
                  }}
                >
                  <VIcon size={14} />
                </button>
              );
            })}
          </div>
        </div>
      )}

      {/* 4A. INSTANCES TAB CONTENT */}
      {globalTab === 'instances' && (
        <>
          {filteredInstances.length === 0 ? (
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
                <WordPressIcon size={28} />
              </div>
              <h3 style={{ margin: '0 0 6px', fontSize: '16px', color: 'var(--text)' }}>
                Chưa có website WordPress nào được quản lý
              </h3>
              <p className="hint" style={{ maxWidth: '500px', margin: '0 auto 18px', fontSize: '13px', lineHeight: 1.55 }}>
                Bấm <strong>Install</strong> để khởi tạo website WordPress mới chỉ với 1 cú nhấp chuột, hoặc bấm{' '}
                <strong>Scan</strong> để hệ thống tự động nhận diện các mã nguồn WordPress đã được upload lên máy chủ.
              </p>
              <div style={{ display: 'inline-flex', gap: '10px' }}>
                <button
                  type="button"
                  disabled={!canInstallWp || !!busyAction}
                  onClick={openInstallDialog}
                  style={{
                    height: '38px',
                    padding: '0 18px',
                    background: 'var(--primary)',
                    color: '#fff',
                    border: 'none',
                    borderRadius: '8px',
                    fontWeight: 600,
                    opacity: !canInstallWp || !!busyAction ? 0.5 : 1,
                    cursor: !canInstallWp || !!busyAction ? 'not-allowed' : 'pointer',
                  }}
                >
                  <Plus size={15} /> Cài đặt WordPress ngay
                </button>
                <button
                  type="button"
                  className="secondary-light"
                  onClick={handleScanInstances}
                  style={{ height: '38px', padding: '0 16px' }}
                >
                  <Search size={15} /> Quét nhận diện (Scan)
                </button>
              </div>
            </div>
          ) : viewMode === 'list' ? (
            /* TABLE LIST VIEW */
            <div
              style={{
                border: '1px solid var(--border)',
                borderRadius: '12px',
                background: 'var(--surface)',
                overflowX: 'auto',
              }}
            >
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '13.5px', textAlign: 'left' }}>
                <thead>
                  <tr style={{ background: 'var(--surface-alt)', borderBottom: '1px solid var(--border)' }}>
                    <th style={{ padding: '11px 16px', width: '36px' }}>
                      <input
                        type="checkbox"
                        checked={allSelected}
                        onChange={toggleSelectAll}
                        style={{ width: '15px', height: '15px', margin: 0, cursor: 'pointer' }}
                      />
                    </th>
                    <th style={{ padding: '11px 14px' }}>Website / Domain</th>
                    <th style={{ padding: '11px 14px' }}>Tiêu đề Site</th>
                    <th style={{ padding: '11px 14px' }}>Phiên bản WP</th>
                    <th style={{ padding: '11px 14px' }}>PHP</th>
                    <th style={{ padding: '11px 14px' }}>SSL</th>
                    <th style={{ padding: '11px 14px' }}>Bảo mật</th>
                    <th style={{ padding: '11px 16px', textAlign: 'right' }}>Thao tác nhanh</th>
                  </tr>
                </thead>
                <tbody>
                  {filteredInstances.map(inst => (
                    <tr key={inst.id} style={{ borderBottom: '1px solid var(--border)' }}>
                      <td style={{ padding: '11px 16px' }}>
                        <input
                          type="checkbox"
                          checked={selectedIds.includes(inst.id)}
                          onChange={() => toggleSelectOne(inst.id)}
                          style={{ width: '15px', height: '15px', margin: 0, cursor: 'pointer' }}
                        />
                      </td>
                      <td style={{ padding: '11px 14px', fontWeight: 600 }}>
                        <a
                          href={inst.url}
                          target="_blank"
                          rel="noopener noreferrer"
                          style={{ color: 'var(--primary)', textDecoration: 'none', display: 'inline-flex', alignItems: 'center', gap: '5px' }}
                        >
                          {inst.domain}{inst.subpath} <ExternalLink size={12} />
                        </a>
                      </td>
                      <td style={{ padding: '11px 14px' }}>{inst.site_title}</td>
                      <td style={{ padding: '11px 14px' }}>
                        <span className="badge ok">v{inst.wp_version}</span>
                      </td>
                      <td style={{ padding: '11px 14px' }}>PHP {inst.php_version}</td>
                      <td style={{ padding: '11px 14px' }}>
                        <span className={`badge ${inst.ssl_enabled ? 'ok' : 'warn'}`}>
                          {inst.ssl_enabled ? 'Enabled' : 'No SSL'}
                        </span>
                      </td>
                      <td style={{ padding: '11px 14px' }}>
                        <span className={`badge ${inst.security_status === 'ok' ? 'ok' : 'warn'}`}>
                          {inst.security_passed}/{inst.security_total} Secured
                        </span>
                      </td>
                      <td style={{ padding: '11px 16px', textAlign: 'right' }}>
                        <div style={{ display: 'inline-flex', gap: '6px' }}>
                          <button
                            type="button"
                            onClick={() => handleSsoLogin(inst)}
                            style={{
                              height: '30px',
                              minHeight: '30px',
                              padding: '0 10px',
                              fontSize: '12px',
                              background: 'var(--primary)',
                              color: '#fff',
                              border: 'none',
                              borderRadius: '6px',
                            }}
                          >
                            <LogIn size={12} /> Log in
                          </button>
                          <button
                            type="button"
                            className="secondary-light"
                            onClick={() => {
                              setViewMode('expanded');
                              setCollapsedMap(prev => ({ ...prev, [inst.id]: false }));
                            }}
                            style={{ height: '30px', minHeight: '30px', padding: '0 10px', fontSize: '12px' }}
                          >
                            Chi tiết
                          </button>
                          <button
                            type="button"
                            className="secondary-light"
                            onClick={() => handleDetachInstance(inst)}
                            title="Ngắt liên kết khỏi khung quản lý (Giữ nguyên mã nguồn & Database)"
                            style={{ height: '30px', minHeight: '30px', padding: '0 8px' }}
                          >
                            <Unlink size={12} />
                          </button>
                          <button
                            type="button"
                            className="secondary-light"
                            onClick={() => openRemoveModal([inst])}
                            title="Xóa cài đặt WordPress (Xóa mã nguồn public_html & Database)"
                            style={{ height: '30px', minHeight: '30px', padding: '0 8px', color: '#ef4444' }}
                          >
                            <Trash2 size={12} />
                          </button>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : viewMode === 'grid' ? (
            /* COMPACT 2-COLUMN CARD GRID VIEW */
            <div
              style={{
                display: 'grid',
                gridTemplateColumns: 'repeat(auto-fill, minmax(420px, 1fr))',
                gap: '16px',
              }}
            >
              {filteredInstances.map(inst => (
                <div
                  key={inst.id}
                  style={{
                    border: '1px solid var(--border)',
                    borderRadius: '12px',
                    background: 'var(--surface)',
                    padding: '18px 20px',
                    display: 'flex',
                    flexDirection: 'column',
                    gap: '12px',
                  }}
                >
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: '10px' }}>
                    <div>
                      <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                        <span
                          style={{
                            width: '8px',
                            height: '8px',
                            borderRadius: '50%',
                            background: inst.toggles?.maintenance_mode ? '#f59e0b' : '#10b981',
                          }}
                        />
                        <strong style={{ fontSize: '15px', color: 'var(--text)' }}>
                          {inst.domain}{inst.subpath}
                        </strong>
                        <span className="badge">WP {inst.wp_version}</span>
                        <span className="badge">PHP {inst.php_version}</span>
                      </div>
                      <div style={{ fontSize: '12.5px', color: 'var(--text-muted)', marginTop: '4px' }}>
                        {inst.site_title} · DB: <code>{inst.db_name}</code>
                      </div>
                    </div>

                    <button
                      type="button"
                      onClick={() => handleSsoLogin(inst)}
                      style={{
                        height: '32px',
                        minHeight: '32px',
                        padding: '0 12px',
                        fontSize: '12.5px',
                        background: 'var(--primary)',
                        color: '#fff',
                        border: 'none',
                        borderRadius: '7px',
                        fontWeight: 600,
                      }}
                    >
                      <LogIn size={13} /> wp-admin
                    </button>
                  </div>

                  <div
                    style={{
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'space-between',
                      paddingTop: '10px',
                      borderTop: '1px solid var(--border)',
                      fontSize: '12.5px',
                      color: 'var(--text-secondary)',
                    }}
                  >
                    <div style={{ display: 'flex', gap: '12px' }}>
                      <span>Plugins: <strong>{inst.plugins_count}</strong></span>
                      <span>Themes: <strong>{inst.themes_count}</strong></span>
                      <span>Security: <strong>{inst.security_passed}/{inst.security_total}</strong></span>
                    </div>
                    <div style={{ display: 'inline-flex', alignItems: 'center', gap: '6px' }}>
                      <button
                        type="button"
                        className="secondary-light"
                        onClick={() => {
                          setViewMode('expanded');
                          setCollapsedMap(prev => ({ ...prev, [inst.id]: false }));
                        }}
                        style={{ height: '28px', minHeight: '28px', padding: '0 10px', fontSize: '12px' }}
                      >
                        Mở Toolkit
                      </button>
                      <button
                        type="button"
                        className="secondary-light"
                        onClick={() => handleDetachInstance(inst)}
                        title="Ngắt liên kết khỏi khung quản lý (Giữ nguyên mã nguồn & Database)"
                        style={{ height: '28px', minHeight: '28px', padding: '0 8px' }}
                      >
                        <Unlink size={12} />
                      </button>
                      <button
                        type="button"
                        className="secondary-light"
                        onClick={() => openRemoveModal([inst])}
                        title="Xóa cài đặt WordPress (Xóa mã nguồn public_html & Database)"
                        style={{ height: '28px', minHeight: '28px', padding: '0 8px', color: '#ef4444' }}
                      >
                        <Trash2 size={12} />
                      </button>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          ) : (
            /* PLESK-STYLE EXPANDED INSTANCE CARDS (DEFAULT) */
            <div style={{ display: 'flex', flexDirection: 'column', gap: '18px' }}>
              {filteredInstances.map(inst => {
                const isCollapsed = !!collapsedMap[inst.id];
                const subTab = getSubTab(inst.id);
                const instPlugins = instancePluginsMap[inst.id] ?? inst.plugins ?? [];
                const instThemes = instanceThemesMap[inst.id] ?? inst.themes ?? [];
                const totalUpdatableCount =
                  (inst.plugins_updatable_count || 0) +
                  (inst.themes_updatable_count || 0) +
                  (inst.update_available ? 1 : 0);

                return (
                  <div
                    key={inst.id}
                    style={{
                      border: '1px solid var(--border)',
                      borderRadius: '12px',
                      background: 'var(--surface)',
                      overflow: 'hidden',
                      boxShadow: '0 2px 8px rgba(15, 23, 42, 0.04)',
                    }}
                  >
                    {/* INSTANCE DARK/PRIMARY HEADER BAR (Plesk Style) */}
                    <div
                      style={{
                        background: 'color-mix(in srgb, var(--primary) 10%, var(--surface-alt))',
                        borderBottom: isCollapsed ? 'none' : '1px solid var(--border)',
                        padding: '12px 18px',
                        display: 'flex',
                        justifyContent: 'space-between',
                        alignItems: 'center',
                        flexWrap: 'wrap',
                        gap: '12px',
                      }}
                    >
                      <div style={{ display: 'flex', alignItems: 'center', gap: '10px', flexWrap: 'wrap' }}>
                        <input
                          type="checkbox"
                          checked={selectedIds.includes(inst.id)}
                          onChange={() => toggleSelectOne(inst.id)}
                          style={{ width: '16px', height: '16px', cursor: 'pointer' }}
                        />
                        <WordPressIcon size={18} style={{ color: 'var(--primary)' }} />
                        <strong style={{ fontSize: '16px', color: 'var(--text)', letterSpacing: '-0.01em' }}>
                          {inst.domain}{inst.subpath}
                        </strong>
                        <a
                          href={inst.url}
                          target="_blank"
                          rel="noopener noreferrer"
                          title="Mở trang chủ website trong tab mới"
                          style={{
                            color: 'var(--primary)',
                            display: 'inline-flex',
                            alignItems: 'center',
                            padding: '4px',
                            borderRadius: '6px',
                          }}
                        >
                          <ExternalLink size={14} />
                        </a>
                        <span className="badge ok" style={{ fontSize: '11.5px' }}>
                          WP {inst.wp_version}
                        </span>
                        <span className="badge" style={{ fontSize: '11.5px' }}>
                          PHP {inst.php_version}
                        </span>
                        {inst.toggles?.maintenance_mode && (
                          <span className="badge warn" style={{ fontSize: '11.5px' }}>
                            Đang bảo trì (Maintenance)
                          </span>
                        )}
                      </div>

                      <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                        <button
                          type="button"
                          onClick={() => handleSsoLogin(inst)}
                          disabled={busyAction === `sso-${inst.id}`}
                          style={{
                            height: '32px',
                            minHeight: '32px',
                            padding: '0 12px',
                            fontSize: '12.5px',
                            fontWeight: 600,
                            background: 'var(--primary)',
                            color: '#fff',
                            border: 'none',
                            borderRadius: '7px',
                          }}
                          title="Đăng nhập tự động 1-Click vào wp-admin"
                        >
                          <LogIn size={13} /> Log in wp-admin
                        </button>

                        <button
                          type="button"
                          className="secondary-light"
                          onClick={() => handleDetachInstance(inst)}
                          title="Ngắt liên kết khỏi khung quản lý (Giữ nguyên mã nguồn & Database)"
                          style={{ height: '32px', minHeight: '32px', padding: '0 9px' }}
                        >
                          <Unlink size={13} />
                        </button>

                        <button
                          type="button"
                          className="secondary-light"
                          onClick={() => openRemoveModal([inst])}
                          title="Xóa cài đặt WordPress (Xóa mã nguồn public_html & Database)"
                          style={{ height: '32px', minHeight: '32px', padding: '0 9px', color: '#ef4444' }}
                        >
                          <Trash2 size={13} />
                        </button>

                        <button
                          type="button"
                          className="secondary-light"
                          onClick={() =>
                            setCollapsedMap(prev => ({ ...prev, [inst.id]: !prev[inst.id] }))
                          }
                          title={isCollapsed ? 'Mở rộng' : 'Thu gọn'}
                          style={{ height: '32px', minHeight: '32px', padding: '0 9px' }}
                        >
                          {isCollapsed ? <ChevronDown size={15} /> : <ChevronUp size={15} />}
                        </button>
                      </div>
                    </div>

                    {/* INSTANCE BODY (Two Columns: Left Status Card + Right Sub-Tabs) */}
                    {!isCollapsed && (
                      <div
                        style={{
                          display: 'grid',
                          gridTemplateColumns: '275px minmax(0, 1fr)',
                          alignItems: 'stretch',
                        }}
                      >
                        {/* LEFT COLUMN: Site Visual Card & Health Summary (Improved Plesk Left Sidebar) */}
                        <div
                          style={{
                            borderRight: '1px solid var(--border)',
                            padding: '18px',
                            background: 'var(--surface-alt)',
                            display: 'flex',
                            flexDirection: 'column',
                            justifyContent: 'space-between',
                            gap: '16px',
                          }}
                        >
                          <div>
                            {/* Stylized Site Preview Card */}
                            <div
                              style={{
                                borderRadius: '10px',
                                border: '1px solid var(--border)',
                                background: 'var(--surface)',
                                overflow: 'hidden',
                                marginBottom: '16px',
                              }}
                            >
                              <div
                                style={{
                                  padding: '7px 10px',
                                  borderBottom: '1px solid var(--border)',
                                  background: 'var(--surface-alt)',
                                  display: 'flex',
                                  alignItems: 'center',
                                  gap: '5px',
                                }}
                              >
                                <span style={{ width: '7px', height: '7px', borderRadius: '50%', background: '#ef4444' }} />
                                <span style={{ width: '7px', height: '7px', borderRadius: '50%', background: '#f59e0b' }} />
                                <span style={{ width: '7px', height: '7px', borderRadius: '50%', background: '#10b981' }} />
                                <span
                                  style={{
                                    marginLeft: '6px',
                                    fontSize: '11px',
                                    color: 'var(--text-muted)',
                                    overflow: 'hidden',
                                    textOverflow: 'ellipsis',
                                    whiteSpace: 'nowrap',
                                  }}
                                >
                                  {inst.url}
                                </span>
                              </div>
                              <div
                                style={{
                                  padding: '18px 14px',
                                  background:
                                    'linear-gradient(135deg, color-mix(in srgb, var(--primary) 14%, var(--surface)), var(--surface))',
                                  textAlign: 'center',
                                }}
                              >
                                <div
                                  style={{
                                    width: '42px',
                                    height: '42px',
                                    borderRadius: '10px',
                                    background: 'var(--surface)',
                                    border: '1px solid var(--border)',
                                    color: 'var(--primary)',
                                    display: 'flex',
                                    alignItems: 'center',
                                    justifyContent: 'center',
                                    margin: '0 auto 10px',
                                  }}
                                >
                                  <WordPressIcon size={22} />
                                </div>
                                <strong
                                  style={{
                                    display: 'block',
                                    fontSize: '14px',
                                    color: 'var(--text)',
                                    marginBottom: '3px',
                                  }}
                                >
                                  {inst.site_title}
                                </strong>
                                <span style={{ fontSize: '11.5px', color: 'var(--text-muted)' }}>
                                  Theme: {inst.active_theme}
                                </span>
                              </div>
                            </div>

                            {/* Status Indicators Table (Matching Plesk's SSL/TLS, Security status, Updates) */}
                            <div style={{ display: 'flex', flexDirection: 'column', gap: '10px', fontSize: '13px' }}>
                              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                                <span style={{ color: 'var(--text-secondary)' }}>SSL/TLS</span>
                                <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                                  <span className={`badge ${inst.ssl_enabled ? 'ok' : 'warn'}`}>
                                    {inst.ssl_enabled ? 'enabled' : 'not enabled'}
                                  </span>
                                  {!inst.ssl_enabled && navigateToPage && (
                                    <button
                                      type="button"
                                      onClick={() => navigateToPage('ssl')}
                                      style={{
                                        border: 'none',
                                        background: 'transparent',
                                        color: 'var(--primary)',
                                        fontSize: '12px',
                                        padding: 0,
                                        cursor: 'pointer',
                                        boxShadow: 'none',
                                      }}
                                    >
                                      Setup
                                    </button>
                                  )}
                                </div>
                              </div>

                              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                                <span style={{ color: 'var(--text-secondary)' }}>Security status</span>
                                <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                                  <span
                                    className={`badge ${
                                      inst.security_status === 'ok'
                                        ? 'ok'
                                        : inst.security_status === 'warning'
                                          ? 'warn'
                                          : 'bad'
                                    }`}
                                  >
                                    {inst.security_status === 'ok'
                                      ? 'secured'
                                      : `${inst.security_passed}/${inst.security_total}`}
                                  </span>
                                  <button
                                    type="button"
                                    onClick={() => selectSubTab(inst, 'security')}
                                    style={{
                                      border: 'none',
                                      background: 'transparent',
                                      color: 'var(--primary)',
                                      fontSize: '12px',
                                      padding: 0,
                                      cursor: 'pointer',
                                      boxShadow: 'none',
                                    }}
                                  >
                                    View
                                  </button>
                                </div>
                              </div>

                              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                                <span style={{ color: 'var(--text-secondary)' }}>Updates</span>
                                <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                                  <span
                                    className={`badge ${totalUpdatableCount > 0 ? 'warn' : 'ok'}`}
                                  >
                                    {totalUpdatableCount > 0
                                      ? `${totalUpdatableCount} available`
                                      : 'installed'}
                                  </span>
                                  <button
                                    type="button"
                                    onClick={() =>
                                      selectSubTab(
                                        inst,
                                        (inst.plugins_updatable_count || 0) === 0 && (inst.themes_updatable_count || 0) > 0
                                          ? 'themes'
                                          : 'plugins'
                                      )
                                    }
                                    style={{
                                      border: 'none',
                                      background: 'transparent',
                                      color: 'var(--primary)',
                                      fontSize: '12px',
                                      padding: 0,
                                      cursor: 'pointer',
                                      boxShadow: 'none',
                                    }}
                                  >
                                    View
                                  </button>
                                </div>
                              </div>

                              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                                <span style={{ color: 'var(--text-secondary)' }}>Memory Limit</span>
                                <code>{inst.wp_memory_limit}</code>
                              </div>
                            </div>
                          </div>

                          <div
                            style={{
                              paddingTop: '12px',
                              borderTop: '1px solid var(--border)',
                              fontSize: '11.5px',
                              color: 'var(--text-muted)',
                            }}
                          >
                            Path: <code style={{ fontSize: '11px' }}>{inst.public_path}</code>
                          </div>
                        </div>

                        {/* RIGHT COLUMN: Sub-Tabs (Information & Tools | Plugins | Themes | Security | Database) */}
                        <div style={{ padding: '18px 22px', display: 'flex', flexDirection: 'column', gap: '16px' }}>
                          {/* Instance Sub-Tab Bar */}
                          <div
                            style={{
                              display: 'flex',
                              alignItems: 'center',
                              gap: '4px',
                              borderBottom: '1px solid var(--border)',
                              flexWrap: 'wrap',
                            }}
                          >
                            {[
                              { id: 'overview', label: 'Information & Tools' },
                              {
                                id: 'plugins',
                                label: `Plugins (${inst.plugins_count})`,
                                warn: inst.plugins_updatable_count > 0,
                              },
                              { id: 'themes', label: `Themes (${inst.themes_count})` },
                              {
                                id: 'security',
                                label: `Security (${inst.security_passed}/${inst.security_total})`,
                              },
                              { id: 'database', label: 'Database & Config' },
                            ].map(st => {
                              const active = subTab === st.id;
                              return (
                                <button
                                  key={st.id}
                                  type="button"
                                  onClick={() => selectSubTab(inst, st.id)}
                                  style={{
                                    height: '36px',
                                    minHeight: '36px',
                                    padding: '0 14px',
                                    marginBottom: '-1px',
                                    borderRadius: '7px 7px 0 0',
                                    border: active ? '1px solid var(--border)' : '1px solid transparent',
                                    borderBottom: active ? '2px solid var(--primary)' : '1px solid transparent',
                                    background: active ? 'var(--surface)' : 'transparent',
                                    color: active ? 'var(--primary)' : 'var(--text-secondary)',
                                    fontWeight: active ? 600 : 500,
                                    fontSize: '13px',
                                    cursor: 'pointer',
                                    boxShadow: 'none',
                                  }}
                                >
                                  {st.label}
                                </button>
                              );
                            })}
                          </div>

                          {/* SUB-TAB 1: INFORMATION & TOOLS (Plesk Layout) */}
                          {subTab === 'overview' && (
                            <div style={{ display: 'flex', flexDirection: 'column', gap: '18px' }}>
                              {/* General Info Section */}
                              <div>
                                <h4
                                  style={{
                                    margin: '0 0 10px',
                                    fontSize: '14px',
                                    fontWeight: 600,
                                    color: 'var(--text-secondary)',
                                  }}
                                >
                                  General Info
                                </h4>
                                <div
                                  style={{
                                    display: 'grid',
                                    gridTemplateColumns: '160px minmax(0, 1fr)',
                                    rowGap: '8px',
                                    fontSize: '13.5px',
                                    alignItems: 'center',
                                  }}
                                >
                                  <span style={{ color: 'var(--text-muted)' }}>Site title</span>
                                  <div>
                                    <strong style={{ color: 'var(--text)', marginRight: '8px' }}>
                                      {inst.site_title}
                                    </strong>
                                    <button
                                      type="button"
                                      onClick={() => openGeneralSetupModal(inst)}
                                      style={{
                                        border: 'none',
                                        background: 'transparent',
                                        color: 'var(--primary)',
                                        fontSize: '12.5px',
                                        padding: 0,
                                        cursor: 'pointer',
                                        boxShadow: 'none',
                                      }}
                                    >
                                      [Change]
                                    </button>
                                  </div>

                                  <span style={{ color: 'var(--text-muted)' }}>Current version</span>
                                  <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                                    <strong>{inst.wp_version}</strong>
                                    <span style={{ color: 'var(--text-muted)', fontSize: '12px' }}>
                                      (PHP {inst.php_version})
                                    </span>
                                    <button
                                      type="button"
                                      onClick={() => handleToolAction(inst, { action: 'update_core' })}
                                      style={{
                                        border: 'none',
                                        background: 'transparent',
                                        color: 'var(--primary)',
                                        fontSize: '12.5px',
                                        padding: 0,
                                        cursor: 'pointer',
                                        boxShadow: 'none',
                                      }}
                                    >
                                      [Check / Re-sync Core]
                                    </button>
                                  </div>

                                  <span style={{ color: 'var(--text-muted)' }}>Administrator</span>
                                  <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                                    <span style={{ fontWeight: 500 }}>{inst.admin_user}</span>
                                    <button
                                      type="button"
                                      onClick={() => handleSsoLogin(inst)}
                                      style={{
                                        border: 'none',
                                        background: 'transparent',
                                        color: 'var(--primary)',
                                        fontWeight: 600,
                                        fontSize: '13px',
                                        padding: 0,
                                        cursor: 'pointer',
                                        boxShadow: 'none',
                                      }}
                                    >
                                      Log in
                                    </button>
                                    <button
                                      type="button"
                                      onClick={() => openGeneralSetupModal(inst)}
                                      style={{
                                        border: 'none',
                                        background: 'transparent',
                                        color: 'var(--primary)',
                                        fontSize: '12.5px',
                                        padding: 0,
                                        cursor: 'pointer',
                                        boxShadow: 'none',
                                      }}
                                    >
                                      [Setup]
                                    </button>
                                  </div>
                                </div>
                              </div>

                              {/* Tools Action Strip (Sync, Clone, Manage Files, Back Up / Restore, Search & Replace) */}
                              <div>
                                <h4
                                  style={{
                                    margin: '0 0 10px',
                                    fontSize: '14px',
                                    fontWeight: 600,
                                    color: 'var(--text-secondary)',
                                  }}
                                >
                                  Tools
                                </h4>
                                <div
                                  style={{
                                    display: 'flex',
                                    alignItems: 'center',
                                    gap: '8px',
                                    flexWrap: 'wrap',
                                    paddingBottom: '12px',
                                    borderBottom: '1px solid var(--border)',
                                  }}
                                >
                                  <button
                                    type="button"
                                    className="secondary-light"
                                    onClick={() => {
                                      const otherSite = allWebsites.find(w => w.id !== inst.id) || allWebsites[0];
                                      setCloneSyncModal({ mode: 'sync', instance: inst });
                                      setCloneSyncForm({
                                        target_website_id: otherSite ? String(otherSite.id) : '',
                                        target_subpath: '',
                                        sync_files: true,
                                        sync_database: true,
                                      });
                                    }}
                                    style={{ height: '32px', minHeight: '32px', padding: '0 12px', fontSize: '12.5px' }}
                                  >
                                    <RefreshCw size={13} /> Sync
                                  </button>

                                  <button
                                    type="button"
                                    className="secondary-light"
                                    onClick={() => {
                                      const otherSite = allWebsites.find(w => w.id !== inst.id) || allWebsites[0];
                                      setCloneSyncModal({ mode: 'clone', instance: inst });
                                      setCloneSyncForm({
                                        target_website_id: otherSite ? String(otherSite.id) : '',
                                        target_subpath: '',
                                        sync_files: true,
                                        sync_database: true,
                                      });
                                    }}
                                    style={{ height: '32px', minHeight: '32px', padding: '0 12px', fontSize: '12.5px' }}
                                  >
                                    <Copy size={13} /> Clone
                                  </button>

                                  <button
                                    type="button"
                                    className="secondary-light"
                                    onClick={() => {
                                      if (openWebsiteFileManager) {
                                        openWebsiteFileManager({ id: inst.website_id, domain: inst.domain });
                                      } else if (navigateToPage) {
                                        navigateToPage('files');
                                      }
                                    }}
                                    style={{ height: '32px', minHeight: '32px', padding: '0 12px', fontSize: '12.5px' }}
                                  >
                                    <FolderOpen size={13} /> Manage Files
                                  </button>

                                  <button
                                    type="button"
                                    className="secondary-light"
                                    onClick={() => {
                                      setSnapshotModalInst(inst);
                                      setSnapshotLabelDraft(`Before Update (${new Date().toLocaleDateString()})`);
                                    }}
                                    style={{ height: '32px', minHeight: '32px', padding: '0 12px', fontSize: '12.5px' }}
                                  >
                                    <Archive size={13} /> Back Up / Restore ({inst.snapshots?.length || 0})
                                  </button>

                                  <button
                                    type="button"
                                    className="secondary-light"
                                    onClick={() => {
                                      setSearchReplaceModalInst(inst);
                                      setSearchReplaceOutput('');
                                      setSearchReplaceForm({
                                        search_from: `http://${inst.domain}`,
                                        replace_to: `https://${inst.domain}`,
                                        dry_run: false,
                                      });
                                    }}
                                    style={{ height: '32px', minHeight: '32px', padding: '0 12px', fontSize: '12.5px' }}
                                  >
                                    <Search size={13} /> Search & Replace URL
                                  </button>

                                  <button
                                    type="button"
                                    className="secondary-light"
                                    onClick={() => handleToolAction(inst, { action: 'clear_cache' })}
                                    style={{ height: '32px', minHeight: '32px', padding: '0 12px', fontSize: '12.5px' }}
                                  >
                                    <Zap size={13} /> Clear Cache
                                  </button>
                                </div>
                              </div>

                              {/* 6 Smart Toggle Cards Grid (Plesk Style + Disable wp-cron) */}
                              <div
                                style={{
                                  display: 'grid',
                                  gridTemplateColumns: 'repeat(auto-fill, minmax(225px, 1fr))',
                                  gap: '12px',
                                }}
                              >
                                {/* 1. Search engine indexing */}
                                <div
                                  style={{
                                    border: '1px solid var(--border)',
                                    borderRadius: '10px',
                                    padding: '12px 14px',
                                    background: 'var(--surface)',
                                    display: 'flex',
                                    flexDirection: 'column',
                                    gap: '10px',
                                  }}
                                >
                                  <span style={{ fontSize: '13px', fontWeight: 500, color: 'var(--text)' }}>
                                    Search engine indexing
                                  </span>
                                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                                    {renderSwitch(
                                      !!inst.toggles?.search_engine_indexing,
                                      () =>
                                        handleToggleSwitch(
                                          inst,
                                          'search_engine_indexing',
                                          !inst.toggles?.search_engine_indexing
                                        )
                                    )}
                                    <span style={{ fontSize: '11.5px', color: 'var(--text-muted)' }}>
                                      {inst.toggles?.search_engine_indexing ? 'Cho phép Google' : 'Chặn lập chỉ mục'}
                                    </span>
                                  </div>
                                </div>

                                {/* 2. Caching (nginx / WP_CACHE) */}
                                <div
                                  style={{
                                    border: '1px solid var(--border)',
                                    borderRadius: '10px',
                                    padding: '12px 14px',
                                    background: 'var(--surface)',
                                    display: 'flex',
                                    flexDirection: 'column',
                                    gap: '10px',
                                  }}
                                >
                                  <span style={{ fontSize: '13px', fontWeight: 500, color: 'var(--text)' }}>
                                    Caching (nginx / WP)
                                  </span>
                                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                                    {renderSwitch(
                                      !!inst.toggles?.caching_enabled,
                                      () =>
                                        handleToggleSwitch(
                                          inst,
                                          'caching_enabled',
                                          !inst.toggles?.caching_enabled
                                        )
                                    )}
                                    <button
                                      type="button"
                                      onClick={() => handleToolAction(inst, { action: 'clear_cache' })}
                                      style={{
                                        border: 'none',
                                        background: 'transparent',
                                        color: 'var(--primary)',
                                        fontSize: '12px',
                                        padding: 0,
                                        cursor: 'pointer',
                                        boxShadow: 'none',
                                      }}
                                    >
                                      Clear cache
                                    </button>
                                  </div>
                                </div>

                                {/* 3. Maintenance mode */}
                                <div
                                  style={{
                                    border: '1px solid var(--border)',
                                    borderRadius: '10px',
                                    padding: '12px 14px',
                                    background: 'var(--surface)',
                                    display: 'flex',
                                    flexDirection: 'column',
                                    gap: '10px',
                                  }}
                                >
                                  <span style={{ fontSize: '13px', fontWeight: 500, color: 'var(--text)' }}>
                                    Maintenance mode
                                  </span>
                                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                                    {renderSwitch(
                                      !!inst.toggles?.maintenance_mode,
                                      () =>
                                        handleToggleSwitch(
                                          inst,
                                          'maintenance_mode',
                                          !inst.toggles?.maintenance_mode
                                        )
                                    )}
                                    <button
                                      type="button"
                                      onClick={() => openMaintenanceSetupModal(inst)}
                                      style={{
                                        border: 'none',
                                        background: 'transparent',
                                        color: 'var(--primary)',
                                        fontSize: '12px',
                                        padding: 0,
                                        cursor: 'pointer',
                                        boxShadow: 'none',
                                      }}
                                    >
                                      Setup
                                    </button>
                                  </div>
                                </div>

                                {/* 4. Debugging */}
                                <div
                                  style={{
                                    border: '1px solid var(--border)',
                                    borderRadius: '10px',
                                    padding: '12px 14px',
                                    background: 'var(--surface)',
                                    display: 'flex',
                                    flexDirection: 'column',
                                    gap: '10px',
                                  }}
                                >
                                  <span style={{ fontSize: '13px', fontWeight: 500, color: 'var(--text)' }}>
                                    Debugging (WP_DEBUG)
                                  </span>
                                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                                    {renderSwitch(
                                      !!inst.toggles?.debugging,
                                      () => handleToggleSwitch(inst, 'debugging', !inst.toggles?.debugging)
                                    )}
                                    <button
                                      type="button"
                                      onClick={() => openDebugModal(inst)}
                                      style={{
                                        border: 'none',
                                        background: 'transparent',
                                        color: 'var(--primary)',
                                        fontSize: '12px',
                                        padding: 0,
                                        cursor: 'pointer',
                                        boxShadow: 'none',
                                      }}
                                    >
                                      Setup / Log
                                    </button>
                                  </div>
                                </div>

                                {/* 5. Password protection */}
                                <div
                                  style={{
                                    border: '1px solid var(--border)',
                                    borderRadius: '10px',
                                    padding: '12px 14px',
                                    background: 'var(--surface)',
                                    display: 'flex',
                                    flexDirection: 'column',
                                    gap: '10px',
                                  }}
                                >
                                  <span style={{ fontSize: '13px', fontWeight: 500, color: 'var(--text)' }}>
                                    Password protection
                                  </span>
                                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                                    {renderSwitch(
                                      !!inst.toggles?.password_protection,
                                      () => {
                                        if (!inst.toggles?.password_protection && !inst.password_protection_configured) {
                                          setPasswordAuthModalInst(inst);
                                          setBasicAuthForm({
                                            user: inst.basic_auth_user || 'preview',
                                            password: '',
                                          });
                                          return;
                                        }
                                        handleToggleSwitch(
                                          inst,
                                          'password_protection',
                                          !inst.toggles?.password_protection
                                        );
                                      }
                                    )}
                                    <button
                                      type="button"
                                      onClick={() => {
                                        setPasswordAuthModalInst(inst);
                                        setBasicAuthForm({
                                          user: inst.basic_auth_user || 'preview',
                                          password: '',
                                        });
                                      }}
                                      style={{
                                        border: 'none',
                                        background: 'transparent',
                                        color: 'var(--primary)',
                                        fontSize: '12px',
                                        padding: 0,
                                        cursor: 'pointer',
                                        boxShadow: 'none',
                                      }}
                                    >
                                      Setup
                                    </button>
                                  </div>
                                </div>

                                {/* 6. System Cron (DISABLE_WP_CRON) */}
                                <div
                                  style={{
                                    border: '1px solid var(--border)',
                                    borderRadius: '10px',
                                    padding: '12px 14px',
                                    background: 'var(--surface)',
                                    display: 'flex',
                                    flexDirection: 'column',
                                    gap: '10px',
                                  }}
                                >
                                  <span style={{ fontSize: '13px', fontWeight: 500, color: 'var(--text)' }}>
                                    Disable wp-cron.php
                                  </span>
                                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                                    {renderSwitch(
                                      !!inst.toggles?.disable_wp_cron,
                                      () =>
                                        handleToggleSwitch(
                                          inst,
                                          'disable_wp_cron',
                                          !inst.toggles?.disable_wp_cron
                                        )
                                    )}
                                    <span style={{ fontSize: '11.5px', color: 'var(--text-muted)' }}>
                                      {inst.toggles?.disable_wp_cron ? 'System Cron (15m)' : 'Mặc định WP'}
                                    </span>
                                  </div>
                                </div>
                              </div>
                            </div>
                          )}

                          {/* SUB-TAB 2: PLUGINS MANAGEMENT */}
                          {subTab === 'plugins' && (
                            <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
                              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '8px' }}>
                                <div style={{ display: 'flex', gap: '8px' }}>
                                  <button
                                    type="button"
                                    onClick={() => openWporgCatalog('plugins', inst)}
                                    style={{
                                      height: '32px',
                                      minHeight: '32px',
                                      padding: '0 12px',
                                      fontSize: '12.5px',
                                      fontWeight: 600,
                                      background: 'var(--primary)',
                                      color: '#fff',
                                      border: 'none',
                                      borderRadius: '7px',
                                    }}
                                  >
                                    <Plus size={13} /> Install Plugin (WordPress.org)
                                  </button>
                                  <button
                                    type="button"
                                    className="secondary-light"
                                    onClick={() => handlePluginAction(inst, 'update_all')}
                                    style={{ height: '32px', minHeight: '32px', padding: '0 12px', fontSize: '12.5px' }}
                                  >
                                    <RefreshCw size={13} /> Update All Plugins
                                  </button>
                                </div>
                                <span style={{ fontSize: '12.5px', color: 'var(--text-muted)' }}>
                                  Bật/tắt, cập nhật hoặc gỡ bỏ Plugin trực tiếp không cần vào wp-admin
                                </span>
                              </div>

                              <div style={{ border: '1px solid var(--border)', borderRadius: '10px', overflow: 'hidden' }}>
                                <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '13px', textAlign: 'left' }}>
                                  <thead>
                                    <tr style={{ background: 'var(--surface-alt)', borderBottom: '1px solid var(--border)' }}>
                                      <th style={{ padding: '9px 14px' }}>Plugin</th>
                                      <th style={{ padding: '9px 14px' }}>Trạng thái (State)</th>
                                      <th style={{ padding: '9px 14px' }}>Phiên bản</th>
                                      <th style={{ padding: '9px 14px', textAlign: 'right' }}>Thao tác</th>
                                    </tr>
                                  </thead>
                                  <tbody>
                                    {instPlugins.length === 0 && (
                                      <tr>
                                        <td colSpan={4} style={{ padding: '22px 14px', textAlign: 'center', color: 'var(--text-muted)' }}>
                                          Không có Plugin nào đang được cài đặt trên website này.
                                        </td>
                                      </tr>
                                    )}
                                    {instPlugins.map(pl => (
                                      <tr key={pl.slug} style={{ borderBottom: '1px solid var(--border)' }}>
                                        <td style={{ padding: '9px 14px' }}>
                                          <strong>{pl.name}</strong>
                                          <div style={{ fontSize: '11.5px', color: 'var(--text-muted)' }}>
                                            <code>{pl.slug}</code>
                                          </div>
                                        </td>
                                        <td style={{ padding: '9px 14px' }}>
                                          <div style={{ display: 'inline-flex', alignItems: 'center', gap: '8px' }}>
                                            {renderSwitch(pl.status === 'active', () =>
                                              handlePluginAction(inst, 'toggle', pl.slug)
                                            )}
                                            <span style={{ fontSize: '12px', color: pl.status === 'active' ? 'var(--text)' : 'var(--text-muted)' }}>
                                              {pl.status === 'active' ? 'Active' : 'Inactive'}
                                            </span>
                                          </div>
                                        </td>
                                        <td style={{ padding: '9px 14px' }}>
                                          <code>{pl.version}</code>
                                          {pl.update_available && (
                                            <span className="badge warn" style={{ marginLeft: '6px', fontSize: '11px' }}>
                                              → {pl.latest_version}
                                            </span>
                                          )}
                                        </td>
                                        <td style={{ padding: '9px 14px', textAlign: 'right' }}>
                                          <div style={{ display: 'inline-flex', gap: '6px' }}>
                                            {pl.update_available && (
                                              <button
                                                type="button"
                                                className="secondary-light"
                                                onClick={() => handlePluginAction(inst, 'update', pl.slug)}
                                                style={{ height: '28px', minHeight: '28px', padding: '0 10px', fontSize: '12px' }}
                                              >
                                                Update
                                              </button>
                                            )}
                                            <button
                                              type="button"
                                              className="secondary-light"
                                              onClick={() => handlePluginAction(inst, 'delete', pl.slug)}
                                              title="Xóa Plugin"
                                              style={{ height: '28px', minHeight: '28px', padding: '0 8px', color: '#ef4444' }}
                                            >
                                              <Trash2 size={13} />
                                            </button>
                                          </div>
                                        </td>
                                      </tr>
                                    ))}
                                  </tbody>
                                </table>
                              </div>
                            </div>
                          )}

                          {/* SUB-TAB 3: THEMES MANAGEMENT */}
                          {subTab === 'themes' && (
                            <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
                              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '8px' }}>
                                <div style={{ display: 'flex', gap: '8px' }}>
                                  <button
                                    type="button"
                                    onClick={() => openWporgCatalog('themes', inst)}
                                    style={{
                                      height: '32px',
                                      minHeight: '32px',
                                      padding: '0 12px',
                                      fontSize: '12.5px',
                                      fontWeight: 600,
                                      background: 'var(--primary)',
                                      color: '#fff',
                                      border: 'none',
                                      borderRadius: '7px',
                                    }}
                                  >
                                    <Plus size={13} /> Install Theme (WordPress.org)
                                  </button>
                                  <button
                                    type="button"
                                    className="secondary-light"
                                    onClick={() => handleThemeAction(inst, 'update_all')}
                                    style={{ height: '32px', minHeight: '32px', padding: '0 12px', fontSize: '12.5px' }}
                                  >
                                    <RefreshCw size={13} /> Update All Themes
                                  </button>
                                </div>
                              </div>

                              <div style={{ border: '1px solid var(--border)', borderRadius: '10px', overflow: 'hidden' }}>
                                <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '13px', textAlign: 'left' }}>
                                  <thead>
                                    <tr style={{ background: 'var(--surface-alt)', borderBottom: '1px solid var(--border)' }}>
                                      <th style={{ padding: '9px 14px' }}>Theme</th>
                                      <th style={{ padding: '9px 14px' }}>Trạng thái</th>
                                      <th style={{ padding: '9px 14px' }}>Phiên bản</th>
                                      <th style={{ padding: '9px 14px', textAlign: 'right' }}>Thao tác</th>
                                    </tr>
                                  </thead>
                                  <tbody>
                                    {instThemes.length === 0 && (
                                      <tr>
                                        <td colSpan={4} style={{ padding: '22px 14px', textAlign: 'center', color: 'var(--text-muted)' }}>
                                          Không có Theme nào đang được cài đặt trên website này.
                                        </td>
                                      </tr>
                                    )}
                                    {instThemes.map(th => (
                                      <tr key={th.slug} style={{ borderBottom: '1px solid var(--border)' }}>
                                        <td style={{ padding: '9px 14px' }}>
                                          <strong>{th.name}</strong>
                                          <div style={{ fontSize: '11.5px', color: 'var(--text-muted)' }}>
                                            <code>{th.slug}</code>
                                          </div>
                                        </td>
                                        <td style={{ padding: '9px 14px' }}>
                                          {th.status === 'active' ? (
                                            <span className="badge ok">Active Theme</span>
                                          ) : (
                                            <button
                                              type="button"
                                              className="secondary-light"
                                              onClick={() => handleThemeAction(inst, 'activate', th.slug)}
                                              style={{ height: '26px', minHeight: '26px', padding: '0 10px', fontSize: '12px' }}
                                            >
                                              Activate
                                            </button>
                                          )}
                                        </td>
                                        <td style={{ padding: '9px 14px' }}>
                                          <code>{th.version}</code>
                                          {th.update_available && (
                                            <span className="badge warn" style={{ marginLeft: '6px', fontSize: '11px' }}>
                                              → {th.latest_version}
                                            </span>
                                          )}
                                        </td>
                                        <td style={{ padding: '9px 14px', textAlign: 'right' }}>
                                          <div style={{ display: 'inline-flex', gap: '6px' }}>
                                            {th.update_available && (
                                              <button
                                                type="button"
                                                className="secondary-light"
                                                onClick={() => handleThemeAction(inst, 'update', th.slug)}
                                                style={{ height: '28px', minHeight: '28px', padding: '0 10px', fontSize: '12px' }}
                                              >
                                                Update
                                              </button>
                                            )}
                                            {th.status !== 'active' && (
                                              <button
                                                type="button"
                                                className="secondary-light"
                                                onClick={() => handleThemeAction(inst, 'delete', th.slug)}
                                                title="Xóa Theme"
                                                style={{ height: '28px', minHeight: '28px', padding: '0 8px', color: '#ef4444' }}
                                              >
                                                <Trash2 size={13} />
                                              </button>
                                            )}
                                          </div>
                                        </td>
                                      </tr>
                                    ))}
                                  </tbody>
                                </table>
                              </div>
                            </div>
                          )}

                          {/* SUB-TAB 4: SECURITY HARDENING & CHECKSUMS */}
                          {subTab === 'security' && (
                            <div style={{ display: 'flex', flexDirection: 'column', gap: '14px' }}>
                              <div
                                style={{
                                  display: 'flex',
                                  justifyContent: 'space-between',
                                  alignItems: 'center',
                                  flexWrap: 'wrap',
                                  gap: '10px',
                                  padding: '10px 14px',
                                  background: 'var(--surface-alt)',
                                  border: '1px solid var(--border)',
                                  borderRadius: '10px',
                                }}
                              >
                                <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap' }}>
                                  <button
                                    type="button"
                                    disabled={!!busyAction}
                                    onClick={() =>
                                      handleSecurityAction(
                                        inst,
                                        'secure',
                                        (inst.security_checks || []).map(c => c.id)
                                      )
                                    }
                                    style={{
                                      height: '32px',
                                      minHeight: '32px',
                                      padding: '0 14px',
                                      fontSize: '12.5px',
                                      fontWeight: 600,
                                      background: 'var(--primary)',
                                      color: '#fff',
                                      border: 'none',
                                      borderRadius: '7px',
                                    }}
                                  >
                                    <ShieldCheck size={14} /> Bật tất cả ({inst.security_passed}/{inst.security_total})
                                  </button>
                                  <button
                                    type="button"
                                    className="secondary-light"
                                    disabled={!!busyAction}
                                    onClick={() =>
                                      handleSecurityAction(
                                        inst,
                                        'revert',
                                        (inst.security_checks || []).map(c => c.id)
                                      )
                                    }
                                    style={{ height: '32px', minHeight: '32px', padding: '0 12px', fontSize: '12.5px' }}
                                  >
                                    Tắt tất cả (Revert)
                                  </button>
                                  <button
                                    type="button"
                                    className="secondary-light"
                                    disabled={!!busyAction}
                                    onClick={() => handleSecurityAction(inst, 'verify_checksums')}
                                    style={{ height: '32px', minHeight: '32px', padding: '0 12px', fontSize: '12.5px' }}
                                  >
                                    <CheckCircle size={13} /> Verify Core Checksums
                                  </button>
                                  <button
                                    type="button"
                                    className="secondary-light"
                                    disabled={!!busyAction}
                                    onClick={() => handleSecurityAction(inst, 'reinstall_core')}
                                    style={{ height: '32px', minHeight: '32px', padding: '0 12px', fontSize: '12.5px' }}
                                  >
                                    <RotateCcw size={13} /> Reinstall Clean Core
                                  </button>
                                </div>
                                <span style={{ fontSize: '12px', color: 'var(--text-muted)' }}>
                                  Gạt công tắc từng mục để bật/tắt trực tiếp lớp bảo vệ tương ứng
                                </span>
                              </div>

                              <div
                                style={{
                                  border: '1px solid var(--border)',
                                  borderRadius: '10px',
                                  overflow: 'hidden',
                                  background: 'var(--surface)',
                                }}
                              >
                                {(inst.security_checks || []).map((chk, idx, arr) => {
                                  const isSecured = !!chk.passed;
                                  return (
                                    <div
                                      key={chk.id}
                                      onClick={() => {
                                        if (!busyAction) {
                                          handleSecurityAction(inst, isSecured ? 'revert' : 'secure', [chk.id]);
                                        }
                                      }}
                                      style={{
                                        display: 'grid',
                                        gridTemplateColumns: '52px minmax(0, 1fr) auto',
                                        alignItems: 'center',
                                        padding: '13px 18px',
                                        borderBottom: idx < arr.length - 1 ? '1px solid var(--border)' : 'none',
                                        background: isSecured
                                          ? 'color-mix(in srgb, var(--primary) 3%, var(--surface))'
                                          : 'var(--surface)',
                                        cursor: busyAction ? 'wait' : 'pointer',
                                        gap: '14px',
                                        transition: 'background 0.15s ease',
                                      }}
                                    >
                                      <div
                                        onClick={e => e.stopPropagation()}
                                        style={{ display: 'flex', alignItems: 'center' }}
                                      >
                                        {renderSwitch(
                                          isSecured,
                                          () => handleSecurityAction(inst, isSecured ? 'revert' : 'secure', [chk.id]),
                                          !!busyAction
                                        )}
                                      </div>

                                      <div style={{ minWidth: 0 }}>
                                        <div
                                          style={{
                                            display: 'flex',
                                            alignItems: 'center',
                                            gap: '8px',
                                            flexWrap: 'wrap',
                                            marginBottom: '3px',
                                          }}
                                        >
                                          <strong style={{ fontSize: '13.5px', color: 'var(--text)', lineHeight: 1.35 }}>
                                            {chk.title}
                                          </strong>
                                        </div>
                                        <div style={{ fontSize: '12.5px', color: 'var(--text-muted)', lineHeight: 1.45 }}>
                                          {chk.description}
                                        </div>
                                      </div>

                                      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'flex-end' }}>
                                        <span
                                          className={`badge ${chk.passed ? 'ok' : chk.critical ? 'bad' : 'warn'}`}
                                          style={{ minWidth: '74px', justifyContent: 'center', textAlign: 'center' }}
                                        >
                                          {chk.passed ? 'Secured' : chk.critical ? 'Critical' : 'Warning'}
                                        </span>
                                      </div>
                                    </div>
                                  );
                                })}
                              </div>
                            </div>
                          )}

                          {/* SUB-TAB 5: DATABASE & WP-CONFIG.PHP */}
                          {subTab === 'database' && (
                            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))', gap: '16px' }}>
                              <div
                                style={{
                                  border: '1px solid var(--border)',
                                  borderRadius: '10px',
                                  padding: '14px 16px',
                                  background: 'var(--surface-alt)',
                                }}
                              >
                                <strong style={{ fontSize: '13.5px', display: 'block', marginBottom: '10px' }}>
                                  Thông tin kết nối MySQL Database
                                </strong>
                                <div style={{ display: 'grid', gridTemplateColumns: '120px 1fr', rowGap: '7px', fontSize: '13px' }}>
                                  <span style={{ color: 'var(--text-muted)' }}>Database Name:</span>
                                  <code>{inst.db_name}</code>
                                  <span style={{ color: 'var(--text-muted)' }}>Database User:</span>
                                  <code>{inst.db_user}</code>
                                  <span style={{ color: 'var(--text-muted)' }}>Database Host:</span>
                                  <code>{inst.db_host}</code>
                                  <span style={{ color: 'var(--text-muted)' }}>Table Prefix:</span>
                                  <code>{inst.table_prefix}</code>
                                  <span style={{ color: 'var(--text-muted)' }}>Tables / Size:</span>
                                  <span>
                                    <strong>{inst.database?.table_count ?? 0}</strong> bảng ({inst.database?.size_mb ?? inst.db_size_mb ?? 0} MB)
                                  </span>
                                </div>
                                <div style={{ display: 'flex', gap: '8px', marginTop: '14px' }}>
                                  <button
                                    type="button"
                                    className="secondary-light"
                                    onClick={() => handleToolAction(inst, { action: 'optimize_db' })}
                                    style={{ height: '32px', minHeight: '32px', padding: '0 12px', fontSize: '12.5px' }}
                                  >
                                    <Zap size={13} /> Optimize & Repair DB
                                  </button>
                                  {navigateToPage && (
                                    <button
                                      type="button"
                                      className="secondary-light"
                                      onClick={() => navigateToPage('databases')}
                                      style={{ height: '32px', minHeight: '32px', padding: '0 12px', fontSize: '12.5px' }}
                                    >
                                      <Database size={13} /> MySQL Manager
                                    </button>
                                  )}
                                </div>
                              </div>

                              <div
                                style={{
                                  border: '1px solid var(--border)',
                                  borderRadius: '10px',
                                  padding: '14px 16px',
                                  background: 'var(--surface-alt)',
                                }}
                              >
                                <strong style={{ fontSize: '13.5px', display: 'block', marginBottom: '10px' }}>
                                  Hiệu năng wp-config.php
                                </strong>
                                <div style={{ display: 'flex', flexDirection: 'column', gap: '10px', fontSize: '13px' }}>
                                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '12px' }}>
                                    <code>WP_MEMORY_LIMIT</code>
                                    <select
                                      value={inst.wp_memory_limit || '256M'}
                                      onChange={e =>
                                        handleToolAction(inst, {
                                          action: 'update_config',
                                          wp_memory_limit: e.target.value,
                                        })
                                      }
                                      style={{ width: '155px', height: '32px' }}
                                    >
                                      <option value="128M">128M</option>
                                      <option value="256M">256M (Khuyên dùng)</option>
                                      <option value="512M">512M</option>
                                      <option value="1024M">1024M</option>
                                    </select>
                                  </div>

                                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '12px' }}>
                                    <code>WP_MAX_MEMORY_LIMIT</code>
                                    <select
                                      value={inst.wp_max_memory_limit || '512M'}
                                      onChange={e =>
                                        handleToolAction(inst, {
                                          action: 'update_config',
                                          wp_max_memory_limit: e.target.value,
                                        })
                                      }
                                      style={{ width: '155px', height: '32px' }}
                                    >
                                      <option value="256M">256M</option>
                                      <option value="512M">512M</option>
                                      <option value="1024M">1024M</option>
                                    </select>
                                  </div>

                                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '12px' }}>
                                    <code>WP_POST_REVISIONS</code>
                                    <select
                                      value={String(inst.wp_post_revisions || '10')}
                                      onChange={e =>
                                        handleToolAction(inst, {
                                          action: 'update_config',
                                          wp_post_revisions: e.target.value,
                                        })
                                      }
                                      style={{ width: '155px', height: '32px' }}
                                    >
                                      <option value="false">Tắt lưu bản nháp</option>
                                      <option value="5">Tối đa 5 bản</option>
                                      <option value="10">Tối đa 10 bản</option>
                                      <option value="25">Tối đa 25 bản</option>
                                    </select>
                                  </div>
                                </div>
                              </div>
                            </div>
                          )}
                        </div>
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          )}
        </>
      )}

      {/* 4B. GLOBAL PLUGINS TAB */}
      {globalTab === 'plugins' && (
        <div
          style={{
            border: '1px solid var(--border)',
            borderRadius: '12px',
            background: 'var(--surface)',
            padding: '20px',
          }}
        >
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '16px', flexWrap: 'wrap', gap: '10px' }}>
            <div>
              <h3 style={{ margin: 0, fontSize: '15.5px' }}>Quản lý Plugins trên toàn bộ Website WordPress</h3>
              <p className="hint" style={{ margin: '3px 0 0', fontSize: '12.5px' }}>
                Tìm kiếm và cài đặt Plugin từ kho chính thức WordPress.org cho bất kỳ website nào chỉ với 1 cú nhấp chuột.
              </p>
            </div>
            {instances[0] && (
              <button
                type="button"
                onClick={() => openWporgCatalog('plugins', instances[0])}
                style={{
                  height: '36px',
                  padding: '0 14px',
                  background: 'var(--primary)',
                  color: '#fff',
                  border: 'none',
                  borderRadius: '8px',
                  fontWeight: 600,
                }}
              >
                <Plus size={14} /> Tìm & Cài Plugin từ WordPress.org
              </button>
            )}
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(320px, 1fr))', gap: '12px' }}>
            {instances.map(inst => (
              <div
                key={inst.id}
                style={{
                  border: '1px solid var(--border)',
                  borderRadius: '10px',
                  padding: '14px 16px',
                  background: 'var(--surface-alt)',
                  display: 'flex',
                  justifyContent: 'space-between',
                  alignItems: 'center',
                }}
              >
                <div>
                  <strong>{inst.domain}</strong>
                  <div style={{ fontSize: '12px', color: 'var(--text-muted)', marginTop: '2px' }}>
                    {inst.plugins_count} Plugins ({inst.plugins_active_count} đang bật)
                  </div>
                </div>
                <div style={{ display: 'flex', gap: '6px' }}>
                  <button
                    type="button"
                    className="secondary-light"
                    onClick={() => openWporgCatalog('plugins', inst)}
                    style={{ height: '30px', minHeight: '30px', padding: '0 10px', fontSize: '12px' }}
                  >
                    + Cài thêm
                  </button>
                  <button
                    type="button"
                    className="secondary-light"
                    onClick={() => {
                      setGlobalTab('instances');
                      selectSubTab(inst, 'plugins');
                    }}
                    style={{ height: '30px', minHeight: '30px', padding: '0 10px', fontSize: '12px' }}
                  >
                    Quản lý
                  </button>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* 4C. GLOBAL THEMES TAB */}
      {globalTab === 'themes' && (
        <div
          style={{
            border: '1px solid var(--border)',
            borderRadius: '12px',
            background: 'var(--surface)',
            padding: '20px',
          }}
        >
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '16px', flexWrap: 'wrap', gap: '10px' }}>
            <div>
              <h3 style={{ margin: 0, fontSize: '15.5px' }}>Quản lý Giao diện (Themes) tập trung</h3>
              <p className="hint" style={{ margin: '3px 0 0', fontSize: '12.5px' }}>
                Xem giao diện đang kích hoạt và cài đặt Theme mới trực tiếp từ kho WordPress.org.
              </p>
            </div>
            {instances[0] && (
              <button
                type="button"
                onClick={() => openWporgCatalog('themes', instances[0])}
                style={{
                  height: '36px',
                  padding: '0 14px',
                  background: 'var(--primary)',
                  color: '#fff',
                  border: 'none',
                  borderRadius: '8px',
                  fontWeight: 600,
                }}
              >
                <Plus size={14} /> Tìm & Cài Theme từ WordPress.org
              </button>
            )}
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(320px, 1fr))', gap: '12px' }}>
            {instances.map(inst => (
              <div
                key={inst.id}
                style={{
                  border: '1px solid var(--border)',
                  borderRadius: '10px',
                  padding: '14px 16px',
                  background: 'var(--surface-alt)',
                  display: 'flex',
                  justifyContent: 'space-between',
                  alignItems: 'center',
                }}
              >
                <div>
                  <strong>{inst.domain}</strong>
                  <div style={{ fontSize: '12px', color: 'var(--text-muted)', marginTop: '2px' }}>
                    Đang dùng: <strong>{inst.active_theme}</strong> ({inst.themes_count} Themes)
                  </div>
                </div>
                <div style={{ display: 'flex', gap: '6px' }}>
                  <button
                    type="button"
                    className="secondary-light"
                    onClick={() => openWporgCatalog('themes', inst)}
                    style={{ height: '30px', minHeight: '30px', padding: '0 10px', fontSize: '12px' }}
                  >
                    + Cài Theme
                  </button>
                  <button
                    type="button"
                    className="secondary-light"
                    onClick={() => {
                      setGlobalTab('instances');
                      selectSubTab(inst, 'themes');
                    }}
                    style={{ height: '30px', minHeight: '30px', padding: '0 10px', fontSize: '12px' }}
                  >
                    Đổi Theme
                  </button>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* 4D. PACKAGE SETS TAB (Plesk Sets) */}
      {globalTab === 'sets' && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '10px' }}>
            <div>
              <h3 style={{ margin: 0, fontSize: '15.5px' }}>Bộ cài mẫu (Plugin & Theme Sets)</h3>
              <p className="hint" style={{ margin: '3px 0 0', fontSize: '12.5px' }}>
                Đóng gói các Plugin và Theme yêu thích thành 1 bộ mẫu để cài đặt hàng loạt chỉ với 1 cú nhấp chuột.
              </p>
            </div>
            <button
              type="button"
              onClick={() => setShowCreateSetModal(true)}
              style={{
                height: '36px',
                padding: '0 14px',
                background: 'var(--primary)',
                color: '#fff',
                border: 'none',
                borderRadius: '8px',
                fontWeight: 600,
              }}
            >
              <Plus size={14} /> Tạo Bộ cài mới (Create Set)
            </button>
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(340px, 1fr))', gap: '16px' }}>
            {packageSets.map(pkg => (
              <div
                key={pkg.id}
                style={{
                  border: '1px solid var(--border)',
                  borderRadius: '12px',
                  background: 'var(--surface)',
                  padding: '18px 20px',
                  display: 'flex',
                  flexDirection: 'column',
                  justifyContent: 'space-between',
                  gap: '14px',
                }}
              >
                <div>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '8px' }}>
                    <strong style={{ fontSize: '15px' }}>{pkg.name}</strong>
                    <span className="badge">{pkg.is_builtin ? 'Built-in Set' : 'Custom Set'}</span>
                  </div>
                  <p className="hint" style={{ margin: '6px 0 12px', fontSize: '12.5px' }}>
                    {pkg.description}
                  </p>
                  <div style={{ fontSize: '12px', marginBottom: '6px' }}>
                    <strong>Plugins ({pkg.plugins?.length || 0}):</strong>{' '}
                    {(pkg.plugins || []).map(p => (
                      <code key={p} style={{ marginRight: '5px' }}>
                        {p}
                      </code>
                    ))}
                  </div>
                  {pkg.themes?.length > 0 && (
                    <div style={{ fontSize: '12px' }}>
                      <strong>Themes:</strong>{' '}
                      {pkg.themes.map(th => (
                        <code key={th} style={{ marginRight: '5px' }}>
                          {th}
                        </code>
                      ))}
                    </div>
                  )}
                </div>

                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '8px', paddingTop: '10px', borderTop: '1px solid var(--border)' }}>
                  <div>
                    {!pkg.is_builtin && (
                      <button
                        type="button"
                        className="secondary-light"
                        disabled={!!busyAction}
                        onClick={() => handleDeletePackageSet(pkg)}
                        title="Xóa bộ cài mẫu này"
                        style={{ height: '32px', minHeight: '32px', padding: '0 10px', fontSize: '12px', color: '#ef4444' }}
                      >
                        <Trash2 size={13} /> Xóa
                      </button>
                    )}
                  </div>
                  <button
                    type="button"
                    disabled={!!busyAction}
                    onClick={() => openApplySetModal(pkg)}
                    style={{
                      height: '32px',
                      minHeight: '32px',
                      padding: '0 14px',
                      fontSize: '12.5px',
                      background: 'var(--primary)',
                      color: '#fff',
                      border: 'none',
                      borderRadius: '7px',
                      fontWeight: 600,
                    }}
                  >
                    Cài bộ này cho Website
                  </button>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* =====================================================================
          MODALS
         ===================================================================== */}

      {/* MODAL 1: 1-CLICK INSTALL WORDPRESS */}
      {showInstallModal && (
        <div className="modal-overlay" onClick={() => setShowInstallModal(false)}>
          <div
            className="modal"
            style={{ maxWidth: '640px', width: '100%' }}
            onClick={e => e.stopPropagation()}
          >
            <div className="modal-header">
              <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                <WordPressIcon size={20} style={{ color: 'var(--primary)' }} />
                <h3 style={{ margin: 0, fontSize: '16px' }}>Install WordPress (1-Click Toolkit)</h3>
              </div>
              <button type="button" className="icon-button" onClick={() => setShowInstallModal(false)}>
                <X size={18} />
              </button>
            </div>

            <form onSubmit={handleInstallSubmit}>
              <div className="modal-body" style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '12px' }}>
                <label style={{ gridColumn: 'span 1' }}>
                  <span>Tên miền (Domain)</span>
                  <select
                    value={installForm.website_id}
                    onChange={e => {
                      const wid = e.target.value;
                      const found = allWebsites.find(w => String(w.id) === String(wid));
                      setInstallForm(prev => ({
                        ...prev,
                        website_id: wid,
                        site_title: found ? `${found.domain} CMS` : prev.site_title,
                        admin_email: found ? `admin@${found.domain}` : prev.admin_email,
                      }));
                    }}
                    required
                  >
                    <option value="">-- Chọn tên miền --</option>
                    {installableWebsites.map(w => (
                      <option key={w.id} value={w.id}>
                        {w.domain}
                      </option>
                    ))}
                  </select>
                </label>

                <label style={{ gridColumn: 'span 1' }}>
                  <span>Thư mục cài đặt (Để trống = gốc /)</span>
                  <input
                    type="text"
                    placeholder="Ví dụ: blog hoặc để trống"
                    value={installForm.subpath}
                    onChange={e => setInstallForm(prev => ({ ...prev, subpath: e.target.value }))}
                  />
                </label>

                <label style={{ gridColumn: 'span 1' }}>
                  <span>Tiêu đề Website (Site Title)</span>
                  <input
                    type="text"
                    value={installForm.site_title}
                    onChange={e => setInstallForm(prev => ({ ...prev, site_title: e.target.value }))}
                    required
                  />
                </label>

                <label style={{ gridColumn: 'span 1' }}>
                  <span>Ngôn ngữ & Phiên bản PHP</span>
                  <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '8px' }}>
                    <select
                      value={installForm.language}
                      onChange={e => setInstallForm(prev => ({ ...prev, language: e.target.value }))}
                    >
                      <option value="vi">Tiếng Việt (vi)</option>
                      <option value="en_US">English (en_US)</option>
                      <option value="ja">日本語 (ja)</option>
                    </select>
                    <select
                      value={installForm.php_version}
                      onChange={e => setInstallForm(prev => ({ ...prev, php_version: e.target.value }))}
                    >
                      {installedPhpList.map(v => (
                        <option key={v} value={v}>
                          PHP {v}
                        </option>
                      ))}
                    </select>
                  </div>
                </label>

                <label style={{ gridColumn: 'span 1' }}>
                  <span>Tài khoản Admin (Username)</span>
                  <input
                    type="text"
                    value={installForm.admin_user}
                    onChange={e => setInstallForm(prev => ({ ...prev, admin_user: e.target.value }))}
                    required
                  />
                </label>

                <label style={{ gridColumn: 'span 1' }}>
                  <span>Mật khẩu Admin (Tối thiểu 10 ký tự)</span>
                  <div style={{ display: 'flex', gap: '6px' }}>
                    <input
                      type="text"
                      value={installForm.admin_password}
                      onChange={e => setInstallForm(prev => ({ ...prev, admin_password: e.target.value }))}
                      required
                    />
                    <button
                      type="button"
                      className="secondary-light"
                      onClick={() =>
                        setInstallForm(prev => ({ ...prev, admin_password: generateStrongPassword(16) }))
                      }
                      title="Tạo mật khẩu ngẫu nhiên mới"
                      style={{ padding: '0 10px' }}
                    >
                      <RefreshCw size={13} />
                    </button>
                  </div>
                </label>

                <label style={{ gridColumn: 'span 1' }}>
                  <span>Email Quản trị viên</span>
                  <input
                    type="email"
                    value={installForm.admin_email}
                    onChange={e => setInstallForm(prev => ({ ...prev, admin_email: e.target.value }))}
                    required
                  />
                </label>

                <label style={{ gridColumn: 'span 1' }}>
                  <span>Tiền tố bảng DB (Table Prefix bảo mật)</span>
                  <input
                    type="text"
                    value={installForm.table_prefix}
                    onChange={e => setInstallForm(prev => ({ ...prev, table_prefix: e.target.value }))}
                  />
                </label>

                <label style={{ gridColumn: 'span 2' }}>
                  <span>Bộ cài đặt Plugin/Theme đi kèm (Starter Set)</span>
                  <select
                    value={installForm.starter_set}
                    onChange={e => setInstallForm(prev => ({ ...prev, starter_set: e.target.value }))}
                  >
                    <option value="none">WordPress Mặc định (Không cài sẵn Plugin phụ)</option>
                    {packageSets.map(s => (
                      <option key={s.id} value={s.id}>
                        {s.name} ({(s.plugins || []).join(', ')})
                      </option>
                    ))}
                  </select>
                </label>
              </div>

              <div className="modal-footer">
                <button type="button" className="secondary-light" onClick={() => setShowInstallModal(false)}>
                  Hủy
                </button>
                <button
                  type="submit"
                  disabled={busyAction === 'installing'}
                  style={{
                    background: 'var(--primary)',
                    color: '#fff',
                    border: 'none',
                    padding: '0 18px',
                    height: '38px',
                    borderRadius: '8px',
                    fontWeight: 600,
                  }}
                >
                  {busyAction === 'installing' ? 'Đang cài đặt...' : 'Cài đặt ngay (Install)'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* MODAL 2: POST-INSTALL CREDENTIALS SUMMARY */}
      {installedCredsModal && (
        <div className="modal-overlay" onClick={() => setInstalledCredsModal(null)}>
          <div className="modal" style={{ maxWidth: '500px', width: '100%' }} onClick={e => e.stopPropagation()}>
            <div className="modal-header">
              <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                <CheckCircle size={20} style={{ color: '#10b981' }} />
                <h3 style={{ margin: 0, fontSize: '16px' }}>Cài đặt WordPress hoàn tất!</h3>
              </div>
              <button type="button" className="icon-button" onClick={() => setInstalledCredsModal(null)}>
                <X size={18} />
              </button>
            </div>
            <div className="modal-body" style={{ display: 'flex', flexDirection: 'column', gap: '10px', fontSize: '13.5px' }}>
              <div>
                Website: <strong>{installedCredsModal.url}</strong>
              </div>
              <div>
                Tài khoản Admin: <code>{installedCredsModal.admin_user}</code>
              </div>
              <div>
                Mật khẩu Admin: <code>{installedCredsModal.admin_password}</code>
              </div>
              <div>
                Database tự động tạo: <code>{installedCredsModal.db_name}</code>
              </div>
            </div>
            <div className="modal-footer">
              <button type="button" className="secondary-light" onClick={() => setInstalledCredsModal(null)}>
                Đóng
              </button>
              {installedCredsModal.website_id && (
                <button
                  type="button"
                  onClick={() => {
                    const id = installedCredsModal.website_id;
                    const domain = installedCredsModal.domain;
                    setInstalledCredsModal(null);
                    handleSsoLogin({ id, domain });
                  }}
                  style={{
                    background: 'var(--primary)',
                    color: '#fff',
                    border: 'none',
                    padding: '0 16px',
                    height: '36px',
                    borderRadius: '8px',
                    fontWeight: 600,
                  }}
                >
                  <LogIn size={14} /> Vào thẳng wp-admin (1-Click SSO)
                </button>
              )}
            </div>
          </div>
        </div>
      )}

      {/* MODAL 3: GENERAL & ADMIN SETUP MODAL ([Change] / [Setup]) */}
      {generalModalInst && (
        <div className="modal-overlay" onClick={() => setGeneralModalInst(null)}>
          <div className="modal" style={{ maxWidth: '520px', width: '100%' }} onClick={e => e.stopPropagation()}>
            <div className="modal-header">
              <h3 style={{ margin: 0, fontSize: '16px' }}>
                Thiết lập Website & Tài khoản Admin ({generalModalInst.domain})
              </h3>
              <button type="button" className="icon-button" onClick={() => setGeneralModalInst(null)}>
                <X size={18} />
              </button>
            </div>
            <form onSubmit={handleSaveGeneralSetup}>
              <div className="modal-body" style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
                <label>
                  <span>Tiêu đề Website (Site Title)</span>
                  <input
                    type="text"
                    value={generalForm.site_title}
                    onChange={e => setGeneralForm(prev => ({ ...prev, site_title: e.target.value }))}
                  />
                </label>
                <label>
                  <span>Mô tả ngắn (Tagline)</span>
                  <input
                    type="text"
                    value={generalForm.site_description}
                    onChange={e => setGeneralForm(prev => ({ ...prev, site_description: e.target.value }))}
                  />
                </label>
                <label>
                  <span>Phiên bản PHP của Website</span>
                  <select
                    value={generalForm.php_version}
                    onChange={e => setGeneralForm(prev => ({ ...prev, php_version: e.target.value }))}
                  >
                    {installedPhpList.map(v => (
                      <option key={v} value={v}>
                        PHP {v}
                      </option>
                    ))}
                  </select>
                </label>
                <label>
                  <span>Tài khoản Quản trị viên (Admin Username)</span>
                  <input
                    type="text"
                    value={generalForm.admin_user}
                    onChange={e => setGeneralForm(prev => ({ ...prev, admin_user: e.target.value }))}
                  />
                </label>
                <label>
                  <span>Email Quản trị viên</span>
                  <input
                    type="email"
                    value={generalForm.admin_email}
                    onChange={e => setGeneralForm(prev => ({ ...prev, admin_email: e.target.value }))}
                  />
                </label>
                <label>
                  <span>Đặt mật khẩu Admin mới (Để trống nếu không đổi)</span>
                  <div style={{ display: 'flex', gap: '6px' }}>
                    <input
                      type="text"
                      placeholder="Nhập mật khẩu mới tối thiểu 10 ký tự..."
                      value={generalForm.admin_password}
                      onChange={e => setGeneralForm(prev => ({ ...prev, admin_password: e.target.value }))}
                    />
                    <button
                      type="button"
                      className="secondary-light"
                      onClick={() =>
                        setGeneralForm(prev => ({ ...prev, admin_password: generateStrongPassword(16) }))
                      }
                      style={{ padding: '0 10px' }}
                    >
                      <RefreshCw size={13} />
                    </button>
                  </div>
                </label>
              </div>
              <div className="modal-footer">
                <button type="button" className="secondary-light" onClick={() => setGeneralModalInst(null)}>
                  Hủy
                </button>
                <button
                  type="submit"
                  style={{
                    background: 'var(--primary)',
                    color: '#fff',
                    border: 'none',
                    padding: '0 16px',
                    height: '36px',
                    borderRadius: '8px',
                    fontWeight: 600,
                  }}
                >
                  Lưu thay đổi
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* MODAL 4: WORDPRESS.ORG OFFICIAL DIRECTORY SEARCH & 1-CLICK INSTALL */}
      {wporgModal && (
        <div className="modal-overlay" onClick={() => setWporgModal(null)}>
          <div className="modal" style={{ maxWidth: '720px', width: '100%' }} onClick={e => e.stopPropagation()}>
            <div className="modal-header">
              <h3 style={{ margin: 0, fontSize: '16px' }}>
                Kho {wporgModal.kind === 'plugins' ? 'Plugins' : 'Themes'} chính thức WordPress.org —{' '}
                {wporgModal.instance.domain}
              </h3>
              <button type="button" className="icon-button" onClick={() => setWporgModal(null)}>
                <X size={18} />
              </button>
            </div>
            <div className="modal-body" style={{ display: 'flex', flexDirection: 'column', gap: '14px' }}>
              <form onSubmit={searchWporgCatalog} style={{ display: 'flex', gap: '8px' }}>
                <input
                  type="text"
                  placeholder={
                    wporgModal.kind === 'plugins'
                      ? 'Nhập tên Plugin (VD: WooCommerce, Elementor, Yoast SEO, Contact Form 7...)'
                      : 'Nhập tên Theme (VD: Astra, GeneratePress, Hello Elementor, OceanWP...)'
                  }
                  value={wporgQuery}
                  onChange={e => setWporgQuery(e.target.value)}
                  style={{ flex: 1 }}
                />
                <button
                  type="submit"
                  disabled={wporgSearching}
                  style={{
                    background: 'var(--primary)',
                    color: '#fff',
                    border: 'none',
                    padding: '0 16px',
                    borderRadius: '8px',
                    fontWeight: 600,
                  }}
                >
                  <Search size={14} /> {wporgSearching ? 'Đang tìm...' : 'Tìm kiếm'}
                </button>
              </form>

              <div
                style={{
                  maxHeight: '400px',
                  overflowY: 'auto',
                  display: 'flex',
                  flexDirection: 'column',
                  gap: '10px',
                }}
              >
                {wporgResults.map(item => (
                  <div
                    key={item.slug}
                    style={{
                      border: '1px solid var(--border)',
                      borderRadius: '10px',
                      padding: '12px 14px',
                      background: 'var(--surface-alt)',
                      display: 'flex',
                      justifyContent: 'space-between',
                      alignItems: 'center',
                      gap: '12px',
                    }}
                  >
                    <div style={{ flex: 1 }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap' }}>
                        <strong style={{ fontSize: '14px' }}>{item.name}</strong>
                        <span className="badge">v{item.version}</span>
                        <span style={{ fontSize: '12px', color: 'var(--text-muted)' }}>
                          bởi {item.author}
                        </span>
                      </div>
                      <p style={{ margin: '4px 0 0', fontSize: '12.5px', color: 'var(--text-secondary)', lineHeight: 1.45 }}>
                        {item.short_description}
                      </p>
                    </div>
                    <button
                      type="button"
                      onClick={() => {
                        if (wporgModal.kind === 'plugins') {
                          handlePluginAction(wporgModal.instance, 'install', item.slug, true);
                        } else {
                          handleThemeAction(wporgModal.instance, 'install', item.slug, true);
                        }
                      }}
                      style={{
                        height: '34px',
                        minHeight: '34px',
                        padding: '0 14px',
                        fontSize: '12.5px',
                        background: 'var(--primary)',
                        color: '#fff',
                        border: 'none',
                        borderRadius: '7px',
                        fontWeight: 600,
                        whiteSpace: 'nowrap',
                      }}
                    >
                      <Download size={13} /> Install & Activate
                    </button>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </div>
      )}

      {/* MODAL 5: DEBUGGING & LIVE DEBUG.LOG VIEWER */}
      {debugModalInst && (
        <div className="modal-overlay" onClick={() => setDebugModalInst(null)}>
          <div className="modal" style={{ maxWidth: '680px', width: '100%' }} onClick={e => e.stopPropagation()}>
            <div className="modal-header">
              <h3 style={{ margin: 0, fontSize: '16px' }}>
                WordPress Debugging & Nhật ký lỗi ({debugModalInst.domain})
              </h3>
              <button type="button" className="icon-button" onClick={() => setDebugModalInst(null)}>
                <X size={18} />
              </button>
            </div>
            <div className="modal-body" style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
              <div style={{ display: 'flex', gap: '20px', flexWrap: 'wrap', fontSize: '13px' }}>
                <div style={{ display: 'inline-flex', alignItems: 'center', gap: '10px' }}>
                  {renderSwitch(!!debugModalInst.toggles?.debugging, () => {
                    const v = !debugModalInst.toggles?.debugging;
                    handleToggleSwitch(debugModalInst, 'debugging', v);
                    setDebugModalInst(prev => ({
                      ...prev,
                      toggles: { ...prev.toggles, debugging: v },
                    }));
                  })}
                  <span style={{ fontWeight: 500 }}>WP_DEBUG (Bật chế độ gỡ lỗi)</span>
                </div>

                <div style={{ display: 'inline-flex', alignItems: 'center', gap: '10px' }}>
                  {renderSwitch(!!debugModalInst.toggles?.debug_log, () => {
                    const v = !debugModalInst.toggles?.debug_log;
                    handleToggleSwitch(debugModalInst, 'debug_log', v, {
                      debugging: debugModalInst.toggles?.debugging,
                    });
                    setDebugModalInst(prev => ({
                      ...prev,
                      toggles: { ...prev.toggles, debug_log: v },
                    }));
                  })}
                  <span style={{ fontWeight: 500 }}>WP_DEBUG_LOG (Ghi vào wp-content/debug.log)</span>
                </div>
              </div>

              <div>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '6px', gap: '8px' }}>
                  <strong style={{ fontSize: '13px' }}>Nội dung wp-content/debug.log:</strong>
                  <div style={{ display: 'inline-flex', gap: '6px' }}>
                    <button
                      type="button"
                      className="secondary-light"
                      onClick={handleRefreshDebugLog}
                      style={{ height: '28px', minHeight: '28px', padding: '0 10px', fontSize: '12px' }}
                    >
                      <RefreshCw size={12} /> Làm mới Log
                    </button>
                    <button
                      type="button"
                      className="secondary-light"
                      onClick={handleClearDebugLog}
                      style={{ height: '28px', minHeight: '28px', padding: '0 10px', fontSize: '12px' }}
                    >
                      Dọn sạch Log
                    </button>
                  </div>
                </div>
                <pre
                  style={{
                    background: '#0f172a',
                    color: '#e2e8f0',
                    padding: '12px 14px',
                    borderRadius: '8px',
                    fontSize: '12px',
                    maxHeight: '260px',
                    overflowY: 'auto',
                    margin: 0,
                  }}
                >
                  {debugLogText}
                </pre>
              </div>
            </div>
            <div className="modal-footer">
              <button type="button" className="secondary-light" onClick={() => setDebugModalInst(null)}>
                Đóng
              </button>
            </div>
          </div>
        </div>
      )}

      {/* MODAL 6: MAINTENANCE MODE SETUP (CUSTOM MESSAGE + BACKGROUND IMAGE + LIVE PREVIEW) */}
      {maintenanceModalInst && (() => {
        const rawBgUrl = String(maintenanceForm.bg_url || '').trim();
        let resolvedPreviewBgUrl = '';
        if (maintenanceLocalPreviewUrl && rawBgUrl.includes('/wp-content/maintenance-assets/')) {
          resolvedPreviewBgUrl = maintenanceLocalPreviewUrl;
        } else if (rawBgUrl.startsWith('/') && maintenanceModalInst.url) {
          try {
            resolvedPreviewBgUrl = new URL(rawBgUrl, maintenanceModalInst.url).href;
          } catch {
            resolvedPreviewBgUrl = rawBgUrl;
          }
        } else if (/^https:\/\//i.test(rawBgUrl)) {
          resolvedPreviewBgUrl = rawBgUrl;
        }
        const activePresetObj =
          MAINTENANCE_PRESET_OPTIONS.find(p => p.id === maintenanceForm.bg_preset) ||
          MAINTENANCE_PRESET_OPTIONS[0];
        const overlayTop = Math.max(0.2, Math.min(0.9, Number(maintenanceForm.bg_overlay ?? 55) / 100));
        const overlayBottom = Math.min(0.95, overlayTop + 0.12);
        const isMaintActive = !!maintenanceModalInst.toggles?.maintenance_mode;

        return (
          <div className="modal-overlay" onClick={() => setMaintenanceModalInst(null)}>
            <div
              className="modal"
              style={{ maxWidth: '700px', width: '100%' }}
              onClick={e => e.stopPropagation()}
            >
              <div className="modal-header">
                <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap' }}>
                  <h3 style={{ margin: 0, fontSize: '16px' }}>
                    Cấu hình Trang bảo trì ({maintenanceModalInst.domain}{maintenanceModalInst.subpath || ''})
                  </h3>
                  <span className={`badge ${isMaintActive ? 'warn' : ''}`} style={{ fontSize: '11px' }}>
                    {isMaintActive ? 'Đang bật Bảo trì' : 'Đang tắt'}
                  </span>
                </div>
                <button type="button" className="icon-button" onClick={() => setMaintenanceModalInst(null)}>
                  <X size={18} />
                </button>
              </div>

              <div
                className="modal-body"
                style={{
                  display: 'flex',
                  flexDirection: 'column',
                  gap: '14px',
                  maxHeight: '72vh',
                  overflowY: 'auto',
                }}
              >
                <label>
                  <span>Tiêu đề chính hiển thị trên trang bảo trì</span>
                  <input
                    type="text"
                    placeholder={DEFAULT_MAINTENANCE_TITLE}
                    value={maintenanceForm.title}
                    onChange={e => setMaintenanceForm(prev => ({ ...prev, title: e.target.value }))}
                  />
                </label>

                <label>
                  <span>Thông điệp hiển thị cho khách truy cập khi đang bảo trì</span>
                  <textarea
                    rows={3}
                    placeholder={DEFAULT_MAINTENANCE_MESSAGE}
                    value={maintenanceForm.message}
                    onChange={e => setMaintenanceForm(prev => ({ ...prev, message: e.target.value }))}
                  />
                </label>

                {/* Background Theme Presets + Custom Image Upload */}
                <div
                  style={{
                    border: '1px solid var(--border)',
                    borderRadius: '10px',
                    padding: '12px 14px',
                    background: 'var(--surface-alt)',
                    display: 'flex',
                    flexDirection: 'column',
                    gap: '12px',
                  }}
                >
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '8px' }}>
                    <strong style={{ fontSize: '13px', display: 'inline-flex', alignItems: 'center', gap: '6px' }}>
                      <ImageIcon size={14} style={{ color: 'var(--primary)' }} /> Hình nền & Phong cách hiển thị
                    </strong>
                    <div style={{ display: 'inline-flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap' }}>
                      <label
                        style={{
                          display: 'inline-flex',
                          alignItems: 'center',
                          gap: '6px',
                          height: '30px',
                          padding: '0 12px',
                          borderRadius: '7px',
                          background: 'var(--primary)',
                          color: '#fff',
                          fontSize: '12px',
                          fontWeight: 600,
                          cursor: uploadingMaintBg ? 'wait' : 'pointer',
                          opacity: uploadingMaintBg ? 0.65 : 1,
                          margin: 0,
                        }}
                      >
                        <Upload size={13} />
                        <span>{uploadingMaintBg ? 'Đang tải ảnh...' : 'Tải ảnh nền lên (≤ 5MB)'}</span>
                        <input
                          type="file"
                          accept="image/jpeg,image/png,image/webp,image/gif"
                          disabled={uploadingMaintBg}
                          onChange={handleUploadMaintenanceBackground}
                          style={{ display: 'none' }}
                        />
                      </label>
                      {rawBgUrl && (
                        <button
                          type="button"
                          className="secondary-light"
                          disabled={uploadingMaintBg}
                          onClick={handleRemoveMaintenanceBackground}
                          style={{ height: '30px', minHeight: '30px', padding: '0 10px', fontSize: '12px', color: '#ef4444' }}
                        >
                          <Trash2 size={12} /> Gỡ ảnh nền
                        </button>
                      )}
                    </div>
                  </div>

                  {/* 3 Preset Gradient Cards */}
                  <div>
                    <div style={{ fontSize: '12px', color: 'var(--text-muted)', marginBottom: '6px' }}>
                      Chủ đề nền mặc định (dùng khi không chọn ảnh nền riêng hoặc làm lớp lót):
                    </div>
                    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, minmax(0, 1fr))', gap: '8px' }}>
                      {MAINTENANCE_PRESET_OPTIONS.map(preset => {
                        const selected = maintenanceForm.bg_preset === preset.id;
                        return (
                          <button
                            key={preset.id}
                            type="button"
                            onClick={() => setMaintenanceForm(prev => ({ ...prev, bg_preset: preset.id }))}
                            style={{
                              padding: '8px 10px',
                              borderRadius: '8px',
                              border: selected ? '2px solid var(--primary)' : '1px solid var(--border)',
                              background: preset.gradient,
                              color: '#f8fafc',
                              fontSize: '12px',
                              fontWeight: selected ? 700 : 500,
                              cursor: 'pointer',
                              textAlign: 'left',
                              display: 'flex',
                              alignItems: 'center',
                              justifyContent: 'space-between',
                              minHeight: '42px',
                            }}
                          >
                            <span>{preset.label}</span>
                            {selected && <Check size={14} style={{ color: '#34d399', flexShrink: 0 }} />}
                          </button>
                        );
                      })}
                    </div>
                  </div>

                  {/* External HTTPS URL or Uploaded Asset Path */}
                  <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 1fr) 190px', gap: '12px', alignItems: 'end' }}>
                    <label style={{ margin: 0 }}>
                      <span style={{ fontSize: '12px' }}>Đường dẫn ảnh nền (Tự động điền khi tải lên hoặc dán link https://...)</span>
                      <input
                        type="text"
                        placeholder="Để trống để dùng nền Gradient hoặc dán link https://..."
                        value={maintenanceForm.bg_url}
                        onChange={e => {
                          setMaintenanceLocalPreviewUrl('');
                          setMaintenanceForm(prev => ({ ...prev, bg_url: e.target.value }));
                        }}
                      />
                    </label>
                    <label style={{ margin: 0 }}>
                      <span style={{ fontSize: '12px' }}>
                        Độ phủ tối nền: <strong>{maintenanceForm.bg_overlay}%</strong>
                      </span>
                      <input
                        type="range"
                        min={20}
                        max={90}
                        step={5}
                        value={maintenanceForm.bg_overlay}
                        onChange={e =>
                          setMaintenanceForm(prev => ({ ...prev, bg_overlay: Number(e.target.value) }))
                        }
                        style={{ width: '100%', cursor: 'pointer', margin: '6px 0 2px' }}
                      />
                    </label>
                  </div>
                </div>

                {/* Live Visual Preview */}
                <div>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '6px' }}>
                    <strong style={{ fontSize: '12.5px', color: 'var(--text-secondary)' }}>
                      Xem trước trực tiếp (Live Preview — HTTP 503):
                    </strong>
                    <span style={{ fontSize: '11.5px', color: 'var(--text-muted)' }}>
                      Admin bấm &quot;Log in wp-admin&quot; vẫn vào quản trị bình thường
                    </span>
                  </div>
                  <div
                    style={{
                      borderRadius: '12px',
                      overflow: 'hidden',
                      border: '1px solid var(--border)',
                      minHeight: '215px',
                      padding: '24px 18px',
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'center',
                      backgroundImage: resolvedPreviewBgUrl
                        ? `linear-gradient(180deg, rgba(2, 6, 23, ${overlayTop}), rgba(2, 6, 23, ${overlayBottom})), url("${resolvedPreviewBgUrl.replace(/"/g, '')}")`
                        : activePresetObj.gradient,
                      backgroundSize: 'cover',
                      backgroundPosition: 'center',
                    }}
                  >
                    <div
                      style={{
                        maxWidth: '460px',
                        width: '100%',
                        padding: '20px 22px',
                        borderRadius: '16px',
                        background: 'rgba(15, 23, 42, 0.72)',
                        border: '1px solid rgba(255, 255, 255, 0.16)',
                        backdropFilter: 'blur(12px)',
                        color: '#f8fafc',
                        textAlign: 'center',
                        boxShadow: '0 14px 32px rgba(0, 0, 0, 0.38)',
                      }}
                    >
                      <div
                        style={{
                          display: 'inline-flex',
                          alignItems: 'center',
                          gap: '7px',
                          padding: '4px 12px',
                          borderRadius: '999px',
                          background: 'rgba(16, 185, 129, 0.18)',
                          border: '1px solid rgba(16, 185, 129, 0.38)',
                          color: '#6ee7b7',
                          fontSize: '10.5px',
                          fontWeight: 700,
                          letterSpacing: '0.04em',
                          marginBottom: '10px',
                        }}
                      >
                        <span
                          style={{
                            width: '7px',
                            height: '7px',
                            borderRadius: '50%',
                            background: '#10b981',
                          }}
                        />
                        ĐANG BẢO TRÌ HỆ THỐNG
                      </div>
                      <div style={{ fontSize: '17px', fontWeight: 700, lineHeight: 1.3, marginBottom: '8px', color: '#ffffff' }}>
                        {maintenanceForm.title || DEFAULT_MAINTENANCE_TITLE}
                      </div>
                      <div
                        style={{
                          fontSize: '12.5px',
                          lineHeight: 1.6,
                          color: 'rgba(226, 232, 240, 0.92)',
                          whiteSpace: 'pre-line',
                        }}
                      >
                        {maintenanceForm.message || DEFAULT_MAINTENANCE_MESSAGE}
                      </div>
                      <div
                        style={{
                          marginTop: '12px',
                          paddingTop: '10px',
                          borderTop: '1px solid rgba(255, 255, 255, 0.1)',
                          fontSize: '11px',
                          color: 'rgba(148, 163, 184, 0.9)',
                        }}
                      >
                        {maintenanceModalInst.site_title || maintenanceModalInst.domain} &bull; Xin cảm ơn sự kiên nhẫn của quý khách
                      </div>
                    </div>
                  </div>
                </div>
              </div>

              <div className="modal-footer" style={{ display: 'flex', justifyContent: 'space-between', flexWrap: 'wrap', gap: '8px' }}>
                <button type="button" className="secondary-light" onClick={() => setMaintenanceModalInst(null)}>
                  Hủy
                </button>
                <div style={{ display: 'inline-flex', gap: '8px', flexWrap: 'wrap' }}>
                  <button
                    type="button"
                    className="secondary-light"
                    disabled={!!busyAction || uploadingMaintBg}
                    onClick={() => handleSaveMaintenanceSetup(undefined)}
                  >
                    Chỉ lưu cấu hình
                  </button>
                  <button
                    type="button"
                    disabled={!!busyAction || uploadingMaintBg}
                    onClick={() => handleSaveMaintenanceSetup(true)}
                    style={{
                      background: 'var(--primary)',
                      color: '#fff',
                      border: 'none',
                      padding: '0 16px',
                      height: '36px',
                      borderRadius: '8px',
                      fontWeight: 600,
                    }}
                  >
                    {isMaintActive ? 'Lưu & Cập nhật trang bảo trì' : 'Lưu & Bật chế độ bảo trì'}
                  </button>
                </div>
              </div>
            </div>
          </div>
        );
      })()}

      {/* MODAL 7: PASSWORD PROTECTION SETUP */}
      {passwordAuthModalInst && (
        <div className="modal-overlay" onClick={() => setPasswordAuthModalInst(null)}>
          <div className="modal" style={{ maxWidth: '460px', width: '100%' }} onClick={e => e.stopPropagation()}>
            <div className="modal-header">
              <h3 style={{ margin: 0, fontSize: '16px' }}>
                Khóa mật khẩu truy cập Website ({passwordAuthModalInst.domain})
              </h3>
              <button type="button" className="icon-button" onClick={() => setPasswordAuthModalInst(null)}>
                <X size={18} />
              </button>
            </div>
            <div className="modal-body" style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
              <label>
                <span>Tên đăng nhập HTTP Basic Auth</span>
                <input
                  type="text"
                  value={basicAuthForm.user}
                  onChange={e => setBasicAuthForm(prev => ({ ...prev, user: e.target.value }))}
                />
              </label>
              <label>
                <span>Mật khẩu bảo vệ</span>
                <input
                  type="password"
                  value={basicAuthForm.password}
                  onChange={e => setBasicAuthForm(prev => ({ ...prev, password: e.target.value }))}
                />
              </label>
            </div>
            <div className="modal-footer">
              <button type="button" className="secondary-light" onClick={() => setPasswordAuthModalInst(null)}>
                Hủy
              </button>
              <button
                type="button"
                disabled={!basicAuthForm.user.trim() || !basicAuthForm.password.trim() || !!busyAction}
                onClick={async () => {
                  if (!basicAuthForm.user.trim() || !basicAuthForm.password.trim()) {
                    setError('Vui lòng nhập đầy đủ Tên đăng nhập và Mật khẩu bảo vệ.');
                    return;
                  }
                  await handleToggleSwitch(passwordAuthModalInst, 'password_protection', true, {
                    basic_auth_user: basicAuthForm.user.trim(),
                    basic_auth_password: basicAuthForm.password,
                  });
                  setPasswordAuthModalInst(null);
                }}
                style={{
                  background: 'var(--primary)',
                  color: '#fff',
                  border: 'none',
                  padding: '0 16px',
                  height: '36px',
                  borderRadius: '8px',
                  fontWeight: 600,
                  opacity: !basicAuthForm.user.trim() || !basicAuthForm.password.trim() ? 0.55 : 1,
                }}
              >
                Lưu & Bật bảo vệ mật khẩu
              </button>
            </div>
          </div>
        </div>
      )}

      {/* MODAL 8: CLONE / SYNC MODAL */}
      {cloneSyncModal && (() => {
        const normSubpath = s => String(s || '').trim().replace(/^\/+|\/+$/g, '');
        const isSameCloneTarget =
          String(cloneSyncForm.target_website_id || '') ===
            String(cloneSyncModal.instance.website_id || cloneSyncModal.instance.id) &&
          normSubpath(cloneSyncForm.target_subpath) === normSubpath(cloneSyncModal.instance.subpath);
        const nothingToSync = !cloneSyncForm.sync_files && !cloneSyncForm.sync_database;
        const disableCloneSubmit =
          !cloneSyncForm.target_website_id || isSameCloneTarget || nothingToSync || !!busyAction;

        return (
          <div className="modal-overlay" onClick={() => setCloneSyncModal(null)}>
            <div className="modal" style={{ maxWidth: '540px', width: '100%' }} onClick={e => e.stopPropagation()}>
              <div className="modal-header">
                <h3 style={{ margin: 0, fontSize: '16px' }}>
                  {cloneSyncModal.mode === 'clone'
                    ? `Nhân bản Website (Clone): ${cloneSyncModal.instance.domain}${cloneSyncModal.instance.subpath || ''}`
                    : `Đồng bộ dữ liệu (Sync): ${cloneSyncModal.instance.domain}${cloneSyncModal.instance.subpath || ''}`}
                </h3>
                <button type="button" className="icon-button" onClick={() => setCloneSyncModal(null)}>
                  <X size={18} />
                </button>
              </div>
              <div className="modal-body" style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
                <label>
                  <span>Chọn Tên miền / Subdomain đích</span>
                  <select
                    value={cloneSyncForm.target_website_id}
                    onChange={e => setCloneSyncForm(prev => ({ ...prev, target_website_id: e.target.value }))}
                  >
                    <option value="">-- Chọn tên miền đích --</option>
                    {allWebsites.map(w => (
                      <option key={w.id} value={w.id}>
                        {w.domain}
                      </option>
                    ))}
                  </select>
                </label>
                <label>
                  <span>Thư mục con đích (Để trống nếu nhân bản vào gốc /)</span>
                  <input
                    type="text"
                    placeholder="Ví dụ: staging"
                    value={cloneSyncForm.target_subpath}
                    onChange={e => setCloneSyncForm(prev => ({ ...prev, target_subpath: e.target.value }))}
                  />
                </label>

                <div
                  style={{
                    border: '1px solid var(--border)',
                    borderRadius: '10px',
                    padding: '10px 14px',
                    background: 'var(--surface-alt)',
                    display: 'flex',
                    flexDirection: 'column',
                    gap: '10px',
                    fontSize: '13px',
                  }}
                >
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '10px' }}>
                    <span>Đồng bộ toàn bộ mã nguồn (Files &amp; wp-content)</span>
                    {renderSwitch(!!cloneSyncForm.sync_files, () =>
                      setCloneSyncForm(prev => ({ ...prev, sync_files: !prev.sync_files }))
                    )}
                  </div>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '10px' }}>
                    <span>Đồng bộ Cơ sở dữ liệu &amp; tự động đổi URL đích</span>
                    {renderSwitch(!!cloneSyncForm.sync_database, () =>
                      setCloneSyncForm(prev => ({ ...prev, sync_database: !prev.sync_database }))
                    )}
                  </div>
                </div>

                {isSameCloneTarget && (
                  <div
                    style={{
                      padding: '10px 12px',
                      borderRadius: '8px',
                      background: 'rgba(245, 158, 11, 0.12)',
                      border: '1px solid rgba(245, 158, 11, 0.35)',
                      color: '#b45309',
                      fontSize: '12.5px',
                      lineHeight: 1.5,
                    }}
                  >
                    Tên miền và thư mục đích đang trùng với chính website nguồn. Vui lòng chọn tên miền khác hoặc nhập thư mục con đích (ví dụ: <code>staging</code>).
                  </div>
                )}
              </div>
              <div className="modal-footer">
                <button type="button" className="secondary-light" onClick={() => setCloneSyncModal(null)}>
                  Hủy
                </button>
                <button
                  type="button"
                  disabled={disableCloneSubmit}
                  onClick={() =>
                    handleToolAction(cloneSyncModal.instance, {
                      action: cloneSyncModal.mode,
                      target_website_id: Number(cloneSyncForm.target_website_id),
                      target_subpath: cloneSyncForm.target_subpath,
                      sync_files: cloneSyncForm.sync_files,
                      sync_database: cloneSyncForm.sync_database,
                    })
                  }
                  style={{
                    background: 'var(--primary)',
                    color: '#fff',
                    border: 'none',
                    padding: '0 16px',
                    height: '36px',
                    borderRadius: '8px',
                    fontWeight: 600,
                    opacity: disableCloneSubmit ? 0.5 : 1,
                    cursor: disableCloneSubmit ? 'not-allowed' : 'pointer',
                  }}
                >
                  {cloneSyncModal.mode === 'clone' ? 'Bắt đầu Nhân bản (Clone)' : 'Bắt đầu Đồng bộ (Sync)'}
                </button>
              </div>
            </div>
          </div>
        );
      })()}

      {/* MODAL 9: SNAPSHOT BACKUP & RESTORE */}
      {snapshotModalInst && (
        <div className="modal-overlay" onClick={() => setSnapshotModalInst(null)}>
          <div className="modal" style={{ maxWidth: '620px', width: '100%' }} onClick={e => e.stopPropagation()}>
            <div className="modal-header">
              <h3 style={{ margin: 0, fontSize: '16px' }}>
                Sao lưu & Khôi phục nhanh ({snapshotModalInst.domain})
              </h3>
              <button type="button" className="icon-button" onClick={() => setSnapshotModalInst(null)}>
                <X size={18} />
              </button>
            </div>
            <div className="modal-body" style={{ display: 'flex', flexDirection: 'column', gap: '14px' }}>
              <div style={{ display: 'flex', gap: '8px' }}>
                <input
                  type="text"
                  placeholder="Ghi chú bản sao lưu..."
                  value={snapshotLabelDraft}
                  onChange={e => setSnapshotLabelDraft(e.target.value)}
                  style={{ flex: 1 }}
                />
                <button
                  type="button"
                  disabled={!!busyAction}
                  onClick={async () => {
                    const res = await handleToolAction(snapshotModalInst, {
                      action: 'create_snapshot',
                      snapshot_label: snapshotLabelDraft,
                    });
                    if (res) {
                      setSnapshotLabelDraft(`Snapshot (${new Date().toLocaleDateString()})`);
                      await loadToolkitData(true);
                    }
                  }}
                  style={{
                    background: 'var(--primary)',
                    color: '#fff',
                    border: 'none',
                    padding: '0 16px',
                    borderRadius: '8px',
                    fontWeight: 600,
                    whiteSpace: 'nowrap',
                  }}
                >
                  <Plus size={14} /> Tạo Snapshot ngay
                </button>
              </div>

              {(snapshotModalInst.snapshots || []).length === 0 ? (
                <div style={{ padding: '24px', textAlign: 'center', color: 'var(--text-muted)', fontSize: '13px' }}>
                  Chưa có bản sao lưu Snapshot nào cho website này.
                </div>
              ) : (
                <div style={{ border: '1px solid var(--border)', borderRadius: '8px', overflow: 'hidden' }}>
                  <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '13px', textAlign: 'left' }}>
                    <thead>
                      <tr style={{ background: 'var(--surface-alt)', borderBottom: '1px solid var(--border)' }}>
                        <th style={{ padding: '8px 12px' }}>Ghi chú / ID</th>
                        <th style={{ padding: '8px 12px' }}>Dung lượng</th>
                        <th style={{ padding: '8px 12px', textAlign: 'right' }}>Thao tác</th>
                      </tr>
                    </thead>
                    <tbody>
                      {(snapshotModalInst.snapshots || []).map(sn => (
                        <tr key={sn.id} style={{ borderBottom: '1px solid var(--border)' }}>
                          <td style={{ padding: '8px 12px' }}>
                            <strong>{sn.label || sn.id}</strong>
                            <div style={{ fontSize: '11px', color: 'var(--text-muted)' }}>
                              <code>{sn.id}</code>
                            </div>
                          </td>
                          <td style={{ padding: '8px 12px' }}>{sn.size_mb} MB</td>
                          <td style={{ padding: '8px 12px', textAlign: 'right' }}>
                            <div style={{ display: 'inline-flex', gap: '6px' }}>
                              <button
                                type="button"
                                className="secondary-light"
                                disabled={!!busyAction}
                                onClick={() => {
                                  if (
                                    !window.confirm(
                                      `Xác nhận khôi phục bản sao lưu "${sn.label || sn.id}" cho ${snapshotModalInst.domain}?\n\nMã nguồn và Database hiện tại sẽ được ghi đè bằng thời điểm tạo Snapshot này.`
                                    )
                                  ) {
                                    return;
                                  }
                                  handleToolAction(snapshotModalInst, {
                                    action: 'restore_snapshot',
                                    snapshot_id: sn.id,
                                  });
                                }}
                                style={{ height: '28px', minHeight: '28px', padding: '0 10px', fontSize: '12px' }}
                              >
                                Khôi phục
                              </button>
                              <button
                                type="button"
                                className="secondary-light"
                                disabled={!!busyAction}
                                onClick={async () => {
                                  if (!window.confirm(`Xóa vĩnh viễn bản sao lưu "${sn.label || sn.id}"?`)) {
                                    return;
                                  }
                                  await handleToolAction(snapshotModalInst, {
                                    action: 'delete_snapshot',
                                    snapshot_id: sn.id,
                                  });
                                }}
                                style={{ height: '28px', minHeight: '28px', padding: '0 8px', color: '#ef4444' }}
                              >
                                <Trash2 size={12} />
                              </button>
                            </div>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
            <div className="modal-footer">
              <button type="button" className="secondary-light" onClick={() => setSnapshotModalInst(null)}>
                Đóng
              </button>
            </div>
          </div>
        </div>
      )}

      {/* MODAL 10: SEARCH & REPLACE URL IN DATABASE */}
      {searchReplaceModalInst && (
        <div className="modal-overlay" onClick={() => setSearchReplaceModalInst(null)}>
          <div className="modal" style={{ maxWidth: '560px', width: '100%' }} onClick={e => e.stopPropagation()}>
            <div className="modal-header">
              <h3 style={{ margin: 0, fontSize: '16px' }}>
                Search & Replace Database ({searchReplaceModalInst.domain})
              </h3>
              <button type="button" className="icon-button" onClick={() => setSearchReplaceModalInst(null)}>
                <X size={18} />
              </button>
            </div>
            <div className="modal-body" style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
              <label>
                <span>Chuỗi hoặc URL cần tìm (Search for)</span>
                <input
                  type="text"
                  value={searchReplaceForm.search_from}
                  onChange={e => setSearchReplaceForm(prev => ({ ...prev, search_from: e.target.value }))}
                />
              </label>
              <label>
                <span>Thay thế bằng (Replace with)</span>
                <input
                  type="text"
                  value={searchReplaceForm.replace_to}
                  onChange={e => setSearchReplaceForm(prev => ({ ...prev, replace_to: e.target.value }))}
                />
              </label>
              <div style={{ display: 'inline-flex', alignItems: 'center', gap: '10px', fontSize: '13px', marginTop: '4px' }}>
                {renderSwitch(searchReplaceForm.dry_run, () =>
                  setSearchReplaceForm(prev => ({ ...prev, dry_run: !prev.dry_run }))
                )}
                <span>Chạy thử (Dry-run) kiểm tra số lượng thay đổi trước khi ghi thật vào Database</span>
              </div>

              {searchReplaceOutput && (
                <div>
                  <strong style={{ fontSize: '12.5px', display: 'block', marginBottom: '5px' }}>
                    Kết quả thực thi WP-CLI:
                  </strong>
                  <pre
                    style={{
                      background: '#0f172a',
                      color: '#e2e8f0',
                      padding: '10px 12px',
                      borderRadius: '8px',
                      fontSize: '12px',
                      maxHeight: '180px',
                      overflowY: 'auto',
                      margin: 0,
                      whiteSpace: 'pre-wrap',
                    }}
                  >
                    {searchReplaceOutput}
                  </pre>
                </div>
              )}
            </div>
            <div className="modal-footer">
              <button type="button" className="secondary-light" onClick={() => setSearchReplaceModalInst(null)}>
                Đóng
              </button>
              <button
                type="button"
                disabled={!searchReplaceForm.search_from.trim() || !!busyAction}
                onClick={async () => {
                  const res = await handleToolAction(searchReplaceModalInst, {
                    action: 'search_replace',
                    ...searchReplaceForm,
                  });
                  if (res && !searchReplaceForm.dry_run) setSearchReplaceModalInst(null);
                }}
                style={{
                  background: 'var(--primary)',
                  color: '#fff',
                  border: 'none',
                  padding: '0 16px',
                  height: '36px',
                  borderRadius: '8px',
                  fontWeight: 600,
                  opacity: !searchReplaceForm.search_from.trim() || !!busyAction ? 0.55 : 1,
                }}
              >
                {searchReplaceForm.dry_run ? 'Chạy thử (Dry-run)' : 'Thực thi Search & Replace'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* MODAL 11: CREATE NEW PACKAGE SET */}
      {showCreateSetModal && (
        <div className="modal-overlay" onClick={() => setShowCreateSetModal(false)}>
          <div className="modal" style={{ maxWidth: '500px', width: '100%' }} onClick={e => e.stopPropagation()}>
            <div className="modal-header">
              <h3 style={{ margin: 0, fontSize: '16px' }}>Tạo Bộ cài mẫu (Create Package Set)</h3>
              <button type="button" className="icon-button" onClick={() => setShowCreateSetModal(false)}>
                <X size={18} />
              </button>
            </div>
            <form onSubmit={handleCreatePackageSet}>
              <div className="modal-body" style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
                <label>
                  <span>Tên Bộ cài đặt</span>
                  <input
                    type="text"
                    placeholder="Ví dụ: Bộ chuẩn Landing Page Doanh nghiệp"
                    value={newSetForm.name}
                    onChange={e => setNewSetForm(prev => ({ ...prev, name: e.target.value }))}
                    required
                  />
                </label>
                <label>
                  <span>Mô tả</span>
                  <input
                    type="text"
                    value={newSetForm.description}
                    onChange={e => setNewSetForm(prev => ({ ...prev, description: e.target.value }))}
                  />
                </label>
                <label>
                  <span>Danh sách Plugin Slugs (Cách nhau bằng dấu phẩy)</span>
                  <input
                    type="text"
                    value={newSetForm.pluginsText}
                    onChange={e => setNewSetForm(prev => ({ ...prev, pluginsText: e.target.value }))}
                  />
                </label>
                <label>
                  <span>Danh sách Theme Slugs (Cách nhau bằng dấu phẩy)</span>
                  <input
                    type="text"
                    value={newSetForm.themesText}
                    onChange={e => setNewSetForm(prev => ({ ...prev, themesText: e.target.value }))}
                  />
                </label>
              </div>
              <div className="modal-footer">
                <button type="button" className="secondary-light" onClick={() => setShowCreateSetModal(false)}>
                  Hủy
                </button>
                <button
                  type="submit"
                  style={{
                    background: 'var(--primary)',
                    color: '#fff',
                    border: 'none',
                    padding: '0 16px',
                    height: '36px',
                    borderRadius: '8px',
                    fontWeight: 600,
                  }}
                >
                  Lưu Bộ cài mẫu
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* MODAL 12: CONFIRM PERMANENT WORDPRESS INSTANCE REMOVAL (SOURCE + DATABASE) */}
      {removeModalTargets && removeModalTargets.length > 0 && (
        <div
          className="modal-overlay"
          onClick={() => {
            if (busyAction !== 'removing-instances') setRemoveModalTargets(null);
          }}
        >
          <div className="modal" style={{ maxWidth: '580px', width: '100%' }} onClick={e => e.stopPropagation()}>
            <div className="modal-header">
              <div style={{ display: 'flex', alignItems: 'center', gap: '8px', color: '#dc2626' }}>
                <AlertTriangle size={20} />
                <h3 style={{ margin: 0, fontSize: '16px', color: '#dc2626' }}>
                  Xác nhận xóa vĩnh viễn WordPress ({removeModalTargets.length} website)
                </h3>
              </div>
              <button
                type="button"
                className="icon-button"
                disabled={busyAction === 'removing-instances'}
                onClick={() => setRemoveModalTargets(null)}
              >
                <X size={18} />
              </button>
            </div>

            <div className="modal-body" style={{ display: 'flex', flexDirection: 'column', gap: '14px', fontSize: '13.5px' }}>
              <div
                style={{
                  padding: '12px 14px',
                  borderRadius: '10px',
                  background: 'rgba(239, 68, 68, 0.08)',
                  border: '1px solid rgba(239, 68, 68, 0.28)',
                  color: 'var(--text)',
                  lineHeight: 1.55,
                }}
              >
                Hành động này sẽ <strong>xóa sạch toàn bộ mã nguồn WordPress</strong> trong thư mục cài đặt, xóa các bản Snapshot đi kèm và gỡ website khỏi WordPress Manager (khi bấm <strong>Scan</strong> sẽ không còn xuất hiện lại).
                <div style={{ marginTop: '4px', fontSize: '12px', color: 'var(--text-secondary)' }}>
                  Nếu chỉ muốn gỡ khỏi khung quản lý mà giữ nguyên mã nguồn &amp; Database, hãy bấm Hủy và chọn nút <strong>Detach</strong>.
                </div>
              </div>

              <div
                style={{
                  border: '1px solid var(--border)',
                  borderRadius: '10px',
                  maxHeight: '200px',
                  overflowY: 'auto',
                  background: 'var(--surface-alt)',
                }}
              >
                {removeModalTargets.map((inst, idx) => (
                  <div
                    key={inst.id}
                    style={{
                      padding: '10px 14px',
                      borderBottom: idx < removeModalTargets.length - 1 ? '1px solid var(--border)' : 'none',
                      fontSize: '12.5px',
                    }}
                  >
                    <div style={{ fontWeight: 700, fontSize: '13.5px', color: 'var(--text)' }}>
                      {inst.domain}{inst.subpath || ''}
                    </div>
                    <div style={{ color: 'var(--text-secondary)', marginTop: '3px' }}>
                      Thư mục bị xóa sạch: <code>{inst.public_path}</code>
                    </div>
                    <div style={{ color: 'var(--text-secondary)', marginTop: '2px' }}>
                      Database liên kết: <code>{inst.db_name || 'Tự động dò từ wp-config.php'}</code>
                    </div>
                  </div>
                ))}
              </div>

              <label
                style={{
                  display: 'flex',
                  flexDirection: 'row',
                  alignItems: 'flex-start',
                  gap: '10px',
                  padding: '12px 14px',
                  borderRadius: '10px',
                  border: '1px solid var(--border)',
                  background: 'var(--surface)',
                  cursor: 'pointer',
                  margin: 0,
                }}
              >
                <input
                  type="checkbox"
                  checked={removeDropDb}
                  onChange={e => setRemoveDropDb(e.target.checked)}
                  style={{ width: '17px', height: '17px', marginTop: '2px', flexShrink: 0, cursor: 'pointer' }}
                />
                <div style={{ lineHeight: 1.45 }}>
                  <strong style={{ display: 'block', fontSize: '13.5px', color: 'var(--text)' }}>
                    Xóa đồng thời cơ sở dữ liệu MySQL và tài khoản Database đi kèm
                  </strong>
                  <span style={{ fontSize: '12px', color: 'var(--text-muted)' }}>
                    Khuyên dùng để dọn sạch triệt để dung lượng hosting. Bỏ tích nếu bạn muốn giữ lại Database trong MySQL Management.
                  </span>
                </div>
              </label>
            </div>

            <div className="modal-footer">
              <button
                type="button"
                className="secondary-light"
                disabled={busyAction === 'removing-instances'}
                onClick={() => setRemoveModalTargets(null)}
              >
                Hủy
              </button>
              <button
                type="button"
                disabled={busyAction === 'removing-instances'}
                onClick={handleConfirmRemoveInstances}
                style={{
                  background: '#dc2626',
                  color: '#fff',
                  border: 'none',
                  padding: '0 18px',
                  height: '38px',
                  borderRadius: '8px',
                  fontWeight: 600,
                }}
              >
                <Trash2 size={14} />{' '}
                {busyAction === 'removing-instances'
                  ? 'Đang xóa mã nguồn & Database...'
                  : removeDropDb
                    ? 'Xóa vĩnh viễn Mã nguồn & Database'
                    : 'Chỉ xóa Mã nguồn (Giữ lại DB)'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* MODAL 13: CORE CHECKSUMS VERIFICATION RESULT */}
      {checksumResultModal && (
        <div className="modal-overlay" onClick={() => setChecksumResultModal(null)}>
          <div className="modal" style={{ maxWidth: '640px', width: '100%' }} onClick={e => e.stopPropagation()}>
            <div className="modal-header">
              <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                {checksumResultModal.verified ? (
                  <CheckCircle size={20} style={{ color: '#10b981' }} />
                ) : (
                  <ShieldAlert size={20} style={{ color: '#ef4444' }} />
                )}
                <h3 style={{ margin: 0, fontSize: '16px' }}>
                  Kết quả kiểm tra Core Checksums ({checksumResultModal.instance?.domain})
                </h3>
              </div>
              <button type="button" className="icon-button" onClick={() => setChecksumResultModal(null)}>
                <X size={18} />
              </button>
            </div>
            <div className="modal-body" style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
              <div
                style={{
                  padding: '10px 14px',
                  borderRadius: '8px',
                  background: checksumResultModal.verified
                    ? 'rgba(16, 185, 129, 0.1)'
                    : 'rgba(239, 68, 68, 0.1)',
                  border: checksumResultModal.verified
                    ? '1px solid rgba(16, 185, 129, 0.3)'
                    : '1px solid rgba(239, 68, 68, 0.3)',
                  fontSize: '13px',
                  fontWeight: 500,
                }}
              >
                {checksumResultModal.message}
              </div>
              {checksumResultModal.output && (
                <pre
                  style={{
                    background: '#0f172a',
                    color: '#e2e8f0',
                    padding: '12px 14px',
                    borderRadius: '8px',
                    fontSize: '12px',
                    maxHeight: '260px',
                    overflowY: 'auto',
                    margin: 0,
                    whiteSpace: 'pre-wrap',
                  }}
                >
                  {checksumResultModal.output}
                </pre>
              )}
            </div>
            <div className="modal-footer">
              <button type="button" className="secondary-light" onClick={() => setChecksumResultModal(null)}>
                Đóng
              </button>
              {!checksumResultModal.verified && checksumResultModal.instance && (
                <button
                  type="button"
                  disabled={!!busyAction}
                  onClick={async () => {
                    const targetInst = checksumResultModal.instance;
                    setChecksumResultModal(null);
                    await handleSecurityAction(targetInst, 'reinstall_core');
                  }}
                  style={{
                    background: 'var(--primary)',
                    color: '#fff',
                    border: 'none',
                    padding: '0 16px',
                    height: '36px',
                    borderRadius: '8px',
                    fontWeight: 600,
                  }}
                >
                  <RotateCcw size={14} /> Tải lại Core sạch ngay (Reinstall Clean Core)
                </button>
              )}
            </div>
          </div>
        </div>
      )}

      {/* MODAL 14: SELECT TARGET WEBSITES FOR PACKAGE SET */}
      {applySetModal && applySetModal.pkg && (
        <div className="modal-overlay" onClick={() => setApplySetModal(null)}>
          <div className="modal" style={{ maxWidth: '520px', width: '100%' }} onClick={e => e.stopPropagation()}>
            <div className="modal-header">
              <h3 style={{ margin: 0, fontSize: '16px' }}>
                Cài đặt bộ gói &ldquo;{applySetModal.pkg.name}&rdquo;
              </h3>
              <button type="button" className="icon-button" onClick={() => setApplySetModal(null)}>
                <X size={18} />
              </button>
            </div>
            <div className="modal-body" style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
              <div style={{ fontSize: '12.5px', color: 'var(--text-secondary)', lineHeight: 1.5 }}>
                Chọn các website WordPress muốn cài đặt tự động{' '}
                <strong>{applySetModal.pkg.plugins?.length || 0} Plugins</strong>
                {applySetModal.pkg.themes?.length > 0
                  ? ` và ${applySetModal.pkg.themes.length} Themes`
                  : ''}{' '}
                trong bộ gói này:
              </div>

              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', fontSize: '12px' }}>
                <span style={{ color: 'var(--text-muted)' }}>
                  Đã chọn: <strong>{applySetModal.websiteIds.length}</strong> / {instances.length} website
                </span>
                <div style={{ display: 'inline-flex', gap: '10px' }}>
                  <button
                    type="button"
                    onClick={() =>
                      setApplySetModal(prev => ({ ...prev, websiteIds: instances.map(i => i.id) }))
                    }
                    style={{ border: 'none', background: 'transparent', color: 'var(--primary)', padding: 0, cursor: 'pointer', fontSize: '12px', boxShadow: 'none' }}
                  >
                    Chọn tất cả
                  </button>
                  <button
                    type="button"
                    onClick={() => setApplySetModal(prev => ({ ...prev, websiteIds: [] }))}
                    style={{ border: 'none', background: 'transparent', color: 'var(--text-muted)', padding: 0, cursor: 'pointer', fontSize: '12px', boxShadow: 'none' }}
                  >
                    Bỏ chọn
                  </button>
                </div>
              </div>

              <div
                style={{
                  border: '1px solid var(--border)',
                  borderRadius: '10px',
                  maxHeight: '240px',
                  overflowY: 'auto',
                  background: 'var(--surface-alt)',
                }}
              >
                {instances.map((inst, idx) => {
                  const checked = applySetModal.websiteIds.includes(inst.id);
                  return (
                    <label
                      key={inst.id}
                      style={{
                        display: 'flex',
                        flexDirection: 'row',
                        alignItems: 'center',
                        gap: '10px',
                        padding: '10px 14px',
                        margin: 0,
                        cursor: 'pointer',
                        borderBottom: idx < instances.length - 1 ? '1px solid var(--border)' : 'none',
                      }}
                    >
                      <input
                        type="checkbox"
                        checked={checked}
                        onChange={() =>
                          setApplySetModal(prev => ({
                            ...prev,
                            websiteIds: checked
                              ? prev.websiteIds.filter(id => id !== inst.id)
                              : [...prev.websiteIds, inst.id],
                          }))
                        }
                        style={{ width: '16px', height: '16px', margin: 0, cursor: 'pointer' }}
                      />
                      <div>
                        <strong style={{ fontSize: '13.5px', color: 'var(--text)' }}>
                          {inst.domain}{inst.subpath || ''}
                        </strong>
                        <div style={{ fontSize: '11.5px', color: 'var(--text-muted)' }}>
                          {inst.site_title}
                        </div>
                      </div>
                    </label>
                  );
                })}
              </div>
            </div>
            <div className="modal-footer">
              <button type="button" className="secondary-light" onClick={() => setApplySetModal(null)}>
                Hủy
              </button>
              <button
                type="button"
                disabled={applySetModal.websiteIds.length === 0 || !!busyAction}
                onClick={handleConfirmApplySet}
                style={{
                  background: 'var(--primary)',
                  color: '#fff',
                  border: 'none',
                  padding: '0 16px',
                  height: '36px',
                  borderRadius: '8px',
                  fontWeight: 600,
                  opacity: applySetModal.websiteIds.length === 0 || !!busyAction ? 0.55 : 1,
                }}
              >
                Cài đặt cho {applySetModal.websiteIds.length} website
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
