import React, { useState, useEffect } from 'react';
import { Archive, ArchiveRestore, ArrowLeft, Ban, Bot, Boxes, Check, ChevronDown, Clock, Code2, Copy, Cpu, Database, Dices, ExternalLink, Eye, FileText, FolderOpen, Globe, HardDrive, Home, Image, KeyRound, Lock, LogIn, LogOut, MemoryStick, Menu, Moon, MoveRight, Network, Pencil, Save, Search, Server, Settings as SettingsIcon, Shield, Sun, Trash2, TerminalIcon, Users, X, RefreshCw, Plus, Download, Upload, Play, Square, RotateCcw, AlertCircle, Activity, BrickWall, Bug, LockKeyhole, PackageOpen, ScrollText, ShieldAlert, CheckCircle, Zap, Bell, Mail, Send, Cloud, Inbox, Forward, ShieldCheck } from 'lucide-react';
import { EmptyState } from '../components/EmptyState.jsx';
import { useAppContext } from '../contexts/AppContext.jsx';
import { t } from '../i18n.js';
import { NoAutofillInput } from '../components/NoAutofillInput.jsx';
import { passkeySupported, b64urlToBytes, bytesToB64url, decodeCreationOptions, decodeRequestOptions, encodeRegistration, encodeAssertion } from '../utils/passkey.js';


