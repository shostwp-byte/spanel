import React, { useState, useEffect } from 'react';
import { Archive, ArchiveRestore, ArrowLeft, Ban, Bot, Boxes, Check, ChevronDown, Clock, Code2, Copy, Cpu, Database, Dices, ExternalLink, Eye, FileText, FolderOpen, Globe, HardDrive, Home, Image, KeyRound, Lock, LogIn, LogOut, MemoryStick, Menu, Moon, MoveRight, Network, Pencil, Save, Search, Server, Settings as SettingsIcon, Shield, Sun, Trash2, TerminalIcon, Users, X, RefreshCw, Plus, Download, Upload, Play, Square, RotateCcw, AlertCircle, Activity, BrickWall, Bug, LockKeyhole, PackageOpen, ScrollText, ShieldAlert, CheckCircle, Zap, Bell, Mail, Send, Cloud, Inbox, Forward, ShieldCheck } from 'lucide-react';
import { Terminal } from '../components/Terminal';
import { EmptyState } from '../components/EmptyState.jsx';
import { NoAutofillInput } from '../components/NoAutofillInput.jsx';
import { useAppContext } from '../contexts/AppContext.jsx';
import { t } from '../i18n.js';

export function WebsitesPage({ 
  mode = 'domains',
  request, API, setPage, phpVersions, isReseller, fileAppId, setFileAppId, setFileListPath, 
  websiteConfigForm, standaloneEditor,
  domain, setDomain, adminEmail, setAdminEmail, siteType, setSiteType, 
  installWordPress, setInstallWordPress, createFormOpen, setCreateFormOpen, 
  createSslMode, setCreateSslMode, createSslToken, setCreateSslToken, 
  createSiteAppId, setCreateSiteAppId, wordpressInstaller, setWordpressInstaller, 
  aliasDrafts, setAliasDrafts, aliasModes, setAliasModes, 
  createWordPress, refreshAll, navigateToPage, WEBSITE_MODES, appsFeatureEnabled, 
  siteApps, websiteUrl, openWebsiteFileManager, loadSiteApps
}) {
  const {
    loading, setLoading, setError, setNotice, phpVersion, setPhpVersion,
    phpVersions: ctxPhpVersions, loadPhpVersions,
    currentUser, isAdmin, users, packages, storageUsageText,
    websites, setWebsites, selectedWebsiteId, setSelectedWebsiteId,
    wpAdminUser, setWpAdminUser, wpAdminPassword, setWpAdminPassword,
    NGINX_REWRITE_MODES, APP_TYPE_LABELS, SITE_APP_KIND_LABELS, WordPressIcon,
    isProxiedAppType, composeWebPorts, generateRandomPassword,
    copyText, copiedField, enableSsl, loadCfZone, loadDatabases, openWebsiteTerminal,
    cfZones, cfScanning, cfScanError, cfApplying, cfDnsCreating,
  } = useAppContext();
  
  const [nginxCustomEditing, setNginxCustomEditing] = useState(null);
  const [websiteSettingsForm, setWebsiteSettingsForm] = useState(websiteConfigForm());
  const [logViewer, setLogViewer] = useState(null);
  const [terminalViewer, setTerminalViewer] = useState(null);
  const [websiteList, setWebsiteList] = useState([]);
  const [websiteSearch, setWebsiteSearch] = useState('');
  const [websiteSearching, setWebsiteSearching] = useState(false);
  const isSubdomainMode = mode === 'subdomains';
  const [subdomainPrefix, setSubdomainPrefix] = useState('');
  const [subdomainParent, setSubdomainParent] = useState('');
  const [subdomainParentFilter, setSubdomainParentFilter] = useState('all');
  const [subdomainModalOpen, setSubdomainModalOpen] = useState(false);
  const [subdomainDocrootMode, setSubdomainDocrootMode] = useState('default');
  const [selectedSubdomainIds, setSelectedSubdomainIds] = useState([]);
  const [subdomainColsOpen, setSubdomainColsOpen] = useState(false);
  const [subdomainCols, setSubdomainCols] = useState(() => {
    const mobile = typeof window !== 'undefined' && window.innerWidth <= 640;
    return {
      bandwidth: !mobile,
      docroot: !mobile,
      php: true,
      ssl: true,
    };
  });
  const [selectedDomainIds, setSelectedDomainIds] = useState([]);
  const [domainColsOpen, setDomainColsOpen] = useState(false);
  const [domainCols, setDomainCols] = useState(() => {
    const mobile = typeof window !== 'undefined' && window.innerWidth <= 640;
    return {
      bandwidth: !mobile,
      disk: !mobile,
      subdomains: true,
      docroot: !mobile,
      php: true,
      ssl: true,
    };
  });
  const [renameModalOpen, setRenameModalOpen] = useState(false);
  const [renameTargetId, setRenameTargetId] = useState('');
  const [renameNewDomain, setRenameNewDomain] = useState('');
  const [defaultDomainId, setDefaultDomainId] = useState(() => {
    try { return localStorage.getItem('spanel_default_domain_id') || ''; } catch { return ''; }
  });
  
  const selectedWebsite = websiteList.find(s => s.id === selectedWebsiteId);
  const [siteRuntimes, setSiteRuntimes] = useState([]);
  const [nginxEditorContent, setNginxEditorContent] = useState('');

  const installedPhpList = (phpVersions?.installed?.length ? phpVersions.installed : ctxPhpVersions?.installed?.length ? ctxPhpVersions.installed : ['8.4']);
  useEffect(() => { if (loadPhpVersions) loadPhpVersions({ silent: true }); }, []);
  useEffect(() => { loadWebsiteList(); }, [websiteSearch]);
  useEffect(() => {
    if (websiteList.length === 0) {
      const id = setInterval(() => loadWebsiteList(websiteSearch, false), 5000);
      return () => clearInterval(id);
    }
  }, [websiteList, websiteSearch]);
  useEffect(() => {
    if (logViewer) {
      const timer = setInterval(() => {
        loadWebsiteLog(logViewer.id, logViewer.kind, logViewer.lines, logViewer.domain);
      }, 3000);
      return () => clearInterval(timer);
    }
  }, [logViewer]);

  async function loadWebsiteList(search = websiteSearch, showLoading = false) {
    const query = String(search || '').trim();
    const suffix = query ? `?q=${encodeURIComponent(query)}` : '';
    setWebsiteSearching(true);
    const data = await request(`/websites${suffix}`, {}, showLoading ? 'Loading websites...' : '');
    setWebsiteSearching(false);
    if (data) {
      setWebsiteList(data);
      if (!query) setWebsites(data);
      if (!selectedWebsiteId && data[0]) setSelectedWebsiteId(String(data[0].id));
    }
  }

  async function deleteWebsite(id) {
    if (!confirm(t('Delete this website including files, vhost, database, and its SSL certificate?'))) return;
    const data = await request(`/websites/${id}?delete_files=true&delete_database=true`, { method: 'DELETE' }, t('Deleting website...'));
    if (data) refreshAll();
  }

  async function addWebsiteAlias(site) {
    const cleanAlias = String(aliasDrafts[site.id] || '').trim().toLowerCase();
    const aliasMode = aliasModes[site.id] || 'alias';
    if (!cleanAlias) { setError(t('Enter a domain.')); return; }
    const data = await request(`/websites/${site.id}/aliases`, {
      method: 'POST',
      body: JSON.stringify({ domain: cleanAlias, mode: aliasMode }),
    }, `Adding ${aliasMode === 'redirect' ? 'redirect' : 'alias'} ${cleanAlias}...`);
    if (data) {
      const label = aliasMode === 'redirect' ? 'redirect' : 'alias';
      // Adding a domain only wires it into Nginx - same split DirectAdmin
      // uses. Getting it a certificate is the separate, explicit step on the
      // SSL page (Install / Renew SSL there already asks for every alias and
      // redirect), so nothing SSL-related is attempted or claimed here.
      setNotice(site.ssl_mode === 'letsencrypt' && !data.ssl_enabled
        ? `Added ${label} ${cleanAlias}. Go to the SSL page and press "Install / Renew SSL" to issue a certificate for it.`
        : `Added ${label} ${cleanAlias}.`);
      setAliasDrafts(prev => ({ ...prev, [site.id]: '' }));
      setNginxCustomEditing(prev => {
        if (!prev || prev.id !== site.id) return prev;
        const nextSite = prev.site || site;
        return { ...prev, site: { ...nextSite, aliases: [...(nextSite.aliases || []), data] } };
      });
      await refreshAll();
    }
  }

  async function deleteWebsiteAlias(site, alias) {
    const label = alias.mode === 'redirect' ? 'redirect' : 'alias';
    if (!confirm(`Remove ${label} ${alias.domain} from ${site.domain}?`)) return;
    const data = await request(`/websites/${site.id}/aliases/${alias.id}`, { method: 'DELETE' }, `Removing ${label} ${alias.domain}...`);
    if (data) {
      setNotice(`Removed ${label} ${alias.domain}.`);
      setNginxCustomEditing(prev => {
        if (!prev || prev.id !== site.id) return prev;
        const nextSite = prev.site || site;
        return { ...prev, site: { ...nextSite, aliases: (nextSite.aliases || []).filter(item => item.id !== alias.id) } };
      });
      await refreshAll();
    }
  }

  async function openNginxCustom(site) {
    setWordpressInstaller(null);
    setLogViewer(null);
    setTerminalViewer(null);
    setWebsiteSettingsForm(websiteConfigForm(site));
    const data = await request(`/websites/${site.id}/nginx-custom`, {}, t('Loading Custom Nginx...'));
    if (data !== null) {
      setNginxCustomEditing({
        id: site.id,
        domain: site.domain,
        site,
        mode: 'custom',
        content: data?.nginx_custom || '',
      });
      await loadSiteApps();
    }
  }

  async function loadSiteRuntimes() {
    const data = await request('/site-runtimes/status', { silent: true });
    if (data) setSiteRuntimes(data);
  }

  async function viewFullNginxConfig() {
    if (!nginxCustomEditing) return;
    const data = await request(`/websites/${nginxCustomEditing.id}/nginx-config`, {}, t('Loading full Nginx config...'));
    if (data !== null) {
      setNginxCustomEditing(prev => ({ ...prev, mode: 'full', customContent: prev?.content || '', content: data?.nginx_config || '' }));
    }
  }

  async function saveNginxCustom() {
    if (!nginxCustomEditing) return;
    if (nginxCustomEditing.mode === 'full') return;
    const data = await request(`/websites/${nginxCustomEditing.id}/nginx-custom`, {
      method: 'PUT',
      body: JSON.stringify({ nginx_custom: nginxCustomEditing.content }),
    }, t('Applying Custom Nginx and reloading...'));
    if (data) {
      setNotice(`Updated Custom Nginx for ${nginxCustomEditing.domain}`);
      setNginxCustomEditing(null);
      refreshAll();
    }
  }

  async function saveWebsiteSettings() {
    if (!nginxCustomEditing) return;
    const original = nginxCustomEditing.site || {};
    const body = {};
    const nextAppType = websiteSettingsForm.app_type || original.app_type || 'wordpress';
    const nextPhp = websiteSettingsForm.php_version || original.php_version || '8.4';
    const nextRewrite = nextAppType === 'wordpress'
      ? 'front_controller'
      : nextAppType === 'static' || isProxiedAppType(nextAppType)
        ? 'none'
        : websiteSettingsForm.nginx_rewrite_mode || 'none';

    if (isProxiedAppType(nextAppType)) {
      if (siteApps.items.length === 0) {
        setError(t('Install an application first, on the Applications page.'));
        return;
      }
      if (!websiteSettingsForm.app_id) {
        setError(t('Pick which application this website should serve.'));
        return;
      }
      if (String(websiteSettingsForm.app_id) !== String(original.app_id || '')) {
        body.app_id = Number(websiteSettingsForm.app_id);
      }
    }
    if (nextAppType !== (original.app_type || 'wordpress')) body.app_type = nextAppType;
    if (nextAppType !== 'static' && !isProxiedAppType(nextAppType) && nextPhp !== original.php_version) body.php_version = nextPhp;
    if (nextRewrite !== (original.nginx_rewrite_mode || (original.app_type === 'wordpress' ? 'front_controller' : 'none'))) {
      body.nginx_rewrite_mode = nextRewrite;
    }
    if (Object.keys(body).length === 0) return;

    const data = await request(`/websites/${nginxCustomEditing.id}`, {
      method: 'PATCH',
      body: JSON.stringify(body),
    }, `Saving ${nginxCustomEditing.domain} settings...`);
    if (data) {
      setNotice(`Updated settings for ${nginxCustomEditing.domain}.`);
      setWebsiteSettingsForm(websiteConfigForm(data));
      setNginxCustomEditing(prev => prev ? ({ ...prev, site: data }) : prev);
      await refreshAll();
    }
  }

  async function resetNginxDefault() {
    if (!nginxCustomEditing) return;
    if (!confirm(`Clear Custom Nginx for ${nginxCustomEditing.domain}?`)) return;
    const data = await request(`/websites/${nginxCustomEditing.id}/nginx-custom`, {
      method: 'PUT',
      body: JSON.stringify({ nginx_custom: '' }),
    }, t('Clearing Custom Nginx...'));
    if (data) {
      setNotice(`Cleared Custom Nginx for ${nginxCustomEditing.domain}.`);
      setNginxCustomEditing(null);
      await refreshAll();
    }
  }

  async function loadWebsiteLog(siteOrId = logViewer?.id, kind = logViewer?.kind || 'access', lines = logViewer?.lines || 200, domainLabel = logViewer?.domain || '') {
    const websiteId = typeof siteOrId === 'object' ? siteOrId.id : siteOrId;
    const domainName = typeof siteOrId === 'object' ? siteOrId.domain : domainLabel;
    if (!websiteId) return;
    const data = await request(`/websites/${websiteId}/logs?kind=${encodeURIComponent(kind)}&lines=${encodeURIComponent(lines)}`, {}, `Loading ${kind} log...`);
    if (data) {
      setLogViewer({
        id: websiteId,
        domain: data.domain || domainName,
        kind: data.kind || kind,
        lines: data.lines || lines,
        path: data.path || '',
        content: data.content || '',
        exists: !!data.exists,
      });
    }
  }

  async function openWebsiteLogs(site) {
    setNginxCustomEditing(null);
    setWordpressInstaller(null);
    setTerminalViewer(null);
    setLogViewer({ id: site.id, domain: site.domain, kind: 'access', lines: 200, path: '', content: '', exists: true });
    await loadWebsiteLog(site, 'access', 200, site.domain);
  }

  function openWordPressInstaller(site) {
    setNginxCustomEditing(null);
    setLogViewer(null);
    setTerminalViewer(null);
    setWordpressInstaller({
      website_id: site.id,
      domain: site.domain,
      php_version: site.php_version || phpVersion,
      title: site.domain,
      admin_user: 'admin',
      admin_email: `admin@${site.domain}`,
      admin_password: generateRandomPassword(20),
    });
  }

  async function installWordPressOnSite() {
    if (!wordpressInstaller) return;
    const title = String(wordpressInstaller.title || '').trim() || wordpressInstaller.domain;
    const adminUser = String(wordpressInstaller.admin_user || '').trim();
    const adminEmailValue = String(wordpressInstaller.admin_email || '').trim();
    const adminPasswordValue = String(wordpressInstaller.admin_password || '').trim();
    if (!adminUser || !adminEmailValue || !adminPasswordValue) {
      setError(t('Please fill all WordPress admin fields.'));
      return;
    }
    if (adminPasswordValue.length < 10) {
      setError(t('WordPress admin password must be at least 10 characters.'));
      return;
    }
    const data = await request(`/websites/${wordpressInstaller.website_id}/wordpress`, {
      method: 'POST',
      body: JSON.stringify({
        title,
        admin_user: adminUser,
        admin_email: adminEmailValue,
        admin_password: adminPasswordValue,
      }),
    }, `Installing WordPress for ${wordpressInstaller.domain}...`);
    if (data) {
      setNotice(`Installed WordPress: https://${wordpressInstaller.domain}\nAdmin: ${adminUser} | Password: ${adminPasswordValue}`);
      setWordpressInstaller(null);
      await refreshAll();
    }
  }

  async function runWordPressAction(site, action) {
    const labels = { core: 'core', plugins: 'plugins', themes: 'themes' };
    const label = labels[action] || action;
    const data = await request('/maintenance/wordpress', {
      method: 'POST',
      body: JSON.stringify({ website_id: site.id, action }),
    }, `Updating WordPress ${label}...`);
    if (data?.returncode && data.returncode !== 0) {
      setError(data.stderr || data.stdout || `WordPress ${label} update failed.`);
      return;
    }
    if (data) {
      setNotice(`Updated WordPress ${label} for ${site.domain}.`);
    }
  }

  async function updateWordPressAll(site) {
    if (!site) return;
    setLoading('Updating WordPress...');
    for (const action of ['core', 'plugins', 'themes']) {
      const data = await request('/maintenance/wordpress', {
        method: 'POST',
        body: JSON.stringify({ website_id: site.id, action }),
      });
      if (data?.returncode && data.returncode !== 0) {
        setError(data.stderr || data.stdout || `WordPress ${action} update failed.`);
        setLoading('');
        return;
      }
    }
    setLoading('');
    setNotice(`Updated WordPress core, plugins, and themes for ${site.domain}.`);
  }

  async function toggleWebsiteWaf(site) {
    const next = !site.waf_enabled;
    const data = await request(`/websites/${site.id}/waf`, {
      method: 'PATCH',
      body: JSON.stringify({ waf_enabled: next }),
    }, `${next ? 'Enabling' : 'Disabling'} WAF for ${site.domain}...`);
    if (data) {
      setNotice(`${next ? 'Enabled' : 'Disabled'} WAF for ${site.domain}.`);
      await refreshAll();
    }
  }

  async function changeWebsitePhp(site, nextPhp) {
    if (!site || !nextPhp || nextPhp === site.php_version) return;
    const data = await request(`/websites/${site.id}`, {
      method: 'PATCH',
      body: JSON.stringify({ php_version: nextPhp }),
    }, t('Switching {domain} to PHP {version}...', { domain: site.domain, version: nextPhp }));
    if (data) {
      setNotice(t('Switched {domain} to PHP {version}.', { domain: site.domain, version: nextPhp }));
      await loadWebsiteList(websiteSearch, false);
      if (refreshAll) await refreshAll();
    }
  }

  async function fixNginxSecurity(id) {
    const data = await request(`/websites/${id}/fix-nginx-security`, { method: 'POST' }, t('Rewriting Nginx security template...'));
    if (data?.message) setNotice(data.message);
  }

  function renderNginxEditor() {
    if (!nginxCustomEditing) return null;
    const fullConfig = nginxCustomEditing.mode === 'full';
    const selectedAppType = websiteSettingsForm.app_type || nginxCustomEditing.site?.app_type || 'wordpress';
    const rewriteDisabled = selectedAppType !== 'php';
    const proxied = isProxiedAppType(selectedAppType);
    const settingsSite = nginxCustomEditing.site || {};
    const siteDomains = settingsSite.aliases || [];
    const aliasMode = aliasModes[nginxCustomEditing.id] || 'alias';
    return <div className="section nginx-modal inline-nginx-editor">
      <div className="section-title">
        <div className="nginx-config-title">
          <h2>{fullConfig ? t('Full Nginx config') : t('Domain settings')} - {nginxCustomEditing.domain}</h2>
          <p className="hint">{fullConfig
            ? t('This is read-only. spanel manages the main vhost template.')
            : t('Configure PHP version, domain pointers (aliases/redirects), and custom Nginx directives.')}</p>
        </div>
        <div className="actions">
          {!fullConfig && isAdmin && <button className="secondary-light" disabled={!!loading} onClick={viewFullNginxConfig}><FileText size={14}/>{t('View all')}</button>}
          {fullConfig && <button className="secondary-light" disabled={!!loading} onClick={() => setNginxCustomEditing(prev => ({ ...prev, mode: 'custom', content: prev?.customContent ?? prev?.content ?? '' }))}><SettingsIcon size={14}/>{t('Settings')}</button>}
          <button className="secondary-light" onClick={() => setNginxCustomEditing(null)}><X size={14}/>{t('Close')}</button>
        </div>
      </div>
      {!fullConfig && <div className="website-settings-grid">
        <label><span>{t('PHP version')}</span><select
          value={websiteSettingsForm.php_version}
          onChange={e => setWebsiteSettingsForm(prev => ({ ...prev, php_version: e.target.value }))}
          disabled={!!loading}
        >
          {(installedPhpList.includes(websiteSettingsForm.php_version) ? installedPhpList : [...installedPhpList, websiteSettingsForm.php_version].filter(Boolean)).map(v => <option key={v} value={v}>PHP {v}</option>)}
        </select></label>
        <label><span>{t('Nginx rewrite')}</span><select
          value={websiteSettingsForm.nginx_rewrite_mode || 'front_controller'}
          onChange={e => setWebsiteSettingsForm(prev => ({ ...prev, app_type: 'php', nginx_rewrite_mode: e.target.value }))}
          disabled={!!loading}
        >
          {NGINX_REWRITE_MODES.map(mode => <option key={mode.value} value={mode.value}>{mode.label}</option>)}
        </select></label>
        <div className="website-settings-actions">
          <button disabled={!!loading} onClick={saveWebsiteSettings}><Save size={14}/>{t('Save settings')}</button>
        </div>
      </div>}
      {!fullConfig && <div className="site-aliases settings-domain-manager">
        <div className="domain-manager-head">
          <h3>{t('Domains')}</h3>
          <p className="hint">{t('Alias serves the same app. Redirect sends visitors to {domain}.', { domain: nginxCustomEditing.domain })}</p>
        </div>
        <div className="alias-list">
          <span className="alias-chip primary-domain"><Globe size={12}/>{nginxCustomEditing.domain}<span>{t('Main')}</span></span>
          {siteDomains.length === 0
            ? <span className="alias-empty">{t('No extra domains')}</span>
            : siteDomains.map(alias => <span className="alias-chip" key={alias.id}>
              <Globe size={12}/>{alias.domain}<span>{alias.mode === 'redirect' ? 'Redirect' : 'Alias'}</span>
              <button type="button" disabled={!!loading} title={`Remove ${alias.domain}`} aria-label={`Remove ${alias.domain}`} onClick={() => deleteWebsiteAlias(settingsSite, alias)}><X size={12}/></button>
            </span>)}
        </div>
        <div className="alias-form settings-domain-form">
          <input
            value={aliasDrafts[nginxCustomEditing.id] || ''}
            onChange={e => setAliasDrafts(prev => ({ ...prev, [nginxCustomEditing.id]: e.target.value }))}
            onKeyDown={e => { if (e.key === 'Enter') addWebsiteAlias(settingsSite); }}
            placeholder="domain-alias.com"
            disabled={!!loading}
          />
          <select
            value={aliasMode}
            onChange={e => setAliasModes(prev => ({ ...prev, [nginxCustomEditing.id]: e.target.value }))}
            disabled={!!loading}
          >
            <option value="alias">{t('Alias')}</option>
            <option value="redirect">{t('Redirect')}</option>
          </select>
          <button className="secondary-light" disabled={!!loading || !(aliasDrafts[nginxCustomEditing.id] || '').trim()} onClick={() => addWebsiteAlias(settingsSite)}><Plus size={14}/>{t('Add domain')}</button>
        </div>
      </div>}
      <div className="custom-nginx-block">
        {!fullConfig && <h3>{t('Custom Nginx')}</h3>}
        <textarea
          className="code-editor"
          value={nginxCustomEditing.content}
          onChange={e => setNginxCustomEditing(prev => ({ ...prev, content: e.target.value, customContent: e.target.value }))}
          placeholder={fullConfig
            ? `server {\n    listen 80;\n    server_name ${nginxCustomEditing.domain};\n}`
            : `# Optional extra directives only. Use Nginx rewrite above for location / routing.`}
          spellCheck={false}
          rows={fullConfig ? 18 : 10}
          readOnly={fullConfig}
        />
      </div>
      <div className="actions">
        {!fullConfig && <button disabled={!!loading} onClick={saveNginxCustom}>{t('Save and reload Nginx')}</button>}
        {!fullConfig && <button className="secondary-light" disabled={!!loading} onClick={resetNginxDefault}><RotateCcw size={14}/>{t('Reset custom')}</button>}
        <button className="secondary-light" disabled={!!loading} onClick={() => setNginxCustomEditing(null)}>{fullConfig ? 'Close' : 'Cancel'}</button>
      </div>
    </div>;
  }

  function renderWordPressInstaller() {
    if (!wordpressInstaller) return null;
    return <div className="section nginx-modal inline-nginx-editor wordpress-install-modal">
      <div className="section-title">
        <div className="nginx-config-title">
          <h2>{t('Install WordPress -')} {wordpressInstaller.domain}</h2>
          <p className="hint">PHP {wordpressInstaller.php_version || '8.4'}</p>
        </div>
        <button className="secondary-light" onClick={() => setWordpressInstaller(null)}><X size={14}/>{t('Close')}</button>
      </div>
      <div className="website-settings-grid">
        <label><span>{t('Site title')}</span><input
          value={wordpressInstaller.title}
          onChange={e => setWordpressInstaller(prev => ({ ...prev, title: e.target.value }))}
          disabled={!!loading}
        /></label>
        <label><span>{t('Admin user')}</span><input
          value={wordpressInstaller.admin_user}
          onChange={e => setWordpressInstaller(prev => ({ ...prev, admin_user: e.target.value }))}
          disabled={!!loading}
        /></label>
        <label><span>{t('Admin email')}</span><input
          value={wordpressInstaller.admin_email}
          onChange={e => setWordpressInstaller(prev => ({ ...prev, admin_email: e.target.value }))}
          disabled={!!loading}
        /></label>
        <label><span>{t('Admin password')}</span><input
          value={wordpressInstaller.admin_password}
          onChange={e => setWordpressInstaller(prev => ({ ...prev, admin_password: e.target.value }))}
          disabled={!!loading}
        /></label>
        <div className="website-settings-actions">
          <button className="secondary-light" disabled={!!loading} onClick={() => setWordpressInstaller(prev => prev ? ({ ...prev, admin_password: generateRandomPassword(20) }) : prev)}><Dices size={14}/>{t('Generate')}</button>
          <button disabled={!!loading || !wordpressInstaller.admin_user || !wordpressInstaller.admin_email || !wordpressInstaller.admin_password} onClick={installWordPressOnSite}><WordPressIcon size={14}/>{t('Install')}</button>
        </div>
      </div>
    </div>;
  }

  function renderWebsiteTerminal() {
    if (!terminalViewer) return null;
    return <div className="section nginx-modal terminal-modal">
      <div className="section-title">
        <h2>{t('Terminal -')} {terminalViewer.domain}</h2>
        <button className="secondary-light" onClick={() => setTerminalViewer(null)}><X size={14}/>{t('Close')}</button>
      </div>
      <div style={{ height: '500px', marginTop: '8px' }}>
        <Terminal websiteId={terminalViewer.id} apiBase={API} />
      </div>
    </div>;
  }

  function renderWebsiteLogViewer() {
    if (!logViewer) return null;
    return <div className="section nginx-modal log-viewer">
      <div className="section-title">
        <div className="nginx-config-title">
          <h2>{t('Nginx logs -')} {logViewer.domain}</h2>
          <p className="hint">{logViewer.path || `/var/log/nginx/${logViewer.domain}.${logViewer.kind}.log`}</p>
        </div>
        <button className="secondary-light" onClick={() => setLogViewer(null)}><X size={14}/>{t('Close')}</button>
      </div>
      <div className="log-toolbar">
        <div className="segmented-control">
          <button className={logViewer.kind === 'access' ? 'active' : ''} disabled={!!loading} onClick={() => loadWebsiteLog(logViewer.id, 'access', logViewer.lines, logViewer.domain)}>{t('Access')}</button>
          <button className={logViewer.kind === 'error' ? 'active' : ''} disabled={!!loading} onClick={() => loadWebsiteLog(logViewer.id, 'error', logViewer.lines, logViewer.domain)}>{t('Error')}</button>
        </div>
        <select value={logViewer.lines} onChange={e => loadWebsiteLog(logViewer.id, logViewer.kind, Number(e.target.value), logViewer.domain)} disabled={!!loading}>
          <option value={100}>100 lines</option>
          <option value={200}>200 lines</option>
          <option value={500}>500 lines</option>
          <option value={1000}>1000 lines</option>
          <option value={2000}>2000 lines</option>
        </select>
        <button className="secondary-light" disabled={!!loading} onClick={() => loadWebsiteLog(logViewer.id, logViewer.kind, logViewer.lines, logViewer.domain)}><RefreshCw size={14}/>{t('Refresh')}</button>
      </div>
      <pre className="log-output">{logViewer.exists ? (logViewer.content || 'Log is empty.') : 'Log file has not been created yet.'}</pre>
    </div>;
  }

  const allKnownSites = websiteList.length ? websiteList : websites;
  const allDomainSet = React.useMemo(
    () => new Set(allKnownSites.map(s => String(s.domain || '').toLowerCase()).filter(Boolean)),
    [allKnownSites]
  );

  function findParentDomain(domainName) {
    const lower = String(domainName || '').toLowerCase();
    if (!lower) return null;
    let bestParent = null;
    for (const candidate of allDomainSet) {
      if (lower !== candidate && lower.endsWith('.' + candidate)) {
        if (!bestParent || candidate.length > bestParent.length) {
          bestParent = candidate;
        }
      }
    }
    return bestParent;
  }

  const rootDomains = React.useMemo(
    () => allKnownSites.filter(s => !findParentDomain(s.domain)),
    [allKnownSites, allDomainSet]
  );
  const subdomainSites = React.useMemo(
    () => allKnownSites.filter(s => !!findParentDomain(s.domain)),
    [allKnownSites, allDomainSet]
  );

  const userPkg = React.useMemo(
    () => (packages || []).find(p => p.id === currentUser?.package_id || (currentUser?.package_name && p.name === currentUser.package_name)),
    [packages, currentUser]
  );
  const subdomainLimit = Number(currentUser?.subdomain_limit ?? userPkg?.subdomain_limit ?? 0);
  const websiteLimit = Number(currentUser?.website_limit ?? userPkg?.website_limit ?? 0);

  const defaultSite = React.useMemo(() => {
    if (!rootDomains.length) return null;
    if (defaultDomainId) {
      const byDefault = rootDomains.find(d => String(d.id) === String(defaultDomainId));
      if (byDefault) return byDefault;
    }
    if (selectedWebsiteId) {
      const bySelected = rootDomains.find(d => String(d.id) === String(selectedWebsiteId));
      if (bySelected) return bySelected;
    }
    return rootDomains[0];
  }, [rootDomains, defaultDomainId, selectedWebsiteId]);

  useEffect(() => {
    if (!websiteSearch.trim() && Array.isArray(websites) && websites.length > 0) {
      setWebsiteList(websites);
    }
  }, [websites, websiteSearch]);

  useEffect(() => {
    if (rootDomains.length > 0 && (!subdomainParent || !rootDomains.some(d => d.domain === subdomainParent))) {
      const selectedRoot = rootDomains.find(d => String(d.id) === String(selectedWebsiteId));
      const chosen = (selectedRoot || rootDomains[0]).domain;
      setSubdomainParent(chosen);
      if (subdomainParentFilter === 'all' || !rootDomains.some(d => d.domain.toLowerCase() === subdomainParentFilter)) {
        setSubdomainParentFilter(chosen.toLowerCase());
      }
    }
  }, [rootDomains, selectedWebsiteId, subdomainParent]);

  async function createSubdomain() {
    const parent = String(subdomainParent || (rootDomains[0]?.domain || '')).trim().toLowerCase();
    if (!parent) {
      setError(t('Please add a main domain first before creating a subdomain.'));
      return;
    }
    let prefix = String(subdomainPrefix || '').trim().toLowerCase();
    if (prefix.endsWith('.' + parent)) {
      prefix = prefix.slice(0, -(parent.length + 1));
    }
    prefix = prefix.replace(/^\.+|\.+$/g, '');
    if (!prefix) {
      setError(t('Please enter a subdomain prefix (e.g. blog, app, shop).'));
      return;
    }
    if (!/^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)*$/.test(prefix)) {
      setError(t('Invalid subdomain name. Use letters, numbers, and hyphens only.'));
      return;
    }
    const fullDomain = `${prefix}.${parent}`;
    const chosenPhp = installedPhpList.includes(phpVersion) ? phpVersion : (installedPhpList[0] || '8.4');
    const data = await request(
      '/websites',
      {
        method: 'POST',
        body: JSON.stringify({
          domain: fullDomain,
          title: fullDomain,
          admin_user: 'admin',
          admin_password: '',
          admin_email: null,
          php_version: chosenPhp,
          app_type: 'php',
          install_wordpress: false,
        }),
      },
      t('Creating subdomain {domain}...', { domain: fullDomain })
    );
    if (data) {
      setSubdomainPrefix('');
      setSubdomainModalOpen(false);
      setNotice(t('Created subdomain {domain}.', { domain: fullDomain }));
      await loadWebsiteList(websiteSearch, false);
      if (refreshAll) await refreshAll();
    }
  }

  async function deleteSelectedSubdomains() {
    if (!selectedSubdomainIds.length) return;
    if (!confirm(t('Delete {n} selected subdomain(s) including files, virtual host, and SSL certificate?', { n: selectedSubdomainIds.length }))) return;
    for (const id of selectedSubdomainIds) {
      await request(`/websites/${id}?delete_files=true&delete_database=true`, { method: 'DELETE' }, t('Deleting subdomain...'));
    }
    setSelectedSubdomainIds([]);
    await loadWebsiteList(websiteSearch, false);
    if (refreshAll) await refreshAll();
  }

  function handleSetDefaultDomain() {
    if (selectedDomainIds.length !== 1) return;
    const targetId = String(selectedDomainIds[0]);
    const targetSite = rootDomains.find(d => String(d.id) === targetId);
    if (!targetSite) return;
    setDefaultDomainId(targetId);
    setSelectedWebsiteId(targetId);
    try { localStorage.setItem('spanel_default_domain_id', targetId); } catch { }
    setSelectedDomainIds([]);
    setNotice(t('Default domain set to {domain}.', { domain: targetSite.domain }));
  }

  async function handleSuspendToggleDomains() {
    if (!selectedDomainIds.length) return;
    for (const id of selectedDomainIds) {
      const site = rootDomains.find(d => d.id === id);
      if (!site) continue;
      const nextStatus = site.status === 'suspended' ? 'active' : 'suspended';
      await request(
        `/websites/${id}`,
        { method: 'PATCH', body: JSON.stringify({ status: nextStatus }) },
        t('Updating domain status...')
      );
    }
    setSelectedDomainIds([]);
    await loadWebsiteList(websiteSearch, false);
    if (refreshAll) await refreshAll();
    setNotice(t('Updated status for selected domain(s).'));
  }

  async function handleDeleteSelectedDomains() {
    if (!selectedDomainIds.length) return;
    if (!confirm(t('Delete {n} selected domain(s) including files, virtual host, database, and SSL certificate?', { n: selectedDomainIds.length }))) return;
    for (const id of selectedDomainIds) {
      await request(`/websites/${id}?delete_files=true&delete_database=true`, { method: 'DELETE' }, t('Deleting website...'));
    }
    setSelectedDomainIds([]);
    await loadWebsiteList(websiteSearch, false);
    if (refreshAll) await refreshAll();
  }

  async function handleRenameDomain() {
    const targetId = renameTargetId || (defaultSite ? String(defaultSite.id) : '');
    const cleanNew = String(renameNewDomain || '').trim().toLowerCase();
    if (!targetId || !cleanNew) {
      setError(t('Please enter a new domain name.'));
      return;
    }
    const data = await request(
      `/websites/${targetId}`,
      { method: 'PATCH', body: JSON.stringify({ domain: cleanNew }) },
      t('Renaming domain to {domain}...', { domain: cleanNew })
    );
    if (data) {
      setRenameModalOpen(false);
      setRenameNewDomain('');
      setNotice(t('Renamed domain to {domain}.', { domain: cleanNew }));
      await loadWebsiteList(websiteSearch, false);
      if (refreshAll) await refreshAll();
    }
  }

  function renderWebsites() {
    const searchActive = !!websiteSearch.trim();
    const sslBadge = site => !site.ssl_enabled ? t('No SSL')
      : site.ssl_mode === 'cloudflare' ? t('Wildcard')
        : site.ssl_mode === 'shared' ? t('Shared cert')
          : site.ssl_mode === 'manual' ? t('Manual SSL') : 'SSL OK';

    if (isSubdomainMode) {
      const filteredSubdomains = subdomainSites.filter(site => {
        const parent = findParentDomain(site.domain);
        if (subdomainParentFilter !== 'all' && parent !== subdomainParentFilter) return false;
        if (!searchActive) return true;
        const q = websiteSearch.trim().toLowerCase();
        return site.domain.toLowerCase().includes(q) || (parent && parent.includes(q));
      });
      const activeParentDomain = subdomainParentFilter !== 'all'
        ? subdomainParentFilter
        : (subdomainParent || rootDomains[0]?.domain || '');
      const previewSubdomain = `${(subdomainPrefix.trim().toLowerCase().replace(/^\.+|\.+$/g, '') || 'sub')}.${subdomainParent || rootDomains[0]?.domain || 'domain.com'}`;
      const allFilteredSelected = filteredSubdomains.length > 0 && filteredSubdomains.every(s => selectedSubdomainIds.includes(s.id));
      const visibleColCount = 2
        + (subdomainCols.bandwidth ? 1 : 0)
        + (subdomainCols.docroot ? 1 : 0)
        + (subdomainCols.php ? 1 : 0)
        + (subdomainCols.ssl ? 1 : 0)
        + 1;

      return (
        <div className="da-subdomain-manager" style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
          {/* DirectAdmin-style Top Bar: Breadcrumb on Left, Active Domain Context Selector on Right */}
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
              gap: '12px',
              flexWrap: 'wrap',
              padding: '10px 16px',
              borderRadius: '10px',
              background: 'var(--surface)',
              border: '1px solid var(--border)',
              boxShadow: 'var(--shadow-xs)',
            }}
          >
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px', fontSize: '13px', color: 'var(--text-muted)' }}>
              <button
                type="button"
                className="secondary-light"
                onClick={() => navigateToPage('dashboard')}
                style={{
                  border: 'none',
                  background: 'transparent',
                  padding: '2px 6px',
                  height: 'auto',
                  minHeight: 'unset',
                  fontSize: '13px',
                  color: 'var(--text-secondary)',
                  fontWeight: 500,
                }}
              >
                <Home size={14} style={{ marginRight: '4px' }} />
                {t('Dashboard')}
              </button>
              <span>›</span>
              <strong style={{ color: 'var(--text)', fontWeight: 600 }}>{t('Subdomain Management')}</strong>
            </div>

            <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
              <span style={{ fontSize: '12.5px', color: 'var(--text-muted)', fontWeight: 500 }}>{t('Domain')}:</span>
              <div
                style={{
                  display: 'inline-flex',
                  alignItems: 'center',
                  gap: '8px',
                  padding: '4px 12px',
                  borderRadius: '999px',
                  background: 'var(--surface-alt)',
                  border: '1px solid var(--border-strong)',
                }}
              >
                <Globe size={14} style={{ color: 'var(--primary)', flexShrink: 0 }} />
                {rootDomains.length === 0 ? (
                  <span style={{ fontSize: '13px', color: 'var(--text-muted)' }}>{t('No domain yet')}</span>
                ) : (
                  <select
                    value={subdomainParentFilter}
                    onChange={e => {
                      const val = e.target.value;
                      setSubdomainParentFilter(val);
                      setSelectedSubdomainIds([]);
                      if (val !== 'all') {
                        const matched = rootDomains.find(d => d.domain.toLowerCase() === val);
                        if (matched) {
                          setSubdomainParent(matched.domain);
                          setSelectedWebsiteId(String(matched.id));
                        }
                      }
                    }}
                    aria-label={t('Select active domain')}
                    style={{
                      border: 'none',
                      background: 'transparent',
                      fontWeight: 600,
                      fontSize: '13.5px',
                      color: 'var(--text)',
                      padding: '2px 20px 2px 2px',
                      height: '26px',
                      minHeight: '26px',
                      boxShadow: 'none',
                      cursor: 'pointer',
                    }}
                  >
                    {rootDomains.map(d => (
                      <option key={d.id} value={d.domain.toLowerCase()}>
                        {d.domain}
                      </option>
                    ))}
                    {rootDomains.length > 1 && (
                      <option value="all">{t('All domains')} ({rootDomains.length})</option>
                    )}
                  </select>
                )}
              </div>
            </div>
          </div>

          {/* DirectAdmin-style Header Row: Title on Left + Green "+ ADD SUBDOMAIN" Button on Right */}
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
              gap: '12px',
              flexWrap: 'wrap',
              padding: '4px 2px',
            }}
          >
            <div>
              <h2 style={{ margin: 0, fontSize: '21px', fontWeight: 700, letterSpacing: '-0.01em', color: 'var(--text)' }}>
                {t('Subdomain Management')}
              </h2>
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
              {rootDomains.length === 0 ? (
                <button
                  type="button"
                  onClick={() => {
                    setCreateFormOpen(true);
                    navigateToPage('websites');
                  }}
                >
                  <Plus size={15} />
                  {t('Add Main Domain First')}
                </button>
              ) : (
                <button
                  type="button"
                  onClick={() => {
                    if (subdomainParentFilter !== 'all') {
                      const matched = rootDomains.find(d => d.domain.toLowerCase() === subdomainParentFilter);
                      if (matched) setSubdomainParent(matched.domain);
                    }
                    setSubdomainModalOpen(true);
                  }}
                  style={{
                    background: '#16a34a',
                    borderColor: '#15803d',
                    color: '#ffffff',
                    fontWeight: 700,
                    fontSize: '12.5px',
                    letterSpacing: '0.03em',
                    textTransform: 'uppercase',
                    padding: '0 16px',
                    height: '38px',
                    borderRadius: '8px',
                    boxShadow: '0 2px 6px rgba(22, 163, 74, 0.25)',
                  }}
                >
                  <Plus size={16} />
                  {t('Add Subdomain')}
                </button>
              )}
            </div>
          </div>

          {/* DirectAdmin-style Main Card: Quota Bar + Toolbar + Table */}
          <div className="section" style={{ padding: 0, overflow: 'visible' }}>
            {/* Top Quota & Action Toolbar */}
            <div
              style={{
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'space-between',
                gap: '12px',
                flexWrap: 'wrap',
                padding: '14px 18px',
                borderBottom: '1px solid var(--border)',
                background: 'var(--surface)',
                borderTopLeftRadius: '12px',
                borderTopRightRadius: '12px',
              }}
            >
              {/* Left: Subdomains Quota Counter */}
              <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                <div
                  style={{
                    width: '32px',
                    height: '32px',
                    borderRadius: '8px',
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    background: 'var(--primary-10)',
                    color: 'var(--primary)',
                  }}
                >
                  <Globe size={16} />
                </div>
                <div style={{ display: 'flex', alignItems: 'baseline', gap: '8px' }}>
                  <span style={{ fontSize: '13.5px', color: 'var(--text-secondary)', fontWeight: 500 }}>
                    {t('Subdomains')}
                  </span>
                  <strong style={{ fontSize: '15px', fontWeight: 700, color: 'var(--text)' }}>
                    {subdomainSites.length} / {subdomainLimit > 0 ? subdomainLimit : '∞'}
                  </strong>
                </div>
                {activeParentDomain && subdomainParentFilter !== 'all' && (
                  <span
                    style={{
                      fontSize: '12px',
                      padding: '2px 8px',
                      borderRadius: '999px',
                      background: 'var(--surface-alt)',
                      border: '1px solid var(--border)',
                      color: 'var(--text-muted)',
                    }}
                  >
                    *.{activeParentDomain} ({filteredSubdomains.length})
                  </span>
                )}
              </div>

              {/* Right: Search, Columns Dropdown, Refresh, Bulk Delete */}
              <div className="da-toolbar-controls" style={{ display: 'flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap' }}>
                <div
                  style={{
                    position: 'relative',
                    display: 'flex',
                    alignItems: 'center',
                    minWidth: '210px',
                  }}
                >
                  <Search
                    size={14}
                    style={{
                      position: 'absolute',
                      left: '10px',
                      color: 'var(--text-muted)',
                      pointerEvents: 'none',
                    }}
                  />
                  <NoAutofillInput
                    type="search"
                    name="subdomain-search"
                    value={websiteSearch}
                    onChange={e => setWebsiteSearch(e.target.value)}
                    placeholder={t('Search subdomain...')}
                    aria-label={t('Search subdomains')}
                    style={{
                      paddingLeft: '32px',
                      paddingRight: websiteSearch ? '28px' : '10px',
                      height: '34px',
                      minHeight: '34px',
                      fontSize: '13px',
                      width: '100%',
                    }}
                  />
                  {websiteSearch && (
                    <button
                      type="button"
                      className="mini secondary-light"
                      onClick={() => setWebsiteSearch('')}
                      aria-label={t('Clear search')}
                      style={{
                        position: 'absolute',
                        right: '4px',
                        border: 'none',
                        background: 'transparent',
                        padding: '2px',
                        height: '24px',
                        minHeight: '24px',
                      }}
                    >
                      <X size={13} />
                    </button>
                  )}
                </div>

                {/* Columns Picker Dropdown */}
                <div style={{ position: 'relative' }}>
                  <button
                    type="button"
                    className="secondary-light"
                    onClick={() => setSubdomainColsOpen(prev => !prev)}
                    style={{ height: '34px', minHeight: '34px', padding: '0 12px', fontSize: '13px' }}
                  >
                    {t('Columns')}
                    <ChevronDown size={14} />
                  </button>
                  {subdomainColsOpen && (
                    <div
                      style={{
                        position: 'absolute',
                        top: 'calc(100% + 6px)',
                        right: 0,
                        zIndex: 40,
                        minWidth: '185px',
                        padding: '8px 10px',
                        borderRadius: '10px',
                        background: 'var(--bg-elevated)',
                        border: '1px solid var(--border-strong)',
                        boxShadow: 'var(--shadow-lg)',
                        display: 'flex',
                        flexDirection: 'column',
                        gap: '6px',
                      }}
                    >
                      {[
                        ['bandwidth', t('Bandwidth')],
                        ['docroot', t('Docroot')],
                        ['php', t('PHP Version')],
                        ['ssl', t('SSL Status')],
                      ].map(([colKey, colLabel]) => (
                        <label
                          key={colKey}
                          style={{
                            display: 'flex',
                            alignItems: 'center',
                            gap: '8px',
                            fontSize: '13px',
                            padding: '4px 6px',
                            borderRadius: '6px',
                            cursor: 'pointer',
                            color: 'var(--text)',
                          }}
                        >
                          <input
                            type="checkbox"
                            checked={!!subdomainCols[colKey]}
                            onChange={e =>
                              setSubdomainCols(prev => ({ ...prev, [colKey]: e.target.checked }))
                            }
                          />
                          <span>{colLabel}</span>
                        </label>
                      ))}
                    </div>
                  )}
                </div>

                {/* Refresh Button */}
                <button
                  type="button"
                  className="secondary-light"
                  disabled={!!loading || websiteSearching}
                  onClick={() => loadWebsiteList(websiteSearch, true)}
                  title={t('Refresh')}
                  aria-label={t('Refresh')}
                  style={{ height: '34px', minHeight: '34px', padding: '0 10px' }}
                >
                  <RefreshCw size={14} className={websiteSearching ? 'spin' : ''} />
                </button>

                {/* Bulk Delete Button */}
                <button
                  type="button"
                  className={selectedSubdomainIds.length > 0 ? 'danger' : 'secondary-light'}
                  disabled={!!loading || selectedSubdomainIds.length === 0}
                  onClick={deleteSelectedSubdomains}
                  style={{
                    height: '34px',
                    minHeight: '34px',
                    padding: '0 12px',
                    fontSize: '13px',
                    opacity: selectedSubdomainIds.length === 0 ? 0.55 : 1,
                  }}
                >
                  <Trash2 size={14} />
                  {selectedSubdomainIds.length > 0
                    ? `${t('Delete')} (${selectedSubdomainIds.length})`
                    : t('Delete')}
                </button>
              </div>
            </div>

            {/* DirectAdmin-Style Subdomain Table */}
            <div style={{ overflowX: 'auto' }}>
              <table
                className="da-subdomain-table"
                style={{
                  width: '100%',
                  borderCollapse: 'collapse',
                  fontSize: '13.5px',
                }}
              >
                <thead>
                  <tr
                    style={{
                      borderBottom: '1px solid var(--border)',
                      background: 'var(--surface-alt)',
                      textAlign: 'left',
                    }}
                  >
                    <th style={{ width: '42px', padding: '11px 14px', textAlign: 'center' }}>
                      <input
                        type="checkbox"
                        checked={allFilteredSelected}
                        disabled={filteredSubdomains.length === 0}
                        onChange={e => {
                          if (e.target.checked) {
                            setSelectedSubdomainIds(filteredSubdomains.map(s => s.id));
                          } else {
                            setSelectedSubdomainIds([]);
                          }
                        }}
                        aria-label={t('Select all subdomains')}
                      />
                    </th>
                    <th style={{ padding: '11px 14px', fontWeight: 600, color: 'var(--text-secondary)' }}>
                      {t('Subdomain Name')}
                    </th>
                    {subdomainCols.bandwidth && (
                      <th style={{ padding: '11px 14px', fontWeight: 600, color: 'var(--text-secondary)' }}>
                        {t('Bandwidth')}
                      </th>
                    )}
                    {subdomainCols.docroot && (
                      <th style={{ padding: '11px 14px', fontWeight: 600, color: 'var(--text-secondary)' }}>
                        {t('Docroot')}
                      </th>
                    )}
                    {subdomainCols.php && (
                      <th style={{ padding: '11px 14px', fontWeight: 600, color: 'var(--text-secondary)' }}>
                        {t('PHP Version')}
                      </th>
                    )}
                    {subdomainCols.ssl && (
                      <th style={{ padding: '11px 14px', fontWeight: 600, color: 'var(--text-secondary)' }}>
                        SSL
                      </th>
                    )}
                    <th style={{ padding: '11px 16px', fontWeight: 600, color: 'var(--text-secondary)', textAlign: 'right' }}>
                      {t('Actions')}
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {filteredSubdomains.length === 0 ? (
                    <tr>
                      <td
                        colSpan={visibleColCount}
                        style={{
                          padding: '38px 20px',
                          textAlign: 'center',
                          color: 'var(--text-muted)',
                        }}
                      >
                        <div
                          style={{
                            display: 'inline-flex',
                            alignItems: 'center',
                            gap: '8px',
                            fontSize: '14px',
                            fontWeight: 500,
                            color: 'var(--text-secondary)',
                          }}
                        >
                          <AlertCircle size={17} style={{ color: 'var(--text-muted)' }} />
                          <span>
                            {searchActive
                              ? t('No subdomains match this search.')
                              : t('No items to show')}
                          </span>
                        </div>
                      </td>
                    </tr>
                  ) : (
                    filteredSubdomains.map(site => {
                      const isChecked = selectedSubdomainIds.includes(site.id);
                      const parentDomain = findParentDomain(site.domain);
                      const prefixPart = parentDomain && site.domain.toLowerCase().endsWith('.' + parentDomain)
                        ? site.domain.slice(0, -(parentDomain.length + 1))
                        : site.domain;
                      return (
                        <React.Fragment key={site.id}>
                          <tr
                            style={{
                              borderBottom: '1px solid var(--border)',
                              background: isChecked ? 'var(--primary-5)' : 'transparent',
                              transition: 'background 0.15s ease',
                            }}
                          >
                            <td style={{ padding: '12px 14px', textAlign: 'center' }}>
                              <input
                                type="checkbox"
                                checked={isChecked}
                                onChange={e => {
                                  if (e.target.checked) {
                                    setSelectedSubdomainIds(prev => [...new Set([...prev, site.id])]);
                                  } else {
                                    setSelectedSubdomainIds(prev => prev.filter(id => id !== site.id));
                                  }
                                }}
                                aria-label={`Select ${site.domain}`}
                              />
                            </td>
                            <td style={{ padding: '12px 14px' }}>
                              <div className="da-toolbar-controls" style={{ display: 'flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap' }}>
                                <a
                                  href={websiteUrl(site)}
                                  target="_blank"
                                  rel="noopener noreferrer"
                                  style={{
                                    fontWeight: 600,
                                    color: 'var(--primary)',
                                    textDecoration: 'none',
                                    display: 'inline-flex',
                                    alignItems: 'center',
                                    gap: '5px',
                                  }}
                                >
                                  <span>{ prefixPart }</span>
                                  {parentDomain && (
                                    <span style={{ color: 'var(--text-secondary)', fontWeight: 500 }}>
                                      .{parentDomain}
                                    </span>
                                  )}
                                  <ExternalLink size={12} style={{ opacity: 0.65 }} />
                                </a>
                                {site.waf_enabled && <span className="badge ok" style={{ fontSize: '10.5px' }}>WAF</span>}
                                {site.nginx_custom && <span className="badge ok" style={{ fontSize: '10.5px' }}>{t('Custom Nginx')}</span>}
                              </div>
                            </td>
                            {subdomainCols.bandwidth && (
                              <td style={{ padding: '12px 14px', color: 'var(--text-secondary)', fontSize: '13px' }}>
                                0 MB / ∞
                              </td>
                            )}
                            {subdomainCols.docroot && (
                              <td style={{ padding: '12px 14px' }}>
                                <button
                                  type="button"
                                  className="secondary-light"
                                  onClick={() => openWebsiteFileManager(site)}
                                  title={t('Open in File Manager')}
                                  style={{
                                    height: '28px',
                                    minHeight: '28px',
                                    padding: '0 10px',
                                    fontSize: '12px',
                                    fontFamily: 'var(--font-mono)',
                                    color: 'var(--text-secondary)',
                                    background: 'var(--surface-alt)',
                                    border: '1px solid var(--border)',
                                    borderRadius: '6px',
                                  }}
                                >
                                  <FolderOpen size={13} style={{ color: 'var(--primary)' }} />
                                  <span>/domains/{site.domain}/public_html</span>
                                </button>
                              </td>
                            )}
                            {subdomainCols.php && (
                              <td style={{ padding: '12px 14px' }}>
                                {site.app_type !== 'static' && !isProxiedAppType(site.app_type) ? (
                                  <select
                                    value={site.php_version || '8.4'}
                                    disabled={!!loading}
                                    onChange={e => changeWebsitePhp(site, e.target.value)}
                                    aria-label={`PHP version for ${site.domain}`}
                                    style={{
                                      height: '30px',
                                      minHeight: '30px',
                                      width: 'auto',
                                      padding: '0 24px 0 8px',
                                      fontSize: '12.5px',
                                      fontWeight: 600,
                                    }}
                                  >
                                    {(installedPhpList.includes(site.php_version)
                                      ? installedPhpList
                                      : [...installedPhpList, site.php_version].filter(Boolean)
                                    ).map(v => (
                                      <option key={v} value={v}>PHP {v}</option>
                                    ))}
                                  </select>
                                ) : (
                                  <span style={{ color: 'var(--text-muted)', fontSize: '12.5px' }}>—</span>
                                )}
                              </td>
                            )}
                            {subdomainCols.ssl && (
                              <td style={{ padding: '12px 14px' }}>
                                <span className={`badge site-ssl-badge ${site.ssl_enabled ? 'ok' : 'warn'}`}>
                                  {sslBadge(site)}
                                </span>
                              </td>
                            )}
                            <td style={{ padding: '12px 16px', textAlign: 'right' }}>
                              <div style={{ display: 'inline-flex', alignItems: 'center', gap: '6px', justifyContent: 'flex-end' }}>
                                <button
                                  className="site-icon-button secondary-light"
                                  data-tooltip="Files"
                                  title={t('Files')}
                                  aria-label={`Open file manager for ${site.domain}`}
                                  disabled={!!loading}
                                  onClick={() => openWebsiteFileManager(site)}
                                >
                                  <FolderOpen size={14} />
                                </button>
                                <button
                                  className="site-icon-button secondary-light"
                                  data-tooltip="Logs"
                                  title={t('Logs')}
                                  aria-label={`View logs for ${site.domain}`}
                                  disabled={!!loading}
                                  onClick={() => openWebsiteLogs(site)}
                                >
                                  <FileText size={14} />
                                </button>
                                <button
                                  className="site-icon-button secondary-light"
                                  data-tooltip="Terminal"
                                  title={t('Terminal')}
                                  aria-label={`Open terminal for ${site.domain}`}
                                  disabled={!!loading}
                                  onClick={() => openWebsiteTerminal(site)}
                                >
                                  <TerminalIcon size={14} />
                                </button>
                                <button
                                  className="site-icon-button secondary-light"
                                  data-tooltip="Settings"
                                  title={t('Domain settings')}
                                  aria-label={`Edit settings for ${site.domain}`}
                                  disabled={!!loading}
                                  onClick={() => openNginxCustom(site)}
                                >
                                  <SettingsIcon size={14} />
                                </button>
                                <button
                                  className="site-icon-button danger"
                                  data-tooltip="Delete"
                                  title={t('Delete')}
                                  aria-label={`Delete ${site.domain}`}
                                  disabled={!!loading}
                                  onClick={() => deleteWebsite(site.id)}
                                >
                                  <Trash2 size={14} />
                                </button>
                              </div>
                            </td>
                          </tr>
                          {(nginxCustomEditing?.id === site.id ||
                            logViewer?.id === site.id ||
                            terminalViewer?.id === site.id) && (
                            <tr>
                              <td colSpan={visibleColCount} style={{ padding: '12px 16px', background: 'var(--surface-alt)' }}>
                                {nginxCustomEditing?.id === site.id && renderNginxEditor()}
                                {logViewer?.id === site.id && renderWebsiteLogViewer()}
                                {terminalViewer?.id === site.id && renderWebsiteTerminal()}
                              </td>
                            </tr>
                          )}
                        </React.Fragment>
                      );
                    })
                  )}
                </tbody>
              </table>
            </div>
          </div>

          {/* DirectAdmin-Style "+ ADD SUBDOMAIN" Modal Dialog */}
          {subdomainModalOpen && (
            <div
              className="modal-backdrop"
              onClick={() => setSubdomainModalOpen(false)}
              style={{
                position: 'fixed',
                inset: 0,
                zIndex: 1000,
                background: 'rgba(15, 23, 42, 0.55)',
                backdropFilter: 'blur(4px)',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                padding: '16px',
              }}
            >
              <div
                className="modal"
                onClick={e => e.stopPropagation()}
                style={{
                  width: '100%',
                  maxWidth: '520px',
                  borderRadius: '14px',
                  background: 'var(--bg-elevated)',
                  border: '1px solid var(--border-strong)',
                  boxShadow: 'var(--shadow-lg)',
                  overflow: 'hidden',
                }}
              >
                <div
                  className="modal-header"
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'space-between',
                    padding: '16px 20px',
                    borderBottom: '1px solid var(--border)',
                  }}
                >
                  <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                    <div
                      style={{
                        width: '32px',
                        height: '32px',
                        borderRadius: '8px',
                        background: 'rgba(22, 163, 74, 0.14)',
                        color: '#16a34a',
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'center',
                      }}
                    >
                      <Globe size={17} />
                    </div>
                    <h3 style={{ margin: 0, fontSize: '16px', fontWeight: 700, color: 'var(--text)' }}>
                      {t('Add Subdomain')}
                    </h3>
                  </div>
                  <button
                    type="button"
                    className="secondary-light"
                    onClick={() => setSubdomainModalOpen(false)}
                    style={{ padding: '4px', height: '30px', minHeight: '30px', width: '30px' }}
                    aria-label={t('Close')}
                  >
                    <X size={16} />
                  </button>
                </div>

                <div
                  className="modal-body"
                  style={{
                    padding: '20px',
                    display: 'flex',
                    flexDirection: 'column',
                    gap: '16px',
                  }}
                >
                  <div>
                    <label
                      style={{
                        display: 'block',
                        fontSize: '13px',
                        fontWeight: 600,
                        color: 'var(--text)',
                        marginBottom: '6px',
                      }}
                    >
                      {t('Subdomain')}
                    </label>
                    <div
                      className="da-subdomain-input-grid"
                      style={{
                        display: 'grid',
                        gridTemplateColumns: '1fr auto minmax(165px, 46%)',
                        alignItems: 'center',
                        gap: '6px',
                      }}
                    >
                      <input
                        autoFocus
                        value={subdomainPrefix}
                        onChange={e => setSubdomainPrefix(e.target.value)}
                        onKeyDown={e => {
                          if (e.key === 'Enter' && subdomainPrefix.trim()) createSubdomain();
                        }}
                        placeholder={t('sub (e.g. blog, shop, api)')}
                        aria-label={t('Subdomain prefix')}
                      />
                      <span style={{ fontWeight: 700, fontSize: '16px', color: 'var(--text-muted)' }}>.</span>
                      <select
                        value={subdomainParent || (rootDomains[0]?.domain || '')}
                        onChange={e => setSubdomainParent(e.target.value)}
                        aria-label={t('Parent domain')}
                      >
                        {rootDomains.map(d => (
                          <option key={d.id} value={d.domain}>
                            {d.domain}
                          </option>
                        ))}
                      </select>
                    </div>
                  </div>

                  <div>
                    <label
                      style={{
                        display: 'block',
                        fontSize: '13px',
                        fontWeight: 600,
                        color: 'var(--text)',
                        marginBottom: '6px',
                      }}
                    >
                      {t('Document Root')}
                    </label>
                    <div
                      style={{
                        padding: '10px 12px',
                        borderRadius: '8px',
                        background: 'var(--surface-alt)',
                        border: '1px solid var(--border)',
                        display: 'flex',
                        flexDirection: 'column',
                        gap: '6px',
                      }}
                    >
                      <label
                        style={{
                          display: 'flex',
                          alignItems: 'center',
                          gap: '8px',
                          fontSize: '13px',
                          fontWeight: 500,
                          color: 'var(--text)',
                          cursor: 'pointer',
                        }}
                      >
                        <input
                          type="radio"
                          name="subdomain-docroot-mode"
                          checked={subdomainDocrootMode === 'default'}
                          onChange={() => setSubdomainDocrootMode('default')}
                        />
                        <span>{t('Default')}</span>
                      </label>
                      <code
                        style={{
                          fontSize: '12px',
                          color: 'var(--text-secondary)',
                          paddingLeft: '24px',
                          wordBreak: 'break-all',
                        }}
                      >
                        /domains/{previewSubdomain}/public_html
                      </code>
                    </div>
                  </div>

                  <div>
                    <label
                      style={{
                        display: 'block',
                        fontSize: '13px',
                        fontWeight: 600,
                        color: 'var(--text)',
                        marginBottom: '6px',
                      }}
                    >
                      {t('PHP Version')}
                    </label>
                    <select
                      value={installedPhpList.includes(phpVersion) ? phpVersion : (installedPhpList[0] || '8.4')}
                      onChange={e => setPhpVersion(e.target.value)}
                      aria-label={t('PHP version')}
                    >
                      {installedPhpList.map(v => (
                        <option key={v} value={v}>
                          PHP {v}
                        </option>
                      ))}
                    </select>
                  </div>
                </div>

                <div
                  className="modal-footer"
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'flex-end',
                    gap: '10px',
                    padding: '14px 20px',
                    borderTop: '1px solid var(--border)',
                    background: 'var(--surface-alt)',
                  }}
                >
                  <button
                    type="button"
                    className="secondary-light"
                    onClick={() => setSubdomainModalOpen(false)}
                  >
                    {t('Cancel')}
                  </button>
                  <button
                    type="button"
                    disabled={!!loading || !subdomainPrefix.trim() || !subdomainParent}
                    onClick={createSubdomain}
                    style={{
                      background: '#16a34a',
                      borderColor: '#15803d',
                      color: '#ffffff',
                      fontWeight: 600,
                    }}
                  >
                    <Plus size={15} />
                    {t('Add Subdomain')}
                  </button>
                </div>
              </div>
            </div>
          )}
        </div>
      );
    }

    // =========================================================================
    // DIRECTADMIN-STYLE DOMAINS MANAGEMENT VIEW (mode === 'domains')
    // =========================================================================
    const visibleWebsites = searchActive
      ? rootDomains.filter(site => {
          const q = websiteSearch.trim().toLowerCase();
          return (
            site.domain.toLowerCase().includes(q) ||
            (site.aliases || []).some(a => (a.domain || '').toLowerCase().includes(q))
          );
        })
      : rootDomains;
    const allDomainsSelected = visibleWebsites.length > 0 && visibleWebsites.every(s => selectedDomainIds.includes(s.id));
    const domainVisibleColCount = 2
      + (domainCols.bandwidth ? 1 : 0)
      + (domainCols.disk ? 1 : 0)
      + (domainCols.subdomains ? 1 : 0)
      + (domainCols.docroot ? 1 : 0)
      + (domainCols.php ? 1 : 0)
      + (domainCols.ssl ? 1 : 0)
      + 1;
    const previewNewDomain = domain.trim().toLowerCase() || 'domain.com';

    return (
      <div className="da-domains-manager" style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
        {/* DirectAdmin-style Top Bar: Breadcrumb on Left, Active Domain Context Selector on Right */}
        <div
          className="da-top-bar"
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            gap: '12px',
            flexWrap: 'wrap',
            padding: '10px 16px',
            borderRadius: '10px',
            background: 'var(--surface)',
            border: '1px solid var(--border)',
            boxShadow: 'var(--shadow-xs)',
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px', fontSize: '13px', color: 'var(--text-muted)' }}>
            <button
              type="button"
              className="secondary-light"
              onClick={() => navigateToPage('dashboard')}
              style={{
                border: 'none',
                background: 'transparent',
                padding: '2px 6px',
                height: 'auto',
                minHeight: 'unset',
                fontSize: '13px',
                color: 'var(--text-secondary)',
                fontWeight: 500,
              }}
            >
              <Home size={14} style={{ marginRight: '4px' }} />
              {t('Dashboard')}
            </button>
            <span>›</span>
            <strong style={{ color: 'var(--text)', fontWeight: 600 }}>{t('Domains')}</strong>
          </div>

          <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
            <span style={{ fontSize: '12.5px', color: 'var(--text-muted)', fontWeight: 500 }}>{t('Domain')}:</span>
            <div
              style={{
                display: 'inline-flex',
                alignItems: 'center',
                gap: '8px',
                padding: '4px 12px',
                borderRadius: '999px',
                background: 'var(--surface-alt)',
                border: '1px solid var(--border-strong)',
              }}
            >
              <Globe size={14} style={{ color: 'var(--primary)', flexShrink: 0 }} />
              {rootDomains.length === 0 ? (
                <span style={{ fontSize: '13px', color: 'var(--text-muted)' }}>{t('No domain yet')}</span>
              ) : (
                <select
                  value={String(selectedWebsiteId || defaultSite?.id || rootDomains[0]?.id || '')}
                  onChange={e => {
                    const idVal = e.target.value;
                    setSelectedWebsiteId(idVal);
                    const matched = rootDomains.find(d => String(d.id) === idVal);
                    if (matched) {
                      setSubdomainParent(matched.domain);
                      setSubdomainParentFilter(matched.domain.toLowerCase());
                    }
                  }}
                  aria-label={t('Select active domain')}
                  style={{
                    border: 'none',
                    background: 'transparent',
                    fontWeight: 600,
                    fontSize: '13.5px',
                    color: 'var(--text)',
                    padding: '2px 20px 2px 2px',
                    height: '26px',
                    minHeight: '26px',
                    boxShadow: 'none',
                    cursor: 'pointer',
                  }}
                >
                  {rootDomains.map(d => (
                    <option key={d.id} value={String(d.id)}>
                      {d.domain}
                    </option>
                  ))}
                </select>
              )}
            </div>
          </div>
        </div>

        {/* DirectAdmin-style Header Row: Title on Left + "+ ADD NEW" & "RENAME DOMAIN" Buttons on Right */}
        <div
          className="da-page-header"
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            gap: '12px',
            flexWrap: 'wrap',
            padding: '4px 2px',
          }}
        >
          <div>
            <h2 style={{ margin: 0, fontSize: '21px', fontWeight: 700, letterSpacing: '-0.01em', color: 'var(--text)' }}>
              {t('Domains')}
            </h2>
          </div>
          <div className="da-header-actions" style={{ display: 'flex', alignItems: 'center', gap: '10px', flexWrap: 'wrap' }}>
            <button
              type="button"
              onClick={() => setCreateFormOpen(true)}
              style={{
                background: '#16a34a',
                borderColor: '#15803d',
                color: '#ffffff',
                fontWeight: 700,
                fontSize: '12.5px',
                letterSpacing: '0.03em',
                textTransform: 'uppercase',
                padding: '0 16px',
                height: '38px',
                borderRadius: '8px',
                boxShadow: '0 2px 6px rgba(22, 163, 74, 0.25)',
              }}
            >
              <Plus size={16} />
              {t('Add New')}
            </button>
            <button
              type="button"
              disabled={rootDomains.length === 0}
              onClick={() => {
                const initialTarget = selectedDomainIds.length === 1
                  ? String(selectedDomainIds[0])
                  : String(defaultSite?.id || rootDomains[0]?.id || '');
                setRenameTargetId(initialTarget);
                const found = rootDomains.find(d => String(d.id) === initialTarget);
                setRenameNewDomain(found ? found.domain : '');
                setRenameModalOpen(true);
              }}
              style={{
                background: 'var(--primary)',
                borderColor: 'var(--primary)',
                color: '#ffffff',
                fontWeight: 700,
                fontSize: '12.5px',
                letterSpacing: '0.03em',
                textTransform: 'uppercase',
                padding: '0 16px',
                height: '38px',
                borderRadius: '8px',
              }}
            >
              <Pencil size={14} />
              {t('Rename Domain')}
            </button>
          </div>
        </div>

        {/* DirectAdmin-style Main Card: Default Domain + Domains Quota Bar + Toolbar + Table */}
        <div className="section" style={{ padding: 0, overflow: 'visible' }}>
          {/* Top Quota & Action Toolbar */}
          <div
            className="da-quota-toolbar"
            style={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
              gap: '14px',
              flexWrap: 'wrap',
              padding: '14px 18px',
              borderBottom: '1px solid var(--border)',
              background: 'var(--surface)',
              borderTopLeftRadius: '12px',
              borderTopRightRadius: '12px',
            }}
          >
            {/* Left: Default Domain & Domains Quota Counter */}
            <div style={{ display: 'flex', alignItems: 'center', gap: '22px', flexWrap: 'wrap' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                <Globe size={16} style={{ color: 'var(--text-secondary)' }} />
                <span style={{ fontSize: '13.5px', color: 'var(--text-secondary)', fontWeight: 500 }}>
                  {t('Default Domain')}
                </span>
                <strong style={{ fontSize: '14px', fontWeight: 700, color: 'var(--text)' }}>
                  {defaultSite ? defaultSite.domain : '—'}
                </strong>
              </div>

              <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                <Globe size={16} style={{ color: 'var(--primary)' }} />
                <span style={{ fontSize: '13.5px', color: 'var(--text-secondary)', fontWeight: 500 }}>
                  {t('Domains')}
                </span>
                <strong style={{ fontSize: '14.5px', fontWeight: 700, color: 'var(--text)' }}>
                  {rootDomains.length} / {websiteLimit > 0 ? websiteLimit : '∞'}
                </strong>
              </div>
            </div>

            {/* Right: Search, Columns Dropdown, Refresh, Set as default, Suspend / Unsuspend, Delete */}
            <div className="da-toolbar-controls" style={{ display: 'flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap' }}>
              <div
                style={{
                  position: 'relative',
                  display: 'flex',
                  alignItems: 'center',
                  minWidth: '200px',
                }}
              >
                <Search
                  size={14}
                  style={{
                    position: 'absolute',
                    left: '10px',
                    color: 'var(--text-muted)',
                    pointerEvents: 'none',
                  }}
                />
                <NoAutofillInput
                  type="search"
                  name="website-search"
                  value={websiteSearch}
                  onChange={e => setWebsiteSearch(e.target.value)}
                  placeholder={t('Search domain, alias, or path')}
                  aria-label={t('Search domains')}
                  style={{
                    paddingLeft: '32px',
                    paddingRight: websiteSearch ? '28px' : '10px',
                    height: '34px',
                    minHeight: '34px',
                    fontSize: '13px',
                    width: '100%',
                  }}
                />
                {websiteSearch && (
                  <button
                    type="button"
                    className="mini secondary-light"
                    onClick={() => setWebsiteSearch('')}
                    aria-label={t('Clear search')}
                    style={{
                      position: 'absolute',
                      right: '4px',
                      border: 'none',
                      background: 'transparent',
                      padding: '2px',
                      height: '24px',
                      minHeight: '24px',
                    }}
                  >
                    <X size={13} />
                  </button>
                )}
              </div>

              {/* Columns Picker Dropdown */}
              <div style={{ position: 'relative' }}>
                <button
                  type="button"
                  className="secondary-light"
                  onClick={() => setDomainColsOpen(prev => !prev)}
                  style={{ height: '34px', minHeight: '34px', padding: '0 12px', fontSize: '13px' }}
                >
                  {t('Columns')}
                  <ChevronDown size={14} />
                </button>
                {domainColsOpen && (
                  <div
                    style={{
                      position: 'absolute',
                      top: 'calc(100% + 6px)',
                      right: 0,
                      zIndex: 40,
                      minWidth: '190px',
                      padding: '8px 10px',
                      borderRadius: '10px',
                      background: 'var(--bg-elevated)',
                      border: '1px solid var(--border-strong)',
                      boxShadow: 'var(--shadow-lg)',
                      display: 'flex',
                      flexDirection: 'column',
                      gap: '6px',
                    }}
                  >
                    {[
                      ['bandwidth', t('Bandwidth')],
                      ['disk', t('Disk Usage')],
                      ['subdomains', t('Subdomains')],
                      ['docroot', t('Docroot')],
                      ['php', t('PHP Version')],
                      ['ssl', t('SSL Status')],
                    ].map(([colKey, colLabel]) => (
                      <label
                        key={colKey}
                        style={{
                          display: 'flex',
                          alignItems: 'center',
                          gap: '8px',
                          fontSize: '13px',
                          padding: '4px 6px',
                          borderRadius: '6px',
                          cursor: 'pointer',
                          color: 'var(--text)',
                        }}
                      >
                        <input
                          type="checkbox"
                          checked={!!domainCols[colKey]}
                          onChange={e =>
                            setDomainCols(prev => ({ ...prev, [colKey]: e.target.checked }))
                          }
                        />
                        <span>{colLabel}</span>
                      </label>
                    ))}
                  </div>
                )}
              </div>

              {/* Refresh Button */}
              <button
                type="button"
                className="secondary-light"
                disabled={!!loading || websiteSearching}
                onClick={() => loadWebsiteList(websiteSearch, true)}
                title={t('Refresh')}
                aria-label={t('Refresh')}
                style={{ height: '34px', minHeight: '34px', padding: '0 10px' }}
              >
                <RefreshCw size={14} className={websiteSearching ? 'spin' : ''} />
              </button>

              {/* Set as default Button */}
              <button
                type="button"
                className="secondary-light"
                disabled={!!loading || selectedDomainIds.length !== 1}
                onClick={handleSetDefaultDomain}
                style={{
                  height: '34px',
                  minHeight: '34px',
                  padding: '0 12px',
                  fontSize: '13px',
                  opacity: selectedDomainIds.length !== 1 ? 0.55 : 1,
                }}
              >
                {t('Set as default')}
              </button>

              {/* Suspend / Unsuspend Button */}
              <button
                type="button"
                className="secondary-light"
                disabled={!!loading || selectedDomainIds.length === 0}
                onClick={handleSuspendToggleDomains}
                style={{
                  height: '34px',
                  minHeight: '34px',
                  padding: '0 12px',
                  fontSize: '13px',
                  opacity: selectedDomainIds.length === 0 ? 0.55 : 1,
                }}
              >
                {t('Suspend / Unsuspend')}
              </button>

              {/* Bulk Delete Button */}
              <button
                type="button"
                className={selectedDomainIds.length > 0 ? 'danger' : 'secondary-light'}
                disabled={!!loading || selectedDomainIds.length === 0}
                onClick={handleDeleteSelectedDomains}
                style={{
                  height: '34px',
                  minHeight: '34px',
                  padding: '0 12px',
                  fontSize: '13px',
                  opacity: selectedDomainIds.length === 0 ? 0.55 : 1,
                }}
              >
                <Trash2 size={14} />
                {selectedDomainIds.length > 0
                  ? `${t('Delete')} (${selectedDomainIds.length})`
                  : t('Delete')}
              </button>
            </div>
          </div>

          {/* DirectAdmin-Style Domains Table */}
          <div style={{ overflowX: 'auto' }}>
            <table
              className="da-domains-table"
              style={{
                width: '100%',
                borderCollapse: 'collapse',
                fontSize: '13.5px',
              }}
            >
              <thead>
                <tr
                  style={{
                    borderBottom: '1px solid var(--border)',
                    background: 'var(--surface-alt)',
                    textAlign: 'left',
                  }}
                >
                  <th style={{ width: '42px', padding: '11px 14px', textAlign: 'center' }}>
                    <input
                      type="checkbox"
                      checked={allDomainsSelected}
                      disabled={visibleWebsites.length === 0}
                      onChange={e => {
                        if (e.target.checked) {
                          setSelectedDomainIds(visibleWebsites.map(s => s.id));
                        } else {
                          setSelectedDomainIds([]);
                        }
                      }}
                      aria-label={t('Select all domains')}
                    />
                  </th>
                  <th style={{ padding: '11px 14px', fontWeight: 600, color: 'var(--text-secondary)' }}>
                    {t('Domain')}
                  </th>
                  {domainCols.bandwidth && (
                    <th style={{ padding: '11px 14px', fontWeight: 600, color: 'var(--text-secondary)' }}>
                      {t('Bandwidth')}
                    </th>
                  )}
                  {domainCols.disk && (
                    <th style={{ padding: '11px 14px', fontWeight: 600, color: 'var(--text-secondary)' }}>
                      {t('Disk Usage')}
                    </th>
                  )}
                  {domainCols.subdomains && (
                    <th style={{ padding: '11px 14px', fontWeight: 600, color: 'var(--text-secondary)' }}>
                      {t('Subdomains')}
                    </th>
                  )}
                  {domainCols.docroot && (
                    <th style={{ padding: '11px 14px', fontWeight: 600, color: 'var(--text-secondary)' }}>
                      {t('Docroot')}
                    </th>
                  )}
                  {domainCols.php && (
                    <th style={{ padding: '11px 14px', fontWeight: 600, color: 'var(--text-secondary)' }}>
                      {t('PHP Version')}
                    </th>
                  )}
                  {domainCols.ssl && (
                    <th style={{ padding: '11px 14px', fontWeight: 600, color: 'var(--text-secondary)' }}>
                      SSL
                    </th>
                  )}
                  <th style={{ padding: '11px 16px', fontWeight: 600, color: 'var(--text-secondary)', textAlign: 'right' }}>
                    {t('Actions')}
                  </th>
                </tr>
              </thead>
              <tbody>
                {visibleWebsites.length === 0 ? (
                  <tr>
                    <td
                      colSpan={domainVisibleColCount}
                      style={{
                        padding: '38px 20px',
                        textAlign: 'center',
                        color: 'var(--text-muted)',
                      }}
                    >
                      <div
                        style={{
                          display: 'inline-flex',
                          alignItems: 'center',
                          gap: '8px',
                          fontSize: '14px',
                          fontWeight: 500,
                          color: 'var(--text-secondary)',
                        }}
                      >
                        <AlertCircle size={17} style={{ color: 'var(--text-muted)' }} />
                        <span>
                          {searchActive
                            ? t('No domains match this search.')
                            : t('No items to show')}
                        </span>
                      </div>
                    </td>
                  </tr>
                ) : (
                  visibleWebsites.map(site => {
                    const isChecked = selectedDomainIds.includes(site.id);
                    const isDefault = defaultSite && String(defaultSite.id) === String(site.id);
                    const childSubdomainsCount = subdomainSites.filter(
                      sub => findParentDomain(sub.domain) === site.domain.toLowerCase()
                    ).length;

                    return (
                      <React.Fragment key={site.id}>
                        <tr
                          style={{
                            borderBottom: '1px solid var(--border)',
                            background: isChecked ? 'var(--primary-5)' : 'transparent',
                            transition: 'background 0.15s ease',
                          }}
                        >
                          <td style={{ padding: '12px 14px', textAlign: 'center' }}>
                            <input
                              type="checkbox"
                              checked={isChecked}
                              onChange={e => {
                                if (e.target.checked) {
                                  setSelectedDomainIds(prev => [...new Set([...prev, site.id])]);
                                } else {
                                  setSelectedDomainIds(prev => prev.filter(id => id !== site.id));
                                }
                              }}
                              aria-label={`Select ${site.domain}`}
                            />
                          </td>
                          <td style={{ padding: '12px 14px' }}>
                            <div className="da-toolbar-controls" style={{ display: 'flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap' }}>
                              <a
                                href={websiteUrl(site)}
                                target="_blank"
                                rel="noopener noreferrer"
                                style={{
                                  fontWeight: 600,
                                  color: 'var(--primary)',
                                  textDecoration: 'none',
                                  display: 'inline-flex',
                                  alignItems: 'center',
                                  gap: '5px',
                                }}
                              >
                                <span>{site.domain}</span>
                                <ExternalLink size={12} style={{ opacity: 0.65 }} />
                              </a>
                              {isDefault && (
                                <span
                                  className="badge"
                                  style={{
                                    fontSize: '10.5px',
                                    background: 'var(--primary-10)',
                                    color: 'var(--primary)',
                                    borderColor: 'var(--primary-20)',
                                  }}
                                >
                                  {t('Default')}
                                </span>
                              )}
                              {site.status === 'suspended' && (
                                <span className="badge danger" style={{ fontSize: '10.5px' }}>
                                  {t('Suspended')}
                                </span>
                              )}
                              {site.waf_enabled && (
                                <span className="badge ok" style={{ fontSize: '10.5px' }}>
                                  WAF
                                </span>
                              )}
                              {site.nginx_custom && (
                                <span className="badge ok" style={{ fontSize: '10.5px' }}>
                                  {t('Custom Nginx')}
                                </span>
                              )}
                              {(site.aliases || []).length > 0 && (
                                <span className="badge" style={{ fontSize: '10.5px' }}>
                                  {t('Pointers')}: {(site.aliases || []).length}
                                </span>
                              )}
                            </div>
                          </td>
                          {domainCols.bandwidth && (
                            <td style={{ padding: '12px 14px', color: 'var(--text-secondary)', fontSize: '13px' }}>
                              0 MB / ∞
                            </td>
                          )}
                          {domainCols.disk && (
                            <td style={{ padding: '12px 14px', color: 'var(--text-secondary)', fontSize: '13px' }}>
                              {storageUsageText && currentUser ? storageUsageText(currentUser) : '0 MB / ∞'}
                            </td>
                          )}
                          {domainCols.subdomains && (
                            <td style={{ padding: '12px 14px' }}>
                              <button
                                type="button"
                                className="secondary-light"
                                title={t('Manage subdomains')}
                                onClick={() => {
                                  setSubdomainParentFilter(site.domain.toLowerCase());
                                  setSubdomainParent(site.domain);
                                  setSelectedWebsiteId(String(site.id));
                                  navigateToPage('subdomains');
                                }}
                                style={{
                                  height: '26px',
                                  minHeight: '26px',
                                  padding: '0 10px',
                                  fontSize: '12.5px',
                                  fontWeight: 600,
                                  borderRadius: '999px',
                                }}
                              >
                                {childSubdomainsCount}
                              </button>
                            </td>
                          )}
                          {domainCols.docroot && (
                            <td style={{ padding: '12px 14px' }}>
                              <button
                                type="button"
                                className="secondary-light"
                                onClick={() => openWebsiteFileManager(site)}
                                title={t('Open in File Manager')}
                                style={{
                                  height: '28px',
                                  minHeight: '28px',
                                  padding: '0 10px',
                                  fontSize: '12px',
                                  fontFamily: 'var(--font-mono)',
                                  color: 'var(--text-secondary)',
                                  background: 'var(--surface-alt)',
                                  border: '1px solid var(--border)',
                                  borderRadius: '6px',
                                }}
                              >
                                <FolderOpen size={13} style={{ color: 'var(--primary)' }} />
                                <span>/domains/{site.domain}/public_html</span>
                              </button>
                            </td>
                          )}
                          {domainCols.php && (
                            <td style={{ padding: '12px 14px' }}>
                              {site.app_type !== 'static' && !isProxiedAppType(site.app_type) ? (
                                <select
                                  value={site.php_version || '8.4'}
                                  disabled={!!loading}
                                  onChange={e => changeWebsitePhp(site, e.target.value)}
                                  aria-label={`PHP version for ${site.domain}`}
                                  style={{
                                    height: '30px',
                                    minHeight: '30px',
                                    width: 'auto',
                                    padding: '0 24px 0 8px',
                                    fontSize: '12.5px',
                                    fontWeight: 600,
                                  }}
                                >
                                  {(installedPhpList.includes(site.php_version)
                                    ? installedPhpList
                                    : [...installedPhpList, site.php_version].filter(Boolean)
                                  ).map(v => (
                                    <option key={v} value={v}>PHP {v}</option>
                                  ))}
                                </select>
                              ) : (
                                <span style={{ color: 'var(--text-muted)', fontSize: '12.5px' }}>—</span>
                              )}
                            </td>
                          )}
                          {domainCols.ssl && (
                            <td style={{ padding: '12px 14px' }}>
                              <span className={`badge site-ssl-badge ${site.ssl_enabled ? 'ok' : 'warn'}`}>
                                {sslBadge(site)}
                              </span>
                            </td>
                          )}
                          <td style={{ padding: '12px 16px', textAlign: 'right' }}>
                            <div style={{ display: 'inline-flex', alignItems: 'center', gap: '6px', justifyContent: 'flex-end' }}>
                              <button
                                className="site-icon-button secondary-light"
                                data-tooltip="Files"
                                title={t('Files')}
                                aria-label={`Open file manager for ${site.domain}`}
                                disabled={!!loading}
                                onClick={() => openWebsiteFileManager(site)}
                              >
                                <FolderOpen size={14} />
                              </button>
                              <button
                                className="site-icon-button secondary-light"
                                data-tooltip="Logs"
                                title={t('Logs')}
                                aria-label={`View logs for ${site.domain}`}
                                disabled={!!loading}
                                onClick={() => openWebsiteLogs(site)}
                              >
                                <FileText size={14} />
                              </button>
                              <button
                                className="site-icon-button secondary-light"
                                data-tooltip="Terminal"
                                title={t('Terminal')}
                                aria-label={`Open terminal for ${site.domain}`}
                                disabled={!!loading}
                                onClick={() => openWebsiteTerminal(site)}
                              >
                                <TerminalIcon size={14} />
                              </button>
                              <button
                                className="site-icon-button secondary-light"
                                data-tooltip="Settings"
                                title={t('Domain settings')}
                                aria-label={`Edit settings for ${site.domain}`}
                                disabled={!!loading}
                                onClick={() => openNginxCustom(site)}
                              >
                                <SettingsIcon size={14} />
                              </button>
                              <button
                                className="site-icon-button danger"
                                data-tooltip="Delete"
                                title={t('Delete')}
                                aria-label={`Delete ${site.domain}`}
                                disabled={!!loading}
                                onClick={() => deleteWebsite(site.id)}
                              >
                                <Trash2 size={14} />
                              </button>
                            </div>
                          </td>
                        </tr>
                        {(nginxCustomEditing?.id === site.id ||
                          logViewer?.id === site.id ||
                          terminalViewer?.id === site.id) && (
                          <tr>
                            <td colSpan={domainVisibleColCount} style={{ padding: '12px 16px', background: 'var(--surface-alt)' }}>
                              {nginxCustomEditing?.id === site.id && renderNginxEditor()}
                              {logViewer?.id === site.id && renderWebsiteLogViewer()}
                              {terminalViewer?.id === site.id && renderWebsiteTerminal()}
                            </td>
                          </tr>
                        )}
                      </React.Fragment>
                    );
                  })
                )}
              </tbody>
            </table>
          </div>
        </div>

        {/* DirectAdmin-Style "+ ADD NEW" (Add Domain) Modal Dialog */}
        {createFormOpen && (
          <div
            className="modal-backdrop"
            onClick={() => setCreateFormOpen(false)}
            style={{
              position: 'fixed',
              inset: 0,
              zIndex: 1000,
              background: 'rgba(15, 23, 42, 0.55)',
              backdropFilter: 'blur(4px)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              padding: '16px',
            }}
          >
            <div
              className="modal"
              onClick={e => e.stopPropagation()}
              style={{
                width: '100%',
                maxWidth: '520px',
                borderRadius: '14px',
                background: 'var(--bg-elevated)',
                border: '1px solid var(--border-strong)',
                boxShadow: 'var(--shadow-lg)',
                overflow: 'hidden',
              }}
            >
              <div
                className="modal-header"
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'space-between',
                  padding: '16px 20px',
                  borderBottom: '1px solid var(--border)',
                }}
              >
                <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                  <div
                    style={{
                      width: '32px',
                      height: '32px',
                      borderRadius: '8px',
                      background: 'rgba(22, 163, 74, 0.14)',
                      color: '#16a34a',
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'center',
                    }}
                  >
                    <Globe size={17} />
                  </div>
                  <h3 style={{ margin: 0, fontSize: '16px', fontWeight: 700, color: 'var(--text)' }}>
                    {t('Add Domain')}
                  </h3>
                </div>
                <button
                  type="button"
                  className="secondary-light"
                  onClick={() => setCreateFormOpen(false)}
                  style={{ padding: '4px', height: '30px', minHeight: '30px', width: '30px' }}
                  aria-label={t('Close')}
                >
                  <X size={16} />
                </button>
              </div>

              <div
                className="modal-body"
                style={{
                  padding: '20px',
                  display: 'flex',
                  flexDirection: 'column',
                  gap: '16px',
                }}
              >
                <div>
                  <label
                    style={{
                      display: 'block',
                      fontSize: '13px',
                      fontWeight: 600,
                      color: 'var(--text)',
                      marginBottom: '6px',
                    }}
                  >
                    {t('Domain')}
                  </label>
                  <input
                    autoFocus
                    value={domain}
                    onChange={e => setDomain(e.target.value)}
                    onKeyDown={e => {
                      if (e.key === 'Enter' && domain.trim()) createWordPress();
                    }}
                    placeholder="domain.com"
                  />
                </div>

                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '12px' }}>
                  <div>
                    <label
                      style={{
                        display: 'block',
                        fontSize: '13px',
                        fontWeight: 600,
                        color: 'var(--text)',
                        marginBottom: '6px',
                      }}
                    >
                      {t('Bandwidth')}
                    </label>
                    <div
                      style={{
                        padding: '8px 12px',
                        borderRadius: '8px',
                        background: 'var(--surface-alt)',
                        border: '1px solid var(--border)',
                        fontSize: '13px',
                        color: 'var(--text-secondary)',
                      }}
                    >
                      {t('Same as Main Account')}
                    </div>
                  </div>
                  <div>
                    <label
                      style={{
                        display: 'block',
                        fontSize: '13px',
                        fontWeight: 600,
                        color: 'var(--text)',
                        marginBottom: '6px',
                      }}
                    >
                      {t('Disk Usage')}
                    </label>
                    <div
                      style={{
                        padding: '8px 12px',
                        borderRadius: '8px',
                        background: 'var(--surface-alt)',
                        border: '1px solid var(--border)',
                        fontSize: '13px',
                        color: 'var(--text-secondary)',
                      }}
                    >
                      {t('Same as Main Account')}
                    </div>
                  </div>
                </div>

                <div>
                  <label
                    style={{
                      display: 'block',
                      fontSize: '13px',
                      fontWeight: 600,
                      color: 'var(--text)',
                      marginBottom: '6px',
                    }}
                  >
                    {t('Document Root')}
                  </label>
                  <div
                    style={{
                      padding: '9px 12px',
                      borderRadius: '8px',
                      background: 'var(--surface-alt)',
                      border: '1px solid var(--border)',
                      fontFamily: 'var(--font-mono)',
                      fontSize: '12.5px',
                      color: 'var(--text-secondary)',
                    }}
                  >
                    /domains/{previewNewDomain}/public_html
                  </div>
                </div>

                <div>
                  <label
                    style={{
                      display: 'block',
                      fontSize: '13px',
                      fontWeight: 600,
                      color: 'var(--text)',
                      marginBottom: '6px',
                    }}
                  >
                    {t('PHP Version')}
                  </label>
                  <select
                    value={installedPhpList.includes(phpVersion) ? phpVersion : (installedPhpList[0] || '8.4')}
                    onChange={e => setPhpVersion(e.target.value)}
                    aria-label={t('PHP version')}
                  >
                    {installedPhpList.map(v => (
                      <option key={v} value={v}>
                        PHP {v}
                      </option>
                    ))}
                  </select>
                </div>
              </div>

              <div
                className="modal-footer"
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'flex-end',
                  gap: '10px',
                  padding: '14px 20px',
                  borderTop: '1px solid var(--border)',
                  background: 'var(--surface-alt)',
                }}
              >
                <button
                  type="button"
                  className="secondary-light"
                  onClick={() => setCreateFormOpen(false)}
                >
                  {t('Cancel')}
                </button>
                <button
                  type="button"
                  disabled={!!loading || !domain.trim()}
                  onClick={createWordPress}
                  style={{
                    background: '#16a34a',
                    borderColor: '#15803d',
                    color: '#ffffff',
                    fontWeight: 600,
                  }}
                >
                  <Plus size={15} />
                  {t('Add Domain')}
                </button>
              </div>
            </div>
          </div>
        )}

        {/* DirectAdmin-Style "RENAME DOMAIN" Modal Dialog */}
        {renameModalOpen && (
          <div
            className="modal-backdrop"
            onClick={() => setRenameModalOpen(false)}
            style={{
              position: 'fixed',
              inset: 0,
              zIndex: 1000,
              background: 'rgba(15, 23, 42, 0.55)',
              backdropFilter: 'blur(4px)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              padding: '16px',
            }}
          >
            <div
              className="modal"
              onClick={e => e.stopPropagation()}
              style={{
                width: '100%',
                maxWidth: '480px',
                borderRadius: '14px',
                background: 'var(--bg-elevated)',
                border: '1px solid var(--border-strong)',
                boxShadow: 'var(--shadow-lg)',
                overflow: 'hidden',
              }}
            >
              <div
                className="modal-header"
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'space-between',
                  padding: '16px 20px',
                  borderBottom: '1px solid var(--border)',
                }}
              >
                <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                  <div
                    style={{
                      width: '32px',
                      height: '32px',
                      borderRadius: '8px',
                      background: 'var(--primary-10)',
                      color: 'var(--primary)',
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'center',
                    }}
                  >
                    <Pencil size={16} />
                  </div>
                  <h3 style={{ margin: 0, fontSize: '16px', fontWeight: 700, color: 'var(--text)' }}>
                    {t('Rename Domain')}
                  </h3>
                </div>
                <button
                  type="button"
                  className="secondary-light"
                  onClick={() => setRenameModalOpen(false)}
                  style={{ padding: '4px', height: '30px', minHeight: '30px', width: '30px' }}
                  aria-label={t('Close')}
                >
                  <X size={16} />
                </button>
              </div>

              <div
                className="modal-body"
                style={{
                  padding: '20px',
                  display: 'flex',
                  flexDirection: 'column',
                  gap: '16px',
                }}
              >
                <div>
                  <label
                    style={{
                      display: 'block',
                      fontSize: '13px',
                      fontWeight: 600,
                      color: 'var(--text)',
                      marginBottom: '6px',
                    }}
                  >
                    {t('Select Domain')}
                  </label>
                  <select
                    value={renameTargetId}
                    onChange={e => {
                      const val = e.target.value;
                      setRenameTargetId(val);
                      const found = rootDomains.find(d => String(d.id) === val);
                      if (found) setRenameNewDomain(found.domain);
                    }}
                  >
                    {rootDomains.map(d => (
                      <option key={d.id} value={String(d.id)}>
                        {d.domain}
                      </option>
                    ))}
                  </select>
                </div>

                <div>
                  <label
                    style={{
                      display: 'block',
                      fontSize: '13px',
                      fontWeight: 600,
                      color: 'var(--text)',
                      marginBottom: '6px',
                    }}
                  >
                    {t('New Domain Name')}
                  </label>
                  <input
                    autoFocus
                    value={renameNewDomain}
                    onChange={e => setRenameNewDomain(e.target.value)}
                    onKeyDown={e => {
                      if (e.key === 'Enter' && renameNewDomain.trim()) handleRenameDomain();
                    }}
                    placeholder="new-domain.com"
                  />
                </div>
              </div>

              <div
                className="modal-footer"
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'flex-end',
                  gap: '10px',
                  padding: '14px 20px',
                  borderTop: '1px solid var(--border)',
                  background: 'var(--surface-alt)',
                }}
              >
                <button
                  type="button"
                  className="secondary-light"
                  onClick={() => setRenameModalOpen(false)}
                >
                  {t('Cancel')}
                </button>
                <button
                  type="button"
                  disabled={!!loading || !renameNewDomain.trim()}
                  onClick={handleRenameDomain}
                >
                  <Save size={14} />
                  {t('Rename Domain')}
                </button>
              </div>
            </div>
          </div>
        )}
      </div>
    );
  }

  return renderWebsites();
}
