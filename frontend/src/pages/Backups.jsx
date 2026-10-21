import React, { useState, useEffect } from 'react';
import {
  Archive, ArchiveRestore, Check, Clock, Cloud, Copy, Database, Download,
  FolderOpen, Globe, HardDrive, KeyRound, Pause, Pencil, Play, Plus,
  RefreshCw, RotateCcw, Search, Server, ShieldCheck, Trash2, Upload,
  Users, AlertCircle, CheckCircle, X
} from 'lucide-react';
import { EmptyState } from '../components/EmptyState.jsx';
import { useAppContext } from '../contexts/AppContext.jsx';
import { t } from '../i18n.js';
import { WebsiteSelect } from '../components/WebsiteSelect.jsx';
import { formatBytes, BACKUP_SCHEDULE_PRESETS, isPresetSchedule } from '../utils/helpers.js';

const S3_PRESETS = [
  { id: 'aws', label: 'Amazon S3', endpoint: 's3.amazonaws.com', region: 'us-east-1' },
  { id: 'r2', label: 'Cloudflare R2', endpoint: '<account_id>.r2.cloudflarestorage.com', region: 'auto' },
  { id: 'wasabi', label: 'Wasabi', endpoint: 's3.wasabisys.com', region: 'us-east-1' },
  { id: 'b2', label: 'Backblaze B2', endpoint: 's3.us-west-004.backblazeb2.com', region: 'us-west-004' },
  { id: 'spaces', label: 'DigitalOcean Spaces', endpoint: 'nyc3.digitaloceanspaces.com', region: 'nyc3' },
  { id: 'minio', label: 'MinIO / Custom S3', endpoint: '', region: 'us-east-1' },
];

