import React, { useState, useEffect } from 'react';
import {
  BarChart, Cpu, HardDrive, Network, Server, Globe, Database,
  Lock, Shield, ShieldAlert, RefreshCw, Users, FolderOpen, FileText,
  Activity, CheckCircle, AlertCircle, Layers
} from 'lucide-react';
import { useAppContext } from '../contexts/AppContext.jsx';
import { t } from '../i18n.js';

function formatBytesVal(bytes) {
  const num = Number(bytes) || 0;
  if (num <= 0) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  const i = Math.min(units.length - 1, Math.floor(Math.log(num) / Math.log(1024)));
  return `${(num / Math.pow(1024, i)).toFixed(i === 0 ? 0 : 1)} ${units[i]}`;
}

function parseOsRelease(raw = '') {
  const map = {};
  String(raw || '').split('\n').forEach(line => {
    const idx = line.indexOf('=');
    if (idx > 0) {
      const k = line.slice(0, idx).trim();
      const v = line.slice(idx + 1).trim().replace(/^"|"$/g, '');
      map[k] = v;
    }
  });
  return {
    prettyName: map.PRETTY_NAME || map.NAME || 'Ubuntu / Debian Linux',
    versionId: map.VERSION_ID || '',
    id: map.ID || 'linux',
  };
}

