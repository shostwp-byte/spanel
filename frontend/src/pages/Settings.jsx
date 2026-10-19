import React, { useState, useEffect } from 'react';
import { Archive, ArchiveRestore, ArrowLeft, Ban, Bot, Boxes, Check, ChevronDown, Clock, Code2, Copy, Cpu, Database, Dices, ExternalLink, Eye, FileText, FolderOpen, Globe, HardDrive, Home, Image, KeyRound, Lock, LogIn, LogOut, MemoryStick, Menu, Moon, MoveRight, Network, Pencil, Save, Search, Server, Settings as SettingsIcon, Shield, Sun, Trash2, TerminalIcon, Users, X, RefreshCw, Plus, Download, Upload, Play, Square, RotateCcw, AlertCircle, Activity, BrickWall, Bug, LockKeyhole, PackageOpen, ScrollText, ShieldAlert, CheckCircle, Zap, Bell, Mail, Send, Cloud, Inbox, Forward, ShieldCheck } from 'lucide-react';
import { EmptyState } from '../components/EmptyState.jsx';
import { useAppContext } from '../contexts/AppContext.jsx';
import { t } from '../i18n.js';

export function SettingsPage({ request, isAdmin, loadPanelSettings, currentPanelHost }) {
  const {
    loading, setLoading, setError, setNotice, currentUser, loadCurrentUser, panelSettings, setPanelSettings,
    API, readCookie, clearSession, handleAuthExpired, formatApiError,
    copyText, copiedField, currentPanelPort, renderDemoAccounts,
    panelSettingsTab, setPanelSettingsTab,
  } = useAppContext();
  const [panelSettingsForm, setPanelSettingsForm] = useState({ app_name: 'spanel', panel_hostname: '', panel_port: 2222, ssl_enabled: false, primary_color: '#4f46e5' });
  const [apiTokens, setApiTokens] = useState([]);
  const [newApiToken, setNewApiToken] = useState({ name: 'WHMCS', allowed_ips: '' });
  const [createdApiToken, setCreatedApiToken] = useState('');
  const [brandAssetsForm, setBrandAssetsForm] = useState({ logo: null, favicon: null });
  const [panelLogoFile, setPanelLogoFile] = useState(null);
  const [panelFaviconFile, setPanelFaviconFile] = useState(null);
  const [adminAccountForm, setAdminAccountForm] = useState({ email: '', current_password: '', password: '', confirm_password: '', code: '' });

  useEffect(() => {
    if (currentUser?.email) {
      setAdminAccountForm(prev => ({ ...prev, email: prev.email || currentUser.email }));
    }
  }, [currentUser?.email]);

  useEffect(() => {
    if (isAdmin) {
      loadApiTokens();
      request('/panel-settings').then(data => {
        if (data) setPanelSettings(data);
      });
    }
  }, [isAdmin]);

  useEffect(() => {
    setPanelSettingsForm(formFromPanelSettings(panelSettings));
  }, [panelSettings]);

  function formFromPanelSettings(data = {}) {
    let hostname = data.panel_hostname || currentPanelHost();
    let port = Number(data.panel_port || 2222);
    if ((!hostname || !port) && data.panel_url) {
      try {
        const parsed = new URL(data.panel_url);
        hostname = hostname || parsed.hostname;
        port = port || Number(parsed.port || 2222);
      } catch {}
    }
    return {
      app_name: data.app_name || 'spanel',
      panel_hostname: hostname,
      panel_port: port,
      ssl_enabled: !!data.ssl_enabled,
      primary_color: data.primary_color || '#4f46e5',
      ticket_url: data.ticket_url || '',
    };
  }

  async function savePanelSettings() {
    const wantsSsl = !!panelSettingsForm.ssl_enabled;
    const hasSsl = !!panelSettings.ssl_enabled;
    const hostname = String(panelSettingsForm.panel_hostname || '').trim();
    const port = Number(panelSettingsForm.panel_port || 2222);
    const currentHostname = panelSettings.panel_hostname || currentPanelHost();
    const hostnameChanged = hostname && hostname !== currentHostname;

    if (wantsSsl && (!hasSsl || hostnameChanged)) {
      const nameData = await request('/panel-settings', {
        method: 'PATCH',
        body: JSON.stringify({ app_name: panelSettingsForm.app_name, panel_port: port, ticket_url: panelSettingsForm.ticket_url }),
      }, t('Saving panel settings...'));
      if (!nameData) return;
      const sslData = await request('/panel-settings/ssl', {
        method: 'POST',
        body: JSON.stringify({ panel_hostname: hostname, panel_port: port }),
      }, t('Installing panel SSL...'));
      if (sslData) {
        setPanelSettings(sslData);
        setPanelSettingsForm(formFromPanelSettings(sslData));
        setNotice(sslData.message || 'Panel SSL installed. The panel may restart in a moment.');
        if (loadPanelSettings) await loadPanelSettings();
      }
      return;
    }

    const payload = hasSsl && !wantsSsl
      ? { app_name: panelSettingsForm.app_name, panel_url: `http://${hostname}:${port}`, ticket_url: panelSettingsForm.ticket_url }
      : { app_name: panelSettingsForm.app_name, panel_hostname: hostname, ticket_url: panelSettingsForm.ticket_url };
    const data = await request('/panel-settings', {
      method: 'PATCH',
      body: JSON.stringify(payload),
    }, t('Saving panel settings...'));
    if (data) {
      setPanelSettings(data);
      setPanelSettingsForm(formFromPanelSettings(data));
      setNotice(hasSsl && !wantsSsl ? 'Panel SSL disabled. The panel remains reachable by IP and port over HTTP.' : 'Panel settings updated.');
      if (loadPanelSettings) await loadPanelSettings();
    }
  }

  async function saveBrandColor() {
    const data = await request('/panel-settings', {
      method: 'PATCH',
      body: JSON.stringify({ primary_color: panelSettingsForm.primary_color }),
    }, t('Saving brand color...'));
    if (data) {
      setPanelSettings(data);
      setNotice(t('Brand color updated.'));
      if (loadPanelSettings) await loadPanelSettings();
    }
  }

  async function toggleIpv6(enable) {
    const ipv6 = panelSettings.ipv6 || {};
    if (enable && !ipv6.available) {
      setError(ipv6.detail || 'This server has no IPv6 address, so this feature cannot be used.');
      return;
    }
    if (!confirm(t(enable
      ? 'Enable IPv6 for every website and the panel?\n\nspanel adds listen [::] to every website\'s nginx config, checks it with nginx -t, and rolls back on any error. The panel restarts.'
      : 'Disable IPv6?\n\nWebsites and the panel will accept IPv4 only. If a domain still has an AAAA record, visitors arriving over IPv6 will not get through.'))) return;
    const data = await request('/panel-settings/ipv6', {
      method: 'POST',
      body: JSON.stringify({ enabled: enable }),
    }, enable ? 'Enabling IPv6...' : 'Disabling IPv6...');
    if (data) {
      setPanelSettings(data);
      setNotice(data.message || (enable ? 'IPv6 enabled.' : 'IPv6 disabled.'));
    }
  }

  async function loadApiTokens() {
    const data = await request('/provisioning/v1/tokens');
    if (data) setApiTokens(data);
  }

  async function createApiToken() {
    if (!newApiToken.name.trim()) { setError(t('Token name is required.')); return; }
    const data = await request('/provisioning/v1/tokens', {
      method: 'POST',
      body: JSON.stringify({
        name: newApiToken.name.trim(),
        scopes: 'provisioning:read,provisioning:write',
        allowed_ips: newApiToken.allowed_ips.trim(),
      }),
    }, t('Creating API token...'));
    if (data) {
      setCreatedApiToken(data.token || '');
      setNotice(t('API token created. Copy it now; it will not be shown again. Paste it into WHMCS Server Access Hash.'));
      setNewApiToken({ name: 'WHMCS', allowed_ips: '' });
      await loadApiTokens();
    }
  }

  async function copyApiToken() {
    if (!createdApiToken) return;
    try {
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(createdApiToken);
      } else {
        const input = document.getElementById('created-api-token');
        input?.focus();
        input?.select();
        document.execCommand('copy');
      }
      setNotice(t('API token copied. Paste it into WHMCS Server Access Hash.'));
    } catch {
      const input = document.getElementById('created-api-token');
      input?.focus();
      input?.select();
      setError(t('Copy failed. The token is selected; press Ctrl+C.'));
    }
  }

  async function revokeApiToken(token) {
    if (!confirm(`Revoke API token ${token.name}? WHMCS using it will stop working.`)) return;
    const data = await request(`/provisioning/v1/tokens/${token.id}`, { method: 'DELETE' }, `Revoking ${token.name}...`);
    if (data) {
      setNotice(`Revoked API token ${token.name}.`);
      await loadApiTokens();
    }
  }

    async function saveAdminAccount() {
    const email = String(adminAccountForm.email || '').trim();
    const password = String(adminAccountForm.password || '');
    const confirmPassword = String(adminAccountForm.confirm_password || '');
    const currentPassword = String(adminAccountForm.current_password || '');
    const code = String(adminAccountForm.code || '').trim();

    if (!email) {
      setError(t('Email is required.'));
      return;
    }
    if (password && password.length < 12) {
      setError(t('Password must be at least 12 characters.'));
      return;
    }
    if (password && password !== confirmPassword) {
      setError(t('Passwords do not match.'));
      return;
    }

    const payload = { email };
    if (password) {
      if (!currentPassword) {
        setError(t('Current password is required to change password.'));
        return;
      }
      payload.password = password;
      payload.current_password = currentPassword;
      if (currentUser?.totp_enabled) {
        if (!code) {
          setError(t('Authentication code is required.'));
          return;
        }
        payload.code = code;
      }
    }

    const data = await request('/panel-settings/admin-account', {
      method: 'PATCH',
      body: JSON.stringify(payload),
    }, t('Saving admin account...'));
    if (!data) return;
    if (data.password_changed) {
      clearSession('Password changed. Please log in again.');
      return;
    }
    setAdminAccountForm(prev => ({ ...prev, current_password: '', password: '', confirm_password: '', code: '' }));
    await loadCurrentUser({ clearOnUnauthorized: false });
    setNotice(data.message || 'Admin account updated.');
  }

  async function uploadPanelAsset(kind) {
    const file = kind === 'logo' ? panelLogoFile : panelFaviconFile;
    if (!file) return;
    const body = new FormData();
    body.append('file', file);
    const data = await request(`/panel-settings/${kind}`, { method: 'POST', body }, `Uploading ${kind}...`);
    if (data) {
      setPanelSettings(data);
      setPanelSettingsForm(formFromPanelSettings(data));
      if (kind === 'logo') setPanelLogoFile(null);
      if (kind === 'favicon') setPanelFaviconFile(null);
      if (loadPanelSettings) await loadPanelSettings();
    }
  }

  function renderPanelSettings() {
    if (!isAdmin) return <div className="section"><h2>{t('Settings')}</h2><p className="hint">{t('No permission.')}</p></div>;
    // Four tabs, one underlined row like Backups and Panel users (operator,
    // 2026-09-27): the page had grown into four unrelated forms stacked up.
    const tabs = [
      ['general', 'General', SettingsIcon],
      ['account', 'Admin account', Lock],
      ['api', 'API Tokens', KeyRound],
    ];
    const activeTab = tabs.some(([id]) => id === panelSettingsTab) ? panelSettingsTab : 'general';
    const taspanel = (id, content) => activeTab === id && <div className="backup-tab-panel" id={`panel-settings-tab-${id}`} role="tabpanel" aria-labelledby={`panel-settings-tab-button-${id}`}>{content}</div>;
    return <div className="section panel-settings-page">
      <div className="segmented-control backup-tabs" role="tablist" aria-label={t('Panel settings sections')}>
        {tabs.map(([id, label, Icon]) => <button
          key={id}
          type="button"
          role="tab"
          id={`panel-settings-tab-button-${id}`}
          aria-controls={`panel-settings-tab-${id}`}
          aria-selected={activeTab === id}
          className={activeTab === id ? 'active' : ''}
          onClick={() => setPanelSettingsTab(id)}
        ><Icon size={14}/>{t(label)}</button>)}
      </div>
      {taspanel('general', <>
        <div className="backup-panel-title">
          <div><h3>{t('General')}</h3><p className="hint">{t('Panel port, hostname and the server\'s addresses.')}</p></div>
          <button className="secondary-light" disabled={!!loading} onClick={loadPanelSettings}><RefreshCw size={14}/>{t('Refresh')}</button>
        </div>
        <div className="panel-settings-grid panel-settings-compact">
            <label><span>{t('Panel hostname')}</span><input value={panelSettingsForm.panel_hostname} onChange={e => setPanelSettingsForm(prev => ({ ...prev, panel_hostname: e.target.value }))} placeholder="panel.domain.com" /></label>
          <label><span>{t('Panel port')}</span><input type="number" min="1024" max="65535" value={panelSettingsForm.panel_port} onChange={e => setPanelSettingsForm(prev => ({ ...prev, panel_port: e.target.value }))} placeholder="2222" /></label>
          <label><span>{t('Support Ticket URL')}</span><input value={panelSettingsForm.ticket_url} onChange={e => setPanelSettingsForm(prev => ({ ...prev, ticket_url: e.target.value }))} placeholder="https://my.shost.vn/submitticket.php" /></label>
          <label className="check-line panel-ssl-status"><input type="checkbox" checked={!!panelSettingsForm.ssl_enabled} onChange={e => setPanelSettingsForm(prev => ({ ...prev, ssl_enabled: e.target.checked }))} />{t('Panel SSL')}</label>
          <button disabled={!!loading || !panelSettingsForm.panel_port || !panelSettingsForm.panel_hostname} onClick={savePanelSettings}><SettingsIcon size={14}/>{t('Save settings')}</button>
        </div>
        <div className="panel-net-strip">
          <div className="panel-net-row">
            <span className="panel-net-label">IPv4</span>
            <div className="panel-net-value">
              {panelSettings.server_ipv4?.length > 0
                ? panelSettings.server_ipv4.map(address => <span key={address} className="badge">{address}</span>)
                : <span className="hint">{t('Could not read the server\'s IPv4 address.')}</span>}
            </div>
          </div>
          <div className="panel-net-row">
            <span className="panel-net-label">IPv6</span>
            <div className="panel-net-value">
              {panelSettings.ipv6?.addresses?.length > 0
                ? panelSettings.ipv6.addresses.map(address => <span key={address} className="badge">{address}</span>)
                : <span className="badge">{t('None')}</span>}
              <span className={`badge ${panelSettings.ipv6?.enabled ? 'ok' : ''}`}>
                {panelSettings.ipv6?.enabled ? 'On' : 'Off'}
              </span>
            </div>
            {panelSettings.ipv6?.enabled
              ? <button className="secondary-light" disabled={!!loading} onClick={() => toggleIpv6(false)}>{t('Disable IPv6')}</button>
              : <button className="secondary-light" disabled={!!loading || !panelSettings.ipv6?.available} onClick={() => toggleIpv6(true)}>{t('Enable IPv6')}</button>}
          </div>
          <span className="hint">{panelSettings.ipv6?.detail}</span>
        </div>
      </>)}
      {taspanel('account', <>
        <div className="backup-panel-title">
          <div><h3>{t('Admin account')}</h3><p className="hint">{t('A new password needs the current one, and the authenticator code when two-factor sign-in is on.')}</p></div>
        </div>
        <div className="panel-settings-grid admin-account-grid">
          <label><span>{t('Email')}</span><input type="email" value={adminAccountForm.email} onChange={e => setAdminAccountForm(prev => ({ ...prev, email: e.target.value }))} placeholder="admin@domain.com" /></label>
          <label><span>{t('Current password')}</span><input type="password" value={adminAccountForm.current_password} onChange={e => setAdminAccountForm(prev => ({ ...prev, current_password: e.target.value }))} placeholder={t('Current password')} autoComplete="current-password" /></label>
          <label><span>{t('New password')}</span><input type="password" value={adminAccountForm.password} onChange={e => setAdminAccountForm(prev => ({ ...prev, password: e.target.value }))} placeholder={t('New password')} autoComplete="new-password" /></label>
          <label><span>{t('Confirm password')}</span><input type="password" value={adminAccountForm.confirm_password} onChange={e => setAdminAccountForm(prev => ({ ...prev, confirm_password: e.target.value }))} placeholder={t('Repeat new password')} autoComplete="new-password" /></label>
          <label><span>{t('Authenticator code')}</span><input value={adminAccountForm.code} onChange={e => setAdminAccountForm(prev => ({ ...prev, code: e.target.value }))} placeholder="123456" inputMode="numeric" autoComplete="one-time-code" /></label>
          <button disabled={!!loading || !adminAccountForm.email.trim() || (!!adminAccountForm.password && adminAccountForm.password !== adminAccountForm.confirm_password)} onClick={saveAdminAccount}><Lock size={14}/>{t('Save account')}</button>
        </div>
      </>)}
      {taspanel('api', <>
        <div className="backup-panel-title">
          <div><h3>{t('API Tokens')}</h3><p className="hint">{t('Create one token for WHMCS. Paste it into WHMCS Server → Access Hash.')}</p></div>
          <button className="secondary-light" disabled={!!loading} onClick={loadApiTokens}><RefreshCw size={14}/>{t('Refresh')}</button>
        </div>
        {createdApiToken && <div className="user-create-card">
          <label><span>{t('New token (copy now)')}</span><input id="created-api-token" readOnly value={createdApiToken} onFocus={e => e.target.select()} /></label>
          <button className="secondary" disabled={!!loading} onClick={copyApiToken}><Copy size={14}/>{t('Copy token')}</button>
          <button className="secondary-light" onClick={() => setCreatedApiToken('')}>{t('Hide')}</button>
        </div>}
        <div className="user-create-card">
          <label><span>{t('Name')}</span><input value={newApiToken.name} onChange={e => setNewApiToken(prev => ({ ...prev, name: e.target.value }))} placeholder="WHMCS" /></label>
          <label><span>{t('WHMCS server IP')}</span><input value={newApiToken.allowed_ips} onChange={e => setNewApiToken(prev => ({ ...prev, allowed_ips: e.target.value }))} placeholder={t('optional: 1.2.3.4 or 1.2.3.4, 5.6.7.8')} /></label>
          <button disabled={!!loading || !newApiToken.name.trim()} onClick={createApiToken}><Plus size={14}/>{t('Create token')}</button>
        </div>
        <p className="hint">{t('Leave WHMCS server IP empty to allow all IPs. Multiple IPs: separate with comma.')}</p>
        <div className="package-list">
          {apiTokens.length === 0 && <EmptyState icon={KeyRound} message={t('No API tokens found.')} />}
          {apiTokens.map(token => <div className="package-row" key={token.id}>
            <div className="user-main"><strong>{token.name}</strong><small>{token.allowed_ips ? `Allowed IPs: ${token.allowed_ips}` : 'Allowed IPs: all'}</small></div>
            <span className="user-metric"><KeyRound size={13}/>{token.is_active ? 'Active' : 'Revoked'}</span>
            <span className="user-metric"><Clock size={13}/>{token.last_used_at ? new Date(token.last_used_at).toLocaleString() : 'Never used'}</span>
            <div className="row-actions">
              <button className="mini danger" disabled={!!loading || !token.is_active} onClick={() => revokeApiToken(token)}><Trash2 size={14}/>{t('Revoke')}</button>
            </div>
          </div>)}
        </div>
      </>)}
    </div>;
  }
  return renderPanelSettings();
}


