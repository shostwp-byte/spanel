import React, { useState, useEffect, useRef } from 'react';
import {
  Cloud,
  Server,
  Upload,
  FolderArchive,
  Search,
  Trash2,
  RefreshCw,
  CheckCircle2,
  AlertTriangle,
  Play,
  Copy,
  Download,
  Globe,
  Database,
  Mail,
  ShieldCheck,
  Terminal,
  Link2,
  HardDrive,
} from 'lucide-react';
import { t } from '../i18n.js';

const PANEL_CARDS = [
  {
    id: 'cpanel',
    title: 'cPanel / WHM',
    port: '2087 / 2083',
    formats: 'cpmove-user.tar.gz, backup-*.tar.gz',
    desc: 'Khôi phục trọn bộ tài khoản cPanel: Main/Addon/Sub/Parked Domains, MySQL + Hash, Email + Maildir, SSL.',
    accent: '#ff6c2c',
  },
  {
    id: 'directadmin',
    title: 'DirectAdmin',
    port: '2222',
    formats: 'user.admin.*.tar.zst, .tar.gz',
    desc: 'Khôi phục full backup DirectAdmin: Users, Domains, Subdomains, Pointers, MariaDB, Email + Maildir, SSL.',
    accent: '#3b82f6',
  },
  {
    id: 'plesk',
    title: 'Plesk Obsidian',
    port: '8443',
    formats: 'backup_info_*.tar, .tar.gz, .zip',
    desc: 'Khôi phục backup từ Plesk: Subscriptions, vhosts/httpdocs, Subdomains, Databases và hộp thư Email.',
    accent: '#06b6d4',
  },
];

function formatBytes(bytes) {
  const n = Number(bytes || 0);
  if (!n || n <= 0) return '0 B';
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  if (n < 1024 * 1024 * 1024) return `${(n / (1024 * 1024)).toFixed(2)} MB`;
  return `${(n / (1024 * 1024 * 1024)).toFixed(2)} GB`;
}