export function SecurityPage({ request, currentUser, setCurrentUser }) {
  const {
    loading, setLoading, setError, setNotice,
    loadCurrentUser, copyText, submitPasswordChange,
    passwordForm, setPasswordForm,
  } = useAppContext();
  const [passkeyStatus, setPasskeyStatus] = useState(null);
  const [passkeyPassword, setPasskeyPassword] = useState('');
  const [passkeyName, setPasskeyName] = useState('');
  const [twoFactorStatus, setTwoFactorStatus] = useState(null);
  const [twoFactorSetup, setTwoFactorSetup] = useState(null);
  const [twoFactorCode, setTwoFactorCode] = useState('');

  useEffect(() => {
    loadPasskeyStatus();
    loadTwoFactorStatus();
  }, []);

  async function loadPasskeyStatus() {
    const data = await request('/auth/passkey/status', { silent: true });
    if (data) setPasskeyStatus(data);
  }

  async function addPasskey() {
    if (!passkeySupported()) {
      setError(t('This browser does not support passkeys.'));
      return;
    }
    const started = await request('/auth/passkey/register/options', {
      method: 'POST',
      body: JSON.stringify({ current_password: passkeyPassword || null }),
    }, t('Preparing passkey...'));
    if (!started?.options) return;
    try {
      const parsed = JSON.parse(started.options);
      const credential = await navigator.credentials.create({
        publicKey: decodeCreationOptions(parsed),
      });
      if (!credential) return;
      const done = await request('/auth/passkey/register/verify', {
        method: 'POST',
        body: JSON.stringify({ credential: encodeRegistration(credential), name: passkeyName }),
      }, t('Saving passkey...'));
      if (done?.id) {
        setNotice(`Added passkey ${done.name}.`);
        setPasskeyPassword('');
        setPasskeyName('');
        await loadPasskeyStatus();
      }
    } catch (err) {
      setError(t('Could not create the passkey. The device may have refused it, or you cancelled.'));
    }
  }

  async function removePasskey(credential) {
    if (!confirm(`Delete passkey ${credential.name}? That device will no longer be able to sign in.`)) return;
    await request(`/auth/passkey/credentials/${credential.id}`, { method: 'DELETE' }, t('Deleting passkey...'));
    await loadPasskeyStatus();
  }

  async function loadTwoFactorStatus() {
    const data = await request('/auth/2fa/status');
    if (data) setTwoFactorStatus(data);
  }

  async function setupTwoFactorAuth() {
    const currentPassword = prompt('Enter your current password to generate a new 2FA secret:');
    if (!currentPassword) return;
    const payload = { current_password: currentPassword };
    if (currentUser?.totp_enabled) {
      const code = prompt('Enter the 6-digit code from your authenticator:');
      if (!code) return;
      payload.code = code.trim();
    }
    const data = await request('/auth/2fa/setup', { method: 'POST', body: JSON.stringify(payload) }, t('Preparing 2FA...'));
    if (data) {
      setTwoFactorSetup(data);
      setTwoFactorStatus({ enabled: false });
    }
  }

  async function enableTwoFactorAuth() {
    const data = await request('/auth/2fa/enable', { method: 'POST', body: JSON.stringify({ code: twoFactorCode }) }, t('Enabling 2FA...'));
    if (data) {
      setTwoFactorStatus(data);
      setTwoFactorSetup(null);
      setTwoFactorCode('');
      await loadCurrentUser();
      setNotice(t('2FA enabled.'));
    }
  }

  async function disableTwoFactorAuth() {
    const currentPassword = prompt('Enter your current password to disable 2FA:');
    if (!currentPassword) return;
    const data = await request(
      '/auth/2fa/disable',
      { method: 'POST', body: JSON.stringify({ current_password: currentPassword, code: twoFactorCode }) },
      'Disabling 2FA...',
    );
    if (data) {
      setTwoFactorStatus(data);
      setTwoFactorCode('');
      await loadCurrentUser();
      setNotice(t('2FA disabled.'));
    }
  }

  function renderSecurity() {
    const enabled = Boolean(twoFactorStatus?.enabled || currentUser?.totp_enabled);
    const pk = passkeyStatus;
    return <>
      <div className="section">
        <div className="section-title">
          <div>
            <h2>{t('Passkey')}</h2>
            <p className="hint">{t('Sign in with a fingerprint, Face ID or a security key instead of typing a code.')}</p>
          </div>
          <button className="secondary-light" disabled={!!loading} onClick={loadPasskeyStatus}><RefreshCw size={14}/>{t('Refresh')}</button>
        </div>

        {pk && !pk.supported && <div className="info-box">
          <p className="hint" style={{color:'var(--red)'}}>
            {t('You are reaching the panel by IP address ({host}). Browsers only create passkeys for domain names, so open the panel by its domain and add one there.', { host: pk.hostname })}
          </p>
        </div>}

        {pk?.supported && <>
          <p className="hint">{t('A passkey is tied to the domain')}{' '}<strong>{pk.rp_id}</strong>.{' '}
            {t('Reach the panel by any other name and it will not be offered — use Google Authenticator below for that.')}
          </p>
          <div className="passkey-form">
            <NoAutofillInput
              name="passkey-name"
              value={passkeyName}
              onChange={e => setPasskeyName(e.target.value)}
              placeholder={t('Device name, e.g. MacBook')}
              aria-label={t('Passkey name')}
            />
            <input
              type="password"
              value={passkeyPassword}
              onChange={e => setPasskeyPassword(e.target.value)}
              placeholder={t('Current password')}
              autoComplete="current-password"
              aria-label={t('Current password')}
            />
            <button disabled={!!loading || !passkeyPassword} onClick={addPasskey}>
              <KeyRound size={14}/>{t('Add passkey')}</button>
          </div>
          <p className="hint">{t('Your current password confirms it is you, the same as when turning on Google Authenticator.')}</p>
        </>}

        {pk?.credentials?.length > 0 && <div className="table">
          {pk.credentials.map(c => <div className="row db-row" key={c.id}>
            <span><strong>{c.name}</strong></span>
            <span style={{color:'var(--text-muted)'}}>{c.rp_id}</span>
            <span className={c.usable_here ? 'badge ok' : 'badge'}>
              {c.usable_here ? 'Works here' : 'Another domain'}
            </span>
            <button className="danger" disabled={!!loading} onClick={() => removePasskey(c)}><Trash2 size={14}/></button>
          </div>)}
        </div>}

        {pk?.supported && (pk?.credentials?.length || 0) === 0 &&
          <EmptyState icon={KeyRound} message={t('No passkeys yet.')} />}

        {(pk?.credentials?.length || 0) > 0 && !enabled && <p className="hint" style={{color:'var(--red)'}}>
          {t('A passkey is your only second factor. Reach the panel by a different domain and there is no second factor at all — turn on Google Authenticator below as well.')}
        </p>}
      </div>

      <div className="section">
        <div className="section-title">
          <div><h2>{t('Google Authenticator 2FA')}</h2><p className="hint">{t('Current status:')}{' '}<strong>{enabled ? 'Enabled' : 'Disabled'}</strong></p></div>
          <button className="secondary-light" disabled={!!loading} onClick={loadTwoFactorStatus}><RefreshCw size={14}/>{t('Refresh')}</button>
        </div>
        {!enabled && <div className="security-grid">
          <div className="info-box">
            <strong>{t('Setup')}</strong>
            {twoFactorSetup?.qr_data_url ? <img className="qr-code" src={twoFactorSetup.qr_data_url} alt="2FA QR code" /> : <p className="hint">{t('No setup code generated.')}</p>}
            {twoFactorSetup?.secret && <code className="secret-text">{twoFactorSetup.secret}</code>}
            <div className="actions">
              <button disabled={!!loading} onClick={setupTwoFactorAuth}><Shield size={14}/>{t('Generate QR')}</button>
            </div>
          </div>
          <div className="info-box">
            <strong>{t('Verify')}</strong>
            <input value={twoFactorCode} onChange={e => setTwoFactorCode(e.target.value)} placeholder="123456" inputMode="numeric" />
            <button disabled={!!loading || !twoFactorSetup || !twoFactorCode} onClick={enableTwoFactorAuth}><Lock size={14}/>{t('Enable 2FA')}</button>
          </div>
        </div>}
        {enabled && <div className="security-grid one">
          <div className="info-box">
            <strong>{t('Disable 2FA')}</strong>
            <input value={twoFactorCode} onChange={e => setTwoFactorCode(e.target.value)} placeholder="123456" inputMode="numeric" />
            <button className="danger" disabled={!!loading || !twoFactorCode} onClick={disableTwoFactorAuth}>{t('Disable 2FA')}</button>
          </div>
        </div>}
      </div>

    </>;
  }
  return renderSecurity();
}
