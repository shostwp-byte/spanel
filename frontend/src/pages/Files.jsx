import React, { useState, useEffect } from 'react';
import { Archive, ArchiveRestore, ArrowLeft, Ban, Bot, Boxes, Check, ChevronDown, ChevronRight, Clock, Code2, Copy, Cpu, Database, Dices, ExternalLink, Eye, FileText, FolderOpen, Globe, HardDrive, Home, Image, KeyRound, Lock, LogIn, LogOut, MemoryStick, Menu, Moon, MoveRight, Network, Pencil, Save, Search, Server, Settings as SettingsIcon, Shield, Sun, Trash2, TerminalIcon, Users, X, RefreshCw, Plus, Download, Upload, Play, Square, RotateCcw, AlertCircle, Activity, BrickWall, Bug, LockKeyhole, PackageOpen, ScrollText, ShieldAlert, CheckCircle, Zap, Bell, Mail, Send, Cloud, Inbox, Forward, ShieldCheck } from 'lucide-react';
import { EmptyState } from '../components/EmptyState.jsx';
import { useAppContext } from '../contexts/AppContext.jsx';
import { t } from '../i18n.js';
import { formatBytes } from '../utils/helpers.js';

function humanSize(bytes) {
  if (bytes === 0 || !bytes) return '0 B';
  const k = 1024, sizes = ['B', 'KB', 'MB', 'GB', 'TB'], i = Math.floor(Math.log(bytes) / Math.log(k));
  return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
}