export function ServerStatisticsPage({ request, isAdmin, isReseller, page }) {
  const {
    websites = [], databases = [], users = [], currentUser,
    loading, setLoading, navigateToPage, setSelectedWebsiteId, loadUsers
  } = useAppContext();

  const [activeTab, setActiveTab] = useState('overview');
  const [resourceUsage, setResourceUsage] = useState(null);
  const [systemInfo, setSystemInfo] = useState(null);
  const [dashSummary, setDashSummary] = useState(null);
  const [domainTraffic, setDomainTraffic] = useState(null);
  const [accountFilter, setAccountFilter] = useState('');
  const [domainFilter, setDomainFilter] = useState('');

  async function loadStats() {
    setLoading(t('Loading statistics & resource telemetry...'));
    try {
      if (isAdmin) {
        const [resUsage, sysInfo, summary, traffic] = await Promise.all([
          request('/services/resource-usage', { silent: true }),
          request('/services/system-info', { silent: true }),
          request('/dashboard/summary', { silent: true }),
          request('/resources/domain-traffic', { silent: true }),
        ]);
        if (resUsage) setResourceUsage(resUsage);
        if (sysInfo) setSystemInfo(sysInfo);
        if (summary) setDashSummary(summary);
        if (traffic) setDomainTraffic(traffic);
      } else {
        const [summary, traffic] = await Promise.all([
          request('/dashboard/summary', { silent: true }),
          request('/resources/domain-traffic', { silent: true }),
        ]);
        if (summary) setDashSummary(summary);
        if (traffic) setDomainTraffic(traffic);
        if (isReseller && users.length === 0 && loadUsers) {
          await loadUsers();
        }
      }
    } finally {
      setLoading('');
    }
  }

  useEffect(() => {
    loadStats();
  }, [isAdmin, page]);

  const cpu = resourceUsage?.cpu || {};
  const memory = resourceUsage?.memory || {};
  const disk = resourceUsage?.disk || {};
  const network = resourceUsage?.network || {};
  const osMeta = parseOsRelease(systemInfo?.os || '');

  const totalAccountStorage = users.reduce((acc, u) => acc + (Number(u.storage_used_bytes) || 0), 0);
  const myUsedBytes = Number(currentUser?.storage_used_bytes) || 0;
  const myLimitMb = Number(currentUser?.storage_limit_mb) || 0;
  const myLimitBytes = myLimitMb > 0 ? myLimitMb * 1024 * 1024 : 0;
  const myStoragePct = myLimitBytes > 0 ? Math.min(100, Math.round((myUsedBytes / myLimitBytes) * 100)) : 0;

  const securedSitesCount = websites.filter(w => w.ssl_enabled).length;
  const wafSitesCount = websites.filter(w => w.waf_enabled).length;

  const filteredUsers = users
    .filter(u => {
      if (!accountFilter.trim()) return true;
      const q = accountFilter.toLowerCase();
      return (
        (u.username || '').toLowerCase().includes(q) ||
        (u.email || '').toLowerCase().includes(q) ||
        (u.role || '').toLowerCase().includes(q)
      );
    })
    .sort((a, b) => (Number(b.storage_used_bytes) || 0) - (Number(a.storage_used_bytes) || 0));

  const filteredWebsites = websites.filter(w => {
    if (!domainFilter.trim()) return true;
    const q = domainFilter.toLowerCase();
    return (
      (w.domain || '').toLowerCase().includes(q) ||
      (w.php_version || '').toLowerCase().includes(q) ||
      (w.root_path || '').toLowerCase().includes(q) ||
      (w.linux_user || '').toLowerCase().includes(q)
    );
  });

  const pageTitle = isAdmin
    ? t('Server Statistics & Resource Telemetry')
    : isReseller
      ? t('System & Reseller Resource Info')
      : t('Site Summary & Account Statistics');

  const pageSubtitle = isAdmin
    ? t('Real-time hardware utilization, network throughput, disk partitions, account storage quotas, and domain security metrics.')
    : isReseller
      ? t('Overview of your reseller storage allocation, customer account usage, and hosted domains.')
      : t('Summary of your hosting account quotas, domain configurations, SSL coverage, and database usage.');

  return (
    <div className="section">
      <div className="section-title" style={{ marginBottom: '14px' }}>
        <div>
          <h2 style={{ display: 'flex', alignItems: 'center', gap: '10px', margin: 0 }}>
            <BarChart size={19} />
            <span>{pageTitle}</span>
          </h2>
          <p className="hint" style={{ marginTop: '4px' }}>{pageSubtitle}</p>
        </div>
        <div className="cb-toolbar-actions">
          <button type="button" className="cb-btn secondary" onClick={loadStats} disabled={!!loading}>
            <RefreshCw size={14} /> {t('Refresh Statistics')}
          </button>
        </div>
      </div>

      {/* KPI Strip */}
      {isAdmin ? (
        <div className="cb-stats-strip">
          <div className="cb-stat-box">
            <div className="cb-stat-icon blue"><Cpu size={18} /></div>
            <div className="cb-stat-info">
              <span className="cb-stat-label">{t('CPU Utilization')}</span>
              <strong>
                {cpu.percent !== undefined ? `${cpu.percent}%` : '--'}
                <small className="cb-stat-sub">
                  {cpu.cores ? `${cpu.cores} Cores` : ''} {cpu.load?.length ? `· Load ${cpu.load[0]}` : ''}
                </small>
              </strong>
            </div>
          </div>
          <div className="cb-stat-box">
            <div className="cb-stat-icon indigo"><Server size={18} /></div>
            <div className="cb-stat-info">
              <span className="cb-stat-label">{t('System Memory (RAM)')}</span>
              <strong>
                {memory.percent !== undefined ? `${memory.percent}%` : '--'}
                <small className="cb-stat-sub">
                  {formatBytesVal(memory.used)} / {formatBytesVal(memory.total)}
                </small>
              </strong>
            </div>
          </div>
          <div className="cb-stat-box">
            <div className="cb-stat-icon emerald"><HardDrive size={18} /></div>
            <div className="cb-stat-info">
              <span className="cb-stat-label">{t('Primary Disk (/)')}</span>
              <strong>
                {disk.percent !== undefined ? `${disk.percent}%` : '--'}
                <small className="cb-stat-sub">
                  {formatBytesVal(disk.used)} / {formatBytesVal(disk.total)}
                </small>
              </strong>
            </div>
          </div>
          <div className="cb-stat-box">
            <div className="cb-stat-icon amber"><Network size={18} /></div>
            <div className="cb-stat-info">
              <span className="cb-stat-label">{t('Network Traffic')}</span>
              <strong>
                {formatBytesVal((Number(network.rx_per_sec) || 0) + (Number(network.tx_per_sec) || 0))}/s
                <small className="cb-stat-sub">
                  RX {formatBytesVal(network.rx_total)} · TX {formatBytesVal(network.tx_total)}
                </small>
              </strong>
            </div>
          </div>
        </div>
      ) : (
        <div className="cb-stats-strip">
          <div className="cb-stat-box">
            <div className="cb-stat-icon blue"><HardDrive size={18} /></div>
            <div className="cb-stat-info">
              <span className="cb-stat-label">{t('Account Storage Usage')}</span>
              <strong>
                {formatBytesVal(myUsedBytes)}
                <small className="cb-stat-sub">
                  {myLimitBytes > 0 ? `/ ${formatBytesVal(myLimitBytes)} (${myStoragePct}%)` : t('Unlimited Quota')}
                </small>
              </strong>
            </div>
          </div>
          <div className="cb-stat-box">
            <div className="cb-stat-icon emerald"><Globe size={18} /></div>
            <div className="cb-stat-info">
              <span className="cb-stat-label">{t('Hosted Websites')}</span>
              <strong>
                {websites.length} {currentUser?.website_limit ? `/ ${currentUser.website_limit}` : ''}
                <small className="cb-stat-sub">{wafSitesCount} {t('WAF protected')}</small>
              </strong>
            </div>
          </div>
          <div className="cb-stat-box">
            <div className="cb-stat-icon indigo"><Lock size={18} /></div>
            <div className="cb-stat-info">
              <span className="cb-stat-label">{t('SSL / TLS Coverage')}</span>
              <strong>
                {securedSitesCount} / {websites.length}
                <small className="cb-stat-sub">{t('HTTPS Active')}</small>
              </strong>
            </div>
          </div>
          <div className="cb-stat-box">
            <div className="cb-stat-icon amber"><Database size={18} /></div>
            <div className="cb-stat-info">
              <span className="cb-stat-label">{isReseller ? t('Managed Customers') : t('MySQL Databases')}</span>
              <strong>
                {isReseller ? users.length : databases.length}
                <small className="cb-stat-sub">{isReseller ? t('Active accounts') : t('MariaDB instances')}</small>
              </strong>
            </div>
          </div>
        </div>
      )}

      {/* Navigation Tabs */}
      <div className="segmented-control backup-tabs" role="tablist" style={{ marginBottom: '16px' }}>
        <button
          type="button"
          role="tab"
          aria-selected={activeTab === 'overview'}
          className={activeTab === 'overview' ? 'active' : ''}
          onClick={() => setActiveTab('overview')}
        >
          <Activity size={14} /> {isAdmin ? t('System & Resource Telemetry') : t('Account & Quota Summary')}
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={activeTab === 'websites'}
          className={activeTab === 'websites' ? 'active' : ''}
          onClick={() => setActiveTab('websites')}
        >
          <Globe size={14} /> {t('Website & Domain Statistics')} ({websites.length})
        </button>
        {(isAdmin || isReseller) && (
          <button
            type="button"
            role="tab"
            aria-selected={activeTab === 'accounts'}
            className={activeTab === 'accounts' ? 'active' : ''}
            onClick={() => setActiveTab('accounts')}
          >
            <Users size={14} /> {t('Account Storage Breakdown')} ({users.length})
          </button>
        )}
      </div>

      {/* TAB 1: OVERVIEW */}
      {activeTab === 'overview' && (
        <div style={{ display: 'grid', gap: '16px' }}>
          {isAdmin ? (
            <>
              <div className="cb-grid">
                {/* Hardware & OS Card */}
                <div className="cb-card">
                  <div className="cb-card-top">
                    <div className="cb-card-header">
                      <div className="cb-card-icon blue"><Server size={18} /></div>
                      <div className="cb-card-heading">
                        <div className="cb-card-title-row">
                          <h4 className="cb-card-title">{t('Operating System & Compute')}</h4>
                        </div>
                        <div className="cb-card-meta">
                          <span>{osMeta.prettyName}</span>
                          <span className="cb-dot">•</span>
                          <code className="cb-ver-tag">{cpu.cores || 1} vCPU</code>
                        </div>
                      </div>
                      <span className="cb-status-pill running">
                        <span className="cb-status-dot" />
                        {t('Healthy')}
                      </span>
                    </div>
                    <div style={{ display: 'grid', gap: '8px', fontSize: '13px', marginTop: '4px' }}>
                      <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                        <span className="hint">{t('CPU Load Average (1m / 5m / 15m)')}</span>
                        <strong>{cpu.load?.length ? cpu.load.join(' / ') : '0.00 / 0.00 / 0.00'}</strong>
                      </div>
                      <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                        <span className="hint">{t('Available RAM')}</span>
                        <strong>{formatBytesVal(memory.available)} {t('free')}</strong>
                      </div>
                      <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                        <span className="hint">{t('Unallocated Disk Space')}</span>
                        <strong>{formatBytesVal(disk.free)} {t('available')}</strong>
                      </div>
                    </div>
                  </div>
                  <div className="cb-card-footer">
                    <div className="cb-card-unit"><code>mount: {disk.mount || '/'}</code></div>
                    <div className="cb-card-actions">
                      <button type="button" className="cb-btn secondary" onClick={() => navigateToPage('process_monitor')}>
                        <Activity size={13} /> {t('Process Monitor')}
                      </button>
                    </div>
                  </div>
                </div>

                {/* Hosted Infrastructure Summary */}
                <div className="cb-card">
                  <div className="cb-card-top">
                    <div className="cb-card-header">
                      <div className="cb-card-icon emerald"><Layers size={18} /></div>
                      <div className="cb-card-heading">
                        <div className="cb-card-title-row">
                          <h4 className="cb-card-title">{t('Hosted Infrastructure Summary')}</h4>
                        </div>
                        <div className="cb-card-meta">
                          <span>{users.length} {t('Accounts')}</span>
                          <span className="cb-dot">•</span>
                          <code className="cb-ver-tag">{websites.length} {t('Domains')}</code>
                        </div>
                      </div>
                      <span className="cb-status-pill running">
                        <span className="cb-status-dot" />
                        {t('Active')}
                      </span>
                    </div>
                    <div style={{ display: 'grid', gap: '8px', fontSize: '13px', marginTop: '4px' }}>
                      <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                        <span className="hint">{t('Total User Quota Used')}</span>
                        <strong>{formatBytesVal(totalAccountStorage)}</strong>
                      </div>
                      <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                        <span className="hint">{t('SSL Secured Domains')}</span>
                        <strong>{securedSitesCount} / {websites.length}</strong>
                      </div>
                      <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                        <span className="hint">{t('MariaDB Databases')}</span>
                        <strong>{dashSummary?.databases?.total ?? databases.length}</strong>
                      </div>
                    </div>
                  </div>
                  <div className="cb-card-footer">
                    <div className="cb-card-unit"><code>/var/www</code></div>
                    <div className="cb-card-actions">
                      <button type="button" className="cb-btn secondary" onClick={() => setActiveTab('accounts')}>
                        <Users size={13} /> {t('View Accounts')}
                      </button>
                    </div>
                  </div>
                </div>

                {/* Security & Daemons Posture */}
                <div className="cb-card">
                  <div className="cb-card-top">
                    <div className="cb-card-header">
                      <div className="cb-card-icon indigo"><Shield size={18} /></div>
                      <div className="cb-card-heading">
                        <div className="cb-card-title-row">
                          <h4 className="cb-card-title">{t('Security & Daemons Posture')}</h4>
                        </div>
                        <div className="cb-card-meta">
                          <span>Systemd & WAF</span>
                          <span className="cb-dot">•</span>
                          <code className="cb-ver-tag">
                            {dashSummary?.services?.running ?? 0}/{dashSummary?.services?.total ?? 0} {t('Daemons')}
                          </code>
                        </div>
                      </div>
                      <span className="cb-status-pill running">
                        <span className="cb-status-dot" />
                        {t('Protected')}
                      </span>
                    </div>
                    <div style={{ display: 'grid', gap: '8px', fontSize: '13px', marginTop: '4px' }}>
                      <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                        <span className="hint">{t('WAF Enabled Websites')}</span>
                        <strong>{wafSitesCount} / {websites.length}</strong>
                      </div>
                      <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                        <span className="hint">{t('Host Firewall Rules')}</span>
                        <strong>{dashSummary?.firewall?.rules_count ?? 0} {t('rules')}</strong>
                      </div>
                      <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                        <span className="hint">{t('Stored Backup Archives')}</span>
                        <strong>{dashSummary?.backups?.count ?? 0} ({formatBytesVal(dashSummary?.backups?.total_bytes ?? 0)})</strong>
                      </div>
                    </div>
                  </div>
                  <div className="cb-card-footer">
                    <div className="cb-card-unit"><code>services.monitor</code></div>
                    <div className="cb-card-actions">
                      <button type="button" className="cb-btn secondary" onClick={() => navigateToPage('services')}>
                        <Server size={13} /> {t('System Services')}
                      </button>
                    </div>
                  </div>
                </div>
              </div>

              {/* Raw Filesystem & Memory Telemetry */}
              {systemInfo && (
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(340px, 1fr))', gap: '16px' }}>
                  <div style={{ border: '1px solid var(--border)', borderRadius: '12px', overflow: 'hidden', background: 'var(--surface)' }}>
                    <div style={{ padding: '12px 16px', background: 'var(--surface-alt)', borderBottom: '1px solid var(--border)', fontWeight: 600 }}>
                      {t('Disk Partition Table (df -h /)')}
                    </div>
                    <pre style={{ margin: 0, padding: '14px 16px', fontSize: '12px', overflowX: 'auto', fontFamily: 'monospace', background: '#0f172a', color: '#e2e8f0' }}>
                      {systemInfo.disk || 'No partition telemetry available.'}
                    </pre>
                  </div>
                  <div style={{ border: '1px solid var(--border)', borderRadius: '12px', overflow: 'hidden', background: 'var(--surface)' }}>
                    <div style={{ padding: '12px 16px', background: 'var(--surface-alt)', borderBottom: '1px solid var(--border)', fontWeight: 600 }}>
                      {t('Memory & Swap Allocation (free -m)')}
                    </div>
                    <pre style={{ margin: 0, padding: '14px 16px', fontSize: '12px', overflowX: 'auto', fontFamily: 'monospace', background: '#0f172a', color: '#e2e8f0' }}>
                      {systemInfo.memory || 'No memory telemetry available.'}
                    </pre>
                  </div>
                </div>
              )}
            </>
          ) : (
            <div className="cb-grid">
              <div className="cb-card">
                <div className="cb-card-top">
                  <div className="cb-card-header">
                    <div className="cb-card-icon blue"><HardDrive size={18} /></div>
                    <div className="cb-card-heading">
                      <div className="cb-card-title-row">
                        <h4 className="cb-card-title">{t('Disk Quota & Storage')}</h4>
                      </div>
                      <div className="cb-card-meta">
                        <span>{currentUser?.username}</span>
                        <span className="cb-dot">•</span>
                        <code className="cb-ver-tag">{currentUser?.role}</code>
                      </div>
                    </div>
                    <span className="cb-status-pill running">
                      <span className="cb-status-dot" />
                      {myLimitBytes > 0 ? `${myStoragePct}% Used` : t('Active')}
                    </span>
                  </div>
                  <p className="cb-card-desc">
                    {myLimitBytes > 0
                      ? t('Using {u} out of {l} allocated storage space.', { u: formatBytesVal(myUsedBytes), l: formatBytesVal(myLimitBytes) })
                      : t('Using {u} storage space (Unlimited package quota).', { u: formatBytesVal(myUsedBytes) })}
                  </p>
                </div>
                <div className="cb-card-footer">
                  <div className="cb-card-unit"><code>/var/www</code></div>
                  <div className="cb-card-actions">
                    {(isAdmin || isReseller) ? (
                      <button type="button" className="cb-btn secondary" onClick={() => navigateToPage('users')}>
                        <Users size={13} /> {t('List Users')}
                      </button>
                    ) : (
                      <button type="button" className="cb-btn secondary" onClick={() => navigateToPage('files')}>
                        <FolderOpen size={13} /> {t('File Manager')}
                      </button>
                    )}
                  </div>
                </div>
              </div>

              <div className="cb-card">
                <div className="cb-card-top">
                  <div className="cb-card-header">
                    <div className="cb-card-icon emerald"><Lock size={18} /></div>
                    <div className="cb-card-heading">
                      <div className="cb-card-title-row">
                        <h4 className="cb-card-title">{t('SSL & WAF Protection')}</h4>
                      </div>
                      <div className="cb-card-meta">
                        <span>{securedSitesCount}/{websites.length} HTTPS</span>
                        <span className="cb-dot">•</span>
                        <code className="cb-ver-tag">{wafSitesCount}/{websites.length} WAF</code>
                      </div>
                    </div>
                    <span className={`cb-status-pill ${securedSitesCount === websites.length ? 'running' : 'stopped'}`}>
                      <span className="cb-status-dot" />
                      {securedSitesCount === websites.length ? t('All Secured') : t('Action Needed')}
                    </span>
                  </div>
                  <p className="cb-card-desc">
                    {t('Ensure all hosted domains have valid Let\'s Encrypt SSL certificates and Web Application Firewall protection enabled.')}
                  </p>
                </div>
                <div className="cb-card-footer">
                  <div className="cb-card-unit"><code>tls + modsec</code></div>
                  <div className="cb-card-actions">
                    <button type="button" className="cb-btn secondary" onClick={() => navigateToPage((isAdmin || isReseller) ? 'waf' : 'ssl')}>
                      <Lock size={13} /> {(isAdmin || isReseller) ? t('Manage WAF') : t('Manage SSL')}
                    </button>
                  </div>
                </div>
              </div>
            </div>
          )}
        </div>
      )}

      {/* TAB 2: WEBSITE & DOMAIN STATISTICS */}
      {activeTab === 'websites' && (
        <div style={{ display: 'grid', gap: '14px' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '10px' }}>
            <input
              type="text"
              value={domainFilter}
              onChange={e => setDomainFilter(e.target.value)}
              placeholder={t('Filter websites by domain, PHP version, or Linux user...')}
              style={{ maxWidth: '360px' }}
            />
            <span className="hint">{t('Showing {n} website(s)', { n: filteredWebsites.length })}</span>
          </div>

          <div style={{ border: '1px solid var(--border)', borderRadius: '12px', overflow: 'hidden', background: 'var(--surface)' }}>
            <div style={{ width: '100%', overflowX: 'auto' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse', textAlign: 'left', fontSize: '13.5px' }}>
                <thead>
                  <tr style={{ background: 'var(--surface-alt)', borderBottom: '1px solid var(--border)' }}>
                    <th style={{ padding: '12px 16px' }}>{t('Domain')}</th>
                    <th style={{ padding: '12px 16px' }}>{t('Linux User')}</th>
                    <th style={{ padding: '12px 16px' }}>{t('Runtime & Acceleration')}</th>
                    <th style={{ padding: '12px 16px' }}>{t('Bandwidth & HTTP Status')}</th>
                    <th style={{ padding: '12px 16px' }}>{t('SSL Status')}</th>
                    <th style={{ padding: '12px 16px' }}>{t('WAF')}</th>
                    <th style={{ padding: '12px 16px', textAlign: 'right' }}>{t('Quick Actions')}</th>
                  </tr>
                </thead>
                <tbody>
                  {filteredWebsites.map(site => {
                    const tItem = (domainTraffic?.items || []).find(row => Number(row.website_id) === Number(site.id) || row.domain === site.domain);
                    return (
                    <tr key={site.id} style={{ borderBottom: '1px solid var(--border)' }}>
                      <td style={{ padding: '12px 16px' }}>
                        <div style={{ fontWeight: 600 }}>{site.domain}</div>
                        <small className="hint"><code>{site.root_path}</code></small>
                      </td>
                      <td style={{ padding: '12px 16px' }}>
                        <code>{site.linux_user || 'www-data'}</code>
                      </td>
                      <td style={{ padding: '12px 16px' }}>
                        <div style={{ display: 'flex', flexWrap: 'wrap', gap: '4px' }}>
                          <span className="badge">PHP {site.php_version || '8.4'}</span>
                          {(tItem?.fastcgi_cache_enabled || tItem?.fastcgi_cache || site.app_type === 'wordpress') && <span className="badge ok" title={t('Nginx FastCGI Micro-Cache with WooCommerce Bypass')}>FastCGI Cache</span>}
                          {(tItem?.http3_quic_ready ?? tItem?.http3_quic ?? site.ssl_enabled) && <span className="badge ok" title={t('HTTP/3 QUIC UDP/443 Active')}>HTTP/3</span>}
                        </div>
                      </td>
                      <td style={{ padding: '12px 16px' }}>
                        <div style={{ fontWeight: 600 }}>
                          {formatBytesVal(tItem?.bandwidth_bytes ?? tItem?.bytes_sent ?? 0)}
                          <span className="hint" style={{ fontWeight: 400, marginLeft: '6px' }}>
                            ({tItem?.requests_total ?? tItem?.total_requests ?? 0} reqs)
                          </span>
                        </div>
                        <small className="hint">
                          2xx: {tItem?.status_2xx || 0} · 3xx: {tItem?.status_3xx || 0} · 4xx: {tItem?.status_4xx || 0} · <strong style={{ color: (tItem?.status_5xx || 0) > 0 ? 'var(--danger)' : 'inherit' }}>5xx: {tItem?.status_5xx || 0}</strong>
                        </small>
                      </td>
                      <td style={{ padding: '12px 16px' }}>
                        <span className={`badge ${site.ssl_enabled ? 'ok' : 'warn'}`}>
                          {site.ssl_enabled ? t('Secured (HTTPS)') : t('Unsecured (HTTP)')}
                        </span>
                      </td>
                      <td style={{ padding: '12px 16px' }}>
                        <span className={`badge ${site.waf_enabled ? 'ok' : ''}`}>
                          {site.waf_enabled ? t('Protected') : t('Off')}
                        </span>
                      </td>
                      <td style={{ padding: '12px 16px', textAlign: 'right' }}>
                        <div style={{ display: 'inline-flex', gap: '8px' }}>
                          {!isAdmin && !isReseller && (
                            <button
                              type="button"
                              className="cb-btn secondary"
                              onClick={() => {
                                if (setSelectedWebsiteId) setSelectedWebsiteId(String(site.id));
                                navigateToPage('files');
                              }}
                            >
                              <FolderOpen size={13} /> {t('Files')}
                            </button>
                          )}
                          <button
                            type="button"
                            className="cb-btn secondary"
                            onClick={() => {
                              if (setSelectedWebsiteId) setSelectedWebsiteId(String(site.id));
                              navigateToPage('access-logs');
                            }}
                          >
                            <FileText size={13} /> {t('Logs')}
                          </button>
                        </div>
                      </td>
                    </tr>
                    );
                  })}
                  {filteredWebsites.length === 0 && (
                    <tr>
                      <td colSpan={7} style={{ padding: '32px', textAlign: 'center' }} className="hint">
                        {t('No matching websites found.')}
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      )}

      {/* TAB 3: ACCOUNT STORAGE BREAKDOWN (Admin & Reseller) */}
      {(isAdmin || isReseller) && activeTab === 'accounts' && (
        <div style={{ display: 'grid', gap: '14px' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '10px' }}>
            <input
              type="text"
              value={accountFilter}
              onChange={e => setAccountFilter(e.target.value)}
              placeholder={t('Filter accounts by username, email, or role...')}
              style={{ maxWidth: '360px' }}
            />
            <span className="hint">{t('Total Storage Used: {s}', { s: formatBytesVal(totalAccountStorage) })}</span>
          </div>

          <div style={{ border: '1px solid var(--border)', borderRadius: '12px', overflow: 'hidden', background: 'var(--surface)' }}>
            <div style={{ width: '100%', overflowX: 'auto' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse', textAlign: 'left', fontSize: '13.5px' }}>
                <thead>
                  <tr style={{ background: 'var(--surface-alt)', borderBottom: '1px solid var(--border)' }}>
                    <th style={{ padding: '12px 16px' }}>{t('Account')}</th>
                    <th style={{ padding: '12px 16px' }}>{t('Role')}</th>
                    <th style={{ padding: '12px 16px' }}>{t('Websites')}</th>
                    <th style={{ padding: '12px 16px' }}>{t('Storage Used / Quota')}</th>
                    <th style={{ padding: '12px 16px' }}>{t('Status')}</th>
                    <th style={{ padding: '12px 16px', textAlign: 'right' }}>{t('Action')}</th>
                  </tr>
                </thead>
                <tbody>
                  {filteredUsers.map(u => {
                    const used = Number(u.storage_used_bytes) || 0;
                    const limitMb = Number(u.storage_limit_mb) || 0;
                    const limitBytes = limitMb > 0 ? limitMb * 1024 * 1024 : 0;
                    const pct = limitBytes > 0 ? Math.min(100, Math.round((used / limitBytes) * 100)) : 0;
                    const userSitesCount = websites.filter(w => Number(w.owner_id) === Number(u.id)).length;
                    return (
                      <tr key={u.id} style={{ borderBottom: '1px solid var(--border)' }}>
                        <td style={{ padding: '12px 16px' }}>
                          <div style={{ fontWeight: 600 }}>{u.username}</div>
                          <small className="hint">{u.email || ''}</small>
                        </td>
                        <td style={{ padding: '12px 16px' }}>
                          <span className="badge">{u.role}</span>
                        </td>
                        <td style={{ padding: '12px 16px' }}>
                          <strong>{userSitesCount}</strong> {u.website_limit ? `/ ${u.website_limit}` : ''}
                        </td>
                        <td style={{ padding: '12px 16px', minWidth: '210px' }}>
                          <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '12.5px', marginBottom: '4px' }}>
                            <strong>{formatBytesVal(used)}</strong>
                            <span className="hint">{limitBytes > 0 ? `${formatBytesVal(limitBytes)} (${pct}%)` : t('Unlimited')}</span>
                          </div>
                          {limitBytes > 0 && (
                            <div style={{ height: '6px', background: 'var(--surface-alt)', borderRadius: '999px', overflow: 'hidden' }}>
                              <div style={{ width: `${pct}%`, height: '100%', background: pct > 85 ? 'var(--danger)' : 'var(--primary)' }} />
                            </div>
                          )}
                        </td>
                        <td style={{ padding: '12px 16px' }}>
                          <span className={`badge ${u.is_active !== false ? 'ok' : 'warn'}`}>
                            {u.is_active !== false ? t('Active') : t('Suspended')}
                          </span>
                        </td>
                        <td style={{ padding: '12px 16px', textAlign: 'right' }}>
                          <button type="button" className="cb-btn secondary" onClick={() => navigateToPage('users')}>
                            {t('Manage User')}
                          </button>
                        </td>
                      </tr>
                    );
                  })}
                  {filteredUsers.length === 0 && (
                    <tr>
                      <td colSpan={6} style={{ padding: '32px', textAlign: 'center' }} className="hint">
                        {t('No matching accounts found.')}
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