export function Migration({ request, setError, setNotice, loading }) {
  const [migrateJob, setMigrateJob] = useState(null);
  const [recentJobs, setRecentJobs] = useState([]);
  const timerRef = useRef(null);
  const fileInputRef = useRef(null);

  // Server backup files state
  const [backups, setBackups] = useState([]);
  const [backupDir, setBackupDir] = useState('/home/admin/spanel_backups/da');
  const [selectedArchives, setSelectedArchives] = useState([]);
  const [uploading, setUploading] = useState(false);
  const [scanningPath, setScanningPath] = useState('');
  const [scanResult, setScanResult] = useState(null);

  // Remote Panel API discovery state
  const [remoteForm, setRemoteForm] = useState({
    host_url: '',
    username: 'root',
    secret: '',
    auth_type: 'token',
    verify_ssl: false,
  });
  const [remoteAccounts, setRemoteAccounts] = useState([]);
  const [selectedRemoteAccounts, setSelectedRemoteAccounts] = useState([]);
  const [fetchingRemote, setFetchingRemote] = useState(false);
  const [triggeringBackup, setTriggeringBackup] = useState(false);

  // Main migration form
  const [migrateForm, setMigrateForm] = useState({
    panel_type: 'cpanel',
    method: 'local', // local | sftp | url | ftp
    source_url: '',
    ftp_host: '',
    ftp_port: 22,
    ftp_user: 'root',
    ftp_pass: '',
    ftp_path: '/backup',
    import_websites: true,
    import_databases: true,
    import_emails: true,
    force: false,
  });

  useEffect(() => {
    loadServerBackups();
    loadRecentJobs();
    return () => {
      if (timerRef.current) clearInterval(timerRef.current);
    };
  }, []);

  async function loadServerBackups() {
    const data = await request('/migration/backups', { silent: true });
    if (data) {
      setBackups(data.items || []);
      if (data.directory) setBackupDir(data.directory);
    }
  }

  async function loadRecentJobs() {
    const data = await request('/migration/jobs', { silent: true });
    if (data?.jobs?.length) {
      setRecentJobs(data.jobs);
      const active = data.jobs.find((j) => j.status === 'running' || j.status === 'queued');
      if (active) {
        setMigrateJob(active);
        pollMigrationJob(active.id);
      }
    }
  }

  function handleSelectPanel(panelId) {
    setMigrateForm((prev) => ({
      ...prev,
      panel_type: panelId,
      ftp_path:
        panelId === 'directadmin'
          ? '/home/admin/admin_backups'
          : panelId === 'cpanel'
          ? '/backup'
          : '/var/lib/psa/dumps',
    }));
    setRemoteForm((prev) => ({
      ...prev,
      username: panelId === 'directadmin' ? 'admin' : 'root',
      auth_type: panelId === 'cpanel' ? 'token' : 'password',
    }));
    setScanResult(null);
  }

  function handleSelectMethod(method) {
    setMigrateForm((prev) => ({
      ...prev,
      method,
      ftp_port: method === 'sftp' ? 22 : method === 'ftp' ? 21 : prev.ftp_port,
    }));
  }

  async function handleUploadBackup(e) {
    const files = Array.from(e.target.files || []);
    if (!files.length) return;
    setUploading(true);
    try {
      const uploadedPaths = [];
      for (const file of files) {
        const fd = new FormData();
        fd.append('file', file);
        const res = await request('/migration/upload', {
          method: 'POST',
          body: fd,
        });
        if (res?.path) {
          uploadedPaths.push(res.path);
          if (res.detected_panel) {
            setMigrateForm((prev) => ({ ...prev, panel_type: res.detected_panel }));
          }
        }
      }
      if (uploadedPaths.length) {
        setNotice(`Đã tải lên thành công ${uploadedPaths.length} file backup.`);
        await loadServerBackups();
        setSelectedArchives((prev) => Array.from(new Set([...prev, ...uploadedPaths])));
      }
    } finally {
      setUploading(false);
      if (fileInputRef.current) fileInputRef.current.value = '';
    }
  }

  async function handleScanArchive(archivePath, detectedPanel) {
    setScanningPath(archivePath);
    setScanResult(null);
    try {
      const panelToUse = migrateForm.panel_type || detectedPanel || 'cpanel';
      const data = await request('/migration/scan', {
        method: 'POST',
        body: JSON.stringify({
          archive_path: archivePath,
          panel_type: panelToUse,
        }),
      });
      if (data) {
        setScanResult(data);
        if (!selectedArchives.includes(archivePath)) {
          setSelectedArchives((prev) => [...prev, archivePath]);
        }
      }
    } finally {
      setScanningPath('');
    }
  }

  async function handleDeleteArchive(archivePath) {
    if (!window.confirm('Bạn có chắc muốn xóa file backup này khỏi máy chủ?')) return;
    const res = await request('/migration/backups', {
      method: 'DELETE',
      body: JSON.stringify({ archive_path: archivePath }),
    });
    if (res?.deleted) {
      setNotice(`Đã xóa file backup: ${res.deleted}`);
      setSelectedArchives((prev) => prev.filter((p) => p !== archivePath));
      if (scanResult?.archive_path === archivePath) setScanResult(null);
      await loadServerBackups();
    }
  }

  async function handleFetchRemoteAccounts() {
    if (!remoteForm.host_url.trim()) {
      return setError('Vui lòng nhập địa chỉ IP hoặc tên miền của máy chủ Panel cũ.');
    }
    if (!remoteForm.secret.trim()) {
      return setError('Vui lòng nhập mật khẩu hoặc API Token / Login Key.');
    }
    setFetchingRemote(true);
    try {
      const data = await request('/migration/remote-accounts', {
        method: 'POST',
        body: JSON.stringify({
          panel_type: migrateForm.panel_type,
          ...remoteForm,
        }),
      });
      if (data?.accounts) {
        setRemoteAccounts(data.accounts);
        setSelectedRemoteAccounts(data.accounts.map((a) => a.username).filter(Boolean));
        setNotice(`Đã tìm thấy ${data.accounts.length} tài khoản trên máy chủ ${migrateForm.panel_type.toUpperCase()}.`);
        // Auto-fill SFTP host if empty
        if (!migrateForm.ftp_host) {
          try {
            const rawHost = remoteForm.host_url.replace(/^https?:\/\//i, '').split('/')[0].split(':')[0];
            if (rawHost) {
              setMigrateForm((prev) => ({ ...prev, ftp_host: rawHost }));
            }
          } catch (_) {}
        }
      }
    } finally {
      setFetchingRemote(false);
    }
  }

  async function handleTriggerRemoteBackup() {
    if (!selectedRemoteAccounts.length) {
      return setError('Hãy chọn ít nhất 1 tài khoản để kích hoạt tạo backup trên máy chủ cũ.');
    }
    setTriggeringBackup(true);
    try {
      const res = await request('/migration/remote-trigger-backup', {
        method: 'POST',
        body: JSON.stringify({
          panel_type: migrateForm.panel_type,
          ...remoteForm,
          accounts: selectedRemoteAccounts,
        }),
      });
      if (res?.message) {
        setNotice(res.message);
      }
    } finally {
      setTriggeringBackup(false);
    }
  }

  function toggleArchiveSelection(path) {
    setSelectedArchives((prev) =>
      prev.includes(path) ? prev.filter((p) => p !== path) : [...prev, path]
    );
  }

  function toggleRemoteAccountSelection(username) {
    setSelectedRemoteAccounts((prev) =>
      prev.includes(username) ? prev.filter((u) => u !== username) : [...prev, username]
    );
  }

  async function startUserMigration() {
    if (migrateForm.method === 'local' && !selectedArchives.length) {
      return setError('Vui lòng chọn ít nhất 1 file backup trong danh sách (hoặc tải file lên).');
    }
    if (migrateForm.method === 'url' && !migrateForm.source_url.trim()) {
      return setError('Vui lòng nhập đường dẫn URL trực tiếp của file backup.');
    }
    if ((migrateForm.method === 'ftp' || migrateForm.method === 'sftp') && !migrateForm.ftp_host.trim()) {
      return setError('Vui lòng nhập địa chỉ Host / IP của máy chủ cũ.');
    }

    const payload = {
      ...migrateForm,
      archive_paths: migrateForm.method === 'local' ? selectedArchives : [],
      archive_path: migrateForm.method === 'local' ? selectedArchives[0] || null : null,
      selected_accounts: migrateForm.method === 'sftp' ? selectedRemoteAccounts : [],
    };

    const data = await request(
      '/migration/start',
      { method: 'POST', body: JSON.stringify(payload) },
      t('Starting migration...')
    );
    if (data?.job_id) {
      setNotice('Đã bắt đầu tiến trình Migration! Bạn có thể theo dõi log trực tiếp bên dưới.');
      const initialJob = {
        id: data.job_id,
        panel_type: migrateForm.panel_type,
        method: migrateForm.method,
        status: 'running',
        progress: 'Đang khởi tạo tiến trình Migration...',
        percent: 5,
        logs: [],
        summary: [],
        credentials: [],
      };
      setMigrateJob(initialJob);
      pollMigrationJob(data.job_id);
    }
  }

  async function pollMigrationJob(jobId) {
    if (timerRef.current) clearInterval(timerRef.current);
    const fetchOnce = async () => {
      const data = await request(`/migration/status/${jobId}`, { silent: true });
      if (data) {
        setMigrateJob(data);
        if (
          data.status === 'completed' ||
          data.status === 'completed_with_warnings' ||
          data.status === 'failed'
        ) {
          if (timerRef.current) {
            clearInterval(timerRef.current);
            timerRef.current = null;
          }
          loadRecentJobs();
          loadServerBackups();
          if (data.status === 'completed' || data.status === 'completed_with_warnings') {
            setNotice('Quá trình Migration đã hoàn tất!');
          } else if (data.status === 'failed') {
            setError('Migration thất bại: ' + (data.error || 'Lỗi không xác định'));
          }
        }
      }
    };
    await fetchOnce();
    timerRef.current = setInterval(fetchOnce, 2500);
  }

  function copyCredentials(creds) {
    const text = (creds || []).join('\n');
    navigator.clipboard.writeText(text);
    setNotice('Đã sao chép thông tin tài khoản vào Clipboard.');
  }

  function downloadCredentials(creds, jobId) {
    const text = (creds || []).join('\n');
    const blob = new Blob([text], { type: 'text/plain;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `spanel-migration-credentials-${(jobId || 'export').slice(0, 8)}.txt`;
    a.click();
    URL.revokeObjectURL(url);
  }

  return (
    <div className="section packages-page">
      <div className="section-title" style={{ marginBottom: 18 }}>
        <div>
          <h2>{t('Data Migration Center')}</h2>
          <p className="hint">
            Chuyển dữ liệu tự động từ cPanel/WHM, DirectAdmin và Plesk sang SPanel (Websites, Databases, Emails, SSL & DNS).
          </p>
        </div>
      </div>

      {/* Live Progress / Active Job View */}
      {migrateJob && (
        <div
          style={{
            background: 'var(--bg-elevated, #111b2e)',
            border: '1px solid var(--border, #24324b)',
            borderRadius: 12,
            padding: 20,
            marginBottom: 24,
          }}
        >
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 12 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
              {migrateJob.status === 'running' || migrateJob.status === 'queued' ? (
                <RefreshCw size={20} className="spin" style={{ color: '#3b82f6' }} />
              ) : migrateJob.status === 'failed' ? (
                <AlertTriangle size={20} style={{ color: '#ef4444' }} />
              ) : (
                <CheckCircle2 size={20} style={{ color: '#10b981' }} />
              )}
              <div>
                <h3 style={{ margin: 0, fontSize: 16 }}>
                  Tiến trình Migration ({(migrateJob.panel_type || '').toUpperCase()}) —{' '}
                  {migrateJob.status === 'running' || migrateJob.status === 'queued'
                    ? 'Đang xử lý...'
                    : migrateJob.status === 'failed'
                    ? 'Thất bại'
                    : 'Hoàn tất'}
                </h3>
                <div style={{ fontSize: 13, color: '#94a3b8', marginTop: 2 }}>{migrateJob.progress}</div>
              </div>
            </div>

            {(migrateJob.status === 'completed' ||
              migrateJob.status === 'completed_with_warnings' ||
              migrateJob.status === 'failed') && (
              <button
                type="button"
                className="ghost"
                onClick={() => setMigrateJob(null)}
              >
                Đóng bảng kết quả
              </button>
            )}
          </div>

          {/* Progress Bar */}
          <div
            style={{
              height: 8,
              background: 'rgba(148, 163, 184, 0.15)',
              borderRadius: 999,
              overflow: 'hidden',
              marginTop: 14,
              marginBottom: 14,
            }}
          >
            <div
              style={{
                width: `${migrateJob.percent ?? (migrateJob.status === 'completed' ? 100 : 25)}%`,
                height: '100%',
                background:
                  migrateJob.status === 'failed'
                    ? '#ef4444'
                    : migrateJob.status === 'completed' || migrateJob.status === 'completed_with_warnings'
                    ? '#10b981'
                    : 'linear-gradient(90deg, #3b82f6, #06b6d4)',
                transition: 'width 0.4s ease',
              }}
            />
          </div>

          {/* Live Log Box */}
          {migrateJob.logs?.length > 0 && (
            <div
              style={{
                background: '#090f1d',
                border: '1px solid #1e293b',
                borderRadius: 8,
                padding: '10px 14px',
                maxHeight: 200,
                overflowY: 'auto',
                fontFamily: 'monospace',
                fontSize: 12.5,
                lineHeight: 1.6,
                color: '#cbd5e1',
                marginBottom: 14,
              }}
            >
              {migrateJob.logs.map((line, i) => (
                <div key={i}>{line}</div>
              ))}
            </div>
          )}

          {/* Error Banner */}
          {migrateJob.error && (
            <div
              style={{
                background: 'rgba(239, 68, 68, 0.12)',
                border: '1px solid rgba(239, 68, 68, 0.35)',
                color: '#fca5a5',
                padding: '10px 14px',
                borderRadius: 8,
                fontSize: 13,
                marginBottom: 14,
              }}
            >
              <strong>Chi tiết lỗi:</strong> {migrateJob.error}
            </div>
          )}

          {/* Imported Summary Cards */}
          {migrateJob.summary?.length > 0 && (
            <div style={{ display: 'grid', gap: 12, marginTop: 12 }}>
              {migrateJob.summary.map((item, idx) => (
                <div
                  key={idx}
                  style={{
                    background: 'rgba(15, 23, 42, 0.65)',
                    border: '1px solid #1e293b',
                    borderRadius: 10,
                    padding: 14,
                  }}
                >
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
                    <strong style={{ color: '#e2e8f0', fontSize: 14 }}>
                      Tài khoản: <span style={{ color: '#38bdf8' }}>{item.username}</span>{' '}
                      <span style={{ color: '#64748b', fontWeight: 400, fontSize: 12 }}>({item.archive})</span>
                    </strong>
                  </div>
                  <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))', gap: 10, fontSize: 13 }}>
                    <div>
                      <Globe size={13} style={{ marginRight: 5, verticalAlign: 'middle', color: '#38bdf8' }} />
                      <strong>Domains ({item.imported_domains?.length || 0}):</strong>{' '}
                      <span style={{ color: '#94a3b8' }}>
                        {(item.imported_domains || []).join(', ') || 'Không có'}
                      </span>
                    </div>
                    <div>
                      <Database size={13} style={{ marginRight: 5, verticalAlign: 'middle', color: '#a855f7' }} />
                      <strong>Databases ({item.databases?.length || 0}):</strong>{' '}
                      <span style={{ color: '#94a3b8' }}>
                        {(item.databases || []).map((d) => d.db_name).join(', ') || 'Không có'}
                      </span>
                    </div>
                    <div>
                      <Mail size={13} style={{ marginRight: 5, verticalAlign: 'middle', color: '#10b981' }} />
                      <strong>Emails ({item.mailboxes?.length || 0}):</strong>{' '}
                      <span style={{ color: '#94a3b8' }}>
                        {(item.mailboxes || []).join(', ') || 'Không có'}
                      </span>
                    </div>
                    <div>
                      <ShieldCheck size={13} style={{ marginRight: 5, verticalAlign: 'middle', color: '#f59e0b' }} />
                      <strong>SSL:</strong>{' '}
                      <span style={{ color: '#94a3b8' }}>
                        {[...(item.ssl_enabled_domains || []), ...(item.ssl_imported_domains || [])].join(', ') || 'Chưa kích hoạt'}
                      </span>
                    </div>
                  </div>
                  {item.warnings?.length > 0 && (
                    <div style={{ marginTop: 10, fontSize: 12, color: '#fbbf24' }}>
                      {item.warnings.map((w, wi) => (
                        <div key={wi}>• {w}</div>
                      ))}
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}

          {/* Generated Credentials */}
          {migrateJob.credentials?.length > 4 && (
            <div
              style={{
                marginTop: 14,
                background: '#090f1d',
                border: '1px solid #1e293b',
                borderRadius: 10,
                padding: 14,
              }}
            >
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8, flexWrap: 'wrap', gap: 8 }}>
                <strong style={{ fontSize: 13, color: '#38bdf8' }}>
                  Thông tin đăng nhập được khởi tạo (Panel / SFTP / Database / Email):
                </strong>
                <div style={{ display: 'flex', gap: 8 }}>
                  <button
                    type="button"
                    className="ghost"
                    style={{ padding: '4px 10px', fontSize: 12 }}
                    onClick={() => copyCredentials(migrateJob.credentials)}
                  >
                    <Copy size={13} /> Sao chép
                  </button>
                  <button
                    type="button"
                    className="ghost"
                    style={{ padding: '4px 10px', fontSize: 12 }}
                    onClick={() => downloadCredentials(migrateJob.credentials, migrateJob.id)}
                  >
                    <Download size={13} /> Tải file .txt
                  </button>
                </div>
              </div>
              <pre
                style={{
                  margin: 0,
                  fontSize: 12,
                  color: '#a7f3d0',
                  overflowX: 'auto',
                  maxHeight: 160,
                }}
              >
                {migrateJob.credentials.join('\n')}
              </pre>
            </div>
          )}
        </div>
      )}

      {/* STEP 1: Select Source Control Panel */}
      <div style={{ marginBottom: 22 }}>
        <div style={{ fontSize: 13, fontWeight: 600, color: '#94a3b8', textTransform: 'uppercase', letterSpacing: 0.6, marginBottom: 10 }}>
          Bước 1: Chọn Control Panel Nguồn
        </div>
        <div
          style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))',
            gap: 14,
          }}
        >
          {PANEL_CARDS.map((card) => {
            const active = migrateForm.panel_type === card.id;
            return (
              <div
                key={card.id}
                onClick={() => handleSelectPanel(card.id)}
                style={{
                  cursor: 'pointer',
                  background: active ? 'rgba(59, 130, 246, 0.1)' : 'var(--bg-elevated, #111b2e)',
                  border: active ? `2px solid ${card.accent}` : '1px solid var(--border, #24324b)',
                  borderRadius: 12,
                  padding: 16,
                  transition: 'all 0.18s ease',
                }}
              >
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 6 }}>
                  <span style={{ fontSize: 16, fontWeight: 700, color: '#f8fafc' }}>{card.title}</span>
                  <span
                    style={{
                      fontSize: 11,
                      padding: '2px 8px',
                      borderRadius: 999,
                      background: 'rgba(148, 163, 184, 0.14)',
                      color: card.accent,
                      fontWeight: 600,
                    }}
                  >
                    Port {card.port}
                  </span>
                </div>
                <div style={{ fontSize: 12.5, color: '#94a3b8', lineHeight: 1.5, marginBottom: 8 }}>{card.desc}</div>
                <div style={{ fontSize: 11.5, color: '#64748b', fontFamily: 'monospace' }}>
                  Định dạng: {card.formats}
                </div>
              </div>
            );
          })}
        </div>
      </div>

      {/* STEP 2: Transfer Method Tabs */}
      <div
        style={{
          background: 'var(--bg-elevated, #111b2e)',
          border: '1px solid var(--border, #24324b)',
          borderRadius: 12,
          padding: 20,
          marginBottom: 22,
        }}
      >
        <div style={{ fontSize: 13, fontWeight: 600, color: '#94a3b8', textTransform: 'uppercase', letterSpacing: 0.6, marginBottom: 12 }}>
          Bước 2: Chọn Phương thức Lấy File Backup ({migrateForm.panel_type.toUpperCase()})
        </div>

        {/* Method Tabs */}
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginBottom: 18 }}>
          <button
            type="button"
            className={migrateForm.method === 'local' ? '' : 'ghost'}
            onClick={() => handleSelectMethod('local')}
          >
            <FolderArchive size={14} /> File Backup trên Server / Upload
          </button>
          <button
            type="button"
            className={migrateForm.method === 'sftp' ? '' : 'ghost'}
            onClick={() => handleSelectMethod('sftp')}
          >
            <Server size={14} /> Kết nối VPS Cũ (Remote Panel API & SFTP)
          </button>
          <button
            type="button"
            className={migrateForm.method === 'url' ? '' : 'ghost'}
            onClick={() => handleSelectMethod('url')}
          >
            <Link2 size={14} /> Tải trực tiếp qua URL (wget)
          </button>
          <button
            type="button"
            className={migrateForm.method === 'ftp' ? '' : 'ghost'}
            onClick={() => handleSelectMethod('ftp')}
          >
            <HardDrive size={14} /> Tải qua Remote FTP
          </button>
        </div>

        {/* TAB 1: Local Server Backups & Upload */}
        {migrateForm.method === 'local' && (
          <div>
            <div
              style={{
                display: 'flex',
                justifyContent: 'space-between',
                alignItems: 'center',
                flexWrap: 'wrap',
                gap: 10,
                padding: '12px 14px',
                background: 'rgba(15, 23, 42, 0.55)',
                border: '1px dashed #334155',
                borderRadius: 10,
                marginBottom: 14,
              }}
            >
              <div>
                <div style={{ fontSize: 13.5, fontWeight: 600, color: '#e2e8f0' }}>
                  Thư mục chứa file backup trên máy chủ: <code style={{ color: '#38bdf8' }}>{backupDir}</code>
                </div>
                <div style={{ fontSize: 12, color: '#94a3b8', marginTop: 2 }}>
                  Bạn có thể tải file backup (.tar.gz, .tgz, .tar.zst, .zip) trực tiếp từ máy tính hoặc copy qua SFTP vào thư mục trên rồi bấm Làm mới.
                </div>
              </div>
              <div style={{ display: 'flex', gap: 8 }}>
                <input
                  ref={fileInputRef}
                  type="file"
                  multiple
                  accept=".tar,.gz,.tgz,.zst,.tzst,.bz2,.xz,.zip"
                  style={{ display: 'none' }}
                  onChange={handleUploadBackup}
                />
                <button
                  type="button"
                  disabled={uploading}
                  onClick={() => fileInputRef.current?.click()}
                >
                  <Upload size={14} /> {uploading ? 'Đang tải lên...' : 'Upload File Backup'}
                </button>
                <button type="button" className="ghost" onClick={loadServerBackups}>
                  <RefreshCw size={14} /> Làm mới
                </button>
              </div>
            </div>

            {backups.length === 0 ? (
              <div style={{ textAlign: 'center', padding: '26px 16px', color: '#64748b', fontSize: 13.5 }}>
                Chưa có file backup nào trong thư mục <code>{backupDir}</code>. Hãy bấm <strong>Upload File Backup</strong> hoặc chuyển sang tab <strong>Kết nối VPS Cũ</strong>.
              </div>
            ) : (
              <div className="table-wrap" style={{ maxHeight: 320, overflowY: 'auto' }}>
                <table>
                  <thead>
                    <tr>
                      <th style={{ width: 38 }}>
                        <input
                          type="checkbox"
                          checked={backups.length > 0 && selectedArchives.length === backups.length}
                          onChange={(e) =>
                            setSelectedArchives(e.target.checked ? backups.map((b) => b.path) : [])
                          }
                        />
                      </th>
                      <th>Tên File Backup</th>
                      <th>Định dạng nhận diện</th>
                      <th>Dung lượng</th>
                      <th style={{ textAlign: 'right' }}>Thao tác</th>
                    </tr>
                  </thead>
                  <tbody>
                    {backups.map((b) => {
                      const checked = selectedArchives.includes(b.path);
                      return (
                        <tr key={b.path} style={checked ? { background: 'rgba(59, 130, 246, 0.08)' } : undefined}>
                          <td>
                            <input
                              type="checkbox"
                              checked={checked}
                              onChange={() => toggleArchiveSelection(b.path)}
                            />
                          </td>
                          <td style={{ fontFamily: 'monospace', fontWeight: 600 }}>{b.filename}</td>
                          <td>
                            <span
                              style={{
                                fontSize: 11.5,
                                padding: '2px 8px',
                                borderRadius: 999,
                                background: 'rgba(59, 130, 246, 0.15)',
                                color: '#60a5fa',
                                fontWeight: 600,
                                textTransform: 'uppercase',
                              }}
                            >
                              {b.detected_panel || migrateForm.panel_type}
                            </span>
                          </td>
                          <td>{formatBytes(b.size)}</td>
                          <td style={{ textAlign: 'right' }}>
                            <div style={{ display: 'inline-flex', gap: 6 }}>
                              <button
                                type="button"
                                className="ghost"
                                style={{ padding: '4px 10px', fontSize: 12 }}
                                disabled={scanningPath === b.path}
                                onClick={() => handleScanArchive(b.path, b.detected_panel)}
                              >
                                <Search size={13} />{' '}
                                {scanningPath === b.path ? 'Đang quét...' : 'Xem trước (Scan)'}
                              </button>
                              <button
                                type="button"
                                className="ghost danger"
                                style={{ padding: '4px 8px', fontSize: 12 }}
                                onClick={() => handleDeleteArchive(b.path)}
                              >
                                <Trash2 size={13} />
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

            {/* Scan Preview Panel */}
            {scanResult && (
              <div
                style={{
                  marginTop: 16,
                  background: '#090f1d',
                  border: '1px solid #1e293b',
                  borderRadius: 10,
                  padding: 16,
                }}
              >
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 10 }}>
                  <strong style={{ color: '#38bdf8', fontSize: 14 }}>
                    Kết quả kiểm tra trước khi Import: <code>{scanResult.filename}</code> ({formatBytes(scanResult.size)})
                  </strong>
                  <button type="button" className="ghost" style={{ padding: '2px 8px', fontSize: 12 }} onClick={() => setScanResult(null)}>
                    Đóng
                  </button>
                </div>

                {(scanResult.existing_users?.length > 0 || scanResult.existing_domains?.length > 0) && (
                  <div
                    style={{
                      background: 'rgba(245, 158, 11, 0.12)',
                      border: '1px solid rgba(245, 158, 11, 0.4)',
                      color: '#fcd34d',
                      padding: '8px 12px',
                      borderRadius: 8,
                      fontSize: 12.5,
                      marginBottom: 12,
                    }}
                  >
                    <AlertTriangle size={14} style={{ verticalAlign: 'middle', marginRight: 6 }} />
                    Phát hiện dữ liệu đã tồn tại trên SPanel:{' '}
                    {scanResult.existing_users?.length > 0 && `User (${scanResult.existing_users.join(', ')}) `}
                    {scanResult.existing_domains?.length > 0 && `Domain (${scanResult.existing_domains.join(', ')})`}.
                    Hãy bật tùy chọn <strong>"Ghi đè nếu User / Website đã tồn tại"</strong> ở Bước 3 nếu bạn muốn thay thế.
                  </div>
                )}

                {(scanResult.users || []).map((u, idx) => (
                  <div key={idx} style={{ fontSize: 13 }}>
                    <div style={{ marginBottom: 8, color: '#e2e8f0' }}>
                      <strong>User:</strong> <code style={{ color: '#38bdf8' }}>{u.username}</code>{' '}
                      {u.email && <span style={{ color: '#94a3b8' }}>({u.email})</span>} —{' '}
                      <strong>{u.domains?.length || 0}</strong> Website,{' '}
                      <strong>
                        {(u.databases?.length || 0) + (u.domains || []).filter((d) => d.db_name).length}
                      </strong>{' '}
                      Database, <strong>{u.mailboxes?.length || 0}</strong> Hộp thư Email
                    </div>
                    {u.domains?.length > 0 && (
                      <div className="table-wrap">
                        <table>
                          <thead>
                            <tr>
                              <th>Tên miền (Domain)</th>
                              <th>Loại mã nguồn</th>
                              <th>Mã nguồn Web</th>
                              <th>Database đi kèm</th>
                              <th>Số Email</th>
                              <th>Parked / Aliases</th>
                            </tr>
                          </thead>
                          <tbody>
                            {u.domains.map((d) => (
                              <tr key={d.domain}>
                                <td style={{ fontWeight: 600 }}>{d.domain}</td>
                                <td style={{ textTransform: 'uppercase', fontSize: 12 }}>{d.app_type}</td>
                                <td>{d.has_files ? 'Có (public_html)' : 'Trống'}</td>
                                <td>{d.db_name ? `${d.db_name} ${d.has_sql_dump ? '(Có SQL)' : ''}` : '—'}</td>
                                <td>{d.email_count || 0}</td>
                                <td>{(d.aliases || []).map((a) => a.domain).join(', ') || '—'}</td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>
        )}

        {/* TAB 2: Remote Panel API Inspector + SFTP Pull */}
        {migrateForm.method === 'sftp' && (
          <div style={{ display: 'grid', gap: 18 }}>
            {/* Sub-box A: Remote Panel API Account Discovery */}
            <div
              style={{
                background: 'rgba(15, 23, 42, 0.55)',
                border: '1px solid #1e293b',
                borderRadius: 10,
                padding: 16,
              }}
            >
              <div style={{ fontWeight: 600, fontSize: 14, color: '#e2e8f0', marginBottom: 4 }}>
                1. Kết nối API máy chủ {migrateForm.panel_type.toUpperCase()} để lấy danh sách tài khoản (Tùy chọn)
              </div>
              <div style={{ fontSize: 12.5, color: '#94a3b8', marginBottom: 12 }}>
                Giúp bạn kiểm tra danh sách User/Domain đang hoạt động trên VPS cũ và kích hoạt lệnh nén Full Backup từ xa.
              </div>

              <div
                style={{
                  display: 'grid',
                  gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))',
                  gap: 12,
                  alignItems: 'end',
                }}
              >
                <label style={{ margin: 0 }}>
                  <span>Panel URL / IP</span>
                  <input
                    type="text"
                    value={remoteForm.host_url}
                    onChange={(e) => setRemoteForm((p) => ({ ...p, host_url: e.target.value }))}
                    placeholder={
                      migrateForm.panel_type === 'cpanel'
                        ? 'https://103.x.x.x:2087'
                        : migrateForm.panel_type === 'directadmin'
                        ? 'https://103.x.x.x:2222'
                        : 'https://103.x.x.x:8443'
                    }
                  />
                </label>
                <label style={{ margin: 0 }}>
                  <span>Admin / Reseller Username</span>
                  <input
                    type="text"
                    value={remoteForm.username}
                    onChange={(e) => setRemoteForm((p) => ({ ...p, username: e.target.value }))}
                    placeholder="root / admin"
                  />
                </label>
                <label style={{ margin: 0 }}>
                  <span>Kiểu xác thực</span>
                  <select
                    value={remoteForm.auth_type}
                    onChange={(e) => setRemoteForm((p) => ({ ...p, auth_type: e.target.value }))}
                  >
                    <option value="token">API Token / Login Key</option>
                    <option value="password">Mật khẩu (Password)</option>
                  </select>
                </label>
                <label style={{ margin: 0 }}>
                  <span>{remoteForm.auth_type === 'token' ? 'API Token / Login Key' : 'Password'}</span>
                  <input
                    type="password"
                    value={remoteForm.secret}
                    onChange={(e) => setRemoteForm((p) => ({ ...p, secret: e.target.value }))}
                    placeholder="••••••••••••"
                  />
                </label>
              </div>

              <div style={{ display: 'flex', gap: 10, marginTop: 12, flexWrap: 'wrap' }}>
                <button
                  type="button"
                  disabled={fetchingRemote}
                  onClick={handleFetchRemoteAccounts}
                >
                  <Search size={14} />{' '}
                  {fetchingRemote ? 'Đang kết nối Panel...' : 'Lấy danh sách Tài khoản từ Panel cũ'}
                </button>
                {remoteAccounts.length > 0 && migrateForm.panel_type !== 'plesk' && (
                  <button
                    type="button"
                    className="ghost"
                    disabled={triggeringBackup || !selectedRemoteAccounts.length}
                    onClick={handleTriggerRemoteBackup}
                  >
                    <RefreshCw size={14} className={triggeringBackup ? 'spin' : ''} />{' '}
                    {triggeringBackup
                      ? 'Đang gửi lệnh tạo Backup...'
                      : `Kích hoạt tạo Full Backup trên VPS cũ (${selectedRemoteAccounts.length} TK)`}
                  </button>
                )}
              </div>

              {remoteAccounts.length > 0 && (
                <div className="table-wrap" style={{ maxHeight: 240, overflowY: 'auto', marginTop: 12 }}>
                  <table>
                    <thead>
                      <tr>
                        <th style={{ width: 36 }}>
                          <input
                            type="checkbox"
                            checked={
                              remoteAccounts.length > 0 &&
                              selectedRemoteAccounts.length === remoteAccounts.length
                            }
                            onChange={(e) =>
                              setSelectedRemoteAccounts(
                                e.target.checked ? remoteAccounts.map((a) => a.username) : []
                              )
                            }
                          />
                        </th>
                        <th>Username</th>
                        <th>Primary Domain</th>
                        <th>Package</th>
                        <th>Dung lượng</th>
                        <th>Trạng thái</th>
                      </tr>
                    </thead>
                    <tbody>
                      {remoteAccounts.map((a) => (
                        <tr key={a.username}>
                          <td>
                            <input
                              type="checkbox"
                              checked={selectedRemoteAccounts.includes(a.username)}
                              onChange={() => toggleRemoteAccountSelection(a.username)}
                            />
                          </td>
                          <td style={{ fontWeight: 600, fontFamily: 'monospace' }}>{a.username}</td>
                          <td>{a.domain || '—'}</td>
                          <td>{a.package || '—'}</td>
                          <td>{a.disk_used || '—'}</td>
                          <td>
                            <span style={{ color: a.suspended ? '#ef4444' : '#10b981', fontSize: 12 }}>
                              {a.suspended ? 'Suspended' : 'Active'}
                            </span>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>

            {/* Sub-box B: SFTP Transfer Configuration */}
            <div
              style={{
                background: 'rgba(15, 23, 42, 0.55)',
                border: '1px solid #1e293b',
                borderRadius: 10,
                padding: 16,
              }}
            >
              <div style={{ fontWeight: 600, fontSize: 14, color: '#e2e8f0', marginBottom: 4 }}>
                2. Thông tin SFTP (SSH) để kéo file Backup trực tiếp về SPanel
              </div>
              <div style={{ fontSize: 12.5, color: '#94a3b8', marginBottom: 12 }}>
                Nhập đường dẫn file cụ thể (VD: <code>/home/cpmove-user.tar.gz</code>) hoặc thư mục chứa các bản sao lưu (VD: <code>/backup</code> hoặc <code>/home/admin/admin_backups</code>).
              </div>

              <div
                style={{
                  display: 'grid',
                  gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))',
                  gap: 12,
                }}
              >
                <label style={{ margin: 0 }}>
                  <span>SFTP Host / IP</span>
                  <input
                    type="text"
                    value={migrateForm.ftp_host}
                    onChange={(e) => setMigrateForm((p) => ({ ...p, ftp_host: e.target.value }))}
                    placeholder="103.x.x.x"
                  />
                </label>
                <label style={{ margin: 0 }}>
                  <span>SSH / SFTP Port</span>
                  <input
                    type="number"
                    value={migrateForm.ftp_port}
                    onChange={(e) =>
                      setMigrateForm((p) => ({ ...p, ftp_port: parseInt(e.target.value || '22', 10) }))
                    }
                  />
                </label>
                <label style={{ margin: 0 }}>
                  <span>SSH Username</span>
                  <input
                    type="text"
                    value={migrateForm.ftp_user}
                    onChange={(e) => setMigrateForm((p) => ({ ...p, ftp_user: e.target.value }))}
                    placeholder="root"
                  />
                </label>
                <label style={{ margin: 0 }}>
                  <span>SSH Password</span>
                  <input
                    type="password"
                    value={migrateForm.ftp_pass}
                    onChange={(e) => setMigrateForm((p) => ({ ...p, ftp_pass: e.target.value }))}
                    placeholder="••••••••••••"
                  />
                </label>
                <label style={{ margin: 0, gridColumn: '1 / -1' }}>
                  <span>Đường dẫn File hoặc Thư mục Backup trên VPS cũ</span>
                  <input
                    type="text"
                    value={migrateForm.ftp_path}
                    onChange={(e) => setMigrateForm((p) => ({ ...p, ftp_path: e.target.value }))}
                    placeholder="/backup/cpmove-user.tar.gz hoặc /home/admin/admin_backups"
                  />
                </label>
              </div>
            </div>
          </div>
        )}

        {/* TAB 3: Direct Download URL (wget) */}
        {migrateForm.method === 'url' && (
          <div>
            <label style={{ margin: 0 }}>
              <span>Đường dẫn tải trực tiếp File Backup (HTTP / HTTPS)</span>
              <input
                type="text"
                value={migrateForm.source_url}
                onChange={(e) => setMigrateForm((p) => ({ ...p, source_url: e.target.value }))}
                placeholder="https://old-server.example.com/cpmove-username.tar.gz"
              />
            </label>
            <div className="hint" style={{ marginTop: 6 }}>
              SPanel sẽ tải trực tiếp gói sao lưu từ URL này về máy chủ và tự động giải nén, khôi phục theo chuẩn {migrateForm.panel_type.toUpperCase()}.
            </div>
          </div>
        )}

        {/* TAB 4: Remote FTP */}
        {migrateForm.method === 'ftp' && (
          <div
            style={{
              display: 'grid',
              gridTemplateColumns: 'repeat(auto-fit, minmax(210px, 1fr))',
              gap: 12,
            }}
          >
            <label style={{ margin: 0 }}>
              <span>FTP Host / IP</span>
              <input
                type="text"
                value={migrateForm.ftp_host}
                onChange={(e) => setMigrateForm((p) => ({ ...p, ftp_host: e.target.value }))}
                placeholder="103.x.x.x"
              />
            </label>
            <label style={{ margin: 0 }}>
              <span>FTP Port</span>
              <input
                type="number"
                value={migrateForm.ftp_port}
                onChange={(e) =>
                  setMigrateForm((p) => ({ ...p, ftp_port: parseInt(e.target.value || '21', 10) }))
                }
              />
            </label>
            <label style={{ margin: 0 }}>
              <span>FTP Username</span>
              <input
                type="text"
                value={migrateForm.ftp_user}
                onChange={(e) => setMigrateForm((p) => ({ ...p, ftp_user: e.target.value }))}
                placeholder="ftpuser"
              />
            </label>
            <label style={{ margin: 0 }}>
              <span>FTP Password</span>
              <input
                type="password"
                value={migrateForm.ftp_pass}
                onChange={(e) => setMigrateForm((p) => ({ ...p, ftp_pass: e.target.value }))}
                placeholder="••••••••••••"
              />
            </label>
            <label style={{ margin: 0, gridColumn: '1 / -1' }}>
              <span>Đường dẫn File Backup trên FTP</span>
              <input
                type="text"
                value={migrateForm.ftp_path}
                onChange={(e) => setMigrateForm((p) => ({ ...p, ftp_path: e.target.value }))}
                placeholder="/backups/cpmove-user.tar.gz"
              />
            </label>
          </div>
        )}
      </div>

      {/* STEP 3: Restore Options & Start Button */}
      <div
        style={{
          background: 'var(--bg-elevated, #111b2e)',
          border: '1px solid var(--border, #24324b)',
          borderRadius: 12,
          padding: 20,
        }}
      >
        <div style={{ fontSize: 13, fontWeight: 600, color: '#94a3b8', textTransform: 'uppercase', letterSpacing: 0.6, marginBottom: 12 }}>
          Bước 3: Tùy chọn Thành phần Khôi phục
        </div>

        <div
          style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))',
            gap: 12,
            marginBottom: 18,
          }}
        >
          <label className="check-line" style={{ margin: 0 }}>
            <input
              type="checkbox"
              checked={migrateForm.import_websites}
              onChange={(e) => setMigrateForm((p) => ({ ...p, import_websites: e.target.checked }))}
            />
            <span>Khôi phục Mã nguồn Website, Nginx Vhost, SSL & DNS</span>
          </label>

          <label className="check-line" style={{ margin: 0 }}>
            <input
              type="checkbox"
              checked={migrateForm.import_databases}
              onChange={(e) => setMigrateForm((p) => ({ ...p, import_databases: e.target.checked }))}
            />
            <span>Khôi phục MariaDB Databases & Tự động cập nhật wp-config/.env</span>
          </label>

          <label className="check-line" style={{ margin: 0 }}>
            <input
              type="checkbox"
              checked={migrateForm.import_emails}
              onChange={(e) => setMigrateForm((p) => ({ ...p, import_emails: e.target.checked }))}
            />
            <span>Khôi phục Tài khoản Email, Forwarders & Hộp thư Maildir</span>
          </label>

          <label className="check-line" style={{ margin: 0 }}>
            <input
              type="checkbox"
              checked={migrateForm.force}
              onChange={(e) => setMigrateForm((p) => ({ ...p, force: e.target.checked }))}
            />
            <span style={{ color: migrateForm.force ? '#fbbf24' : undefined }}>
              Ghi đè nếu User / Website đã tồn tại trên SPanel (Force Overwrite)
            </span>
          </label>
        </div>

        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 12 }}>
          <div style={{ fontSize: 12.5, color: '#94a3b8' }}>
            {migrateForm.method === 'local'
              ? `Đang chọn ${selectedArchives.length} file backup trên máy chủ để khôi phục.`
              : `Phương thức: ${migrateForm.method.toUpperCase()} (${migrateForm.panel_type.toUpperCase()})`}
          </div>
          <button
            type="button"
            disabled={!!loading || (migrateJob && (migrateJob.status === 'running' || migrateJob.status === 'queued'))}
            onClick={startUserMigration}
            style={{ padding: '10px 22px', fontSize: 14, fontWeight: 600 }}
          >
            <Play size={15} /> Bắt đầu Chuyển dữ liệu ({migrateForm.panel_type.toUpperCase()})
          </button>
        </div>
      </div>
    </div>
  );
}