export function FilesPage({ request, API, navigateToPage, standaloneEditor }) {
  const {
    loading, setLoading, setError, setNotice, fileAppId, setFileAppId, filePath, setFilePath, fileListPath, setFileListPath,
    websites, selectedWebsiteId, setSelectedWebsiteId, siteApps,
    currentUser, isAdmin, storageUsageText, loadCurrentUser,
    clearSession, readCookie, upsertFileJob, dismissFileJob, formatFileTime, fileJobs, setFileJobs,
  } = useAppContext();
  const currentSite = websites.find(site => String(site.id) === String(selectedWebsiteId));
  const currentFileApp = () => siteApps?.items?.find(app => String(app.id) === String(fileAppId)) || null;
  
  const [fileUploadDir, setFileUploadDir] = useState('public_html');
  const [files, setFiles] = useState([]);

  const [archiveFormat, setArchiveFormat] = useState('zip');
  const [editorCursor, setEditorCursor] = useState({ line: 1, column: 1 });

  function formatApiError(detail, fallback = 'Request failed.') {
    if (!detail) return fallback;
    if (typeof detail === 'string') return detail;
    if (typeof detail === 'object') {
      if (detail.detail !== undefined) return typeof detail.detail === 'string' ? detail.detail : fallback;
      if (detail.message !== undefined) return typeof detail.message === 'string' ? detail.message : fallback;
    }
    return fallback;
  }

  function handleAuthExpired(status) {
    if (status === 401) {
      window.location.reload();
      return true;
    }
    return false;
  }

  const [fileContent, setFileContent] = useState('');
  const [selectedFilePaths, setSelectedFilePaths] = useState([]);
  const [virtualView, setVirtualView] = useState(() => (selectedWebsiteId || fileAppId) ? 'target' : 'domains');
  const [userNavigatedVirtual, setUserNavigatedVirtual] = useState(false);

  const [chmodTarget, setChmodTarget] = useState(null);
  const [chmodMode, setChmodMode] = useState('644');

  function hasFileTarget() {
    return virtualView === 'target' && !!(currentFileApp() || currentSite);
  }

  function openVirtualHome() {
    setUserNavigatedVirtual(true);
    setVirtualView('home');
    setSelectedFilePaths([]);
  }

  function openVirtualDomains() {
    setUserNavigatedVirtual(true);
    setVirtualView('domains');
    setSelectedFilePaths([]);
  }

  function openVirtualApplications() {
    setUserNavigatedVirtual(true);
    setVirtualView('applications');
    setSelectedFilePaths([]);
  }

  function openSiteFolder(site, initialPath = 'public_html') {
    if (!site) return;
    const sameTarget = !fileAppId && String(selectedWebsiteId) === String(site.id);
    setUserNavigatedVirtual(false);
    setFileAppId('');
    setSelectedWebsiteId(String(site.id));
    setFileListPath(initialPath);
    setFileUploadDir(initialPath || '');
    setSelectedFilePaths([]);
    setVirtualView('target');
    if (sameTarget) {
      listFiles(initialPath, site.id, '');
    }
  }

  function openAppFolder(app, initialPath = '') {
    if (!app) return;
    const sameTarget = String(fileAppId) === String(app.id);
    setUserNavigatedVirtual(false);
    setFileAppId(String(app.id));
    setFileListPath(initialPath);
    setFileUploadDir(initialPath || '');
    setSelectedFilePaths([]);
    setVirtualView('target');
    if (sameTarget) {
      listFiles(initialPath, '', app.id);
    }
  }

  function parentFilePath(path) {
    const parts = String(path || '').split('/').filter(Boolean);
    parts.pop();
    return parts.join('/');
  }

  function fileBreadcrumbs(path) {
    const parts = String(path || '').split('/').filter(Boolean);
    let current = '';
    return parts.map(part => {
      current = current ? `${current}/${part}` : part;
      return { label: part, path: current };
    });
  }

  function isTextEditable(item) {
    if (!item || item.is_dir) return false;
    const name = (item.name || '').toLowerCase();
    const editableDotfiles = new Set(['.env', '.env.example', '.htaccess', '.user.ini', '.gitignore', '.gitattributes']);
    return editableDotfiles.has(name) || /\.(txt|md|json|css|js|jsx|ts|tsx|html|htm|xml|yml|yaml|ini|conf|log|php|env|htaccess)$/.test(name) || !name.includes('.');
  }

  function isArchiveFile(item) {
    if (!item || item.is_dir) return false;
    const name = (item.name || '').toLowerCase();
    return name.endsWith('.zip') || name.endsWith('.tar.gz') || name.endsWith('.tgz');
  }

  function toggleFileSelection(path) {
    setSelectedFilePaths(prev => prev.includes(path) ? prev.filter(item => item !== path) : [...prev, path]);
  }

  function toggleAllFiles() {
    setSelectedFilePaths(prev => prev.length === files.length ? [] : files.map(item => item.path));
  }

  function fileTargetBody() {
    return fileAppId ? { app_id: Number(fileAppId) } : { website_id: Number(selectedWebsiteId) };
  }

  function fileTargetBase() {
    return fileAppId ? `/maintenance/app-files/${fileAppId}` : `/maintenance/files/${selectedWebsiteId}`;
  }

  function fileTargetKey() {
    return fileAppId ? `app:${fileAppId}` : (selectedWebsiteId ? `site:${selectedWebsiteId}` : '');
  }

  function fileEditorUrl(path) {
    const url = new URL(window.location.href);
    url.searchParams.delete('page');
    url.searchParams.delete('view');
    url.searchParams.delete('app_id');
    url.searchParams.delete('website_id');
    url.searchParams.delete('path');
    url.searchParams.set('view', 'editor');
    if (fileAppId) url.searchParams.set('app_id', String(fileAppId));
    else url.searchParams.set('website_id', String(selectedWebsiteId));
    url.searchParams.set('path', path);
    return url.toString();
  }

  function openFileEditorTab(path) {
    if (!hasFileTarget() || !path) return;
    window.open(fileEditorUrl(path), '_blank', 'noopener,noreferrer');
  }

  function FileTargetSelect() {
    const selectVal = virtualView !== 'target'
      ? ''
      : (fileAppId ? `app:${fileAppId}` : (currentSite ? String(currentSite.id) : ''));
    return <select
      value={selectVal}
      onChange={e => {
        const value = e.target.value;
        if (!value) {
          openVirtualDomains();
          return;
        }
        if (value.startsWith('app:')) {
          const nextAppId = value.slice(4);
          const appObj = siteApps?.items?.find(a => String(a.id) === String(nextAppId));
          if (appObj) openAppFolder(appObj, '');
        } else {
          const siteObj = websites.find(s => String(s.id) === String(value));
          if (siteObj) openSiteFolder(siteObj, 'public_html');
        }
      }}
    >
      <option value="">{t('-- Select website or application --')}</option>
      {websites.map(site => <option key={`site-${site.id}`} value={site.id}>{site.domain}</option>)}
      {siteApps?.items?.map(app => <option key={`app-${app.id}`} value={`app:${app.id}`}>{t('App:')} {app.name}</option>)}
    </select>;
  }

  const PERMISSION_BITS = [
    { key: 'read', label: 'Read', value: 4 },
    { key: 'write', label: 'Write', value: 2 },
    { key: 'execute', label: 'Execute', value: 1 },
  ];

  const PERMISSION_PRESETS = {
    file: [['644', 'Default'], ['755', 'Executable'], ['600', 'Private'], ['444', 'Read-only']],
    dir: [['755', 'Default'], ['750', 'Group read'], ['775', 'Group write'], ['700', 'Private']],
  };

  function normalizeOctalMode(mode) {
    const value = String(mode ?? '').trim();
    return /^[0-7]{3,4}$/.test(value) ? value : '';
  }

  function octalToPermissionBits(mode) {
    const padded = (normalizeOctalMode(mode) || '0644').padStart(4, '0');
    return {
      special: Number(padded[0]),
      owner: Number(padded[1]),
      group: Number(padded[2]),
      other: Number(padded[3]),
    };
  }

  function permissionBitsToOctal({ special, owner, group, other }) {
    const body = `${owner}${group}${other}`;
    return special ? `${special}${body}` : body;
  }

  function permissionSymbols(mode) {
    const padded = (normalizeOctalMode(mode) || '0644').padStart(4, '0');
    const bits = octalToPermissionBits(padded);
    const renderClass = (classBits, isOwner, isGroup) => {
      let r = classBits & 4 ? 'r' : '-';
      let w = classBits & 2 ? 'w' : '-';
      let x = classBits & 1 ? 'x' : '-';
      if (isOwner && (bits.special & 4)) x = (classBits & 1) ? 's' : 'S';
      if (isGroup && (bits.special & 2)) x = (classBits & 1) ? 's' : 'S';
      if (!isOwner && !isGroup && (bits.special & 1)) x = (classBits & 1) ? 't' : 'T';
      return `${r}${w}${x}`;
    };
    return `d${renderClass(bits.owner, true, false)}${renderClass(bits.group, false, true)}${renderClass(bits.other, false, false)}`;
  }

  function openChmodDialog(items) {
    const targets = (Array.isArray(items) ? items : [items]).filter(Boolean);
    if (targets.length === 0) return;
    setChmodTarget(targets);
    if (targets.length === 1 && normalizeOctalMode(targets[0].mode)) {
      setChmodMode(normalizeOctalMode(targets[0].mode));
      return;
    }
    const specials = targets.map(item => octalToPermissionBits(item.mode).special);
    const special = specials.every(value => value === specials[0]) ? specials[0] : 0;
    const base = targets.every(item => item.is_dir)
      ? { owner: 7, group: 5, other: 5 }
      : { special: 0, owner: 6, group: 4, other: 4 };
    setChmodMode(permissionBitsToOctal({ special, ...base }));
  }

  async function applyChmod() {
    const targets = chmodTarget || [];
    const mode = chmodMode.trim();
    if (targets.length === 0) return;
    if (!/^[0-7]{3,4}$/.test(mode)) { setError(t('Mode must be octal, for example 644 or 755.')); return; }
    for (const item of targets) {
      const data = await request('/maintenance/files/chmod', {
        method: 'POST',
        body: JSON.stringify({ ...fileTargetBody(), path: item.path, mode }),
      }, `Setting permissions on ${item.name}...`);
      if (!data) return;
    }
    setChmodTarget(null);
    setNotice(t('Permissions set to {mode} on {n} item(s).', { mode, n: targets.length }));
    await listFiles(fileListPath);
  }

  function renderChmodDialog() {
    const targets = chmodTarget || [];
    if (targets.length === 0) return null;
    const bits = octalToPermissionBits(chmodMode);
    const onlyDirs = targets.every(item => item.is_dir);
    const hasFiles = targets.some(item => !item.is_dir);
    const worldWritable = !!(bits.other & 2);
    const setBit = (classKey, bitValue) => setChmodMode(permissionBitsToOctal({
      ...bits,
      [classKey]: bits[classKey] ^ bitValue,
    }));
    const title = targets.length === 1 ? targets[0].name : `${targets.length} selected items`;
    return <div className="chmod-backdrop" role="presentation" onClick={() => setChmodTarget(null)}>
      <div className="chmod-dialog" role="dialog" aria-modal="true" aria-label={t('Change permissions')} onClick={e => e.stopPropagation()}>
        <div className="chmod-head">
          <div>
            <h3><Lock size={15}/>{t('Permissions')}</h3>
            <p>{title}</p>
          </div>
          <button className="mini secondary-light" onClick={() => setChmodTarget(null)} aria-label={t('Close')}><X size={14}/></button>
        </div>
        <table className="chmod-grid">
          <thead>
            <tr><th scope="col"></th>{PERMISSION_BITS.map(bit => <th scope="col" key={bit.key}>{bit.label}</th>)}</tr>
          </thead>
          <tbody>
            {[{ key: 'owner', label: 'Owner' }, { key: 'group', label: 'Group' }, { key: 'other', label: 'Public' }].map(cls => <tr key={cls.key}>
              <th scope="row">{cls.label}</th>
              {PERMISSION_BITS.map(bit => <td key={bit.key}>
                <input
                  type="checkbox"
                  checked={!!(bits[cls.key] & bit.value)}
                  onChange={() => setBit(cls.key, bit.value)}
                  aria-label={`${cls.label} ${bit.label}`}
                />
              </td>)}
            </tr>)}
          </tbody>
        </table>
        <div className="chmod-value">
          <label>
            <span>{t('Octal')}</span>
            <input value={chmodMode} inputMode="numeric" maxLength={4} onChange={e => setChmodMode(e.target.value.replace(/[^0-7]/g, '').slice(0, 4))} />
          </label>
          <code>{permissionSymbols(chmodMode)}</code>
        </div>
        <div className="chmod-presets">
          {(onlyDirs ? PERMISSION_PRESETS.dir : PERMISSION_PRESETS.file).map(([preset, label]) => <button
            key={preset}
            type="button"
            className={`mini ${chmodMode === preset ? '' : 'secondary-light'}`}
            onClick={() => setChmodMode(preset)}
          >{preset} <small>{t(label)}</small></button>)}
        </div>
        {onlyDirs && <label className="chmod-setgid">
          <input
            type="checkbox"
            checked={bits.special === 2}
            onChange={() => setChmodMode(permissionBitsToOctal({ ...bits, special: bits.special === 2 ? 0 : 2 }))}
          />
          <span>{t("Setgid — new files inside keep the folder's group. spanel sets this on site folders; leave it on unless you know otherwise.")}</span>
        </label>}
        {worldWritable && <p className="chmod-note warn">
          <AlertCircle size={13}/> {hasFiles
            ? t('World-writable: anyone with an account on the server can change these files. Use 755 unless something really needs it.')
            : t('World-writable: anyone with an account on the server can change what is inside these folders. Use 755 unless something really needs it.')}
        </p>}
        <p className="chmod-note">
          {t('Any permission combination is allowed. The setuid and sticky bits are not — setgid on a folder is the only special bit the panel sets.')}
        </p>
        <div className="chmod-actions">
          <button className="secondary-light" disabled={!!loading} onClick={() => setChmodTarget(null)}>{t('Cancel')}</button>
          <button disabled={!!loading} onClick={applyChmod}><Check size={14}/> Apply {chmodMode}</button>
        </div>
      </div>
    </div>;
  }


  useEffect(() => { if (hasFileTarget()) loadFileJobs(); }, []);
  useEffect(() => {
    if (fileJobs.length > 0 && !loading) {
      const running = fileJobs.filter(j => j.status === 'running');
      if (running.length > 0) {
        const timer = setTimeout(() => { loadFileJobs(); }, 2000);
        return () => clearTimeout(timer);
      }
    }
  }, [fileJobs, loading]);
  useEffect(() => {
    const activeApp = currentFileApp();
    if (activeApp || currentSite) {
      if (virtualView === 'target') {
        listFiles(fileListPath);
      }
    } else if (!userNavigatedVirtual && websites.length === 1 && !(siteApps?.items?.length > 0)) {
      setSelectedWebsiteId(String(websites[0].id));
      setFileListPath('public_html');
      setVirtualView('target');
    } else if (virtualView === 'target') {
      setVirtualView('domains');
    }
  }, [selectedWebsiteId, fileAppId, websites.length, Boolean(currentSite)]);

  async function listFiles(path = fileListPath, overrideSiteId = null, overrideAppId = null) {
    const effAppId = overrideAppId !== null ? overrideAppId : fileAppId;
    const effSiteId = overrideSiteId !== null ? overrideSiteId : selectedWebsiteId;
    if (!effAppId && !effSiteId) return;
    const base = effAppId ? `/maintenance/app-files/${effAppId}` : `/maintenance/files/${effSiteId}`;
    const data = await request(`${base}?path=${encodeURIComponent(path)}`, {}, t('Loading file list...'));
    if (data?.items) { setFiles(data.items); setFileListPath(path); setFileUploadDir(path || ''); setSelectedFilePaths([]); }
  }

  function navigateUpDirectory() {
    if (hasFileTarget()) {
      if (fileListPath && fileListPath !== '') {
        listFiles(parentFilePath(fileListPath));
      } else if (currentFileApp()) {
        openVirtualApplications();
      } else {
        openVirtualDomains();
      }
    } else if (virtualView === 'domains' || virtualView === 'applications') {
      openVirtualHome();
    }
  }

  async function readFile(pathOverride = filePath) {
    const targetPath = pathOverride || filePath;
    if (!hasFileTarget() || !targetPath) return;
    if (pathOverride) setFilePath(pathOverride);
    const data = await request(`${fileTargetBase()}/read?path=${encodeURIComponent(targetPath)}`, {}, t('Reading file...'));
    if (data?.content !== undefined) {
      setFileContent(data.content);
      setEditorCursor({ line: 1, column: 1 });
    }
  }

  async function writeFile() {
    const data = await request('/maintenance/files/write', { method: 'POST', body: JSON.stringify({ ...fileTargetBody(), path: filePath, content: fileContent }) }, t('Saving file...'));
    if (data) {
      const savedPath = data.path || data.target || filePath;
      setNotice(t('Saved {path}', { path: savedPath }));
      await listFiles(fileListPath);
      await loadCurrentUser();
    }
  }

  async function downloadFile(path) {
    if (!hasFileTarget() || !path) return;
    try {
      setError(''); setLoading('Downloading file...');
      const res = await fetch(`${API}${fileTargetBase()}/download?path=${encodeURIComponent(path)}`, { credentials: 'include' });
      if (!res.ok) { const data = await res.json().catch(() => ({})); if (handleAuthExpired(res.status, data.detail)) return; setError(formatApiError(data.detail, 'Download failed.')); return; }
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url; link.download = path.split('/').pop() || 'download';
      document.body.appendChild(link); link.click(); link.remove();
      URL.revokeObjectURL(url);
    } catch (err) { setError(t('File download failed.')); }
    finally { setLoading(''); }
  }

  async function makeFileDirectory() {
    if (!hasFileTarget()) return;
    const name = prompt('Folder name:');
    if (!name) return;
    const data = await request('/maintenance/files/mkdir', { method: 'POST', body: JSON.stringify({ ...fileTargetBody(), path: fileListPath || '', name }) }, t('Creating folder...'));
    if (data) await listFiles(fileListPath);
  }

  async function makeFile() {
    if (!hasFileTarget()) return;
    const name = prompt('File name:', 'new-file.txt');
    if (!name) return;
    const data = await request('/maintenance/files/create', { method: 'POST', body: JSON.stringify({ ...fileTargetBody(), path: fileListPath || '', name }) }, t('Creating file...'));
    if (data) {
      await listFiles(fileListPath);
      const newPath = [fileListPath, name].filter(Boolean).join('/');
      openFileEditorTab(newPath);
    }
  }

  async function renameFileItem(item) {
    if (!item) return;
    const newName = prompt('New name:', item.name);
    if (!newName || newName === item.name) return;
    const data = await request('/maintenance/files/rename', { method: 'POST', body: JSON.stringify({ ...fileTargetBody(), path: item.path, new_name: newName }) }, t('Renaming...'));
    if (data) await listFiles(fileListPath);
  }

  async function deleteSelectedFiles() {
    if (selectedFilePaths.length === 0) return;
    if (!confirm(t('Delete {n} selected item(s)?', { n: selectedFilePaths.length }))) return;
    const data = await request('/maintenance/files/delete', { method: 'POST', body: JSON.stringify({ ...fileTargetBody(), paths: selectedFilePaths }) }, t('Deleting selected files...'));
    if (data) { await listFiles(fileListPath); await loadCurrentUser(); }
  }

  async function transferFileItems(action, paths) {
    if (!hasFileTarget() || !paths?.length) return;
    const verb = action === 'copy' ? 'Copy' : 'Move';
    const destination = prompt(`${verb} to folder:`, fileListPath || 'public_html');
    if (destination === null) return;
    const targetPath = destination.trim() || fileListPath || 'public_html';
    const data = await request(`/maintenance/files/${action}`, {
      method: 'POST',
      body: JSON.stringify({ ...fileTargetBody(), paths, destination_path: targetPath }),
    }, `${verb}ing files...`);
    if (data) { await listFiles(fileListPath); await loadCurrentUser(); }
  }

  async function copySelectedFiles() {
    await transferFileItems('copy', selectedFilePaths);
  }

  async function moveSelectedFiles() {
    await transferFileItems('move', selectedFilePaths);
  }

  async function archiveSelectedFiles() {
    if (selectedFilePaths.length === 0) return;
    const ext = archiveFormat === 'tar.gz' ? 'tar.gz' : 'zip';
    const outputName = prompt('Archive file name:', `archive-${Date.now()}.${ext}`);
    if (!outputName) return;
    const data = await request('/maintenance/files/archive', {
      method: 'POST',
      body: JSON.stringify({ ...fileTargetBody(), base_path: fileListPath || '', paths: selectedFilePaths, output_name: outputName, format: archiveFormat }),
    }, t('Creating archive...'));
    if (data) { await listFiles(fileListPath); await loadCurrentUser(); }
  }

  async function extractArchiveFile(path) {
    if (!hasFileTarget() || !path) return;
    const destination = prompt('Extract to folder:', fileListPath || '.');
    if (destination === null) return;
    const targetPath = destination.trim() || fileListPath || '.';
    const data = await request('/maintenance/files/extract', {
      method: 'POST',
      body: JSON.stringify({ ...fileTargetBody(), archive_path: path, destination_path: targetPath }),
    }, t('Starting extraction...'));
    if (data?.job_id) upsertFileJob(data);
    else if (data) { await listFiles(targetPath === '.' ? '' : targetPath); await loadCurrentUser(); }
  }

  async function loadFileJob(jobId) {
    try {
      const res = await fetch(`${API}/maintenance/files/jobs/${jobId}`, { credentials: 'include' });
      const text = await res.text();
      let data;
      try { data = text ? JSON.parse(text) : {}; } catch { data = { detail: text || `HTTP ${res.status}` }; }
      if (!res.ok && handleAuthExpired(res.status, data.detail)) return null;
      if (!res.ok) return null;
      return data;
    } catch {
      return null;
    }
  }

  async function loadFileJobs() {
    const data = await request('/maintenance/files/jobs');
    if (data?.jobs) setFileJobs(data.jobs.filter(job => job.status !== 'done').slice(0, 6));
  }

  async function uploadSiteFile(file) {
    if (!file) return;
    if (!hasFileTarget()) { setError(t('Please select a website or application first.')); return; }
    const uploadDir = fileUploadDir.trim();
    const form = new FormData();
    form.append('file', file);
    try {
      setError('');
      setLoading('Uploading file...');
      const csrfToken = readCookie('spanel_csrf');
      const headers = csrfToken ? { 'X-CSRF-Token': csrfToken } : {};
      const res = await fetch(`${API}${fileTargetBase()}/upload?path=${encodeURIComponent(uploadDir)}`, {
        method: 'POST',
        credentials: 'include',
        headers,
        body: form,
      });
      const responseText = await res.text();
      let data;
      try { data = responseText ? JSON.parse(responseText) : {}; } catch { data = { detail: responseText || `HTTP ${res.status}` }; }
      if (!res.ok) { if (handleAuthExpired(res.status, data.detail)) return; setError(formatApiError(data.detail, 'Upload failed.')); return; }
      setNotice(`Uploaded ${file.name} to ${uploadDir || 'site root'}.`);
      if (String(fileListPath || '') === uploadDir) await listFiles(uploadDir);
      await loadCurrentUser();
    } catch (err) { setError(t('File upload failed.')); }
    finally { setLoading(''); }
  }

  function renderFiles() {
    const allSelected = files.length > 0 && selectedFilePaths.length === files.length;
    const selectedArchiveFile = selectedFilePaths.length === 1
      ? files.find(item => item.path === selectedFilePaths[0] && isArchiveFile(item))
      : null;
    const activeFileApp = currentFileApp();
    const isTargetActive = hasFileTarget();
    const targetKey = fileTargetKey();
    const visibleFileJobs = fileJobs
      .filter(job => (job.target_key || `site:${job.website_id}`) === targetKey && job.status !== 'done')
      .slice(0, 4);
    const selectedChmodItems = files.filter(item => selectedFilePaths.includes(item.path));
    const crumbs = isTargetActive ? fileBreadcrumbs(fileListPath) : [];
    const defaultShortcutSite = currentSite || websites[0] || null;
    const virtualItemCount = virtualView === 'home'
      ? (1 + (siteApps?.items?.length > 0 ? 1 : 0) + (defaultShortcutSite ? 1 : 0))
      : virtualView === 'applications'
        ? (siteApps?.items?.length || 0)
        : websites.length;

    return <div className="section">
      {renderChmodDialog()}
      <div className="section-title">
        <div><h2>{t('File manager')}</h2></div>
        <button className="secondary-light" disabled={!isTargetActive || !!loading} onClick={() => listFiles(fileListPath)}><RefreshCw size={14}/>{t('Refresh')}</button>
      </div>
      <div className="file-manager">
        <div className="file-panel">
          <div className="file-controls">
            <FileTargetSelect />
            <div className="file-meta">
              {isTargetActive && activeFileApp ? (
                <>
                  <span>{t('Application:')}{' '}<strong>{activeFileApp.name}</strong></span>
                  <span>{t('Path:')}{' '}<strong>/applications/{activeFileApp.name}{fileListPath ? `/${fileListPath}` : ''}</strong></span>
                </>
              ) : isTargetActive && currentSite ? (
                <>
                  <span>{t('Website:')}{' '}<strong>{currentSite.domain}</strong></span>
                  <span>{t('Path:')}{' '}<strong>/domains/{currentSite.domain}{fileListPath ? `/${fileListPath}` : ''}</strong></span>
                </>
              ) : (
                <>
                  <span>{t('Path:')}{' '}<strong>/{virtualView === 'home' ? '' : virtualView}</strong></span>
                  <span>{t('{n} domain(s)', { n: websites.length })}</span>
                </>
              )}
              {currentUser && !isAdmin && <span className="file-meta-storage">{t('Storage:')}{' '}<strong>{storageUsageText(currentUser)}</strong></span>}
            </div>
            <nav className="da-file-breadcrumb" aria-label={t('File path')}>
              <button
                type="button"
                className={`da-crumb-home ${virtualView === 'home' ? 'da-crumb-current' : ''}`}
                onClick={openVirtualHome}
                title={t('Home')}
              >
                <Home size={17} />
              </button>
              {virtualView !== 'home' && <>
                <ChevronRight size={15} className="da-crumb-sep" />
                {isTargetActive && activeFileApp ? (
                  <button
                    type="button"
                    className="da-crumb-link"
                    onClick={openVirtualApplications}
                  >
                    applications
                  </button>
                ) : virtualView === 'applications' ? (
                  <button
                    type="button"
                    className="da-crumb-link da-crumb-current"
                    onClick={openVirtualApplications}
                  >
                    applications
                  </button>
                ) : (
                  <button
                    type="button"
                    className={`da-crumb-link ${!isTargetActive && virtualView === 'domains' ? 'da-crumb-current' : ''}`}
                    onClick={openVirtualDomains}
                  >
                    domains
                  </button>
                )}
              </>}
              {isTargetActive && (activeFileApp || currentSite) && <>
                <ChevronRight size={15} className="da-crumb-sep" />
                <button
                  type="button"
                  className={`da-crumb-link ${crumbs.length === 0 ? 'da-crumb-current' : ''}`}
                  onClick={() => listFiles('')}
                >
                  {activeFileApp ? activeFileApp.name : currentSite.domain}
                </button>
              </>}
              {crumbs.map((crumb, idx) => <React.Fragment key={crumb.path}>
                <ChevronRight size={15} className="da-crumb-sep" />
                <button
                  type="button"
                  className={`da-crumb-link ${idx === crumbs.length - 1 ? 'da-crumb-current' : ''}`}
                  onClick={() => listFiles(crumb.path)}
                >
                  {crumb.label}
                </button>
              </React.Fragment>)}
            </nav>
            <div className="file-toolbar">
              <button className="secondary" disabled={virtualView === 'home' || !!loading} onClick={navigateUpDirectory}>{t('Up')}</button>
              <button className="secondary" disabled={!isTargetActive || !!loading} onClick={makeFileDirectory}><Plus size={14}/>{t('Folder')}</button>
              <button className="secondary" disabled={!isTargetActive || !!loading} onClick={makeFile}><FileText size={14}/>{t('File')}</button>
              <label className={`upload-button ${(!isTargetActive || !!loading) ? 'disabled' : ''}`}>
                <Upload size={14}/>{t('Upload')}<input type="file" disabled={!isTargetActive || !!loading} onChange={e => { uploadSiteFile(e.target.files?.[0]); e.target.value = ''; }} />
              </label>
              <select value={archiveFormat} onChange={e => setArchiveFormat(e.target.value)} disabled={!isTargetActive || !!loading}>
                <option value="zip">zip</option>
                <option value="tar.gz">tar.gz</option>
              </select>
              <button className="secondary" disabled={!isTargetActive || selectedFilePaths.length === 0 || !!loading} onClick={copySelectedFiles}><Copy size={14}/>{t('Copy')}</button>
              <button className="secondary" disabled={!isTargetActive || selectedFilePaths.length === 0 || !!loading} onClick={moveSelectedFiles}><MoveRight size={14}/>{t('Move')}</button>
              <button className="secondary" disabled={!isTargetActive || selectedFilePaths.length === 0 || !!loading} onClick={archiveSelectedFiles}><Archive size={14}/>{t('Archive')}</button>
              <button className="secondary" disabled={!isTargetActive || !selectedArchiveFile || !!loading} onClick={() => extractArchiveFile(selectedArchiveFile.path)}><ArchiveRestore size={14}/>{t('Extract')}</button>
              <button disabled={!isTargetActive || selectedChmodItems.length === 0 || !!loading} onClick={() => openChmodDialog(selectedChmodItems)}><Lock size={14}/>{t('Permissions')}</button>
              <button className="danger" disabled={!isTargetActive || selectedFilePaths.length === 0 || !!loading} onClick={deleteSelectedFiles}><Trash2 size={14}/>{t('Delete')}</button>
            </div>
            {visibleFileJobs.length > 0 && <div className="file-job-list">
              {visibleFileJobs.map(job => <div className={`file-job ${job.status}`} key={job.job_id}>
                <Clock size={14}/>
                <span><strong>{job.archive_path?.split('/').pop() || 'Archive'}</strong> {job.status === 'error' ? 'failed' : job.status}</span>
                {job.error && <small>{job.error}</small>}
                <button className="file-job-dismiss" onClick={() => dismissFileJob(job.job_id)} aria-label={t('Dismiss')}><X size={13}/></button>
              </div>)}
            </div>}
          </div>
          <div className="file-list-header">
            <label><input type="checkbox" checked={isTargetActive && allSelected} onChange={toggleAllFiles} disabled={!isTargetActive || files.length === 0} /><span className="sr-only">{t('Select')}</span></label>
            <span>{t('Name')}</span>
            <span>{t('Mode')}</span>
            <span>{t('Size')}</span>
            <span>{t('Modified')}</span>
            <span className="file-list-count">{t('{n} item(s)', { n: isTargetActive ? files.length : virtualItemCount })}</span>
          </div>
          <div className="file-list">
            {!isTargetActive && virtualView === 'home' && <>
              <div className="file-item" key="virtual-domains">
                <input type="checkbox" disabled />
                <button className="file-name" onClick={openVirtualDomains}>
                  <FolderOpen size={16}/> <strong>domains</strong>
                </button>
                <span className="file-mode">0755</span>
                <span className="file-size">{t('Folder')}</span>
                <span className="file-modified">{t('{n} domain(s)', { n: websites.length })}</span>
                <div className="file-row-actions">
                  <button className="mini secondary-light" onClick={openVirtualDomains}>{t('Open')}</button>
                </div>
              </div>
              {siteApps?.items?.length > 0 && <div className="file-item" key="virtual-applications">
                <input type="checkbox" disabled />
                <button className="file-name" onClick={openVirtualApplications}>
                  <FolderOpen size={16}/> <strong>applications</strong>
                </button>
                <span className="file-mode">0755</span>
                <span className="file-size">{t('Folder')}</span>
                <span className="file-modified">{t('{n} item(s)', { n: siteApps.items.length })}</span>
                <div className="file-row-actions">
                  <button className="mini secondary-light" onClick={openVirtualApplications}>{t('Open')}</button>
                </div>
              </div>}
              {defaultShortcutSite && <div className="file-item" key="virtual-public-html">
                <input type="checkbox" disabled />
                <button className="file-name" onClick={() => openSiteFolder(defaultShortcutSite, 'public_html')}>
                  <FolderOpen size={16}/> <strong>public_html</strong>
                  <small className="da-symlink-badge">→ domains/{defaultShortcutSite.domain}/public_html</small>
                </button>
                <span className="file-mode">0777</span>
                <span className="file-size">{t('Shortcut')}</span>
                <span className="file-modified">{defaultShortcutSite.domain}</span>
                <div className="file-row-actions">
                  <button className="mini secondary-light" onClick={() => openSiteFolder(defaultShortcutSite, 'public_html')}>{t('Open')}</button>
                </div>
              </div>}
            </>}
            {!isTargetActive && virtualView === 'domains' && <>
              {websites.length === 0 && <div className="empty-box">{t('No websites found.')}</div>}
              {websites.map(site => <div className="file-item" key={`domain-folder-${site.id}`}>
                <input type="checkbox" disabled />
                <button className="file-name" onClick={() => openSiteFolder(site, 'public_html')}>
                  <FolderOpen size={16}/> <strong>{site.domain}</strong>
                </button>
                <span className="file-mode">0755</span>
                <span className="file-size">{t('Folder')}</span>
                <span className="file-modified">PHP {site.php_version || '8.4'}</span>
                <div className="file-row-actions">
                  <button className="mini secondary-light" onClick={() => openSiteFolder(site, 'public_html')}>public_html</button>
                  <button className="mini secondary-light" onClick={() => openSiteFolder(site, '')}>{t('Root')}</button>
                </div>
              </div>)}
            </>}
            {!isTargetActive && virtualView === 'applications' && <>
              {(!siteApps?.items || siteApps.items.length === 0) && <div className="empty-box">{t('No files in this folder.')}</div>}
              {(siteApps?.items || []).map(app => <div className="file-item" key={`app-folder-${app.id}`}>
                <input type="checkbox" disabled />
                <button className="file-name" onClick={() => openAppFolder(app, '')}>
                  <FolderOpen size={16}/> <strong>{app.name}</strong>
                </button>
                <span className="file-mode">0755</span>
                <span className="file-size">{t('Folder')}</span>
                <span className="file-modified">{app.runtime || 'app'}</span>
                <div className="file-row-actions">
                  <button className="mini secondary-light" onClick={() => openAppFolder(app, '')}>{t('Open')}</button>
                </div>
              </div>)}
            </>}
            {isTargetActive && files.length === 0 && <div className="empty-box">{t('No files in this folder.')}</div>}
            {isTargetActive && files.map(item => <div className={`file-item ${selectedFilePaths.includes(item.path) ? 'selected' : ''}`} key={item.path}>
              <input type="checkbox" checked={selectedFilePaths.includes(item.path)} onChange={() => toggleFileSelection(item.path)} />
              <button className="file-name" onClick={() => item.is_dir ? listFiles(item.path) : (isTextEditable(item) ? openFileEditorTab(item.path) : downloadFile(item.path))}>
                {item.is_dir ? <FolderOpen size={16}/> : <FileText size={16}/>} <strong>{item.name}</strong>
              </button>
              <button
                className="file-mode"
                type="button"
                disabled={!!loading}
                title={`Permissions ${item.mode || '---'} (${permissionSymbols(item.mode)}) - click to change`}
                onClick={() => openChmodDialog(item)}
              >{item.mode || '---'}</button>
              <span className="file-size">{item.is_dir ? t('Folder') : formatBytes(item.size)}</span>
              <span className="file-modified" title={formatFileTime(item.modified, true)}>{formatFileTime(item.modified)}</span>
              <div className="file-row-actions">
                {!item.is_dir && <button className="mini secondary-light" disabled={!!loading} onClick={() => downloadFile(item.path)}><Download size={13}/></button>}
                {isArchiveFile(item) && <button className="mini secondary-light" disabled={!!loading} onClick={() => extractArchiveFile(item.path)}><ArchiveRestore size={13}/>{t('Extract')}</button>}
                <button className="mini secondary-light" disabled={!!loading} onClick={() => openChmodDialog(item)}><Lock size={13}/>{t('Perms')}</button>
                <button className="mini secondary-light" disabled={!!loading} onClick={() => renameFileItem(item)}>{t('Rename')}</button>
              </div>
            </div>)}
          </div>
        </div>
      </div>
    </div>;
  }
  return renderFiles();
}
