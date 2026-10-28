import React, { useState, useEffect, useMemo } from 'react';
import {
  Activity, AlertTriangle, CheckCircle, Cpu, Database, Globe, HardDrive,
  Lock, Unlock, Network, Pencil, RefreshCw, Search, Server, ShieldAlert,
  Sliders, Sparkles, Users, Wrench, X, Zap, Eye
} from 'lucide-react';
import { DiskQuotaCard } from '../components/DiskQuotaCard.jsx';
import { EmptyState } from '../components/EmptyState.jsx';
import { useAppContext } from '../contexts/AppContext.jsx';
import { t } from '../i18n.js';
import { roleLabel } from '../utils/helpers.js';

function fmtBytes(bytes) {
  const num = Number(bytes) || 0;
  if (num <= 0) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  const i = Math.min(units.length - 1, Math.floor(Math.log(num) / Math.log(1024)));
  return `${(num / Math.pow(1024, i)).toFixed(i === 0 ? 0 : 1)} ${units[i]}`;
}

function barColor(pct, locked = false) {
  if (locked || pct >= 95) return '#ef4444';
  if (pct >= 75) return '#f59e0b';
  return 'var(--primary)';
}

function UsageProgressCell({ usedLabel, limitLabel, percent, subLabel, alertBadge }) {
  const numPct = Number(percent) || 0;
  const clamped = Math.min(100, Math.max(0, numPct));
  const color = barColor(numPct, !!alertBadge);
  return (
    <div className="rm-usage-cell">
      <div className="rm-usage-top">
        <strong className="rm-usage-used">{usedLabel}</strong>
        <span className="rm-usage-limit">/ {limitLabel}</span>
      </div>
      <div className="rm-progress-track">
        <div
          className="rm-progress-fill"
          style={{
            width: `${clamped}%`,
            minWidth: numPct > 0 ? '4px' : 0,
            background: color,
          }}
        />
      </div>
      <div className="rm-usage-sub">
        <span className="rm-usage-sublabel">{subLabel}</span>
        {alertBadge ? (
          <span className="badge danger" style={{ fontSize: '10px', padding: '1px 6px' }}>{alertBadge}</span>
        ) : (
          <span className="rm-usage-pct">{Math.round(numPct)}%</span>
        )}
      </div>
    </div>
  );
}

