import React, { useState, useEffect, useRef } from 'react';
import {
  RefreshCw, RotateCcw, ShieldCheck, Server, Lock, Archive,
  ArchiveRestore, CheckCircle, AlertCircle, Clock, FileText, X,
  Download, Trash2, Zap
} from 'lucide-react';
import { useAppContext } from '../contexts/AppContext.jsx';
import { t } from '../i18n.js';

const UPDATE_STEPS = [
  { key: 'snapshot', label: '1. Tạo Snapshot Dự phòng', minPct: 10 },
  { key: 'downloading', label: '2. Tải gói & Kiểm tra SHA-256', minPct: 35 },
  { key: 'validating', label: '3. Kiểm tra vùng đệm Staging', minPct: 60 },
  { key: 'applying', label: '4. Áp dụng & Đồng bộ Dịch vụ', minPct: 80 },
  { key: 'healthcheck', label: '5. Kiểm tra Healthcheck / Hoàn tất', minPct: 92 },
];

function fmtVer(ver) {
  const s = String(ver || '1.0.0').trim();
  return s.toLowerCase().startsWith('v') ? s : `v${s}`;
}

function fmtDateTime(isoStr) {
  if (!isoStr) return '';
  try {
    const d = new Date(isoStr);
    if (Number.isNaN(d.getTime())) return isoStr;
    const pad = n => String(n).padStart(2, '0');
    return `${pad(d.getDate())}/${pad(d.getMonth() + 1)}/${d.getFullYear()} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
  } catch {
    return isoStr;
  }
}

function fmtUpdateStatus(st) {
  if (!st) return '';
  const map = {
    installed: t('Installed'),
    completed: t('Completed'),
    rolled_back: t('Auto-Rolled Back'),
    rolling_back: t('Rolling Back'),
    running: t('Running'),
    failed: t('Failed'),
  };
  return map[st] || st;
}

export function UpdatesPage({ request, isAdmin }) {
  const { loading, setNotice, appVersion } = useAppContext();
  const [updatesStatus, setUpdatesStatus] = useState(null);
  const [showUpdateLog, setShowUpdateLog] = useState(false);
  const [osUpdating, setOsUpdating] = useState(false);
  const [panelUpdating, setPanelUpdating] = useState(false);
  const [creatingSnapshot, setCreatingSnapshot] = useState(false);
  const [rollingBackId, setRollingBackId] = useState('');
  const [panelUpdateLog, setPanelUpdateLog] = useState([]);
  const panelUpdateInterval = useRef(null);

  const [showSourceModal, setShowSourceModal] = useState(false);
  const [sourceForm, setSourceForm] = useState({
    source_type: 'auto',
    server_url: '',
    manifest_url: '',
    channel: 'stable',
    auth_token: '',
    clear_auth_token: false,
    verify_checksum: true,
    auto_check_enabled: true,
    auto_check_interval_hours: 6,
    auto_update_enabled: false,
    auto_update_window: '02:00',
    auto_rollback_enabled: true,
  });

  const [osAutoUpdate, setOsAutoUpdate] = useState({
    enabled: true,
    mode: 'security',
    auto_reboot: false,
  });

  const [phpConfigForm, setPhpConfigForm] = useState({
    auto_patch_enabled: true,
    auto_harden_php2shell: true,
    harden_mode: 'strict',
    preferred_window: '03:00',
  });
  const [phpPatchingBranch, setPhpPatchingBranch] = useState('');
  const [phpSavingConfig, setPhpSavingConfig] = useState(false);

  useEffect(() => {
    if (isAdmin) loadUpdates(false);
    return () => {
      if (panelUpdateInterval.current) clearInterval(panelUpdateInterval.current);
    };
  }, [isAdmin]);

  function syncSourceFormFromStatus(data) {
    const src = data?.source;
    if (src) {
      setSourceForm(prev => ({
        ...prev,
        source_type: src.source_type || 'auto',
        server_url: '',
        manifest_url: '',
        channel: src.channel || 'stable',
        auth_token: '',
        clear_auth_token: false,
        verify_checksum: src.verify_checksum !== false,
        auto_check_enabled: src.auto_check_enabled !== false,
        auto_check_interval_hours: src.auto_check_interval_hours || 6,
        auto_update_enabled: !!src.auto_update_enabled,
        auto_update_window: src.auto_update_window || '02:00',
        auto_rollback_enabled: src.auto_rollback_enabled !== false,
      }));
    }
    const pg = data?.php_smart_guard;
    if (pg) {
      setPhpConfigForm({
        auto_patch_enabled: pg.auto_patch_enabled !== false,
        auto_harden_php2shell: pg.auto_harden_php2shell !== false,
        harden_mode: pg.auto_harden_php2shell === false ? 'off' : pg.harden_mode || 'strict',
        preferred_window: pg.preferred_window || '03:00',
      });
    }
  }

  async function loadUpdates(force = false) {
    const data = await request(
      `/updates/status${force ? '?refresh=true' : ''}`,
      {},
      force ? t('Checking designated update server for new releases...') : t('Loading update status...')
    );
    if (data) {
      setUpdatesStatus(data);
      syncSourceFormFromStatus(data);
      if (Array.isArray(data.panel_update_log)) {
        setPanelUpdateLog(data.panel_update_log);
      }
    }
  }

  async function handleSaveSourceConfig(e) {
    if (e) e.preventDefault();
    const res = await request(
      '/updates/source',
      {
        method: 'POST',
        body: JSON.stringify(sourceForm),
      },
      t('Saving protected update server configuration...')
    );
    if (res) {
      setShowSourceModal(false);
      setNotice(t('Update server configuration saved and verified securely.'));
      await loadUpdates(false);
    }
  }

  async function handleCreateSnapshot() {
    setCreatingSnapshot(true);
    const res = await request(
      '/updates/snapshots/create',
      {
        method: 'POST',
        body: JSON.stringify({ reason: 'manual' }),
      },
      t('Creating pre-update recovery snapshot...')
    );
    setCreatingSnapshot(false);
    if (res) {
      setNotice(t('Recovery snapshot created: {id}', { id: res.snapshot?.id || '' }));
      setUpdatesStatus(prev => (prev ? { ...prev, snapshots: res.snapshots || prev.snapshots } : prev));
    }
  }

  async function handleRestoreSnapshot(snap) {
    if (
      !window.confirm(
        t(
          'Rollback Panel to snapshot {id} ({ver})? The panel will restore code & frontend assets and restart automatically.',
          { id: snap.id, ver: fmtVer(snap.version) }
        )
      )
    ) {
      return;
    }
    setRollingBackId(snap.id);
    const res = await request(
      `/updates/snapshots/${encodeURIComponent(snap.id)}/restore`,
      {
        method: 'POST',
        body: JSON.stringify({ restore_db: false }),
      },
      t('Rolling back Panel to snapshot {id}...', { id: snap.id })
    );
    setRollingBackId('');
    if (res) {
      setNotice(t('Rollback to {id} completed. Reloading panel...', { id: snap.id }));
      await loadUpdates(false);
      setTimeout(() => {
        window.location.reload();
      }, 2000);
    }
  }

  async function handleDeleteSnapshot(snap) {
    if (!window.confirm(t('Delete recovery snapshot {id}?', { id: snap.id }))) return;
    const res = await request(
      `/updates/snapshots/${encodeURIComponent(snap.id)}`,
      { method: 'DELETE' },
      t('Deleting snapshot...')
    );
    if (res) {
      setNotice(t('Snapshot {id} deleted.', { id: snap.id }));
      setUpdatesStatus(prev => (prev ? { ...prev, snapshots: res.snapshots || [] } : prev));
    }
  }

  async function runPanelUpdate() {
    if (
      !window.confirm(
        t(
          'Start safe online update now? System will automatically create a recovery snapshot before updating and auto-rollback if any error occurs.'
        )
      )
    ) {
      return;
    }
    setPanelUpdating(true);
    setShowUpdateLog(true);
    setPanelUpdateLog([]);
    const data = await request('/updates/panel/run', { method: 'POST' }, t('Starting safe transactional update...'));
    if (!data) {
      setPanelUpdating(false);
      return;
    }

    const pollOnce = async () => {
      const status = await request('/updates/status', { silent: true }, null);
      if (!status) return;
      setUpdatesStatus(status);
      if (Array.isArray(status.panel_update_log)) {
        setPanelUpdateLog(status.panel_update_log);
      } else if (typeof status.panel_update_log === 'string' && status.panel_update_log) {
        setPanelUpdateLog(status.panel_update_log.split('\n'));
      }
      const st = status.panel || {};
      const done =
        st.last_update_status === 'completed' ||
        st.last_update_status === 'failed' ||
        st.last_update_status === 'rolled_back';
      if (done) {
        if (panelUpdateInterval.current) {
          clearInterval(panelUpdateInterval.current);
          panelUpdateInterval.current = null;
        }
        setPanelUpdating(false);
        if (st.last_update_status === 'completed' && Number(st.progress_percent) === 100) {
          setNotice(t('Panel update completed and passed healthcheck! Reloading...'));
          setTimeout(() => {
            window.location.reload();
          }, 2000);
        } else if (st.last_update_status === 'rolled_back') {
          setNotice(
            st.progress_message ||
              t('Update encountered an issue and was automatically rolled back to the safe snapshot.')
          );
        } else if (st.last_update_status === 'failed') {
          setNotice((st.progress_message || st.last_update_message || t('Panel update failed.')).trim());
        }
      }
    };
    await pollOnce();
    if (panelUpdateInterval.current) clearInterval(panelUpdateInterval.current);
    panelUpdateInterval.current = setInterval(pollOnce, 2000);
  }

  async function runOsUpdate() {
    if (!window.confirm(t('Run apt-get update && apt-get upgrade now?'))) return;
    setOsUpdating(true);
    const data = await request('/updates/os/run', { method: 'POST' }, t('Updating OS packages...'));
    setOsUpdating(false);
    if (data) {
      setNotice((data.stdout || data.stderr || t('OS update started in background.')).trim());
      if (showUpdateLog) await loadUpdates(false);
    }
  }

  async function saveOsAutoUpdate() {
    const data = await request(
      '/updates/os/auto',
      { method: 'POST', body: JSON.stringify(osAutoUpdate) },
      t('Saving OS auto update...')
    );
    if (data) {
      setNotice((data.stdout || data.stderr || t('OS auto update saved.')).trim());
    }
  }

  async function handleSavePhpSmartConfig() {
    setPhpSavingConfig(true);
    const payload = {
      auto_patch_enabled: !!phpConfigForm.auto_patch_enabled,
      auto_harden_php2shell: phpConfigForm.harden_mode !== 'off',
      harden_mode: phpConfigForm.harden_mode || 'strict',
      preferred_window: phpConfigForm.preferred_window || '03:00',
    };
    const res = await request(
      '/updates/php/config',
      { method: 'POST', body: JSON.stringify(payload) },
      t('Đang lưu cấu hình Smart PHP Auto-Patch & lớp bảo vệ chống PHP2SHELL...')
    );
    setPhpSavingConfig(false);
    if (res?.php_smart_guard) {
      setUpdatesStatus(prev => (prev ? { ...prev, php_smart_guard: res.php_smart_guard } : prev));
      setNotice(t('Đã lưu cấu hình Smart PHP Auto-Patch và đồng bộ lớp bảo vệ chống PHP2SHELL.'));
    }
  }

  async function handleRunPhpPatch(branch = null) {
    const label = branch ? `PHP ${branch}` : t('tất cả các phiên bản PHP');
    if (
      !window.confirm(
        t(
          'Thực hiện cập nhật bản vá bảo mật cho {label} ngay bây giờ? Hệ thống chỉ nâng cấp trong cùng nhánh major.minor, kiểm tra cú pháp php-fpm -t và reload mượt mà (Zero-Downtime, không làm gián đoạn website).',
          { label }
        )
      )
    ) {
      return;
    }
    setPhpPatchingBranch(branch || 'all');
    const res = await request(
      '/updates/php/run',
      {
        method: 'POST',
        body: JSON.stringify({ versions: branch ? [branch] : null }),
      },
      t('Đang cập nhật bản vá bảo mật PHP & kiểm tra Zero-Downtime Reload...')
    );
    setPhpPatchingBranch('');
    if (res) {
      if (res.php_smart_guard) {
        setUpdatesStatus(prev => (prev ? { ...prev, php_smart_guard: res.php_smart_guard } : prev));
      }
      setNotice(res.summary || t('Đã hoàn tất cập nhật bản vá bảo mật PHP an toàn.'));
    }
  }

  async function handleAcknowledgePhpUpdates(updateId = null) {
    const res = await request(
      '/updates/php/acknowledge',
      {
        method: 'POST',
        body: JSON.stringify({ update_id: updateId }),
        silent: true,
      },
      null
    );
    if (res?.php_smart_guard) {
      setUpdatesStatus(prev => (prev ? { ...prev, php_smart_guard: res.php_smart_guard } : prev));
    }
  }

  if (!isAdmin) {
    return (
      <div className="section">
        <h2>{t('Updates')}</h2>
        <p className="hint">{t('No permission.')}</p>
      </div>
    );
  }

  const panelUpdate = updatesStatus?.panel || {};
  const sourceInfo = updatesStatus?.source || {};
  const snapshots = updatesStatus?.snapshots || [];
  const phpSmartGuard = updatesStatus?.php_smart_guard || {};
  const phpVersions = Array.isArray(phpSmartGuard.versions) ? phpSmartGuard.versions : [];
  const phpUnacknowledged = Array.isArray(phpSmartGuard.unacknowledged_updates)
    ? phpSmartGuard.unacknowledged_updates
    : [];
  const phpRecentUpdates = Array.isArray(phpSmartGuard.recent_updates) ? phpSmartGuard.recent_updates : [];
  const phpUpdatableCount = Number(phpSmartGuard.updates_available_count || 0);
  const statusText = updatesStatus?.stdout || updatesStatus?.stderr || t('No OS package logs yet.');
  const updateAvailable = panelUpdate.update_available === true;
  const currentPanelVersion = panelUpdate.current_version || appVersion || '1.0.0';
  const latestPanelVersion = panelUpdate.latest_version || currentPanelVersion;
  const pct = Number(panelUpdate.progress_percent) || 0;
  const isRunningUpdate =
    panelUpdating ||
    (pct > 0 &&
      panelUpdate.last_update_status &&
      !['completed', 'failed', 'rolled_back', 'installed'].includes(panelUpdate.last_update_status));

  const fieldLabelStyle = {
    display: 'flex',
    flexDirection: 'column',
    gap: '5px',
    margin: 0,
    minWidth: 0,
  };
  const fieldTitleStyle = {
    fontSize: '12.5px',
    fontWeight: 600,
    color: 'var(--text-secondary)',
    whiteSpace: 'nowrap',
    overflow: 'hidden',
    textOverflow: 'ellipsis',
  };
  const selectInputStyle = {
    width: '100%',
    minWidth: 0,
    height: '38px',
    minHeight: '38px',
    padding: '0 10px',
    fontSize: '13px',
    margin: 0,
  };

  return (
    <div className="section">
      {/* 1. Top Header & Action Toolbar */}
      <div
        className="section-title"
        style={{
          marginBottom: '16px',
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          flexWrap: 'wrap',
          gap: '12px',
        }}
      >
        <div>
          <h2 style={{ display: 'flex', alignItems: 'center', gap: '10px', margin: 0 }}>
            <ShieldCheck size={20} style={{ color: 'var(--primary)' }} />
            <span>{t('Updates Online & Rollback Center')}</span>
          </h2>
          <p className="hint" style={{ marginTop: '4px', maxWidth: 'none' }}>
            {t(
              'Automatically detect new releases from your designated update server with pre-update snapshots and instant auto-rollback protection.'
            )}
          </p>
        </div>

        <div className="cb-toolbar-actions" style={{ display: 'flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap' }}>
          <button
            type="button"
            className="cb-btn secondary"
            disabled={!!loading || isRunningUpdate}
            onClick={() => loadUpdates(true)}
          >
            <RefreshCw size={14} /> {t('Check for Updates')}
          </button>
          <button
            type="button"
            className="cb-btn secondary"
            disabled={!!loading}
            onClick={() => setShowSourceModal(true)}
          >
            <Lock size={14} /> {t('Update Server Config')}
          </button>
          <button
            type="button"
            className="cb-btn secondary"
            onClick={() => setShowUpdateLog(prev => !prev)}
          >
            {showUpdateLog ? <X size={14} /> : <FileText size={14} />}
            {showUpdateLog ? t('Hide Logs') : t('View Logs')}
          </button>
        </div>
      </div>

      {/* 2. Balanced 4-Card KPI Strip (3-Line Vertical Hierarchy) */}
      <div
        className="cb-stats-strip"
        style={{
          gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))',
          gap: '14px',
          marginBottom: '16px',
        }}
      >
        <div className="cb-stat-box" style={{ padding: '14px 16px' }}>
          <div className="cb-stat-icon blue">
            <Server size={18} />
          </div>
          <div className="cb-stat-info" style={{ gap: '3px', flex: 1 }}>
            <span className="cb-stat-label">{t('Current Version')}</span>
            <strong style={{ display: 'block', fontSize: '15px', lineHeight: 1.25 }}>
              {fmtVer(currentPanelVersion)}
            </strong>
            <small className="cb-stat-sub" style={{ display: 'block', color: '#10b981' }}>
              {t('Active & Healthy')}
            </small>
          </div>
        </div>

        <div className="cb-stat-box" style={{ padding: '14px 16px' }}>
          <div className={`cb-stat-icon ${updateAvailable ? 'amber' : 'emerald'}`}>
            {updateAvailable ? <Download size={18} /> : <CheckCircle size={18} />}
          </div>
          <div className="cb-stat-info" style={{ gap: '3px', flex: 1 }}>
            <span className="cb-stat-label">{t('Latest Release')}</span>
            <strong style={{ display: 'block', fontSize: '15px', lineHeight: 1.25 }}>
              {fmtVer(latestPanelVersion)}
            </strong>
            <small
              className="cb-stat-sub"
              style={{
                display: 'block',
                color: updateAvailable ? '#d97706' : 'var(--text-muted)',
                fontWeight: updateAvailable ? 600 : 500,
              }}
            >
              {updateAvailable ? t('New update available') : t('Up to date')}
            </small>
          </div>
        </div>

        <div className="cb-stat-box" style={{ padding: '14px 16px' }}>
          <div className="cb-stat-icon indigo">
            <Lock size={18} />
          </div>
          <div className="cb-stat-info" style={{ gap: '3px', flex: 1 }}>
            <span className="cb-stat-label">{t('Designated Update Server')}</span>
            <strong
              title={sourceInfo.masked_url || 'Protected Update Server (TLS)'}
              style={{
                display: 'block',
                fontSize: '13.5px',
                lineHeight: 1.25,
                overflow: 'hidden',
                textOverflow: 'ellipsis',
                whiteSpace: 'nowrap',
              }}
            >
              {sourceInfo.masked_url || 'Protected Update Server (TLS)'}
            </strong>
            <small className="cb-stat-sub" style={{ display: 'block' }}>
              {t('Channel')}: {(sourceInfo.channel || 'stable').toUpperCase()}{' '}
              {sourceInfo.has_auth_token ? `· ${t('Token Auth')}` : '· TLS Protected'}
            </small>
          </div>
        </div>

        <div className="cb-stat-box" style={{ padding: '14px 16px' }}>
          <div className="cb-stat-icon emerald">
            <ArchiveRestore size={18} />
          </div>
          <div className="cb-stat-info" style={{ gap: '3px', flex: 1 }}>
            <span className="cb-stat-label">{t('Auto-Rollback Shield')}</span>
            <strong style={{ display: 'block', fontSize: '15px', lineHeight: 1.25 }}>
              {snapshots.length} {t('Snapshots')}
            </strong>
            <small className="cb-stat-sub" style={{ display: 'block', color: '#10b981' }}>
              {t('Auto-Rollback Enabled')}
            </small>
          </div>
        </div>
      </div>

      {/* 3. Hero Release & Transactional Update Banner */}
      <div
        style={{
          border: updateAvailable ? '1px solid rgba(245, 158, 11, 0.45)' : '1px solid var(--border)',
          borderRadius: '14px',
          padding: '18px 22px',
          background: updateAvailable
            ? 'linear-gradient(135deg, rgba(245, 158, 11, 0.07), var(--surface))'
            : 'var(--surface)',
          marginBottom: '16px',
        }}
      >
        <div
          style={{
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'center',
            flexWrap: 'wrap',
            gap: '16px',
          }}
        >
          <div style={{ flex: '1 1 420px', minWidth: 0 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '10px', flexWrap: 'wrap' }}>
              <strong style={{ fontSize: '15.5px', color: 'var(--text)' }}>
                {updateAvailable
                  ? t('New SPanel Release Available: {ver}', { ver: fmtVer(latestPanelVersion) })
                  : t('SPanel is running the latest release ({ver})', { ver: fmtVer(currentPanelVersion) })}
              </strong>
              <span className={`badge ${updateAvailable ? 'warn' : 'ok'}`}>
                {updateAvailable ? t('Update Available') : t('Up to date')}
              </span>
              {panelUpdate.sha256_ready && (
                <span className="badge ok" title={t('SHA-256 Checksum Verified')}>
                  SHA-256 Verified
                </span>
              )}
              {panelUpdate.last_update_status === 'rolled_back' && (
                <span className="badge warn">{t('Auto-Rolled Back Safely')}</span>
              )}
            </div>

            <div
              style={{
                marginTop: '7px',
                display: 'flex',
                alignItems: 'center',
                gap: '18px',
                flexWrap: 'wrap',
                fontSize: '12.5px',
                color: 'var(--text-secondary)',
              }}
            >
              <span style={{ display: 'inline-flex', alignItems: 'center', gap: '5px' }}>
                <Clock size={13} style={{ color: 'var(--text-muted)' }} />
                {t('Last checked')}:{' '}
                <strong style={{ color: 'var(--text)' }}>
                  {fmtDateTime(panelUpdate.last_checked_at) || t('Just now')}
                </strong>
              </span>
              {panelUpdate.last_update_finished_at && (
                <span>
                  {t('Last update')}:{' '}
                  <strong style={{ color: 'var(--text)' }}>
                    {fmtUpdateStatus(panelUpdate.last_update_status)} ({fmtDateTime(panelUpdate.last_update_finished_at)})
                  </strong>
                </span>
              )}
              {panelUpdate.rollback_snapshot_id && (
                <span>
                  {t('Protected by snapshot')}: <code>{panelUpdate.rollback_snapshot_id}</code>
                </span>
              )}
            </div>
          </div>

          <div style={{ display: 'flex', gap: '10px', alignItems: 'center', flexWrap: 'wrap' }}>
            <button
              type="button"
              className={`cb-btn ${updateAvailable ? 'primary' : 'secondary'}`}
              style={{ height: '38px', padding: '0 16px', fontWeight: 600 }}
              disabled={!!loading || isRunningUpdate}
              onClick={runPanelUpdate}
            >
              <RotateCcw size={14} className={isRunningUpdate ? 'spin' : ''} />
              {isRunningUpdate
                ? t('Updating Panel Safely...')
                : updateAvailable
                  ? t('Update Panel Now (Safe Mode)')
                  : t('Re-sync / Repair Current Release')}
            </button>
          </div>
        </div>

        {panelUpdate.check_error && (
          <div
            style={{
              marginTop: '14px',
              padding: '10px 14px',
              borderRadius: '10px',
              background: 'rgba(245, 158, 11, 0.1)',
              border: '1px solid rgba(245, 158, 11, 0.28)',
              color: 'var(--warning-text, #d97706)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
              flexWrap: 'wrap',
              gap: '10px',
              fontSize: '13px',
            }}
          >
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
              <AlertCircle size={15} style={{ flexShrink: 0 }} />
              <span>{panelUpdate.check_error}</span>
            </div>
            <button
              type="button"
              className="cb-btn secondary"
              style={{ height: '28px', padding: '0 10px', fontSize: '12px' }}
              onClick={() => setShowSourceModal(true)}
            >
              <Lock size={12} /> {t('Update Server Config')}
            </button>
          </div>
        )}

        {/* Changelog */}
        {Array.isArray(panelUpdate.changelog) && panelUpdate.changelog.length > 0 && (
          <div
            style={{
              marginTop: '14px',
              padding: '12px 16px',
              borderRadius: '10px',
              background: 'var(--surface-alt)',
              border: '1px solid var(--border)',
            }}
          >
            <strong style={{ fontSize: '13px', display: 'block', marginBottom: '6px' }}>
              {t('Release Notes & Changelog ({ver}):', { ver: fmtVer(latestPanelVersion) })}
            </strong>
            <ul style={{ margin: 0, paddingLeft: '18px', fontSize: '13px', lineHeight: 1.6 }}>
              {panelUpdate.changelog.map((item, idx) => (
                <li key={idx}>{item}</li>
              ))}
            </ul>
          </div>
        )}

        {/* 5-Step Transactional Progress Stepper */}
        {(isRunningUpdate ||
          panelUpdate.last_update_status === 'rolled_back' ||
          panelUpdate.last_update_status === 'failed') && (
          <div
            style={{
              marginTop: '16px',
              padding: '16px',
              borderRadius: '12px',
              background: 'var(--surface-alt)',
              border: '1px solid var(--border)',
            }}
          >
            <div
              style={{
                display: 'flex',
                justifyContent: 'space-between',
                alignItems: 'center',
                marginBottom: '10px',
                flexWrap: 'wrap',
                gap: '8px',
              }}
            >
              <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                <span
                  className={
                    panelUpdate.last_update_status === 'failed'
                      ? 'badge bad'
                      : panelUpdate.last_update_status === 'rolled_back'
                        ? 'badge warn'
                        : 'badge ok'
                  }
                >
                  {isRunningUpdate
                    ? t('Running Safe Update')
                    : panelUpdate.last_update_status === 'rolled_back'
                      ? t('Auto-Rolled Back')
                      : fmtUpdateStatus(panelUpdate.last_update_status)}
                </span>
                <strong style={{ fontSize: '13.5px' }}>
                  {panelUpdate.progress_message || t('Processing transactional update...')}
                </strong>
              </div>
              <strong style={{ fontSize: '14px' }}>{pct}%</strong>
            </div>

            <div className="progress-bar" style={{ height: '8px', marginBottom: '14px' }}>
              <div
                className="progress-bar-fill"
                style={{
                  width: `${Math.max(5, pct)}%`,
                  background:
                    panelUpdate.last_update_status === 'rolled_back'
                      ? '#f59e0b'
                      : panelUpdate.last_update_status === 'failed'
                        ? '#ef4444'
                        : undefined,
                }}
              />
            </div>

            <div
              style={{
                display: 'grid',
                gridTemplateColumns: 'repeat(auto-fit, minmax(170px, 1fr))',
                gap: '8px',
              }}
            >
              {UPDATE_STEPS.map(step => {
                const done = pct > step.minPct || panelUpdate.last_update_status === 'completed';
                const current = panelUpdate.progress_phase === step.key;
                return (
                  <div
                    key={step.key}
                    style={{
                      padding: '8px 10px',
                      borderRadius: '8px',
                      fontSize: '12px',
                      border: current ? '1px solid var(--primary)' : '1px solid var(--border)',
                      background: done ? 'rgba(16, 185, 129, 0.08)' : 'var(--surface)',
                      display: 'flex',
                      alignItems: 'center',
                      gap: '6px',
                    }}
                  >
                    <CheckCircle
                      size={13}
                      style={{
                        color: done ? '#10b981' : current ? 'var(--primary)' : 'var(--text-muted)',
                        flexShrink: 0,
                      }}
                    />
                    <span style={{ fontWeight: current || done ? 600 : 400 }}>{t(step.label)}</span>
                  </div>
                );
              })}
            </div>

            {panelUpdateLog.length > 0 && (
              <pre
                className="update-progress-log"
                style={{ marginTop: '12px', maxHeight: '220px', overflowY: 'auto' }}
              >
                {panelUpdateLog.join('\n')}
              </pre>
            )}
          </div>
        )}
      </div>

      {/* 4. Sanitized Update Logs Box (Collapsible) */}
      {showUpdateLog && (
        <div
          className="info-box firewall-status update-log-box"
          style={{ marginBottom: '16px', borderRadius: '12px' }}
        >
          <div className="update-log-head">
            <strong>{t('Sanitized Update & System Logs')}</strong>
            <button
              type="button"
              className="secondary-light"
              disabled={!!loading}
              onClick={() => loadUpdates(false)}
            >
              <RefreshCw size={13} /> {t('Refresh')}
            </button>
          </div>
          <pre style={{ maxHeight: '240px', overflowY: 'auto' }}>
            {panelUpdateLog.length > 0 ? panelUpdateLog.join('\n') : statusText}
          </pre>
        </div>
      )}

      {/* 4B. Smart PHP Auto-Patch Notification Banner (Unacknowledged Updates) */}
      {phpUnacknowledged.length > 0 && (
        <div
          style={{
            border: '1px solid rgba(16, 185, 129, 0.45)',
            borderRadius: '14px',
            padding: '16px 20px',
            background: 'linear-gradient(135deg, rgba(16, 185, 129, 0.08), var(--surface))',
            marginBottom: '16px',
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'center',
            flexWrap: 'wrap',
            gap: '14px',
          }}
        >
          <div style={{ flex: '1 1 420px', minWidth: 0 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap' }}>
              <CheckCircle size={17} style={{ color: '#10b981', flexShrink: 0 }} />
              <strong style={{ fontSize: '14.5px', color: 'var(--text)' }}>
                {t('Thông báo Smart Guard: Hệ thống vừa cập nhật bản vá bảo mật PHP an toàn')}
              </strong>
              <span className="badge ok">{t('Zero-Downtime Verified')}</span>
              <span className="badge">{t('Anti-PHP2SHELL Active')}</span>
            </div>
            <div style={{ marginTop: '6px', fontSize: '13px', color: 'var(--text-secondary)', lineHeight: 1.5 }}>
              {phpUnacknowledged[0]?.summary}
              {phpUnacknowledged[0]?.timestamp && (
                <span style={{ marginLeft: '10px', color: 'var(--text-muted)' }}>
                  ({fmtDateTime(phpUnacknowledged[0].timestamp)})
                </span>
              )}
            </div>
          </div>
          <button
            type="button"
            className="cb-btn secondary"
            style={{ height: '34px', padding: '0 14px' }}
            onClick={() => handleAcknowledgePhpUpdates(null)}
          >
            <CheckCircle size={13} /> {t('Đã xem / Xác nhận')}
          </button>
        </div>
      )}

      {/* 4C. Smart PHP Auto-Patching & Anti-PHP2SHELL Security Guard Card */}
      <div
        style={{
          border: phpUpdatableCount > 0 ? '1px solid rgba(245, 158, 11, 0.45)' : '1px solid var(--border)',
          borderRadius: '14px',
          overflow: 'hidden',
          background: 'var(--surface)',
          marginBottom: '16px',
        }}
      >
        <div
          style={{
            background: 'var(--surface-alt)',
            padding: '15px 20px',
            borderBottom: '1px solid var(--border)',
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'center',
            flexWrap: 'wrap',
            gap: '12px',
          }}
        >
          <div style={{ flex: '1 1 440px', minWidth: 0 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap' }}>
              <ShieldCheck size={17} style={{ color: '#10b981' }} />
              <strong style={{ fontSize: '15px', color: 'var(--text)' }}>
                {t('Smart PHP Auto-Patch & Chống tấn công PHP2SHELL (Zero-Downtime)')}
              </strong>
              <span className={`badge ${phpUpdatableCount > 0 ? 'warn' : 'ok'}`}>
                {phpUpdatableCount > 0
                  ? t('{n} bản vá bảo mật mới', { n: phpUpdatableCount })
                  : t('Tất cả nhánh PHP đã mới nhất')}
              </span>
              <span className={`badge ${phpConfigForm.harden_mode !== 'off' ? 'ok' : 'warn'}`}>
                {phpConfigForm.harden_mode === 'strict'
                  ? t('Anti-PHP2SHELL: Nghiêm ngặt (Strict)')
                  : phpConfigForm.harden_mode === 'balanced'
                    ? t('Anti-PHP2SHELL: Cân bằng')
                    : t('Anti-PHP2SHELL: Tắt')}
              </span>
            </div>
            <span style={{ display: 'block', marginTop: '4px', fontSize: '12.5px', color: 'var(--text-muted)', lineHeight: 1.45 }}>
              {t(
                'Tự động phát hiện và cập nhật bản vá bảo mật mới nhất trong cùng nhánh major.minor (không tự ý đổi phiên bản PHP của website), kiểm tra cú pháp php-fpm -t trước khi reload mượt mà và khóa các hàm thực thi shell nguy hiểm (exec, shell_exec, system, proc_open, passthru) trên PHP-FPM để chặn đứng PHP2SHELL.'
              )}
            </span>
          </div>

          <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap' }}>
            <button
              type="button"
              className={`cb-btn ${phpUpdatableCount > 0 ? 'primary' : 'secondary'}`}
              style={{ height: '36px', padding: '0 14px', fontWeight: 600 }}
              disabled={!!loading || !!phpPatchingBranch}
              onClick={() => handleRunPhpPatch(null)}
            >
              <RefreshCw size={14} className={phpPatchingBranch === 'all' ? 'spin' : ''} />
              {phpPatchingBranch === 'all'
                ? t('Đang cập nhật PHP an toàn...')
                : phpUpdatableCount > 0
                  ? t('Cập nhật bản vá PHP ngay ({n})', { n: phpUpdatableCount })
                  : t('Kiểm tra & Đồng bộ bảo mật PHP')}
            </button>
          </div>
        </div>

        {/* Configuration Bar */}
        <div
          style={{
            padding: '14px 20px',
            borderBottom: '1px solid var(--border)',
            background: 'var(--surface)',
            display: 'grid',
            gridTemplateColumns: 'repeat(auto-fit, minmax(210px, 1fr))',
            gap: '12px',
            alignItems: 'end',
          }}
        >
          <label style={fieldLabelStyle}>
            <span style={fieldTitleStyle}>{t('Tự động cập nhật bản vá PHP (Zero-Downtime)')}</span>
            <select
              style={selectInputStyle}
              value={phpConfigForm.auto_patch_enabled ? 'on' : 'off'}
              onChange={e =>
                setPhpConfigForm(prev => ({ ...prev, auto_patch_enabled: e.target.value === 'on' }))
              }
            >
              <option value="on">{t('Bật — Tự động vá lỗi an toàn & thông báo')}</option>
              <option value="off">{t('Tắt — Chỉ cảnh báo khi có bản vá mới')}</option>
            </select>
          </label>

          <label style={fieldLabelStyle}>
            <span style={fieldTitleStyle}>{t('Khung giờ tự động vá lỗi PHP')}</span>
            <select
              style={selectInputStyle}
              value={phpConfigForm.preferred_window}
              onChange={e =>
                setPhpConfigForm(prev => ({ ...prev, preferred_window: e.target.value }))
              }
            >
              <option value="03:00">{t('03:00 AM (Ban đêm — Khuyên dùng)')}</option>
              <option value="02:00">02:00 AM</option>
              <option value="04:00">04:00 AM</option>
              <option value="immediate">{t('Cập nhật ngay khi phát hiện bản vá')}</option>
            </select>
          </label>

          <label style={fieldLabelStyle}>
            <span style={fieldTitleStyle}>{t('Lớp bảo vệ chống PHP2SHELL / RCE (PHP-FPM)')}</span>
            <select
              style={selectInputStyle}
              value={phpConfigForm.harden_mode}
              onChange={e =>
                setPhpConfigForm(prev => ({
                  ...prev,
                  harden_mode: e.target.value,
                  auto_harden_php2shell: e.target.value !== 'off',
                }))
              }
            >
              <option value="strict">
                {t('Nghiêm ngặt — Khóa 15 hàm Shell & CGI (Khuyên dùng)')}
              </option>
              <option value="balanced">
                {t('Cân bằng — Khóa system/passthru/pcntl (Tương thích cũ)')}
              </option>
              <option value="off">{t('Tắt lớp khóa hàm tự động')}</option>
            </select>
          </label>

          <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
            <button
              type="button"
              className="cb-btn primary"
              style={{ height: '38px', padding: '0 16px', width: '100%', justifyContent: 'center', fontWeight: 600 }}
              disabled={!!loading || phpSavingConfig}
              onClick={handleSavePhpSmartConfig}
            >
              {phpSavingConfig ? t('Đang lưu...') : t('Lưu cấu hình Smart PHP')}
            </button>
          </div>
        </div>

        {/* Installed PHP Branches Status Table */}
        {phpVersions.length > 0 && (
          <div style={{ width: '100%', overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', textAlign: 'left', fontSize: '13.5px' }}>
              <thead>
                <tr style={{ background: 'var(--surface-alt)', borderBottom: '1px solid var(--border)' }}>
                  <th style={{ padding: '10px 20px' }}>{t('Nhánh PHP')}</th>
                  <th style={{ padding: '10px 16px' }}>{t('Phiên bản đang chạy')}</th>
                  <th style={{ padding: '10px 16px' }}>{t('Bản vá mới nhất (APT Repo)')}</th>
                  <th style={{ padding: '10px 16px' }}>{t('Trạng thái PHP-FPM')}</th>
                  <th style={{ padding: '10px 16px' }}>{t('Chống PHP2SHELL / RCE')}</th>
                  <th style={{ padding: '10px 20px', textAlign: 'right' }}>{t('Thao tác')}</th>
                </tr>
              </thead>
              <tbody>
                {phpVersions.map(row => {
                  const hasUpd = !!row.update_available;
                  const hard = row.hardening || {};
                  return (
                    <tr key={row.branch} style={{ borderBottom: '1px solid var(--border)' }}>
                      <td style={{ padding: '10px 20px', fontWeight: 600 }}>
                        PHP {row.branch}
                      </td>
                      <td style={{ padding: '10px 16px' }}>
                        <code>{row.current_version || row.branch}</code>
                      </td>
                      <td style={{ padding: '10px 16px' }}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap' }}>
                          <code>{row.latest_version || row.current_version}</code>
                          <span className={`badge ${hasUpd ? 'warn' : 'ok'}`}>
                            {hasUpd
                              ? t('Có bản vá mới ({n} gói)', { n: row.upgradable_count || 1 })
                              : t('Đã mới nhất')}
                          </span>
                        </div>
                      </td>
                      <td style={{ padding: '10px 16px' }}>
                        <span className={`badge ${row.fpm_active ? 'ok' : 'warn'}`}>
                          {row.fpm_active ? 'Active (Zero-Downtime)' : 'Inactive'}
                        </span>
                      </td>
                      <td style={{ padding: '10px 16px' }}>
                        <span
                          className={`badge ${hard.protected ? 'ok' : 'warn'}`}
                          title={
                            Array.isArray(hard.disabled_functions) && hard.disabled_functions.length > 0
                              ? `disable_functions: ${hard.disabled_functions.join(', ')}`
                              : ''
                          }
                        >
                          {hard.protected
                            ? t('Đã bảo vệ ({mode} · {n} hàm bị khóa)', {
                                mode: hard.mode === 'strict' ? 'Strict' : hard.mode === 'balanced' ? 'Balanced' : 'Custom',
                                n: hard.disabled_count || 0,
                              })
                            : t('Chưa bật khóa hàm')}
                        </span>
                      </td>
                      <td style={{ padding: '10px 20px', textAlign: 'right' }}>
                        <button
                          type="button"
                          className={`cb-btn ${hasUpd ? 'primary' : 'secondary'}`}
                          style={{ height: '30px', padding: '0 12px', fontSize: '12.5px' }}
                          disabled={!!loading || !!phpPatchingBranch}
                          onClick={() => handleRunPhpPatch(row.branch)}
                        >
                          <RefreshCw size={12} className={phpPatchingBranch === row.branch ? 'spin' : ''} />
                          {phpPatchingBranch === row.branch
                            ? t('Đang vá...')
                            : hasUpd
                              ? t('Cập nhật ngay')
                              : t('Đồng bộ lại')}
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}

        {/* Recent Auto-Patch & Security Audit Log */}
        {phpRecentUpdates.length > 0 && (
          <div
            style={{
              padding: '12px 20px',
              background: 'var(--surface-alt)',
              borderTop: '1px solid var(--border)',
            }}
          >
            <strong style={{ fontSize: '12.5px', color: 'var(--text-secondary)', display: 'block', marginBottom: '8px' }}>
              {t('Lịch sử cập nhật bảo mật PHP gần đây (Audit Trail):')}
            </strong>
            <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
              {phpRecentUpdates.slice(0, 4).map(rec => (
                <div
                  key={rec.id}
                  style={{
                    display: 'flex',
                    justifyContent: 'space-between',
                    alignItems: 'center',
                    flexWrap: 'wrap',
                    gap: '8px',
                    fontSize: '12.5px',
                    padding: '7px 10px',
                    borderRadius: '8px',
                    background: 'var(--surface)',
                    border: '1px solid var(--border)',
                  }}
                >
                  <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap' }}>
                    <span className={`badge ${rec.trigger === 'auto' ? 'ok' : ''}`}>
                      {rec.trigger === 'auto' ? t('Tự động (Smart Guard)') : t('Thủ công')}
                    </span>
                    <span style={{ color: 'var(--text)', fontWeight: 500 }}>{rec.summary}</span>
                  </div>
                  <span style={{ color: 'var(--text-muted)', fontSize: '12px' }}>
                    {fmtDateTime(rec.timestamp)}
                  </span>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>

      {/* 5. Recovery Snapshots & 1-Click Rollback Card */}
      <div
        style={{
          border: '1px solid var(--border)',
          borderRadius: '14px',
          overflow: 'hidden',
          background: 'var(--surface)',
          marginBottom: '16px',
        }}
      >
        <div
          style={{
            background: 'var(--surface-alt)',
            padding: '14px 20px',
            borderBottom: '1px solid var(--border)',
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'center',
            flexWrap: 'wrap',
            gap: '12px',
          }}
        >
          <div>
            <strong style={{ fontSize: '15px', display: 'block', color: 'var(--text)' }}>
              {t('Recovery Snapshots & 1-Click Rollback')}
            </strong>
            <span style={{ fontSize: '12.5px', color: 'var(--text-muted)' }}>
              {t(
                'Before every update, SPanel automatically archives the current code, UI build, helper, and SQLite database so it can roll back instantaneously.'
              )}
            </span>
          </div>
          <button
            type="button"
            className="cb-btn secondary"
            style={{ height: '36px', padding: '0 14px' }}
            disabled={!!loading || creatingSnapshot || isRunningUpdate}
            onClick={handleCreateSnapshot}
          >
            <Archive size={14} /> {creatingSnapshot ? t('Creating...') : t('Create Snapshot Now')}
          </button>
        </div>

        {snapshots.length === 0 ? (
          <div
            style={{
              padding: '28px 20px',
              display: 'flex',
              flexDirection: 'column',
              alignItems: 'center',
              justifyContent: 'center',
              textAlign: 'center',
            }}
          >
            <div
              style={{
                width: '44px',
                height: '44px',
                borderRadius: '12px',
                background: 'var(--surface-alt)',
                border: '1px solid var(--border)',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                marginBottom: '10px',
                color: 'var(--text-muted)',
              }}
            >
              <ArchiveRestore size={22} />
            </div>
            <strong style={{ fontSize: '14.5px', color: 'var(--text)', marginBottom: '4px' }}>
              {t('No recovery snapshots yet')}
            </strong>
            <div
              style={{
                fontSize: '13px',
                color: 'var(--text-muted)',
                maxWidth: '520px',
                margin: '0 auto',
                lineHeight: 1.5,
              }}
            >
              {t(
                'A snapshot is created automatically before every update, or click "Create Snapshot Now" to save a recovery point manually.'
              )}
            </div>
          </div>
        ) : (
          <div style={{ width: '100%', overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', textAlign: 'left', fontSize: '13.5px' }}>
              <thead>
                <tr style={{ background: 'var(--surface-alt)', borderBottom: '1px solid var(--border)' }}>
                  <th style={{ padding: '11px 20px' }}>{t('Snapshot ID')}</th>
                  <th style={{ padding: '11px 16px' }}>{t('Version')}</th>
                  <th style={{ padding: '11px 16px' }}>{t('Type')}</th>
                  <th style={{ padding: '11px 16px' }}>{t('Created At')}</th>
                  <th style={{ padding: '11px 16px' }}>{t('Size')}</th>
                  <th style={{ padding: '11px 20px', textAlign: 'right' }}>{t('Actions')}</th>
                </tr>
              </thead>
              <tbody>
                {snapshots.map(snap => (
                  <tr key={snap.id} style={{ borderBottom: '1px solid var(--border)' }}>
                    <td style={{ padding: '11px 20px' }}>
                      <code>{snap.id}</code>
                    </td>
                    <td style={{ padding: '11px 16px' }}>
                      <span className="badge ok">{fmtVer(snap.version)}</span>
                    </td>
                    <td style={{ padding: '11px 16px' }}>
                      <span className="badge">
                        {snap.reason === 'manual' ? t('Manual Snapshot') : t('Pre-Update Auto')}
                      </span>
                    </td>
                    <td style={{ padding: '11px 16px' }}>{fmtDateTime(snap.created_at)}</td>
                    <td style={{ padding: '11px 16px' }}>{snap.size_mb} MB</td>
                    <td
                      style={{
                        padding: '11px 20px',
                        textAlign: 'right',
                        display: 'flex',
                        justifyContent: 'flex-end',
                        gap: '8px',
                      }}
                    >
                      <button
                        type="button"
                        className="cb-btn secondary"
                        disabled={!!loading || rollingBackId === snap.id || isRunningUpdate}
                        onClick={() => handleRestoreSnapshot(snap)}
                      >
                        <ArchiveRestore size={13} />{' '}
                        {rollingBackId === snap.id ? t('Rolling back...') : t('Rollback to this version')}
                      </button>
                      <button
                        type="button"
                        className="cb-btn danger"
                        disabled={!!loading || isRunningUpdate}
                        onClick={() => handleDeleteSnapshot(snap)}
                      >
                        <Trash2 size={13} />
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* 6. Symmetrical Two-Column Automation & OS Security Cards */}
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fit, minmax(420px, 1fr))',
          gap: '16px',
          alignItems: 'stretch',
        }}
      >
        {/* Left Card: Automatic Version Discovery & Auto-Update */}
        <div
          style={{
            border: '1px solid var(--border)',
            borderRadius: '14px',
            padding: '18px 20px',
            background: 'var(--surface)',
            display: 'flex',
            flexDirection: 'column',
            justifyContent: 'space-between',
            gap: '14px',
          }}
        >
          <div>
            <div
              style={{
                display: 'flex',
                justifyContent: 'space-between',
                alignItems: 'center',
                gap: '10px',
                marginBottom: '6px',
              }}
            >
              <h3 style={{ margin: 0, fontSize: '15px', display: 'flex', alignItems: 'center', gap: '8px' }}>
                <Zap size={16} style={{ color: 'var(--primary)' }} />
                <span>{t('Automatic Version Discovery & Auto-Update')}</span>
              </h3>
              <span className={`badge ${sourceForm.auto_check_enabled ? 'ok' : ''}`}>
                {sourceForm.auto_check_enabled ? t('Auto-Check Active') : t('Manual Check')}
              </span>
            </div>
            <div style={{ fontSize: '12.5px', color: 'var(--text-muted)', lineHeight: 1.5 }}>
              {t(
                'Automatically poll the designated update server for new releases and optionally install them during your maintenance window with Auto-Rollback protection.'
              )}
            </div>
          </div>

          <div
            style={{
              display: 'grid',
              gridTemplateColumns: 'minmax(0, 1fr) minmax(0, 1fr) minmax(0, 0.85fr) auto',
              gap: '10px',
              alignItems: 'end',
            }}
          >
            <label style={fieldLabelStyle}>
              <span style={fieldTitleStyle}>{t('Auto-Detect New Releases')}</span>
              <select
                style={selectInputStyle}
                value={sourceForm.auto_check_enabled ? 'on' : 'off'}
                onChange={e =>
                  setSourceForm(prev => ({ ...prev, auto_check_enabled: e.target.value === 'on' }))
                }
              >
                <option value="on">{t('On (Every 6 hours)')}</option>
                <option value="off">{t('Off (Manual check only)')}</option>
              </select>
            </label>

            <label style={fieldLabelStyle}>
              <span style={fieldTitleStyle}>{t('Auto-Install Updates')}</span>
              <select
                style={selectInputStyle}
                value={sourceForm.auto_update_enabled ? 'on' : 'off'}
                onChange={e =>
                  setSourceForm(prev => ({ ...prev, auto_update_enabled: e.target.value === 'on' }))
                }
              >
                <option value="on">{t('On (With Auto-Rollback)')}</option>
                <option value="off">{t('Off (Notify Admin only)')}</option>
              </select>
            </label>

            <label style={fieldLabelStyle}>
              <span style={fieldTitleStyle}>{t('Maintenance Window')}</span>
              <select
                style={selectInputStyle}
                value={sourceForm.auto_update_window}
                onChange={e =>
                  setSourceForm(prev => ({ ...prev, auto_update_window: e.target.value }))
                }
              >
                <option value="01:00">01:00 AM</option>
                <option value="02:00">02:00 AM</option>
                <option value="03:00">03:00 AM</option>
                <option value="04:00">04:00 AM</option>
              </select>
            </label>

            <button
              type="button"
              className="cb-btn primary"
              style={{ height: '38px', padding: '0 16px', whiteSpace: 'nowrap', fontWeight: 600 }}
              disabled={!!loading}
              onClick={handleSaveSourceConfig}
            >
              {t('Save Settings')}
            </button>
          </div>
        </div>

        {/* Right Card: OS Security Updates (APT) */}
        <div
          style={{
            border: '1px solid var(--border)',
            borderRadius: '14px',
            padding: '18px 20px',
            background: 'var(--surface)',
            display: 'flex',
            flexDirection: 'column',
            justifyContent: 'space-between',
            gap: '14px',
          }}
        >
          <div>
            <div
              style={{
                display: 'flex',
                justifyContent: 'space-between',
                alignItems: 'center',
                gap: '10px',
                marginBottom: '6px',
              }}
            >
              <h3 style={{ margin: 0, fontSize: '15px', display: 'flex', alignItems: 'center', gap: '8px' }}>
                <ShieldCheck size={16} style={{ color: '#10b981' }} />
                <span>{t('OS Security Updates (APT)')}</span>
              </h3>
              <button
                type="button"
                className="cb-btn secondary"
                style={{ height: '30px', padding: '0 12px', fontSize: '12.5px' }}
                disabled={!!loading || osUpdating}
                onClick={runOsUpdate}
              >
                <RefreshCw size={12} className={osUpdating ? 'spin' : ''} />
                {osUpdating ? t('Updating OS...') : t('Update OS Now')}
              </button>
            </div>
            <div style={{ fontSize: '12.5px', color: 'var(--text-muted)', lineHeight: 1.5 }}>
              {t('Keep Ubuntu / Debian system packages and security patches up to date via unattended-upgrades.')}
            </div>
          </div>

          <div
            style={{
              display: 'grid',
              gridTemplateColumns: 'minmax(0, 1fr) minmax(0, 1fr) minmax(0, 0.85fr) auto',
              gap: '10px',
              alignItems: 'end',
            }}
          >
            <label style={fieldLabelStyle}>
              <span style={fieldTitleStyle}>{t('Enabled')}</span>
              <select
                style={selectInputStyle}
                value={osAutoUpdate.enabled ? 'on' : 'off'}
                onChange={e => setOsAutoUpdate(prev => ({ ...prev, enabled: e.target.value === 'on' }))}
              >
                <option value="on">{t('On')}</option>
                <option value="off">{t('Off')}</option>
              </select>
            </label>

            <label style={fieldLabelStyle}>
              <span style={fieldTitleStyle}>{t('Mode')}</span>
              <select
                style={selectInputStyle}
                value={osAutoUpdate.mode}
                onChange={e => setOsAutoUpdate(prev => ({ ...prev, mode: e.target.value }))}
              >
                <option value="security">{t('Security')}</option>
                <option value="all">{t('All packages')}</option>
              </select>
            </label>

            <label style={fieldLabelStyle}>
              <span style={fieldTitleStyle}>{t('Auto reboot')}</span>
              <select
                style={selectInputStyle}
                value={osAutoUpdate.auto_reboot ? 'on' : 'off'}
                onChange={e =>
                  setOsAutoUpdate(prev => ({ ...prev, auto_reboot: e.target.value === 'on' }))
                }
              >
                <option value="off">{t('Off')}</option>
                <option value="on">{t('On')}</option>
              </select>
            </label>

            <button
              type="button"
              className="cb-btn primary"
              style={{ height: '38px', padding: '0 16px', whiteSpace: 'nowrap', fontWeight: 600 }}
              disabled={!!loading}
              onClick={saveOsAutoUpdate}
            >
              {t('Save Settings')}
            </button>
          </div>
        </div>
      </div>

      {/* 7. Centered Protected Designated Update Server Configuration Modal */}
      {showSourceModal && (
        <div
          className="modal-overlay"
          style={{
            position: 'fixed',
            inset: 0,
            backgroundColor: 'rgba(15, 23, 42, 0.55)',
            backdropFilter: 'blur(4px)',
            zIndex: 1000,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            padding: '20px',
          }}
          onClick={() => setShowSourceModal(false)}
        >
          <div
            className="modal"
            style={{
              maxWidth: '580px',
              width: '100%',
              borderRadius: '16px',
              background: 'var(--surface)',
              border: '1px solid var(--border)',
              boxShadow: '0 24px 48px rgba(0, 0, 0, 0.25)',
              overflow: 'hidden',
            }}
            onClick={e => e.stopPropagation()}
          >
            <div
              className="modal-header"
              style={{
                padding: '16px 22px',
                borderBottom: '1px solid var(--border)',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'space-between',
                gap: '12px',
              }}
            >
              <h3 style={{ margin: 0, fontSize: '16px', display: 'flex', alignItems: 'center', gap: '8px' }}>
                <Lock size={17} style={{ color: 'var(--primary)' }} />
                <span>{t('Designated Update Server Configuration')}</span>
              </h3>
              <button
                type="button"
                className="secondary-light"
                style={{ width: '32px', height: '32px', minHeight: '32px', padding: 0, borderRadius: '8px' }}
                onClick={() => setShowSourceModal(false)}
              >
                <X size={15} />
              </button>
            </div>

            <form onSubmit={handleSaveSourceConfig}>
              <div
                className="modal-body"
                style={{
                  padding: '20px 22px',
                  display: 'flex',
                  flexDirection: 'column',
                  gap: '14px',
                }}
              >
                <div
                  style={{
                    padding: '12px 14px',
                    borderRadius: '10px',
                    background: 'var(--surface-alt)',
                    border: '1px solid var(--border)',
                    fontSize: '12.5px',
                    lineHeight: 1.5,
                  }}
                >
                  <div style={{ color: 'var(--text)' }}>
                    <strong>{t('Stealth Protection Active:')}</strong>{' '}
                    {t(
                      'Update server URLs and authentication tokens are stored with 0600 root-only permissions and masked in all API responses and logs.'
                    )}
                  </div>
                  <div style={{ marginTop: '6px', display: 'flex', alignItems: 'center', gap: '6px', flexWrap: 'wrap' }}>
                    <span style={{ color: 'var(--text-secondary)' }}>{t('Current Masked Endpoint')}:</span>
                    <code>{sourceInfo.masked_url || 'Protected Update Server (TLS)'}</code>
                  </div>
                </div>

                <div style={{ display: 'grid', gridTemplateColumns: '1.15fr 0.85fr', gap: '12px' }}>
                  <label style={{ display: 'flex', flexDirection: 'column', gap: '5px', margin: 0 }}>
                    <span style={{ fontSize: '13px', fontWeight: 600, margin: 0 }}>{t('Protocol Mode')}</span>
                    <select
                      value={sourceForm.source_type}
                      onChange={e => setSourceForm(prev => ({ ...prev, source_type: e.target.value }))}
                      style={{ width: '100%', height: '38px', margin: 0 }}
                    >
                      <option value="auto">{t('Auto (Manifest JSON + Release Tag)')}</option>
                      <option value="manifest">{t('Designated Manifest Server (JSON)')}</option>
                      <option value="git">{t('Designated Git Release Server')}</option>
                    </select>
                  </label>

                  <label style={{ display: 'flex', flexDirection: 'column', gap: '5px', margin: 0 }}>
                    <span style={{ fontSize: '13px', fontWeight: 600, margin: 0 }}>{t('Release Channel')}</span>
                    <select
                      value={sourceForm.channel}
                      onChange={e => setSourceForm(prev => ({ ...prev, channel: e.target.value }))}
                      style={{ width: '100%', height: '38px', margin: 0 }}
                    >
                      <option value="stable">{t('Stable (Recommended)')}</option>
                      <option value="beta">{t('Beta / Testing')}</option>
                    </select>
                  </label>
                </div>

                <label style={{ display: 'flex', flexDirection: 'column', gap: '5px', margin: 0 }}>
                  <span style={{ fontSize: '13px', fontWeight: 600, margin: 0 }}>
                    {t('New Designated Update Server URL (Write-Only)')}
                  </span>
                  <input
                    type="text"
                    value={sourceForm.server_url}
                    onChange={e => setSourceForm(prev => ({ ...prev, server_url: e.target.value }))}
                    placeholder={t('Leave blank to keep current protected server ({url})', {
                      url: sourceInfo.masked_url || 'Protected',
                    })}
                    autoComplete="off"
                    style={{ width: '100%', height: '38px', margin: 0 }}
                  />
                </label>

                <label style={{ display: 'flex', flexDirection: 'column', gap: '5px', margin: 0 }}>
                  <span style={{ fontSize: '13px', fontWeight: 600, margin: 0 }}>
                    {t('Custom Version Manifest URL (Optional, Write-Only)')}
                  </span>
                  <input
                    type="text"
                    value={sourceForm.manifest_url}
                    onChange={e => setSourceForm(prev => ({ ...prev, manifest_url: e.target.value }))}
                    placeholder={t('Optional JSON manifest endpoint for version & SHA-256 discovery')}
                    autoComplete="off"
                    style={{ width: '100%', height: '38px', margin: 0 }}
                  />
                </label>

                <label style={{ display: 'flex', flexDirection: 'column', gap: '5px', margin: 0 }}>
                  <span style={{ fontSize: '13px', fontWeight: 600, margin: 0, display: 'flex', alignItems: 'center', gap: '6px' }}>
                    <span>{t('Update Server Access Token / Secret Key (Write-Only)')}</span>
                    {sourceInfo.has_auth_token && (
                      <span className="badge ok">{t('Configured')}</span>
                    )}
                  </span>
                  <input
                    type="password"
                    value={sourceForm.auth_token}
                    onChange={e => setSourceForm(prev => ({ ...prev, auth_token: e.target.value }))}
                    placeholder={
                      sourceInfo.has_auth_token
                        ? t('•••••••••••• (Leave blank to keep current token)')
                        : t('Enter Bearer / X-SPanel-Update-Token if required by server')
                    }
                    autoComplete="new-password"
                    style={{ width: '100%', height: '38px', margin: 0 }}
                  />
                </label>

                <div
                  style={{
                    padding: '12px 14px',
                    borderRadius: '10px',
                    background: 'var(--surface-alt)',
                    border: '1px solid var(--border)',
                    display: 'grid',
                    gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))',
                    gap: '10px',
                  }}
                >
                  <label
                    className="check-line"
                    style={{
                      display: 'inline-flex',
                      alignItems: 'center',
                      gap: '8px',
                      margin: 0,
                      fontSize: '13px',
                      fontWeight: 500,
                      cursor: 'pointer',
                    }}
                  >
                    <input
                      type="checkbox"
                      checked={sourceForm.verify_checksum}
                      onChange={e =>
                        setSourceForm(prev => ({ ...prev, verify_checksum: e.target.checked }))
                      }
                      style={{
                        width: '16px',
                        height: '16px',
                        minHeight: 0,
                        margin: 0,
                        flexShrink: 0,
                        accentColor: 'var(--primary)',
                      }}
                    />
                    <span style={{ margin: 0, display: 'inline' }}>{t('Enforce SHA-256 integrity verification')}</span>
                  </label>

                  <label
                    className="check-line"
                    style={{
                      display: 'inline-flex',
                      alignItems: 'center',
                      gap: '8px',
                      margin: 0,
                      fontSize: '13px',
                      fontWeight: 500,
                      cursor: 'pointer',
                    }}
                  >
                    <input
                      type="checkbox"
                      checked={sourceForm.auto_rollback_enabled}
                      onChange={e =>
                        setSourceForm(prev => ({ ...prev, auto_rollback_enabled: e.target.checked }))
                      }
                      style={{
                        width: '16px',
                        height: '16px',
                        minHeight: 0,
                        margin: 0,
                        flexShrink: 0,
                        accentColor: 'var(--primary)',
                      }}
                    />
                    <span style={{ margin: 0, display: 'inline' }}>{t('Automatic Rollback on failed healthcheck')}</span>
                  </label>
                </div>
              </div>

              <div
                className="modal-footer"
                style={{
                  padding: '14px 22px',
                  background: 'var(--surface-alt)',
                  borderTop: '1px solid var(--border)',
                  display: 'flex',
                  justifyContent: 'flex-end',
                  gap: '10px',
                }}
              >
                <button
                  type="button"
                  className="cb-btn secondary"
                  onClick={() => setShowSourceModal(false)}
                >
                  {t('Cancel')}
                </button>
                <button type="submit" className="cb-btn primary" disabled={!!loading}>
                  <Lock size={14} /> {t('Save & Verify Update Server')}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}
