import React, { useState, useEffect } from 'react';
import { Archive, ArchiveRestore, ArrowLeft, Ban, Bot, Boxes, Check, ChevronDown, Clock, Code2, Copy, Cpu, Database, Dices, ExternalLink, Eye, FileText, FolderOpen, Globe, HardDrive, Home, Image, KeyRound, Lock, LogIn, LogOut, MemoryStick, Menu, Moon, MoveRight, Network, Pencil, Save, Search, Server, Settings as SettingsIcon, Shield, Sun, Trash2, TerminalIcon, Users, X, RefreshCw, Plus, Download, Upload, Play, Square, RotateCcw, AlertCircle, Activity, BrickWall, Bug, LockKeyhole, PackageOpen, ScrollText, ShieldAlert, CheckCircle, Zap, Bell, Mail, Send, Cloud, Inbox, Forward, ShieldCheck } from 'lucide-react';
import { EmptyState } from '../components/EmptyState.jsx';
import { useAppContext } from '../contexts/AppContext.jsx';
import { t } from '../i18n.js';
import { WebsiteSelect } from '../components/WebsiteSelect.jsx';

export function SftpPage({ request, currentUser, appVersion }) {
  const {
    loading, setLoading, setError, setNotice, selectedWebsiteId, setSelectedWebsiteId,
    loadCurrentUser, copyText, copiedField, setCopiedField, generateRandomPassword, currentPanelHost,
  } = useAppContext();
  
  const [sftpAccounts, setSftpAccounts] = useState([]);
  const [newSftpAccount, setNewSftpAccount] = useState({ label: '', password: '' });
  const [createdSftpInfo, setCreatedSftpInfo] = useState(null);
  const [sftpLimits, setSftpLimits] = useState({ limit: 0, used: 0, unlimited: false });
  const [ownSftpPassword, setOwnSftpPassword] = useState(null);

  useEffect(() => { loadSftpAccounts(); }, [selectedWebsiteId]);

  async function loadSftpAccounts() {
    if (!selectedWebsiteId) { setSftpAccounts([]); return; }
    const data = await request(`/sftp-accounts?website_id=${Number(selectedWebsiteId)}`, {}, t('Loading SFTP accounts...'));
    if (Array.isArray(data)) setSftpAccounts(data);
    const limits = await request('/sftp-accounts/limits', {}, null);
    if (limits) setSftpLimits(limits);
  }

  async function changeOwnSftpPassword() {
    const typed = prompt(
      'New SFTP password for your own account (leave empty to generate a strong one):',
      ''
    );
    if (typed === null) return;
    const data = await request(`/users/${currentUser.id}/sftp-password`, {
      method: 'POST',
      body: JSON.stringify({ password: typed ? typed : null }),
    }, t('Setting SFTP password...'));
    if (data) {
      if (data.password) setOwnSftpPassword(data.password);
      // The session carries sftp_password_set_at, so refresh it to clear the
      // "same as your panel password" warning.
      await loadCurrentUser();
      await loadSftpAccounts();
    }
  }

  async function createSftpAccount() {
    if (!selectedWebsiteId || !newSftpAccount.label.trim()) return;
    const body = {
      website_id: Number(selectedWebsiteId),
      label: newSftpAccount.label.trim(),
      password: newSftpAccount.password ? newSftpAccount.password : null,
    };
    const data = await request('/sftp-accounts', { method: 'POST', body: JSON.stringify(body) }, t('Creating SFTP account...'));
    if (data?.id) {
      setCreatedSftpInfo(data);
      setNewSftpAccount({ label: '', password: '' });
      await loadSftpAccounts();
    }
  }

  async function resetSftpPassword(account) {
    const typed = prompt(`New password for ${account.username} (leave empty to generate one):`, '');
    if (typed === null) return;
    const data = await request(`/sftp-accounts/${account.id}/password`, {
      method: 'POST',
      body: JSON.stringify({ password: typed ? typed : null }),
    }, t('Updating SFTP password...'));
    if (data) {
      if (data.password) setCreatedSftpInfo({ ...account, password: data.password });
      await loadSftpAccounts();
    }
  }

  async function deleteSftpAccount(account) {
    if (!confirm(`Delete SFTP account ${account.username}? The login stops working immediately. Site files are not touched.`)) return;
    await request(`/sftp-accounts/${account.id}`, { method: 'DELETE' }, t('Removing SFTP account...'));
    if (createdSftpInfo?.id === account.id) setCreatedSftpInfo(null);
    await loadSftpAccounts();
  }

  async function createSftpBackup() {
    if (!selectedWebsiteId || !selectedSftpTargetId) return;
    const data = await request('/maintenance/backup-sftp', {
      method: 'POST',
      body: JSON.stringify({ website_id: Number(selectedWebsiteId), target_id: Number(selectedSftpTargetId) }),
    }, t('Queueing SFTP backup...'));
    if (data?.job_id) {
      setNotice(t('SFTP backup queued. It will keep running on the server.'));
      await loadBackupJobs();
    } else if (data?.remote_file) {
      setNotice(`SFTP backup uploaded: ${data.remote_file}`);
      await listBackups();
    }
  }

  function renderSftp() {
    function copySftp(text, field) {
      const doCopy = navigator.clipboard ? navigator.clipboard.writeText(text) : new Promise((resolve, reject) => {
        try { const ta = document.createElement('textarea'); ta.value = text; ta.style.position = 'fixed'; ta.style.opacity = '0'; document.body.appendChild(ta); ta.select(); document.execCommand('copy'); document.body.removeChild(ta); resolve(); } catch(e) { reject(e); }
      });
      doCopy.then(() => { setCopiedField(field); setTimeout(() => setCopiedField(null), 2000); }).catch(() => setError(t('Copy failed.')));
    }
    const atLimit = !sftpLimits.unlimited && Number(sftpLimits.limit || 0) > 0
      && Number(sftpLimits.used || 0) >= Number(sftpLimits.limit || 0);
    const noAllowance = !sftpLimits.unlimited && Number(sftpLimits.limit || 0) <= 0;
    return <div className="section">
      <div className="section-title">
        <div><h2>{t('SFTP accounts')}</h2></div>
        <button className="secondary-light" disabled={!selectedWebsiteId || !!loading} onClick={loadSftpAccounts}><RefreshCw size={14}/>{t('Refresh')}</button>
      </div>

      <div className="info-box">
        <div className="db-created-head">
          <strong>{t('Your own SFTP login')}</strong>
          <button className="mini" onClick={changeOwnSftpPassword}><KeyRound size={13}/>{t('Set password')}</button>
        </div>
        <div className="db-created-grid">
          <label>{t('Username')}</label><span>{currentUser?.sftp_username || currentUser?.username}</span>
          <label>{t('Port')}</label><span>22 (SFTP)</span>
          <label>{t('Reaches')}</label><span>every website on this account</span>
        </div>
        {!currentUser?.sftp_password_set_at && <p className="hint" style={{color:'var(--red)'}}>
          {t('This login still uses your panel password. Anyone who guesses it over SFTP is also in the panel. Set a separate password — your panel password will stop working for SFTP the moment you do.')}
        </p>}
        {currentUser?.sftp_password_set_at && <p className="hint">{t('Separate from your panel password. Changing one does not change the other.')}</p>}
        {ownSftpPassword && <div className="db-created-grid" style={{marginTop:'0.5rem'}}>
          <label>{t('New password')}</label>
          <span><code>{ownSftpPassword}</code> <button className="mini secondary-light" onClick={() => { copySftp(ownSftpPassword, 'own_sftp'); }}>{copiedField === 'own_sftp' ? <Check size={12} style={{color:'var(--green)'}}/> : <Copy size={12}/>}</button></span>
        </div>}
        {ownSftpPassword && <p className="hint">{t('Shown once. It is not stored anywhere the panel can read back.')}</p>}
      </div>
      <div className="sftp-form">
        <WebsiteSelect />
        <input
          value={newSftpAccount.label}
          onChange={e => setNewSftpAccount(prev => ({ ...prev, label: e.target.value }))}
          placeholder={t('Name, e.g. designer')}
          aria-label={t('SFTP account name')}
        />
        <input
          value={newSftpAccount.password}
          onChange={e => setNewSftpAccount(prev => ({ ...prev, password: e.target.value }))}
          placeholder={t('password (empty = generate)')}
          aria-label={t('SFTP password')}
        />
        <button className="mini secondary-light" title={t('Generate random password')} onClick={() => setNewSftpAccount(prev => ({ ...prev, password: generateRandomPassword() }))}><Dices size={13}/></button>
        <button disabled={!selectedWebsiteId || !!loading || !newSftpAccount.label.trim() || atLimit || noAllowance} onClick={createSftpAccount}><Plus size={14}/>{t('Create account')}</button>
      </div>

      {noAllowance && <p className="hint">{t('Your hosting package does not include SFTP accounts.')}</p>}
      {atLimit && <p className="hint">{t('You have used all {n} SFTP accounts in your package.', { n: sftpLimits.limit })}</p>}
      {!noAllowance && !sftpLimits.unlimited && !atLimit &&
        <p className="hint">{t('{used} of {limit} SFTP accounts used.', { used: sftpLimits.used, limit: sftpLimits.limit })}</p>}

      {createdSftpInfo && <div className="info-box db-created-box">
        <div className="db-created-head"><strong>{t('SFTP account ready')}</strong><button className="mini secondary-light" onClick={() => setCreatedSftpInfo(null)}><X size={13}/></button></div>
        <div className="db-created-grid">
          <label>Host</label><span>{createdSftpInfo.host} <button className="mini secondary-light" title={copiedField === 'sftp_host' ? 'Copied!' : 'Copy'} onClick={() => copySftp(createdSftpInfo.host, 'sftp_host')}>{copiedField === 'sftp_host' ? <Check size={12} style={{color:'var(--green)'}}/> : <Copy size={12}/>}</button></span>
          <label>{t('Port')}</label><span>{createdSftpInfo.port} (SFTP)</span>
          <label>{t('Username')}</label><span>{createdSftpInfo.username} <button className="mini secondary-light" title={copiedField === 'sftp_user' ? 'Copied!' : 'Copy'} onClick={() => copySftp(createdSftpInfo.username, 'sftp_user')}>{copiedField === 'sftp_user' ? <Check size={12} style={{color:'var(--green)'}}/> : <Copy size={12}/>}</button></span>
          {createdSftpInfo.password && <><label>{t('Password')}</label><span><code>{createdSftpInfo.password}</code> <button className="mini secondary-light" title={copiedField === 'sftp_pass' ? 'Copied!' : 'Copy'} onClick={() => copySftp(createdSftpInfo.password, 'sftp_pass')}>{copiedField === 'sftp_pass' ? <Check size={12} style={{color:'var(--green)'}}/> : <Copy size={12}/>}</button></span></>}
          <label>{t('Folder')}</label><span><code>{createdSftpInfo.path}</code></span>
        </div>
        {createdSftpInfo.password && <p className="hint">{t('This password is shown once. It is not stored anywhere the panel can read it back.')}</p>}
      </div>}

      {selectedWebsiteId && sftpAccounts.length === 0 && !createdSftpInfo &&
        <EmptyState icon={Upload} message={t('No SFTP accounts for this website yet.')} />}

      <div className="table">
        {sftpAccounts.map(account => <div className="row db-row" key={account.id}>
          <span><strong>{account.label}</strong></span>
          <span style={{color:'var(--text-muted)'}}>{account.username}</span>
          <span style={{color:'var(--text-muted)'}}><code>{account.path}</code></span>
          {!account.is_active && <span style={{color:'var(--red)'}}>suspended</span>}
          <button disabled={!!loading} onClick={() => resetSftpPassword(account)}><KeyRound size={14}/>{t('Password')}</button>
          <button className="danger" disabled={!!loading} onClick={() => deleteSftpAccount(account)}><Trash2 size={14}/></button>
        </div>)}
      </div>

      <p className="hint">
        {t('Each account reaches one website and nothing else — not your other sites, and not the server. It signs in over SFTP on port 22 with its own password, which is separate from your panel password.')}
      </p>
    </div>;
  }
  return renderSftp();
}
