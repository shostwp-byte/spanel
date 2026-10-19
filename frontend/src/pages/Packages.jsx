import React, { useState, useEffect } from 'react';
import {
  ArrowLeft, Boxes, Cpu, Globe, Pencil, Plus, RefreshCw, Save, ShieldCheck, Trash2, X
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
    </div>
  );
}

export const Packages = PackagesPage;
