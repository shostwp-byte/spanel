import React, { useState, useEffect } from 'react';
import { Archive, ArchiveRestore, ArrowLeft, Ban, Bot, Boxes, Check, ChevronDown, Clock, Code2, Copy, Cpu, Database, Dices, ExternalLink, Eye, FileText, FolderOpen, Globe, HardDrive, Home, Image, KeyRound, Lock, LogIn, LogOut, MemoryStick, Menu, Moon, MoveRight, Network, Pencil, Save, Search, Server, Settings as SettingsIcon, Shield, Sun, Trash2, TerminalIcon, Users, X, RefreshCw, Plus, Download, Upload, Play, Square, RotateCcw, AlertCircle, Activity, BrickWall, Bug, LockKeyhole, PackageOpen, ScrollText, ShieldAlert, CheckCircle, Zap, Bell, Mail, Send, Cloud, Inbox, Forward, ShieldCheck } from 'lucide-react';
import { EmptyState } from '../components/EmptyState.jsx';
import { useAppContext } from '../contexts/AppContext.jsx';
import { t } from '../i18n.js';

const DEFAULT_SERVICE_NAMES = ['spanel-api', 'nginx', 'php8.3-fpm', 'php8.4-fpm', 'mariadb', 'redis-server'];

export function ServicesPage({ request, isAdmin }) {
  const { loading, setLoading, error, setError, setNotice } = useAppContext();
  const [serviceStates, setServiceStates] = useState({});
  const [serviceNames, setServiceNames] = useState(DEFAULT_SERVICE_NAMES);
  const [selfHealing, setSelfHealing] = useState(null);
  const [dbHealth, setDbHealth] = useState(null);
  const [busyWatchdog, setBusyWatchdog] = useState(false);
  const [busyBackup, setBusyBackup] = useState(false);

  useEffect(() => {
    loadServiceNames().then(checkAllServices);
    if (isAdmin) {
      loadWatchdogAndDb();
    }
  }, [isAdmin]);

  useEffect(() => {
    if (!loading) {
      const timer = setInterval(() => {
        checkAllServices();
        if (isAdmin) loadWatchdogAndDb();
      }, 15000);
      return () => clearInterval(timer);
    }
  }, [loading, isAdmin]);

  async function loadWatchdogAndDb() {
    try {
      const [sh, dbh] = await Promise.all([
        request('/services/self-healing'),
        request('/services/db-health'),
      ]);
      if (sh) setSelfHealing(sh);
      if (dbh) setDbHealth(dbh);
    } catch {}
  }

  async function toggleSelfHealingOption(key, value) {
    setBusyWatchdog(true);
    try {
      const res = await request('/services/self-healing', {
        method: 'PUT',
        body: JSON.stringify({ [key]: value }),
      });
      if (res) {
        setSelfHealing(res);
        setNotice('Đã cập nhật cấu hình Self-Healing Service Watchdog.');
      }
    } finally {
      setBusyWatchdog(false);
    }
  }

  async function runWatchdogNow() {
    setBusyWatchdog(true);
    try {
      const res = await request('/services/self-healing/run', { method: 'POST' }, 'Đang kiểm tra & tự phục hồi dịch vụ...');
      if (res) {
        setSelfHealing(res);
        const healed = res.healed_now || [];
        if (healed.length > 0) {
          setNotice(`Watchdog đã tự động khởi động lại thành công: ${healed.join(', ')}`);
        } else {
          setNotice('Tất cả dịch vụ cốt lõi đều đang hoạt động ổn định (ACTIVE).');
        }
      }
      await checkAllServices();
      await loadWatchdogAndDb();
    } finally {
      setBusyWatchdog(false);
    }
  }

  async function clearWatchdogEvents() {
    setBusyWatchdog(true);
    try {
      const res = await request('/services/self-healing/logs', { method: 'DELETE' });
      if (res) {
        setSelfHealing(res);
        setNotice('Đã xóa lịch sử sự kiện Self-Healing.');
      }
    } finally {
      setBusyWatchdog(false);
    }
  }

  async function triggerHotBackup() {
    setBusyBackup(true);
    try {
      const res = await request('/services/db-backup', { method: 'POST' }, 'Đang chạy SQLite Online Hot-Backup...');
      if (res && res.ok) {
        setNotice(`Đã tạo bản sao lưu nóng thành công: ${res.filename} (${formatBytes(res.size_bytes)}, integrity=${res.integrity})`);
        await loadWatchdogAndDb();
      }
    } finally {
      setBusyBackup(false);
    }
  }

  function formatBytes(bytes) {
    const val = Number(bytes || 0);
    if (val < 1024) return `${val} B`;
    if (val < 1024 * 1024) return `${(val / 1024).toFixed(1)} KB`;
    return `${(val / (1024 * 1024)).toFixed(2)} MB`;
  }

  async function checkService(name) {
    const data = await request('/services/action', { method: 'POST', body: JSON.stringify({ name, action: 'status' }) });
    setServiceStates(prev => ({ ...prev, [name]: data || { stdout: '', stderr: error || 'Cannot check', returncode: 1 } }));
    return data;
  }

  async function loadServiceNames() {
    const data = await request('/services/list');
    const names = data?.services?.length ? data.services : serviceNames;
    setServiceNames(names);
    return names;
  }

  async function checkAllServices() {
    setLoading('Checking services...');
    const names = await loadServiceNames();
    for (const name of names) { await checkService(name); }
    setLoading('');
  }

  async function runServiceAction(name, action) {
    await request('/services/action', { method: 'POST', body: JSON.stringify({ name, action }) }, `${action} ${name}...`);
    await checkService(name);
  }

  function renderServices() {
    return <div className="section">
      <div className="section-title">
        <div>
          <h2>{t('Services')}</h2>
          <p className="hint">{t('Auto-refreshes every 10s')}</p>
        </div>
        <button className="secondary" disabled={!!loading} onClick={checkAllServices}><RefreshCw size={15}/>{t('Refresh')}</button>
      </div>
      {/* A running service's Start and a stopped one's Stop are disabled, so
          the enabled button is the obvious next step. */}
      <div className="service-grid">
        {serviceNames.map(name => {
          const state = serviceStates[name];
          const text = `${state?.stdout || ''} ${state?.stderr || ''}`;
          const active = text.includes('active (running)');
          const inactive = text.includes('inactive') || text.includes('failed');
          const canStop = !['spanel-api', 'redis-server'].includes(name);
          return <div className="service-card" key={name}>
            <div><strong>{name}</strong><span className={active ? 'badge ok' : inactive ? 'badge bad' : 'badge'}>{active ? t('Running') : inactive ? t('Stopped') : '...'}</span></div>
            {isAdmin && <div className="service-actions">
              <button className="secondary" disabled={active} onClick={() => runServiceAction(name, 'start')}><Play size={13}/>{t('Start')}</button>
              {canStop && <button className="danger-light" disabled={inactive} onClick={() => runServiceAction(name, 'stop')}><Square size={13}/>{t('Stop')}</button>}
              <button className="secondary" onClick={() => runServiceAction(name, 'restart')}><RotateCcw size={13}/>{t('Restart')}</button>
            </div>}
          </div>;
        })}
      </div>

      {isAdmin && (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(420px, 1fr))', gap: '18px', marginTop: '22px' }}>
          {/* Card 1: Autonomous Self-Healing Service Watchdog */}
          <div style={{ padding: '18px 20px', borderRadius: '14px', background: 'var(--surface-2)', border: '1px solid var(--border-subtle)' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: '12px', flexWrap: 'wrap', marginBottom: '14px' }}>
              <div>
                <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                  <ShieldCheck size={18} style={{ color: '#10b981' }} />
                  <h3 style={{ margin: 0, fontSize: '1rem' }}>Autonomous Self-Healing Service Watchdog</h3>
                  <span className={selfHealing?.enabled !== false ? 'badge ok' : 'badge'}>
                    {selfHealing?.enabled !== false ? 'ĐANG BẢO VỆ 24/7' : 'TẠM DỪNG'}
                  </span>
                </div>
                <p className="hint" style={{ margin: '4px 0 0 0', fontSize: '0.82rem' }}>
                  Tự động giám sát mỗi 60 giây, kiểm tra cú pháp cấu hình (<code>nginx -t</code> / <code>php-fpm -t</code>) và tự khởi động lại dịch vụ bị treo.
                </p>
              </div>
              <div style={{ display: 'flex', gap: '8px' }}>
                <button className="primary" style={{ padding: '7px 12px', fontSize: '0.8rem' }} disabled={busyWatchdog} onClick={runWatchdogNow}>
                  <Zap size={14} /> Quét & Phục hồi ngay
                </button>
              </div>
            </div>

            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: '10px', marginBottom: '14px' }}>
              <div style={{ padding: '10px 12px', borderRadius: '10px', background: 'var(--surface-1)', border: '1px solid var(--border-subtle)' }}>
                <div style={{ fontSize: '0.73rem', color: 'var(--text-muted)' }}>Dịch vụ đã tự cứu</div>
                <div style={{ fontSize: '1.15rem', fontWeight: 700, color: '#10b981', marginTop: '2px' }}>
                  {selfHealing?.total_healed_count ?? 0} lần
                </div>
              </div>
              <div style={{ padding: '10px 12px', borderRadius: '10px', background: 'var(--surface-1)', border: '1px solid var(--border-subtle)' }}>
                <div style={{ fontSize: '0.73rem', color: 'var(--text-muted)' }}>Giới hạn chống lặp lỗi</div>
                <div style={{ fontSize: '1.05rem', fontWeight: 700, marginTop: '2px' }}>
                  {selfHealing?.max_restarts_per_hour ?? 5} lần / giờ
                </div>
              </div>
              <div style={{ padding: '10px 12px', borderRadius: '10px', background: 'var(--surface-1)', border: '1px solid var(--border-subtle)' }}>
                <div style={{ fontSize: '0.73rem', color: 'var(--text-muted)' }}>Lần quét gần nhất</div>
                <div style={{ fontSize: '0.8rem', fontWeight: 600, marginTop: '4px' }}>
                  {selfHealing?.last_check_at ? new Date(selfHealing.last_check_at).toLocaleTimeString() : 'Đang đồng bộ...'}
                </div>
              </div>
            </div>

            <div style={{ display: 'flex', flexDirection: 'column', gap: '8px', marginBottom: '14px', fontSize: '0.84rem' }}>
              <label style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'flex-start', gap: '10px', cursor: busyWatchdog ? 'not-allowed' : 'pointer', padding: '9px 12px', borderRadius: '10px', background: 'var(--surface-1)', border: '1px solid var(--border-subtle)', margin: 0, width: '100%', boxSizing: 'border-box', textAlign: 'left', lineHeight: 1.45 }}>
                <input
                  type="checkbox"
                  checked={selfHealing?.enabled !== false}
                  disabled={busyWatchdog}
                  onChange={(e) => toggleSelfHealingOption('enabled', e.target.checked)}
                  style={{ width: '16px', minWidth: '16px', height: '16px', minHeight: '16px', margin: '2px 0 0 0', padding: 0, flexShrink: 0, accentColor: 'var(--primary)', cursor: busyWatchdog ? 'not-allowed' : 'pointer' }}
                />
                <span style={{ flex: 1, minWidth: 0, color: 'var(--text)', fontWeight: 500, textAlign: 'left' }}>
                  Tự động khởi động lại dịch vụ cốt lõi (Nginx, MariaDB, Redis, PHP-FPM, Fail2ban) khi bị dừng
                </span>
              </label>
              <label style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'flex-start', gap: '10px', cursor: busyWatchdog ? 'not-allowed' : 'pointer', padding: '9px 12px', borderRadius: '10px', background: 'var(--surface-1)', border: '1px solid var(--border-subtle)', margin: 0, width: '100%', boxSizing: 'border-box', textAlign: 'left', lineHeight: 1.45 }}>
                <input
                  type="checkbox"
                  checked={selfHealing?.validate_config_before_restart !== false}
                  disabled={busyWatchdog}
                  onChange={(e) => toggleSelfHealingOption('validate_config_before_restart', e.target.checked)}
                  style={{ width: '16px', minWidth: '16px', height: '16px', minHeight: '16px', margin: '2px 0 0 0', padding: 0, flexShrink: 0, accentColor: 'var(--primary)', cursor: busyWatchdog ? 'not-allowed' : 'pointer' }}
                />
                <span style={{ flex: 1, minWidth: 0, color: 'var(--text)', fontWeight: 500, textAlign: 'left' }}>
                  Kiểm tra an toàn cú pháp (<code>nginx -t</code>, <code>php-fpm -t</code>) trước khi restart để tránh crash-loop
                </span>
              </label>
              <label style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'flex-start', gap: '10px', cursor: busyWatchdog ? 'not-allowed' : 'pointer', padding: '9px 12px', borderRadius: '10px', background: 'var(--surface-1)', border: '1px solid var(--border-subtle)', margin: 0, width: '100%', boxSizing: 'border-box', textAlign: 'left', lineHeight: 1.45 }}>
                <input
                  type="checkbox"
                  checked={selfHealing?.auto_db_backup_enabled !== false}
                  disabled={busyWatchdog}
                  onChange={(e) => toggleSelfHealingOption('auto_db_backup_enabled', e.target.checked)}
                  style={{ width: '16px', minWidth: '16px', height: '16px', minHeight: '16px', margin: '2px 0 0 0', padding: 0, flexShrink: 0, accentColor: 'var(--primary)', cursor: busyWatchdog ? 'not-allowed' : 'pointer' }}
                />
                <span style={{ flex: 1, minWidth: 0, color: 'var(--text)', fontWeight: 500, textAlign: 'left' }}>
                  Tự động sao lưu nóng cơ sở dữ liệu Panel (SQLite Online Hot-Backup) định kỳ mỗi 6 giờ
                </span>
              </label>
            </div>

            <div style={{ borderTop: '1px solid var(--border-subtle)', paddingTop: '10px' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '8px' }}>
                <strong style={{ fontSize: '0.82rem' }}>Nhật ký Tự phục hồi (Self-Healing Events)</strong>
                {(selfHealing?.events?.length > 0) && (
                  <button className="secondary" style={{ padding: '3px 8px', fontSize: '0.74rem' }} onClick={clearWatchdogEvents}>
                    <Trash2 size={12} /> Xóa nhật ký
                  </button>
                )}
              </div>
              {(!selfHealing?.events || selfHealing.events.length === 0) ? (
                <div style={{ fontSize: '0.8rem', color: 'var(--text-muted)', padding: '8px 0' }}>
                  Chưa phát hiện sự cố dịch vụ nào. Mọi tiến trình hệ thống đang hoạt động 100% ổn định.
                </div>
              ) : (
                <div style={{ maxHeight: '170px', overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: '6px' }}>
                  {selfHealing.events.slice(0, 10).map((ev, idx) => (
                    <div key={idx} style={{ padding: '7px 10px', borderRadius: '8px', background: 'var(--surface-1)', fontSize: '0.78rem', display: 'flex', justifyContent: 'space-between', gap: '8px' }}>
                      <div>
                        <strong style={{ color: ev.recovered ? '#10b981' : '#ef4444' }}>[{ev.service}]</strong>{' '}
                        <span>{ev.detail}</span>
                      </div>
                      <span style={{ color: 'var(--text-muted)', whiteSpace: 'nowrap' }}>
                        {ev.timestamp ? new Date(ev.timestamp).toLocaleString() : ''}
                      </span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>

          {/* Card 2: SQLite High-Concurrency WAL & Online Hot-Backup */}
          <div style={{ padding: '18px 20px', borderRadius: '14px', background: 'var(--surface-2)', border: '1px solid var(--border-subtle)' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: '12px', flexWrap: 'wrap', marginBottom: '14px' }}>
              <div>
                <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                  <Database size={18} style={{ color: 'var(--accent-primary)' }} />
                  <h3 style={{ margin: 0, fontSize: '1rem' }}>SQLite WAL High-Concurrency & Hot-Backup</h3>
                  <span className={dbHealth?.integrity_ok !== false ? 'badge ok' : 'badge bad'}>
                    {dbHealth?.journal_mode || 'WAL'} • {dbHealth?.integrity_ok !== false ? 'INTEGRITY OK' : 'CHECK REQUIRED'}
                  </span>
                </div>
                <p className="hint" style={{ margin: '4px 0 0 0', fontSize: '0.82rem' }}>
                  Chế độ ghi nhật ký song song <code>WAL (Write-Ahead Logging)</code> chống khóa bảng (Database Locked) khi chịu tải cao.
                </p>
              </div>
              <button className="secondary" style={{ padding: '7px 12px', fontSize: '0.8rem' }} disabled={busyBackup} onClick={triggerHotBackup}>
                <Archive size={14} /> Sao lưu nóng ngay (Hot-Backup)
              </button>
            </div>

            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: '8px', marginBottom: '14px' }}>
              <div style={{ padding: '9px 10px', borderRadius: '10px', background: 'var(--surface-1)', border: '1px solid var(--border-subtle)' }}>
                <div style={{ fontSize: '0.72rem', color: 'var(--text-muted)' }}>Journal / Sync</div>
                <div style={{ fontSize: '0.9rem', fontWeight: 700, color: '#10b981', marginTop: '2px' }}>
                  {dbHealth?.journal_mode || 'WAL'} / {dbHealth?.synchronous || 'NORMAL'}
                </div>
              </div>
              <div style={{ padding: '9px 10px', borderRadius: '10px', background: 'var(--surface-1)', border: '1px solid var(--border-subtle)' }}>
                <div style={{ fontSize: '0.72rem', color: 'var(--text-muted)' }}>Busy Timeout</div>
                <div style={{ fontSize: '0.9rem', fontWeight: 700, marginTop: '2px' }}>
                  {dbHealth?.busy_timeout || 15000} ms
                </div>
              </div>
              <div style={{ padding: '9px 10px', borderRadius: '10px', background: 'var(--surface-1)', border: '1px solid var(--border-subtle)' }}>
                <div style={{ fontSize: '0.72rem', color: 'var(--text-muted)' }}>Dung lượng DB / WAL</div>
                <div style={{ fontSize: '0.85rem', fontWeight: 700, marginTop: '2px' }}>
                  {formatBytes(dbHealth?.size_bytes)} / {formatBytes(dbHealth?.wal_size_bytes)}
                </div>
              </div>
              <div style={{ padding: '9px 10px', borderRadius: '10px', background: 'var(--surface-1)', border: '1px solid var(--border-subtle)' }}>
                <div style={{ fontSize: '0.72rem', color: 'var(--text-muted)' }}>Bản sao lưu nóng</div>
                <div style={{ fontSize: '0.9rem', fontWeight: 700, marginTop: '2px' }}>
                  {dbHealth?.backup_count ?? 0} bản (giữ 14)
                </div>
              </div>
            </div>

            <div style={{ borderTop: '1px solid var(--border-subtle)', paddingTop: '10px' }}>
              <strong style={{ fontSize: '0.82rem', display: 'block', marginBottom: '8px' }}>
                Danh sách Bản sao lưu nóng gần nhất (<code>{dbHealth?.backup_dir || '/var/lib/spanel/db-backups'}</code>)
              </strong>
              {(!dbHealth?.backups || dbHealth.backups.length === 0) ? (
                <div style={{ fontSize: '0.8rem', color: 'var(--text-muted)', padding: '6px 0' }}>
                  Chưa có bản sao lưu nóng nào. Nhấn <strong>"Sao lưu nóng ngay"</strong> để tạo snapshot an toàn đầu tiên.
                </div>
              ) : (
                <div style={{ maxHeight: '165px', overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: '5px' }}>
                  {dbHealth.backups.slice(0, 6).map((bk) => (
                    <div key={bk.filename} style={{ padding: '6px 10px', borderRadius: '8px', background: 'var(--surface-1)', fontSize: '0.78rem', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                      <code>{bk.filename}</code>
                      <span style={{ color: 'var(--text-muted)' }}>
                        {formatBytes(bk.size_bytes)} • {bk.created_at ? new Date(bk.created_at).toLocaleString() : ''}
                      </span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </div>;
  }
  return renderServices();
}
