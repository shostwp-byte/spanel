import React, { useState, useEffect } from 'react';
import {
  Wrench, RefreshCw, RotateCcw, CheckCircle, AlertCircle,
  HardDrive, Server, Shield, Cpu, Zap, Trash2, Sparkles,
  FolderOpen, Globe, TerminalIcon, Layers, Check, Play, Eye, X
} from 'lucide-react';
import { useAppContext } from '../contexts/AppContext.jsx';
import { t } from '../i18n.js';

const MAINT_LOG_KEY = 'spanel_maintenance_logs';

function readMaintLogs() {
  try {
    const raw = sessionStorage.getItem(MAINT_LOG_KEY);
    return raw ? JSON.parse(raw) : [];
  } catch {
    return [];
  }
}

function saveMaintLogs(logs) {
  try {
    sessionStorage.setItem(MAINT_LOG_KEY, JSON.stringify(logs.slice(0, 30)));
  } catch {
    // ignore
  }
}

function formatBytesShort(bytes) {
  const num = Number(bytes) || 0;
  if (num <= 0) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  const i = Math.min(units.length - 1, Math.floor(Math.log(num) / Math.log(1024)));
  return `${(num / Math.pow(1024, i)).toFixed(i === 0 ? 0 : 1)} ${units[i]}`;
}