export function ResourceMonitorPage({ request, isAdmin, isReseller }) {
  const { loading, setLoading, setNotice, setError, packages, loadPackages } = useAppContext();

  const [data, setData] = useState({ summary: {}, items: [] });
  const [searchQuery, setSearchQuery] = useState('');
  const [statusFilter, setStatusFilter] = useState('all');
  const [creatorFilter, setCreatorFilter] = useState('all');
  const [sortBy, setSortBy] = useState('mysql');
  const [autoRefresh, setAutoRefresh] = useState(true);

  const [selectedUser, setSelectedUser] = useState(null);
  const [userDetail, setUserDetail] = useState(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [activeDetailTab, setActiveDetailTab] = useState('mysql');

  const [inspectTablesModal, setInspectTablesModal] = useState(null);
  const [limitsForm, setLimitsForm] = useState(null);
  const [bottlenecksOpen, setBottlenecksOpen] = useState(false);
  const [bottlenecksData, setBottlenecksData] = useState(null);
  const [bottlenecksLoading, setBottlenecksLoading] = useState(false);

  async function loadBottlenecks(silent = false) {
    if (!silent) setBottlenecksLoading(true);
    try {
      const res = await request('/resources/bottlenecks?limit=30', { silent: true });
      if (res) setBottlenecksData(res);
    } finally {
      if (!silent) setBottlenecksLoading(false);
    }
  }

  async function loadResourceUsers(silent = false) {
    const res = await request('/resources/users', { silent }, silent ? '' : t('Loading user resource telemetry...'));
    if (res && Array.isArray(res.items)) {
      setData(res);
      if (selectedUser) {
        const updated = res.items.find(i => i.id === selectedUser.id);
        if (updated) setSelectedUser(updated);
      }
    }
  }

  async function handleForceScan() {
    setLoading(t('Scanning MySQL databases, Cgroups & storage quotas...'));
    try {
      await request('/resources/refresh?refresh_disk=true', { method: 'POST', silent: true });
      await loadResourceUsers(true);
      if (selectedUser) {
        await openUserDetail(selectedUser, activeDetailTab);
      }
      setNotice(t('Resource & MySQL telemetry refreshed.'));
    } finally {
      setLoading('');
    }
  }

  useEffect(() => {
    loadResourceUsers(false);
    if (loadPackages && (!packages || packages.length === 0)) loadPackages();
  }, []);

  useEffect(() => {
    if (!autoRefresh) return undefined;
    const timer = setInterval(() => {
      loadResourceUsers(true);
    }, 15000);
    return () => clearInterval(timer);
  }, [autoRefresh, selectedUser?.id]);

  async function openUserDetail(userRow, tab = 'mysql') {
    setSelectedUser(userRow);
    setActiveDetailTab(tab);
    setLimitsForm({
      package_id: userRow.package_id || '',
      cpu_quota_percent: userRow.cpu_quota_percent ?? 100,
      memory_high_mb: userRow.memory_high_mb ?? 1024,
      memory_max_mb: userRow.memory_max_mb ?? 2048,
      tasks_max: userRow.tasks_max ?? 512,
      io_read_mbps: userRow.io_read_mbps ?? 0,
      io_write_mbps: userRow.io_write_mbps ?? 0,
      iops_read_max: userRow.iops_read_max ?? 0,
      iops_write_max: userRow.iops_write_max ?? 0,
      storage_limit_mb: userRow.storage_limit_mb ?? 1024,
      mysql_storage_limit_mb: userRow.mysql_storage_limit_mb ?? 0,
      mysql_max_db_size_mb: userRow.mysql_max_db_size_mb ?? 0,
      mysql_max_queries_per_hour: userRow.mysql_max_queries_per_hour ?? 0,
      mysql_max_updates_per_hour: userRow.mysql_max_updates_per_hour ?? 0,
      mysql_max_connections_per_hour: userRow.mysql_max_connections_per_hour ?? 0,
      bandwidth_limit_mb: userRow.bandwidth_limit_mb ?? 0,
      inode_limit: userRow.inode_limit ?? 0,
      database_limit: userRow.database_limit ?? 5,
    });
    setDetailLoading(true);
    try {
      const detail = await request(`/resources/users/${userRow.id}/detail`, { silent: true });
      if (detail) setUserDetail(detail);
    } finally {
      setDetailLoading(false);
    }
  }

  async function handleToggleWriteLock(dbItem) {
    const nextLocked = !dbItem.write_locked;
    const msg = nextLocked
      ? t('Lock write privileges (INSERT/UPDATE/CREATE) for database "{db}"? Website will remain readable (SELECT) and user can still DELETE/TRUNCATE old data.', { db: dbItem.db_name })
      : t('Restore full Read/Write privileges for database "{db}"?', { db: dbItem.db_name });
    if (!confirm(msg)) return;

    const res = await request(
      `/resources/databases/${dbItem.id}/write-lock`,
      {
        method: 'POST',
        body: JSON.stringify({ locked: nextLocked }),
      },
      nextLocked ? t('Locking database write access...') : t('Unlocking database write access...')
    );
    if (res) {
      setNotice(nextLocked ? t('Database {db} is now Write-Locked (Read-Only).', { db: dbItem.db_name }) : t('Database {db} write access restored.', { db: dbItem.db_name }));
      await loadResourceUsers(true);
      if (selectedUser) await openUserDetail(selectedUser, activeDetailTab);
    }
  }

  async function handleInspectTables(dbItem) {
    const res = await request(`/resources/databases/${dbItem.id}/tables`, {}, t('Inspecting tables in {db}...', { db: dbItem.db_name }));
    if (res) {
      setInspectTablesModal(res);
    }
  }

  async function handleOptimizeDatabase(dbItem) {
    const res = await request(`/resources/databases/${dbItem.id}/optimize`, { method: 'POST' }, t('Optimizing tables in {db}...', { db: dbItem.db_name }));
    if (res) {
      setNotice(t('Optimized {n} table(s) in {db}. Current size: {size}', {
        n: res.optimized_tables || 0,
        db: dbItem.db_name,
        size: fmtBytes(res.size_bytes),
      }));
      await loadResourceUsers(true);
      if (selectedUser) await openUserDetail(selectedUser, activeDetailTab);
    }
  }

  async function handleToggleAttackShield(userRow, e) {
    if (e) e.stopPropagation();
    const nextEnabled = !userRow.under_attack;
    const msg = nextEnabled
      ? t('Enable 5-Layer Anti-DDoS & MySQL Anti-Scraping Shield for user "{user}"? All domains of this user will activate JS Browser Challenge, strict per-vhost connection caps, and MariaDB Governor limits (8 conns, 3s query timeout) so other users on the VPS remain 100% unaffected.', { user: userRow.username })
      : t('Disable Anti-DDoS & Anti-Scraping Shield for user "{user}" and restore normal website traffic settings?', { user: userRow.username });
    if (!confirm(msg)) return;

    const res = await request(
      `/resources/users/${userRow.id}/attack-mode`,
      {
        method: 'POST',
        body: JSON.stringify({ enabled: nextEnabled }),
      },
      nextEnabled ? t('Activating 5-Layer Blast-Radius & MySQL Anti-Scraping Shield...') : t('Restoring normal traffic mode...')
    );
    if (res) {
      setNotice(
        nextEnabled
          ? t('5-Layer Attack & Anti-Scraping Shield enabled for {user}. Other VPS users are protected.', { user: userRow.username })
          : t('Attack Isolation Shield disabled for {user}.', { user: userRow.username })
      );
      await loadResourceUsers(true);
      if (selectedUser && selectedUser.id === userRow.id) {
        await openUserDetail({ ...selectedUser, under_attack: nextEnabled }, activeDetailTab);
      }
    }
  }

  async function handleKillSlowQueries(userRow, e) {
    if (e) e.stopPropagation();
    const res = await request(
      `/resources/users/${userRow.id}/kill-slow-queries`,
      { method: 'POST' },
      t('Sniping slow/heavy SELECT queries in MariaDB...')
    );
    if (res) {
      setNotice(
        t('Terminated {n} slow/scraping SQL queries for {user}.', {
          n: res.killed_count || 0,
          user: userRow.username,
        })
      );
      await loadResourceUsers(true);
      if (selectedUser && selectedUser.id === userRow.id) {
        await openUserDetail(selectedUser, activeDetailTab);
      }
    }
  }

  async function handleSaveLimits(syncFromPkg = false) {
    if (!selectedUser || !limitsForm) return;
    const payload = syncFromPkg
      ? { sync_from_package: true, package_id: limitsForm.package_id ? Number(limitsForm.package_id) : null }
      : {
          sync_from_package: false,
          package_id: limitsForm.package_id ? Number(limitsForm.package_id) : null,
          cpu_quota_percent: Number(limitsForm.cpu_quota_percent || 0),
          memory_high_mb: Number(limitsForm.memory_high_mb || 0),
          memory_max_mb: Number(limitsForm.memory_max_mb || 0),
          tasks_max: Number(limitsForm.tasks_max || 0),
          io_read_mbps: Number(limitsForm.io_read_mbps || 0),
          io_write_mbps: Number(limitsForm.io_write_mbps || 0),
          iops_read_max: Number(limitsForm.iops_read_max || 0),
          iops_write_max: Number(limitsForm.iops_write_max || 0),
          storage_limit_mb: Number(limitsForm.storage_limit_mb || 0),
          mysql_storage_limit_mb: Number(limitsForm.mysql_storage_limit_mb || 0),
          mysql_max_db_size_mb: Number(limitsForm.mysql_max_db_size_mb || 0),
          mysql_max_queries_per_hour: Number(limitsForm.mysql_max_queries_per_hour || 0),
          mysql_max_updates_per_hour: Number(limitsForm.mysql_max_updates_per_hour || 0),
          mysql_max_connections_per_hour: Number(limitsForm.mysql_max_connections_per_hour || 0),
          bandwidth_limit_mb: Number(limitsForm.bandwidth_limit_mb || 0),
          inode_limit: Number(limitsForm.inode_limit || 0),
          database_limit: Number(limitsForm.database_limit || 0),
        };
    const res = await request(
      `/resources/users/${selectedUser.id}/limits`,
      { method: 'POST', body: JSON.stringify(payload) },
      t('Applying Cgroups v2 & MySQL quota limits...')
    );
    if (res) {
      setNotice(res.message || t('Updated resource limits.'));
      await loadResourceUsers(true);
      await openUserDetail(selectedUser, 'limits');
    }
  }

  const filteredItems = useMemo(() => {
    return (data.items || [])
      .filter(item => {
        if (statusFilter === 'alert' && item.status === 'normal') return false;
        if (statusFilter === 'mysql_locked' && item.locked_databases_count === 0) return false;
        if (statusFilter === 'under_attack' && !item.under_attack) return false;
        if (creatorFilter === 'admin' && item.owner_role !== 'admin') return false;
        if (creatorFilter === 'reseller' && item.owner_role !== 'reseller') return false;
        if (searchQuery.trim()) {
          const q = searchQuery.trim().toLowerCase();
          const match =
            (item.username || '').toLowerCase().includes(q) ||
            (item.email || '').toLowerCase().includes(q) ||
            (item.package_name || '').toLowerCase().includes(q) ||
            (item.owner_username || '').toLowerCase().includes(q);
          if (!match) return false;
        }
        return true;
      })
      .sort((a, b) => {
        if (sortBy === 'mysql') return (b.mysql_used_bytes || 0) - (a.mysql_used_bytes || 0);
        if (sortBy === 'mysql_cpu') {
          return ((b.mysql_cpu_percent || 0) * 1000 + (b.mysql_rows_read_rate || 0)) -
                 ((a.mysql_cpu_percent || 0) * 1000 + (a.mysql_rows_read_rate || 0));
        }
        if (sortBy === 'storage') return (b.storage_used_bytes || 0) - (a.storage_used_bytes || 0);
        if (sortBy === 'cpu') return (b.cpu_percent || 0) - (a.cpu_percent || 0);
        if (sortBy === 'ram') return (b.memory_used_bytes || 0) - (a.memory_used_bytes || 0);
        if (sortBy === 'bandwidth') return (b.bandwidth_used_bytes || 0) - (a.bandwidth_used_bytes || 0);
        return b.id - a.id;
      });
  }, [data.items, statusFilter, creatorFilter, searchQuery, sortBy]);

  if (!isAdmin && !isReseller) {
    return (
      <div className="section">
        <h2>{t('User Resource Monitor')}</h2>
        <p className="hint">{t('No permission.')}</p>
      </div>
    );
  }

  const summary = data.summary || {};
  const rowGridTemplate = 'minmax(180px, 1.3fr) repeat(5, minmax(140px, 1fr)) 168px';

  return (
    <div className="section">
      {/* Header */}
      <div className="section-title" style={{ marginBottom: '14px', flexWrap: 'wrap', gap: '12px' }}>
        <div>
          <h2 style={{ display: 'flex', alignItems: 'center', gap: '10px', margin: 0 }}>
            <Cpu size={20} />
            <span>{t('User Resource & MySQL Quota Monitor')}</span>
          </h2>
          <p className="hint" style={{ marginTop: '4px' }}>
            {t('Real-time per-user CPU, RAM, Disk I/O, Bandwidth, and automatic MySQL bloat protection tied to Package limits.')}
          </p>
        </div>
        <div className="cb-toolbar-actions" style={{ display: 'flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap' }}>
          <label className="check-line" style={{ margin: 0, fontSize: '13px', display: 'flex', alignItems: 'center', gap: '6px', cursor: 'pointer' }}>
            <input type="checkbox" checked={autoRefresh} onChange={e => setAutoRefresh(e.target.checked)} style={{ width: '15px', height: '15px', minHeight: 0 }} />
            <span>{t('Live 15s')}</span>
          </label>
          <button
            type="button"
            className={bottlenecksOpen ? 'primary' : 'secondary-light'}
            disabled={!!loading}
            onClick={() => {
              const next = !bottlenecksOpen;
              setBottlenecksOpen(next);
              if (next) loadBottlenecks(false);
            }}
          >
            <Activity size={14} /> {t('Slow Query & PHP Bottleneck Inspector')}
          </button>
          <button type="button" className="secondary-light" disabled={!!loading} onClick={() => { loadResourceUsers(false); if (bottlenecksOpen) loadBottlenecks(true); }}>
            <RefreshCw size={14} /> {t('Refresh')}
          </button>
          <button type="button" disabled={!!loading} onClick={handleForceScan}>
            <Sparkles size={14} /> {t('Deep Scan Quotas')}
          </button>
        </div>
      </div>

      {isAdmin && <DiskQuotaCard request={request} setNotice={setNotice} busy={!!loading} />}

      {/* MySQL Slow Query & PHP-FPM Slowlog Bottleneck Inspector */}
      {bottlenecksOpen && (
        <div
          className="card"
          style={{
            marginBottom: '18px',
            border: '1px solid var(--primary)',
            background: 'var(--surface)',
            padding: '16px',
          }}
        >
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '10px', marginBottom: '12px' }}>
            <div>
              <h3 style={{ margin: 0, display: 'flex', alignItems: 'center', gap: '8px', fontSize: '15px' }}>
                <Activity size={17} />
                <span>{t('MySQL Slow Query & PHP-FPM Slowlog Bottleneck Inspector')}</span>
                <span className="badge ok">Auto-Indexed</span>
              </h3>
              <p className="hint" style={{ margin: '3px 0 0' }}>
                {t('Pinpoints exact SQL queries (>2s) and PHP-FPM function stack traces (>5s) causing server CPU or I/O bottlenecks.')}
              </p>
            </div>
            <div style={{ display: 'flex', gap: '8px' }}>
              <button type="button" className="mini secondary-light" disabled={bottlenecksLoading} onClick={() => loadBottlenecks(false)}>
                <RefreshCw size={13} /> {t('Rescan Logs')}
              </button>
              <button type="button" className="mini secondary-light" onClick={() => setBottlenecksOpen(false)}>
                <X size={14} />
              </button>
            </div>
          </div>

          {bottlenecksLoading ? (
            <p className="hint">{t('Analyzing MariaDB slow query log, live PROCESSLIST, and PHP-FPM slowlog stack traces...')}</p>
          ) : (
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(360px, 1fr))', gap: '14px' }}>
              <div style={{ background: 'var(--surface-alt)', border: '1px solid var(--border)', borderRadius: '10px', padding: '12px' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '8px' }}>
                  <strong style={{ fontSize: '13px', display: 'inline-flex', alignItems: 'center', gap: '6px' }}>
                    <Database size={14} /> {t('MySQL Slow Queries')} ({bottlenecksData?.mysql_slow_queries?.length || 0})
                  </strong>
                  <span className="badge">{t('Threshold')}: &ge;{bottlenecksData?.summary?.long_query_time_sec || 2}s</span>
                </div>
                {(!bottlenecksData?.mysql_slow_queries || bottlenecksData.mysql_slow_queries.length === 0) ? (
                  <p className="hint" style={{ margin: 0 }}>{t('No slow SQL queries detected. MariaDB InnoDB buffer pool & indexes are healthy.')}</p>
                ) : (
                  <div className="rm-sublist" style={{ maxHeight: '260px', overflowY: 'auto' }}>
                    {bottlenecksData.mysql_slow_queries.map((q, idx) => (
                      <div className="rm-sublist-row" key={`msq-${idx}`} style={{ flexDirection: 'column', alignItems: 'stretch', gap: '4px', padding: '8px 10px' }}>
                        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', fontSize: '12px', flexWrap: 'wrap', gap: '6px' }}>
                          <span>
                            <strong>{q.username}</strong> · <code>{q.db_name}</code>
                            {q.source === 'live_processlist' && <span className="badge danger" style={{ marginLeft: '6px' }}>LIVE</span>}
                          </span>
                          <span className="badge warn">
                            {q.query_time_sec}s {q.rows_examined ? `· ${q.rows_examined.toLocaleString()} rows` : ''}
                          </span>
                        </div>
                        <code style={{ fontSize: '11.5px', color: 'var(--text-secondary)', wordBreak: 'break-all', whiteSpace: 'pre-wrap' }}>
                          {q.sql}
                        </code>
                      </div>
                    ))}
                  </div>
                )}
              </div>

              <div style={{ background: 'var(--surface-alt)', border: '1px solid var(--border)', borderRadius: '10px', padding: '12px' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '8px' }}>
                  <strong style={{ fontSize: '13px', display: 'inline-flex', alignItems: 'center', gap: '6px' }}>
                    <Cpu size={14} /> {t('PHP-FPM Slowlog Stack Traces')} ({bottlenecksData?.php_slow_traces?.length || 0})
                  </strong>
                  <span className="badge">{t('Threshold')}: &ge;{bottlenecksData?.summary?.php_slowlog_timeout_sec || 5}s</span>
                </div>
                {(!bottlenecksData?.php_slow_traces || bottlenecksData.php_slow_traces.length === 0) ? (
                  <p className="hint" style={{ margin: 0 }}>{t('No slow PHP-FPM scripts or hanging plugin calls recorded.')}</p>
                ) : (
                  <div className="rm-sublist" style={{ maxHeight: '260px', overflowY: 'auto' }}>
                    {bottlenecksData.php_slow_traces.map((tr, idx) => (
                      <div className="rm-sublist-row" key={`php-tr-${idx}`} style={{ flexDirection: 'column', alignItems: 'stretch', gap: '4px', padding: '8px 10px' }}>
                        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', fontSize: '12px', flexWrap: 'wrap', gap: '6px' }}>
                          <span>
                            <strong>{tr.username}</strong> · <code>{tr.domain}</code>
                          </span>
                          <span className="badge">{tr.top_frame}</span>
                        </div>
                        <small style={{ fontSize: '11px', color: 'var(--text-secondary)', wordBreak: 'break-all' }}>
                          {tr.script_filename}
                        </small>
                        {tr.stack?.length > 0 && (
                          <pre style={{ margin: '2px 0 0', fontSize: '10.5px', padding: '6px', borderRadius: '6px', background: 'var(--surface)', overflowX: 'auto' }}>
                            {tr.stack.join('\n')}
                          </pre>
                        )}
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </div>
          )}
        </div>
      )}

      {/* Top KPI Strip */}
      <div className="cb-stats-strip" style={{ marginBottom: '16px' }}>
        <div className="cb-stat-box">
          <div className="cb-stat-icon blue"><Cpu size={18} /></div>
          <div className="cb-stat-info">
            <span className="cb-stat-label">{t('User CPU & RAM Load')}</span>
            <strong>
              {summary.total_cpu_percent ?? 0}% CPU
              <small className="cb-stat-sub">
                MySQL CPU: {summary.total_mysql_cpu_percent ?? 0}% · RAM: {fmtBytes(summary.total_memory_used_bytes)}
              </small>
            </strong>
          </div>
        </div>
        <div className="cb-stat-box">
          <div className="cb-stat-icon amber"><Database size={18} /></div>
          <div className="cb-stat-info">
            <span className="cb-stat-label">{t('MySQL Total Footprint')}</span>
            <strong>
              {fmtBytes(summary.total_mysql_used_bytes)}
              <small className="cb-stat-sub">
                {summary.total_databases || 0} DBs · {summary.total_mysql_qps ?? 0} QPS · {summary.total_locked_databases || 0} {t('Write-Locked')}
              </small>
            </strong>
          </div>
        </div>
        <div className="cb-stat-box">
          <div className="cb-stat-icon emerald"><HardDrive size={18} /></div>
          <div className="cb-stat-info">
            <span className="cb-stat-label">{t('Combined User Storage')}</span>
            <strong>
              {fmtBytes(summary.total_storage_used_bytes)}
              <small className="cb-stat-sub">
                {t('Web + MySQL + Mail + Apps')}
              </small>
            </strong>
          </div>
        </div>
        <div className="cb-stat-box">
          <div className="cb-stat-icon indigo"><Network size={18} /></div>
          <div className="cb-stat-info">
            <span className="cb-stat-label">{t('Monthly Bandwidth & Alerts')}</span>
            <strong>
              {fmtBytes(summary.total_bandwidth_used_bytes)}
              <small className="cb-stat-sub">
                {summary.warning_or_critical_users || 0} {t('account(s) near/over quota')} · {summary.under_attack_users || 0} {t('Attack Shield')}
              </small>
            </strong>
          </div>
        </div>
      </div>

      {/* Filter & Sort Controls (Single-row horizontal toolbar) */}
      <div
        className="rm-filter-bar"
        style={{
          display: 'flex',
          flexDirection: 'row',
          alignItems: 'center',
          justifyContent: 'space-between',
          flexWrap: 'wrap',
          gap: '12px',
          padding: '12px 16px',
          marginBottom: '16px',
          background: 'var(--surface-alt)',
          border: '1px solid var(--border)',
          borderRadius: '12px',
          minHeight: 0,
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: '10px', flexWrap: 'wrap', flex: '1 1 520px', minWidth: 0 }}>
          <div style={{ position: 'relative', display: 'flex', alignItems: 'center', flex: '1 1 260px', maxWidth: '360px', minWidth: '220px' }}>
            <Search size={15} style={{ position: 'absolute', left: '12px', color: 'var(--text-muted)', pointerEvents: 'none' }} />
            <input
              type="text"
              placeholder={t('Search username, package, creator...')}
              value={searchQuery}
              onChange={e => setSearchQuery(e.target.value)}
              style={{
                margin: 0,
                width: '100%',
                height: '38px',
                minHeight: '38px',
                padding: searchQuery ? '0 32px 0 36px' : '0 12px 0 36px',
                fontSize: '13.5px',
                background: 'var(--surface)',
              }}
            />
            {searchQuery && (
              <button
                type="button"
                onClick={() => setSearchQuery('')}
                title={t('Clear search')}
                style={{
                  position: 'absolute',
                  right: '6px',
                  width: '24px',
                  height: '24px',
                  minHeight: '24px',
                  padding: 0,
                  borderRadius: '6px',
                  background: 'transparent',
                  color: 'var(--text-muted)',
                  border: 'none',
                  boxShadow: 'none',
                }}
              >
                <X size={14} />
              </button>
            )}
          </div>
          <select
            value={statusFilter}
            onChange={e => setStatusFilter(e.target.value)}
            style={{
              margin: 0,
              width: 'auto',
              minWidth: '185px',
              height: '38px',
              minHeight: '38px',
              padding: '0 12px',
              fontSize: '13.5px',
              background: 'var(--surface)',
            }}
          >
            <option value="all">{t('All Statuses')}</option>
            <option value="alert">{t('Near / Over Quota (≥80%)')}</option>
            <option value="mysql_locked">{t('MySQL Write-Locked Only')}</option>
            <option value="under_attack">{t('Under Attack Shield Active')}</option>
          </select>
          {isAdmin && (
            <select
              value={creatorFilter}
              onChange={e => setCreatorFilter(e.target.value)}
              style={{
                margin: 0,
                width: 'auto',
                minWidth: '215px',
                height: '38px',
                minHeight: '38px',
                padding: '0 12px',
                fontSize: '13.5px',
                background: 'var(--surface)',
              }}
            >
              <option value="all">{t('All Creators (Admin & Reseller)')}</option>
              <option value="admin">{t('Created by Admin')}</option>
              <option value="reseller">{t('Created by Reseller')}</option>
            </select>
          )}
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: '10px', flexWrap: 'wrap', marginLeft: 'auto' }}>
          <span className="badge" style={{ height: '30px', padding: '0 10px', display: 'inline-flex', alignItems: 'center', fontSize: '12px' }}>
            {filteredItems.length} / {(data.items || []).length} {t('Users')}
          </span>
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
            <span style={{ fontSize: '13px', color: 'var(--text-secondary)', fontWeight: 600, whiteSpace: 'nowrap' }}>{t('Sort by')}:</span>
            <select
              value={sortBy}
              onChange={e => setSortBy(e.target.value)}
              style={{
                margin: 0,
                width: 'auto',
                minWidth: '195px',
                height: '38px',
                minHeight: '38px',
                padding: '0 12px',
                fontSize: '13.5px',
                background: 'var(--surface)',
              }}
            >
              <option value="mysql">{t('Highest MySQL Usage')}</option>
              <option value="mysql_cpu">{t('Highest MySQL CPU / Scraping Load')}</option>
              <option value="storage">{t('Highest Total Storage')}</option>
              <option value="cpu">{t('Highest CPU Usage')}</option>
              <option value="ram">{t('Highest RAM Usage')}</option>
              <option value="bandwidth">{t('Highest Bandwidth')}</option>
            </select>
          </div>
        </div>
      </div>

      {/* Selected User Detail Drawer / Card */}
      {selectedUser && (
        <div
          style={{
            marginBottom: '20px',
            border: selectedUser.under_attack ? '2px solid #ef4444' : '2px solid var(--primary)',
            borderRadius: '14px',
            padding: '18px 20px',
            background: 'var(--surface)',
            display: 'flex',
            flexDirection: 'column',
            alignItems: 'stretch',
            gap: '14px',
          }}
        >
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', flexWrap: 'wrap', gap: '12px' }}>
            <div>
              <div style={{ display: 'flex', alignItems: 'center', gap: '10px', flexWrap: 'wrap' }}>
                <h3 style={{ margin: 0, fontSize: '17px' }}>
                  {t('Resource Inspector')}: <code>{selectedUser.username}</code>
                </h3>
                <span className="badge">{selectedUser.package_name}</span>
                <span className="badge">{t('Created by')}: {selectedUser.owner_username} ({t(roleLabel(selectedUser.owner_role))})</span>
                {selectedUser.under_attack && (
                  <span className="badge danger" title={selectedUser.under_attack_reason || ''}>
                    <ShieldAlert size={12} style={{ marginRight: '4px' }} />
                    {t('5-Layer Attack Shield Active')}
                  </span>
                )}
                {selectedUser.locked_databases_count > 0 && (
                  <span className="badge danger">
                    <Lock size={12} style={{ marginRight: '4px' }} />
                    {selectedUser.locked_databases_count} {t('MySQL DB Write-Locked')}
                  </span>
                )}
              </div>
              <small style={{ color: 'var(--text-secondary)' }}>
                {selectedUser.email}
                {selectedUser.under_attack_reason ? ` · ${selectedUser.under_attack_reason}` : ''}
              </small>
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap' }}>
              <button
                type="button"
                className="mini secondary-light"
                disabled={!!loading}
                onClick={e => handleKillSlowQueries(selectedUser, e)}
                title={t('Immediately kill slow SELECT queries (>1s) running on this user\'s databases')}
              >
                <Zap size={14} /> {t('Kill Slow SQL')}
              </button>
              <button
                type="button"
                className={selectedUser.under_attack ? 'mini danger' : 'mini secondary-light'}
                disabled={!!loading}
                onClick={() => handleToggleAttackShield(selectedUser)}
              >
                <ShieldAlert size={14} />
                {selectedUser.under_attack ? t('Disable Attack Shield') : t('Isolate / Attack Shield')}
              </button>
              <button type="button" className="secondary-light mini" onClick={() => { setSelectedUser(null); setUserDetail(null); }}>
                <X size={14} /> {t('Close')}
              </button>
            </div>
          </div>

          {/* Live MySQL Governor & Anti-Scraping Telemetry Bar */}
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: '12px', padding: '10px 14px', background: 'var(--surface-alt)', borderRadius: '8px', border: '1px solid var(--border)', fontSize: '12.5px' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
              <Database size={15} style={{ color: 'var(--primary)' }} />
              <strong>{t('MySQL Governor & Anti-Scraping')}:</strong>
              <span className="badge ok">InnoDB Anti-Pollution Active</span>
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: '16px', flexWrap: 'wrap' }}>
              <span>PHP CPU: <strong>{selectedUser.php_cpu_percent || 0}%</strong></span>
              <span>MySQL CPU: <strong style={{ color: (selectedUser.mysql_cpu_percent || 0) >= 35 ? '#ef4444' : 'var(--text)' }}>{selectedUser.mysql_cpu_percent || 0}%</strong></span>
              <span>SQL Rate: <strong>{selectedUser.mysql_qps || 0} QPS</strong></span>
              <span>{t('Rows Scanned')}: <strong style={{ color: (selectedUser.mysql_rows_read_rate || 0) >= 30000 ? '#ef4444' : 'var(--text)' }}>{(selectedUser.mysql_rows_read_rate || 0).toLocaleString()} rows/s</strong></span>
              <span>{t('Sniped Slow Queries')}: <strong>{selectedUser.mysql_sniped_queries || 0}</strong></span>
            </div>
          </div>

          {/* Storage Composition Breakdown Bar */}
          {userDetail?.breakdown && (() => {
            const b = userDetail.breakdown;
            const tot = Math.max(1, b.storage_used_bytes || 1);
            const webPct = Math.round((b.web_used_bytes / tot) * 100);
            const mysqlPct = Math.round((b.mysql_used_bytes / tot) * 100);
            const mailPct = Math.round((b.mail_used_bytes / tot) * 100);
            const appPct = Math.max(0, 100 - webPct - mysqlPct - mailPct);
            return (
              <div style={{ padding: '12px 14px', background: 'var(--surface-alt)', borderRadius: '8px', border: '1px solid var(--border)' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '6px', fontSize: '12px', flexWrap: 'wrap', gap: '8px' }}>
                  <strong>{t('Storage Composition Breakdown')} ({fmtBytes(b.storage_used_bytes)} / {selectedUser.storage_limit_mb ? `${selectedUser.storage_limit_mb} MB` : t('Unlimited')})</strong>
                  <div style={{ display: 'flex', gap: '14px', flexWrap: 'wrap' }}>
                    <span><span style={{ display: 'inline-block', width: 8, height: 8, borderRadius: '50%', background: '#3b82f6', marginRight: 5 }} />Web: <strong>{fmtBytes(b.web_used_bytes)}</strong></span>
                    <span><span style={{ display: 'inline-block', width: 8, height: 8, borderRadius: '50%', background: '#f59e0b', marginRight: 5 }} />MySQL: <strong>{fmtBytes(b.mysql_used_bytes)}</strong></span>
                    <span><span style={{ display: 'inline-block', width: 8, height: 8, borderRadius: '50%', background: '#10b981', marginRight: 5 }} />Mail: <strong>{fmtBytes(b.mail_used_bytes)}</strong></span>
                    <span><span style={{ display: 'inline-block', width: 8, height: 8, borderRadius: '50%', background: '#8b5cf6', marginRight: 5 }} />Apps: <strong>{fmtBytes(b.app_used_bytes)}</strong></span>
                  </div>
                </div>
                <div style={{ display: 'flex', height: '10px', borderRadius: '999px', overflow: 'hidden', background: 'var(--border)' }}>
                  <div style={{ width: `${webPct}%`, background: '#3b82f6' }} title={`Web: ${fmtBytes(b.web_used_bytes)}`} />
                  <div style={{ width: `${mysqlPct}%`, background: '#f59e0b' }} title={`MySQL: ${fmtBytes(b.mysql_used_bytes)}`} />
                  <div style={{ width: `${mailPct}%`, background: '#10b981' }} title={`Mail: ${fmtBytes(b.mail_used_bytes)}`} />
                  <div style={{ width: `${appPct}%`, background: '#8b5cf6' }} title={`Apps: ${fmtBytes(b.app_used_bytes)}`} />
                </div>
              </div>
            );
          })()}

          {/* Detail Sub-tabs */}
          <div className="segmented-control backup-tabs" role="tablist">
            <button type="button" className={activeDetailTab === 'mysql' ? 'active' : ''} onClick={() => setActiveDetailTab('mysql')}>
              <Database size={14} /> {t('MySQL Databases')} ({userDetail?.databases?.length ?? selectedUser.databases_count})
            </button>
            <button type="button" className={activeDetailTab === 'limits' ? 'active' : ''} onClick={() => setActiveDetailTab('limits')}>
              <Sliders size={14} /> {t('Resource Limits (Cgroups v2 & Quotas)')}
            </button>
            <button type="button" className={activeDetailTab === 'processes' ? 'active' : ''} onClick={() => setActiveDetailTab('processes')}>
              <Activity size={14} /> {t('Active Processes & Domains')}
            </button>
          </div>

          {detailLoading ? (
            <p className="hint">{t('Loading detailed telemetry...')}</p>
          ) : activeDetailTab === 'mysql' ? (
            <div>
              {(!userDetail?.databases || userDetail.databases.length === 0) ? (
                <p className="hint">{t('This user has no MySQL databases.')}</p>
              ) : (
                <div className="rm-sublist">
                  {userDetail.databases.map(dbItem => (
                    <div className="rm-sublist-row" key={dbItem.id} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '10px' }}>
                      <div style={{ display: 'flex', flexDirection: 'column', gap: '2px' }}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                          <strong>{dbItem.db_name}</strong>
                          <span className="badge">{fmtBytes(dbItem.size_bytes)}</span>
                          {dbItem.write_locked ? (
                            <span className="badge danger" title={dbItem.write_locked_reason || ''}>
                              <Lock size={11} style={{ marginRight: '4px' }} />
                              {t('Write-Locked (Read-Only)')}
                            </span>
                          ) : (
                            <span className="badge ok">{t('Read / Write')}</span>
                          )}
                        </div>
                        <small style={{ color: 'var(--text-secondary)' }}>
                          {t('DB User')}: <code>{dbItem.db_user}</code>
                          {dbItem.write_locked_reason ? ` · ${dbItem.write_locked_reason}` : ''}
                        </small>
                      </div>
                      <div className="row-actions">
                        <button type="button" className="mini secondary-light" onClick={() => handleInspectTables(dbItem)}>
                          <Eye size={13} /> {t('Inspect Tables')}
                        </button>
                        <button type="button" className="mini secondary-light" onClick={() => handleOptimizeDatabase(dbItem)}>
                          <Wrench size={13} /> {t('Optimize DB')}
                        </button>
                        <button
                          type="button"
                          className={dbItem.write_locked ? 'mini primary' : 'mini danger'}
                          onClick={() => handleToggleWriteLock(dbItem)}
                        >
                          {dbItem.write_locked ? <><Unlock size={13} /> {t('Unlock Write')}</> : <><Lock size={13} /> {t('Lock Write')}</>}
                        </button>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          ) : activeDetailTab === 'limits' && limitsForm ? (
            <div>
              <div className="user-edit-grid" style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(210px, 1fr))', gap: '12px', marginBottom: '14px' }}>
                <label>
                  <span>{t('Assigned Package')}</span>
                  <select
                    value={limitsForm.package_id}
                    onChange={e => setLimitsForm(prev => ({ ...prev, package_id: e.target.value }))}
                  >
                    <option value="">{t('Custom')}</option>
                    {(packages || []).filter(p => !p.is_reseller_package).map(pkg => (
                      <option key={pkg.id} value={pkg.id}>{pkg.name}</option>
                    ))}
                  </select>
                </label>
                <label>
                  <span>{t('CPU Quota (%) (0 = Unlimited)')}</span>
                  <input type="number" min="0" value={limitsForm.cpu_quota_percent} onChange={e => setLimitsForm(prev => ({ ...prev, cpu_quota_percent: e.target.value }))} />
                </label>
                <label>
                  <span>{t('Memory High (MB) (0 = Unlimited)')}</span>
                  <input type="number" min="0" value={limitsForm.memory_high_mb} onChange={e => setLimitsForm(prev => ({ ...prev, memory_high_mb: e.target.value }))} />
                </label>
                <label>
                  <span>{t('Memory Max (MB) (0 = Unlimited)')}</span>
                  <input type="number" min="0" value={limitsForm.memory_max_mb} onChange={e => setLimitsForm(prev => ({ ...prev, memory_max_mb: e.target.value }))} />
                </label>
                <label>
                  <span>{t('Tasks Max (0 = Unlimited)')}</span>
                  <input type="number" min="0" value={limitsForm.tasks_max} onChange={e => setLimitsForm(prev => ({ ...prev, tasks_max: e.target.value }))} />
                </label>
                <label>
                  <span>{t('IO Read Max (MB/s)')}</span>
                  <input type="number" min="0" value={limitsForm.io_read_mbps} onChange={e => setLimitsForm(prev => ({ ...prev, io_read_mbps: e.target.value }))} />
                </label>
                <label>
                  <span>{t('IO Write Max (MB/s)')}</span>
                  <input type="number" min="0" value={limitsForm.io_write_mbps} onChange={e => setLimitsForm(prev => ({ ...prev, io_write_mbps: e.target.value }))} />
                </label>
                <label>
                  <span>{t('Disk Space Limit (MB)')}</span>
                  <input type="number" min="0" value={limitsForm.storage_limit_mb} onChange={e => setLimitsForm(prev => ({ ...prev, storage_limit_mb: e.target.value }))} />
                </label>
                <label>
                  <span>{t('MySQL Total Quota (MB) (0 = Shared)')}</span>
                  <input type="number" min="0" value={limitsForm.mysql_storage_limit_mb} onChange={e => setLimitsForm(prev => ({ ...prev, mysql_storage_limit_mb: e.target.value }))} />
                </label>
                <label>
                  <span>{t('Max Single DB Size (MB) (0 = Unlimited)')}</span>
                  <input type="number" min="0" value={limitsForm.mysql_max_db_size_mb} onChange={e => setLimitsForm(prev => ({ ...prev, mysql_max_db_size_mb: e.target.value }))} />
                </label>
                <label>
                  <span>{t('MySQL Queries per Hour (0 = Unlimited)')}</span>
                  <input type="number" min="0" value={limitsForm.mysql_max_queries_per_hour} onChange={e => setLimitsForm(prev => ({ ...prev, mysql_max_queries_per_hour: e.target.value }))} />
                </label>
                <label>
                  <span>{t('MySQL Updates per Hour (0 = Unlimited)')}</span>
                  <input type="number" min="0" value={limitsForm.mysql_max_updates_per_hour} onChange={e => setLimitsForm(prev => ({ ...prev, mysql_max_updates_per_hour: e.target.value }))} />
                </label>
                <label>
                  <span>{t('MySQL Connections per Hour (0 = Unlimited)')}</span>
                  <input type="number" min="0" value={limitsForm.mysql_max_connections_per_hour} onChange={e => setLimitsForm(prev => ({ ...prev, mysql_max_connections_per_hour: e.target.value }))} />
                </label>
                <label>
                  <span>{t('Bandwidth Limit (MB)')}</span>
                  <input type="number" min="0" value={limitsForm.bandwidth_limit_mb} onChange={e => setLimitsForm(prev => ({ ...prev, bandwidth_limit_mb: e.target.value }))} />
                </label>
                <label>
                  <span>{t('Max Databases')}</span>
                  <input type="number" min="0" value={limitsForm.database_limit} onChange={e => setLimitsForm(prev => ({ ...prev, database_limit: e.target.value }))} />
                </label>
              </div>
              <div style={{ display: 'flex', gap: '10px', justifyContent: 'flex-end', flexWrap: 'wrap' }}>
                {limitsForm.package_id && (
                  <button type="button" className="secondary" disabled={!!loading} onClick={() => handleSaveLimits(true)}>
                    <RefreshCw size={14} /> {t('Reset to Package Defaults')}
                  </button>
                )}
                <button type="button" className="primary" disabled={!!loading} onClick={() => handleSaveLimits(false)}>
                  <Zap size={14} /> {t('Save & Enforce Limits Now')}
                </button>
              </div>
            </div>
          ) : (
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))', gap: '16px' }}>
              <div>
                <h4 style={{ margin: '0 0 8px 0', fontSize: '14px' }}>{t('Active Linux Processes')} ({userDetail?.live?.processes?.length || 0})</h4>
                {(!userDetail?.live?.processes || userDetail.live.processes.length === 0) ? (
                  <p className="hint">{t('No active processes running right now.')}</p>
                ) : (
                  <div className="rm-sublist">
                    {userDetail.live.processes.map((proc, idx) => (
                      <div className="rm-sublist-row" key={`${proc.pid}-${idx}`} style={{ fontSize: '12px' }}>
                        <span><strong>PID {proc.pid}</strong> · {proc.cpu_percent}% CPU · {fmtBytes(proc.memory_bytes)}</span>
                        <code style={{ fontSize: '11px', color: 'var(--text-secondary)' }}>{proc.command}</code>
                      </div>
                    ))}
                  </div>
                )}
              </div>
              <div>
                <h4 style={{ margin: '0 0 8px 0', fontSize: '14px' }}>{t('Hosted Domains Breakdown')} ({userDetail?.websites?.length || 0})</h4>
                {(!userDetail?.websites || userDetail.websites.length === 0) ? (
                  <p className="hint">{t('No domains hosted under this account.')}</p>
                ) : (
                  <div className="rm-sublist">
                    {userDetail.websites.map(w => (
                      <div className="rm-sublist-row" key={w.id} style={{ fontSize: '12px', display: 'flex', justifyContent: 'space-between' }}>
                        <strong>
                          {w.domain} <small className="badge">PHP {w.php_version}</small>
                          {w.http_flood_enabled && <small className="badge danger" style={{ marginLeft: '6px' }}>Anti-Flood Shield</small>}
                        </strong>
                        <span>{t('Files')}: {fmtBytes(w.storage_bytes)} · {t('Traffic')}: {fmtBytes(w.bandwidth_bytes)}</span>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </div>
          )}
        </div>
      )}

      {/* Main User Resource Table */}
      {filteredItems.length === 0 ? (
        <EmptyState icon={Cpu} message={t('No user accounts match the current filter.')} />
      ) : (
        <div className="rm-account-table-card">
          <div className="rm-account-table" style={{ minWidth: '1060px' }}>
            {/* Column Header Row */}
            <div
              className="rm-account-header-row"
              style={{ gridTemplateColumns: rowGridTemplate }}
            >
              <span>{t('Account / Package')}</span>
              <span style={{ display: 'inline-flex', alignItems: 'center', gap: '5px' }}><Cpu size={13} /> {t('CPU & Tasks')}</span>
              <span style={{ display: 'inline-flex', alignItems: 'center', gap: '5px' }}><Activity size={13} /> {t('RAM (Memory)')}</span>
              <span style={{ display: 'inline-flex', alignItems: 'center', gap: '5px' }}><Database size={13} /> {t('MySQL Quota')}</span>
              <span style={{ display: 'inline-flex', alignItems: 'center', gap: '5px' }}><HardDrive size={13} /> {t('Total Disk')}</span>
              <span style={{ display: 'inline-flex', alignItems: 'center', gap: '5px' }}><Network size={13} /> {t('Bandwidth')}</span>
              <span style={{ textAlign: 'right' }}>{t('Actions')}</span>
            </div>

            {filteredItems.map(item => (
              <div
                className={`rm-account-row${item.under_attack ? ' is-under-attack' : ''}${selectedUser?.id === item.id ? ' is-selected' : ''}`}
                key={item.id}
                style={{ gridTemplateColumns: rowGridTemplate }}
              >
                {/* User & Package Info */}
                <div style={{ display: 'flex', flexDirection: 'column', gap: '4px', minWidth: 0 }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '6px', flexWrap: 'wrap' }}>
                    <strong style={{ fontSize: '14px' }}>{item.username}</strong>
                    <span className="badge">{item.package_name}</span>
                    {item.under_attack && (
                      <span className="badge danger" title={item.under_attack_reason || ''} style={{ fontSize: '10.5px', padding: '1px 6px' }}>
                        <ShieldAlert size={11} style={{ marginRight: '3px' }} />
                        {t('Attack Shield')}
                      </span>
                    )}
                  </div>
                  <small style={{ color: 'var(--text-secondary)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {t('Created by')}: {item.owner_username} ({t(roleLabel(item.owner_role))})
                  </small>
                </div>

                {/* CPU & Tasks */}
                <UsageProgressCell
                  usedLabel={`${item.cpu_percent}% CPU`}
                  limitLabel={item.cpu_quota_percent ? `${item.cpu_quota_percent}%` : t('Unlimited')}
                  percent={item.cpu_utilization_pct}
                  subLabel={`PHP ${item.php_cpu_percent || 0}% · DB ${item.mysql_cpu_percent || 0}% · ${item.tasks_count} tasks`}
                  alertBadge={item.under_attack ? t('Isolated') : null}
                />

                {/* Memory (RAM) */}
                <UsageProgressCell
                  usedLabel={fmtBytes(item.memory_used_bytes)}
                  limitLabel={item.memory_max_mb ? `${item.memory_max_mb} MB` : t('Unlimited')}
                  percent={item.memory_percent}
                  subLabel={`High: ${item.memory_high_mb ? `${item.memory_high_mb} MB` : '∞'}`}
                />

                {/* MySQL Storage & Quota */}
                <UsageProgressCell
                  usedLabel={fmtBytes(item.mysql_used_bytes)}
                  limitLabel={item.effective_mysql_limit_bytes ? fmtBytes(item.effective_mysql_limit_bytes) : t('Unlimited')}
                  percent={item.mysql_percent}
                  subLabel={
                    (item.mysql_qps > 0 || item.mysql_rows_read_rate > 0)
                      ? `${item.mysql_qps || 0} QPS · ${(item.mysql_rows_read_rate || 0).toLocaleString()} rows/s`
                      : `${item.databases_count} / ${item.database_limit || '∞'} DBs`
                  }
                  alertBadge={item.locked_databases_count > 0 ? `${item.locked_databases_count} Locked` : null}
                />

                {/* Total Disk Storage */}
                <UsageProgressCell
                  usedLabel={fmtBytes(item.storage_used_bytes)}
                  limitLabel={item.storage_limit_bytes ? fmtBytes(item.storage_limit_bytes) : t('Unlimited')}
                  percent={item.storage_percent}
                  subLabel={`Web: ${fmtBytes(item.web_used_bytes)}`}
                />

                {/* Monthly Bandwidth */}
                <UsageProgressCell
                  usedLabel={fmtBytes(item.bandwidth_used_bytes)}
                  limitLabel={item.bandwidth_limit_bytes ? fmtBytes(item.bandwidth_limit_bytes) : t('Unlimited')}
                  percent={item.bandwidth_percent}
                  subLabel={`${item.websites_count} ${t('domains')}`}
                />

                {/* Actions */}
                <div className="row-actions" style={{ justifyContent: 'flex-end', flexWrap: 'nowrap' }}>
                  <button
                    type="button"
                    className={item.under_attack ? 'mini danger' : 'mini secondary-light'}
                    onClick={e => handleToggleAttackShield(item, e)}
                    title={item.under_attack ? t('Disable Attack Isolation Shield') : t('Enable 5-Layer Attack Isolation Shield')}
                  >
                    <ShieldAlert size={13} />
                  </button>
                  <button
                    type="button"
                    className="mini secondary"
                    onClick={() => openUserDetail(item, 'mysql')}
                  >
                    <Database size={13} /> {t('Manage')}
                  </button>
                  <button
                    type="button"
                    className="mini secondary-light"
                    onClick={() => openUserDetail(item, 'limits')}
                    title={t('Configure Resource Limits')}
                  >
                    <Sliders size={13} />
                  </button>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Modal: Inspect MySQL Tables */}
      {inspectTablesModal && (
        <div className="modal-backdrop" onClick={() => setInspectTablesModal(null)}>
          <div className="modal" style={{ maxWidth: '680px', width: '95%' }} onClick={e => e.stopPropagation()}>
            <div className="modal-header" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <h3 style={{ margin: 0 }}>
                {t('MySQL Table Breakdown')}: <code>{inspectTablesModal.db_name}</code> ({fmtBytes(inspectTablesModal.size_bytes)})
              </h3>
              <button type="button" className="secondary-light mini" onClick={() => setInspectTablesModal(null)}>
                <X size={15} />
              </button>
            </div>
            <div className="modal-body" style={{ maxHeight: '420px', overflowY: 'auto' }}>
              {(!inspectTablesModal.tables || inspectTablesModal.tables.length === 0) ? (
                <p className="hint">{t('No tables found in this database.')}</p>
              ) : (
                <div className="rm-sublist">
                  {inspectTablesModal.tables.map(tbl => (
                    <div className="rm-sublist-row" key={tbl.table_name} style={{ display: 'flex', justifyContent: 'space-between', fontSize: '13px' }}>
                      <div>
                        <strong>{tbl.table_name}</strong> <span className="badge">{tbl.engine}</span>
                        <small style={{ marginLeft: '8px', color: 'var(--text-secondary)' }}>{tbl.rows.toLocaleString()} rows</small>
                      </div>
                      <div>
                        <strong>{fmtBytes(tbl.total_bytes)}</strong>
                        <small style={{ marginLeft: '8px', color: 'var(--text-secondary)' }}>
                          (Data: {fmtBytes(tbl.data_bytes)} · Index: {fmtBytes(tbl.index_bytes)})
                        </small>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
