import React, { useState, useEffect } from 'react';
import {
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
  Wrench,
  X,
  Zap,
} from 'lucide-react';
import { useAppContext, WordPressIcon } from '../contexts/AppContext.jsx';
import { t } from '../i18n.js';

export function WordPressManagerPage({ request, websites = [], openWebsiteFileManager, navigateToPage }) {
  const { loading, setError, setNotice, phpVersions, loadPhpVersions } = useAppContext();
  const installedPhpList = phpVersions?.installed?.length ? phpVersions.installed : ['8.4', '8.3', '8.2', '8.1'];

  // Global tabs: 'instances' | 'plugins' | 'themes' | 'sets'
  const [globalTab, setGlobalTab] = useState('instances');
  // View mode for instances: 'expanded' | 'grid' | 'list'
  const [viewMode, setViewMode] = useState('expanded');
  const [searchQuery, setSearchQuery] = useState('');

  const [instances, setInstances] = useState([]);
  const [packageSets, setPackageSets] = useState([]);
  const [allWebsites, setAllWebsites] = useState(websites || []);
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
  const [maintenanceMsgDraft, setMaintenanceMsgDraft] = useState('');

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

  // WordPress.org Plugin / Theme catalog modal
  const [wporgModal, setWporgModal] = useState(null); // { kind: 'plugins' | 'themes', instance }
  const [wporgQuery, setWporgQuery] = useState('');
  const [wporgResults, setWporgResults] = useState([]);
  const [wporgSearching, setWporgSearching] = useState(false);

  // Create Package Set modal
  const [showCreateSetModal, setShowCreateSetModal] = useState(false);
  const [newSetForm, setNewSetForm] = useState({
    name: '',
    description: '',
    pluginsText: 'classic-editor, wordpress-seo, litespeed-cache',
    themesText: 'astra',
  });

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
        if (Array.isArray(data.sets)) setPackageSets(data.sets);
        if (Array.isArray(data.websites) && data.websites.length > 0) {
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
  }

  function getSubTab(instId) {
    return instanceTabMap[instId] || 'overview';
  }

  async function selectSubTab(inst, tabKey) {
    setInstanceTabMap(prev => ({ ...prev, [inst.id]: tabKey }));
    if (tabKey === 'plugins' && !instancePluginsMap[inst.id]) {
      const res = await request(`/wordpress-manager/instances/${inst.id}/plugins`, { silent: true });
      if (res?.plugins) {
        setInstancePluginsMap(prev => ({ ...prev, [inst.id]: res.plugins }));
      }
    }
    if (tabKey === 'themes' && !instanceThemesMap[inst.id]) {
      const res = await request(`/wordpress-manager/instances/${inst.id}/themes`, { silent: true });
      if (res?.themes) {
        setInstanceThemesMap(prev => ({ ...prev, [inst.id]: res.themes }));
      }
    }
    if (tabKey === 'security' && !securityCheckedMap[inst.id]) {
      const allIds = (inst.security_checks || []).map(c => c.id);
      setSecurityCheckedMap(prev => ({ ...prev, [inst.id]: allIds }));
    }
  }

  function openInstallDialog() {
    const firstSite = allWebsites[0] || websites[0];
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

  async function handlePluginAction(inst, action, slug = null, activateAfterInstall = true) {
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
    } finally {
      setBusyAction('');
    }
  }

  async function handleThemeAction(inst, action, slug = null, activateAfterInstall = false) {
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

  async function handleSecurityAction(inst, action, checkIds = []) {
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
        setNotice(res.message);
      }
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
      }
    } finally {
      setBusyAction('');
    }
  }

  async function handleDetachInstance(inst) {
    if (!window.confirm(`Ngắt liên kết ${inst.domain} khỏi WordPress Manager? (Toàn bộ file và Database vẫn được giữ nguyên 100%)`)) {
      return;
    }
    const res = await request(`/wordpress-manager/instances/${inst.id}/detach`, { method: 'POST' });
    if (res) {
      setNotice(res.message);
      await loadToolkitData(true);
    }
  }

  async function handleRemoveInstance(inst) {
    if (!window.confirm(`Gỡ cài đặt WordPress khỏi ${inst.domain}?`)) {
      return;
    }
    const dropDb = window.confirm('Bạn có muốn xóa luôn Database MySQL đi kèm của website này không? (Bấm OK để xóa cả DB, Cancel để giữ lại DB)');
    const res = await request(
      `/wordpress-manager/instances/${inst.id}/remove?drop_database=${dropDb ? 'true' : 'false'}`,
      { method: 'POST' }
    );
    if (res) {
      setNotice(res.message);
      await loadToolkitData(true);
    }
  }

  async function handleBulkAction(kind) {
    const targets = selectedIds.length > 0 ? instances.filter(i => selectedIds.includes(i.id)) : instances;
    if (targets.length === 0) return;

    if (kind === 'updates') {
      for (const inst of targets) {
        await handleToolAction(inst, { action: 'update_core' });
        await handlePluginAction(inst, 'update_all');
      }
      setNotice(`Đã cập nhật đồng loạt ${targets.length} website WordPress.`);
    } else if (kind === 'security') {
      for (const inst of targets) {
        await handleSecurityAction(inst, 'secure', []);
      }
      setNotice(`Đã áp dụng 9 lớp bảo mật Hardening cho ${targets.length} website WordPress.`);
    } else if (kind === 'detach') {
      if (selectedIds.length === 0) {
        setError('Vui lòng tích chọn ít nhất 1 website để Detach.');
        return;
      }
      for (const inst of targets) {
        await request(`/wordpress-manager/instances/${inst.id}/detach`, { method: 'POST' });
      }
      setSelectedIds([]);
      await loadToolkitData(true);
    } else if (kind === 'remove') {
      if (selectedIds.length === 0) {
        setError('Vui lòng tích chọn ít nhất 1 website để gỡ bỏ.');
        return;
      }
      if (!window.confirm(`Xác nhận gỡ bỏ WordPress trên ${targets.length} website đã chọn?`)) return;
      for (const inst of targets) {
        await request(`/wordpress-manager/instances/${inst.id}/remove?drop_database=false`, { method: 'POST' });
      }
      setSelectedIds([]);
      await loadToolkitData(true);
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

  async function handleApplySetToAll(pkgSet) {
    if (instances.length === 0) {
      setError('Chưa có website WordPress nào để áp dụng bộ cài.');
      return;
    }
    const targetIds = selectedIds.length > 0 ? selectedIds : instances.map(i => i.id);
    const res = await request(
      '/wordpress-manager/sets/apply',
      {
        method: 'POST',
        body: JSON.stringify({
          set_id: pkgSet.id,
          website_ids: targetIds,
        }),
      },
      `Đang cài đặt bộ gói '${pkgSet.name}'...`
    );
    if (res) {
      setNotice(res.message);
      await loadToolkitData(true);
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
        aria-checked={checked}
        disabled={disabled}
        onClick={onChange}
        style={{
          position: 'relative',
          width: '44px',
          height: '24px',
          minHeight: '24px',
          borderRadius: '999px',
          padding: 0,
          border: checked ? '1px solid var(--primary)' : '1px solid var(--border)',
          background: checked ? 'var(--primary)' : 'var(--surface-alt)',
          cursor: disabled ? 'not-allowed' : 'pointer',
          transition: 'all 0.18s ease',
          display: 'inline-flex',
          alignItems: 'center',
          flexShrink: 0,
          boxShadow: 'none',
        }}
      >
        <span
          style={{
            width: '18px',
            height: '18px',
            borderRadius: '50%',
            background: '#ffffff',
            boxShadow: '0 1px 3px rgba(0,0,0,0.2)',
            transform: checked ? 'translateX(21px)' : 'translateX(3px)',
            transition: 'transform 0.18s ease',
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
            <label
              style={{
                display: 'inline-flex',
                alignItems: 'center',
                gap: '6px',
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
                style={{ width: '15px', height: '15px' }}
              />
              <span style={{ color: 'var(--text-secondary)', fontWeight: 500 }}>
                {selectedIds.length > 0 ? `Đã chọn (${selectedIds.length})` : 'Chọn tất cả'}
              </span>
            </label>

            <button
              type="button"
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
              { id: 'expanded', title: 'Plesk Detailed Card View', icon: Server },
              { id: 'grid', title: 'Compact 2-Column Card Grid', icon: LayoutGrid },
              { id: 'list', title: 'Compact Table List View', icon: List },
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
                  onClick={openInstallDialog}
                  style={{
                    height: '38px',
                    padding: '0 18px',
                    background: 'var(--primary)',
                    color: '#fff',
                    border: 'none',
                    borderRadius: '8px',
                    fontWeight: 600,
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
                      <input type="checkbox" checked={allSelected} onChange={toggleSelectAll} />
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
                const instPlugins = instancePluginsMap[inst.id] || [];
                const instThemes = instanceThemesMap[inst.id] || [];
                const checkedSecIds =
                  securityCheckedMap[inst.id] || (inst.security_checks || []).map(c => c.id);

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
                          title="Ngắt liên kết khỏi WordPress Toolkit (Giữ nguyên file & DB)"
                          style={{ height: '32px', minHeight: '32px', padding: '0 9px' }}
                        >
                          <Unlink size={13} />
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
                                    className={`badge ${
                                      inst.plugins_updatable_count > 0 || inst.update_available ? 'warn' : 'ok'
                                    }`}
                                  >
                                    {inst.plugins_updatable_count > 0
                                      ? `${inst.plugins_updatable_count} available`
                                      : 'installed'}
                                  </span>
                                  <button
                                    type="button"
                                    onClick={() => selectSubTab(inst, 'plugins')}
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
                                      onClick={() => {
                                        setMaintenanceModalInst(inst);
                                        setMaintenanceMsgDraft(inst.maintenance_message || '');
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
                                      () =>
                                        handleToggleSwitch(
                                          inst,
                                          'password_protection',
                                          !inst.toggles?.password_protection
                                        )
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
                                        </td>
                                        <td style={{ padding: '9px 14px', textAlign: 'right' }}>
                                          {th.status !== 'active' && (
                                            <button
                                              type="button"
                                              className="secondary-light"
                                              onClick={() => handleThemeAction(inst, 'delete', th.slug)}
                                              style={{ height: '28px', minHeight: '28px', padding: '0 8px', color: '#ef4444' }}
                                            >
                                              <Trash2 size={13} />
                                            </button>
                                          )}
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
                            <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
                              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '8px' }}>
                                <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
                                  <button
                                    type="button"
                                    onClick={() => handleSecurityAction(inst, 'secure', checkedSecIds)}
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
                                    <ShieldCheck size={14} /> Secure Selected ({checkedSecIds.length})
                                  </button>
                                  <button
                                    type="button"
                                    className="secondary-light"
                                    onClick={() => handleSecurityAction(inst, 'revert', checkedSecIds)}
                                    style={{ height: '32px', minHeight: '32px', padding: '0 12px', fontSize: '12.5px' }}
                                  >
                                    Revert
                                  </button>
                                  <button
                                    type="button"
                                    className="secondary-light"
                                    onClick={() => handleSecurityAction(inst, 'verify_checksums')}
                                    style={{ height: '32px', minHeight: '32px', padding: '0 12px', fontSize: '12.5px' }}
                                  >
                                    <CheckCircle size={13} /> Verify Core Checksums
                                  </button>
                                  <button
                                    type="button"
                                    className="secondary-light"
                                    onClick={() => handleSecurityAction(inst, 'reinstall_core')}
                                    style={{ height: '32px', minHeight: '32px', padding: '0 12px', fontSize: '12.5px' }}
                                  >
                                    <RotateCcw size={13} /> Reinstall Clean Core
                                  </button>
                                </div>
                              </div>

                              <div style={{ border: '1px solid var(--border)', borderRadius: '10px', overflow: 'hidden' }}>
                                {(inst.security_checks || []).map(chk => {
                                  const isChecked = checkedSecIds.includes(chk.id);
                                  return (
                                    <label
                                      key={chk.id}
                                      style={{
                                        display: 'flex',
                                        justifyContent: 'space-between',
                                        alignItems: 'center',
                                        padding: '10px 14px',
                                        borderBottom: '1px solid var(--border)',
                                        cursor: 'pointer',
                                        gap: '12px',
                                      }}
                                    >
                                      <div style={{ display: 'flex', alignItems: 'flex-start', gap: '10px' }}>
                                        <input
                                          type="checkbox"
                                          checked={isChecked}
                                          onChange={() => {
                                            setSecurityCheckedMap(prev => {
                                              const cur = prev[inst.id] || [];
                                              return {
                                                ...prev,
                                                [inst.id]: cur.includes(chk.id)
                                                  ? cur.filter(x => x !== chk.id)
                                                  : [...cur, chk.id],
                                              };
                                            });
                                          }}
                                          style={{ marginTop: '3px' }}
                                        />
                                        <div>
                                          <strong style={{ fontSize: '13px', color: 'var(--text)', display: 'block' }}>
                                            {chk.title}
                                          </strong>
                                          <span style={{ fontSize: '12px', color: 'var(--text-muted)' }}>
                                            {chk.description}
                                          </span>
                                        </div>
                                      </div>
                                      <span className={`badge ${chk.passed ? 'ok' : chk.critical ? 'bad' : 'warn'}`}>
                                        {chk.passed ? 'Secured' : chk.critical ? 'Critical' : 'Warning'}
                                      </span>
                                    </label>
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
                                    <strong>{inst.database?.table_count ?? 12}</strong> bảng ({inst.database?.size_mb ?? 2.4} MB)
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
                                  <label style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                                    <span>WP_MEMORY_LIMIT</span>
                                    <select
                                      value={inst.wp_memory_limit || '256M'}
                                      onChange={e =>
                                        handleToolAction(inst, {
                                          action: 'update_config',
                                          wp_memory_limit: e.target.value,
                                        })
                                      }
                                      style={{ width: '130px', height: '32px' }}
                                    >
                                      <option value="128M">128M</option>
                                      <option value="256M">256M (Khuyên dùng)</option>
                                      <option value="512M">512M</option>
                                      <option value="1024M">1024M</option>
                                    </select>
                                  </label>

                                  <label style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                                    <span>WP_MAX_MEMORY_LIMIT</span>
                                    <select
                                      value={inst.wp_max_memory_limit || '512M'}
                                      onChange={e =>
                                        handleToolAction(inst, {
                                          action: 'update_config',
                                          wp_max_memory_limit: e.target.value,
                                        })
                                      }
                                      style={{ width: '130px', height: '32px' }}
                                    >
                                      <option value="256M">256M</option>
                                      <option value="512M">512M</option>
                                      <option value="1024M">1024M</option>
                                    </select>
                                  </label>

                                  <label style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                                    <span>WP_POST_REVISIONS</span>
                                    <select
                                      value={String(inst.wp_post_revisions || '10')}
                                      onChange={e =>
                                        handleToolAction(inst, {
                                          action: 'update_config',
                                          wp_post_revisions: e.target.value,
                                        })
                                      }
                                      style={{ width: '130px', height: '32px' }}
                                    >
                                      <option value="false">Tắt lưu bản nháp</option>
                                      <option value="5">Tối đa 5 bản</option>
                                      <option value="10">Tối đa 10 bản</option>
                                      <option value="25">Tối đa 25 bản</option>
                                    </select>
                                  </label>
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

                <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '8px', paddingTop: '10px', borderTop: '1px solid var(--border)' }}>
                  <button
                    type="button"
                    onClick={() => handleApplySetToAll(pkg)}
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
                    {allWebsites.map(w => (
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
              <div style={{ display: 'flex', gap: '16px', flexWrap: 'wrap', fontSize: '13px' }}>
                <label style={{ display: 'inline-flex', alignItems: 'center', gap: '8px' }}>
                  <input
                    type="checkbox"
                    checked={!!debugModalInst.toggles?.debugging}
                    onChange={e => {
                      const v = e.target.checked;
                      handleToggleSwitch(debugModalInst, 'debugging', v);
                      setDebugModalInst(prev => ({
                        ...prev,
                        toggles: { ...prev.toggles, debugging: v },
                      }));
                    }}
                  />
                  <span>WP_DEBUG (Bật chế độ gỡ lỗi)</span>
                </label>

                <label style={{ display: 'inline-flex', alignItems: 'center', gap: '8px' }}>
                  <input
                    type="checkbox"
                    checked={!!debugModalInst.toggles?.debug_log}
                    onChange={e => {
                      const v = e.target.checked;
                      handleToggleSwitch(debugModalInst, 'debug_log', v, {
                        debugging: debugModalInst.toggles?.debugging,
                      });
                      setDebugModalInst(prev => ({
                        ...prev,
                        toggles: { ...prev.toggles, debug_log: v },
                      }));
                    }}
                  />
                  <span>WP_DEBUG_LOG (Ghi vào wp-content/debug.log)</span>
                </label>
              </div>

              <div>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '6px' }}>
                  <strong style={{ fontSize: '13px' }}>Nội dung wp-content/debug.log:</strong>
                  <button
                    type="button"
                    className="secondary-light"
                    onClick={handleClearDebugLog}
                    style={{ height: '28px', minHeight: '28px', padding: '0 10px', fontSize: '12px' }}
                  >
                    Dọn sạch Log
                  </button>
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

      {/* MODAL 6: MAINTENANCE MODE SETUP */}
      {maintenanceModalInst && (
        <div className="modal-overlay" onClick={() => setMaintenanceModalInst(null)}>
          <div className="modal" style={{ maxWidth: '500px', width: '100%' }} onClick={e => e.stopPropagation()}>
            <div className="modal-header">
              <h3 style={{ margin: 0, fontSize: '16px' }}>
                Cấu hình Trang bảo trì ({maintenanceModalInst.domain})
              </h3>
              <button type="button" className="icon-button" onClick={() => setMaintenanceModalInst(null)}>
                <X size={18} />
              </button>
            </div>
            <div className="modal-body" style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
              <label>
                <span>Thông điệp hiển thị cho khách truy cập khi đang bảo trì</span>
                <textarea
                  rows={3}
                  value={maintenanceMsgDraft}
                  onChange={e => setMaintenanceMsgDraft(e.target.value)}
                />
              </label>
            </div>
            <div className="modal-footer">
              <button type="button" className="secondary-light" onClick={() => setMaintenanceModalInst(null)}>
                Hủy
              </button>
              <button
                type="button"
                onClick={async () => {
                  await handleToggleSwitch(
                    maintenanceModalInst,
                    'maintenance_mode',
                    true,
                    { maintenance_message: maintenanceMsgDraft }
                  );
                  setMaintenanceModalInst(null);
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
                Lưu & Bật chế độ bảo trì
              </button>
            </div>
          </div>
        </div>
      )}

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
                onClick={async () => {
                  await handleToggleSwitch(passwordAuthModalInst, 'password_protection', true, {
                    basic_auth_user: basicAuthForm.user,
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
                }}
              >
                Lưu & Bật bảo vệ mật khẩu
              </button>
            </div>
          </div>
        </div>
      )}

      {/* MODAL 8: CLONE / SYNC MODAL */}
      {cloneSyncModal && (
        <div className="modal-overlay" onClick={() => setCloneSyncModal(null)}>
          <div className="modal" style={{ maxWidth: '520px', width: '100%' }} onClick={e => e.stopPropagation()}>
            <div className="modal-header">
              <h3 style={{ margin: 0, fontSize: '16px' }}>
                {cloneSyncModal.mode === 'clone'
                  ? `Nhân bản Website (Clone): ${cloneSyncModal.instance.domain}`
                  : `Đồng bộ dữ liệu (Sync): ${cloneSyncModal.instance.domain}`}
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
            </div>
            <div className="modal-footer">
              <button type="button" className="secondary-light" onClick={() => setCloneSyncModal(null)}>
                Hủy
              </button>
              <button
                type="button"
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
                }}
              >
                {cloneSyncModal.mode === 'clone' ? 'Bắt đầu Nhân bản (Clone)' : 'Bắt đầu Đồng bộ (Sync)'}
              </button>
            </div>
          </div>
        </div>
      )}

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
                  onClick={async () => {
                    await handleToolAction(snapshotModalInst, {
                      action: 'create_snapshot',
                      snapshot_label: snapshotLabelDraft,
                    });
                    await loadToolkitData(true);
                    setSnapshotModalInst(null);
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
                                onClick={() =>
                                  handleToolAction(snapshotModalInst, {
                                    action: 'restore_snapshot',
                                    snapshot_id: sn.id,
                                  })
                                }
                                style={{ height: '28px', minHeight: '28px', padding: '0 10px', fontSize: '12px' }}
                              >
                                Khôi phục
                              </button>
                              <button
                                type="button"
                                className="secondary-light"
                                onClick={async () => {
                                  await handleToolAction(snapshotModalInst, {
                                    action: 'delete_snapshot',
                                    snapshot_id: sn.id,
                                  });
                                  setSnapshotModalInst(null);
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
          </div>
        </div>
      )}

      {/* MODAL 10: SEARCH & REPLACE URL IN DATABASE */}
      {searchReplaceModalInst && (
        <div className="modal-overlay" onClick={() => setSearchReplaceModalInst(null)}>
          <div className="modal" style={{ maxWidth: '500px', width: '100%' }} onClick={e => e.stopPropagation()}>
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
              <label style={{ display: 'inline-flex', alignItems: 'center', gap: '8px', fontSize: '13px' }}>
                <input
                  type="checkbox"
                  checked={searchReplaceForm.dry_run}
                  onChange={e => setSearchReplaceForm(prev => ({ ...prev, dry_run: e.target.checked }))}
                />
                <span>Chạy thử (Dry-run) kiểm tra trước khi ghi vào Database</span>
              </label>
            </div>
            <div className="modal-footer">
              <button type="button" className="secondary-light" onClick={() => setSearchReplaceModalInst(null)}>
                Đóng
              </button>
              <button
                type="button"
                onClick={async () => {
                  await handleToolAction(searchReplaceModalInst, {
                    action: 'search_replace',
                    ...searchReplaceForm,
                  });
                  if (!searchReplaceForm.dry_run) setSearchReplaceModalInst(null);
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
                Thực thi Search & Replace
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
    </div>
  );
}