export function BackupsPage({ request, isAdmin, isReseller, API }) {
  const {
    loading, setLoading, setError, setNotice, users, selectedWebsiteId, setSelectedWebsiteId, websites,
    loadUsers, loadDatabases, refreshAll,
  } = useAppContext();

  const isManager = Boolean(isAdmin || isReseller);
  const [backupTab, setBackupTab] = useState(isManager ? 'user' : 'website');
  const [serverHostname, setServerHostname] = useState(window.location.hostname || 'spanel-server');

  // Restore Wizard state (Admin)
  const [restoreSource, setRestoreSource] = useState({ kind: 'local', target_id: '' });
  const [restoreRemote, setRestoreRemote] = useState({
    protocol: 'sftp', host: '', port: '22', username: 'root', password: '', path: '/var/backups/spanel/users',
  });
  const [restoreListing, setRestoreListing] = useState(null);
  const [restoreChoice, setRestoreChoice] = useState({});
  const [restorePicks, setRestorePicks] = useState([]);
  const [restoreFilter, setRestoreFilter] = useState('');
  const [restoreJob, setRestoreJob] = useState(null);
  const [restoreBusy, setRestoreBusy] = useState(false);

  // Backups & Jobs state
  const [backups, setBackups] = useState([]);
  const [backupJobs, setBackupJobs] = useState([]);
  const [userBackups, setUserBackups] = useState([]);
  const [userBackupFilter, setUserBackupFilter] = useState('');
  const [backupSchedules, setBackupSchedules] = useState([]);
  const [sftpTargets, setSftpTargets] = useState([]);
  const [selectedBackupUserId, setSelectedBackupUserId] = useState('');
  const [selectedBackupTargetId, setSelectedBackupTargetId] = useState('');
  const [selectedUserBackupTargetId, setSelectedUserBackupTargetId] = useState('');
  const [lastRestoredUserCredentials, setLastRestoredUserCredentials] = useState(null);

  // Schedule Builder / Editor state
  const [editingScheduleId, setEditingScheduleId] = useState(null);
  const [scheduleForm, setScheduleForm] = useState({
    all_users: true,
    user_ids: [],
    target_id: '',
    schedule: '0 2 * * *',
    name_suffix: 'full_date',
    retention: 7,
    is_active: true,
  });

  // Destination (SFTP / S3) Form state
  const [showDestinationForm, setShowDestinationForm] = useState(false);
  const [sftpTargetForm, setSftpTargetForm] = useState({
    name: '', kind: 'sftp',
    host: '', port: 22, username: '', password: '', private_key: '', remote_path: '/backups/spanel',
    endpoint: '', region: 'us-east-1', bucket: '', access_key: '', secret_key: '', prefix: '', secure: true,
  });

  const backupUsers = users.filter((item) => item.is_active);
  const hasActiveBackupJobs = backupJobs.some((job) => job.status === 'queued' || job.status === 'running');

  useEffect(() => {
    if (selectedWebsiteId) {
      loadBackups(selectedWebsiteId);
    }
    loadBackupJobs({ silent: true });
    loadUserBackups();
    loadSftpTargets();
    if (isAdmin) {
      loadBackupSchedules();
    }
  }, [selectedWebsiteId, isAdmin]);

  useEffect(() => {
    if (!selectedBackupUserId && users.length > 0) {
      const preferred = users.find((item) => item.role !== 'admin') || users[0];
      if (preferred) setSelectedBackupUserId(String(preferred.id));
    }
  }, [users, selectedBackupUserId]);

  useEffect(() => {
    if (!hasActiveBackupJobs) return undefined;
    const timer = window.setInterval(() => {
      loadBackupJobs({ silent: true });
    }, 2500);
    return () => window.clearInterval(timer);
  }, [hasActiveBackupJobs, selectedWebsiteId, isAdmin]);

  // Poll Restore Job via /maintenance/backup-jobs/{job_id}
  useEffect(() => {
    if (!restoreJob || ['done', 'error', 'completed', 'failed'].includes(restoreJob.status)) return undefined;
    const timer = window.setInterval(async () => {
      try {
        const data = await request(`/maintenance/backup-jobs/${restoreJob.job_id}`);
        setRestoreJob(data);
        if (['done', 'error', 'completed', 'failed'].includes(data.status)) {
          loadUserBackups();
          if (loadUsers) loadUsers();
          if (refreshAll) refreshAll();
        }
      } catch (_err) {
        // Ignore transient network errors while polling
      }
    }, 2000);
    return () => window.clearInterval(timer);
  }, [restoreJob]);

  async function loadBackups(websiteId = selectedWebsiteId) {
    if (!websiteId) {
      setBackups([]);
      return;
    }
    try {
      const data = await request(`/maintenance/backups/${websiteId}`);
      setBackups(data.items || []);
      if (data.server_hostname) setServerHostname(data.server_hostname);
    } catch (_err) {
      setBackups([]);
    }
  }

  async function loadUserBackups() {
    try {
      const data = await request('/maintenance/user-backups');
      setUserBackups(data.items || []);
      if (data.server_hostname) setServerHostname(data.server_hostname);
    } catch (_err) {
      setUserBackups([]);
    }
  }

  async function loadBackupSchedules() {
    if (!isAdmin) {
      setBackupSchedules([]);
      return;
    }
    try {
      const data = await request('/maintenance/backup-schedules');
      setBackupSchedules(data || []);
    } catch (_err) {
      setBackupSchedules([]);
    }
  }

  async function loadSftpTargets() {
    try {
      const data = await request('/maintenance/sftp-targets');
      setSftpTargets(data || []);
      if (data?.length && !selectedBackupTargetId) {
        setSelectedBackupTargetId(String(data[0].id));
      }
    } catch (_err) {
      setSftpTargets([]);
    }
  }

  async function loadBackupJobs({ silent = false } = {}) {
    try {
      const data = await request('/maintenance/backup-jobs');
      const nextJobs = data.jobs || [];
      setBackupJobs((prevJobs) => {
        const prevMap = new Map(prevJobs.map((job) => [job.job_id, job]));
        const completedNow = nextJobs.filter((job) => {
          const prev = prevMap.get(job.job_id);
          return prev && (prev.status === 'queued' || prev.status === 'running') && (job.status === 'done' || job.status === 'error');
        });
        if (completedNow.length > 0) {
          for (const finished of completedNow) {
            if (finished.status === 'done') {
              setNotice(finished.message || t('backupCompleted'));
            } else if (finished.error) {
              setError(finished.error);
            }
          }
          if (selectedWebsiteId) loadBackups(selectedWebsiteId);
          loadUserBackups();
          if (isAdmin) loadBackupSchedules();
        }
        return nextJobs;
      });
    } catch (err) {
      if (!silent) setError(err.message);
    }
  }

  function queueBackupJob(job, noticeText) {
    if (job?.job_id) {
      setBackupJobs((current) => [job, ...current.filter((item) => item.job_id !== job.job_id)].slice(0, 20));
    }
    setNotice(noticeText);
  }

  async function createBackup() {
    if (!selectedWebsiteId) return;
    setLoading(true);
    setError('');
    try {
      const data = await request('/maintenance/backup', {
        method: 'POST',
        body: JSON.stringify({ website_id: Number(selectedWebsiteId) }),
      });
      queueBackupJob(data, t('websiteBackupRunningInBackground'));
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  async function createUserBackup() {
    const targetUserId = isManager ? Number(selectedBackupUserId) : Number(users[0]?.id || 0);
    if (!targetUserId) {
      setError(t('selectUserForBackup'));
      return;
    }
    setLoading(true);
    setError('');
    try {
      const data = await request('/maintenance/user-backups', {
        method: 'POST',
        body: JSON.stringify({
          user_id: targetUserId,
          target_id: selectedUserBackupTargetId ? Number(selectedUserBackupTargetId) : null,
        }),
      });
      queueBackupJob(
        data,
        selectedUserBackupTargetId ? t('fullUserBackupSftpRunningInBackground') : t('fullUserBackupRunningInBackground')
      );
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  async function restoreUserBackupItem(item) {
    if (!item?.backup_file) return;
    const label = item.username ? `${item.username} (${item.filename})` : item.filename;
    if (!window.confirm(t('confirmRestoreUserBackup', { name: label }))) return;
    setLoading(true);
    setError('');
    setLastRestoredUserCredentials(null);
    try {
      const data = await request('/maintenance/user-backups/restore', {
        method: 'POST',
        body: JSON.stringify({ backup_file: item.backup_file }),
      });
      await Promise.all([
        refreshAll ? refreshAll() : Promise.resolve(),
        loadUserBackups(),
        selectedWebsiteId ? loadDatabases(selectedWebsiteId) : Promise.resolve(),
      ]);
      if (data.generated_password) {
        setLastRestoredUserCredentials({
          username: data.username,
          password: data.generated_password,
        });
      }
      setNotice(t('restoredUserBackupSummary', {
        username: data.username,
        count: data.websites?.length || 0,
      }));
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  async function deleteUserBackupItem(item) {
    if (!item?.backup_file) return;
    if (!window.confirm(t('confirmDeleteBackup', { name: item.filename }))) return;
    setLoading(true);
    setError('');
    try {
      await request(`/maintenance/user-backups?backup_file=${encodeURIComponent(item.backup_file)}`, {
        method: 'DELETE',
      });
      await loadUserBackups();
      setNotice(t('deletedBackupFile', { file: item.filename }));
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  function downloadUserBackup(item) {
    if (!item?.backup_file) return;
    const url = `${API}/maintenance/user-backups/download?backup_file=${encodeURIComponent(item.backup_file)}`;
    const link = document.createElement('a');
    link.href = url;
    link.download = item.filename || 'user-backup.tar.gz';
    document.body.appendChild(link);
    link.click();
    link.remove();
  }

  function downloadBackup(backupFile) {
    if (!selectedWebsiteId || !backupFile) return;
    const filename = backupFile.split('/').pop() || 'website-backup.tar.gz';
    const url = `${API}/maintenance/backups/${selectedWebsiteId}/download?backup_file=${encodeURIComponent(backupFile)}`;
    const link = document.createElement('a');
    link.href = url;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    link.remove();
  }

  function toggleScheduleUser(userId) {
    const normalizedId = Number(userId);
    setScheduleForm((prev) => {
      const exists = prev.user_ids.includes(normalizedId);
      const userIds = exists
        ? prev.user_ids.filter((id) => id !== normalizedId)
        : [...prev.user_ids, normalizedId];
      return { ...prev, all_users: false, user_ids: userIds };
    });
  }

  function startEditSchedule(item) {
    let parsedIds = [];
    try {
      parsedIds = JSON.parse(item.user_ids || '[]');
    } catch (_err) {
      parsedIds = item.user_id ? [item.user_id] : [];
    }
    setEditingScheduleId(item.id);
    setScheduleForm({
      all_users: Boolean(item.all_users),
      user_ids: Array.isArray(parsedIds) ? parsedIds.map(Number) : [],
      target_id: item.target_id ? String(item.target_id) : '',
      schedule: item.schedule || '0 2 * * *',
      name_suffix: item.name_suffix || 'full_date',
      retention: Number(item.retention || 7),
      is_active: Boolean(item.is_active),
    });
  }

  function cancelEditSchedule() {
    setEditingScheduleId(null);
    setScheduleForm({
      all_users: true,
      user_ids: [],
      target_id: '',
      schedule: '0 2 * * *',
      name_suffix: 'full_date',
      retention: 7,
      is_active: true,
    });
  }

  async function saveBackupSchedule(event) {
    event.preventDefault();
    if (!scheduleForm.all_users && scheduleForm.user_ids.length === 0) {
      setError(t('selectAtLeastOneBackupUser'));
      return;
    }
    setLoading(true);
    setError('');
    try {
      const payload = {
        all_users: Boolean(scheduleForm.all_users),
        user_ids: scheduleForm.all_users ? [] : scheduleForm.user_ids.map((id) => Number(id)),
        target_id: scheduleForm.target_id ? Number(scheduleForm.target_id) : null,
        schedule: scheduleForm.schedule.trim(),
        name_suffix: scheduleForm.name_suffix,
        retention: Number(scheduleForm.retention || 7),
        is_active: Boolean(scheduleForm.is_active),
      };
      if (editingScheduleId) {
        await request(`/maintenance/backup-schedules/${editingScheduleId}`, {
          method: 'PUT',
          body: JSON.stringify(payload),
        });
        setNotice('Đã cập nhật lịch sao lưu tự động thành công.');
      } else {
        await request('/maintenance/backup-schedules', {
          method: 'POST',
          body: JSON.stringify(payload),
        });
        setNotice(t('createdBackupSchedule'));
      }
      cancelEditSchedule();
      await loadBackupSchedules();
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  async function toggleBackupSchedule(item) {
    setLoading(true);
    setError('');
    try {
      await request(`/maintenance/backup-schedules/${item.id}/toggle`, {
        method: 'PATCH',
      });
      await loadBackupSchedules();
      setNotice(item.is_active ? 'Đã tạm dừng lịch sao lưu.' : 'Đã kích hoạt lại lịch sao lưu.');
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  async function deleteBackupSchedule(item) {
    if (!window.confirm(t('confirmDeleteBackupSchedule'))) return;
    setLoading(true);
    setError('');
    try {
      await request(`/maintenance/backup-schedules/${item.id}`, { method: 'DELETE' });
      if (editingScheduleId === item.id) cancelEditSchedule();
      await loadBackupSchedules();
      setNotice(t('deletedBackupSchedule'));
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  async function runBackupScheduleNow(item) {
    setLoading(true);
    setError('');
    try {
      const res = await request(`/maintenance/backup-schedules/${item.id}/run`, { method: 'POST' });
      setNotice(res.detail || t('backupScheduleRunningNow'));
      await loadBackupSchedules();
      window.setTimeout(() => {
        loadBackupSchedules();
        loadUserBackups();
      }, 3000);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  function currentRestoreSourceSpec() {
    if (restoreSource.kind === 'local') return { kind: 'local' };
    if (restoreSource.kind === 'target') {
      return { kind: 'target', target_id: Number(restoreSource.target_id) || null };
    }
    return {
      kind: 'remote',
      protocol: restoreRemote.protocol,
      host: restoreRemote.host.trim(),
      port: restoreRemote.port ? Number(restoreRemote.port) : null,
      username: restoreRemote.username.trim(),
      password: restoreRemote.password,
      path: restoreRemote.path.trim() || '/',
      host_key: restoreListing?.host_key || null,
    };
  }

  function selectRestoreSource(next) {
    setRestoreSource(next);
    setRestoreListing(null);
    setRestoreChoice({});
    setRestorePicks([]);
  }

  async function loadRestoreListing(specOverride = null) {
    const spec = specOverride || currentRestoreSourceSpec();
    if (spec.kind === 'target' && !spec.target_id) {
      setError(t('selectBackupDestination'));
      return;
    }
    setRestoreBusy(true);
    setError('');
    try {
      const data = await request('/maintenance/restore/list', {
        method: 'POST',
        body: JSON.stringify({ source: spec }),
      });
      setRestoreListing(data);
      const firstByAccount = {};
      for (const item of data.items || []) {
        const acct = item.username || item.name;
        if (!(acct in firstByAccount)) firstByAccount[acct] = item.key;
      }
      setRestoreChoice(firstByAccount);
      setRestorePicks([]);
    } catch (err) {
      setRestoreListing(null);
      setError(err.message);
    } finally {
      setRestoreBusy(false);
    }
  }

  async function uploadToRestoreSource(event) {
    const picked = Array.from(event.target.files || []);
    event.target.value = '';
    if (picked.length === 0) return;
    const spec = currentRestoreSourceSpec();
    if (spec.kind === 'target' && !spec.target_id) {
      setError(t('selectBackupDestination'));
      return;
    }
    setRestoreBusy(true);
    setError('');
    try {
      const formData = new FormData();
      for (const file of picked) formData.append('files', file);
      formData.append('source', JSON.stringify(spec));
      const res = await request('/maintenance/restore/upload', {
        method: 'POST',
        body: formData,
      });
      setNotice(t('uploadedRestoreBackups', { files: (res.uploaded || []).join(', ') }));
      await loadRestoreListing(spec);
      if (spec.kind === 'local') loadUserBackups();
    } catch (err) {
      setError(err.message);
    } finally {
      setRestoreBusy(false);
    }
  }

  async function runRestoreFromSource() {
    if (!restoreListing || restorePicks.length === 0) return;
    const existingSet = new Set(restoreListing.existing || []);
    const willOverwrite = restorePicks.filter((acct) => existingSet.has(acct));
    const msg = willOverwrite.length > 0
      ? t('confirmRestoreOverwrite', { count: restorePicks.length, overwrite: willOverwrite.join(', ') })
      : t('confirmRestoreAccounts', { count: restorePicks.length });
    if (!window.confirm(msg)) return;

    const byKey = new Map((restoreListing.items || []).map((it) => [it.key, it]));
    const items = restorePicks.map((acct) => {
      const it = byKey.get(restoreChoice[acct]);
      return { key: it.key, username: it.username || '', size: it.size || 0 };
    });
    setRestoreBusy(true);
    setError('');
    try {
      const job = await request('/maintenance/restore/run', {
        method: 'POST',
        body: JSON.stringify({ source: currentRestoreSourceSpec(), items }),
      });
      setRestoreJob(job);
    } catch (err) {
      setError(err.message);
    } finally {
      setRestoreBusy(false);
    }
  }

  async function createSftpTarget(event) {
    event.preventDefault();
    setLoading(true);
    setError('');
    try {
      const created = await request('/maintenance/sftp-targets', {
        method: 'POST',
        body: JSON.stringify({
          ...sftpTargetForm,
          port: Number(sftpTargetForm.port || 22),
        }),
      });
      setSftpTargetForm({
        name: '', kind: 'sftp',
        host: '', port: 22, username: '', password: '', private_key: '', remote_path: '/backups/spanel',
        endpoint: '', region: 'us-east-1', bucket: '', access_key: '', secret_key: '', prefix: '', secure: true,
      });
      setShowDestinationForm(false);
      await loadSftpTargets();
      setSelectedBackupTargetId(String(created.id));
      setNotice(t('savedBackupTarget', { name: created.name }));
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  async function deleteSftpTarget(target) {
    if (!window.confirm(t('confirmDeleteSftpTarget', { name: target.name }))) return;
    setLoading(true);
    setError('');
    try {
      await request(`/maintenance/sftp-targets/${target.id}`, { method: 'DELETE' });
      await loadSftpTargets();
      setNotice(t('deletedSftpTarget', { name: target.name }));
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  async function runSftpBackup() {
    if (!selectedWebsiteId || !selectedBackupTargetId) {
      setError('Vui lòng chọn Website và đích lưu trữ (Backup Destination).');
      return;
    }
    setLoading(true);
    setError('');
    try {
      const data = await request('/maintenance/backup-sftp', {
        method: 'POST',
        body: JSON.stringify({
          website_id: Number(selectedWebsiteId),
          target_id: Number(selectedBackupTargetId),
        }),
      });
      queueBackupJob(data, t('sftpBackupRunningInBackground'));
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  async function uploadBackupArchive(event) {
    const file = event.target.files?.[0];
    event.target.value = '';
    if (!selectedWebsiteId || !file) return;
    setLoading(true);
    setError('');
    try {
      const formData = new FormData();
      formData.append('file', file);
      const data = await request(`/maintenance/backups/${selectedWebsiteId}/upload`, {
        method: 'POST',
        body: formData,
      });
      await loadBackups(selectedWebsiteId);
      setNotice(t('uploadedBackupFile', { file: data.backup_file }));
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  async function restoreBackup(backupFile) {
    if (!selectedWebsiteId) return;
    const filename = backupFile.split('/').pop() || backupFile;
    if (!window.confirm(`Bạn có chắc chắn muốn khôi phục website từ bản sao lưu "${filename}"? Dữ liệu mã nguồn và Database hiện tại của website sẽ được ghi đè.`)) return;
    setLoading(true);
    setError('');
    try {
      const data = await request('/maintenance/restore', {
        method: 'POST',
        body: JSON.stringify({ website_id: Number(selectedWebsiteId), backup_file: backupFile }),
      });
      setNotice(t('restoredWebsiteTo', { path: data.restored_to }));
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  async function deleteBackupFile(backupFile) {
    if (!selectedWebsiteId) return;
    const filename = backupFile.split('/').pop() || backupFile;
    if (!window.confirm(t('confirmDeleteBackup', { name: filename }))) return;
    setLoading(true);
    setError('');
    try {
      const data = await request(`/maintenance/backups/${selectedWebsiteId}?backup_file=${encodeURIComponent(backupFile)}`, {
        method: 'DELETE',
      });
      await loadBackups(selectedWebsiteId);
      setNotice(t('deletedBackupFile', { file: data.deleted }));
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  function formatScheduleScope(item) {
    if (item.all_users) return t('allUsers');
    let ids = [];
    try {
      ids = JSON.parse(item.user_ids || '[]');
    } catch (_err) {
      ids = item.user_id ? [item.user_id] : [];
    }
    if (!Array.isArray(ids) || ids.length === 0) {
      return item.user_id ? `#${item.user_id}` : '-';
    }
    return ids.map((id) => users.find((user) => user.id === Number(id))?.username || `#${id}`).join(', ');
  }

  const activeBackupTab = !isAdmin && backupTab === 'schedule' ? 'website' : backupTab;
  const localUserBackupFiles = userBackups.filter((item) => item.location !== 'restore');
  const filteredUserBackups = localUserBackupFiles.filter((item) => {
    if (!userBackupFilter.trim()) return true;
    const q = userBackupFilter.toLowerCase();
    return (
      (item.username || '').toLowerCase().includes(q)
      || (item.filename || '').toLowerCase().includes(q)
      || (item.relative_path || '').toLowerCase().includes(q)
    );
  });

  const activeJobsList = backupJobs.filter((job) => job.status === 'queued' || job.status === 'running');

  return (
    <div className="content-shell" style={{ display: 'flex', flexDirection: 'column', gap: '20px' }}>
      {/* Top Navigation & Summary Header */}
      <div className="pkg-card" style={{ padding: '18px 22px' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '16px' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '14px' }}>
            <div style={{
              width: '44px', height: '44px', borderRadius: '12px',
              background: 'rgba(13, 148, 136, 0.12)', border: '1px solid rgba(13, 148, 136, 0.28)',
              display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--accent-primary)',
            }}>
              <Archive size={22} />
            </div>
            <div>
              <div style={{ display: 'flex', alignItems: 'center', gap: '10px', flexWrap: 'wrap' }}>
                <h3 style={{ margin: 0, fontSize: '1.1rem', fontWeight: 700, color: 'var(--text-main)' }}>
                  Trung tâm Sao lưu & Khôi phục (Backup & Restore)
                </h3>
                {isManager && (
                  <span style={{
                    padding: '2px 9px', borderRadius: '999px', fontSize: '0.73rem', fontWeight: 700,
                    background: 'rgba(13, 148, 136, 0.12)', border: '1px solid rgba(13, 148, 136, 0.25)',
                    color: 'var(--accent-primary)',
                  }}>
                    Chuẩn Multi-Server: {serverHostname}/&lt;user&gt;/&lt;file&gt;.tar.gz
                  </span>
                )}
              </div>
              <p style={{ margin: '3px 0 0', fontSize: '0.84rem', color: 'var(--text-muted)' }}>
                {isManager
                  ? 'Quản lý sao lưu phân cấp theo Hostname/User trên Local, S3 Object Storage và SFTP/FTP dành cho Admin/Reseller.'
                  : 'Sao lưu toàn bộ tài khoản hoặc từng Website và tự cấu hình lưu trữ từ xa (SFTP / S3) về tài khoản riêng của bạn.'}
              </p>
            </div>
          </div>

          {/* Modern Pill Tab Bar */}
          <div style={{
            display: 'inline-flex', background: 'var(--surface-2)', padding: '4px',
            borderRadius: '10px', border: '1px solid var(--border-subtle)', gap: '4px', flexWrap: 'wrap',
          }}>
            {[
              { id: 'user', label: 'User Backups', icon: Users, badge: localUserBackupFiles.length },
              { id: 'website', label: 'Website Backups', icon: Globe, badge: backups.length },
              ...(isAdmin ? [
                { id: 'schedule', label: 'Scheduled Backups', icon: Clock, badge: backupSchedules.length },
              ] : []),
              {
                id: 'restore',
                label: isAdmin ? 'Restore & Destinations' : 'My Backup Storage (SFTP / S3)',
                icon: isAdmin ? ArchiveRestore : Cloud,
                badge: sftpTargets.length,
              },
            ].map((tab) => {
              const Icon = tab.icon;
              const isActive = activeBackupTab === tab.id;
              return (
                <button
                  key={tab.id}
                  type="button"
                  onClick={() => {
                    setBackupTab(tab.id);
                    if (isAdmin && tab.id === 'restore' && !restoreListing && restoreSource.kind === 'local') {
                      loadRestoreListing({ kind: 'local' });
                    }
                  }}
                  style={{
                    display: 'inline-flex', alignItems: 'center', gap: '8px',
                    padding: '8px 14px', borderRadius: '8px', fontSize: '0.84rem', fontWeight: 600,
                    border: isActive ? '1px solid rgba(13, 148, 136, 0.35)' : '1px solid transparent',
                    background: isActive ? 'var(--surface-1)' : 'transparent',
                    color: isActive ? 'var(--accent-primary)' : 'var(--text-muted)',
                    boxShadow: isActive ? '0 2px 6px rgba(0,0,0,0.06)' : 'none',
                    cursor: 'pointer', transition: 'all 0.15s ease',
                  }}
                >
                  <Icon size={15} />
                  <span>{tab.label}</span>
                  <span style={{
                    padding: '1px 7px', borderRadius: '999px', fontSize: '0.73rem', fontWeight: 700,
                    background: isActive ? 'rgba(13, 148, 136, 0.14)' : 'var(--surface-3)',
                    color: isActive ? 'var(--accent-primary)' : 'var(--text-muted)',
                  }}>
                    {tab.badge}
                  </span>
                </button>
              );
            })}
          </div>
        </div>

        {/* Active Background Backup Job Indicator */}
        {activeJobsList.length > 0 && (
          <div style={{
            marginTop: '14px', padding: '10px 14px', borderRadius: '8px',
            background: 'rgba(13, 148, 136, 0.1)', border: '1px solid rgba(13, 148, 136, 0.28)',
            display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '12px',
          }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '10px', fontSize: '0.85rem', color: 'var(--text-main)' }}>
              <RefreshCw size={16} className="spin" style={{ color: 'var(--accent-primary)' }} />
              <strong>Đang xử lý tiến trình ngầm ({activeJobsList.length}):</strong>
              <span>{activeJobsList[0]?.message || 'Đang đóng gói dữ liệu sao lưu...'}</span>
            </div>
            <span style={{ fontSize: '0.78rem', color: 'var(--text-muted)' }}>Tự động cập nhật mỗi 2.5 giây</span>
          </div>
        )}
      </div>

      {/* =====================================================================
          TAB 1: USER BACKUPS (Full Account Backup)
         ===================================================================== */}
      {activeBackupTab === 'user' && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '18px' }}>
          <div className="pkg-card" style={{ padding: '20px 22px' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', flexWrap: 'wrap', gap: '16px' }}>
              <div>
                <h4 style={{ margin: 0, fontSize: '1rem', fontWeight: 700, color: 'var(--text-main)', display: 'flex', alignItems: 'center', gap: '8px' }}>
                  <Users size={18} style={{ color: 'var(--accent-primary)' }} />
                  Tạo bản sao lưu toàn bộ tài khoản (Full User Backup)
                </h4>
                <p style={{ margin: '4px 0 0', fontSize: '0.83rem', color: 'var(--text-muted)' }}>
                  {isManager ? (
                    <>
                      Lưu trữ chuẩn phân cấp Multi-Server: <code>{serverHostname}/&lt;username&gt;/user-&lt;username&gt;-&lt;timestamp&gt;.tar.gz</code> trên cả Local, S3 và SFTP.
                    </>
                  ) : (
                    <>
                      Đóng gói trọn vẹn Website, Database MariaDB và Hộp thư Email của bạn vào 1 file <code>.tar.gz</code> để lưu tại chỗ hoặc đẩy về kho SFTP/S3 cá nhân.
                    </>
                  )}
                </p>
              </div>
              <button type="button" className="secondary-light" onClick={loadUserBackups} disabled={loading}>
                <RefreshCw size={15} /> Làm mới danh sách
              </button>
            </div>

            <div style={{
              marginTop: '16px', padding: '16px', borderRadius: '10px',
              background: 'var(--surface-2)', border: '1px solid var(--border-subtle)',
              display: 'grid', gridTemplateColumns: isManager ? '1fr 1fr auto' : '1fr auto', gap: '14px', alignItems: 'end',
            }}>
              {isManager && (
                <label style={{ margin: 0 }}>
                  <span style={{ display: 'block', fontSize: '0.8rem', fontWeight: 600, color: 'var(--text-muted)', marginBottom: '6px' }}>
                    Tài khoản cần sao lưu (User Account)
                  </span>
                  <select value={selectedBackupUserId} onChange={(e) => setSelectedBackupUserId(e.target.value)}>
                    <option value="">{t('selectUser')}</option>
                    {backupUsers.map((item) => (
                      <option key={item.id} value={item.id}>
                        {item.username} ({item.role}) — {item.email}
                      </option>
                    ))}
                  </select>
                </label>
              )}
              <label style={{ margin: 0 }}>
                <span style={{ display: 'block', fontSize: '0.8rem', fontWeight: 600, color: 'var(--text-muted)', marginBottom: '6px' }}>
                  {isManager ? 'Đích lưu trữ (Local / SFTP / S3)' : 'Đích lưu trữ của bạn (Local hoặc Kho SFTP/S3 cá nhân)'}
                </span>
                <select value={selectedUserBackupTargetId} onChange={(e) => setSelectedUserBackupTargetId(e.target.value)}>
                  <option value="">
                    {isManager
                      ? `Lưu tại máy chủ này (${serverHostname}/<user>/...)`
                      : 'Lưu trên máy chủ hiện tại'}
                  </option>
                  {sftpTargets.map((target) => (
                    <option key={target.id} value={target.id}>
                      [{String(target.kind || 'sftp').toUpperCase()}] {target.name} ({target.kind === 's3' ? target.bucket : target.host})
                    </option>
                  ))}
                </select>
              </label>
              <button
                type="button"
                onClick={createUserBackup}
                disabled={loading || (isManager && !selectedBackupUserId)}
                style={{ height: '40px', padding: '0 18px', fontWeight: 600 }}
              >
                <Archive size={16} />
                {selectedUserBackupTargetId ? 'Backup & Đẩy lên Cloud/SFTP' : 'Tạo Full User Backup'}
              </button>
            </div>

            {lastRestoredUserCredentials && (
              <div style={{
                marginTop: '14px', padding: '12px 16px', borderRadius: '8px',
                background: 'rgba(16, 185, 129, 0.1)', border: '1px solid rgba(16, 185, 129, 0.3)',
                display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: '12px',
              }}>
                <div style={{ fontSize: '0.85rem', color: 'var(--text-main)' }}>
                  <strong>Tài khoản mới được khôi phục:</strong> <code>{lastRestoredUserCredentials.username}</code> ·
                  Mật khẩu tạm thời: <code style={{ fontWeight: 700, color: 'var(--accent-primary)' }}>{lastRestoredUserCredentials.password}</code>
                </div>
                <button
                  type="button"
                  className="secondary-light"
                  onClick={() => {
                    navigator.clipboard?.writeText(lastRestoredUserCredentials.password);
                    setNotice('Đã sao chép mật khẩu vào bộ nhớ tạm.');
                  }}
                >
                  <Copy size={14} /> Sao chép mật khẩu
                </button>
              </div>
            )}
          </div>

          {/* Local User Backups Table Card */}
          <div className="pkg-card" style={{ padding: '20px 22px' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '12px', marginBottom: '16px' }}>
              <div>
                <h4 style={{ margin: 0, fontSize: '0.98rem', fontWeight: 700, color: 'var(--text-main)' }}>
                  Danh sách bản sao lưu User ({filteredUserBackups.length})
                </h4>
                <span style={{ fontSize: '0.8rem', color: 'var(--text-muted)' }}>
                  {isManager ? (
                    <>Cấu trúc lưu trữ: <code>{serverHostname}/&lt;username&gt;/&lt;file&gt;.tar.gz</code></>
                  ) : (
                    <>Danh sách các bản sao lưu toàn bộ tài khoản của bạn</>
                  )}
                </span>
              </div>
              <div style={{ position: 'relative', minWidth: '260px' }}>
                <Search size={15} style={{ position: 'absolute', left: '11px', top: '50%', transform: 'translateY(-50%)', color: 'var(--text-muted)' }} />
                <input
                  type="search"
                  placeholder="Tìm theo username, hostname hoặc tên file..."
                  value={userBackupFilter}
                  onChange={(e) => setUserBackupFilter(e.target.value)}
                  style={{ paddingLeft: '34px', height: '36px', fontSize: '0.84rem' }}
                />
              </div>
            </div>

            <div className="pkg-table-wrap">
              <table className="pkg-clean-table">
                <thead>
                  <tr>
                    <th>Tài khoản (User)</th>
                    <th>{isManager ? 'Đường dẫn chuẩn (Hostname / User / File)' : 'Tên file Backup'}</th>
                    <th>Số Website</th>
                    <th>Thời gian tạo</th>
                    <th>Dung lượng</th>
                    <th>Trạng thái</th>
                    <th style={{ textAlign: 'right' }}>Thao tác</th>
                  </tr>
                </thead>
                <tbody>
                  {filteredUserBackups.map((item) => {
                    const standardizedPath = item.relative_path && item.relative_path.includes('/')
                      ? item.relative_path
                      : `${item.hostname || serverHostname}/${item.username || 'user'}/${item.filename}`;
                    return (
                      <tr key={item.backup_file}>
                        <td>
                          <strong style={{ color: 'var(--text-main)' }}>{item.username || '-'}</strong>
                        </td>
                        <td>
                          {isManager ? (
                            <>
                              <div style={{ fontWeight: 600, fontSize: '0.84rem', color: 'var(--accent-primary)', fontFamily: 'monospace' }}>
                                {standardizedPath}
                              </div>
                              <div style={{ fontSize: '0.74rem', color: 'var(--text-muted)', fontFamily: 'monospace' }}>
                                {item.backup_file}
                              </div>
                            </>
                          ) : (
                            <div style={{ fontWeight: 600, fontSize: '0.84rem', color: 'var(--text-main)' }}>
                              {item.filename}
                            </div>
                          )}
                        </td>
                        <td>
                          <span style={{
                            padding: '2px 8px', borderRadius: '999px', fontSize: '0.78rem', fontWeight: 600,
                            background: 'var(--surface-2)', border: '1px solid var(--border-subtle)', color: 'var(--text-main)',
                          }}>
                            {item.websites ?? 0} site
                          </span>
                        </td>
                        <td style={{ fontSize: '0.83rem', color: 'var(--text-muted)' }}>
                          {item.generated_at ? new Date(item.generated_at).toLocaleString() : '-'}
                        </td>
                        <td style={{ fontWeight: 600, fontSize: '0.84rem' }}>{formatBytes(item.size)}</td>
                        <td>
                          <span style={{
                            display: 'inline-flex', alignItems: 'center', gap: '5px',
                            padding: '3px 9px', borderRadius: '999px', fontSize: '0.75rem', fontWeight: 600,
                            background: item.valid ? 'rgba(16, 185, 129, 0.12)' : 'rgba(239, 68, 68, 0.12)',
                            color: item.valid ? '#10b981' : '#ef4444',
                          }}>
                            {item.valid ? <CheckCircle size={13} /> : <AlertCircle size={13} />}
                            {item.valid ? 'Hợp lệ' : (item.error || 'Lỗi')}
                          </span>
                        </td>
                        <td style={{ textAlign: 'right' }}>
                          <div style={{ display: 'inline-flex', gap: '6px', justifyContent: 'flex-end' }}>
                            {isAdmin && (
                              <button
                                type="button"
                                className="secondary-light"
                                onClick={() => restoreUserBackupItem(item)}
                                disabled={loading || !item.valid}
                                title="Khôi phục tài khoản này"
                              >
                                <RotateCcw size={14} /> Khôi phục
                              </button>
                            )}
                            <button
                              type="button"
                              className="secondary-light"
                              onClick={() => downloadUserBackup(item)}
                              disabled={loading}
                              title="Tải file .tar.gz về máy"
                            >
                              <Download size={14} /> Tải về
                            </button>
                            <button
                              type="button"
                              className="danger"
                              onClick={() => deleteUserBackupItem(item)}
                              disabled={loading}
                              title="Xóa bản sao lưu"
                              style={{ padding: '6px 10px' }}
                            >
                              <Trash2 size={14} />
                            </button>
                          </div>
                        </td>
                      </tr>
                    );
                  })}
                  {filteredUserBackups.length === 0 && (
                    <tr>
                      <td colSpan="7">
                        <EmptyState
                          icon={Archive}
                          title="Chưa có bản sao lưu User nào"
                          subtitle="Hãy chọn một tài khoản ở khung phía trên và nhấn Tạo Full User Backup."
                        />
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      )}

      {/* =====================================================================
          TAB 2: WEBSITE BACKUPS (Single Website + Database Backup)
         ===================================================================== */}
      {activeBackupTab === 'website' && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '18px' }}>
          <div className="pkg-card" style={{ padding: '20px 22px' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', flexWrap: 'wrap', gap: '16px' }}>
              <div>
                <h4 style={{ margin: 0, fontSize: '1rem', fontWeight: 700, color: 'var(--text-main)', display: 'flex', alignItems: 'center', gap: '8px' }}>
                  <Globe size={18} style={{ color: 'var(--accent-primary)' }} />
                  Sao lưu & Khôi phục từng Website (Mã nguồn + Database)
                </h4>
                <p style={{ margin: '4px 0 0', fontSize: '0.83rem', color: 'var(--text-muted)' }}>
                  {isManager ? (
                    <>Định dạng chuẩn khi đẩy lên S3/SFTP: <code>{serverHostname}/&lt;user&gt;/&lt;user&gt;-&lt;domain&gt;.tar.gz</code></>
                  ) : (
                    <>Đóng gói thư mục gốc của Website và tự động xuất file SQL Database đi kèm vào gói <code>.tar.gz</code>.</>
                  )}
                </p>
              </div>
              <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
                <label className="button secondary-light" style={{ margin: 0, cursor: selectedWebsiteId ? 'pointer' : 'not-allowed', display: 'inline-flex', alignItems: 'center', gap: '6px' }}>
                  <Upload size={15} /> Tải lên file .tar.gz
                  <input type="file" accept=".tar.gz,application/gzip" onChange={uploadBackupArchive} hidden disabled={loading || !selectedWebsiteId} />
                </label>
                <button type="button" onClick={createBackup} disabled={loading || !selectedWebsiteId}>
                  <Archive size={15} /> Tạo Backup Website
                </button>
              </div>
            </div>

            <div style={{
              marginTop: '16px', padding: '16px', borderRadius: '10px',
              background: 'var(--surface-2)', border: '1px solid var(--border-subtle)',
              display: 'grid', gridTemplateColumns: '1fr 1fr auto', gap: '14px', alignItems: 'end',
            }}>
              <div>
                <WebsiteSelect
                  value={selectedWebsiteId}
                  onChange={setSelectedWebsiteId}
                  websites={websites}
                  users={users}
                  isAdmin={isAdmin}
                  isReseller={isReseller}
                />
              </div>
              <label style={{ margin: 0 }}>
                <span style={{ display: 'block', fontSize: '0.8rem', fontWeight: 600, color: 'var(--text-muted)', marginBottom: '6px' }}>
                  {isManager ? 'Đẩy bản sao lưu Website lên Cloud / SFTP' : 'Đẩy về Kho SFTP / S3 cá nhân của bạn'}
                </span>
                <select value={selectedBackupTargetId} onChange={(e) => setSelectedBackupTargetId(e.target.value)}>
                  <option value="">-- Chọn đích lưu trữ SFTP / S3 --</option>
                  {sftpTargets.map((target) => (
                    <option key={target.id} value={target.id}>
                      [{String(target.kind || 'sftp').toUpperCase()}] {target.name} ({target.kind === 's3' ? target.bucket : target.host})
                    </option>
                  ))}
                </select>
              </label>
              <button
                type="button"
                className="secondary-light"
                onClick={runSftpBackup}
                disabled={loading || !selectedWebsiteId || !selectedBackupTargetId}
                style={{ height: '40px' }}
              >
                <Cloud size={15} /> Backup & Gửi đi
              </button>
            </div>
          </div>

          <div className="pkg-card" style={{ padding: '20px 22px' }}>
            <h4 style={{ margin: '0 0 14px', fontSize: '0.98rem', fontWeight: 700, color: 'var(--text-main)' }}>
              Danh sách bản sao lưu của Website ({backups.length})
            </h4>
            <div className="pkg-table-wrap">
              <table className="pkg-clean-table">
                <thead>
                  <tr>
                    <th>Tên file sao lưu</th>
                    <th>{isManager ? 'Đường dẫn phân cấp (Hostname / User / File)' : 'Đường dẫn'}</th>
                    <th style={{ textAlign: 'right' }}>Thao tác</th>
                  </tr>
                </thead>
                <tbody>
                  {backups.map((item) => {
                    const fileName = item.split('/').pop() || item;
                    const selectedSiteObj = websites.find((w) => String(w.id) === String(selectedWebsiteId));
                    const siteOwner = users.find((u) => u.id === selectedSiteObj?.owner_id)?.username || 'user';
                    return (
                      <tr key={item}>
                        <td>
                          <strong style={{ color: 'var(--text-main)' }}>{fileName}</strong>
                        </td>
                        <td style={{ fontFamily: 'monospace', fontSize: '0.8rem', color: 'var(--text-muted)' }}>
                          {isManager ? `${serverHostname}/${siteOwner}/${fileName}` : fileName}
                        </td>
                        <td style={{ textAlign: 'right' }}>
                          <div style={{ display: 'inline-flex', gap: '6px', justifyContent: 'flex-end' }}>
                            <button
                              type="button"
                              className="secondary-light"
                              onClick={() => restoreBackup(item)}
                              disabled={loading}
                            >
                              <RotateCcw size={14} /> Khôi phục
                            </button>
                            <button
                              type="button"
                              className="secondary-light"
                              onClick={() => downloadBackup(item)}
                              disabled={loading}
                            >
                              <Download size={14} /> Tải về
                            </button>
                            <button
                              type="button"
                              className="danger"
                              onClick={() => deleteBackupFile(item)}
                              disabled={loading}
                              style={{ padding: '6px 10px' }}
                            >
                              <Trash2 size={14} />
                            </button>
                          </div>
                        </td>
                      </tr>
                    );
                  })}
                  {backups.length === 0 && (
                    <tr>
                      <td colSpan="3">
                        <EmptyState
                          icon={Archive}
                          title="Chưa có bản sao lưu nào cho Website này"
                          subtitle="Nhấn nút Tạo Backup Website ở trên để tạo bản sao lưu đầu tiên."
                        />
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      )}

      {/* =====================================================================
          TAB 3: SCHEDULED BACKUPS (Cron Automation + Retention + SFTP/S3)
         ===================================================================== */}
      {isAdmin && activeBackupTab === 'schedule' && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '18px' }}>
          <div className="pkg-card" style={{ padding: '20px 22px' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '12px', marginBottom: '16px' }}>
              <div>
                <h4 style={{ margin: 0, fontSize: '1rem', fontWeight: 700, color: 'var(--text-main)', display: 'flex', alignItems: 'center', gap: '8px' }}>
                  <Clock size={18} style={{ color: 'var(--accent-primary)' }} />
                  {editingScheduleId ? `Chỉnh sửa lịch sao lưu #${editingScheduleId}` : 'Thiết lập lịch sao lưu tự động mới (Scheduled Backup)'}
                </h4>
                <p style={{ margin: '4px 0 0', fontSize: '0.83rem', color: 'var(--text-muted)' }}>
                  Tự động tạo Full User Backup định kỳ theo cấu trúc chuẩn <code>{serverHostname}/&lt;username&gt;/&lt;file&gt;.tar.gz</code>.
                </p>
              </div>
              {editingScheduleId && (
                <button type="button" className="secondary-light" onClick={cancelEditSchedule}>
                  <X size={15} /> Hủy chỉnh sửa
                </button>
              )}
            </div>

            <form onSubmit={saveBackupSchedule} style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
              <div style={{
                display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))', gap: '16px',
              }}>
                {/* Box 1: Scope */}
                <div style={{
                  padding: '16px', borderRadius: '10px', background: 'var(--surface-2)',
                  border: '1px solid var(--border-subtle)', display: 'flex', flexDirection: 'column', gap: '12px',
                }}>
                  <div style={{ fontWeight: 700, fontSize: '0.88rem', color: 'var(--text-main)' }}>
                    1. Phạm vi tài khoản (Accounts Scope)
                  </div>
                  <div style={{ display: 'flex', gap: '8px' }}>
                    <button
                      type="button"
                      className={scheduleForm.all_users ? '' : 'secondary-light'}
                      onClick={() => setScheduleForm((p) => ({ ...p, all_users: true, user_ids: [] }))}
                      style={{ flex: 1, justifyContent: 'center' }}
                    >
                      <Users size={14} /> Tất cả Users ({backupUsers.length})
                    </button>
                    <button
                      type="button"
                      className={!scheduleForm.all_users ? '' : 'secondary-light'}
                      onClick={() => setScheduleForm((p) => ({
                        ...p,
                        all_users: false,
                        user_ids: p.user_ids.length ? p.user_ids : backupUsers.slice(0, 1).map((u) => u.id),
                      }))}
                      style={{ flex: 1, justifyContent: 'center' }}
                    >
                      Chọn từng User ({scheduleForm.user_ids.length})
                    </button>
                  </div>

                  {scheduleForm.all_users ? (
                    <p style={{ margin: 0, fontSize: '0.81rem', color: 'var(--text-muted)', lineHeight: 1.5 }}>
                      Hệ thống sẽ tự động sao lưu tất cả tài khoản đang hoạt động (bao gồm cả các tài khoản mới tạo trong tương lai).
                    </p>
                  ) : (
                    <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px', maxHeight: '130px', overflowY: 'auto', padding: '4px 0' }}>
                      {backupUsers.map((item) => {
                        const picked = scheduleForm.user_ids.includes(item.id);
                        return (
                          <button
                            key={item.id}
                            type="button"
                            onClick={() => toggleScheduleUser(item.id)}
                            style={{
                              padding: '4px 10px', borderRadius: '999px', fontSize: '0.78rem', fontWeight: 600,
                              border: picked ? '1px solid var(--accent-primary)' : '1px solid var(--border-subtle)',
                              background: picked ? 'rgba(13, 148, 136, 0.14)' : 'var(--surface-1)',
                              color: picked ? 'var(--accent-primary)' : 'var(--text-muted)',
                              cursor: 'pointer',
                            }}
                          >
                            {picked ? '✓ ' : ''}{item.username}
                          </button>
                        );
                      })}
                    </div>
                  )}
                </div>

                {/* Box 2: Frequency & Cron */}
                <div style={{
                  padding: '16px', borderRadius: '10px', background: 'var(--surface-2)',
                  border: '1px solid var(--border-subtle)', display: 'flex', flexDirection: 'column', gap: '12px',
                }}>
                  <div style={{ fontWeight: 700, fontSize: '0.88rem', color: 'var(--text-main)' }}>
                    2. Chu kỳ chạy & Số bản lưu giữ
                  </div>
                  <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '10px' }}>
                    <label style={{ margin: 0 }}>
                      <span style={{ display: 'block', fontSize: '0.78rem', color: 'var(--text-muted)', marginBottom: '4px' }}>Mẫu thời gian</span>
                      <select
                        value={isPresetSchedule(scheduleForm.schedule) ? scheduleForm.schedule : 'custom'}
                        onChange={(e) => {
                          if (e.target.value !== 'custom') {
                            setScheduleForm((p) => ({ ...p, schedule: e.target.value }));
                          }
                        }}
                      >
                        {BACKUP_SCHEDULE_PRESETS.map((preset) => (
                          <option key={preset.value} value={preset.value}>{t(preset.labelKey)}</option>
                        ))}
                        <option value="custom">{t('customCronSchedule')}</option>
                      </select>
                    </label>
                    <label style={{ margin: 0 }}>
                      <span style={{ display: 'block', fontSize: '0.78rem', color: 'var(--text-muted)', marginBottom: '4px' }}>Biểu thức Cron</span>
                      <input
                        value={scheduleForm.schedule}
                        onChange={(e) => setScheduleForm((p) => ({ ...p, schedule: e.target.value }))}
                        placeholder="0 2 * * *"
                        required
                      />
                    </label>
                  </div>
                  <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '10px' }}>
                    <label style={{ margin: 0 }}>
                      <span style={{ display: 'block', fontSize: '0.78rem', color: 'var(--text-muted)', marginBottom: '4px' }}>Giữ lại (Retention)</span>
                      <input
                        type="number"
                        min="1"
                        max="90"
                        value={scheduleForm.retention}
                        onChange={(e) => setScheduleForm((p) => ({ ...p, retention: e.target.value }))}
                        required
                      />
                    </label>
                    <label style={{ margin: 0 }}>
                      <span style={{ display: 'block', fontSize: '0.78rem', color: 'var(--text-muted)', marginBottom: '4px' }}>Quy tắc đặt tên file</span>
                      <select
                        value={scheduleForm.name_suffix}
                        onChange={(e) => setScheduleForm((p) => ({ ...p, name_suffix: e.target.value }))}
                      >
                        <option value="full_date">Theo ngày (YYYY-MM-DD)</option>
                        <option value="day_of_week">Theo thứ (Mon..Sun)</option>
                        <option value="week_of_month">Theo tuần (W1..W5)</option>
                        <option value="none">Ghi đè 1 file duy nhất</option>
                      </select>
                    </label>
                  </div>
                </div>

                {/* Box 3: Destination */}
                <div style={{
                  padding: '16px', borderRadius: '10px', background: 'var(--surface-2)',
                  border: '1px solid var(--border-subtle)', display: 'flex', flexDirection: 'column', justifyContent: 'space-between', gap: '12px',
                }}>
                  <div>
                    <div style={{ fontWeight: 700, fontSize: '0.88rem', color: 'var(--text-main)', marginBottom: '10px' }}>
                      3. Đích lưu trữ (Destination)
                    </div>
                    <label style={{ margin: 0 }}>
                      <span style={{ display: 'block', fontSize: '0.78rem', color: 'var(--text-muted)', marginBottom: '4px' }}>Nơi lưu bản sao lưu</span>
                      <select
                        value={scheduleForm.target_id}
                        onChange={(e) => setScheduleForm((p) => ({ ...p, target_id: e.target.value }))}
                      >
                        <option value="">Máy chủ hiện tại (Local Disk)</option>
                        {sftpTargets.map((target) => (
                          <option key={target.id} value={target.id}>
                            [{String(target.kind || 'sftp').toUpperCase()}] {target.name} ({target.kind === 's3' ? target.bucket : target.host})
                          </option>
                        ))}
                      </select>
                    </label>
                    <div style={{ marginTop: '10px', fontSize: '0.77rem', color: 'var(--text-muted)', lineHeight: 1.5 }}>
                      Đường dẫn lưu mẫu:<br />
                      <code style={{ color: 'var(--accent-primary)' }}>
                        {scheduleForm.name_suffix === 'none' && `${serverHostname}/demous/user-demous.tar.gz`}
                        {scheduleForm.name_suffix === 'day_of_week' && `${serverHostname}/demous/user-demous-Wed.tar.gz`}
                        {scheduleForm.name_suffix === 'week_of_month' && `${serverHostname}/demous/user-demous-W3.tar.gz`}
                        {scheduleForm.name_suffix === 'full_date' && `${serverHostname}/demous/user-demous-2026-10-21.tar.gz`}
                      </code>
                    </div>
                  </div>

                  <button type="submit" disabled={loading} style={{ width: '100%', justifyContent: 'center', height: '40px', fontWeight: 600 }}>
                    {editingScheduleId ? <Check size={16} /> : <Plus size={16} />}
                    {editingScheduleId ? 'Lưu thay đổi lịch sao lưu' : 'Tạo lịch sao lưu tự động'}
                  </button>
                </div>
              </div>
            </form>
          </div>

          {/* Configured Schedules Table Card */}
          <div className="pkg-card" style={{ padding: '20px 22px' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '14px' }}>
              <h4 style={{ margin: 0, fontSize: '0.98rem', fontWeight: 700, color: 'var(--text-main)' }}>
                Danh sách lịch sao lưu đang cấu hình ({backupSchedules.length})
              </h4>
              <button type="button" className="secondary-light" onClick={loadBackupSchedules} disabled={loading}>
                <RefreshCw size={14} /> Làm mới
              </button>
            </div>

            <div className="pkg-table-wrap">
              <table className="pkg-clean-table">
                <thead>
                  <tr>
                    <th>Phạm vi User</th>
                    <th>Lịch Cron & Đặt tên</th>
                    <th>Đích lưu trữ</th>
                    <th>Giữ lại</th>
                    <th>Trạng thái lịch</th>
                    <th>Lần chạy gần nhất</th>
                    <th style={{ textAlign: 'right' }}>Thao tác</th>
                  </tr>
                </thead>
                <tbody>
                  {backupSchedules.map((item) => {
                    const targetObj = sftpTargets.find((tItem) => tItem.id === item.target_id);
                    return (
                      <tr key={item.id}>
                        <td>
                          <strong style={{ color: 'var(--text-main)' }}>{formatScheduleScope(item)}</strong>
                        </td>
                        <td>
                          <code style={{ fontWeight: 600 }}>{item.schedule}</code>
                          <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>
                            Suffix: {item.name_suffix || 'full_date'}
                          </div>
                        </td>
                        <td>
                          {targetObj ? (
                            <span style={{
                              padding: '3px 9px', borderRadius: '999px', fontSize: '0.76rem', fontWeight: 600,
                              background: 'rgba(13, 148, 136, 0.12)', color: 'var(--accent-primary)',
                            }}>
                              {String(targetObj.kind || 'sftp').toUpperCase()}: {targetObj.name}
                            </span>
                          ) : (
                            <span style={{ fontSize: '0.82rem', color: 'var(--text-muted)' }}>Local ({serverHostname})</span>
                          )}
                        </td>
                        <td>{item.retention} bản</td>
                        <td>
                          <span style={{
                            padding: '3px 9px', borderRadius: '999px', fontSize: '0.75rem', fontWeight: 600,
                            background: item.is_active ? 'rgba(16, 185, 129, 0.12)' : 'var(--surface-3)',
                            color: item.is_active ? '#10b981' : 'var(--text-muted)',
                          }}>
                            {item.is_active ? 'Đang bật (Active)' : 'Tạm dừng (Paused)'}
                          </span>
                        </td>
                        <td>
                          <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                            <span style={{
                              padding: '2px 7px', borderRadius: '999px', fontSize: '0.73rem', fontWeight: 600,
                              background: item.last_status === 'ok'
                                ? 'rgba(16, 185, 129, 0.12)'
                                : item.last_status === 'error'
                                ? 'rgba(239, 68, 68, 0.12)'
                                : 'var(--surface-3)',
                              color: item.last_status === 'ok'
                                ? '#10b981'
                                : item.last_status === 'error'
                                ? '#ef4444'
                                : 'var(--text-muted)',
                            }}>
                              {item.last_status || 'pending'}
                            </span>
                            <span style={{ fontSize: '0.78rem', color: 'var(--text-muted)' }}>
                              {item.last_run_at ? new Date(item.last_run_at).toLocaleString() : 'Chưa chạy'}
                            </span>
                          </div>
                          {item.last_message && (
                            <div style={{ fontSize: '0.74rem', color: 'var(--text-muted)', marginTop: '2px', maxWidth: '240px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={item.last_message}>
                              {item.last_message}
                            </div>
                          )}
                        </td>
                        <td style={{ textAlign: 'right' }}>
                          <div style={{ display: 'inline-flex', gap: '6px', justifyContent: 'flex-end' }}>
                            <button
                              type="button"
                              className="secondary-light"
                              onClick={() => runBackupScheduleNow(item)}
                              disabled={loading || item.last_status === 'running'}
                              title="Chạy ngay lập tức"
                            >
                              <Play size={14} /> Chạy ngay
                            </button>
                            <button
                              type="button"
                              className="secondary-light"
                              onClick={() => toggleBackupSchedule(item)}
                              disabled={loading}
                              title={item.is_active ? 'Tạm dừng lịch' : 'Kích hoạt lịch'}
                            >
                              {item.is_active ? <Pause size={14} /> : <Play size={14} />}
                            </button>
                            <button
                              type="button"
                              className="secondary-light"
                              onClick={() => startEditSchedule(item)}
                              disabled={loading}
                              title="Chỉnh sửa lịch"
                            >
                              <Pencil size={14} />
                            </button>
                            <button
                              type="button"
                              className="danger"
                              onClick={() => deleteBackupSchedule(item)}
                              disabled={loading}
                              style={{ padding: '6px 10px' }}
                              title="Xóa lịch"
                            >
                              <Trash2 size={14} />
                            </button>
                          </div>
                        </td>
                      </tr>
                    );
                  })}
                  {backupSchedules.length === 0 && (
                    <tr>
                      <td colSpan="7">
                        <EmptyState
                          icon={Clock}
                          title="Chưa có lịch sao lưu tự động nào"
                          subtitle="Sử dụng biểu mẫu phía trên để tạo lịch sao lưu định kỳ."
                        />
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      )}

      {/* =====================================================================
          TAB 4: RESTORE CENTER (ADMIN) & OFF-SERVER DESTINATIONS (ALL ROLES)
         ===================================================================== */}
      {activeBackupTab === 'restore' && (() => {
        const byAccount = new Map();
        for (const item of restoreListing?.items || []) {
          const acct = item.username || item.name;
          if (!byAccount.has(acct)) byAccount.set(acct, []);
          byAccount.get(acct).push(item);
        }
        const existingSet = new Set(restoreListing?.existing || []);
        const filter = restoreFilter.trim().toLowerCase();
        const accounts = Array.from(byAccount.entries())
          .filter(([acct]) => !filter || acct.toLowerCase().includes(filter));
        const allPicked = accounts.length > 0 && accounts.every(([acct]) => restorePicks.includes(acct));
        const byKey = new Map((restoreListing?.items || []).map((it) => [it.key, it]));
        const selectedBytes = restorePicks.reduce((sum, acct) => sum + (byKey.get(restoreChoice[acct])?.size || 0), 0);
        const overwriteCount = restorePicks.filter((acct) => existingSet.has(acct)).length;

        return (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '18px' }}>
            {/* 4-Step Restore Wizard Card (Admin only) */}
            {isAdmin && (
              <div className="pkg-card" style={{ padding: '20px 22px' }}>
                <div style={{ marginBottom: '16px' }}>
                  <h4 style={{ margin: 0, fontSize: '1rem', fontWeight: 700, color: 'var(--text-main)', display: 'flex', alignItems: 'center', gap: '8px' }}>
                    <ArchiveRestore size={18} style={{ color: 'var(--accent-primary)' }} />
                    Trình thuật sĩ Khôi phục Tài khoản (4-Step Account Restore Wizard)
                  </h4>
                  <p style={{ margin: '4px 0 0', fontSize: '0.83rem', color: 'var(--text-muted)' }}>
                    Tự động nhận diện cấu trúc phân cấp <code>&lt;hostname&gt;/&lt;username&gt;/&lt;file&gt;.tar.gz</code> trên Local, S3, SFTP và FTP/FTPS để tránh nhầm lẫn giữa các máy chủ.
                  </p>
                </div>

                {/* STEP 1: Choose Source */}
                <div style={{
                  padding: '16px', borderRadius: '10px', background: 'var(--surface-2)',
                  border: '1px solid var(--border-subtle)', marginBottom: '14px',
                }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '12px', marginBottom: '12px' }}>
                    <div style={{ fontWeight: 700, fontSize: '0.9rem', color: 'var(--text-main)' }}>
                      Bước 1: Chọn nguồn chứa bản sao lưu (Backup Source)
                    </div>
                    <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
                      <button
                        type="button"
                        className={restoreSource.kind === 'local' ? '' : 'secondary-light'}
                        onClick={() => {
                          selectRestoreSource({ kind: 'local', target_id: '' });
                          loadRestoreListing({ kind: 'local' });
                        }}
                      >
                        <HardDrive size={14} /> Máy chủ này (Local)
                      </button>
                      <button
                        type="button"
                        className={restoreSource.kind === 'target' ? '' : 'secondary-light'}
                        onClick={() => selectRestoreSource({ kind: 'target', target_id: sftpTargets[0]?.id ? String(sftpTargets[0].id) : '' })}
                      >
                        <Cloud size={14} /> Đích lưu trữ (SFTP / S3)
                      </button>
                      <button
                        type="button"
                        className={restoreSource.kind === 'remote' ? '' : 'secondary-light'}
                        onClick={() => selectRestoreSource({ kind: 'remote', target_id: '' })}
                      >
                        <Server size={14} /> Máy chủ khác (SFTP / FTP)
                      </button>
                    </div>
                  </div>

                  {restoreSource.kind === 'local' && (
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '10px' }}>
                      <span style={{ fontSize: '0.83rem', color: 'var(--text-muted)' }}>
                        Quét toàn bộ bản sao lưu trong <code>/var/backups/spanel/users/{serverHostname}/&lt;user&gt;</code> và thư mục <code>restore/</code>.
                      </span>
                      <div style={{ display: 'flex', gap: '8px' }}>
                        <label className="button secondary-light" style={{ margin: 0, cursor: 'pointer', display: 'inline-flex', alignItems: 'center', gap: '6px' }}>
                          <Upload size={14} /> Tải lên file User Backup (.tar.gz)
                          <input type="file" accept=".tar.gz,application/gzip" multiple onChange={uploadToRestoreSource} hidden disabled={restoreBusy} />
                        </label>
                        <button type="button" onClick={() => loadRestoreListing({ kind: 'local' })} disabled={restoreBusy}>
                          <RefreshCw size={14} className={restoreBusy ? 'spin' : ''} /> Quét danh sách Backup
                        </button>
                      </div>
                    </div>
                  )}

                  {restoreSource.kind === 'target' && (
                    <div style={{ display: 'flex', alignItems: 'end', gap: '12px', flexWrap: 'wrap' }}>
                      <label style={{ flex: 1, minWidth: '240px', margin: 0 }}>
                        <span style={{ display: 'block', fontSize: '0.78rem', color: 'var(--text-muted)', marginBottom: '4px' }}>Chọn kho lưu trữ SFTP / S3</span>
                        <select
                          value={restoreSource.target_id}
                          onChange={(e) => selectRestoreSource({ kind: 'target', target_id: e.target.value })}
                        >
                          <option value="">-- Chọn đích lưu trữ --</option>
                          {sftpTargets.map((tg) => (
                            <option key={tg.id} value={tg.id}>
                              [{String(tg.kind || 'sftp').toUpperCase()}] {tg.name} — {tg.kind === 's3' ? `${tg.endpoint}/${tg.bucket}` : `${tg.host}:${tg.remote_path}`}
                            </option>
                          ))}
                        </select>
                      </label>
                      <button type="button" onClick={() => loadRestoreListing()} disabled={restoreBusy || !restoreSource.target_id} style={{ height: '40px' }}>
                        <RefreshCw size={14} className={restoreBusy ? 'spin' : ''} /> Đọc danh sách từ Cloud/SFTP
                      </button>
                    </div>
                  )}

                  {restoreSource.kind === 'remote' && (
                    <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
                      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(160px, 1fr))', gap: '10px' }}>
                        <label style={{ margin: 0 }}>
                          <span style={{ display: 'block', fontSize: '0.78rem', color: 'var(--text-muted)', marginBottom: '4px' }}>Giao thức</span>
                          <select
                            value={restoreRemote.protocol}
                            onChange={(e) => {
                              const proto = e.target.value;
                              setRestoreRemote((p) => ({ ...p, protocol: proto, port: proto === 'sftp' ? '22' : '21' }));
                              setRestoreListing(null);
                            }}
                          >
                            <option value="sftp">SFTP (SSH)</option>
                            <option value="ftp">FTP</option>
                            <option value="ftps">FTPS (TLS)</option>
                          </select>
                        </label>
                        <label style={{ margin: 0 }}>
                          <span style={{ display: 'block', fontSize: '0.78rem', color: 'var(--text-muted)', marginBottom: '4px' }}>Máy chủ (Host / IP)</span>
                          <input
                            value={restoreRemote.host}
                            onChange={(e) => setRestoreRemote((p) => ({ ...p, host: e.target.value }))}
                            placeholder="backup.example.com"
                          />
                        </label>
                        <label style={{ margin: 0 }}>
                          <span style={{ display: 'block', fontSize: '0.78rem', color: 'var(--text-muted)', marginBottom: '4px' }}>Cổng (Port)</span>
                          <input
                            type="number"
                            value={restoreRemote.port}
                            onChange={(e) => setRestoreRemote((p) => ({ ...p, port: e.target.value }))}
                          />
                        </label>
                        <label style={{ margin: 0 }}>
                          <span style={{ display: 'block', fontSize: '0.78rem', color: 'var(--text-muted)', marginBottom: '4px' }}>Username</span>
                          <input
                            value={restoreRemote.username}
                            onChange={(e) => setRestoreRemote((p) => ({ ...p, username: e.target.value }))}
                          />
                        </label>
                        <label style={{ margin: 0 }}>
                          <span style={{ display: 'block', fontSize: '0.78rem', color: 'var(--text-muted)', marginBottom: '4px' }}>Password</span>
                          <input
                            type="password"
                            value={restoreRemote.password}
                            onChange={(e) => setRestoreRemote((p) => ({ ...p, password: e.target.value }))}
                          />
                        </label>
                        <label style={{ margin: 0 }}>
                          <span style={{ display: 'block', fontSize: '0.78rem', color: 'var(--text-muted)', marginBottom: '4px' }}>Thư mục chứa Backup</span>
                          <input
                            value={restoreRemote.path}
                            onChange={(e) => setRestoreRemote((p) => ({ ...p, path: e.target.value }))}
                            placeholder="/var/backups/spanel/users"
                          />
                        </label>
                      </div>
                      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '10px' }}>
                        {restoreListing?.host_key ? (
                          <span style={{ fontSize: '0.78rem', color: 'var(--text-muted)' }}>
                            SSH Host Key: <code>{restoreListing.host_key}</code>
                          </span>
                        ) : <span />}
                        <button
                          type="button"
                          onClick={() => loadRestoreListing()}
                          disabled={restoreBusy || !restoreRemote.host.trim() || !restoreRemote.username.trim()}
                        >
                          <RefreshCw size={14} className={restoreBusy ? 'spin' : ''} /> Kết nối & Liệt kê bản sao lưu
                        </button>
                      </div>
                    </div>
                  )}
                </div>

                {/* STEP 2 & 3: Pick Accounts and Confirm */}
                {restoreListing && (
                  <div style={{
                    padding: '16px', borderRadius: '10px', background: 'var(--surface-2)',
                    border: '1px solid var(--border-subtle)', marginBottom: '14px',
                  }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '12px', marginBottom: '12px' }}>
                      <div>
                        <div style={{ fontWeight: 700, fontSize: '0.9rem', color: 'var(--text-main)' }}>
                          Bước 2: Chọn tài khoản và phiên bản ngày sao lưu ({accounts.length} tài khoản)
                        </div>
                        <span style={{ fontSize: '0.8rem', color: 'var(--text-muted)' }}>
                          Đã chọn <strong>{restorePicks.length}</strong> tài khoản ({formatBytes(selectedBytes)}) ·
                          Ổ cứng máy chủ còn trống: <strong>{formatBytes(restoreListing.free_bytes || 0)}</strong>
                        </span>
                      </div>
                      <div style={{ position: 'relative', minWidth: '240px' }}>
                        <Search size={14} style={{ position: 'absolute', left: '10px', top: '50%', transform: 'translateY(-50%)', color: 'var(--text-muted)' }} />
                        <input
                          type="search"
                          placeholder="Lọc theo tên tài khoản..."
                          value={restoreFilter}
                          onChange={(e) => setRestoreFilter(e.target.value)}
                          style={{ paddingLeft: '32px', height: '34px', fontSize: '0.82rem' }}
                        />
                      </div>
                    </div>

                    {accounts.length === 0 ? (
                      <EmptyState
                        icon={FolderOpen}
                        title="Không tìm thấy bản sao lưu hợp lệ tại nguồn này"
                        subtitle="Hãy kiểm tra lại đường dẫn thư mục hoặc tải file .tar.gz lên."
                      />
                    ) : (
                      <>
                        <div className="pkg-table-wrap">
                          <table className="pkg-clean-table">
                            <thead>
                              <tr>
                                <th style={{ width: '40px' }}>
                                  <input
                                    type="checkbox"
                                    checked={allPicked}
                                    onChange={(e) => {
                                      const names = accounts.map(([acct]) => acct);
                                      setRestorePicks(e.target.checked
                                        ? Array.from(new Set([...restorePicks, ...names]))
                                        : restorePicks.filter((a) => !names.includes(a)));
                                    }}
                                  />
                                </th>
                                <th>Tài khoản (Account)</th>
                                <th>Máy chủ gốc (Hostname)</th>
                                <th>Loại khôi phục</th>
                                <th>Phiên bản bản sao lưu</th>
                                <th>Dung lượng</th>
                              </tr>
                            </thead>
                            <tbody>
                              {accounts.map(([acct, versions]) => {
                                const picked = restorePicks.includes(acct);
                                const chosen = versions.find((v) => v.key === restoreChoice[acct]) || versions[0];
                                const exists = existingSet.has(acct);
                                return (
                                  <tr key={acct}>
                                    <td>
                                      <input
                                        type="checkbox"
                                        checked={picked}
                                        onChange={(e) => setRestorePicks(
                                          e.target.checked ? [...restorePicks, acct] : restorePicks.filter((a) => a !== acct)
                                        )}
                                      />
                                    </td>
                                    <td><strong style={{ color: 'var(--text-main)' }}>{acct}</strong></td>
                                    <td>
                                      <code style={{ fontSize: '0.78rem', color: 'var(--accent-primary)' }}>
                                        {chosen.hostname || serverHostname}
                                      </code>
                                    </td>
                                    <td>
                                      <span style={{
                                        padding: '2px 8px', borderRadius: '999px', fontSize: '0.74rem', fontWeight: 600,
                                        background: exists ? 'rgba(245, 158, 11, 0.14)' : 'rgba(16, 185, 129, 0.14)',
                                        color: exists ? '#f59e0b' : '#10b981',
                                      }}>
                                        {exists ? 'Ghi đè (Overwrite)' : 'Tạo mới (New)'}
                                      </span>
                                    </td>
                                    <td>
                                      {versions.length === 1 ? (
                                        <span style={{ fontSize: '0.82rem', color: 'var(--text-muted)' }}>
                                          {chosen.modified ? new Date(chosen.modified).toLocaleString() : chosen.name}
                                        </span>
                                      ) : (
                                        <select
                                          value={chosen.key}
                                          onChange={(e) => setRestoreChoice({ ...restoreChoice, [acct]: e.target.value })}
                                          style={{ height: '32px', fontSize: '0.8rem' }}
                                        >
                                          {versions.map((v) => (
                                            <option key={v.key} value={v.key}>
                                              {v.hostname ? `[${v.hostname}] ` : ''}{v.modified ? new Date(v.modified).toLocaleString() : v.name} ({formatBytes(v.size)})
                                            </option>
                                          ))}
                                        </select>
                                      )}
                                    </td>
                                    <td style={{ fontWeight: 600 }}>{formatBytes(chosen.size)}</td>
                                  </tr>
                                );
                              })}
                            </tbody>
                          </table>
                        </div>

                        <div style={{
                          marginTop: '14px', paddingTop: '14px', borderTop: '1px solid var(--border-subtle)',
                          display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '12px',
                        }}>
                          <div style={{ fontSize: '0.83rem', color: 'var(--text-muted)' }}>
                            {overwriteCount > 0 ? (
                              <span style={{ color: '#f59e0b', fontWeight: 600 }}>
                                Lưu ý: {overwriteCount} tài khoản đã tồn tại trên máy chủ sẽ được ghi đè dữ liệu.
                              </span>
                            ) : (
                              <span>Sẵn sàng khôi phục {restorePicks.length} tài khoản đã chọn.</span>
                            )}
                          </div>
                          <button
                            type="button"
                            onClick={runRestoreFromSource}
                            disabled={restoreBusy || restorePicks.length === 0 || ['running', 'queued'].includes(restoreJob?.status)}
                            style={{ height: '40px', padding: '0 20px', fontWeight: 700 }}
                          >
                            <ArchiveRestore size={16} />
                            Bắt đầu Khôi phục ({restorePicks.length} tài khoản)
                          </button>
                        </div>
                      </>
                    )}
                  </div>
                )}

                {/* STEP 4: Restore Progress & Results */}
                {restoreJob && (
                  <div style={{
                    padding: '16px', borderRadius: '10px',
                    background: restoreJob.status === 'error' ? 'rgba(239, 68, 68, 0.06)' : 'rgba(13, 148, 136, 0.06)',
                    border: `1px solid ${restoreJob.status === 'error' ? 'rgba(239, 68, 68, 0.28)' : 'rgba(13, 148, 136, 0.28)'}`,
                  }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '12px' }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: '8px', fontWeight: 700, fontSize: '0.92rem', color: 'var(--text-main)' }}>
                        {['running', 'queued'].includes(restoreJob.status) ? (
                          <RefreshCw size={16} className="spin" style={{ color: 'var(--accent-primary)' }} />
                        ) : restoreJob.status === 'done' ? (
                          <CheckCircle size={16} style={{ color: '#10b981' }} />
                        ) : (
                          <AlertCircle size={16} style={{ color: '#ef4444' }} />
                        )}
                        <span>
                          Tiến trình Khôi phục:{' '}
                          {(restoreJob.results || []).filter((r) => r.status === 'done').length} / {(restoreJob.results || []).length} hoàn tất
                        </span>
                      </div>
                      <span style={{
                        padding: '2px 9px', borderRadius: '999px', fontSize: '0.75rem', fontWeight: 700,
                        background: restoreJob.status === 'done' ? 'rgba(16, 185, 129, 0.16)' : restoreJob.status === 'error' ? 'rgba(239, 68, 68, 0.16)' : 'rgba(13, 148, 136, 0.16)',
                        color: restoreJob.status === 'done' ? '#10b981' : restoreJob.status === 'error' ? '#ef4444' : 'var(--accent-primary)',
                      }}>
                        {restoreJob.status.toUpperCase()}
                      </span>
                    </div>

                    <div className="pkg-table-wrap">
                      <table className="pkg-clean-table">
                        <thead>
                          <tr>
                            <th>Tài khoản / File</th>
                            <th>Trạng thái</th>
                            <th>Chi tiết kết quả</th>
                          </tr>
                        </thead>
                        <tbody>
                          {(restoreJob.results || []).map((row, idx) => (
                            <tr key={`${row.name}-${idx}`}>
                              <td><strong style={{ color: 'var(--text-main)' }}>{row.username || row.name}</strong></td>
                              <td>
                                <span style={{
                                  padding: '2px 8px', borderRadius: '999px', fontSize: '0.74rem', fontWeight: 600,
                                  background: row.status === 'done'
                                    ? 'rgba(16, 185, 129, 0.14)'
                                    : row.status === 'failed'
                                    ? 'rgba(239, 68, 68, 0.14)'
                                    : 'rgba(13, 148, 136, 0.14)',
                                  color: row.status === 'done' ? '#10b981' : row.status === 'failed' ? '#ef4444' : 'var(--accent-primary)',
                                }}>
                                  {row.status}
                                </span>
                              </td>
                              <td style={{ fontSize: '0.82rem', color: 'var(--text-muted)' }}>
                                {row.detail || '—'}
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </div>
                )}
              </div>
            )}

            {/* Off-Server Backup Destinations Card (Admin/Reseller & Personal End-User) */}
            <div className="pkg-card" style={{ padding: '20px 22px' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '12px', marginBottom: '16px' }}>
                <div>
                  <h4 style={{ margin: 0, fontSize: '0.98rem', fontWeight: 700, color: 'var(--text-main)', display: 'flex', alignItems: 'center', gap: '8px' }}>
                    <Cloud size={18} style={{ color: 'var(--accent-primary)' }} />
                    {isManager
                      ? 'Quản lý Đích lưu trữ từ xa (Off-Server Backup Destinations: SFTP & S3)'
                      : 'Cấu hình Kho lưu trữ Sao lưu cá nhân (My Personal SFTP / S3 Storage)'}
                  </h4>
                  <p style={{ margin: '3px 0 0', fontSize: '0.82rem', color: 'var(--text-muted)' }}>
                    {isManager ? (
                      <>
                        Tự động phân tách thư mục theo chuẩn Multi-Server: <code>&lt;Prefix/RemotePath&gt;/{serverHostname}/&lt;username&gt;/&lt;file&gt;.tar.gz</code>.
                      </>
                    ) : (
                      <>
                        Kết nối máy chủ SFTP hoặc S3 Object Storage riêng của bạn để đẩy các bản sao lưu Website/User về tài khoản lưu trữ cá nhân.
                      </>
                    )}
                  </p>
                </div>
                <button
                  type="button"
                  className={showDestinationForm ? 'secondary-light' : ''}
                  onClick={() => setShowDestinationForm((v) => !v)}
                >
                  {showDestinationForm ? <X size={15} /> : <Plus size={15} />}
                  {showDestinationForm ? 'Đóng biểu mẫu' : 'Thêm Đích lưu trữ (SFTP / S3)'}
                </button>
              </div>

              {showDestinationForm && (
                <form
                  onSubmit={createSftpTarget}
                  style={{
                    padding: '18px', borderRadius: '10px', background: 'var(--surface-2)',
                    border: '1px solid var(--border-subtle)', marginBottom: '18px',
                    display: 'flex', flexDirection: 'column', gap: '14px',
                  }}
                >
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '10px' }}>
                    <div style={{ display: 'flex', gap: '8px' }}>
                      <button
                        type="button"
                        className={sftpTargetForm.kind === 'sftp' ? '' : 'secondary-light'}
                        onClick={() => setSftpTargetForm((p) => ({ ...p, kind: 'sftp' }))}
                      >
                        <Server size={14} /> Máy chủ SFTP (SSH)
                      </button>
                      <button
                        type="button"
                        className={sftpTargetForm.kind === 's3' ? '' : 'secondary-light'}
                        onClick={() => setSftpTargetForm((p) => ({ ...p, kind: 's3' }))}
                      >
                        <Cloud size={14} /> S3-Compatible Object Storage
                      </button>
                    </div>

                    {sftpTargetForm.kind === 's3' && (
                      <div style={{ display: 'flex', gap: '6px', flexWrap: 'wrap' }}>
                        {S3_PRESETS.map((preset) => (
                          <button
                            key={preset.id}
                            type="button"
                            className="secondary-light"
                            style={{ fontSize: '0.75rem', padding: '4px 10px' }}
                            onClick={() => setSftpTargetForm((p) => ({
                              ...p,
                              endpoint: preset.endpoint,
                              region: preset.region,
                            }))}
                          >
                            {preset.label}
                          </button>
                        ))}
                      </div>
                    )}
                  </div>

                  {sftpTargetForm.kind === 'sftp' ? (
                    <>
                      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))', gap: '12px' }}>
                        <label style={{ margin: 0 }}>
                          <span style={{ display: 'block', fontSize: '0.78rem', color: 'var(--text-muted)', marginBottom: '4px' }}>Tên gợi nhớ</span>
                          <input value={sftpTargetForm.name} onChange={(e) => setSftpTargetForm((p) => ({ ...p, name: e.target.value }))} placeholder="Storage Box Hetzner" required />
                        </label>
                        <label style={{ margin: 0 }}>
                          <span style={{ display: 'block', fontSize: '0.78rem', color: 'var(--text-muted)', marginBottom: '4px' }}>SFTP Host / IP</span>
                          <input value={sftpTargetForm.host} onChange={(e) => setSftpTargetForm((p) => ({ ...p, host: e.target.value }))} placeholder="u12345.your-storagebox.de" required />
                        </label>
                        <label style={{ margin: 0 }}>
                          <span style={{ display: 'block', fontSize: '0.78rem', color: 'var(--text-muted)', marginBottom: '4px' }}>Port</span>
                          <input type="number" value={sftpTargetForm.port} onChange={(e) => setSftpTargetForm((p) => ({ ...p, port: e.target.value }))} required />
                        </label>
                        <label style={{ margin: 0 }}>
                          <span style={{ display: 'block', fontSize: '0.78rem', color: 'var(--text-muted)', marginBottom: '4px' }}>Username</span>
                          <input value={sftpTargetForm.username} onChange={(e) => setSftpTargetForm((p) => ({ ...p, username: e.target.value }))} placeholder="backup-user" required />
                        </label>
                        <label style={{ margin: 0 }}>
                          <span style={{ display: 'block', fontSize: '0.78rem', color: 'var(--text-muted)', marginBottom: '4px' }}>Remote Path gốc</span>
                          <input value={sftpTargetForm.remote_path} onChange={(e) => setSftpTargetForm((p) => ({ ...p, remote_path: e.target.value }))} placeholder="/backups/spanel" required />
                        </label>
                        <label style={{ margin: 0 }}>
                          <span style={{ display: 'block', fontSize: '0.78rem', color: 'var(--text-muted)', marginBottom: '4px' }}>Mật khẩu SFTP (Hoặc dùng SSH Key)</span>
                          <input type="password" value={sftpTargetForm.password} onChange={(e) => setSftpTargetForm((p) => ({ ...p, password: e.target.value }))} placeholder="••••••••" />
                        </label>
                      </div>
                      <label style={{ margin: 0 }}>
                        <span style={{ display: 'block', fontSize: '0.78rem', color: 'var(--text-muted)', marginBottom: '4px' }}>SSH Private Key (Tùy chọn)</span>
                        <textarea rows={2} value={sftpTargetForm.private_key} onChange={(e) => setSftpTargetForm((p) => ({ ...p, private_key: e.target.value }))} placeholder="-----BEGIN OPENSSH PRIVATE KEY-----" />
                      </label>
                    </>
                  ) : (
                    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: '12px' }}>
                      <label style={{ margin: 0 }}>
                        <span style={{ display: 'block', fontSize: '0.78rem', color: 'var(--text-muted)', marginBottom: '4px' }}>Tên gợi nhớ</span>
                        <input value={sftpTargetForm.name} onChange={(e) => setSftpTargetForm((p) => ({ ...p, name: e.target.value }))} placeholder="Cloudflare R2 Backups" required />
                      </label>
                      <label style={{ margin: 0 }}>
                        <span style={{ display: 'block', fontSize: '0.78rem', color: 'var(--text-muted)', marginBottom: '4px' }}>S3 Endpoint</span>
                        <input value={sftpTargetForm.endpoint} onChange={(e) => setSftpTargetForm((p) => ({ ...p, endpoint: e.target.value }))} placeholder="s3.us-west-004.backblazeb2.com" required />
                      </label>
                      <label style={{ margin: 0 }}>
                        <span style={{ display: 'block', fontSize: '0.78rem', color: 'var(--text-muted)', marginBottom: '4px' }}>Region</span>
                        <input value={sftpTargetForm.region} onChange={(e) => setSftpTargetForm((p) => ({ ...p, region: e.target.value }))} placeholder="us-east-1" />
                      </label>
                      <label style={{ margin: 0 }}>
                        <span style={{ display: 'block', fontSize: '0.78rem', color: 'var(--text-muted)', marginBottom: '4px' }}>Bucket Name</span>
                        <input value={sftpTargetForm.bucket} onChange={(e) => setSftpTargetForm((p) => ({ ...p, bucket: e.target.value }))} placeholder="my-spanel-backups" required />
                      </label>
                      <label style={{ margin: 0 }}>
                        <span style={{ display: 'block', fontSize: '0.78rem', color: 'var(--text-muted)', marginBottom: '4px' }}>Folder Prefix (Để trống nếu lưu từ gốc)</span>
                        <input value={sftpTargetForm.prefix} onChange={(e) => setSftpTargetForm((p) => ({ ...p, prefix: e.target.value }))} placeholder={isManager ? `Trống -> ${serverHostname}/<user>/...` : 'backups'} />
                      </label>
                      <label style={{ margin: 0 }}>
                        <span style={{ display: 'block', fontSize: '0.78rem', color: 'var(--text-muted)', marginBottom: '4px' }}>Access Key ID</span>
                        <input value={sftpTargetForm.access_key} onChange={(e) => setSftpTargetForm((p) => ({ ...p, access_key: e.target.value }))} required />
                      </label>
                      <label style={{ margin: 0 }}>
                        <span style={{ display: 'block', fontSize: '0.78rem', color: 'var(--text-muted)', marginBottom: '4px' }}>Secret Access Key</span>
                        <input type="password" value={sftpTargetForm.secret_key} onChange={(e) => setSftpTargetForm((p) => ({ ...p, secret_key: e.target.value }))} required />
                      </label>
                    </div>
                  )}

                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '10px' }}>
                    <span style={{ fontSize: '0.78rem', color: 'var(--text-muted)' }}>
                      {isManager ? (
                        <>
                          Đường dẫn thực tế khi đẩy lên:{' '}
                          <code style={{ color: 'var(--accent-primary)' }}>
                            {sftpTargetForm.kind === 's3'
                              ? `${sftpTargetForm.bucket || '<bucket>'}/${sftpTargetForm.prefix ? `${sftpTargetForm.prefix.replace(/^\/+|\/+$/g, '')}/` : ''}${serverHostname}/<user>/<file>.tar.gz`
                              : `${(sftpTargetForm.remote_path || '/backups').replace(/\/+$/g, '')}/${serverHostname}/<user>/<file>.tar.gz`}
                          </code>
                        </>
                      ) : (
                        <>Dữ liệu sao lưu sẽ được đẩy trực tiếp vào đường dẫn bạn chỉ định.</>
                      )}
                    </span>
                    <div style={{ display: 'flex', gap: '8px' }}>
                      <button type="button" className="secondary-light" onClick={() => setShowDestinationForm(false)}>
                        Hủy
                      </button>
                      <button type="submit" disabled={loading}>
                        <Check size={15} /> Lưu & Kiểm tra kết nối
                      </button>
                    </div>
                  </div>
                </form>
              )}

              <div className="pkg-table-wrap">
                <table className="pkg-clean-table">
                  <thead>
                    <tr>
                      <th>Tên Đích lưu trữ</th>
                      <th>Loại</th>
                      <th>Máy chủ / Bucket</th>
                      <th>Cấu trúc đường dẫn lưu trữ</th>
                      <th>Bảo mật kết nối</th>
                      <th style={{ textAlign: 'right' }}>Thao tác</th>
                    </tr>
                  </thead>
                  <tbody>
                    {sftpTargets.map((target) => {
                      const basePrefix = target.kind === 's3'
                        ? (target.prefix || '').replace(/^\/+|\/+$/g, '')
                        : (target.remote_path || '/').replace(/\/+$/g, '');
                      const structurePreview = isManager
                        ? `${basePrefix ? `${basePrefix}/` : ''}${serverHostname}/<user>/<file>.tar.gz`
                        : `${basePrefix || '/'}/<file>.tar.gz`;
                      return (
                        <tr key={target.id}>
                          <td><strong style={{ color: 'var(--text-main)' }}>{target.name}</strong></td>
                          <td>
                            <span style={{
                              padding: '2px 8px', borderRadius: '999px', fontSize: '0.74rem', fontWeight: 700,
                              background: target.kind === 's3' ? 'rgba(59, 130, 246, 0.14)' : 'rgba(13, 148, 136, 0.14)',
                              color: target.kind === 's3' ? '#3b82f6' : 'var(--accent-primary)',
                            }}>
                              {String(target.kind || 'sftp').toUpperCase()}
                            </span>
                          </td>
                          <td>
                            {target.kind === 's3'
                              ? <span><code>{target.endpoint}</code> · Bucket: <strong>{target.bucket}</strong></span>
                              : <span><code>{target.username}@{target.host}:{target.port}</code></span>}
                          </td>
                          <td><code style={{ color: 'var(--accent-primary)' }}>{structurePreview}</code></td>
                          <td style={{ fontSize: '0.8rem', color: 'var(--text-muted)' }}>
                            {target.kind === 's3'
                              ? (target.secure ? 'HTTPS (TLS)' : 'HTTP')
                              : (target.host_key_fingerprint ? `Pinned: ${target.host_key_fingerprint.slice(0, 22)}...` : 'Ghim SSH Key ở lần kết nối đầu')}
                          </td>
                          <td style={{ textAlign: 'right' }}>
                            <button
                              type="button"
                              className="danger"
                              onClick={() => deleteSftpTarget(target)}
                              disabled={loading}
                              style={{ padding: '6px 10px' }}
                            >
                              <Trash2 size={14} />
                            </button>
                          </td>
                        </tr>
                      );
                    })}
                    {sftpTargets.length === 0 && (
                      <tr>
                        <td colSpan="6">
                          <EmptyState
                            icon={Cloud}
                            title="Chưa cấu hình đích lưu trữ SFTP / S3 nào"
                            subtitle="Nhấn Thêm Đích lưu trữ ở góc phải để kết nối SFTP hoặc S3 Object Storage."
                          />
                        </td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </div>
            </div>
          </div>
        );
      })()}
    </div>
  );
}
