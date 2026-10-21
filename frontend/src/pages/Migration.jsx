import React, { useState, useEffect, useRef } from 'react';
import {
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
  Link2,
  HardDrive,
  FileSpreadsheet,
  Eye,
  EyeOff,
} from 'lucide-react';
import { t } from '../i18n.js';

const PANEL_CARDS = [
  {
    id: 'cpanel',
    title: 'cPanel / WHM',
    port: '2087 / 2083',
    formats: 'cpmove-user.tar.gz, backup-*.tar.gz',
    desc: 'Khôi phục trọn bộ tài khoản cPanel: Main/Addon/Sub/Parked Domains, MySQL + Hash, Email + Maildir, SSL.',
    iconColor: '#ea580c',
    Icon: FileSpreadsheet,
  },
  {
    id: 'directadmin',
    title: 'DirectAdmin',
    port: '2222',
    formats: 'user.admin.*.tar.zst, .tar.gz',
    desc: 'Khôi phục full backup DirectAdmin: Users, Domains, Subdomains, Pointers, MariaDB, Email + Maildir, SSL.',
    iconColor: '#2563eb',
    Icon: Server,
  },
  {
    id: 'plesk',
    title: 'Plesk Obsidian',
    port: '8443',
    formats: 'backup_info_*.tar, .tar.gz, .zip',
    desc: 'Khôi phục backup từ Plesk: Subscriptions, vhosts/httpdocs, Subdomains, Databases và hộp thư Email.',
    iconColor: '#0891b2',
    Icon: Globe,
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
  const [showRemoteSecret, setShowRemoteSecret] = useState(false);
  const [showFtpPass, setShowFtpPass] = useState(false);
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
          <h2 style={{ margin: 0 }}>{t('Data Migration Center')}</h2>
          <p className="hint" style={{ marginTop: 4 }}>
            Chuyển dữ liệu tự động từ cPanel/WHM, DirectAdmin và Plesk sang SPanel (Websites, Databases, Emails, SSL & DNS).
          </p>
        </div>
      </div>

      {/* Live Progress / Active Job View */}
      {migrateJob && (
        <div
          className="pkg-card"
          style={{
            marginBottom: 22,
            border: '1px solid var(--border)',
            background: 'var(--surface)',
            borderRadius: 12,
            overflow: 'hidden',
          }}
        >
          <div
            className="pkg-card-header"
            style={{
              display: 'flex',
              justifyContent: 'space-between',
              alignItems: 'center',
              flexWrap: 'wrap',
              gap: 12,
              padding: '14px 18px',
              background: 'var(--surface-alt)',
              borderBottom: '1px solid var(--border)',
            }}
          >
            <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
              {migrateJob.status === 'running' || migrateJob.status === 'queued' ? (
                <RefreshCw size={18} className="spin" style={{ color: 'var(--primary)' }} />
              ) : migrateJob.status === 'failed' ? (
                <AlertTriangle size={18} style={{ color: '#dc2626' }} />
              ) : (
                <CheckCircle2 size={18} style={{ color: 'var(--primary)' }} />
              )}
              <div>
                <h3 style={{ margin: 0, fontSize: 15, color: 'var(--text)' }}>
                  Tiến trình Migration ({(migrateJob.panel_type || '').toUpperCase()}) —{' '}
                  {migrateJob.status === 'running' || migrateJob.status === 'queued'
                    ? 'Đang xử lý...'
                    : migrateJob.status === 'failed'
                    ? 'Thất bại'
                    : 'Hoàn tất'}
                </h3>
                <div style={{ fontSize: 12.5, color: 'var(--text-secondary)', marginTop: 2 }}>
                  {migrateJob.progress}
                </div>
              </div>
            </div>

            {(migrateJob.status === 'completed' ||
              migrateJob.status === 'completed_with_warnings' ||
              migrateJob.status === 'failed') && (
              <button
                type="button"
                className="secondary-light mini"
                onClick={() => setMigrateJob(null)}
              >
                Đóng bảng kết quả
              </button>
            )}
          </div>

          <div className="pkg-card-body" style={{ padding: 18 }}>
            {/* Progress Bar */}
            <div
              style={{
                height: 8,
                background: 'var(--surface-alt)',
                border: '1px solid var(--border)',
                borderRadius: 999,
                overflow: 'hidden',
                marginBottom: 14,
              }}
            >
              <div
                style={{
                  width: `${migrateJob.percent ?? (migrateJob.status === 'completed' ? 100 : 25)}%`,
                  height: '100%',
                  background:
                    migrateJob.status === 'failed'
                      ? '#dc2626'
                      : 'var(--primary)',
                  transition: 'width 0.4s ease',
                }}
              />
            </div>

            {/* Live Log Box */}
            {migrateJob.logs?.length > 0 && (
              <div
                style={{
                  background: 'var(--surface-alt)',
                  border: '1px solid var(--border)',
                  borderRadius: 8,
                  padding: '10px 14px',
                  maxHeight: 200,
                  overflowY: 'auto',
                  fontFamily: 'monospace',
                  fontSize: 12.5,
                  lineHeight: 1.6,
                  color: 'var(--text)',
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
                  background: 'rgba(239, 68, 68, 0.1)',
                  border: '1px solid rgba(239, 68, 68, 0.35)',
                  color: '#dc2626',
                  padding: '10px 14px',
                  borderRadius: 8,
                  fontSize: 13,
                  fontWeight: 600,
                  marginBottom: 14,
                }}
              >
                Chi tiết lỗi: {migrateJob.error}
              </div>
            )}

            {/* Imported Summary Cards */}
            {migrateJob.summary?.length > 0 && (
              <div style={{ display: 'grid', gap: 12, marginTop: 12 }}>
                {migrateJob.summary.map((item, idx) => (
                  <div
                    key={idx}
                    style={{
                      background: 'var(--surface-alt)',
                      border: '1px solid var(--border)',
                      borderRadius: 10,
                      padding: 14,
                    }}
                  >
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
                      <strong style={{ color: 'var(--text)', fontSize: 14 }}>
                        Tài khoản: <span style={{ color: 'var(--primary)' }}>{item.username}</span>{' '}
                        <span className="muted" style={{ fontWeight: 400, fontSize: 12 }}>({item.archive})</span>
                      </strong>
                    </div>
                    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))', gap: 10, fontSize: 13, color: 'var(--text)' }}>
                      <div>
                        <Globe size={13} style={{ marginRight: 5, verticalAlign: 'middle', color: '#2563eb' }} />
                        <strong>Domains ({item.imported_domains?.length || 0}):</strong>{' '}
                        <span style={{ color: 'var(--text-secondary)' }}>
                          {(item.imported_domains || []).join(', ') || 'Không có'}
                        </span>
                      </div>
                      <div>
                        <Database size={13} style={{ marginRight: 5, verticalAlign: 'middle', color: '#9333ea' }} />
                        <strong>Databases ({item.databases?.length || 0}):</strong>{' '}
                        <span style={{ color: 'var(--text-secondary)' }}>
                          {(item.databases || []).map((d) => d.db_name).join(', ') || 'Không có'}
                        </span>
                      </div>
                      <div>
                        <Mail size={13} style={{ marginRight: 5, verticalAlign: 'middle', color: 'var(--primary)' }} />
                        <strong>Emails ({item.mailboxes?.length || 0}):</strong>{' '}
                        <span style={{ color: 'var(--text-secondary)' }}>
                          {(item.mailboxes || []).join(', ') || 'Không có'}
                        </span>
                      </div>
                      <div>
                        <ShieldCheck size={13} style={{ marginRight: 5, verticalAlign: 'middle', color: '#d97706' }} />
                        <strong>SSL:</strong>{' '}
                        <span style={{ color: 'var(--text-secondary)' }}>
                          {[...(item.ssl_enabled_domains || []), ...(item.ssl_imported_domains || [])].join(', ') || 'Chưa kích hoạt'}
                        </span>
                      </div>
                    </div>
                    {item.warnings?.length > 0 && (
                      <div style={{ marginTop: 10, fontSize: 12, color: '#d97706', fontWeight: 500 }}>
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
                  background: 'var(--surface-alt)',
                  border: '1px solid var(--border)',
                  borderRadius: 10,
                  padding: 14,
                }}
              >
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8, flexWrap: 'wrap', gap: 8 }}>
                  <strong style={{ fontSize: 13, color: 'var(--primary)' }}>
                    Thông tin đăng nhập được khởi tạo (Panel / SFTP / Database / Email):
                  </strong>
                  <div style={{ display: 'flex', gap: 8 }}>
                    <button
                      type="button"
                      className="secondary-light mini"
                      onClick={() => copyCredentials(migrateJob.credentials)}
                    >
                      <Copy size={13} /> Sao chép
                    </button>
                    <button
                      type="button"
                      className="secondary-light mini"
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
                    color: 'var(--text)',
                    background: 'var(--surface)',
                    border: '1px solid var(--border)',
                    borderRadius: 6,
                    padding: 10,
                    overflowX: 'auto',
                    maxHeight: 160,
                  }}
                >
                  {migrateJob.credentials.join('\n')}
                </pre>
              </div>
            )}
          </div>
        </div>
      )}

      {/* STEP 1: Select Source Control Panel */}
      <div style={{ marginBottom: 20 }}>
        <label style={{ display: 'block', fontSize: 12.5, fontWeight: 700, color: 'var(--text-secondary)', textTransform: 'uppercase', letterSpacing: 0.5, marginBottom: 10 }}>
          1. Chọn Control Panel Nguồn
        </label>
        <div
          className="pkg-panel-cards"
          style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))',
            gap: 12,
          }}
        >
          {PANEL_CARDS.map((card) => {
            const active = migrateForm.panel_type === card.id;
            const CardIcon = card.Icon;
            return (
              <div
                key={card.id}
                className={`pkg-panel-card ${active ? 'active' : ''}`}
                onClick={() => handleSelectPanel(card.id)}
                style={{
                  cursor: 'pointer',
                  background: active
                    ? 'var(--primary-subtle, rgba(22, 163, 74, 0.08))'
                    : 'var(--surface)',
                  border: active
                    ? '2px solid var(--primary)'
                    : '1.5px solid var(--border)',
                  borderRadius: 12,
                  padding: '14px 16px',
                  transition: 'all 0.16s ease',
                  boxShadow: active ? '0 4px 14px rgba(22, 163, 74, 0.10)' : 'none',
                }}
              >
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 6 }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 15, fontWeight: 700, color: 'var(--text)' }}>
                    <CardIcon size={16} style={{ color: card.iconColor, flexShrink: 0 }} />
                    <span>{card.title}</span>
                  </div>
                  <span className={active ? 'badge ok' : 'badge'} style={{ fontSize: 11 }}>
                    Port {card.port}
                  </span>
                </div>
                <div style={{ fontSize: 12.5, color: 'var(--text-secondary)', lineHeight: 1.45, marginBottom: 8 }}>
                  {card.desc}
                </div>
                <div style={{ fontSize: 11.5, color: 'var(--text-muted, var(--text-secondary))', fontFamily: 'monospace' }}>
                  Định dạng: {card.formats}
                </div>
              </div>
            );
          })}
        </div>
      </div>

      {/* STEP 2: Transfer Method Card */}
      <div
        className="pkg-card"
        style={{
          marginBottom: 20,
          background: 'var(--surface)',
          border: '1px solid var(--border)',
          borderRadius: 12,
          overflow: 'hidden',
        }}
      >
        <div
          className="pkg-card-header"
          style={{
            padding: '12px 18px',
            background: 'var(--surface-alt)',
            borderBottom: '1px solid var(--border)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            flexWrap: 'wrap',
            gap: 10,
          }}
        >
          <h4 className="pkg-card-title" style={{ margin: 0, fontSize: 13.5, fontWeight: 700, color: 'var(--text)' }}>
            2. Chọn Phương thức Lấy File Backup ({migrateForm.panel_type.toUpperCase()})
          </h4>

          {/* Method Tabs */}
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
            <button
              type="button"
              className={migrateForm.method === 'local' ? 'mini' : 'secondary-light mini'}
              onClick={() => handleSelectMethod('local')}
            >
              <FolderArchive size={13} /> File trên Server / Upload
            </button>
            <button
              type="button"
              className={migrateForm.method === 'sftp' ? 'mini' : 'secondary-light mini'}
              onClick={() => handleSelectMethod('sftp')}
            >
              <Server size={13} /> Kết nối VPS Cũ (API & SFTP)
            </button>
            <button
              type="button"
              className={migrateForm.method === 'url' ? 'mini' : 'secondary-light mini'}
              onClick={() => handleSelectMethod('url')}
            >
              <Link2 size={13} /> Tải qua URL (wget)
            </button>
            <button
              type="button"
              className={migrateForm.method === 'ftp' ? 'mini' : 'secondary-light mini'}
              onClick={() => handleSelectMethod('ftp')}
            >
              <HardDrive size={13} /> Remote FTP
            </button>
          </div>
        </div>

        <div className="pkg-card-body" style={{ padding: 18 }}>
          {/* TAB 1: Local Server Backups & Upload */}
          {migrateForm.method === 'local' && (
            <div>
              <div
                style={{
                  display: 'flex',
                  justifyContent: 'space-between',
                  alignItems: 'center',
                  flexWrap: 'wrap',
                  gap: 12,
                  padding: '12px 16px',
                  background: 'var(--surface-alt)',
                  border: '1px dashed var(--border)',
                  borderRadius: 10,
                  marginBottom: 14,
                }}
              >
                <div>
                  <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--text)' }}>
                    Thư mục lưu trữ backup trên máy chủ:{' '}
                    <code style={{ color: 'var(--primary)', fontWeight: 700 }}>{backupDir}</code>
                  </div>
                  <div style={{ fontSize: 12, color: 'var(--text-secondary)', marginTop: 3 }}>
                    Tải file backup (<code>.tar.gz</code>, <code>.tgz</code>, <code>.tar.zst</code>, <code>.zip</code>) trực tiếp từ máy tính hoặc tải lên thư mục trên qua SFTP rồi bấm <strong>Làm mới</strong>.
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
                  <button type="button" className="secondary-light" onClick={loadServerBackups}>
                    <RefreshCw size={14} /> Làm mới
                  </button>
                </div>
              </div>

              {backups.length === 0 ? (
                <div
                  style={{
                    textAlign: 'center',
                    padding: '28px 16px',
                    color: 'var(--text-secondary)',
                    fontSize: 13,
                    background: 'var(--surface-alt)',
                    borderRadius: 10,
                    border: '1px solid var(--border)',
                  }}
                >
                  Chưa có file backup nào trong thư mục <code>{backupDir}</code>. Hãy bấm <strong>Upload File Backup</strong> hoặc chuyển sang tab <strong>Kết nối VPS Cũ (API & SFTP)</strong>.
                </div>
              ) : (
                <div
                  className="pkg-list-card"
                  style={{ maxHeight: 320, overflowY: 'auto', border: '1px solid var(--border)', borderRadius: 10 }}
                >
                  <table className="pkg-clean-table">
                    <thead>
                      <tr>
                        <th style={{ width: 38 }}>
                          <input
                            type="checkbox"
                            checked={backups.length > 0 && selectedArchives.length === backups.length}
                            onChange={(e) =>
                              setSelectedArchives(e.target.checked ? backups.map((b) => b.path) : [])
                            }
                            style={{ width: 15, height: 15, margin: 0 }}
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
                          <tr
                            key={b.path}
                            style={
                              checked
                                ? { background: 'var(--primary-subtle, rgba(22, 163, 74, 0.06))' }
                                : undefined
                            }
                          >
                            <td>
                              <input
                                type="checkbox"
                                checked={checked}
                                onChange={() => toggleArchiveSelection(b.path)}
                                style={{ width: 15, height: 15, margin: 0 }}
                              />
                            </td>
                            <td style={{ fontFamily: 'monospace', fontWeight: 600, color: 'var(--text)' }}>
                              {b.filename}
                            </td>
                            <td>
                              <span className="badge ok" style={{ fontSize: 11, textTransform: 'uppercase' }}>
                                {b.detected_panel || migrateForm.panel_type}
                              </span>
                            </td>
                            <td>{formatBytes(b.size)}</td>
                            <td style={{ textAlign: 'right' }}>
                              <div style={{ display: 'inline-flex', gap: 6 }}>
                                <button
                                  type="button"
                                  className="secondary-light mini"
                                  disabled={scanningPath === b.path}
                                  onClick={() => handleScanArchive(b.path, b.detected_panel)}
                                >
                                  <Search size={13} />{' '}
                                  {scanningPath === b.path ? 'Đang quét...' : 'Xem trước (Scan)'}
                                </button>
                                <button
                                  type="button"
                                  className="danger mini"
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
                    background: 'var(--surface-alt)',
                    border: '1px solid var(--border)',
                    borderRadius: 10,
                    padding: 16,
                  }}
                >
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 10 }}>
                    <strong style={{ color: 'var(--primary)', fontSize: 13.5 }}>
                      Kết quả kiểm tra trước khi Import: <code>{scanResult.filename}</code> ({formatBytes(scanResult.size)})
                    </strong>
                    <button
                      type="button"
                      className="secondary-light mini"
                      onClick={() => setScanResult(null)}
                    >
                      Đóng
                    </button>
                  </div>

                  {(scanResult.existing_users?.length > 0 || scanResult.existing_domains?.length > 0) && (
                    <div
                      style={{
                        background: 'rgba(245, 158, 11, 0.12)',
                        border: '1px solid rgba(245, 158, 11, 0.4)',
                        color: '#d97706',
                        padding: '8px 12px',
                        borderRadius: 8,
                        fontSize: 12.5,
                        fontWeight: 500,
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
                      <div style={{ marginBottom: 8, color: 'var(--text)' }}>
                        <strong>User:</strong> <code style={{ color: 'var(--primary)', fontWeight: 700 }}>{u.username}</code>{' '}
                        {u.email && <span className="muted">({u.email})</span>} —{' '}
                        <strong>{u.domains?.length || 0}</strong> Website,{' '}
                        <strong>
                          {(u.databases?.length || 0) + (u.domains || []).filter((d) => d.db_name).length}
                        </strong>{' '}
                        Database, <strong>{u.mailboxes?.length || 0}</strong> Hộp thư Email
                      </div>
                      {u.domains?.length > 0 && (
                        <div className="pkg-list-card" style={{ border: '1px solid var(--border)', borderRadius: 8 }}>
                          <table className="pkg-clean-table">
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
                                  <td>
                                    <span className="badge" style={{ fontSize: 11, textTransform: 'uppercase' }}>
                                      {d.app_type}
                                    </span>
                                  </td>
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
            <div style={{ display: 'grid', gap: 16 }}>
              {/* Sub-box A: Remote Panel API Account Discovery */}
              <div
                style={{
                  background: 'var(--surface-alt)',
                  border: '1px solid var(--border)',
                  borderRadius: 10,
                  padding: '14px 16px',
                }}
              >
                <div style={{ fontWeight: 700, fontSize: 13.5, color: 'var(--text)', marginBottom: 4 }}>
                  2A. Kết nối API máy chủ {migrateForm.panel_type.toUpperCase()} để lấy danh sách tài khoản (Tùy chọn)
                </div>
                <div style={{ fontSize: 12.5, color: 'var(--text-secondary)', marginBottom: 12 }}>
                  Kiểm tra danh sách User/Domain đang hoạt động trên VPS cũ và kích hoạt lệnh nén Full Backup từ xa.
                </div>

                <div
                  style={{
                    display: 'grid',
                    gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))',
                    gap: '12px 14px',
                    alignItems: 'end',
                  }}
                >
                  <div>
                    <label style={{ fontSize: 12.5, fontWeight: 600, display: 'block', marginBottom: 5, color: 'var(--text)' }}>
                      Panel URL / IP
                    </label>
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
                      style={{ margin: 0, height: 38, width: '100%' }}
                    />
                  </div>
                  <div>
                    <label style={{ fontSize: 12.5, fontWeight: 600, display: 'block', marginBottom: 5, color: 'var(--text)' }}>
                      Admin / Reseller Username
                    </label>
                    <input
                      type="text"
                      value={remoteForm.username}
                      onChange={(e) => setRemoteForm((p) => ({ ...p, username: e.target.value }))}
                      placeholder="root / admin"
                      style={{ margin: 0, height: 38, width: '100%' }}
                    />
                  </div>
                  <div>
                    <label style={{ fontSize: 12.5, fontWeight: 600, display: 'block', marginBottom: 5, color: 'var(--text)' }}>
                      Kiểu xác thực
                    </label>
                    <select
                      value={remoteForm.auth_type}
                      onChange={(e) => setRemoteForm((p) => ({ ...p, auth_type: e.target.value }))}
                      style={{ margin: 0, height: 38, width: '100%' }}
                    >
                      <option value="token">API Token / Login Key</option>
                      <option value="password">Mật khẩu (Password)</option>
                    </select>
                  </div>
                  <div>
                    <label style={{ fontSize: 12.5, fontWeight: 600, display: 'block', marginBottom: 5, color: 'var(--text)' }}>
                      {remoteForm.auth_type === 'token' ? 'API Token / Login Key' : 'Password'}
                    </label>
                    <div style={{ position: 'relative', display: 'flex', alignItems: 'center' }}>
                      <input
                        type={showRemoteSecret ? 'text' : 'password'}
                        value={remoteForm.secret}
                        onChange={(e) => setRemoteForm((p) => ({ ...p, secret: e.target.value }))}
                        placeholder="••••••••••••"
                        style={{ margin: 0, height: 38, width: '100%', paddingRight: 38 }}
                      />
                      <button
                        type="button"
                        onClick={() => setShowRemoteSecret((v) => !v)}
                        style={{
                          position: 'absolute',
                          right: 6,
                          background: 'transparent',
                          border: 'none',
                          padding: '4px 6px',
                          minHeight: 'auto',
                          height: 28,
                          color: 'var(--text-secondary)',
                          boxShadow: 'none',
                        }}
                      >
                        {showRemoteSecret ? <EyeOff size={15} /> : <Eye size={15} />}
                      </button>
                    </div>
                  </div>
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
                      className="secondary-light"
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
                  <div
                    className="pkg-list-card"
                    style={{ maxHeight: 240, overflowY: 'auto', marginTop: 12, border: '1px solid var(--border)', borderRadius: 8 }}
                  >
                    <table className="pkg-clean-table">
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
                              style={{ width: 15, height: 15, margin: 0 }}
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
                                style={{ width: 15, height: 15, margin: 0 }}
                              />
                            </td>
                            <td style={{ fontWeight: 600, fontFamily: 'monospace' }}>{a.username}</td>
                            <td>{a.domain || '—'}</td>
                            <td>{a.package || '—'}</td>
                            <td>{a.disk_used || '—'}</td>
                            <td>
                              <span className={a.suspended ? 'badge warn' : 'badge ok'} style={{ fontSize: 11 }}>
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
                  background: 'var(--surface-alt)',
                  border: '1px solid var(--border)',
                  borderRadius: 10,
                  padding: '14px 16px',
                }}
              >
                <div style={{ fontWeight: 700, fontSize: 13.5, color: 'var(--text)', marginBottom: 4 }}>
                  2B. Thông tin SFTP (SSH) để kéo file Backup trực tiếp về SPanel
                </div>
                <div style={{ fontSize: 12.5, color: 'var(--text-secondary)', marginBottom: 12 }}>
                  Nhập đường dẫn file cụ thể (VD: <code>/home/cpmove-user.tar.gz</code>) hoặc thư mục chứa các bản sao lưu (VD: <code>/backup</code> hoặc <code>/home/admin/admin_backups</code>).
                </div>

                <div
                  style={{
                    display: 'grid',
                    gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))',
                    gap: '12px 14px',
                  }}
                >
                  <div>
                    <label style={{ fontSize: 12.5, fontWeight: 600, display: 'block', marginBottom: 5, color: 'var(--text)' }}>
                      SFTP Host / IP
                    </label>
                    <input
                      type="text"
                      value={migrateForm.ftp_host}
                      onChange={(e) => setMigrateForm((p) => ({ ...p, ftp_host: e.target.value }))}
                      placeholder="103.x.x.x"
                      style={{ margin: 0, height: 38, width: '100%' }}
                    />
                  </div>
                  <div>
                    <label style={{ fontSize: 12.5, fontWeight: 600, display: 'block', marginBottom: 5, color: 'var(--text)' }}>
                      SSH / SFTP Port
                    </label>
                    <input
                      type="number"
                      value={migrateForm.ftp_port}
                      onChange={(e) =>
                        setMigrateForm((p) => ({ ...p, ftp_port: parseInt(e.target.value || '22', 10) }))
                      }
                      style={{ margin: 0, height: 38, width: '100%' }}
                    />
                  </div>
                  <div>
                    <label style={{ fontSize: 12.5, fontWeight: 600, display: 'block', marginBottom: 5, color: 'var(--text)' }}>
                      SSH Username
                    </label>
                    <input
                      type="text"
                      value={migrateForm.ftp_user}
                      onChange={(e) => setMigrateForm((p) => ({ ...p, ftp_user: e.target.value }))}
                      placeholder="root"
                      style={{ margin: 0, height: 38, width: '100%' }}
                    />
                  </div>
                  <div>
                    <label style={{ fontSize: 12.5, fontWeight: 600, display: 'block', marginBottom: 5, color: 'var(--text)' }}>
                      SSH Password
                    </label>
                    <div style={{ position: 'relative', display: 'flex', alignItems: 'center' }}>
                      <input
                        type={showFtpPass ? 'text' : 'password'}
                        value={migrateForm.ftp_pass}
                        onChange={(e) => setMigrateForm((p) => ({ ...p, ftp_pass: e.target.value }))}
                        placeholder="••••••••••••"
                        style={{ margin: 0, height: 38, width: '100%', paddingRight: 38 }}
                      />
                      <button
                        type="button"
                        onClick={() => setShowFtpPass((v) => !v)}
                        style={{
                          position: 'absolute',
                          right: 6,
                          background: 'transparent',
                          border: 'none',
                          padding: '4px 6px',
                          minHeight: 'auto',
                          height: 28,
                          color: 'var(--text-secondary)',
                          boxShadow: 'none',
                        }}
                      >
                        {showFtpPass ? <EyeOff size={15} /> : <Eye size={15} />}
                      </button>
                    </div>
                  </div>
                  <div style={{ gridColumn: '1 / -1' }}>
                    <label style={{ fontSize: 12.5, fontWeight: 600, display: 'block', marginBottom: 5, color: 'var(--text)' }}>
                      Đường dẫn File hoặc Thư mục Backup trên VPS cũ
                    </label>
                    <input
                      type="text"
                      value={migrateForm.ftp_path}
                      onChange={(e) => setMigrateForm((p) => ({ ...p, ftp_path: e.target.value }))}
                      placeholder="/backup/cpmove-user.tar.gz hoặc /home/admin/admin_backups"
                      style={{ margin: 0, height: 38, width: '100%' }}
                    />
                  </div>
                </div>
              </div>
            </div>
          )}

          {/* TAB 3: Direct Download URL (wget) */}
          {migrateForm.method === 'url' && (
            <div
              style={{
                background: 'var(--surface-alt)',
                border: '1px solid var(--border)',
                borderRadius: 10,
                padding: '14px 16px',
              }}
            >
              <label style={{ fontSize: 12.5, fontWeight: 600, display: 'block', marginBottom: 6, color: 'var(--text)' }}>
                Đường dẫn tải trực tiếp File Backup (HTTP / HTTPS)
              </label>
              <input
                type="text"
                value={migrateForm.source_url}
                onChange={(e) => setMigrateForm((p) => ({ ...p, source_url: e.target.value }))}
                placeholder="https://old-server.example.com/cpmove-username.tar.gz"
                style={{ margin: 0, height: 38, width: '100%' }}
              />
              <div className="hint" style={{ marginTop: 6 }}>
                SPanel sẽ tải trực tiếp gói sao lưu từ URL này về máy chủ và tự động giải nén, khôi phục theo chuẩn {migrateForm.panel_type.toUpperCase()}.
              </div>
            </div>
          )}

          {/* TAB 4: Remote FTP */}
          {migrateForm.method === 'ftp' && (
            <div
              style={{
                background: 'var(--surface-alt)',
                border: '1px solid var(--border)',
                borderRadius: 10,
                padding: '14px 16px',
                display: 'grid',
                gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))',
                gap: '12px 14px',
              }}
            >
              <div>
                <label style={{ fontSize: 12.5, fontWeight: 600, display: 'block', marginBottom: 5, color: 'var(--text)' }}>
                  FTP Host / IP
                </label>
                <input
                  type="text"
                  value={migrateForm.ftp_host}
                  onChange={(e) => setMigrateForm((p) => ({ ...p, ftp_host: e.target.value }))}
                  placeholder="103.x.x.x"
                  style={{ margin: 0, height: 38, width: '100%' }}
                />
              </div>
              <div>
                <label style={{ fontSize: 12.5, fontWeight: 600, display: 'block', marginBottom: 5, color: 'var(--text)' }}>
                  FTP Port
                </label>
                <input
                  type="number"
                  value={migrateForm.ftp_port}
                  onChange={(e) =>
                    setMigrateForm((p) => ({ ...p, ftp_port: parseInt(e.target.value || '21', 10) }))
                  }
                  style={{ margin: 0, height: 38, width: '100%' }}
                />
              </div>
              <div>
                <label style={{ fontSize: 12.5, fontWeight: 600, display: 'block', marginBottom: 5, color: 'var(--text)' }}>
                  FTP Username
                </label>
                <input
                  type="text"
                  value={migrateForm.ftp_user}
                  onChange={(e) => setMigrateForm((p) => ({ ...p, ftp_user: e.target.value }))}
                  placeholder="ftpuser"
                  style={{ margin: 0, height: 38, width: '100%' }}
                />
              </div>
              <div>
                <label style={{ fontSize: 12.5, fontWeight: 600, display: 'block', marginBottom: 5, color: 'var(--text)' }}>
                  FTP Password
                </label>
                <input
                  type="password"
                  value={migrateForm.ftp_pass}
                  onChange={(e) => setMigrateForm((p) => ({ ...p, ftp_pass: e.target.value }))}
                  placeholder="••••••••••••"
                  style={{ margin: 0, height: 38, width: '100%' }}
                />
              </div>
              <div style={{ gridColumn: '1 / -1' }}>
                <label style={{ fontSize: 12.5, fontWeight: 600, display: 'block', marginBottom: 5, color: 'var(--text)' }}>
                  Đường dẫn File Backup trên FTP
                </label>
                <input
                  type="text"
                  value={migrateForm.ftp_path}
                  onChange={(e) => setMigrateForm((p) => ({ ...p, ftp_path: e.target.value }))}
                  placeholder="/backups/cpmove-user.tar.gz"
                  style={{ margin: 0, height: 38, width: '100%' }}
                />
              </div>
            </div>
          )}
        </div>
      </div>

      {/* STEP 3: Restore Options & Start Button */}
      <div
        className="pkg-card"
        style={{
          background: 'var(--surface)',
          border: '1px solid var(--border)',
          borderRadius: 12,
          overflow: 'hidden',
        }}
      >
        <div
          className="pkg-card-header"
          style={{
            padding: '12px 18px',
            background: 'var(--surface-alt)',
            borderBottom: '1px solid var(--border)',
          }}
        >
          <h4 className="pkg-card-title" style={{ margin: 0, fontSize: 13.5, fontWeight: 700, color: 'var(--text)' }}>
            3. Tùy chọn Thành phần Khôi phục
          </h4>
        </div>

        <div className="pkg-card-body" style={{ padding: 18 }}>
          <div
            className="pkg-feature-grid"
            style={{
              display: 'grid',
              gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))',
              gap: 10,
              marginBottom: 16,
              padding: 0,
            }}
          >
            <label className="pkg-feature-check" style={{ margin: 0 }}>
              <input
                type="checkbox"
                checked={migrateForm.import_websites}
                onChange={(e) => setMigrateForm((p) => ({ ...p, import_websites: e.target.checked }))}
              />
              <span>Khôi phục Mã nguồn Website, Nginx Vhost, SSL & DNS</span>
            </label>

            <label className="pkg-feature-check" style={{ margin: 0 }}>
              <input
                type="checkbox"
                checked={migrateForm.import_databases}
                onChange={(e) => setMigrateForm((p) => ({ ...p, import_databases: e.target.checked }))}
              />
              <span>Khôi phục MariaDB Databases & Tự động cập nhật wp-config/.env</span>
            </label>

            <label className="pkg-feature-check" style={{ margin: 0 }}>
              <input
                type="checkbox"
                checked={migrateForm.import_emails}
                onChange={(e) => setMigrateForm((p) => ({ ...p, import_emails: e.target.checked }))}
              />
              <span>Khôi phục Tài khoản Email, Forwarders & Hộp thư Maildir</span>
            </label>

            <label className="pkg-feature-check" style={{ margin: 0 }}>
              <input
                type="checkbox"
                checked={migrateForm.force}
                onChange={(e) => setMigrateForm((p) => ({ ...p, force: e.target.checked }))}
              />
              <span>Ghi đè nếu User / Website đã tồn tại trên SPanel (Force Overwrite)</span>
            </label>
          </div>

          <div
            style={{
              display: 'flex',
              justifyContent: 'space-between',
              alignItems: 'center',
              flexWrap: 'wrap',
              gap: 12,
              paddingTop: 14,
              borderTop: '1px solid var(--border)',
            }}
          >
            <div style={{ fontSize: 12.5, color: 'var(--text-secondary)' }}>
              {migrateForm.method === 'local'
                ? `Đang chọn ${selectedArchives.length} file backup trên máy chủ để khôi phục.`
                : `Phương thức: ${migrateForm.method.toUpperCase()} (${migrateForm.panel_type.toUpperCase()})`}
            </div>
            <button
              type="button"
              disabled={!!loading || (migrateJob && (migrateJob.status === 'running' || migrateJob.status === 'queued'))}
              onClick={startUserMigration}
              style={{ padding: '10px 20px', fontSize: 13.5, fontWeight: 600 }}
            >
              <Play size={14} /> Bắt đầu Chuyển dữ liệu ({migrateForm.panel_type.toUpperCase()})
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
