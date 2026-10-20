<<<<<<< Updated upstream
import React, { useState, useEffect } from 'react';
import {
  ArrowLeft, Boxes, Cpu, Globe, Pencil, Plus, RefreshCw, Save, ShieldCheck, Trash2, X
=======
import React, { useState, useEffect, useRef } from 'react';
import { createPortal } from 'react-dom';
import {
  ArrowLeft, Boxes, Check, CloudDownload, Cpu, Eye, EyeOff, FileSpreadsheet, FileText,
  FolderUp, Globe, Pencil, Plus, RefreshCw, Save, Server, ShieldCheck, Sparkles, Trash2, Upload, X
>>>>>>> Stashed changes
} from 'lucide-react';
import { EmptyState } from '../components/EmptyState.jsx';
import { useAppContext } from '../contexts/AppContext.jsx';
import { t } from '../i18n.js';

export const DEFAULT_PKG_STATE = {
  name: '',
  is_reseller_package: false,
  reseller_user_limit: 10,
  reseller_overselling_enabled: false,
  storage_limit_mb: 5120,
  bandwidth_limit_mb: 102400,
  website_limit: 5,
  subdomain_limit: 20,
  alias_limit: 10,
  domain_pointers_limit: 10,
  inode_limit: 250000,
  sftp_accounts_limit: 5,
  ftp_accounts_limit: 5,
  backup_retention_days: 14,
  ips_limit: 0,
  database_limit: 5,
  mysql_storage_limit_mb: 2048,
  mysql_max_db_size_mb: 1024,
  mail_accounts_limit: 10,
  email_forwarders_limit: 20,
  autoresponders_limit: 10,
  mailing_lists_limit: 0,
  node_apps_limit: 0,
  node_app_memory_mb: 512,
  cpu_quota_percent: 100,
  tasks_max: 80,
  memory_high_mb: 768,
  memory_max_mb: 1024,
  io_read_mbps: 100,
  io_write_mbps: 100,
  iops_read_max: 1000,
  iops_write_max: 1000,
  php_access: true,
  wordpress_enabled: true,
  waf_enabled: true,
  ssl_access: true,
  terminal_enabled: false,
  ssh_access: false,
  ssh_access_users: false,
  redis_access: true,
  cron_jobs: true,
  dns_control: true,
  git_access: true,
  spamassassin: true,
  catch_all_email: false,
  login_keys: true,
  system_info: false,
  cgi_access: false,
  anonymous_ftp: false,
  personal_dns_mode: 'off',
  share_server_ip: true,
};

function normalizePackageToForm(pkg) {
  const aliasVal = pkg.alias_limit ?? pkg.domain_pointers_limit ?? 0;
  const sftpVal = pkg.sftp_accounts_limit ?? pkg.ftp_accounts_limit ?? 0;
  const termVal = Boolean(pkg.terminal_enabled ?? pkg.ssh_access ?? false);
  return {
    ...DEFAULT_PKG_STATE,
    ...pkg,
    alias_limit: aliasVal,
    domain_pointers_limit: aliasVal,
    sftp_accounts_limit: sftpVal,
    ftp_accounts_limit: sftpVal,
    terminal_enabled: termVal,
    ssh_access: termVal,
    node_apps_limit: Number(pkg.node_apps_limit || 0),
    node_app_memory_mb: Math.max(64, Number(pkg.node_app_memory_mb || 512)),
    backup_retention_days: Math.max(1, Number(pkg.backup_retention_days || 14)),
  };
}

function formatMb(val) {
  const n = Number(val || 0);
  if (n <= 0) return t('Unlimited');
  if (n >= 1024 && n % 1024 === 0) return `${n / 1024} GB`;
  return `${n.toLocaleString()} MB`;
}

function formatCount(val) {
  const n = Number(val || 0);
  return n <= 0 ? '∞' : n.toLocaleString();
}

export function PackagesPage({ mode = 'users' }) {
  const {
    packages, users, request, setNotice, setError, loading, loadPackages, loadUsers,
  } = useAppContext();

  const [packageTab, setPackageTab] = useState('list');
  const [pkgSearch, setPkgSearch] = useState('');
  const [newPackage, setNewPackage] = useState({ ...DEFAULT_PKG_STATE });
  const [editingPackageId, setEditingPackageId] = useState(null);
  const [editingPackageForm, setEditingPackageForm] = useState({ ...DEFAULT_PKG_STATE });

<<<<<<< Updated upstream
=======
  // Import Modal State
  const [showImportModal, setShowImportModal] = useState(false);
  const [importPanel, setImportPanel] = useState('directadmin'); // directadmin | cpanel | plesk
  const [importMethod, setImportMethod] = useState('file'); // file | remote
  const [rawImportText, setRawImportText] = useState('');
  const [parsedPackages, setParsedPackages] = useState([]);
  const [conflictMode, setConflictMode] = useState('overwrite'); // overwrite | skip | rename
  const [remoteForm, setRemoteForm] = useState({
    host_url: '',
    username: 'admin',
    secret: '',
    auth_type: 'password',
    verify_ssl: false,
  });
  const [showRemoteSecret, setShowRemoteSecret] = useState(false);
  const fileInputRef = useRef(null);

>>>>>>> Stashed changes
  useEffect(() => {
    setPackageTab('list');
    setEditingPackageId(null);
    if (!packages || packages.length === 0) {
      if (typeof loadPackages === 'function') loadPackages();
    }
    if ((!users || users.length === 0) && typeof loadUsers === 'function') {
      loadUsers();
    }
  }, [mode]);

  function sanitizePayload(formObj) {
    const clean = { ...formObj };
    const intFields = [
      'storage_limit_mb', 'bandwidth_limit_mb', 'website_limit', 'subdomain_limit',
      'alias_limit', 'domain_pointers_limit', 'inode_limit', 'sftp_accounts_limit',
      'ftp_accounts_limit', 'backup_retention_days', 'ips_limit', 'reseller_user_limit',
      'database_limit', 'mysql_storage_limit_mb', 'mysql_max_db_size_mb',
      'mail_accounts_limit', 'email_forwarders_limit', 'autoresponders_limit', 'mailing_lists_limit',
      'node_apps_limit', 'node_app_memory_mb',
      'cpu_quota_percent', 'tasks_max', 'memory_high_mb', 'memory_max_mb',
      'io_read_mbps', 'io_write_mbps', 'iops_read_max', 'iops_write_max',
    ];
    for (const key of intFields) {
      clean[key] = Math.max(0, parseInt(clean[key], 10) || 0);
    }
    clean.domain_pointers_limit = clean.alias_limit;
    clean.ftp_accounts_limit = clean.sftp_accounts_limit;
    clean.ssh_access = Boolean(clean.terminal_enabled);
    clean.terminal_enabled = Boolean(clean.terminal_enabled);
    clean.node_app_memory_mb = Math.min(16384, Math.max(64, clean.node_app_memory_mb || 512));
    clean.backup_retention_days = Math.min(365, Math.max(1, clean.backup_retention_days || 14));
    clean.is_reseller_package = mode === 'resellers';
    return clean;
  }

  async function createPackage(e) {
    if (e && e.preventDefault) e.preventDefault();
    if (!newPackage.name.trim()) {
      setError(t('Please enter a package name.'));
      return;
    }
    const payload = sanitizePayload(newPackage);
    const created = await request('/packages', {
      method: 'POST',
      body: JSON.stringify(payload),
    }, t('Creating package...'));
    if (created?.id) {
      await loadPackages();
      setNewPackage({ ...DEFAULT_PKG_STATE });
      setNotice(t('Package created successfully.'));
      setPackageTab('list');
    }
  }

  function startEditingPackage(pkg) {
    setEditingPackageId(pkg.id);
    setEditingPackageForm(normalizePackageToForm(pkg));
    setPackageTab('create');
  }

  function cancelEditingPackage() {
    setEditingPackageId(null);
    setEditingPackageForm({ ...DEFAULT_PKG_STATE });
    setPackageTab('list');
  }

  async function savePackageEdit(packageId) {
    if (!editingPackageForm.name.trim()) {
      setError(t('Please enter a package name.'));
      return;
    }
    const payload = sanitizePayload(editingPackageForm);
    const updated = await request(`/packages/${packageId}`, {
      method: 'PATCH',
      body: JSON.stringify(payload),
    }, t('Updating package...'));
    if (updated?.id) {
      await loadPackages();
      cancelEditingPackage();
      setNotice(t('Package updated successfully.'));
    }
  }

  async function deletePackage(pkg) {
    if (!confirm(t('Delete package "{name}"?', { name: pkg.name }))) return;
    const res = await request(`/packages/${pkg.id}`, { method: 'DELETE' }, t('Deleting package...'));
    if (res) {
      await loadPackages();
      if (editingPackageId === pkg.id) cancelEditingPackage();
      setNotice(t('Package deleted.'));
    }
  }

  const isEdit = Boolean(editingPackageId);
  const currentForm = isEdit ? editingPackageForm : newPackage;
  const setCurrentForm = isEdit ? setEditingPackageForm : setNewPackage;

  function renderQuotaRow({
    label,
    field,
    unit = '',
    defaultFallback = 10,
    min = 0,
    max = undefined,
    allowUnlimited = true,
    zeroLabel = t('Unlimited'),
  }) {
    const rawVal = Number(currentForm[field] ?? 0);
    const isZeroChecked = allowUnlimited && rawVal === 0;

    return (
      <div className="pkg-field-row" key={field}>
        <span className="pkg-field-label">{label}</span>
        <div className="pkg-input-unit-wrap">
          <input
            type="number"
            min={allowUnlimited ? (isZeroChecked ? 0 : 1) : min}
            max={max}
            disabled={isZeroChecked}
            value={isZeroChecked ? '' : rawVal}
            placeholder={isZeroChecked ? zeroLabel : String(defaultFallback)}
            onChange={e => {
              const v = e.target.value === '' ? (allowUnlimited ? 0 : min) : Math.max(min, parseInt(e.target.value, 10) || 0);
              setCurrentForm(prev => ({ ...prev, [field]: max ? Math.min(max, v) : v }));
            }}
          />
          {unit && <span className="pkg-unit-tag">{unit}</span>}
        </div>
        {allowUnlimited ? (
          <label className="pkg-unlim-check">
            <input
              type="checkbox"
              checked={isZeroChecked}
              onChange={e => {
                setCurrentForm(prev => ({
                  ...prev,
                  [field]: e.target.checked ? 0 : defaultFallback,
                }));
              }}
            />
            <span>{zeroLabel}</span>
          </label>
        ) : (
          <span style={{ fontSize: '12px', color: 'var(--text-muted)' }}>
            {min}–{max || 365} {unit}
          </span>
        )}
      </div>
    );
  }

  function renderFeatureCheck(field, label, onChangeCustom) {
    const checked = Boolean(currentForm[field]);
    return (
      <label className="pkg-feature-check" key={field}>
        <input
          type="checkbox"
          checked={checked}
          onChange={e => {
            if (onChangeCustom) {
              onChangeCustom(e.target.checked);
            } else {
              setCurrentForm(prev => ({ ...prev, [field]: e.target.checked }));
            }
          }}
        />
        <span>{label}</span>
      </label>
    );
  }

  const filteredPackages = (packages || []).filter(p => {
    const matchMode = mode === 'resellers' ? Boolean(p.is_reseller_package) : !p.is_reseller_package;
    const matchSearch = !pkgSearch || p.name.toLowerCase().includes(pkgSearch.toLowerCase());
    return matchMode && matchSearch;
  });

  return (
    <div className="section">
      <div className="section-title" style={{ marginBottom: '16px', alignItems: 'center', flexWrap: 'wrap' }}>
        <div>
          <h2 style={{ margin: 0 }}>
            {mode === 'resellers' ? t('Manage Reseller Packages') : t('Manage User Packages')}
          </h2>
        </div>

        {packageTab === 'list' ? (
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap' }}>
            <input
              type="text"
              placeholder={t('Search packages by name...')}
              value={pkgSearch}
              onChange={e => setPkgSearch(e.target.value)}
              style={{ width: '210px', height: '36px', margin: 0 }}
            />
            <button
              className="secondary-light"
              disabled={!!loading}
              onClick={() => loadPackages()}
            >
              <RefreshCw size={14} /> {t('Refresh')}
            </button>
            <button
              onClick={() => {
                setEditingPackageId(null);
                setNewPackage({ ...DEFAULT_PKG_STATE });
                setPackageTab('create');
              }}
            >
              <Plus size={14} /> {t('Create Package')}
            </button>
          </div>
        ) : (
          <button className="secondary-light" onClick={cancelEditingPackage}>
            <ArrowLeft size={14} /> {t('Back to Package List')}
          </button>
        )}
      </div>

      {packageTab === 'list' && (
        <div className="pkg-list-card">
          {filteredPackages.length === 0 ? (
            <div style={{ padding: '2.5rem' }}>
              <EmptyState icon={Boxes} message={t('No packages found.')} />
            </div>
          ) : (
            <div style={{ overflowX: 'auto', width: '100%' }}>
              <table className="pkg-clean-table">
                <thead>
                  <tr>
                    <th>{t('Package Name')}</th>
                    <th>{t('Disk')} / {t('Bandwidth')}</th>
                    <th>{t('Websites')} & {t('Databases')}</th>
                    <th>{t('Email')} & {t('App Runtimes')}</th>
                    <th>CPU / RAM</th>
                    <th style={{ textAlign: 'right' }}>{t('Actions')}</th>
                  </tr>
                </thead>
                <tbody>
                  {filteredPackages.map(pkg => {
                    const assignedCount = (users || []).filter(
                      u => u.package_id === pkg.id || u.reseller_package_id === pkg.id
                    ).length;
                    const nodeApps = Number(pkg.node_apps_limit || 0);
                    const nodeMem = Number(pkg.node_app_memory_mb || 512);

                    return (
                      <tr key={pkg.id}>
                        <td>
                          <strong style={{ color: 'var(--primary)', fontSize: '14px' }}>{pkg.name}</strong>
                          <span className="muted" style={{ marginLeft: '8px', fontSize: '12px' }}>
                            ({assignedCount} {t('Users')})
                          </span>
                        </td>
                        <td>
                          {formatMb(pkg.storage_limit_mb)} <span className="muted">/</span> {formatMb(pkg.bandwidth_limit_mb)}
                        </td>
                        <td>
                          {formatCount(pkg.website_limit)} {t('Sites')} <span className="muted">·</span> {formatCount(pkg.database_limit)} DBs
                        </td>
                        <td>
                          {formatCount(pkg.mail_accounts_limit)} Mail <span className="muted">·</span>{' '}
                          {nodeApps > 0 ? (
                            <span className="badge ok" style={{ fontSize: '11.5px' }}>
                              {nodeApps} Apps ({nodeMem} MB)
                            </span>
                          ) : (
                            <span className="muted" style={{ fontSize: '12.5px' }}>{t('Apps: Off')}</span>
                          )}
                        </td>
                        <td>
                          {pkg.cpu_quota_percent > 0 ? `${pkg.cpu_quota_percent}%` : '∞'} CPU <span className="muted">·</span>{' '}
                          {formatMb(pkg.memory_max_mb)} RAM
                        </td>
                        <td style={{ textAlign: 'right' }}>
                          <div style={{ display: 'inline-flex', gap: '6px' }}>
                            <button
                              className="secondary-light mini"
                              onClick={() => startEditingPackage(pkg)}
                            >
                              <Pencil size={13} /> {t('Modify')}
                            </button>
                            <button
                              className="danger mini"
                              onClick={() => deletePackage(pkg)}
                            >
                              <Trash2 size={13} /> {t('Delete')}
                            </button>
                          </div>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {packageTab === 'create' && (
        <div className="pkg-editor-shell">
          <div className="pkg-top-bar">
            <div className="pkg-name-inline">
              <label>{t('Package Name')}</label>
              <input
                type="text"
                placeholder={mode === 'resellers' ? t('e.g. Reseller Gold') : t('e.g. Business Hosting')}
                value={currentForm.name}
                onChange={e => setCurrentForm(prev => ({ ...prev, name: e.target.value }))}
              />
            </div>

            <div style={{ display: 'flex', gap: '8px' }}>
              <button type="button" className="secondary-light" onClick={cancelEditingPackage}>
                <X size={14} /> {t('Cancel')}
              </button>
              <button
                type="button"
                disabled={!!loading}
                onClick={isEdit ? () => savePackageEdit(editingPackageId) : createPackage}
              >
                <Save size={14} /> {isEdit ? t('Save Changes') : t('Create Package')}
              </button>
            </div>
          </div>

          <div className="pkg-cards-grid">
            {/* Left Column: Web, Database & Email Quotas */}
            <div className="pkg-card">
              <div className="pkg-card-header">
                <h4 className="pkg-card-title">
                  <Globe size={15} /> {t('Web, Domains & Storage Quotas')}
                </h4>
              </div>
              <div className="pkg-card-body">
                {mode === 'resellers' && (
                  <>
                    {renderQuotaRow({
                      label: t('Max Sub-Accounts (Users)'),
                      field: 'reseller_user_limit',
                      unit: t('Users'),
                      defaultFallback: 25,
                    })}
                    {renderQuotaRow({
                      label: t('Dedicated IPs Quota'),
                      field: 'ips_limit',
                      unit: 'IPs',
                      defaultFallback: 1,
                    })}
                  </>
                )}
                {renderQuotaRow({
                  label: t('Disk Space Quota'),
                  field: 'storage_limit_mb',
                  unit: 'MB',
                  defaultFallback: 5120,
                })}
                {renderQuotaRow({
                  label: t('Monthly Bandwidth'),
                  field: 'bandwidth_limit_mb',
                  unit: 'MB',
                  defaultFallback: 102400,
                })}
                {renderQuotaRow({
                  label: t('Hosted Websites (Domains)'),
                  field: 'website_limit',
                  unit: t('Sites'),
                  defaultFallback: 5,
                })}
                {renderQuotaRow({
                  label: t('Subdomains'),
                  field: 'subdomain_limit',
                  unit: 'Subs',
                  defaultFallback: 20,
                })}
                {renderQuotaRow({
                  label: t('Domain Aliases / Pointers'),
                  field: 'alias_limit',
                  unit: 'Aliases',
                  defaultFallback: 10,
                })}
                {renderQuotaRow({
                  label: t('MySQL Databases'),
                  field: 'database_limit',
                  unit: 'DBs',
                  defaultFallback: 5,
                })}
                {renderQuotaRow({
                  label: t('Total MySQL Storage Quota'),
                  field: 'mysql_storage_limit_mb',
                  unit: 'MB',
                  defaultFallback: 2048,
                })}
                {renderQuotaRow({
                  label: t('Email Accounts (Mailboxes)'),
                  field: 'mail_accounts_limit',
                  unit: 'Mail',
                  defaultFallback: 10,
                })}
                {renderQuotaRow({
                  label: t('Email Forwarders'),
                  field: 'email_forwarders_limit',
                  unit: 'Fwd',
                  defaultFallback: 20,
                })}
                {renderQuotaRow({
                  label: t('SFTP / FTP Sub-Accounts'),
                  field: 'sftp_accounts_limit',
                  unit: 'SFTP',
                  defaultFallback: 5,
                })}
              </div>
            </div>

            {/* Right Column: Application Runtimes & Cgroups v2 Hardware Limits */}
            <div className="pkg-card">
              <div className="pkg-card-header">
                <h4 className="pkg-card-title">
                  <Cpu size={15} /> {t('App Runtimes')} & {t('Hardware (Cgroups v2)')}
                </h4>
              </div>
              <div className="pkg-card-body">
                {renderQuotaRow({
                  label: t('Max Applications (node_apps_limit)'),
                  field: 'node_apps_limit',
                  unit: 'Apps',
                  defaultFallback: 3,
                  allowUnlimited: true,
                  zeroLabel: t('Disabled (0)'),
                })}
                {renderQuotaRow({
                  label: t('Memory Limit per App (node_app_memory_mb)'),
                  field: 'node_app_memory_mb',
                  unit: 'MB',
                  defaultFallback: 512,
                  min: 64,
                  max: 16384,
                  allowUnlimited: false,
                })}
                {renderQuotaRow({
                  label: t('CPU Quota'),
                  field: 'cpu_quota_percent',
                  unit: '%',
                  defaultFallback: 100,
                })}
                {renderQuotaRow({
                  label: t('RAM Hard Limit (memory.max)'),
                  field: 'memory_max_mb',
                  unit: 'MB',
                  defaultFallback: 1024,
                })}
                {renderQuotaRow({
                  label: t('RAM Soft Limit (memory.high)'),
                  field: 'memory_high_mb',
                  unit: 'MB',
                  defaultFallback: 768,
                })}
                {renderQuotaRow({
                  label: t('Max Processes / Tasks (tasks.max)'),
                  field: 'tasks_max',
                  unit: 'PIDs',
                  defaultFallback: 80,
                })}
                {renderQuotaRow({
                  label: t('Disk Read Bandwidth'),
                  field: 'io_read_mbps',
                  unit: 'MB/s',
                  defaultFallback: 100,
                })}
                {renderQuotaRow({
                  label: t('Disk Write Bandwidth'),
                  field: 'io_write_mbps',
                  unit: 'MB/s',
                  defaultFallback: 100,
                })}
                {renderQuotaRow({
                  label: t('Inodes Limit'),
                  field: 'inode_limit',
                  unit: 'Files',
                  defaultFallback: 250000,
                })}
                {renderQuotaRow({
                  label: t('Backup Retention Period'),
                  field: 'backup_retention_days',
                  unit: t('Days'),
                  defaultFallback: 14,
                  min: 1,
                  max: 365,
                  allowUnlimited: false,
                })}
              </div>
            </div>

            {/* Bottom Full-Width Card: Compact Single-Line Checkboxes */}
            <div className="pkg-card pkg-card-full">
              <div className="pkg-card-header">
                <h4 className="pkg-card-title">
                  <ShieldCheck size={15} /> {t('Package Features & Access Permissions')}
                </h4>
              </div>
              <div className="pkg-feature-grid">
                {renderFeatureCheck('php_access', t('PHP Runtime Access'))}
                {renderFeatureCheck('wordpress_enabled', t('WordPress Toolkit'))}
                {renderFeatureCheck('waf_enabled', t('Web Application Firewall (WAF)'))}
                {renderFeatureCheck('ssl_access', t('SSL Certificates'))}
                {renderFeatureCheck('terminal_enabled', t('SSH & Web Terminal Access'), checked =>
                  setCurrentForm(prev => ({
                    ...prev,
                    terminal_enabled: checked,
                    ssh_access: checked,
                  }))
                )}
                {renderFeatureCheck('redis_access', t('Redis Object Cache'))}
                {renderFeatureCheck('cron_jobs', t('Cron Jobs'))}
                {renderFeatureCheck('dns_control', t('DNS Zone Control'))}
                {renderFeatureCheck('git_access', t('Git Deployment'))}
                {renderFeatureCheck('spamassassin', t('Spam Filter Controls'))}
                {renderFeatureCheck('catch_all_email', t('Catch-All Email'))}
                {renderFeatureCheck('login_keys', t('API & Login Keys'))}

                {mode === 'resellers' && (
                  <>
                    {renderFeatureCheck('reseller_overselling_enabled', t('Allow Overselling'))}
                    {renderFeatureCheck('share_server_ip', t('Share Server Main IP'))}
                    {renderFeatureCheck('ssh_access_users', t('Grant SSH to Sub-Users'))}
                    <label className="pkg-feature-check">
                      <input
                        type="checkbox"
                        checked={currentForm.personal_dns_mode === 'on'}
                        onChange={e =>
                          setCurrentForm(prev => ({
                            ...prev,
                            personal_dns_mode: e.target.checked ? 'on' : 'off',
                          }))
                        }
                      />
                      <span>{t('Custom White-Label Nameservers')}</span>
                    </label>
                  </>
                )}
              </div>
            </div>
          </div>
        </div>
      )}
<<<<<<< Updated upstream
=======

      {/* Modal Import Packages from DirectAdmin, cPanel/WHM, Plesk */}
      {showImportModal && typeof document !== 'undefined' && createPortal(
        <div
          className="modal-backdrop pkg-import-modal-backdrop"
          onClick={() => setShowImportModal(false)}
          style={{ zIndex: 9999 }}
        >
          <div
            className="modal-card pkg-import-modal-card"
            onClick={e => e.stopPropagation()}
          >
            <div className="pkg-import-header">
              <div>
                <h3 style={{ margin: 0, display: 'flex', alignItems: 'center', gap: '8px', fontSize: '16px' }}>
                  <Upload size={18} style={{ color: 'var(--primary)' }} />
                  {mode === 'resellers'
                    ? t('Import Reseller Packages (DirectAdmin / cPanel / Plesk)')
                    : t('Import User Packages (DirectAdmin / cPanel / Plesk)')}
                </h3>
                <p className="muted" style={{ margin: '4px 0 0', fontSize: '12.5px' }}>
                  {t('Automatically map quotas, domains, databases, email limits, and permissions into SPanel packages.')}
                </p>
              </div>
              <button
                type="button"
                className="secondary-light mini"
                onClick={() => setShowImportModal(false)}
              >
                <X size={15} />
              </button>
            </div>

            <div className="pkg-import-body">
              {/* Step 1: Select Source Panel */}
              <div>
                <label style={{ display: 'block', fontSize: '12.5px', fontWeight: 700, marginBottom: '8px', color: 'var(--text)' }}>
                  1. {t('Select Source Control Panel')}
                </label>
                <div className="pkg-panel-cards">
                  <div
                    className={`pkg-panel-card ${importPanel === 'directadmin' ? 'active' : ''}`}
                    onClick={() => setImportPanel('directadmin')}
                  >
                    <div className="pkg-panel-card-title">
                      <Server size={15} style={{ color: '#2563eb' }} /> DirectAdmin
                    </div>
                    <div className="pkg-panel-card-desc">
                      {t('Supports .pkg files (/usr/local/directadmin/data/users/*/packages/*.pkg) & DirectAdmin API :2222')}
                    </div>
                  </div>

                  <div
                    className={`pkg-panel-card ${importPanel === 'cpanel' ? 'active' : ''}`}
                    onClick={() => setImportPanel('cpanel')}
                  >
                    <div className="pkg-panel-card-title">
                      <FileSpreadsheet size={15} style={{ color: '#ea580c' }} /> cPanel / WHM
                    </div>
                    <div className="pkg-panel-card-desc">
                      {t('Supports /var/cpanel/packages/* files, WHM listpkgs JSON & WHM API :2087')}
                    </div>
                  </div>

                  <div
                    className={`pkg-panel-card ${importPanel === 'plesk' ? 'active' : ''}`}
                    onClick={() => setImportPanel('plesk')}
                  >
                    <div className="pkg-panel-card-title">
                      <Globe size={15} style={{ color: '#0891b2' }} /> Plesk Obsidian
                    </div>
                    <div className="pkg-panel-card-desc">
                      {t('Supports Plesk Service/Reseller Plan XML, REST JSON (/api/v2/serviceplans) & API :8443')}
                    </div>
                  </div>
                </div>
              </div>

              {/* Step 2: Select Import Method */}
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: '10px' }}>
                <div className="pkg-import-method-tabs">
                  <button
                    type="button"
                    className={`pkg-import-method-tab ${importMethod === 'file' ? 'active' : ''}`}
                    onClick={() => setImportMethod('file')}
                  >
                    <FolderUp size={14} /> {t('Upload Files / Paste Config')}
                  </button>
                  <button
                    type="button"
                    className={`pkg-import-method-tab ${importMethod === 'remote' ? 'active' : ''}`}
                    onClick={() => setImportMethod('remote')}
                  >
                    <CloudDownload size={14} /> {t('Connect Remote Panel API')}
                  </button>
                </div>

                {importMethod === 'file' && (
                  <button
                    type="button"
                    className="secondary-light mini"
                    onClick={handleLoadSample}
                  >
                    <Sparkles size={13} /> {t('Load Sample Template')} ({importPanel === 'directadmin' ? 'DirectAdmin' : importPanel === 'cpanel' ? 'cPanel/WHM' : 'Plesk'})
                  </button>
                )}
              </div>

              {/* Method A: Upload Files or Paste Raw Configuration */}
              {importMethod === 'file' && (
                <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
                  <div className="pkg-upload-dropzone">
                    <div>
                      <strong style={{ fontSize: '13.5px', display: 'block', color: 'var(--text)' }}>
                        {t('Select one or multiple package files from your computer')}
                      </strong>
                      <span className="muted" style={{ fontSize: '12px' }}>
                        {importPanel === 'directadmin' && t('DirectAdmin path: /usr/local/directadmin/data/users/admin/packages/*.pkg')}
                        {importPanel === 'cpanel' && t('cPanel path: /var/cpanel/packages/<package_name> or WHM JSON')}
                        {importPanel === 'plesk' && t('Plesk format: XML <service-plan> / <reseller-plan> or REST JSON')}
                      </span>
                    </div>
                    <div>
                      <input
                        ref={fileInputRef}
                        type="file"
                        multiple
                        accept=".pkg,.conf,.txt,.json,.xml,.plan,*"
                        style={{ display: 'none' }}
                        onChange={handleFileSelect}
                      />
                      <button
                        type="button"
                        onClick={() => fileInputRef.current && fileInputRef.current.click()}
                      >
                        <FolderUp size={14} /> {t('Choose Package Files...')}
                      </button>
                    </div>
                  </div>

                  <div>
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '6px' }}>
                      <label style={{ fontSize: '12.5px', fontWeight: 600, color: 'var(--text-secondary)' }}>
                        {t('Or paste package configuration content (.pkg / key=value / JSON / XML):')}
                      </label>
                      <button
                        type="button"
                        className="secondary-light mini"
                        onClick={() => handleParseRawText()}
                      >
                        <FileText size={13} /> {t('Parse Configuration')}
                      </button>
                    </div>
                    <textarea
                      rows={5}
                      value={rawImportText}
                      onChange={e => setRawImportText(e.target.value)}
                      placeholder={
                        importPanel === 'directadmin'
                          ? '[Business_Pkg]\nquota=10240\nbandwidth=102400\nvdomains=5\nnsubdomains=20\nmysql=10\nnemails=20\nphp=ON\nssl=ON\nssh=OFF'
                          : importPanel === 'cpanel'
                          ? '[cPanel_Gold]\nQUOTA=10240\nBWLIMIT=102400\nMAXADDON=4\nMAXSUB=20\nMAXSQL=10\nMAXPOP=20\nHASSHELL=n'
                          : '<packet><service-plan><get><result><name>Plesk_Pro</name><limits><limit><name>disk_space</name><value>10737418240</value></limit></limits></result></get></service-plan></packet>'
                      }
                      style={{ width: '100%', fontFamily: 'monospace', fontSize: '12.5px', margin: 0 }}
                    />
                  </div>
                </div>
              )}

              {/* Method B: Remote Panel API Connection */}
              {importMethod === 'remote' && (
                <form
                  onSubmit={handleFetchRemotePackages}
                  className="pkg-remote-form-card"
                >
                  <div className="pkg-remote-form-grid">
                    <div>
                      <label style={{ fontSize: '12.5px', fontWeight: 600, display: 'block', marginBottom: '5px', color: 'var(--text)' }}>
                        {t('Remote Panel URL')}
                      </label>
                      <input
                        type="text"
                        placeholder={
                          importPanel === 'directadmin'
                            ? 'https://da.example.com:2222'
                            : importPanel === 'cpanel'
                            ? 'https://whm.example.com:2087'
                            : 'https://plesk.example.com:8443'
                        }
                        value={remoteForm.host_url}
                        onChange={e => setRemoteForm(prev => ({ ...prev, host_url: e.target.value }))}
                        style={{ margin: 0, height: '38px', width: '100%' }}
                      />
                    </div>

                    <div>
                      <label style={{ fontSize: '12.5px', fontWeight: 600, display: 'block', marginBottom: '5px', color: 'var(--text)' }}>
                        {t('Admin / Reseller Username')}
                      </label>
                      <input
                        type="text"
                        placeholder={importPanel === 'cpanel' ? 'root' : 'admin'}
                        value={remoteForm.username}
                        onChange={e => setRemoteForm(prev => ({ ...prev, username: e.target.value }))}
                        style={{ margin: 0, height: '38px', width: '100%' }}
                      />
                    </div>

                    <div>
                      <label style={{ fontSize: '12.5px', fontWeight: 600, display: 'block', marginBottom: '5px', color: 'var(--text)' }}>
                        {t('Authentication Method')}
                      </label>
                      <select
                        value={remoteForm.auth_type}
                        onChange={e => setRemoteForm(prev => ({ ...prev, auth_type: e.target.value }))}
                        style={{ margin: 0, height: '38px', width: '100%' }}
                      >
                        <option value="password">{t('Password / Login Key')}</option>
                        <option value="token">{t('API Token / API Key')}</option>
                      </select>
                    </div>

                    <div>
                      <label style={{ fontSize: '12.5px', fontWeight: 600, display: 'block', marginBottom: '5px', color: 'var(--text)' }}>
                        {remoteForm.auth_type === 'token' ? t('API Token / Key') : t('Password / Login Key')}
                      </label>
                      <div style={{ position: 'relative', display: 'flex', alignItems: 'center' }}>
                        <input
                          type={showRemoteSecret ? 'text' : 'password'}
                          placeholder="••••••••••••••••"
                          value={remoteForm.secret}
                          onChange={e => setRemoteForm(prev => ({ ...prev, secret: e.target.value }))}
                          style={{ margin: 0, height: '38px', width: '100%', paddingRight: '38px' }}
                        />
                        <button
                          type="button"
                          onClick={() => setShowRemoteSecret(prev => !prev)}
                          title={showRemoteSecret ? t('Hide password') : t('Show password')}
                          style={{
                            position: 'absolute',
                            right: '6px',
                            background: 'transparent',
                            border: 'none',
                            padding: '4px 6px',
                            minHeight: 'auto',
                            height: '28px',
                            color: 'var(--text-secondary)',
                            boxShadow: 'none',
                          }}
                        >
                          {showRemoteSecret ? <EyeOff size={15} /> : <Eye size={15} />}
                        </button>
                      </div>
                    </div>
                  </div>

                  <div className="pkg-remote-action-bar">
                    <label style={{ display: 'inline-flex', alignItems: 'center', gap: '8px', fontSize: '12.5px', color: 'var(--text-secondary)', cursor: 'pointer', margin: 0 }}>
                      <input
                        type="checkbox"
                        checked={Boolean(remoteForm.verify_ssl)}
                        onChange={e => setRemoteForm(prev => ({ ...prev, verify_ssl: e.target.checked }))}
                        style={{ width: '15px', height: '15px', margin: 0 }}
                      />
                      <span>
                        {t('Verify SSL Certificate (uncheck for self-signed SSL or IP:port)')}
                      </span>
                    </label>

                    <button type="submit" disabled={!!loading} style={{ height: '38px', padding: '0 18px' }}>
                      <CloudDownload size={15} /> {t('Fetch Remote Packages')}
                    </button>
                  </div>
                </form>
              )}

              {/* Step 3: Interactive Preview & Mapping Table */}
              {parsedPackages.length > 0 && (
                <div className="pkg-preview-box">
                  <div className="pkg-preview-toolbar">
                    <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                      <label style={{ display: 'inline-flex', alignItems: 'center', gap: '6px', fontSize: '13px', fontWeight: 700, cursor: 'pointer' }}>
                        <input
                          type="checkbox"
                          checked={selectedImportCount === parsedPackages.length && parsedPackages.length > 0}
                          onChange={e => {
                            const checked = e.target.checked;
                            setParsedPackages(prev => prev.map(p => ({ ...p, _selected: checked })));
                          }}
                          style={{ width: '15px', height: '15px', margin: 0 }}
                        />
                        {t('Preview Parsed Packages')} ({selectedImportCount}/{parsedPackages.length})
                      </label>
                    </div>

                    <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap' }}>
                      <span style={{ fontSize: '12px', color: 'var(--text-secondary)', fontWeight: 600 }}>
                        {t('If package name exists:')}
                      </span>
                      <select
                        value={conflictMode}
                        onChange={e => setConflictMode(e.target.value)}
                        style={{ width: 'auto', height: '30px', minHeight: '30px', padding: '2px 8px', fontSize: '12px', margin: 0 }}
                      >
                        <option value="overwrite">{t('Overwrite existing package')}</option>
                        <option value="skip">{t('Skip duplicate package')}</option>
                        <option value="rename">{t('Auto-rename (append _imported)')}</option>
                      </select>
                      <button
                        type="button"
                        className="secondary-light mini"
                        onClick={() => setParsedPackages([])}
                      >
                        <Trash2 size={12} /> {t('Clear List')}
                      </button>
                    </div>
                  </div>

                  <div style={{ overflowX: 'auto', maxHeight: '280px' }}>
                    <table className="pkg-clean-table">
                      <thead>
                        <tr>
                          <th style={{ width: '36px' }}></th>
                          <th>{t('Package Name')}</th>
                          <th>{t('Source')}</th>
                          <th>{t('Disk')} / {t('Bandwidth')}</th>
                          <th>
                            {mode === 'resellers'
                              ? `${t('Max Sub-Accounts (Users)')} / IPs`
                              : `${t('Websites')} / ${t('Subdomains')} / DBs`}
                          </th>
                          <th>{t('Email')} / FTP</th>
                          <th>{t('Features')}</th>
                          <th style={{ textAlign: 'right' }}>{t('Actions')}</th>
                        </tr>
                      </thead>
                      <tbody>
                        {parsedPackages.map((pkg, idx) => (
                          <tr key={pkg._id || idx}>
                            <td>
                              <input
                                type="checkbox"
                                checked={Boolean(pkg._selected)}
                                onChange={e => {
                                  const checked = e.target.checked;
                                  setParsedPackages(prev =>
                                    prev.map((item, i) => (i === idx ? { ...item, _selected: checked } : item))
                                  );
                                }}
                                style={{ width: '15px', height: '15px', margin: 0 }}
                              />
                            </td>
                            <td>
                              <input
                                type="text"
                                value={pkg.name}
                                onChange={e => {
                                  const val = e.target.value;
                                  setParsedPackages(prev =>
                                    prev.map((item, i) => (i === idx ? { ...item, name: val } : item))
                                  );
                                }}
                                style={{ height: '30px', minHeight: '30px', padding: '4px 8px', fontSize: '13px', width: '175px', margin: 0 }}
                              />
                            </td>
                            <td>
                              <span className="badge" style={{ fontSize: '11px', textTransform: 'uppercase' }}>
                                {pkg.source_panel || importPanel}
                              </span>
                            </td>
                            <td>
                              {formatMb(pkg.storage_limit_mb)} <span className="muted">/</span> {formatMb(pkg.bandwidth_limit_mb)}
                            </td>
                            <td>
                              {mode === 'resellers' ? (
                                <>
                                  {formatCount(pkg.reseller_user_limit)} {t('Users')} · {formatCount(pkg.ips_limit)} IPs
                                </>
                              ) : (
                                <>
                                  {formatCount(pkg.website_limit)} Web · {formatCount(pkg.subdomain_limit)} Sub · {formatCount(pkg.database_limit)} DB
                                </>
                              )}
                            </td>
                            <td>
                              {formatCount(pkg.mail_accounts_limit)} Mail · {formatCount(pkg.sftp_accounts_limit)} FTP
                            </td>
                            <td>
                              <div style={{ display: 'inline-flex', gap: '4px', flexWrap: 'wrap' }}>
                                {pkg.php_access && <span className="badge ok" style={{ fontSize: '10.5px' }}>PHP</span>}
                                {pkg.ssl_access && <span className="badge ok" style={{ fontSize: '10.5px' }}>SSL</span>}
                                {pkg.terminal_enabled && <span className="badge warn" style={{ fontSize: '10.5px' }}>SSH</span>}
                                {pkg.dns_control && <span className="badge" style={{ fontSize: '10.5px' }}>DNS</span>}
                                {mode === 'resellers' && pkg.reseller_overselling_enabled && (
                                  <span className="badge ok" style={{ fontSize: '10.5px' }}>Oversell</span>
                                )}
                              </div>
                            </td>
                            <td style={{ textAlign: 'right' }}>
                              <div style={{ display: 'inline-flex', gap: '6px' }}>
                                <button
                                  type="button"
                                  className="secondary-light mini"
                                  onClick={() => handleLoadSingleIntoEditor(pkg)}
                                  title={t('Load into Package Editor to customize all parameters before saving')}
                                >
                                  <Pencil size={12} /> {t('Load to Editor')}
                                </button>
                                <button
                                  type="button"
                                  className="danger mini"
                                  onClick={() => setParsedPackages(prev => prev.filter((_, i) => i !== idx))}
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
                </div>
              )}
            </div>

            <div className="pkg-import-footer">
              <div className="muted" style={{ fontSize: '12px' }}>
                {t('Tip: Click "Load to Editor" on any row to customize Cgroups v2 & App Runtime limits before saving.')}
              </div>
              <div style={{ display: 'flex', gap: '8px' }}>
                <button
                  type="button"
                  className="secondary-light"
                  onClick={() => setShowImportModal(false)}
                >
                  <X size={14} /> {t('Cancel')}
                </button>
                <button
                  type="button"
                  disabled={!!loading || selectedImportCount === 0}
                  onClick={handleBatchImportSubmit}
                >
                  <Check size={14} /> {t('Import Selected')} ({selectedImportCount})
                </button>
              </div>
            </div>
          </div>
        </div>,
        document.body
      )}
>>>>>>> Stashed changes
    </div>
  );
}

export const Packages = PackagesPage;