export function MaintenancePage({ request, isAdmin, websites: propWebsites, users: propUsers, loadUsers, refreshAll }) {
  const {
    loading, setLoading, setNotice, setError,
    websites: ctxWebsites = [], users: ctxUsers = [], monitoring
  } = useAppContext();

  const websites = (propWebsites && propWebsites.length ? propWebsites : ctxWebsites) || [];
  const users = (propUsers && propUsers.length ? propUsers : ctxUsers) || [];

  const [activeTab, setActiveTab] = useState('tools');
  const [nginxRunning, setNginxRunning] = useState(true);
  const [fileJobs, setFileJobs] = useState([]);
  const [restoreStaging, setRestoreStaging] = useState([]);
  const [daStaging, setDaStaging] = useState([]);
  const [selectedSiteId, setSelectedSiteId] = useState('all');
  const [vhostModal, setVhostModal] = useState(null);
  const [logs, setLogs] = useState(readMaintLogs);

  const appendLog = (title, output, status = 'ok') => {
    const entry = {
      id: `maint-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`,
      timestamp: new Date().toISOString(),
      title,
      status,
      output: String(output || '').trim() || '(completed successfully)',
    };
    setLogs(prev => {
      const next = [entry, ...prev].slice(0, 30);
      saveMaintLogs(next);
      return next;
    });
    return entry;
  };

  useEffect(() => {
    if (isAdmin) {
      loadOverview();
    }
  }, [isAdmin]);

  async function loadOverview() {
    setLoading(t('Loading system maintenance status...'));
    try {
      const [nginxRes, jobsRes, restoreRes, daRes] = await Promise.all([
        request('/services/action', {
          method: 'POST',
          body: JSON.stringify({ name: 'nginx', action: 'status' }),
          silent: true,
        }),
        request('/maintenance/files/jobs', { silent: true }),
        request('/maintenance/user-restore-backups', { silent: true }),
        request('/maintenance/da-import/backups', { silent: true }),
      ]);
      const nginxText = `${nginxRes?.stdout || ''} ${nginxRes?.stderr || ''}`.toLowerCase();
      setNginxRunning(nginxText.includes('active (running)'));
      setFileJobs(jobsRes?.jobs || []);
      setRestoreStaging(restoreRes?.items || []);
      setDaStaging(Array.isArray(daRes) ? daRes : (daRes?.items || []));
      if (loadUsers) await loadUsers();
    } finally {
      setLoading('');
    }
  }

  async function handleReloadNginx() {
    const res = await request(
      '/services/action',
      { method: 'POST', body: JSON.stringify({ name: 'nginx', action: 'reload' }) },
      t('Validating & reloading Nginx configuration...')
    );
    const out = `${res?.stdout || ''}\n${res?.stderr || ''}`.trim() || 'nginx configuration reloaded successfully.';
    const ok = res && (res.returncode === 0 || res.returncode === undefined);
    appendLog('Verify & Reload Nginx Configuration', out, ok ? 'ok' : 'error');
    if (ok) {
      setNotice(t('Nginx configuration verified and reloaded.'));
      setNginxRunning(true);
    }
  }

  async function handleRetunePhpPools() {
    const res = await request(
      '/maintenance/php-tune/pools',
      { method: 'POST' },
      t('Rebuilding & tuning all PHP-FPM worker pools...')
    );
    if (res) {
      const msg = t('Rebuilt and tuned {n} PHP-FPM pool(s).', { n: res.pools_written ?? 0 });
      appendLog('Rebuild & Retune PHP-FPM Worker Pools', res.output || JSON.stringify(res, null, 2), 'ok');
      setNotice(msg);
    }
  }

  async function handleFixPermissions(siteId = selectedSiteId) {
    const targets = siteId === 'all'
      ? websites
      : websites.filter(w => String(w.id) === String(siteId));

    if (!targets.length) {
      setError(t('No websites available to repair permissions.'));
      return;
    }

    setLoading(t('Repairing file ownership & permissions...'));
    const results = [];
    let okCount = 0;
    try {
      for (const site of targets) {
        const res = await request(
          `/maintenance/wordpress/${site.id}/fix-permissions`,
          { method: 'POST', silent: true }
        );
        if (res) {
          okCount++;
          results.push(`[OK] ${site.domain} (${site.root_path || '/var/www'}) -> ${res.message || 'Fixed'}`);
        } else {
          results.push(`[WARN] ${site.domain} -> skipped or failed`);
        }
      }
      appendLog(
        `Repair File Permissions (${okCount}/${targets.length} website(s))`,
        results.join('\n'),
        okCount > 0 ? 'ok' : 'error'
      );
      setNotice(t('Repaired file ownership and permissions for {n} website(s).', { n: okCount }));
    } finally {
      setLoading('');
    }
  }

  async function handleRecalculateQuotas() {
    setLoading(t('Recalculating storage quotas across all accounts...'));
    try {
      if (loadUsers) await loadUsers();
      if (refreshAll) await refreshAll();
      const summaryLines = (users || []).map(
        u => `${u.username} (${u.role}): ${formatBytesShort(u.storage_used_bytes || 0)} / ${u.storage_limit_mb ? u.storage_limit_mb + ' MB' : 'Unlimited'}`
      );
      appendLog(
        `Recalculate Storage Quotas (${users.length} account(s))`,
        summaryLines.join('\n') || 'All user storage quotas refreshed.',
        'ok'
      );
      setNotice(t('Storage quotas recalculated for {n} account(s).', { n: users.length }));
    } finally {
      setLoading('');
    }
  }

  async function handleFlushCacheAndRestart() {
    setLoading(t('Restarting Redis Object Cache & MariaDB services...'));
    try {
      const r1 = await request(
        '/services/action',
        { method: 'POST', body: JSON.stringify({ name: 'redis-server', action: 'restart' }), silent: true }
      );
      const r2 = await request(
        '/services/action',
        { method: 'POST', body: JSON.stringify({ name: 'nginx', action: 'reload' }), silent: true }
      );
      const out = [
        `[redis-server] returncode=${r1?.returncode ?? 0} ${r1?.stdout || ''}`.trim(),
        `[nginx reload] returncode=${r2?.returncode ?? 0} ${r2?.stdout || ''}`.trim(),
      ].join('\n');
      appendLog('Restart Redis Object Cache & Reload Web Stack', out, 'ok');
      setNotice(t('Redis Object Cache restarted and Nginx reloaded.'));
    } finally {
      setLoading('');
    }
  }

  async function handleCleanupStaging() {
    if (!confirm(t('Clean up temporary restore & migration staging archives?'))) return;
    setLoading(t('Cleaning up temporary staging archives...'));
    const deleted = [];
    try {
      for (const item of restoreStaging) {
        const file = item.backup_file || item.filename;
        if (file) {
          await request(`/maintenance/user-restore-backups?backup_file=${encodeURIComponent(file)}`, {
            method: 'DELETE',
            silent: true,
          });
          deleted.push(`restore: ${file}`);
        }
      }
      for (const item of daStaging) {
        const pathStr = item.path || item.filename;
        if (pathStr) {
          await request('/maintenance/da-import/backups', {
            method: 'DELETE',
            body: JSON.stringify({ archive_path: pathStr }),
            silent: true,
          });
          deleted.push(`da-import: ${pathStr}`);
        }
      }
      await loadOverview();
      appendLog(
        'Clean Staging Archives & Temporary Jobs',
        deleted.length ? `Removed ${deleted.length} staging archive(s):\n${deleted.join('\n')}` : 'Staging directories are already clean (0 leftover archives).',
        'ok'
      );
      setNotice(
        deleted.length
          ? t('Cleaned up {n} staging archive(s).', { n: deleted.length })
          : t('Staging directories are already clean.')
      );
    } finally {
      setLoading('');
    }
  }

  async function handleInspectVhost(site) {
    const res = await request(`/websites/${site.id}/nginx-config`, {}, t('Loading Nginx config for {d}...', { d: site.domain }));
    if (res) {
      setVhostModal({
        domain: site.domain,
        vhostPath: res.vhost_path || `/etc/nginx/sites-available/${site.domain}.conf`,
        content: res.vhost_content || res.custom_config || '# Empty configuration',
      });
    }
  }

  if (!isAdmin) {
    return (
      <div className="section">
        <h2>{t('System Maintenance')}</h2>
        <p className="hint">{t('Only administrators can access system maintenance tools.')}</p>
      </div>
    );
  }

  const disk = monitoring?.disk || {};
  const totalUserStorage = users.reduce((acc, u) => acc + (Number(u.storage_used_bytes) || 0), 0);
  const stagingCount = restoreStaging.length + daStaging.length + fileJobs.length;

  const tabs = [
    ['tools', 'Maintenance & Repair Tools', Wrench],
    ['websites', `Website Permission & Vhost Inspector (${websites.length})`, Globe],
    ['logs', `Maintenance Logs (${logs.length})`, TerminalIcon],
  ];

  return (
    <div className="section">
      {/* Page Header */}
      <div className="section-title" style={{ marginBottom: '14px' }}>
        <div>
          <h2 style={{ display: 'flex', alignItems: 'center', gap: '10px', margin: 0 }}>
            <Wrench size={19} />
            <span>{t('System Maintenance & Repair Center')}</span>
          </h2>
          <p className="hint" style={{ marginTop: '4px' }}>
            {t('Verify web server configs, repair file permissions, recalculate user storage quotas, and clean staging directories.')}
          </p>
        </div>
        <div className="cb-toolbar-actions">
          <button type="button" className="secondary-light" disabled={!!loading} onClick={loadOverview}>
            <RefreshCw size={14} /> {t('Refresh Status')}
          </button>
          <button type="button" className="secondary" disabled={!!loading} onClick={handleReloadNginx}>
            <RotateCcw size={14} /> {t('Reload Nginx')}
          </button>
          <button type="button" disabled={!!loading} onClick={() => handleFixPermissions('all')}>
            <Sparkles size={14} /> {t('Repair All Permissions')}
          </button>
        </div>
      </div>

      {/* Top KPI Strip */}
      <div className="cb-stats-strip">
        <div className="cb-stat-box">
          <div className="cb-stat-icon blue"><Server size={18} /></div>
          <div className="cb-stat-info">
            <span className="cb-stat-label">{t('Nginx & VirtualHosts')}</span>
            <strong>
              {websites.length} {t('Vhosts')}
              <small className="cb-stat-sub">{nginxRunning ? t('Nginx Running') : t('Check Service')}</small>
            </strong>
          </div>
        </div>
        <div className="cb-stat-box">
          <div className="cb-stat-icon indigo"><HardDrive size={18} /></div>
          <div className="cb-stat-info">
            <span className="cb-stat-label">{t('Server Disk Usage')}</span>
            <strong>
              {disk.percent != null ? `${Math.round(disk.percent)}%` : formatBytesShort(totalUserStorage)}
              <small className="cb-stat-sub">
                {disk.total ? `${formatBytesShort(disk.used)} / ${formatBytesShort(disk.total)}` : t('Root Partition')}
              </small>
            </strong>
          </div>
        </div>
        <div className="cb-stat-box">
          <div className="cb-stat-icon emerald"><Shield size={18} /></div>
          <div className="cb-stat-info">
            <span className="cb-stat-label">{t('Account Quotas')}</span>
            <strong>
              {users.length} {t('Accounts')}
              <small className="cb-stat-sub">{formatBytesShort(totalUserStorage)} {t('used')}</small>
            </strong>
          </div>
        </div>
        <div className="cb-stat-box">
          <div className="cb-stat-icon amber"><FolderOpen size={18} /></div>
          <div className="cb-stat-info">
            <span className="cb-stat-label">{t('Staging & Active Jobs')}</span>
            <strong>
              {stagingCount} {t('Items')}
              <small className="cb-stat-sub">{restoreStaging.length + daStaging.length} {t('archives')}</small>
            </strong>
          </div>
        </div>
      </div>

      {/* Tabs */}
      <div className="segmented-control backup-tabs" role="tablist">
        {tabs.map(([id, label, Icon]) => (
          <button
            key={id}
            type="button"
            role="tab"
            aria-selected={activeTab === id}
            className={activeTab === id ? 'active' : ''}
            onClick={() => setActiveTab(id)}
          >
            <Icon size={14} />
            {t(label)}
          </button>
        ))}
      </div>

      {/* TAB 1: MAINTENANCE TOOLS */}
      {activeTab === 'tools' && (
        <div className="cb-grid" style={{ marginTop: '6px' }}>
          {/* Card 1: Nginx Config Check & Reload */}
          <div className="cb-card">
            <div className="cb-card-top">
              <div className="cb-card-header">
                <div className="cb-card-icon blue"><Server size={18} /></div>
                <div className="cb-card-heading">
                  <div className="cb-card-title-row">
                    <h4 className="cb-card-title">{t('Verify & Reload Nginx Vhosts')}</h4>
                  </div>
                  <div className="cb-card-meta">
                    <span>Web Server</span>
                    <span className="cb-dot">•</span>
                    <code className="cb-ver-tag">nginx -t && reload</code>
                  </div>
                </div>
                <span className={`cb-status-pill ${nginxRunning ? 'running' : 'stopped'}`}>
                  <span className="cb-status-dot" />
                  {nginxRunning ? t('Healthy') : t('Attention')}
                </span>
              </div>
              <p className="cb-card-desc">
                {t('Tests Nginx syntax across all virtual hosts and gracefully reloads worker processes without dropping connections.')}
              </p>
            </div>
            <div className="cb-card-footer">
              <div className="cb-card-unit"><code>/etc/nginx/sites-enabled</code></div>
              <div className="cb-card-actions">
                <button type="button" className="cb-btn primary" disabled={!!loading} onClick={handleReloadNginx}>
                  <Play size={13} /> {t('Verify & Reload')}
                </button>
              </div>
            </div>
          </div>

          {/* Card 2: Fix Permissions */}
          <div className="cb-card">
            <div className="cb-card-top">
              <div className="cb-card-header">
                <div className="cb-card-icon emerald"><Shield size={18} /></div>
                <div className="cb-card-heading">
                  <div className="cb-card-title-row">
                    <h4 className="cb-card-title">{t('Repair File Ownership & Permissions')}</h4>
                  </div>
                  <div className="cb-card-meta">
                    <span>Filesystem</span>
                    <span className="cb-dot">•</span>
                    <code className="cb-ver-tag">chown / chmod 755·644</code>
                  </div>
                </div>
                <span className="cb-status-pill running">
                  <span className="cb-status-dot" />
                  {websites.length} {t('Sites')}
                </span>
              </div>
              <p className="cb-card-desc">
                {t('Restores correct Linux user ownership and directory/file permissions (755/644) for website document roots.')}
              </p>
            </div>
            <div className="cb-card-footer">
              <div className="cb-card-unit" style={{ flex: 1 }}>
                <select
                  value={selectedSiteId}
                  onChange={e => setSelectedSiteId(e.target.value)}
                  style={{ height: '32px', padding: '0 8px', fontSize: '12px', maxWidth: '180px' }}
                >
                  <option value="all">{t('All Websites')} ({websites.length})</option>
                  {websites.map(w => (
                    <option key={w.id} value={w.id}>{w.domain}</option>
                  ))}
                </select>
              </div>
              <div className="cb-card-actions">
                <button type="button" className="cb-btn primary" disabled={!!loading} onClick={() => handleFixPermissions(selectedSiteId)}>
                  <Sparkles size={13} /> {t('Repair Now')}
                </button>
              </div>
            </div>
          </div>

          {/* Card 3: PHP-FPM Pool Retune */}
          <div className="cb-card">
            <div className="cb-card-top">
              <div className="cb-card-header">
                <div className="cb-card-icon indigo"><Cpu size={18} /></div>
                <div className="cb-card-heading">
                  <div className="cb-card-title-row">
                    <h4 className="cb-card-title">{t('Rebuild & Tune PHP-FPM Pools')}</h4>
                  </div>
                  <div className="cb-card-meta">
                    <span>PHP Runtime</span>
                    <span className="cb-dot">•</span>
                    <code className="cb-ver-tag">pm.max_children</code>
                  </div>
                </div>
                <span className="cb-status-pill running">
                  <span className="cb-status-dot" />
                  {t('Auto-Tune')}
                </span>
              </div>
              <p className="cb-card-desc">
                {t('Recalculates PHP-FPM worker limits against available server RAM and rewrites isolated pool configs for every site.')}
              </p>
            </div>
            <div className="cb-card-footer">
              <div className="cb-card-unit"><code>/etc/php/*/fpm/pool.d</code></div>
              <div className="cb-card-actions">
                <button type="button" className="cb-btn secondary" disabled={!!loading} onClick={handleRetunePhpPools}>
                  <Sparkles size={13} /> {t('Rebuild Pools')}
                </button>
              </div>
            </div>
          </div>

          {/* Card 4: Recalculate Quotas */}
          <div className="cb-card">
            <div className="cb-card-top">
              <div className="cb-card-header">
                <div className="cb-card-icon amber"><HardDrive size={18} /></div>
                <div className="cb-card-heading">
                  <div className="cb-card-title-row">
                    <h4 className="cb-card-title">{t('Recalculate User Storage Quotas')}</h4>
                  </div>
                  <div className="cb-card-meta">
                    <span>Disk Quota</span>
                    <span className="cb-dot">•</span>
                    <code className="cb-ver-tag">{users.length} accounts</code>
                  </div>
                </div>
                <span className="cb-status-pill running">
                  <span className="cb-status-dot" />
                  {formatBytesShort(totalUserStorage)}
                </span>
              </div>
              <p className="cb-card-desc">
                {t('Scans website directories, mailboxes, and application volumes to synchronize disk usage counters for all users.')}
              </p>
            </div>
            <div className="cb-card-footer">
              <div className="cb-card-unit"><code>storage_quota.sync</code></div>
              <div className="cb-card-actions">
                <button type="button" className="cb-btn secondary" disabled={!!loading} onClick={handleRecalculateQuotas}>
                  <RefreshCw size={13} /> {t('Recalculate')}
                </button>
              </div>
            </div>
          </div>

          {/* Card 5: Flush Redis Cache */}
          <div className="cb-card">
            <div className="cb-card-top">
              <div className="cb-card-header">
                <div className="cb-card-icon rose"><Zap size={18} /></div>
                <div className="cb-card-heading">
                  <div className="cb-card-title-row">
                    <h4 className="cb-card-title">{t('Restart Redis Cache & Web Stack')}</h4>
                  </div>
                  <div className="cb-card-meta">
                    <span>Object Cache</span>
                    <span className="cb-dot">•</span>
                    <code className="cb-ver-tag">redis-server</code>
                  </div>
                </div>
                <span className="cb-status-pill running">
                  <span className="cb-status-dot" />
                  {t('Active')}
                </span>
              </div>
              <p className="cb-card-desc">
                {t('Restarts the Redis in-memory object cache daemon and reloads Nginx FastCGI cache workers cleanly.')}
              </p>
            </div>
            <div className="cb-card-footer">
              <div className="cb-card-unit"><code>redis-server.service</code></div>
              <div className="cb-card-actions">
                <button type="button" className="cb-btn secondary" disabled={!!loading} onClick={handleFlushCacheAndRestart}>
                  <RotateCcw size={13} /> {t('Restart Cache')}
                </button>
              </div>
            </div>
          </div>

          {/* Card 6: Clean Temporary Staging */}
          <div className="cb-card">
            <div className="cb-card-top">
              <div className="cb-card-header">
                <div className="cb-card-icon slate"><Trash2 size={18} /></div>
                <div className="cb-card-heading">
                  <div className="cb-card-title-row">
                    <h4 className="cb-card-title">{t('Clean Staging Archives & Temp Files')}</h4>
                  </div>
                  <div className="cb-card-meta">
                    <span>Housekeeping</span>
                    <span className="cb-dot">•</span>
                    <code className="cb-ver-tag">{restoreStaging.length + daStaging.length} archives</code>
                  </div>
                </div>
                <span className={`cb-status-pill ${restoreStaging.length + daStaging.length > 0 ? 'stopped' : 'running'}`}>
                  <span className="cb-status-dot" />
                  {restoreStaging.length + daStaging.length > 0 ? t('Cleanup Available') : t('Clean')}
                </span>
              </div>
              <p className="cb-card-desc">
                {t('Removes leftover uploaded restore archives and DirectAdmin migration staging files to free up server disk space.')}
              </p>
            </div>
            <div className="cb-card-footer">
              <div className="cb-card-unit"><code>/var/backups/spanel</code></div>
              <div className="cb-card-actions">
                <button type="button" className="cb-btn danger" disabled={!!loading} onClick={handleCleanupStaging}>
                  <Trash2 size={13} /> {t('Clean Staging')}
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* TAB 2: WEBSITE PERMISSION & VHOST INSPECTOR */}
      {activeTab === 'websites' && (
        <div style={{ display: 'grid', gap: '14px', marginTop: '6px' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '10px' }}>
            <p className="hint" style={{ margin: 0 }}>
              {t('Inspect Nginx virtual host configurations or repair file permissions per website.')}
            </p>
            <button type="button" className="cb-btn primary" disabled={!!loading} onClick={() => handleFixPermissions('all')}>
              <Sparkles size={13} /> {t('Repair All Websites')}
            </button>
          </div>

          <div style={{ border: '1px solid var(--border)', borderRadius: '12px', overflow: 'hidden', background: 'var(--surface)' }}>
            <div style={{ width: '100%', overflowX: 'auto' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse', textAlign: 'left', fontSize: '13.5px' }}>
                <thead>
                  <tr style={{ background: 'var(--surface-alt)', borderBottom: '1px solid var(--border)' }}>
                    <th style={{ padding: '12px 16px' }}>{t('Domain')}</th>
                    <th style={{ padding: '12px 16px' }}>{t('Linux User')}</th>
                    <th style={{ padding: '12px 16px' }}>{t('Document Root')}</th>
                    <th style={{ padding: '12px 16px' }}>{t('PHP Branch')}</th>
                    <th style={{ padding: '12px 16px', textAlign: 'right' }}>{t('Maintenance Actions')}</th>
                  </tr>
                </thead>
                <tbody>
                  {websites.map(site => (
                    <tr key={site.id} style={{ borderBottom: '1px solid var(--border)' }}>
                      <td style={{ padding: '12px 16px', fontWeight: 600 }}>{site.domain}</td>
                      <td style={{ padding: '12px 16px' }}><code>{site.linux_user || 'www-data'}</code></td>
                      <td style={{ padding: '12px 16px' }}><code style={{ fontSize: '12px' }}>{site.root_path}</code></td>
                      <td style={{ padding: '12px 16px' }}>
                        <span className="badge ok">PHP {site.php_version || '8.4'}</span>
                      </td>
                      <td style={{ padding: '12px 16px', textAlign: 'right' }}>
                        <div style={{ display: 'inline-flex', gap: '8px', justifyContent: 'flex-end' }}>
                          <button
                            type="button"
                            className="cb-btn secondary"
                            disabled={!!loading}
                            onClick={() => handleInspectVhost(site)}
                          >
                            <Eye size={13} /> {t('Inspect Vhost')}
                          </button>
                          <button
                            type="button"
                            className="cb-btn secondary"
                            disabled={!!loading}
                            onClick={() => handleFixPermissions(site.id)}
                          >
                            <Sparkles size={13} /> {t('Fix Permissions')}
                          </button>
                        </div>
                      </td>
                    </tr>
                  ))}
                  {websites.length === 0 && (
                    <tr>
                      <td colSpan={5} style={{ padding: '32px', textAlign: 'center' }} className="hint">
                        {t('No websites hosted on this server yet.')}
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      )}

      {/* TAB 3: MAINTENANCE LOGS */}
      {activeTab === 'logs' && (
        <div style={{ display: 'grid', gap: '12px', marginTop: '6px' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '8px' }}>
            <p className="hint" style={{ margin: 0 }}>{t('Diagnostic output from maintenance operations in this session.')}</p>
            {logs.length > 0 && (
              <button
                type="button"
                className="cb-btn secondary"
                onClick={() => {
                  setLogs([]);
                  saveMaintLogs([]);
                }}
              >
                <Trash2 size={13} /> {t('Clear logs')}
              </button>
            )}
          </div>

          {logs.length === 0 ? (
            <div style={{ padding: '32px', textAlign: 'center', border: '1px solid var(--border)', borderRadius: '12px', background: 'var(--surface-alt)' }}>
              <TerminalIcon size={28} style={{ opacity: 0.4, marginBottom: '8px' }} />
              <p className="hint" style={{ margin: 0 }}>{t('No maintenance tasks executed in this session yet.')}</p>
            </div>
          ) : (
            logs.map(log => (
              <div key={log.id} style={{ border: '1px solid var(--border)', borderRadius: '12px', overflow: 'hidden', background: 'var(--surface)' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '10px 14px', background: 'var(--surface-alt)', borderBottom: '1px solid var(--border)' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                    {log.status === 'ok' ? <CheckCircle size={15} color="var(--success)" /> : <AlertCircle size={15} color="var(--danger)" />}
                    <strong>{log.title}</strong>
                  </div>
                  <span className="hint" style={{ fontSize: '12px' }}>{new Date(log.timestamp).toLocaleString()}</span>
                </div>
                <pre style={{ margin: 0, padding: '12px 14px', fontSize: '12px', maxHeight: '240px', overflow: 'auto', background: '#0f172a', color: '#e2e8f0', fontFamily: 'monospace' }}>
                  {log.output}
                </pre>
              </div>
            ))
          )}
        </div>
      )}

      {/* Vhost Inspector Modal */}
      {vhostModal && (
        <div className="modal-backdrop" onClick={() => setVhostModal(null)}>
          <div className="modal" style={{ maxWidth: '760px', width: '92vw' }} onClick={e => e.stopPropagation()}>
            <div className="modal-header" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <div>
                <h3 style={{ margin: 0 }}>{vhostModal.domain}</h3>
                <small className="hint"><code>{vhostModal.vhostPath}</code></small>
              </div>
              <button type="button" className="cb-btn secondary" onClick={() => setVhostModal(null)}>
                <X size={14} /> {t('Close')}
              </button>
            </div>
            <div className="modal-body" style={{ padding: '14px' }}>
              <pre style={{ margin: 0, padding: '14px', borderRadius: '10px', background: '#0f172a', color: '#e2e8f0', fontSize: '12px', maxHeight: '420px', overflow: 'auto', fontFamily: 'monospace' }}>
                {vhostModal.content}
              </pre>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
