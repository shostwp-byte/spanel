import React, { useState } from 'react';
import { Archive, ArchiveRestore, ArrowLeft, Ban, Bot, Boxes, Check, ChevronDown, Clock, Code2, Copy, Cpu, Database, Dices, ExternalLink, Eye, FileText, FolderOpen, Globe, HardDrive, Home, Image, KeyRound, Lock, LogIn, LogOut, MemoryStick, Menu, Moon, MoveRight, Network, Pencil, Save, Search, Server, Settings as SettingsIcon, Shield, Sun, Trash2, TerminalIcon, Users, X, RefreshCw, Plus, Download, Upload, Play, Square, RotateCcw, AlertCircle, Activity, BrickWall, Bug, LockKeyhole, PackageOpen, ScrollText, ShieldAlert, CheckCircle, Zap, Bell, Mail, Send, Cloud, Inbox, Forward, ShieldCheck } from 'lucide-react';
import { EmptyState } from '../components/EmptyState.jsx';
import { useAppContext } from '../contexts/AppContext.jsx';
import { t } from '../i18n.js';
import { NoAutofillInput } from '../components/NoAutofillInput.jsx';

export function DatabasesPage({ request, API, navigateToPage, refreshAll }) {
  const {
    loading, setLoading, setError, setNotice, databases, setDatabases, dbSearch, setDbSearch, dbCreateOpen, setDbCreateOpen,
    websites, readCookie, handleAuthExpired, formatApiError,
    copyText, generateRandomPassword, copiedField, setCopiedField,
  } = useAppContext();
  const [dbSearching, setDbSearching] = useState(false);
  const [newDatabase, setNewDatabase] = useState({ db_name: '', db_user: '', db_password: '', db_host_id: '' });
  // Where a new database can go: localhost first, then any remote MySQL
  // host the administrator has enabled. The picker only shows with a choice.
  const [dbHosts, setDbHosts] = useState([]);
  const [createdDbInfo, setCreatedDbInfo] = useState(null);
  const [tableModal, setTableModal] = useState(null);

  function fmtDbSize(bytes) {
    const n = Number(bytes) || 0;
    if (n <= 0) return '0 KB';
    if (n < 1024 * 1024) return `${Math.max(1, Math.round(n / 1024))} KB`;
    return `${(n / (1024 * 1024)).toFixed(1)} MB`;
  }

  async function handleInspectDbTables(dbItem) {
    const res = await request(`/resources/databases/${dbItem.id}/tables`, {}, t('Inspecting tables...'));
    if (res) setTableModal(res);
  }

  async function handleOptimizeDb(dbItem) {
    const res = await request(`/resources/databases/${dbItem.id}/optimize`, { method: 'POST' }, t('Optimizing database...'));
    if (res) {
      setNotice(t('Optimized {n} table(s) in {db}.', { n: res.optimized_tables || 0, db: dbItem.db_name }));
      await loadDatabases(dbSearch, false);
    }
  }

  React.useEffect(() => {
    loadDatabases(dbSearch, false);
  }, [dbSearch]);

  React.useEffect(() => {
    request('/databases/hosts', { silent: true }).then(data => { if (data?.hosts) setDbHosts(data.hosts); });
  }, []);

  async function loadDatabases(search = dbSearch, showLoading = false) {
    const query = String(search || '').trim();
    const suffix = query ? `?q=${encodeURIComponent(query)}` : '';
    setDbSearching(true);
    const data = await request(`/databases${suffix}`, {}, showLoading ? 'Loading databases...' : '');
    setDbSearching(false);
    if (data) setDatabases(data);
  }

  async function changeDbPassword(id) {
    const newPass = prompt('Enter a new database password, minimum 12 characters:');
    if (!newPass) return;
    await request(`/databases/${id}/password`, { method: 'POST', body: JSON.stringify({ password: newPass }) }, t('Changing database password...'));
  }

  async function deleteDatabase(id, dbName) {
    if (!confirm(`Delete database "${dbName}"? This action cannot be undone.`)) return;
    const data = await request(`/databases/${id}`, { method: 'DELETE' }, t('Deleting database...'));
    if (data) {
      setNotice(`Database "${dbName}" deleted successfully.`);
      await loadDatabases();
      await refreshAll();
    }
  }

  async function createDatabase() {
    const validDbName = /^[a-zA-Z0-9_]+$/;
    const dbName = newDatabase.db_name.trim();
    const dbUser = newDatabase.db_user.trim();
    const dbPass = newDatabase.db_password.trim();
    if (!dbName) { setError(t('Please enter a database name.')); return; }
    if (!validDbName.test(dbName)) { setError(t('Database name can only contain letters, numbers and underscores (no spaces or special characters).')); return; }
    if (dbUser && !validDbName.test(dbUser)) { setError(t('Database user can only contain letters, numbers and underscores (no spaces or special characters).')); return; }
    if (dbPass && dbPass.length < 12) { setError(t('Password must be at least 12 characters.')); return; }
    if (dbPass && /[^\x20-\x7E]/.test(dbPass)) { setError(t('Password contains invalid characters. Use only ASCII characters.')); return; }
    const body = {
      db_name: dbName,
      db_user: dbUser || null,
      db_password: dbPass || null,
      db_host_id: newDatabase.db_host_id ? Number(newDatabase.db_host_id) : null,
    };
    const data = await request('/databases', { method: 'POST', body: JSON.stringify(body) }, t('Creating database...'));
    if (data) {
      setCreatedDbInfo({ db_name: data.db_name, db_user: data.db_user, db_password: data.db_password, db_host_address: data.db_host_address || 'localhost' });
      setNewDatabase({ db_name: '', db_user: '', db_password: '', db_host_id: '' });
      await loadDatabases();
      await refreshAll();
    }
  }

  async function openPhpMyAdmin(databaseId) {
    try {
      setError(''); setLoading('Opening phpMyAdmin...');
      const csrfToken = readCookie('spanel_csrf');
      const headers = csrfToken ? { 'X-CSRF-Token': csrfToken } : {};
      const res = await fetch(`${API}/databases/${databaseId}/phpmyadmin-sso`, {
        method: 'POST',
        credentials: 'include',
        headers,
      });
      const data = await res.json().catch(() => ({}));
      if (handleAuthExpired(res.status, data.detail)) return;
      if (!res.ok || !data.url) { setError(formatApiError(data.detail, 'Cannot open phpMyAdmin.')); return; }
      window.open(data.url, '_blank', 'noopener,noreferrer');
    } catch (err) { setError(t('Cannot open phpMyAdmin.')); }
    finally { setLoading(''); }
  }

  async function downloadDatabase(databaseId, databaseName) {
    try {
      setError(''); setLoading('Downloading database...');
      const res = await fetch(`${API}/databases/${databaseId}/download`, { credentials: 'include' });
      if (!res.ok) { const data = await res.json().catch(() => ({})); if (handleAuthExpired(res.status, data.detail)) return; setError(formatApiError(data.detail, 'Download failed.')); return; }
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url; link.download = `${databaseName || 'database'}.sql`;
      document.body.appendChild(link); link.click(); link.remove();
      URL.revokeObjectURL(url);
      setNotice(t('Database SQL downloaded.'));
    } catch (err) { setError(t('Database download failed.')); }
    finally { setLoading(''); }
  }

  function renderDatabases() {
    function copyToClipboard(text, field) {
      const doCopy = navigator.clipboard ? navigator.clipboard.writeText(text) : new Promise((resolve, reject) => {
        try { const ta = document.createElement('textarea'); ta.value = text; ta.style.position = 'fixed'; ta.style.opacity = '0'; document.body.appendChild(ta); ta.select(); document.execCommand('copy'); document.body.removeChild(ta); resolve(); } catch(e) { reject(e); }
      });
      doCopy.then(() => { setCopiedField(field); setTimeout(() => setCopiedField(null), 2000); }).catch(() => setError(t('Copy failed.')));
    }
    const dbSearchActive = !!dbSearch.trim();
    // Like the website form: making one is occasional, finding one is why
    // the page is open. Open by itself only when there is nothing to find.
    const dbCreateVisible = dbCreateOpen || (databases.length === 0 && !dbSearchActive);
    return <div className="section">
      <div className="section-title">
        <h2>{t('Databases')}</h2>
        <div className="actions">
          <button className="secondary" disabled={!!loading || dbSearching} onClick={() => loadDatabases(dbSearch, true)}><RefreshCw size={15} className={dbSearching ? 'spin' : ''}/>{t('Refresh')}</button>
          {!dbCreateVisible && <button type="button" onClick={() => setDbCreateOpen(true)}><Plus size={15}/>{t('New database')}</button>}
        </div>
      </div>
      {dbCreateVisible && <div className="create-inline">
        <div className="create-inline-head">
          <strong>{t('Create database')}</strong>
          {databases.length > 0 && <button type="button" className="secondary icon-only mini" onClick={() => setDbCreateOpen(false)} aria-label={t('Close')} title={t('Close')}><X size={15}/></button>}
        </div>
        <div className="form-row">
          <input name="new-db-name" autoComplete="off" value={newDatabase.db_name} onChange={e => setNewDatabase(prev => ({ ...prev, db_name: e.target.value }))} placeholder="database_name" />
          <input name="new-db-user" autoComplete="off" value={newDatabase.db_user} onChange={e => setNewDatabase(prev => ({ ...prev, db_user: e.target.value }))} placeholder={t('db_user (default = db_name)')} />
          {/* new-password, or the browser offers the panel's own password here. */}
          <input name="new-db-password" autoComplete="new-password" value={newDatabase.db_password} onChange={e => setNewDatabase(prev => ({ ...prev, db_password: e.target.value }))} placeholder={t('password (min 12 chars)')} />
          <button type="button" className="mini secondary-light" title={t('Generate random password')} onClick={() => setNewDatabase(prev => ({ ...prev, db_password: generateRandomPassword() }))}><Dices size={13}/></button>
          {dbHosts.length > 1 && (
            <select name="new-db-host" value={newDatabase.db_host_id} onChange={e => setNewDatabase(prev => ({ ...prev, db_host_id: e.target.value }))} aria-label={t('Database server')} title={t('Database server')}>
              {dbHosts.map(h => <option key={h.id ?? 'local'} value={h.id ?? ''}>{h.id == null ? t('This server (localhost)') : `${h.name} (${h.address})`}</option>)}
            </select>
          )}
          <button disabled={!!loading || !newDatabase.db_name.trim()} onClick={createDatabase}><Plus size={15}/>{t('Create database')}</button>
        </div>
      </div>}
      <div className="list-search">
        <Search size={15}/>
        <NoAutofillInput
          type="search"
          name="database-search"
          value={dbSearch}
          onChange={e => setDbSearch(e.target.value)}
          placeholder={t('Search by database or user name')}
          aria-label={t('Search databases')}
        />
        {dbSearch && <button className="mini secondary-light" type="button" onClick={() => setDbSearch('')} aria-label={t('Clear database search')} title={t('Clear search')}><X size={13}/></button>}
        <span className="hint">{t('{count} databases', { count: databases.length })}</span>
      </div>
      {createdDbInfo && <div className="info-box db-created-box">
        <div className="db-created-head"><strong>{t('Database created successfully')}</strong><button className="mini secondary-light" onClick={() => setCreatedDbInfo(null)}><X size={13}/></button></div>
        <div className="db-created-grid">
          <label>{t('Database')}</label><span>{createdDbInfo.db_name} <button className="mini secondary-light" title={copiedField === 'db_name' ? 'Copied!' : 'Copy'} onClick={() => copyToClipboard(createdDbInfo.db_name, 'db_name')}>{copiedField === 'db_name' ? <Check size={12} style={{color:'var(--green)'}}/> : <Copy size={12}/>}</button></span>
          <label>{t('User')}</label><span>{createdDbInfo.db_user} <button className="mini secondary-light" title={copiedField === 'db_user' ? 'Copied!' : 'Copy'} onClick={() => copyToClipboard(createdDbInfo.db_user, 'db_user')}>{copiedField === 'db_user' ? <Check size={12} style={{color:'var(--green)'}}/> : <Copy size={12}/>}</button></span>
          <label>{t('Host')}</label><span><code>{createdDbInfo.db_host_address}</code></span>
          <label>{t('Password')}</label><span><code>{createdDbInfo.db_password}</code> <button className="mini secondary-light" title={copiedField === 'db_password' ? 'Copied!' : 'Copy'} onClick={() => copyToClipboard(createdDbInfo.db_password, 'db_password')}>{copiedField === 'db_password' ? <Check size={12} style={{color:'var(--green)'}}/> : <Copy size={12}/>}</button></span>
        </div>
      </div>}
      {databases.length === 0 && !createdDbInfo && <EmptyState icon={Database} message={dbSearchActive ? 'No databases match this search.' : 'No databases found.'} />}
      <div className="table">
        {databases.map(db => {
          // Name and the site it belongs to, the login user, then the actions:
          // phpMyAdmin as the one labelled button, the rest as glyphs.
          const dbSite = websites.find(site => String(site.id) === String(db.website_id));
          return <div className="row db-row" key={db.id}>
          <span style={{ display: 'flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap' }}>
            <strong>{db.db_name}</strong>
            {dbSite && <small className="db-owner">{dbSite.domain}</small>}
            <span className="badge">{fmtDbSize(db.size_bytes)}</span>
            {db.db_host_id && (
              <span className="badge" title={`${t('Database server')}: ${db.db_host_address}`}>
                <Server size={11} style={{ marginRight: '4px' }} />{db.db_host_name}
              </span>
            )}
            {db.write_locked && (
              <span className="badge danger" title={db.write_locked_reason || t('Database write-locked due to quota limit')}>
                <Lock size={11} style={{ marginRight: '4px' }} />
                {t('Read-Only (Quota Exceeded)')}
              </span>
            )}
          </span>
          <span className="db-user"><small>{t('User')}</small> {db.db_user}</span>
          <span className="db-actions">
            <button className="mini secondary" disabled={!!loading} onClick={() => openPhpMyAdmin(db.id)}>phpMyAdmin</button>
            <button className="mini secondary-light icon-only" disabled={!!loading} onClick={() => handleInspectDbTables(db)} title={t('Inspect table sizes')}><Eye size={14}/></button>
            <button className="mini secondary-light icon-only" disabled={!!loading} onClick={() => handleOptimizeDb(db)} title={t('Optimize database tables')}><RotateCcw size={14}/></button>
            <button className="mini secondary-light icon-only" disabled={!!loading} onClick={() => downloadDatabase(db.id, db.db_name)} title={t('Download SQL dump')} aria-label={`${t('Download SQL dump')} ${db.db_name}`}><Download size={14}/></button>
            <button className="mini secondary-light icon-only" disabled={!!loading} onClick={() => changeDbPassword(db.id)} title={t('Change password')} aria-label={`${t('Change password')} ${db.db_name}`}><KeyRound size={14}/></button>
            <button className="mini danger icon-only" disabled={!!loading} onClick={() => deleteDatabase(db.id, db.db_name)} title={t('Delete')} aria-label={`${t('Delete')} ${db.db_name}`}><Trash2 size={14}/></button>
          </span>
        </div>})}
      </div>
      <p className="hint">{t('Click phpMyAdmin to sign in directly. Token expires after 60s.')}</p>
      {tableModal && (
        <div className="modal-backdrop" onClick={() => setTableModal(null)}>
          <div className="modal" style={{ maxWidth: '640px', width: '95%' }} onClick={e => e.stopPropagation()}>
            <div className="modal-header" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <h3 style={{ margin: 0 }}>{t('MySQL Table Breakdown')}: <code>{tableModal.db_name}</code> ({fmtDbSize(tableModal.size_bytes)})</h3>
              <button type="button" className="secondary-light mini" onClick={() => setTableModal(null)}><X size={14}/></button>
            </div>
            <div className="modal-body" style={{ maxHeight: '400px', overflowY: 'auto' }}>
              {(!tableModal.tables || tableModal.tables.length === 0) ? (
                <p className="hint">{t('No tables found.')}</p>
              ) : (
                <div className="table">
                  {tableModal.tables.map(tbl => (
                    <div className="row" key={tbl.table_name} style={{ display: 'flex', justifyContent: 'space-between', fontSize: '13px' }}>
                      <span><strong>{tbl.table_name}</strong> <small className="hint">({tbl.rows} rows)</small></span>
                      <strong>{fmtDbSize(tbl.total_bytes)}</strong>
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
  return renderDatabases();
}
