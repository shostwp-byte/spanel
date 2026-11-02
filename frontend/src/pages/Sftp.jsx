import React, { useState, useEffect } from 'react';
import { Archive, ArchiveRestore, ArrowLeft, Ban, Bot, Boxes, Check, ChevronDown, Clock, Code2, Copy, Cpu, Database, Dices, ExternalLink, Eye, FileText, FolderOpen, Globe, HardDrive, Home, Image, KeyRound, Lock, LogIn, LogOut, MemoryStick, Menu, Moon, MoveRight, Network, Pencil, Save, Search, Server, Settings as SettingsIcon, Shield, Sun, Trash2, TerminalIcon, Users, X, RefreshCw, Plus, Download, Upload, Play, Square, RotateCcw, AlertCircle, Activity, BrickWall, Bug, LockKeyhole, PackageOpen, ScrollText, ShieldAlert, CheckCircle, Zap, Bell, Mail, Send, Cloud, Inbox, Forward, ShieldCheck } from 'lucide-react';
import { EmptyState } from '../components/EmptyState.jsx';
import { PasswordField, CopyPasswordButton } from '../components/PasswordField.jsx';
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
  const [ownPwModal, setOwnPwModal] = useState(null);
  const [resetSftpModal, setResetSftpModal] = useState(null);

  useEffect(() => { loadSftpAccounts(); }, [selectedWebsiteId]);

  async function loadSftpAccounts() {
    if (!selectedWebsiteId) { setSftpAccounts([]); return; }
    const data = await request(`/sftp-accounts?website_id=${Number(selectedWebsiteId)}`, {}, t('Loading SFTP accounts...'));
    if (Array.isArray(data)) setSftpAccounts(data);
    const limits = await request('/sftp-accounts/limits', {}, null);
    if (limits) setSftpLimits(limits);
  }

  function changeOwnSftpPassword() {
    setOwnPwModal({
      password: generateRandomPassword(20),
      current_password: '',
      code: '',
    });
  }

  async function submitOwnSftpPassword(e) {
    if (e) e.preventDefault();
    if (!ownPwModal) return;
    const submittedPassword = String(ownPwModal.password || '').trim();
    const currentPassword = String(ownPwModal.current_password || '');
    const code = String(ownPwModal.code || '').trim();
    if (submittedPassword && submittedPassword.length < 12) {
      setError(t('Password must be at least 12 characters.'));
      return;
    }
    if (!currentPassword) {
      setError(t('Current password is required to change password.'));
      return;
    }
    if (currentUser?.totp_enabled && !code) {
      setError(t('Authentication code is required.'));
      return;
    }
    const payload = {
      password: submittedPassword ? submittedPassword : null,
      current_password: currentPassword,
    };
    if (currentUser?.totp_enabled) payload.code = code;
    const data = await request(`/users/${currentUser.id}/sftp-password`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }, t('Setting SFTP password...'));
    if (data) {
      const effectivePassword = data.password || submittedPassword;
      if (effectivePassword) setOwnSftpPassword(effectivePassword);
      setOwnPwModal(null);
      // The session carries sftp_password_set_at, so refresh it to clear the
      // "same as your panel password" warning.
      await loadCurrentUser();
      await loadSftpAccounts();
    }
  }

  async function createSftpAccount() {
    if (!selectedWebsiteId || !newSftpAccount.label.trim()) return;
    const submittedPassword = String(newSftpAccount.password || '').trim();
    if (submittedPassword && submittedPassword.length < 12) {
      setError(t('Password must be at least 12 characters.'));
      return;
    }
    const body = {
      website_id: Number(selectedWebsiteId),
      label: newSftpAccount.label.trim(),
      password: submittedPassword ? submittedPassword : null,
    };
    const data = await request('/sftp-accounts', { method: 'POST', body: JSON.stringify(body) }, t('Creating SFTP account...'));
    if (data?.id) {
      setCreatedSftpInfo({
        ...data,
        password: data.password || submittedPassword || null,
      });
      setNewSftpAccount({ label: '', password: '' });
      await loadSftpAccounts();
    }
  }

  function resetSftpPassword(account) {
    setResetSftpModal({
      account,
      password: generateRandomPassword(20),
    });
  }

  async function submitResetSftpPassword(e) {
    if (e) e.preventDefault();
    if (!resetSftpModal?.account) return;
    const account = resetSftpModal.account;
    const submittedPassword = String(resetSftpModal.password || '').trim();
    if (submittedPassword && submittedPassword.length < 12) {
      setError(t('Password must be at least 12 characters.'));
      return;
    }
    const data = await request(`/sftp-accounts/${account.id}/password`, {
      method: 'POST',
      body: JSON.stringify({ password: submittedPassword ? submittedPassword : null }),
    }, t('Updating SFTP password...'));
    if (data) {
      const effectivePassword = data.password || submittedPassword;
      setCreatedSftpInfo({
        ...account,
        host: account.host || currentPanelHost(),
        port: account.port || 22,
        path: account.path || account.chroot_path || '',
        password: effectivePassword || null,
      });
      setResetSftpModal(null);
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
          <span className="sp-password-result-val">
            <code>{ownSftpPassword}</code>
            <CopyPasswordButton value={ownSftpPassword} label={t('Copy')} noticeMessage={t('SFTP password copied to clipboard.')} size={12} />
          </span>
        </div>}
        {ownSftpPassword && <p className="hint">{t('Shown once. It is not stored anywhere the panel can read back.')}</p>}
      </div>
      <div className="sftp-form sftp-create-row">
        <WebsiteSelect />
        <input
          value={newSftpAccount.label}
          onChange={e => setNewSftpAccount(prev => ({ ...prev, label: e.target.value }))}
          placeholder={t('Name, e.g. designer')}
          aria-label={t('SFTP account name')}
        />
        <PasswordField
          value={newSftpAccount.password}
          onChange={e => setNewSftpAccount(prev => ({ ...prev, password: e.target.value }))}
          placeholder={t('password (empty = generate)')}
          ariaLabel={t('SFTP password')}
          compact
        />
        <button disabled={!selectedWebsiteId || !!loading || !newSftpAccount.label.trim() || atLimit || noAllowance} onClick={createSftpAccount}><Plus size={14}/>{t('Create account')}</button>
      </div>

      {noAllowance && <p className="hint">{t('Your hosting package does not include SFTP accounts.')}</p>}
      {atLimit && <p className="hint">{t('You have used all {n} SFTP accounts in your package.', { n: sftpLimits.limit })}</p>}
      {!noAllowance && !sftpLimits.unlimited && !atLimit &&
        <p className="hint">{t('{used} of {limit} SFTP accounts used.', { used: sftpLimits.used, limit: sftpLimits.limit })}</p>}

      {createdSftpInfo && <div className="info-box db-created-box sp-password-result">
        <div className="db-created-head"><strong>{t('SFTP account ready')}</strong><button className="mini secondary-light sp-result-close" onClick={() => setCreatedSftpInfo(null)}><X size={13}/></button></div>
        <div className="db-created-grid">
          <label>Host</label><span className="sp-password-result-val"><code>{createdSftpInfo.host}</code> <CopyPasswordButton value={createdSftpInfo.host} label={t('Copy')} noticeMessage={false} size={12} /></span>
          <label>{t('Port')}</label><span>{createdSftpInfo.port} (SFTP)</span>
          <label>{t('Username')}</label><span className="sp-password-result-val"><code>{createdSftpInfo.username}</code> <CopyPasswordButton value={createdSftpInfo.username} label={t('Copy')} noticeMessage={t('Copied username.')} size={12} /></span>
          {createdSftpInfo.password && <><label>{t('Password')}</label><span className="sp-password-result-val"><code>{createdSftpInfo.password}</code> <CopyPasswordButton value={createdSftpInfo.password} label={t('Copy')} noticeMessage={t('SFTP password copied to clipboard.')} size={12} /></span></>}
          <label>{t('Folder')}</label><span><code>{createdSftpInfo.path}</code></span>
        </div>
        {createdSftpInfo.password && <p className="hint sp-password-result-hint">{t('This password is shown once. It is not stored anywhere the panel can read it back.')}</p>}
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

      {ownPwModal && (
        <div className="modal-overlay" onClick={() => setOwnPwModal(null)}>
          <div className="modal-card" onClick={e => e.stopPropagation()}>
            <div className="modal-header">
              <h3>{t('Set SFTP password')}: <code>{currentUser?.sftp_username || currentUser?.username}</code></h3>
              <button type="button" className="secondary-light mini" onClick={() => setOwnPwModal(null)} aria-label={t('Close')}><X size={14}/></button>
            </div>
            <form onSubmit={submitOwnSftpPassword}>
              <div className="modal-body" style={{ display: 'grid', gap: '12px' }}>
                <label className="field">
                  <span className="field-label">{t('SFTP password')}</span>
                  <PasswordField
                    value={ownPwModal.password}
                    onChange={e => setOwnPwModal(prev => ({ ...prev, password: e.target.value }))}
                    placeholder={t('Leave empty to generate on server, or roll with dice')}
                    defaultRevealed
                  />
                </label>
                <label className="field">
                  <span className="field-label">{t('Current password')}</span>
                  <input
                    type="password"
                    value={ownPwModal.current_password}
                    onChange={e => setOwnPwModal(prev => ({ ...prev, current_password: e.target.value }))}
                    placeholder={t('Current password')}
                    autoComplete="current-password"
                  />
                </label>
                {currentUser?.totp_enabled && (
                  <label className="field">
                    <span className="field-label">{t('Authenticator code')}</span>
                    <input
                      value={ownPwModal.code}
                      onChange={e => setOwnPwModal(prev => ({ ...prev, code: e.target.value }))}
                      placeholder="123456"
                      inputMode="numeric"
                      autoComplete="one-time-code"
                    />
                  </label>
                )}
              </div>
              <div className="modal-actions">
                <button type="button" className="secondary-light" onClick={() => setOwnPwModal(null)}>{t('Cancel')}</button>
                <button type="submit" disabled={!!loading || !ownPwModal.current_password}>
                  <Save size={14}/> {t('Set password')}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {resetSftpModal && (
        <div className="modal-overlay" onClick={() => setResetSftpModal(null)}>
          <div className="modal-card" onClick={e => e.stopPropagation()}>
            <div className="modal-header">
              <h3>{t('Reset SFTP password')}: <code>{resetSftpModal.account.username}</code></h3>
              <button type="button" className="secondary-light mini" onClick={() => setResetSftpModal(null)} aria-label={t('Close')}><X size={14}/></button>
            </div>
            <form onSubmit={submitResetSftpPassword}>
              <div className="modal-body" style={{ display: 'grid', gap: '12px' }}>
                <label className="field">
                  <span className="field-label">{t('New password')}</span>
                  <PasswordField
                    value={resetSftpModal.password}
                    onChange={e => setResetSftpModal(prev => ({ ...prev, password: e.target.value }))}
                    placeholder={t('Leave empty to generate on server, or roll with dice')}
                    defaultRevealed
                  />
                </label>
                <p className="hint">{t('Minimum 12 characters, or leave empty to generate automatically.')}</p>
              </div>
              <div className="modal-actions">
                <button type="button" className="secondary-light" onClick={() => setResetSftpModal(null)}>{t('Cancel')}</button>
                <button type="submit" disabled={!!loading}>
                  <Save size={14}/> {t('Set password')}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>;
  }
  return renderSftp();
}
