import React, { useState, useEffect } from 'react';
import { Archive, ArchiveRestore, ArrowLeft, Ban, Bot, Boxes, Check, ChevronDown, Clock, Code2, Copy, Cpu, Database, Dices, ExternalLink, Eye, FileText, FolderOpen, Globe, HardDrive, Home, Image, KeyRound, Lock, LogIn, LogOut, MemoryStick, Menu, Moon, MoveRight, Network, Pencil, Save, Search, Server, Settings as SettingsIcon, Shield, Sun, Trash2, TerminalIcon, Users, X, RefreshCw, Plus, Download, Upload, Play, Square, RotateCcw, AlertCircle, Activity, BrickWall, Bug, LockKeyhole, PackageOpen, ScrollText, ShieldAlert, CheckCircle, Zap, Bell, Mail, Send, Cloud, Inbox, Forward, ShieldCheck } from 'lucide-react';
import { EmptyState } from '../components/EmptyState.jsx';
import { useAppContext } from '../contexts/AppContext.jsx';
import { t } from '../i18n.js';
import { WebsiteSelect } from '../components/WebsiteSelect.jsx';
import { formatBytes, BACKUP_SCHEDULE_PRESETS, isPresetSchedule } from '../utils/helpers.js';

export function BackupsPage({ request, isAdmin, isReseller, API, csvCell }) {
  const {
    loading, setLoading, setError, setNotice, users, selectedWebsiteId, setSelectedWebsiteId, websites, setWebsites,
    readCookie, handleAuthExpired, formatApiError, loadUsers, loadDatabases, generateRandomPassword,
    refreshAll, daFileInputRef, migratePanelType, setMigratePanelType,
  } = useAppContext();
  
  const [backupTab, setBackupTab] = useState('website');

  const [restoreRemote, setRestoreRemote] = useState({ protocol: 'sftp', host: '', port: '', username: '', password: '', path: '/' });
  const [daImportJob, setDaImportJob] = useState(null);


  const [restoreListing, setRestoreListing] = useState(null);
  const [restoreChoice, setRestoreChoice] = useState({});
  const [restorePicks, setRestorePicks] = useState([]);
  const [restoreFilter, setRestoreFilter] = useState('');
  const [restoreJob, setRestoreJob] = useState(null);

  const [backups, setBackups] = useState([]);
  const [backupJobs, setBackupJobs] = useState([]);
  const [userBackups, setUserBackups] = useState([]);
  const [restoreBackups, setRestoreBackups] = useState([]);
  const [restoreSource, setRestoreSource] = useState('local');
  const [restoreBackupDir, setRestoreBackupDir] = useState('');
  const [selectedBackupUserId, setSelectedBackupUserId] = useState('');
  const [backupSchedules, setBackupSchedules] = useState([]);
  const [newBackupSchedule, setNewBackupSchedule] = useState({ user_ids: [], all_users: false, schedule: '0 2 * * *', target_id: '', name_suffix: 'full_date', retention: 7 });
  const [sftpTargets, setSftpTargets] = useState([]);
  const [selectedSftpTargetId, setSelectedSftpTargetId] = useState('');
  const [newSftpTarget, setNewSftpTarget] = useState({ name: '', kind: 'sftp', host: '', port: 22, username: '', password: '', private_key: '', remote_path: '/backups/spanel', endpoint: '', region: '', bucket: '', access_key: '', secret_key: '', prefix: '', secure: true });
  const [restoreTargetId, setRestoreTargetId] = useState('');
  const [daBackups, setDaBackups] = useState([]);
  const [selectedDaBackups, setSelectedDaBackups] = useState([]);
  const [daReplaceExisting, setDaReplaceExisting] = useState(false);
  const [daBulkImportJob, setDaBulkImportJob] = useState(null);
  const [daScanResult, setDaScanResult] = useState(null);
  const [backupScheduleCustom, setBackupScheduleCustom] = useState(false);

  useEffect(() => {
    if (isAdmin) {
      loadSftpTargets();
      if (users.length === 0) loadUsers();
    } else if (isReseller && users.length === 0) {
      loadUsers();
    }
  }, [isAdmin, isReseller]);
  useEffect(() => { if (backupTab === 'website' && selectedWebsiteId) { listBackups(); loadBackupJobs(); } }, [backupTab, selectedWebsiteId]);
  useEffect(() => { if (backupTab === 'user') { listUserBackups(); } }, [backupTab]);
  useEffect(() => { if (backupTab === 'schedule' && isAdmin) { loadBackupSchedules(); loadSftpTargets(); } }, [backupTab]);
  useEffect(() => { if ((backupTab === 'destination' || backupTab === 'targets') && isAdmin) { loadSftpTargets(); } }, [backupTab]);
  useEffect(() => { if (backupTab === 'restore') { listRestoreSource('local'); } }, [backupTab]);
  useEffect(() => { if (backupTab === 'migrate-panel' || backupTab === 'da-import') { listDaBackups(); setSelectedDaBackups([]); setDaBulkImportJob(null); } }, [backupTab]);
  useEffect(() => { if (backupTab === 'restore' && isAdmin && restoreSource === 'local') listRestoreSource('local'); }, [backupTab, restoreSource]);

  async function createBackup() {
    const data = await request('/maintenance/backup', { method: 'POST', body: JSON.stringify({ website_id: Number(selectedWebsiteId) }) }, t('Queueing backup...'));
    if (data?.job_id) { setNotice(t('Backup queued. It will keep running on the server.')); await loadBackupJobs(); }
    else if (data?.backup_file) { setNotice(`Created backup: ${data.backup_file}`); await listBackups(); }
  }

  async function listBackups() {
    if (!selectedWebsiteId) return;
    const data = await request(`/maintenance/backups/${selectedWebsiteId}`);
    if (data?.items) setBackups(data.items);
  }

  function currentRestoreSource() {
    if (restoreSource === 'target') return restoreTargetId ? `target:${restoreTargetId}` : '';
    return restoreSource;
  }

  function toggleRestorePick(id) {
    setRestorePicks(prev => prev.includes(id) ? prev.filter(entry => entry !== id) : [...prev, id]);
  }

  function chosenRestoreBackup(group) {
    return group.backups.find(item => item.key === restoreChoice[group.id]) || group.backups[0];
  }

  async function runRestore() {
    const picked = restoreGroupsOf(restoreListing).filter(group => restorePicks.includes(group.id));
    if (picked.length === 0) return;
    const names = picked.map(group => group.username || chosenRestoreBackup(group).name).join(', ');
    if (!confirm(`${t('Restore {n} user(s)? An account that already exists on this server is overwritten with what is in its backup.', { n: picked.length })}\n\n${names}`)) return;
    const data = await request('/maintenance/restore/run', {
      method: 'POST',
      body: JSON.stringify({
        source: restoreSourceBody(restoreListing.source),
        items: picked.map(group => {
          const item = chosenRestoreBackup(group);
          return { key: item.key, size: item.size, username: group.username };
        }),
      }),
    }, t('Starting the restore...'));
    if (data?.job_id) {
      setRestoreJob(data);
      setRestorePicks([]);
      setNotice(t('Restore started. It keeps running on the server; this page shows each user as it goes.'));
      loadBackupJobs();
    }
  }

  useEffect(() => {
    if (!restoreJob) {
      if (restoreListing && restoreListing.source !== 'remote') listRestoreSource(restoreListing.source);
      return undefined;
    }
    const timer = setInterval(async () => {
      const data = await request(`/maintenance/restore/jobs/${restoreJob.job_id}`, { silent: true });
      if (data) {
        setRestoreJob(data);
        if (['completed', 'failed'].includes(data.status)) {
          clearInterval(timer);
          setNotice(data.status === 'completed' ? t('Restore finished.') : t('Restore failed.'));
          loadBackupJobs();
          if (restoreListing && restoreListing.source !== 'remote') listRestoreSource(restoreListing.source);
        }
      }
    }, 2000);
    return () => clearInterval(timer);
  }, [restoreJob?.job_id, restoreJob?.status]);

  const userNameById = id => users.find(user => String(user.id) === String(id))?.username || `User #${id}`;
  const scheduleUserLabel = item => {
    if (item.all_users) return 'All users';
    const ids = (item.user_ids && item.user_ids.length > 0) ? item.user_ids : (item.user_id ? [item.user_id] : []);
    return ids.length ? ids.map(userNameById).join(', ') : 'No users';
  };

  function pollBackupSchedule(scheduleId, attempts = 60) {
    if (attempts <= 0) return;
    window.setTimeout(async () => {
      const rows = await request('/maintenance/backup-schedules', { silent: true });
      if (!rows) return;
      setBackupSchedules(rows);
      const row = rows.find(entry => entry.id === scheduleId);
      if (!row) return;
      if (row.last_status === 'running') return pollBackupSchedule(scheduleId, attempts - 1);
      if (row.last_status === 'error') setError(`Schedule failed - ${row.last_message || 'no detail'}`);
      else setNotice(`Schedule finished - ${row.last_message || 'ok'}`);
    }, 5000);
  }

  function restoreSourceBody(source) {
    if (source === 'local') return { kind: 'local' };
    if (source.startsWith('target:')) return { kind: 'target', target_id: Number(source.slice(7)) };
    return {
      kind: 'remote', ...restoreRemote, port: restoreRemote.port ? Number(restoreRemote.port) : null,
      host_key: restoreListing?.source === 'remote' ? (restoreListing.host_key?.fingerprint || '') : '',
    };
  }

  function chooseRestoreSource(kind) {
    setRestoreSource(kind);
    setRestoreListing(null);
    setRestorePicks([]);
    setRestoreChoice({});
    if (kind === 'local') listRestoreSource('local');
    if (kind === 'target') {
      const first = restoreTargetId || String(sftpTargets.find(target => target.is_active !== false)?.id || '');
      setRestoreTargetId(first);
      if (first) listRestoreSource(`target:${first}`);
    }
  }

  function editRestoreRemote(changes) {
    setRestoreRemote(prev => ({ ...prev, ...changes }));
    if (restoreListing?.source === 'remote') { setRestoreListing(null); setRestorePicks([]); }
  }

  function restoreGroupsOf(listing) {
    const groups = new Map();
    for (const item of listing?.items || []) {
      const id = item.username || `file:${item.key}`;
      if (!groups.has(id)) groups.set(id, { id, username: item.username, backups: [] });
      groups.get(id).backups.push(item);
    }
    return [...groups.values()].sort((a, b) => (!a.username - !b.username) || (a.username || a.id).localeCompare(b.username || b.id));
  }

  async function pollDaImportJob(jobId) {
    let attempts = 0;
    const maxAttempts = 360;
    while (attempts < maxAttempts) {
      await new Promise(resolve => setTimeout(resolve, 5000));
      const data = await request(`/maintenance/da-import/jobs/${jobId}`, { silent: true });
      if (!data) { attempts++; continue; }
      setDaImportJob(data);
      if (data.status === 'completed') { setNotice(t('DA import completed successfully!')); await listDaBackups(); return; }
      if (data.status === 'failed') { setError(`DA import failed: ${data.error || 'Unknown error'}`); return; }
      attempts++;
    }
  }

  function toggleDaBackupSelect(path) {
    setSelectedDaBackups(prev => prev.includes(path) ? prev.filter(p => p !== path) : [...prev, path]);
  }

  function toggleSelectAllDaBackups() {
    setSelectedDaBackups(prev => prev.length === daBackups.length ? [] : daBackups.map(f => f.path));
  }

  async function loadBackupJobs() {
    const data = await request('/maintenance/backup-jobs');
    if (data?.jobs) {
      const visibleJobs = data.jobs.filter(job => job.status !== 'done');
      const hasActive = data.jobs.some(job => ['queued', 'running'].includes(job.status));
      setBackupJobs(prev => {
        const hadActive = prev.some(job => ['queued', 'running'].includes(job.status));
        if (hadActive && !hasActive) {
          setTimeout(() => {
            if (selectedWebsiteId) listBackups();
            if (selectedBackupUserId) listUserBackups(selectedBackupUserId);
          }, 0);
        }
        return visibleJobs;
      });
    }
  }

  async function refreshBackupArea() {
    await listBackups();
    await loadBackupJobs();
    if (selectedBackupUserId) await listUserBackups(selectedBackupUserId);
  }

  async function refreshUserBackupArea() {
    await loadUsers();
    await loadRestoreBackups();
    await loadBackupJobs();
    if (selectedBackupUserId) await listUserBackups(selectedBackupUserId);
  }

  async function refreshScheduledBackupArea() {
    await loadUsers();
    await loadSftpTargets();
    await loadBackupSchedules();
    await loadBackupJobs();
  }

  async function listUserBackups(userId = selectedBackupUserId) {
    if (!userId) return;
    const data = await request(`/maintenance/user-backups/${userId}`);
    if (data?.items) setUserBackups(data.items);
  }

  async function createUserBackup() {
    if (!selectedBackupUserId) return;
    const body = {
      user_id: Number(selectedBackupUserId),
      target_id: selectedSftpTargetId ? Number(selectedSftpTargetId) : null,
    };
    const data = await request('/maintenance/user-backup', { method: 'POST', body: JSON.stringify(body) }, t('Queueing full user backup...'));
    if (data?.job_id) { setNotice(t('Full user backup queued. It will keep running on the server.')); await loadBackupJobs(); }
    else if (data?.backup_file) {
      setNotice(data.remote_file ? `Full user backup uploaded: ${data.remote_file}` : `Created full user backup: ${data.backup_file}`);
      await listUserBackups();
    }
  }

  async function loadBackupSchedules() {
    const data = await request('/maintenance/backup-schedules');
    if (data) setBackupSchedules(data);
  }

  async function loadRestoreBackups() {
    const data = await request('/maintenance/user-restore-backups');
    if (data?.items) setRestoreBackups(data.items);
    if (data?.directory) setRestoreBackupDir(data.directory);
  }

  async function createBackupSchedule() {
    const selectedUserIds = (newBackupSchedule.user_ids || []).map(Number).filter(Boolean);
    if (!newBackupSchedule.all_users && selectedUserIds.length === 0) return;
    const body = {
      user_id: selectedUserIds[0] || null,
      user_ids: newBackupSchedule.all_users ? [] : selectedUserIds,
      all_users: !!newBackupSchedule.all_users,
      schedule: newBackupSchedule.schedule,
      target_id: newBackupSchedule.target_id ? Number(newBackupSchedule.target_id) : null,
      name_suffix: newBackupSchedule.name_suffix || 'full_date',
      retention: Number(newBackupSchedule.retention || 7),
      is_active: true,
    };
    const data = await request('/maintenance/backup-schedules', { method: 'POST', body: JSON.stringify(body) }, t('Saving backup schedule...'));
    if (data) {
      setNotice(t('Backup schedule saved.'));
      await loadBackupSchedules();
    }
  }

  async function runBackupScheduleNow(item) {
    const who = scheduleUserLabel(item);
    if (!confirm(`Run this schedule now?\n\n${who} - ${item.schedule}\n\nThis is the real thing: the same accounts, the same destination and the same stored name. Only the timing is skipped.`)) return;
    const data = await request(`/maintenance/backup-schedules/${item.id}/run`, { method: 'POST' }, t('Starting...'));
    if (data) {
      setNotice(data.detail || 'Running now.');
      await loadBackupSchedules();
      pollBackupSchedule(item.id);
    }
  }

  async function deleteBackupSchedule(id) {
    if (!confirm(t('Delete this backup schedule?'))) return;
    const data = await request(`/maintenance/backup-schedules/${id}`, { method: 'DELETE' }, t('Deleting backup schedule...'));
    if (data) await loadBackupSchedules();
  }

  async function loadSftpTargets() {
    const data = await request('/maintenance/sftp-targets');
    if (data) {
      setSftpTargets(data);
      if (!selectedSftpTargetId && data[0]) setSelectedSftpTargetId(String(data[0].id));
    }
  }

  async function createSftpTarget() {
    const isS3 = newSftpTarget.kind === 's3';
    const body = {
      ...newSftpTarget,
      port: Number(newSftpTarget.port || 22),
      password: newSftpTarget.password || null,
      private_key: newSftpTarget.private_key || null,
      secret_key: newSftpTarget.secret_key || null,
      endpoint: newSftpTarget.endpoint || null,
      region: newSftpTarget.region || null,
      bucket: newSftpTarget.bucket || null,
      access_key: newSftpTarget.access_key || null,
      prefix: newSftpTarget.prefix || null,
    };
    // An S3 target is checked against the bucket before it is saved, so this
    // can take a moment and can come back refused. That is the point.
    const data = await request('/maintenance/sftp-targets', { method: 'POST', body: JSON.stringify(body) },
      isS3 ? 'Checking the bucket...' : 'Saving SFTP target...');
    if (data) {
      setNotice(`Saved ${isS3 ? 'S3' : 'SFTP'} target ${data.name}`);
      setNewSftpTarget({ name: '', kind: newSftpTarget.kind, host: '', port: 22, username: '', password: '', private_key: '', remote_path: '/backups/spanel', endpoint: '', region: '', bucket: '', access_key: '', secret_key: '', prefix: '', secure: true });
      await loadSftpTargets();
    }
  }

  async function listRestoreSource(source) {
    setRestoreListing(null);
    setRestorePicks([]);
    setRestoreChoice({});
    const data = await request('/maintenance/restore/list', {
      method: 'POST', body: JSON.stringify({ source: restoreSourceBody(source) }),
    }, t('Looking for backups...'));
    if (data) setRestoreListing({ ...data, source });
  }

  async function uploadRestoreBackups(files) {
    const chosen = Array.from(files || []);
    const source = currentRestoreSource();
    if (chosen.length === 0 || !source) return;
    const form = new FormData();
    chosen.forEach(file => form.append('files', file));
    form.append('source', JSON.stringify(restoreSourceBody(source)));
    const data = await request('/maintenance/restore/upload', { method: 'POST', body: form },
      t('Uploading {n} backup(s)...', { n: chosen.length }));
    if (data) {
      setNotice(t('Uploaded {n} backup(s).', { n: (data.uploaded || []).length }));
      await listRestoreSource(source);
    }
  }

  async function deleteSftpTarget(id) {
    if (!confirm(t('Delete this SFTP target?'))) return;
    const data = await request(`/maintenance/sftp-targets/${id}`, { method: 'DELETE' }, t('Deleting SFTP target...'));
    if (data) await loadSftpTargets();
  }

  async function restoreBackup(file) {
    if (!selectedWebsiteId) return;
    if (!confirm(`Restore this backup to the current website?\n${file}`)) return;
    await request('/maintenance/restore', { method: 'POST', body: JSON.stringify({ website_id: Number(selectedWebsiteId), backup_file: file }) }, t('Restoring backup...'));
  }

  async function downloadBackup(file) {
    if (!selectedWebsiteId) return;
    try {
      setError(''); setLoading('Downloading backup...');
      const res = await fetch(`${API}/maintenance/backups/${selectedWebsiteId}/download?backup_file=${encodeURIComponent(file)}`, { credentials: 'include' });
      if (!res.ok) { const data = await res.json().catch(() => ({})); if (handleAuthExpired(res.status, data.detail)) return; setError(formatApiError(data.detail, 'Download failed.')); return; }
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url; link.download = file.split('/').pop() || 'backup.tar.gz';
      document.body.appendChild(link); link.click(); link.remove();
      URL.revokeObjectURL(url);
      setNotice(t('Backup downloaded.'));
    } catch (err) { setError(t('Backup download failed.')); }
    finally { setLoading(''); }
  }

  async function downloadUserBackup(file) {
    try {
      setError(''); setLoading('Downloading full user backup...');
      const res = await fetch(`${API}/maintenance/user-backups-download?backup_file=${encodeURIComponent(file)}`, { credentials: 'include' });
      if (!res.ok) { const data = await res.json().catch(() => ({})); if (handleAuthExpired(res.status, data.detail)) return; setError(formatApiError(data.detail, 'Download failed.')); return; }
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url; link.download = file.split('/').pop() || 'user-backup.tar.gz';
      document.body.appendChild(link); link.click(); link.remove();
      URL.revokeObjectURL(url);
      setNotice(t('Full user backup downloaded.'));
    } catch (err) { setError(t('Full user backup download failed.')); }
    finally { setLoading(''); }
  }

  async function restoreUserBackup(file) {
    if (!confirm(`Restore this full user backup? Missing panel user and websites will be created.\n${file}`)) return;
    const data = await request('/maintenance/user-restore', { method: 'POST', body: JSON.stringify({ backup_file: file }) }, t('Restoring full user backup...'));
    if (data) {
      setNotice(`Restored user ${data.username}. Websites: ${data.websites?.length || 0}`);
      await refreshAll();
      await loadUsers();
      await listUserBackups();
      await loadRestoreBackups();
    }
  }

  async function deleteUserBackup(file) {
    if (!confirm(`Delete this full user backup?\n${file}`)) return;
    const data = await request(`/maintenance/user-backups?backup_file=${encodeURIComponent(file)}`, { method: 'DELETE' }, t('Deleting full user backup...'));
    if (data) {
      await listUserBackups();
      await loadRestoreBackups();
    }
  }

  async function deleteRestoreBackup(file) {
    if (!confirm(`Delete this restore backup?\n${file}`)) return;
    const data = await request(`/maintenance/user-restore-backups?backup_file=${encodeURIComponent(file)}`, { method: 'DELETE' }, t('Deleting restore backup...'));
    if (data) {
      await loadRestoreBackups();
      await listUserBackups();
    }
  }

  async function uploadUserBackups(files) {
    const selectedFiles = Array.from(files || []);
    if (selectedFiles.length === 0) return;
    const form = new FormData();
    selectedFiles.forEach(file => form.append('files', file));
    try {
      setError(''); setLoading('Uploading full user backups...');
      const csrfToken = readCookie('spanel_csrf');
      const headers = csrfToken ? { 'X-CSRF-Token': csrfToken } : {};
      const res = await fetch(`${API}/maintenance/user-restore-backups/upload`, {
        method: 'POST',
        credentials: 'include',
        headers,
        body: form,
      });
      const responseText = await res.text();
      let data;
      try { data = responseText ? JSON.parse(responseText) : {}; } catch { data = { detail: responseText || `HTTP ${res.status}` }; }
      if (!res.ok) { if (handleAuthExpired(res.status, data.detail)) return; setError(formatApiError(data.detail, 'Upload failed.')); return; }
      setNotice(`Uploaded ${data.items?.length || selectedFiles.length} full user backup file(s).`);
      await loadRestoreBackups();
      await listUserBackups();
    } catch (err) { setError(t('Full user backup upload failed.')); }
    finally { setLoading(''); }
  }

  async function listDaBackups() {
    const data = await request('/maintenance/da-import/backups');
    if (data) setDaBackups(Array.isArray(data) ? data : []);
  }

  async function uploadDaBackup(file) {
    if (!file) return;
    const form = new FormData();
    form.append('file', file);
    try {
      setError(''); setLoading('Uploading DA backup...');
      const csrfToken = readCookie('spanel_csrf');
      const headers = csrfToken ? { 'X-CSRF-Token': csrfToken } : {};
      const res = await fetch(`${API}/maintenance/da-import/upload`, {
        method: 'POST', credentials: 'include', headers, body: form,
      });
      const text = await res.text();
      let data;
      try { data = text ? JSON.parse(text) : {}; } catch { data = { detail: text || `HTTP ${res.status}` }; }
      if (!res.ok) { if (handleAuthExpired(res.status, data.detail)) return; setError(formatApiError(data.detail, 'Upload failed.')); return; }
      setNotice(`Uploaded: ${data.filename}`);
      await listDaBackups();
    } catch (err) { setError(t('DA backup upload failed.')); }
    finally { setLoading(''); }
  }

  async function scanDaBackup(archivePath) {
    setDaScanResult(null);
    const apiPath = migratePanelType === 'cpanel' ? '/maintenance/cpanel-import/scan' : (migratePanelType === 'plesk' ? '/maintenance/plesk-import/scan' : '/maintenance/da-import/scan');
      const data = await request(apiPath, { method: 'POST', body: JSON.stringify({ archive_path: archivePath }) }, t(`Scanning ${migratePanelType} backup...`));
    if (data) setDaScanResult(data);
  }

  async function importDaBackup(archivePath, force = daReplaceExisting) {
    const message = t(force
      ? 'Import and REPLACE? Any existing panel user, website, files and databases with the same names are deleted first.'
      : 'Import this DirectAdmin backup? This will create users, websites, databases, and nginx configs.');
    if (!confirm(message)) return;
    setDaImportJob(null);
    const apiPath = migratePanelType === 'cpanel' ? '/maintenance/cpanel-import/import' : (migratePanelType === 'plesk' ? '/maintenance/plesk-import/import' : '/maintenance/da-import/import');
      const data = await request(apiPath, { method: 'POST', body: JSON.stringify({ archive_path: archivePath, force }) }, t(`Starting ${migratePanelType} import...`));
    if (data?.job_id) {
      setNotice(t('DA import started. Polling for result...'));
      setDaImportJob(data);
      pollDaImportJob(data.job_id);
    }
  }

  async function deleteDaBackup(archivePath) {
    if (!confirm(t('Delete this DA backup file?'))) return;
    const data = await request('/maintenance/da-import/backups', { method: 'DELETE', body: JSON.stringify({ archive_path: archivePath }) }, t('Deleting DA backup...'));
    if (data) { setNotice(`Deleted: ${data.deleted}`); setDaScanResult(null); await listDaBackups(); }
  }

  async function bulkImportDaBackups(force = daReplaceExisting) {
    if (selectedDaBackups.length === 0) return;
    const message = force
      ? `Restore and REPLACE ${selectedDaBackups.length} backup(s)? Existing users, websites, files and databases with the same names are deleted first.`
      : `Restore ${selectedDaBackups.length} backup(s)? This will create users, websites, databases, and nginx configs for each.`;
    if (!confirm(message)) return;
    setDaBulkImportJob(null);
    setDaImportJob(null);
    setDaScanResult(null);
    const data = await request('/maintenance/da-import/bulk-import', { method: 'POST', body: JSON.stringify({ archive_paths: selectedDaBackups, force }) }, t('Starting bulk restore...'));
    if (data?.job_id) {
      setNotice(`Bulk restore started: ${data.total} backup(s). Processing sequentially...`);
      setSelectedDaBackups([]);
      pollDaBulkImportJob(data.job_id);
    }
  }

  async function pollDaBulkImportJob(jobId) {
    let attempts = 0;
    const maxAttempts = 720;
    while (attempts < maxAttempts) {
      await new Promise(resolve => setTimeout(resolve, 5000));
      const data = await request(`/maintenance/da-import/bulk-jobs/${jobId}`, { silent: true });
      if (!data) { attempts++; continue; }
      setDaBulkImportJob(data);
      if (data.status === 'completed') {
        const ok = (data.results || []).filter(r => r.status === 'completed').length;
        const fail = (data.results || []).filter(r => r.status === 'failed').length;
        setNotice(`Bulk restore done: ${ok} succeeded, ${fail} failed.`);
        await listDaBackups();
        return;
      }
      attempts++;
    }
  }

  async function bulkDeleteDaBackups() {
    if (selectedDaBackups.length === 0) return;
    if (!confirm(`Delete ${selectedDaBackups.length} selected backup file(s)?`)) return;
    for (const path of selectedDaBackups) {
      await request('/maintenance/da-import/backups', { method: 'DELETE', body: JSON.stringify({ archive_path: path }) }, t('Deleting...'));
    }
    setNotice(`Deleted ${selectedDaBackups.length} backup(s).`);
    setSelectedDaBackups([]);
    setDaScanResult(null);
    await listDaBackups();
  }

  async function deleteBackup(file) {
    if (!selectedWebsiteId) return;
    if (!confirm(`Delete this backup?\n${file}`)) return;
    const data = await request(`/maintenance/backups/${selectedWebsiteId}?backup_file=${encodeURIComponent(file)}`, { method: 'DELETE' }, t('Deleting backup...'));
    if (data) await listBackups();
  }

  async function uploadBackup(file) {
    if (!file || !selectedWebsiteId) return;
    const form = new FormData();
    form.append('file', file);
    try {
      setError(''); setLoading('Uploading backup...');
      const csrfToken = readCookie('spanel_csrf');
      const headers = csrfToken ? { 'X-CSRF-Token': csrfToken } : {};
      const res = await fetch(`${API}/maintenance/backups/${selectedWebsiteId}/upload`, {
        method: 'POST',
        credentials: 'include',
        headers,
        body: form,
      });
      const responseText = await res.text();
      let data;
      try { data = responseText ? JSON.parse(responseText) : {}; } catch { data = { detail: responseText || `HTTP ${res.status}` }; }
      if (!res.ok) { if (handleAuthExpired(res.status, data.detail)) return; setError(formatApiError(data.detail, 'Upload failed.')); return; }
      if (data.backup_file) { setNotice(`Uploaded backup: ${data.backup_file}`); await listBackups(); }
    } catch (err) { setError(t('Upload backup failed.')); }
    finally { setLoading(''); }
  }

  function renderBackups() {
    const selectedBackupUser = users.find(user => String(user.id) === String(selectedBackupUserId));
    const jobTitle = job => ({ site_backup: 'Website backup', user_backup: 'Full user backup', sftp_backup: 'SFTP backup', user_restore: 'Restore' }[job.kind] || 'Backup task');
    const restoreGroups = restoreGroupsOf(restoreListing);
    const restoreNeedle = restoreFilter.trim().toLowerCase();
    const shownRestoreGroups = restoreGroups.filter(group => !restoreNeedle
      || `${group.username} ${group.backups.map(item => item.name).join(' ')}`.toLowerCase().includes(restoreNeedle));
    const allRestoreShownPicked = shownRestoreGroups.length > 0 && shownRestoreGroups.every(group => restorePicks.includes(group.id));
    const restoreRunning = !!restoreJob && ['queued', 'running'].includes(restoreJob.status);
    const restoreDate = value => { const d = new Date(value); return value && !Number.isNaN(d.getTime()) ? d.toLocaleString('vi-VN', { hour12: false }) : '—'; };
    const restoreTargets = sftpTargets.filter(target => target.is_active !== false);
    const restoreSourceOptions = [
      { id: 'local', Icon: HardDrive, label: t('This server'), hint: t('The panel\'s backup folder') },
      { id: 'target', Icon: Cloud, label: t('Backup Destination'), hint: t('S3 or SFTP') },
      { id: 'remote', Icon: Server, label: t('Another server'), hint: t('SFTP, FTP or FTPS') },
    ];
    const restoreStepTwo = { local: 'Backups on this server', target: 'Backup Destination', remote: 'Connection' }[restoreSource];
    const restoreRemoteReady = !!restoreRemote.host.trim() && !!restoreRemote.username.trim() && !!restoreRemote.password;
    const restoreCanUse = !loading && !restoreRunning && (restoreSource !== 'target' || !!restoreTargetId) && (restoreSource !== 'remote' || restoreRemoteReady);
    const restoreTools = <div className="restore-go">
      <label className={`upload-button secondary${restoreCanUse ? '' : ' disabled'}`} aria-disabled={!restoreCanUse}>
        <Upload size={14}/>{t('Upload backup')}
        <input type="file" multiple accept=".tar.gz,application/gzip" disabled={!restoreCanUse}
          onChange={e => { uploadRestoreBackups(e.target.files); e.target.value = ''; }} />
      </label>
      <button type="button" className="secondary" disabled={!restoreCanUse} onClick={() => listRestoreSource(currentRestoreSource())}>
        <RefreshCw size={14}/>{t('Refresh')}</button>
    </div>;
    const restoreStatus = { queued: ['Waiting', 'badge'], fetching: ['Downloading', 'badge warn'], restoring: ['Restoring', 'badge warn'], done: ['Restored', 'badge ok'], failed: ['Failed', 'badge bad'] };
    const restoreDone = (restoreJob?.results || []).filter(row => row.status === 'done').length;
    const jobDetail = job => job.error || job.remote_file || job.backup_file || job.message || job.status;
    const backupTabs = isAdmin
      ? [
        ['website', 'Backup website', Globe],
        ['user', 'Backup user', Users],
        ['schedule', 'Scheduled backups', Clock],
        ['restore', 'Restore', RotateCcw],
        ['destination', 'Backup Destination', Network],
        ['migrate-panel', 'Migrate Panel', ArchiveRestore],
      ]
      : isReseller
        ? [
          ['website', 'Backup website', Globe],
          ['user', 'Backup user', Users],
        ]
        : [['website', 'Backup website', Globe]];
    const activeBackupTab = backupTabs.some(([id]) => id === backupTab) ? backupTab : 'website';
    const visibleBackupJobs = backupJobs.filter(job => job.status !== 'done');

    return <div className="section backups-page">
      <h2>{t('Backups')}</h2>
      <div className="segmented-control backup-tabs" role="tablist" aria-label={t('Backup sections')}>
        {backupTabs.map(([id, label, Icon]) => <button
          key={id}
          type="button"
          role="tab"
          aria-selected={activeBackupTab === id}
          className={activeBackupTab === id ? 'active' : ''}
          onClick={() => setBackupTab(id)}
        ><Icon size={14}/>{t(label)}</button>)}
      </div>
      {visibleBackupJobs.length > 0 && <div className="backup-job-list">
        {visibleBackupJobs.map(job => <div className={`backup-job ${job.status}`} key={job.job_id}>
          <Clock size={14}/>
          <span><strong>{jobTitle(job)}</strong><small>{jobDetail(job)}</small></span>
          <span className={job.status === 'done' ? 'badge ok' : job.status === 'error' ? 'badge bad' : 'badge'}>{job.status}</span>
        </div>)}
      </div>}

      {activeBackupTab === 'website' && <div className="backup-tab-panel">
        <div className="backup-panel-title">
          <div><h3>{t('Backup website')}</h3><p className="hint">{t('Backups include website source files and a database SQL export.')}</p></div>
        </div>
        <WebsiteSelect />
        <div className="actions backup-toolbar">
          <button disabled={!selectedWebsiteId || !!loading} onClick={createBackup}><Plus size={14}/>{t('Create backup')}</button>
          <button className="secondary-light" disabled={!selectedWebsiteId || !!loading} onClick={refreshBackupArea}><RefreshCw size={14}/>{t('Refresh')}</button>
          <label className="upload-button secondary">
            <Upload size={14}/>{t('Upload backup')}<input type="file" accept=".tar.gz,application/gzip" onChange={e => { uploadBackup(e.target.files?.[0]); e.target.value = ''; }} />
          </label>
        </div>
        {backups.length === 0 && selectedWebsiteId && <EmptyState icon={Archive} message={t('No backups found for this website.')} />}
        <div className="backup-list">
          {backups.map(file => <div className="backup-item" key={file}>
            <span>{file.split('/').pop()}</span>
            <div className="actions">
              <button className="secondary" disabled={!!loading} onClick={() => downloadBackup(file)}><Download size={14}/>{t('Download')}</button>
              <button className="secondary" disabled={!!loading} onClick={() => restoreBackup(file)}><RotateCcw size={14}/>{t('Restore')}</button>
              <button className="danger" disabled={!!loading} onClick={() => deleteBackup(file)}><Trash2 size={14}/></button>
            </div>
          </div>)}
        </div>
      </div>}

      {(isAdmin || isReseller) && activeBackupTab === 'user' && <div className="backup-tab-panel">
        <div className="backup-panel-title">
          <div><h3>{t('Backup user')}</h3><p className="hint">{t('Includes the panel user, all owned websites, source files, database dumps, and restore metadata.')}</p></div>
          <button className="secondary" disabled={!!loading} onClick={refreshUserBackupArea}><RefreshCw size={14}/>{t('Reload')}</button>
        </div>
        <div className="sftp-run-row user-backup-row backup-run-row">
          <select value={selectedBackupUserId} onChange={e => setSelectedBackupUserId(e.target.value)}>
            <option value="">{t('Select user')}</option>
            {users.map(user => <option key={user.id} value={user.id}>{user.username}</option>)}
          </select>
          {isAdmin && (
            <select value={selectedSftpTargetId} onChange={e => setSelectedSftpTargetId(e.target.value)}>
              <option value="">{t('Local only')}</option>
              {sftpTargets.map(target => <option key={target.id} value={target.id}>{target.name}</option>)}
            </select>
          )}
          <button disabled={!selectedBackupUserId || !!loading} onClick={createUserBackup}><Archive size={14}/>{t('Create backup')}</button>
        </div>
        {selectedBackupUser && <p className="hint">{t('Current user:')}{' '}<strong>{selectedBackupUser.username}</strong></p>}
        <div className="actions backup-subactions">
          <button className="secondary" disabled={!selectedBackupUserId || !!loading} onClick={() => listUserBackups()}><RefreshCw size={14}/>{t('Refresh list')}</button>
        </div>
        {selectedBackupUserId && userBackups.length === 0 && <EmptyState icon={Archive} message={t('No user backups found.')} />}
        <div className="backup-list">
          {userBackups.map(file => <div className="backup-item" key={file}>
            <span>{file.split('/').pop()}</span>
            {isAdmin && (
              <div className="actions">
                <button className="secondary" disabled={!!loading} onClick={() => downloadUserBackup(file)}><Download size={14}/>{t('Download')}</button>
                <button className="secondary" disabled={!!loading} onClick={() => restoreUserBackup(file)}><RotateCcw size={14}/>{t('Restore user')}</button>
                <button className="danger" disabled={!!loading} onClick={() => deleteUserBackup(file)}><Trash2 size={14}/></button>
              </div>
            )}
          </div>)}
        </div>

        {isAdmin && <>
          <div className="section-title restore-title backup-panel-heading backup-subtitle">
            <div><h3>{t('Restore folder')}</h3><p className="hint">{restoreBackupDir || '/var/backups/spanel/users/restore'}</p></div>
            <div className="actions">
              <button className="secondary-light" disabled={!!loading} onClick={loadRestoreBackups}><RefreshCw size={14}/>{t('Refresh')}</button>
              <label className="upload-button secondary">
                <Upload size={14}/>{t('Upload backups')}<input type="file" multiple accept=".tar.gz,application/gzip" onChange={e => { uploadUserBackups(e.target.files); e.target.value = ''; }} />
              </label>
            </div>
          </div>
          <div className="backup-list">
            {restoreBackups.map(item => <div className="backup-item" key={item.backup_file}>
              <span>{item.filename || item.backup_file.split('/').pop()}<small>{item.valid ? `${item.source === 'opanel' ? 'opanel · ' : ''}` + t('{user} - {n} website(s)', { user: item.username || t('unknown user'), n: item.websites || 0 }) : (item.error || 'Invalid backup')}</small></span>
              <div className="actions">
                <button className="secondary" disabled={!!loading} onClick={() => downloadUserBackup(item.backup_file)}><Download size={14}/>{t('Download')}</button>
                <button className="secondary" disabled={!!loading || !item.valid} onClick={() => restoreUserBackup(item.backup_file)}><RotateCcw size={14}/>{t('Restore user')}</button>
                <button className="danger" disabled={!!loading} onClick={() => deleteRestoreBackup(item.backup_file)}><Trash2 size={14}/></button>
              </div>
            </div>)}
          </div>
        </>}

      </div>}

      {isAdmin && activeBackupTab === 'schedule' && <div className="backup-tab-panel">
        <div className="backup-panel-title">
          <div><h3>{t('Scheduled backups')}</h3><p className="hint">{t('Run full user backups automatically with optional off-server destination.')}{' '}{t('With a destination, the backups are kept there only: each one is removed from this server once it has uploaded, and stays here only if the upload fails.')}</p></div>
          <button className="secondary-light" disabled={!!loading} onClick={refreshScheduledBackupArea}><RefreshCw size={14}/>{t('Refresh')}</button>
        </div>
        {/* Who, then when, where and under what name - each field labelled,
            and one per line on a phone. It was six unlabelled controls in one
            row of 130px cells. */}
        <div className="backup-schedule-builder">
          <div className="schedule-users">
          <label className="schedule-toggle">
            <input type="checkbox" checked={!!newBackupSchedule.all_users} onChange={e => setNewBackupSchedule(prev => ({ ...prev, all_users: e.target.checked }))} />
            <span>{t('All users')}</span>
          </label>
          <select multiple size={6} value={newBackupSchedule.user_ids || []} disabled={!!newBackupSchedule.all_users} onChange={e => setNewBackupSchedule(prev => ({ ...prev, user_ids: Array.from(e.target.selectedOptions, option => option.value) }))}>
            {users.map(user => <option key={user.id} value={String(user.id)}>{user.username}</option>)}
          </select>
          </div>
          <div className="schedule-fields">
          <label><span>{t('Schedule')}</span>
            <select value={backupScheduleCustom || !isPresetSchedule(BACKUP_SCHEDULE_PRESETS, newBackupSchedule.schedule) ? 'custom' : newBackupSchedule.schedule}
              onChange={e => {
                if (e.target.value === 'custom') { setBackupScheduleCustom(true); return; }
                setBackupScheduleCustom(false);
                setNewBackupSchedule(prev => ({ ...prev, schedule: e.target.value }));
              }}>
              {BACKUP_SCHEDULE_PRESETS.map(([value, label]) => <option key={value} value={value}>{t(label)}</option>)}
              <option value="custom">{t('Custom...')}</option>
            </select>
          </label>
          {(backupScheduleCustom || !isPresetSchedule(BACKUP_SCHEDULE_PRESETS, newBackupSchedule.schedule)) && <label><span>{t('Cron expression')}</span>
            <input value={newBackupSchedule.schedule} onChange={e => setNewBackupSchedule(prev => ({ ...prev, schedule: e.target.value }))} placeholder="0 2 * * *" /></label>}
          <label><span>{t('Destination')}</span>
          <select value={newBackupSchedule.target_id} onChange={e => setNewBackupSchedule(prev => ({ ...prev, target_id: e.target.value }))}>
            <option value="">{t('Local only')}</option>
            {sftpTargets.map(target => <option key={target.id} value={target.id}>{target.name} ({target.kind === 's3' ? 'S3' : 'SFTP'})</option>)}
          </select>
          </label>
          <label><span>{t('Stored file name')}</span>
          <select value={newBackupSchedule.name_suffix} aria-label={t('Stored file name')}
            onChange={e => setNewBackupSchedule(prev => ({ ...prev, name_suffix: e.target.value }))}>
            <option value="none">{t('Append: nothing')}</option>
            <option value="day_of_week">{t('Append: day of week')}</option>
            <option value="week_of_month">{t('Append: week of month')}</option>
            <option value="full_date">{t('Append: full date')}</option>
          </select>
          </label>
          <button className="schedule-submit" disabled={(!newBackupSchedule.all_users && (!newBackupSchedule.user_ids || newBackupSchedule.user_ids.length === 0)) || !!loading} onClick={createBackupSchedule}><Clock size={14}/>{t('Schedule')}</button>
          </div>
        </div>
        <p className="hint">{t('The name suffix decides how many copies are kept: nothing keeps one per account, day of week keeps seven, week of month five, and full date one a day until retention removes it.')}</p>
        <div className="backup-list">
          {backupSchedules.map(item => {
            const scheduleTarget = sftpTargets.find(target => target.id === item.target_id);
            return <div className="backup-item" key={item.id}>
              <span>
                {scheduleUserLabel(item)} - {item.schedule}{scheduleTarget ? ` - ${scheduleTarget.name}` : ''}{item.name_suffix && item.name_suffix !== 'full_date' ? ` - ${item.name_suffix.replace(/_/g, ' ')}` : ''}
                {item.last_status === 'running' && <span className="badge"> running</span>}
                <small>{item.last_status}: {item.last_message || 'not run yet'}</small>
              </span>
              <div className="actions schedule-actions">
                <button className="mini secondary-light" disabled={!!loading || item.last_status === 'running'} onClick={() => runBackupScheduleNow(item)}><Play size={14}/>{t('Run now')}</button>
                <button className="mini danger" disabled={!!loading} onClick={() => deleteBackupSchedule(item.id)}><Trash2 size={14}/>{t('Delete')}</button>
              </div>
            </div>;
          })}
        </div>
      </div>}

      {isAdmin && activeBackupTab === 'restore' && <div className="backup-tab-panel restore-flow">
        <div className="backup-panel-title">
          <div>
            <h3>{t('Restore')}</h3>
            <p className="hint">{t('Where the backups are, what that needs, which users - then restore. The same steps as DirectAdmin.')}</p>
          </div>
        </div>

        <div className="restore-step">
          <h4><span className="restore-step-no">1</span>{t('Source')}</h4>
          <div className="restore-sources" role="radiogroup" aria-label={t('Source')}>
            {restoreSourceOptions.map(option => <button type="button" role="radio" key={option.id}
              aria-checked={restoreSource === option.id} disabled={!!loading || restoreRunning}
              className={`restore-source${restoreSource === option.id ? ' active' : ''}`} onClick={() => chooseRestoreSource(option.id)}>
              <option.Icon size={18}/>
              <span><strong>{option.label}</strong><small>{option.hint}</small></span>
            </button>)}
          </div>
        </div>

        <div className="restore-step">
          <h4><span className="restore-step-no">2</span>{t(restoreStepTwo)}</h4>
          {restoreSource === 'local' && <>
            <p className="hint">{t('Scheduled and manual backups kept on this server, and the ones uploaded here.')}{restoreListing?.location && <>{' '}<code>{restoreListing.location}</code></>}</p>
            {restoreTools}
          </>}
          {restoreSource === 'target' && (restoreTargets.length === 0
            ? <p className="hint">{t('No backup destination yet. Add one in the Backup Destination tab.')}</p>
            : <>
                <label className="restore-target-pick"><span>{t('Destination')}</span>
                  <select value={restoreTargetId} disabled={!!loading || restoreRunning}
                    onChange={e => { setRestoreTargetId(e.target.value); if (e.target.value) listRestoreSource(`target:${e.target.value}`); }}>
                    {restoreTargets.map(target => <option key={target.id} value={target.id}>{target.name} · {target.kind === 's3' ? 'S3' : 'SFTP'}</option>)}
                  </select>
                </label>
                {restoreListing?.location && <p className="hint"><code>{restoreListing.location}</code></p>}
                {restoreTools}
              </>)}
          {restoreSource === 'remote' && <>
                <div className="restore-remote">
                  <label><span>{t('Protocol')}</span><select value={restoreRemote.protocol} disabled={restoreRunning} onChange={e => editRestoreRemote({ protocol: e.target.value })}>
                    <option value="sftp">SFTP</option><option value="ftp">FTP</option><option value="ftps">FTPS (FTP + TLS)</option>
                  </select></label>
                  <label><span>{t('Server')}</span><input value={restoreRemote.host} placeholder="backup.example.com" disabled={restoreRunning} onChange={e => editRestoreRemote({ host: e.target.value })} /></label>
                  <label><span>{t('Port')}</span><input value={restoreRemote.port} inputMode="numeric" placeholder={restoreRemote.protocol === 'sftp' ? '22' : '21'} disabled={restoreRunning} onChange={e => editRestoreRemote({ port: e.target.value.replace(/[^0-9]/g, '') })} /></label>
                  <label><span>{t('Username')}</span><input value={restoreRemote.username} autoComplete="off" disabled={restoreRunning} onChange={e => editRestoreRemote({ username: e.target.value })} /></label>
                  <label><span>{t('Password')}</span><input type="password" value={restoreRemote.password} autoComplete="new-password" disabled={restoreRunning} onChange={e => editRestoreRemote({ password: e.target.value })} /></label>
                  <label><span>{t('Folder')}</span><input value={restoreRemote.path} placeholder="/backups" disabled={restoreRunning} onChange={e => editRestoreRemote({ path: e.target.value })} /></label>
                </div>
                {restoreTools}
                <p className="hint">{t('Used for this restore only and never saved. To keep a server, add it under Backup Destination.')}</p>
                {restoreListing?.host_key && <p className="hint">{t('Server key')}: <code>{restoreListing.host_key.type} {restoreListing.host_key.fingerprint}</code></p>}
              </>}
        </div>

        <div className="restore-step">
          <h4><span className="restore-step-no">3</span>{t('Accounts to restore')}</h4>
          {!restoreListing
            ? <p className="hint">{restoreSource === 'local' ? t('Looking for backups...') : restoreSource === 'target' && restoreTargets.length === 0 ? '—' : t('Press Refresh in step 2 to list the backups.')}</p>
            : restoreGroups.length === 0
              ? <EmptyState icon={ArchiveRestore} message={t('No backups found in this source.')} />
              : <>
                  <div className="restore-toolbar">
                    <label className="check-line"><input type="checkbox" checked={allRestoreShownPicked} disabled={!!loading || restoreRunning}
                      onChange={() => setRestorePicks(prev => allRestoreShownPicked
                        ? prev.filter(id => !shownRestoreGroups.some(group => group.id === id))
                        : [...new Set([...prev, ...shownRestoreGroups.map(group => group.id)])])} />{t('Select all')}</label>
                    <input id="restore-filter" value={restoreFilter} onChange={e => setRestoreFilter(e.target.value)} placeholder={t('Filter by user...')} aria-label={t('Filter by user...')} />
                    <span className="hint">{t('{n} selected', { n: restorePicks.length })}</span>
                  </div>
                  <div className="restore-users">
                    {shownRestoreGroups.map(group => {
                      const chosen = chosenRestoreBackup(group);
                      const picked = restorePicks.includes(group.id);
                      const exists = !!group.username && (restoreListing.existing || []).includes(group.username);
                      return <div className={`restore-user${picked ? ' picked' : ''}`} key={group.id}>
                        <label className="restore-user-main">
                          <input type="checkbox" checked={picked} disabled={!!loading || restoreRunning} onChange={() => toggleRestorePick(group.id)} />
                          <span className="restore-main">
                            <strong>{group.username || chosen.name}</strong>
                            <small>{!group.username ? t('The user is read from the backup when it is restored') : exists ? t('Exists on this server - will be overwritten') : t('New on this server')}</small>
                          </span>
                          {group.username && <span className={exists ? 'badge warn' : 'badge ok'}>{exists ? t('Overwrite') : t('New')}</span>}
                        </label>
                        {group.backups.length > 1
                          ? <select value={chosen.key} disabled={!!loading || restoreRunning} aria-label={t('Backup to restore')}
                              onChange={e => setRestoreChoice(prev => ({ ...prev, [group.id]: e.target.value }))}>
                              {group.backups.map(item => <option key={item.key} value={item.key}>{restoreDate(item.modified)} · {formatBytes(item.size)}</option>)}
                            </select>
                          : <span className="hint restore-when">{restoreDate(chosen.modified)} · {formatBytes(chosen.size)}</span>}
                      </div>;
                    })}
                  </div>
                </>}
        </div>

        <div className="restore-step">
          <h4><span className="restore-step-no">4</span>{t('Restore')}</h4>
          <div className="restore-go">
            <button type="button" className="danger" disabled={!!loading || restoreRunning || restorePicks.length === 0} onClick={runRestore}>
              <RotateCcw size={14}/>{t('Restore {n} account(s)', { n: restorePicks.length })}</button>
            <span className="hint">{t('One user after another, in the background. A user that already exists is overwritten with its backup.')}</span>
          </div>
          {restoreJob && <div className="restore-progress" aria-live="polite">
            <strong>{restoreRunning
              ? t('Restoring: {done} of {total} done', { done: restoreDone, total: (restoreJob.results || []).length })
              : t('Finished: {done} of {total} restored', { done: restoreDone, total: (restoreJob.results || []).length })}</strong>
            {(restoreJob.results || []).map((row, index) => {
              const [label, badge] = restoreStatus[row.status] || restoreStatus.queued;
              return <div className="restore-progress-row" key={`${row.name}-${index}`}>
                <span>{row.username || row.name}</span>
                <span className={badge}>{t(label)}</span>
                {row.status === 'failed' && row.detail && <small className="restore-progress-error">{row.detail}</small>}
              </div>;
            })}
          </div>}
        </div>
      </div>}

      {isAdmin && activeBackupTab === 'destination' && <div className="backup-tab-panel">
        <div className="backup-panel-title">
          <div><h3>{t('Backup Destination')}</h3><p className="hint">{t('Somewhere off this machine to keep a copy. A backup that lives on the server it backs up is not a backup.')}</p></div>
          <button className="secondary-light" disabled={!!loading} onClick={loadSftpTargets}><RefreshCw size={14}/>{t('Refresh')}</button>
        </div>
        <div className="segmented" role="tablist" aria-label={t('Destination type')}>
          <button className={newSftpTarget.kind === 'sftp' ? 'active' : ''} disabled={!!loading}
            onClick={() => setNewSftpTarget(prev => ({ ...prev, kind: 'sftp' }))}>{t('SFTP server')}</button>
          <button className={newSftpTarget.kind === 's3' ? 'active' : ''} disabled={!!loading}
            onClick={() => setNewSftpTarget(prev => ({ ...prev, kind: 's3' }))}>{t('S3 storage')}</button>
        </div>
        {newSftpTarget.kind === 's3'
          ? <>
              <p className="hint">{t('Works with S3 and anything that speaks its API: Wasabi, Backblaze B2, DigitalOcean Spaces, Cloudflare R2, MinIO. The bucket is checked before the target is saved, so a destination that cannot be reached never gets attached to a schedule.')}</p>
              <div className="sftp-form sftp-target-form">
                <input id="s3-name" value={newSftpTarget.name} onChange={e => setNewSftpTarget(prev => ({ ...prev, name: e.target.value }))} placeholder={t('Target name')} />
                <input id="s3-endpoint" value={newSftpTarget.endpoint} onChange={e => setNewSftpTarget(prev => ({ ...prev, endpoint: e.target.value }))} placeholder="s3.wasabisys.com" />
                <input id="s3-bucket" value={newSftpTarget.bucket} onChange={e => setNewSftpTarget(prev => ({ ...prev, bucket: e.target.value }))} placeholder={t('Bucket')} />
                <input id="s3-region" value={newSftpTarget.region} onChange={e => setNewSftpTarget(prev => ({ ...prev, region: e.target.value }))} placeholder={t('Region (optional)')} />
                <input id="s3-access" value={newSftpTarget.access_key} onChange={e => setNewSftpTarget(prev => ({ ...prev, access_key: e.target.value }))} placeholder={t('Access key')} />
                <input id="s3-secret" value={newSftpTarget.secret_key} onChange={e => setNewSftpTarget(prev => ({ ...prev, secret_key: e.target.value }))} placeholder={t('Secret key')} type="password" />
                <input id="s3-prefix" value={newSftpTarget.prefix} onChange={e => setNewSftpTarget(prev => ({ ...prev, prefix: e.target.value }))} placeholder={t('Prefix, e.g. spanel/nightly (optional)')} />
                <label className="check-line"><input id="s3-secure" type="checkbox" checked={!!newSftpTarget.secure} onChange={e => setNewSftpTarget(prev => ({ ...prev, secure: e.target.checked }))} /><span>{t('Use HTTPS')}</span></label>
                <button disabled={!!loading || !newSftpTarget.name || !newSftpTarget.endpoint || !newSftpTarget.bucket || !newSftpTarget.access_key || !newSftpTarget.secret_key} onClick={createSftpTarget}><Plus size={14}/>{t('Check and save')}</button>
              </div>
            </>
          : <div className="sftp-form sftp-target-form">
              <input value={newSftpTarget.name} onChange={e => setNewSftpTarget(prev => ({ ...prev, name: e.target.value }))} placeholder={t('Target name')} />
              <input value={newSftpTarget.host} onChange={e => setNewSftpTarget(prev => ({ ...prev, host: e.target.value }))} placeholder="Host" />
              <input value={newSftpTarget.port} onChange={e => setNewSftpTarget(prev => ({ ...prev, port: e.target.value }))} placeholder="22" inputMode="numeric" />
              <input value={newSftpTarget.username} onChange={e => setNewSftpTarget(prev => ({ ...prev, username: e.target.value }))} placeholder={t('Username')} />
              <input value={newSftpTarget.password} onChange={e => setNewSftpTarget(prev => ({ ...prev, password: e.target.value }))} placeholder={t('Password')} type="password" />
              <input value={newSftpTarget.remote_path} onChange={e => setNewSftpTarget(prev => ({ ...prev, remote_path: e.target.value }))} placeholder="/backups/spanel" />
              <textarea value={newSftpTarget.private_key} onChange={e => setNewSftpTarget(prev => ({ ...prev, private_key: e.target.value }))} placeholder={t('Private key (optional)')} rows={4} />
              <button disabled={!!loading || !newSftpTarget.name || !newSftpTarget.host || !newSftpTarget.username || (!newSftpTarget.password && !newSftpTarget.private_key)} onClick={createSftpTarget}><Plus size={14}/>{t('Save target')}</button>
            </div>}
        {sftpTargets.length === 0 && <EmptyState icon={Network} message={t('No backup destinations found.')} />}
        <div className="backup-list">
          {sftpTargets.map(target => <div className="backup-item" key={target.id}>
            <span>
              <span className="badge">{target.kind === 's3' ? 'S3' : 'SFTP'}</span> {target.name}
              <small>{target.kind === 's3'
                ? `${target.endpoint}/${target.bucket}${target.prefix ? '/' + target.prefix : ''}`
                : `${target.username}@${target.host}:${target.remote_path}`}</small>
            </span>
            <button className="danger" disabled={!!loading} onClick={() => deleteSftpTarget(target.id)}><Trash2 size={14}/></button>
          </div>)}
        </div>
      </div>}

      {isAdmin && (activeBackupTab === 'migrate-panel' || activeBackupTab === 'da-import') && <div className="backup-tab-panel">
        <div className="backup-panel-title">
          <div><h3>{t('DirectAdmin Import')}</h3><p className="hint">{t('Import websites, databases, and users from a DirectAdmin backup archive.')}</p></div>
          <button className="secondary-light" disabled={!!loading} onClick={() => listDaBackups()}><RefreshCw size={14}/>{t('Refresh')}</button>
        </div>
        <div className="da-toolbar">
          <label className="upload-button">
            <Upload size={14}/>{t('Upload DA backup')}<input ref={daFileInputRef} type="file" accept=".tar.zst,.tzst,.tar.gz,.tgz,.tar.bz2,.tbz2,.tar.xz,.txz,.tar" onChange={e => { uploadDaBackup(e.target.files?.[0]); e.target.value = ''; }} />
          </label>
          <label className="da-toggle">
            <input type="checkbox" checked={daReplaceExisting} onChange={e => setDaReplaceExisting(e.target.checked)} />{t('Replace existing users/websites')}</label>
        </div>
        {daReplaceExisting && <p className="hint da-warn">{t('Imports will delete any existing panel user, website, files and databases that share a name with the backup. Leave this off to have conflicting imports stop instead.')}</p>}
        {daBackups.length === 0 && <EmptyState icon={ArchiveRestore} message={t('No DirectAdmin backups uploaded. Upload a DA backup archive to get started.')} />}
        {daBackups.length > 0 && <>
          <div className="da-list-head">
            <label className="da-toggle">
              <input type="checkbox" checked={selectedDaBackups.length === daBackups.length && daBackups.length > 0} onChange={toggleSelectAllDaBackups} />
              {t('Select all ({n})', { n: daBackups.length })}
            </label>
            {selectedDaBackups.length > 0 && <div className="da-actions">
              <button disabled={!!loading} onClick={() => bulkImportDaBackups()} className="primary"><ArchiveRestore size={14}/> {t('Restore selected ({n})', { n: selectedDaBackups.length })}</button>
              <button disabled={!!loading} onClick={bulkDeleteDaBackups} className="danger"><Trash2 size={14}/> {t('Delete selected ({n})', { n: selectedDaBackups.length })}</button>
            </div>}
          </div>
          <div className="backup-list">
            {daBackups.map(file => <div className={`backup-item da-backup-row${selectedDaBackups.includes(file.path) ? ' selected' : ''}`} key={file.path}>
              <label className="da-backup-pick">
                <input type="checkbox" checked={selectedDaBackups.includes(file.path)} onChange={() => toggleDaBackupSelect(file.path)} />
                <span>{file.filename}<small>{(file.size / (1024 * 1024)).toFixed(1)} MB</small></span>
              </label>
              <div className="da-actions">
                <button disabled={!!loading} onClick={() => scanDaBackup(file.path)}><Search size={14}/>{t('Scan')}</button>
                <button disabled={!!loading} onClick={() => importDaBackup(file.path)}><ArchiveRestore size={14}/>{t('Import')}</button>
                <button className="danger" disabled={!!loading} onClick={() => deleteDaBackup(file.path)}><Trash2 size={14}/></button>
              </div>
            </div>)}
          </div>
        </>}

        {daScanResult && <div className="da-scan-result">
          <h4>{t('Scan result:')} {daScanResult.filename}</h4>
          {daScanResult.errors?.length > 0 && <div className="error-list">
            {daScanResult.errors.map((err, i) => <p key={i} className="error-text">{err}</p>)}
          </div>}
          {daScanResult.users?.map((user, i) => <div key={i} className="da-user-block">
            <p className="da-user-head"><Users size={13}/> <strong>{user.username}</strong>{user.email && <small>{user.email}</small>}</p>
            {user.domains?.length > 0 && <div className="da-table-wrap">
              <table className="da-scan-table">
                <thead><tr><th>{t('Domain')}</th><th>{t('Type')}</th><th>{t('Files')}</th><th>{t('Database')}</th><th>{t('SQL dump')}</th><th>{t('Pointers')}</th></tr></thead>
                <tbody>
                  {user.domains.map((d, j) => <tr key={j}>
                    <td><Globe size={12}/> {d.domain}</td>
                    <td>{d.app_type}</td>
                    <td>{d.has_files ? <Check size={13} className="da-yes"/> : <X size={13} className="da-no"/>}</td>
                    <td>{d.db_name || <span className="da-muted">—</span>}</td>
                    <td>{d.has_sql_dump ? <Check size={13} className="da-yes"/> : <X size={13} className="da-no"/>}</td>
                    <td>{d.aliases?.length > 0 ? d.aliases.map(a => `${a.domain} (${a.mode})`).join(', ') : <span className="da-muted">—</span>}</td>
                  </tr>)}
                </tbody>
              </table>
            </div>}
            {user.databases?.length > 0 && <div className="da-table-wrap">
              <p className="hint">{t('Unassigned databases ({n})', { n: user.databases.length })}</p>
              <table className="da-scan-table">
                <thead><tr><th>{t('Database')}</th><th>{t('SQL dump')}</th></tr></thead>
                <tbody>
                  {user.databases.map((db, j) => <tr key={j}>
                    <td><Database size={12}/> {db.db_name}</td>
                    <td>{db.has_sql_dump ? <Check size={13} className="da-yes"/> : <X size={13} className="da-no"/>}</td>
                  </tr>)}
                </tbody>
              </table>
            </div>}
          </div>)}
        </div>}

        {daImportJob && <div className={`backup-job da-job ${daImportJob.status}`}>
          <Clock size={14}/>
          <span><strong>{t('DA Import')}</strong><small>{daImportJob.archive || ''}</small></span>
          <span className={daImportJob.status === 'completed' ? 'badge ok' : daImportJob.status === 'failed' ? 'badge bad' : 'badge'}>{daImportJob.status}</span>
        </div>}
        {daImportJob?.status === 'completed' && daImportJob.result?.summary && <div className="da-scan-result">
          <h4>{t('Import summary')}</h4>
          {daImportJob.result.summary.map((item, i) => <div key={i} className="da-user-block">
            <p className="da-user-head"><strong>{item.username}</strong> <span className="badge ok">{t('{n} domain(s)', { n: item.imported_domains?.length || 0 })}</span> <span className="badge">{t('{n} database(s)', { n: item.databases?.length || 0 })}</span></p>
            {item.aliases?.length > 0 && <p className="hint">{t('Pointers:')} {item.aliases.join(', ')}</p>}
            {item.ssl_enabled_domains?.length > 0 && <p className="hint">{t('SSL enabled:')} {item.ssl_enabled_domains.join(', ')}</p>}
            {item.warnings?.length > 0 && <p className="hint da-warn">{t('Warnings:')} {item.warnings.join('; ')}</p>}
          </div>)}
          {daImportJob.result.credentials && <details className="da-creds-details">
            <summary>{t('Generated credentials (click to show)')}</summary>
            <pre className="da-credentials">{daImportJob.result.credentials.join('\n')}</pre>
          </details>}
        </div>}

        {daBulkImportJob && <div className={`backup-job da-job ${daBulkImportJob.status}`}>
          <Clock size={14}/>
          <span><strong>{t('Bulk restore')}</strong><small>{daBulkImportJob.status === 'running' ? `Processing ${daBulkImportJob.current + 1}/${daBulkImportJob.total}: ${daBulkImportJob.current_archive}` : `${daBulkImportJob.total} backup(s)`}</small></span>
          <span className={daBulkImportJob.status === 'completed' ? 'badge ok' : 'badge'}>{daBulkImportJob.status === 'running' ? `${daBulkImportJob.current}/${daBulkImportJob.total}` : daBulkImportJob.status}</span>
        </div>}
        {daBulkImportJob?.status === 'completed' && daBulkImportJob.results && <div className="da-scan-result">
          <h4>{t('Bulk restore results')}</h4>
          {daBulkImportJob.results.map((item, i) => <div key={i} className={`da-user-block ${item.status === 'completed' ? 'ok' : 'bad'}`}>
            <p className="da-user-head"><strong>{item.archive}</strong> <span className={item.status === 'completed' ? 'badge ok' : 'badge bad'}>{item.status}</span></p>
            {item.result?.summary?.map((s, j) => <p key={j} className="hint">{s.username}: {t('{d} domain(s), {b} db(s)', { d: s.imported_domains?.length || 0, b: s.databases?.length || 0 })}</p>)}
            {item.result?.credentials && <details className="da-creds-details">
              <summary>{t('Credentials')}</summary>
              <pre className="da-credentials">{item.result.credentials.join('\n')}</pre>
            </details>}
            {item.error && <p className="error-text">{item.error}</p>}
          </div>)}
        </div>}
      </div>}
    </div>;
  }
  return renderBackups();
}
