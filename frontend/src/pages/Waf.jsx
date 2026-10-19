import React, { useState, useEffect } from 'react';
import { Archive, ArchiveRestore, ArrowLeft, Ban, Bot, Boxes, Check, ChevronDown, Clock, Code2, Copy, Cpu, Database, Dices, ExternalLink, Eye, FileText, FolderOpen, Globe, HardDrive, Home, Image, KeyRound, Lock, LogIn, LogOut, MemoryStick, Menu, Moon, MoveRight, Network, Pencil, Save, Search, Server, Settings as SettingsIcon, Shield, Sun, Trash2, TerminalIcon, Users, X, RefreshCw, Plus, Download, Upload, Play, Square, RotateCcw, AlertCircle, Activity, BrickWall, Bug, LockKeyhole, PackageOpen, ScrollText, ShieldAlert, CheckCircle, Zap, Bell, Mail, Send, Cloud, Inbox, Forward, ShieldCheck } from 'lucide-react';
import { EmptyState } from '../components/EmptyState.jsx';
import { useAppContext } from '../contexts/AppContext.jsx';
import { t } from '../i18n.js';

export function WafPage({ request, API, setPage, page, isReseller, WAF_ACCESS_LOG_DEFAULTS, HTTP_FLOOD_DEFAULTS, websites }) {
  const {
    loading, setLoading, setError, setNotice, selectedWebsiteId,
    isAdmin, addons, websiteList, navigateToPage, refreshAll, websiteUrl,
    normalizeHttpFloodConfig, toggleWafDefaultRule, loadBotBlocks, saveGlobalBots, addGlobalBots,
    updateWafAccessLogFilters, exportWafAccessLogs, accessLogBadgeClass, accessLogVerdictLabel, formatAccessLogTime, accessLogCountryLabel,
    wafRules, setWafRules,
    wafCustomRules, setWafCustomRules,
    selectedWafWebsiteId, setSelectedWafWebsiteId,
    wafSiteConfig, setWafSiteConfig,
    botBlocks, setBotBlocks,
    globalBots, setGlobalBots,
    wafAccessLogFilters, setWafAccessLogFilters,
    wafAccessLogs, setWafAccessLogs,
  } = useAppContext();

  const [wafGlobalRules, setWafGlobalRules] = useState('');
  const [httpFloodForm, setHttpFloodForm] = useState(() => normalizeHttpFloodConfig(HTTP_FLOOD_DEFAULTS));
  const [bulkBotOpen, setBulkBotOpen] = useState(false);
  const [crs, setCrs] = useState(null);
  const [newBotName, setNewBotName] = useState('');
  const [globalBotPaste, setGlobalBotPaste] = useState('');
  const [globalBotFilter, setGlobalBotFilter] = useState('');
  const [siteBotText, setSiteBotText] = useState('');

  async function toggleWebsiteWaf(site) {
    const nextValue = !site.waf_enabled;
    const data = await request(`/websites/${site.id}/waf`, { method: 'PATCH', body: JSON.stringify({ waf_enabled: nextValue }) }, `${nextValue ? 'Enabling' : 'Disabling'} WAF...`);
    if (data) {
      setNotice(`WAF ${nextValue ? 'enabled' : 'disabled'} for ${site.domain}.`);
      await refreshAll();
      if (String(selectedWafWebsiteId) === String(site.id)) {
        await loadWebsiteWafConfig(site.id);
      }
    }
  }

  useEffect(() => {
    if (isAdmin) {
      loadWafRules();
      loadCrs();
    } else {
      const firstWebsiteId = selectedWafWebsiteId || selectedWebsiteId || websites[0]?.id || '';
      if (firstWebsiteId) {
        setSelectedWafWebsiteId(String(firstWebsiteId));
        loadWebsiteWafConfig(firstWebsiteId, false);
      }
    }
    if (loadBotBlocks) loadBotBlocks(false);
    loadWafAccessLogs(wafAccessLogFilters, false);
  }, [isAdmin]);

  useEffect(() => {
    if (!isAdmin && !selectedWafWebsiteId && (selectedWebsiteId || websites[0]?.id)) {
      const firstId = String(selectedWebsiteId || websites[0].id);
      setSelectedWafWebsiteId(firstId);
      loadWebsiteWafConfig(firstId, false);
    }
  }, [isAdmin, websites, selectedWebsiteId]);
  useEffect(() => {
    if (wafSiteConfig === null && selectedWafWebsiteId) {
      loadWebsiteWafConfig();
    }
  }, [selectedWafWebsiteId, wafSiteConfig]);

  async function loadWafRules() {
    const data = await request('/waf/rules', {}, t('Loading WAF rules...'));
    if (data) {
      setWafRules(data);
      setWafGlobalRules(data.custom_rules || '');
      const firstWebsiteId = selectedWafWebsiteId || selectedWebsiteId || websites[0]?.id || '';
      if (firstWebsiteId) {
        setSelectedWafWebsiteId(String(firstWebsiteId));
        await loadWebsiteWafConfig(firstWebsiteId, false);
      }
    }
  }

  async function loadWebsiteWafConfig(websiteId = selectedWafWebsiteId, showLoading = true) {
    if (!websiteId) {
      setWafSiteConfig(null);
      setHttpFloodForm({ http_flood_enabled: false, ...HTTP_FLOOD_DEFAULTS });
      return;
    }
    const data = await request(`/waf/websites/${websiteId}`, {}, showLoading ? 'Loading website WAF...' : '');
    if (data) {
      setSelectedWafWebsiteId(String(websiteId));
      setWafSiteConfig(data);
      setWafCustomRules(data.custom_rules || '');
      setHttpFloodForm({ http_flood_enabled: !!data.http_flood_enabled, ...normalizeHttpFloodConfig(data.http_flood_config) });
      setSiteBotText((data.blocked_bots || []).join('\n'));
    }
  }

  async function openWafSite(websiteId) {
    await loadWebsiteWafConfig(websiteId);
    navigateToPage('waf-site');
  }

  async function saveWafGlobalRules() {
    const data = await request('/waf/rules/custom', { method: 'PUT', body: JSON.stringify({ content: wafGlobalRules }) }, t('Saving...'));
    if (data) { setNotice(t('Global WAF rules saved. They apply to every website with the WAF on.')); await loadWafRules(); }
  }

  async function loadCrs() {
    const data = await request('/waf/crs', { silent: true });
    if (data) setCrs(data);
  }

  async function saveCrsMode(mode) {
    if (mode === 'block' && !confirm(
      t('Switch OWASP CRS to blocking?') + '\n\n'
      + t('Websites with CRS on will start refusing requests that score above the threshold. Run Detect only first and read the logs, or a request somebody depends on may be the one it stops.')
    )) return;
    const data = await request('/waf/crs', {
      method: 'PUT',
      body: JSON.stringify({ mode }),
    }, `Switching OWASP CRS to ${mode}...`);
    if (data) await loadCrs();
  }

  async function toggleSiteCrs(config) {
    const turningOn = !config.crs_enabled;
    const domain = config.domain;
    if (turningOn && !confirm(t('Turn on OWASP CRS for {domain}? It uses about {n} MB of server RAM.', { domain, n: crs?.rss_mb_per_site || 50 }))) return;
    const data = await request(`/waf/websites/${config.website_id}/crs`, {
      method: 'PUT',
      body: JSON.stringify({ enabled: turningOn }),
    }, turningOn ? t('Turning CRS on for {domain}...', { domain }) : t('Turning CRS off for {domain}...', { domain }));
    if (data) {
      setNotice(turningOn ? t('OWASP CRS is on for {domain}.', { domain }) : t('OWASP CRS is off for {domain}.', { domain }));
      await loadWebsiteWafConfig(config.website_id, false);
      if (isAdmin) await loadCrs();
    }
  }

  async function saveWebsiteWafRules() {
    if (!selectedWafWebsiteId || !wafSiteConfig) return;
    const data = await request(`/waf/websites/${selectedWafWebsiteId}`, {
      method: 'PUT',
      body: JSON.stringify({ enabled_rule_ids: wafSiteConfig.enabled_rule_ids || [], custom_rules: wafCustomRules }),
    }, t('Saving website WAF rules...'));
    if (data) {
      setWafSiteConfig(data);
      setWafCustomRules(data.custom_rules || '');
      setNotice(data.message || 'Website WAF rules saved.');
      await refreshAll();
    }
  }

  async function saveWebsiteHttpFlood() {
    if (!selectedWafWebsiteId || !wafSiteConfig) return;
    const config = normalizeHttpFloodConfig(httpFloodForm);
    const data = await request(`/websites/${selectedWafWebsiteId}/http-flood`, {
      method: 'PATCH',
      body: JSON.stringify({ http_flood_enabled: !!httpFloodForm.http_flood_enabled, ...config }),
    }, t('Saving HTTP Flood settings...'));
    if (data) {
      setNotice(`HTTP Flood settings saved for ${data.domain}.`);
      await refreshAll();
      await loadWebsiteWafConfig(selectedWafWebsiteId, false);
    }
  }

  async function saveSiteBots() {
    if (!selectedWafWebsiteId) return;
    const data = await request(`/waf/websites/${selectedWafWebsiteId}/bots`, {
      method: 'PUT',
      body: JSON.stringify({ blocked_bots: siteBotText }),
    }, t('Saving blocked bots...'));
    if (data) {
      setSiteBotText((data.blocked_bots || []).join('\n'));
      setNotice(data.message || 'Blocked bots saved.');
      await loadBotBlocks();
    }
  }

  async function loadWafAccessLogs(filters = wafAccessLogFilters, showLoading = true) {
    const params = new URLSearchParams();
    if (filters.websiteId) params.set('website_id', filters.websiteId);
    params.set('verdict', filters.verdict || 'all');
    params.set('limit', String(filters.limit || 50));
    params.set('lines', '5000');
    if (filters.query?.trim()) params.set('q', filters.query.trim());
    const data = await request(`/waf/access-logs?${params.toString()}`, {}, showLoading ? 'Loading access logs...' : '');
    if (data) setWafAccessLogs(data);
  }

  async function applyWafAccessLogFilters() {
    await loadWafAccessLogs(wafAccessLogFilters, true);
  }

  async function clearWafAccessLogs() {
    const selected = websites.find(site => String(site.id) === String(wafAccessLogFilters.websiteId));
    const label = selected?.domain || 'all websites';
    if (!confirm(`Clear access logs for ${label}?`)) return;
    const params = new URLSearchParams();
    if (wafAccessLogFilters.websiteId) params.set('website_id', wafAccessLogFilters.websiteId);
    const suffix = params.toString() ? `?${params.toString()}` : '';
    const data = await request(`/waf/access-logs${suffix}`, { method: 'DELETE' }, t('Clearing access logs...'));
    if (data) {
      setNotice(data.message || 'Access logs cleared.');
      await loadWafAccessLogs(wafAccessLogFilters, false);
    }
  }

  function renderWaf() {
    const statusText = wafRules.status?.stdout || wafRules.status?.stderr || 'Click Refresh to load WAF status.';
    // The effective list, not the site's own: a site with nothing of its own
    // still enforces the global list, and reporting "No bots" for it was a lie.
    const rowFor = id => botBlocks?.websites?.find(w => w.website_id === id);
    const botCountFor = id => (rowFor(id)?.effective_blocked_bots || []).length;
    const ownCountFor = id => (rowFor(id)?.blocked_bots || []).length;
    return <>
      <div className="section">
        <div className="section-title">
          <div>
            <h2>WAF</h2>
            <p className="hint">{isAdmin
              ? t('Protection for each website. Open one to set its rules, flood limit and blocked bots.')
              : t('Protection for your websites. Open one to set its rules and blocked bots.')}</p>
          </div>
          <button className="secondary-light" disabled={!!loading} onClick={() => { loadBotBlocks(); if (isAdmin) { loadWafRules(); loadCrs(); } }}><RefreshCw size={14}/>{t('Refresh')}</button>
        </div>
        {/* Folded. This is systemctl and timer output - the first thing on the
            page was eleven lines of paths and a timer table, above the
            controls somebody came to use. What the WAF is actually doing is
            said by the badges below in words; this is here for the operator
            who is diagnosing, and they know to open it. */}
        {isAdmin && <details className="info-box firewall-status output-details">
          <summary><strong>{t('Status')}</strong><span>{t('Module, rule files and timers')}</span></summary>
          <pre>{statusText}</pre>
        </details>}
      </div>

      {isAdmin && <div className="section">
        <div className="section-title">
          <div>
            <h2>{t('OWASP Core Rule Set')}</h2>
            <p className="hint">{t('Inspects each request for SQL injection, XSS and similar attacks. Each website turns it on from its own page.')}</p>
          </div>
          <button className="secondary" disabled={!!loading} onClick={loadCrs}><RefreshCw size={14}/>{t('Check')}</button>
        </div>
        {!crs && <p className="hint">{t('Click Check to read the current state.')}</p>}
        {crs && <>
          <div className="waf-overview-badges" style={{ marginBottom: 12 }}>
            <span className={crs.mode === 'block' ? 'badge ok' : 'badge'}>
              {crs.mode === 'off' ? t('Off') : (crs.mode === 'detect' ? t('Detect only') : t('Blocking'))}
            </span>
            <span className={crs.installed ? 'badge ok' : 'badge'}>
              {crs.installed ? t('{n} rule file(s) installed', { n: crs.rule_files }) : t('Not installed')}
            </span>
            <span className="badge">{t('{n} website(s) with CRS on', { n: crs.sites_opted_in ?? 0 })}</span>
            {/* The memory essay is gone; a low-RAM warning is what is left of
                it, because every site that turns CRS on carries it (~50 MB). */}
            {(crs.ram_available_mb || 0) > 0 && crs.ram_available_mb < 1024 && <span className="badge danger">
              {t('{n} MB RAM free', { n: crs.ram_available_mb })}
            </span>}
          </div>
          <div className="segmented-control">
            {[['off', 'Off'], ['detect', 'Detect only'], ['block', 'Block']].map(([value, label]) => (
              <button
                key={value}
                className={crs.mode === value ? 'active' : ''}
                disabled={!!loading || crs.mode === value}
                onClick={() => saveCrsMode(value)}
              >{t(label)}</button>
            ))}
          </div>
          <p className="hint" style={{ marginTop: 10 }}>
            {crs.mode === 'off' && t('Off: requests are not inspected.')}
            {crs.mode === 'detect' && t('Detect only: attacks are logged, nothing is blocked.')}
            {crs.mode === 'block' && t('Block: attacks are refused on websites with CRS on.')}
          </p>
          {crs.mode !== 'off' && crs.panel_mode !== crs.mode && (
            <p className="hint">{t('Panel setting says "{panel}" but the server reports "{server}".', { panel: crs.panel_mode, server: crs.mode })}</p>
          )}
        </>}
      </div>}

      <div className="section">
        <div className="section-title"><h2>{t('Websites')}</h2></div>
        {websites.length === 0 && <EmptyState icon={Globe} message={t('No websites yet.')} />}
        <div className="table waf-overview-list">
          {websites.map(site => {
            const bots = botCountFor(site.id);
            // No CRS here: "WAF on" beside "CRS block" read as two things to
            // worry about. CRS is switched on the site's own page.
            return <div className="waf-overview-row" key={site.id}>
              <span className="waf-overview-domain"><strong>{site.domain}</strong></span>
              <div className="waf-overview-badges">
                <span className={site.waf_enabled ? 'badge ok' : 'badge'}>{site.waf_enabled ? t('WAF on') : t('WAF off')}</span>
                <span className={site.http_flood_enabled ? 'badge ok' : 'badge'}>{site.http_flood_enabled ? t('Flood on') : t('Flood off')}</span>
                <span
                  className={bots > 0 ? 'badge ok' : 'badge'}
                  title={ownCountFor(site.id) > 0 ? `${ownCountFor(site.id)} set on this site, the rest from the global list` : 'All from the global list'}
                >{bots > 0 ? t('{n} bot(s)', { n: bots }) : t('No bots')}</span>
              </div>
              <button className="secondary" disabled={!!loading} onClick={() => openWafSite(site.id)}><SettingsIcon size={14}/>{t('Configure')}</button>
            </div>;
          })}
        </div>
      </div>

      {isAdmin && <div className="section">
        <div className="section-title">
          <div>
            <h2>{t('Global custom rules')}</h2>
            <p className="hint">{t('ModSecurity rules for every website with the WAF on. Rules an AI assistant added are marked # spanel-mcp; delete a rule by deleting its lines.')}</p>
          </div>
          <span className="badge">{t('{n} rule(s)', { n: (wafGlobalRules.match(/^\s*SecRule/gm) || []).length })}</span>
        </div>
        <textarea className="code-editor" rows={12} spellCheck={false} value={wafGlobalRules} onChange={e => setWafGlobalRules(e.target.value)} />
        <div className="notify-actions" style={{ marginTop: 10 }}>
          <button type="button" disabled={!!loading || wafGlobalRules === (wafRules.custom_rules || '')} onClick={saveWafGlobalRules}>{t('Save')}</button>
          <button type="button" className="secondary-light" disabled={!!loading || wafGlobalRules === (wafRules.custom_rules || '')} onClick={() => setWafGlobalRules(wafRules.custom_rules || '')}>{t('Reset')}</button>
        </div>
      </div>}

      {isAdmin && <div className="section">
        <div className="section-title">
          <div>
            <h2>{t('Global bad bots')}</h2>
            <p className="hint">
              Blocked on every website on this server. A site can add more of its own from its page.
              {globalBots.length > 0 ? ` Currently ${globalBots.length} bot(s).` : ' Nothing blocked globally yet.'}
            </p>
          </div>
          <button disabled={!!loading} onClick={() => setBulkBotOpen(open => !open)}>{bulkBotOpen ? 'Hide' : 'Edit'}</button>
        </div>

        {bulkBotOpen && <div className="global-bots">
          <div className="global-bots-add">
            <input
              value={newBotName}
              placeholder={t('Add one bot, e.g. Amazonbot')}
              onChange={e => setNewBotName(e.target.value)}
              onKeyDown={e => { if (e.key === 'Enter') { addGlobalBots(newBotName); setNewBotName(''); } }}
            />
            <button type="button" disabled={!newBotName.trim()} onClick={() => { addGlobalBots(newBotName); setNewBotName(''); }}>
              <Plus size={14}/>{t('Add')}</button>
            <input
              className="global-bots-filter"
              value={globalBotFilter}
              placeholder={t('Filter the list')}
              onChange={e => setGlobalBotFilter(e.target.value)}
            />
          </div>

          <div className="global-bots-list">
            {globalBots.length === 0 && <p className="hint">{t('No bots yet. Add one above, or paste a list below.')}</p>}
            {globalBots
              .filter(name => !globalBotFilter.trim() || name.toLowerCase().includes(globalBotFilter.trim().toLowerCase()))
              .map(name => <span className="global-bot-chip" key={name}>
                <code>{name}</code>
                <button
                  type="button"
                  title={`Remove ${name}`}
                  onClick={() => setGlobalBots(prev => prev.filter(n => n !== name))}
                ><X size={12}/></button>
              </span>)}
          </div>

          <details className="global-bots-paste">
            <summary>{t('Paste a list')}</summary>
            <textarea
              className="code-editor"
              rows={6}
              spellCheck={false}
              value={globalBotPaste}
              onChange={e => setGlobalBotPaste(e.target.value)}
              placeholder={'AhrefsBot\nSemrushBot\nMJ12bot'}
            />
            <button type="button" disabled={!globalBotPaste.trim()} onClick={() => { addGlobalBots(globalBotPaste); setGlobalBotPaste(''); }}>
              <Plus size={14}/>{t('Add to list')}</button>
          </details>

          <div className="global-bots-actions">
            <button disabled={!!loading} onClick={() => saveGlobalBots(globalBots)}>
              <Shield size={14}/> {t('Save and apply to all {n} website(s)', { n: websites.length })}
            </button>
            <button
              className="secondary-light"
              disabled={!!loading}
              onClick={() => setGlobalBots(botBlocks?.global_blocked_bots || [])}
            >{t('Reset')}</button>
            <span className="hint">
              {globalBots.length} bot(s)
              {botBlocks?.max_bots ? ` - max ${botBlocks.max_bots}` : ''}
              {JSON.stringify(globalBots) !== JSON.stringify(botBlocks?.global_blocked_bots || []) ? ' - unsaved changes' : ''}
            </span>
          </div>
        </div>}
      </div>}
    </>;
  }

  function renderWafSite() {
    const selectedSite = websites.find(site => String(site.id) === String(selectedWafWebsiteId));
    const groupedRules = (wafSiteConfig?.default_rules || wafRules.default_rule_definitions || []).reduce((groups, rule) => {
      const category = rule.category || 'General';
      groups[category] = groups[category] || [];
      groups[category].push(rule);
      return groups;
    }, {});
    const siteBotNames = siteBotText.split(/[\n,;]+/).map(s => s.trim()).filter(Boolean);
    const siteBotUnique = new Set(siteBotNames.map(s => s.toLowerCase()));
    return <>
      <div className="section">
        <div className="section-title waf-site-header">
          <div>
            <h2>{wafSiteConfig?.domain || selectedSite?.domain || 'Website'}</h2>
            <p className="hint">{t('WAF rules, flood limits and blocked bots for this website.')}</p>
          </div>
          <div className="waf-site-header-actions">
            <select value={selectedWafWebsiteId} onChange={e => loadWebsiteWafConfig(e.target.value)}>
              {websites.map(site => <option key={site.id} value={site.id}>{site.domain}</option>)}
            </select>
            <button className="secondary-light" onClick={() => navigateToPage('waf')}><ArrowLeft size={14}/>{t('All websites')}</button>
          </div>
        </div>
        {/* One row per protection: what it does, its state, its switch. CRS
            is here and not in the list - whether a site carries it, and its
            memory, is for whoever manages this site to decide. */}
        <div className="waf-switches">
          <div className="waf-switch-row">
            <div className="waf-switch-text">
              <strong>WAF</strong>
              <span className="hint">{t('Blocks known bad paths.')}</span>
            </div>
            <span className={selectedSite?.waf_enabled ? 'badge ok' : 'badge'}>{selectedSite?.waf_enabled ? t('WAF on') : t('WAF off')}</span>
            <button
              className={selectedSite?.waf_enabled ? 'secondary' : ''}
              disabled={!selectedWafWebsiteId || !!loading}
              onClick={() => selectedSite && toggleWebsiteWaf(selectedSite)}
            ><Shield size={14}/>{selectedSite?.waf_enabled ? t('Turn WAF off') : t('Turn WAF on')}</button>
          </div>
          {wafSiteConfig && <div className="waf-switch-row">
            <div className="waf-switch-text">
              <strong>OWASP CRS</strong>
              <span className="hint">
                {t('Inspects each request for SQL injection, XSS and similar attacks. Uses about {n} MB of server RAM.', { n: crs?.rss_mb_per_site || 50 })}
                {wafSiteConfig.crs_enabled && !selectedSite?.waf_enabled ? ' ' + t('It loads when the WAF is on.') : ''}
                {wafSiteConfig.crs_enabled && selectedSite?.waf_enabled && wafSiteConfig.crs_mode === 'off' ? ' ' + t('CRS is off for the whole server, so nothing is loaded yet.') : ''}
                {wafSiteConfig?.crs_active && wafSiteConfig.crs_mode === 'detect' ? ' ' + t('Detect only: attacks are logged, nothing is blocked.') : ''}
                {wafSiteConfig?.crs_active && wafSiteConfig.may_edit_custom_rules ? ' ' + t('Add SecRuleRemoveById <id> to the custom rules below to excuse this site from one rule.') : ''}
              </span>
            </div>
            <span className={wafSiteConfig?.crs_active ? 'badge ok' : 'badge'}>{wafSiteConfig.crs_enabled ? t('CRS on') : t('CRS off')}</span>
            <button
              className={wafSiteConfig.crs_enabled ? 'secondary' : ''}
              disabled={!!loading || (!wafSiteConfig.crs_enabled && !selectedSite?.waf_enabled)}
              title={!wafSiteConfig.crs_enabled && !selectedSite?.waf_enabled ? t('Turn the WAF on first') : ''}
              onClick={() => toggleSiteCrs(wafSiteConfig)}
            ><Shield size={14}/>{wafSiteConfig.crs_enabled ? t('Turn CRS off') : t('Turn CRS on')}</button>
          </div>}
        </div>
      </div>

      {!wafSiteConfig && websites.length === 0 && <div className="section"><EmptyState icon={Globe} message={t('No websites yet.')} /></div>}

      {wafSiteConfig && <div className="section bot-block-panel">
        <div className="section-title">
          <div>
            <h2>{t('Blocked bots')}</h2>
            <p className="hint">{t('One name per line, matched anywhere in User-Agent. Matched literally, so')}{' '}<code>bingbot/2.0</code> will not also match <code>bingbotX2Y0</code>. Blocked requests get 403 before WAF and rate limiting run.</p>
          </div>
        </div>
        <textarea
          className="code-editor"
          value={siteBotText}
          onChange={e => setSiteBotText(e.target.value)}
          rows={10}
          spellCheck={false}
          placeholder={'AhrefsBot\nSemrushBot\nMJ12bot'}
        />
        <p className="hint">
          {`${siteBotUnique.size} bot(s)`}
          {siteBotNames.length !== siteBotUnique.size ? ` (${siteBotNames.length - siteBotUnique.size} duplicate(s) will be dropped)` : ''}
          {botBlocks?.max_bots ? ` - max ${botBlocks.max_bots}` : ''}
        </p>
        <div className="actions">
          <button disabled={!!loading} onClick={saveSiteBots}><Shield size={14}/>{t('Save blocked bots')}</button>
          <button className="secondary-light" disabled={!!loading || siteBotNames.length === 0} onClick={() => setSiteBotText('')}>{t('Clear list')}</button>
        </div>
      </div>}

      {wafSiteConfig && isAdmin && <div className="section http-flood-panel">
        <div className="section-title">
          <h2>{t('HTTP Flood')}</h2>
          <span className={httpFloodForm.http_flood_enabled ? 'badge ok' : 'badge'}>{httpFloodForm.http_flood_enabled ? 'Enabled' : 'Disabled'}</span>
        </div>
        <label className="schedule-toggle http-flood-toggle">
          <input type="checkbox" checked={!!httpFloodForm.http_flood_enabled} onChange={e => setHttpFloodForm(prev => ({ ...prev, http_flood_enabled: e.target.checked }))} />{t('Enabled')}</label>
        <div className="http-flood-grid">
          <label><span>{t('Requests')}</span><input type="number" min="1" max="100000" value={httpFloodForm.access_limit_requests} onChange={e => setHttpFloodForm(prev => ({ ...prev, access_limit_requests: e.target.value }))} /></label>
          <label><span>{t('Window (sec)')}</span><input type="number" min="1" max="3600" value={httpFloodForm.access_limit_window} onChange={e => setHttpFloodForm(prev => ({ ...prev, access_limit_window: e.target.value }))} /></label>
          <label><span>{t('Burst')}</span><input type="number" min="0" max="100000" value={httpFloodForm.access_limit_burst} onChange={e => setHttpFloodForm(prev => ({ ...prev, access_limit_burst: e.target.value }))} /></label>
          <label><span>{t('Connections/IP')}</span><input type="number" min="1" max="10000" value={httpFloodForm.connection_limit} onChange={e => setHttpFloodForm(prev => ({ ...prev, connection_limit: e.target.value }))} /></label>
          <button disabled={!!loading} onClick={saveWebsiteHttpFlood}><Shield size={14}/>{t('Save HTTP Flood')}</button>
        </div>
      </div>}

      {wafSiteConfig && <div className="section waf-rules-grid">
        <div className="waf-rule-panel">
          <div className="section-title"><h2>{t('Default rules')}</h2></div>
          <div className="waf-default-groups">
            {Object.entries(groupedRules).map(([category, rules]) => <div className="waf-rule-group" key={category}>
              <h3>{category}</h3>
              {rules.map(rule => <label className="waf-rule-toggle" key={rule.id}>
                <input type="checkbox" checked={!!rule.enabled} onChange={e => toggleWafDefaultRule(rule.id, e.target.checked)} />
                <span><strong>{rule.title}</strong><small>{rule.description}</small></span>
              </label>)}
            </div>)}
          </div>
        </div>
        <div className="waf-rule-panel">
          <div className="section-title"><h2>{t('Custom rules')}</h2></div>
          <textarea
            className="code-editor"
            value={wafCustomRules}
            onChange={e => setWafCustomRules(e.target.value)}
            rows={14}
            spellCheck={false}
            placeholder="SecRule ..."
            readOnly={wafSiteConfig.may_edit_custom_rules === false}
          />
          <p className="hint">
            {wafSiteConfig.may_edit_custom_rules === false
              ? 'Custom rules are arbitrary ModSecurity directives, so only an administrator can change them. Ask your provider if you need a rule added or excluded.'
              : `Saved into ${wafSiteConfig.rules_file}`}
          </p>
          <div className="actions"><button disabled={!!loading} onClick={saveWebsiteWafRules}>{t('Save website WAF rules')}</button></div>
        </div>
      </div>}
    </>;
  }

  function renderWafAccessLogs() {
    const rows = wafAccessLogs.items || [];
    const selectedSite = websites.find(site => String(site.id) === String(wafAccessLogFilters.websiteId));
    const entryLabel = wafAccessLogs.total >= 1000 ? `${(wafAccessLogs.total / 1000).toFixed(1)}k entries` : `${wafAccessLogs.total || 0} entries`;
    return <div className="section access-logs-">
      <div className="section-title access-logs-title">
        <div><h2>{t('Access Logs')}</h2><p className="hint">{t('Protected Nginx traffic across all websites.')}</p></div>
        <div className="access-log-icon-actions">
          <button className="secondary-light icon-button" disabled={!!loading} onClick={() => loadWafAccessLogs(wafAccessLogFilters, true)} aria-label={t('Refresh access logs')} title={t('Refresh access logs')}><RefreshCw size={15}/></button>
          <button className="secondary-light icon-button" onClick={() => selectedSite && window.open(websiteUrl(selectedSite), '_blank', 'noopener,noreferrer')} disabled={!selectedSite} aria-label={t('Open website')} title={t('Open website')}><ExternalLink size={15}/></button>
        </div>
      </div>
      <div className="access-log-panel">
        <div className="access-log-toolbar">
          <div className="access-log-toolbar-label"><strong>{t('Access Logs')}</strong><span>{entryLabel}</span></div>
          <button className="secondary-light" disabled={rows.length === 0} onClick={exportWafAccessLogs}><Download size={14}/>{t('Export')}</button>
          <button className="danger light" disabled={!!loading || websites.length === 0} onClick={clearWafAccessLogs}><Trash2 size={14}/>{t('Clear')}</button>
          <select value={wafAccessLogFilters.websiteId} onChange={e => updateWafAccessLogFilters({ websiteId: e.target.value }, true)}>
            <option value="">{t('All websites')}</option>
            {websites.map(site => <option key={site.id} value={site.id}>{site.domain}</option>)}
          </select>
          <select value={wafAccessLogFilters.verdict} onChange={e => updateWafAccessLogFilters({ verdict: e.target.value }, true)}>
            <option value="all">{t('All verdicts')}</option>
            <option value="block">{t('Blocked')}</option>
            <option value="allow">{t('Allowed')}</option>
            <option value="error">{t('Errors')}</option>
          </select>
          <input value={wafAccessLogFilters.query} onChange={e => updateWafAccessLogFilters({ query: e.target.value })} onKeyDown={e => { if (e.key === 'Enter') applyWafAccessLogFilters(); }} placeholder={t('Filter logs')} />
          <select value={wafAccessLogFilters.limit} onChange={e => updateWafAccessLogFilters({ limit: Number(e.target.value) }, true)}>
            <option value={50}>50 / page</option>
            <option value={100}>100 / page</option>
            <option value={200}>200 / page</option>
            <option value={500}>500 / page</option>
          </select>
          <select value={wafAccessLogFilters.refresh} onChange={e => updateWafAccessLogFilters({ refresh: Number(e.target.value) })}>
            <option value={0}>{t('Manual refresh')}</option>
            <option value={5}>{t('Refresh 5s')}</option>
            <option value={10}>{t('Refresh 10s')}</option>
            <option value={30}>{t('Refresh 30s')}</option>
          </select>
          <button disabled={!!loading} onClick={applyWafAccessLogFilters}><Search size={14}/>{t('Apply')}</button>
        </div>
        <div className="access-log-table-wrap">
          <table className="access-log-table">
            <thead>
              <tr>
                <th>{t('Verdict')}</th>
                <th>{t('Time')}</th>
                <th>{t('Site')}</th>
                <th>{t('Method')}</th>
                <th>{t('Path')}</th>
                <th>IP</th>
                <th>{t('Country')}</th>
                <th>{t('Reason')}</th>
                <th>{t('Status')}</th>
              </tr>
            </thead>
            <tbody>
              {rows.map(item => <tr key={item.id}>
                <td data-label={t('Verdict')}><span className={accessLogBadgeClass(item.verdict)}>{accessLogVerdictLabel(item.verdict)}</span></td>
                <td data-label={t('Time')}><span className="access-log-time">{formatAccessLogTime(item.timestamp)}</span><small>{item.duration_ms || 0} ms</small></td>
                <td data-label={t('Site')}><span className="access-log-site">{item.domain}</span></td>
                <td data-label={t('Method')}>{item.method || '-'}</td>
                <td data-label={t('Path')}><code>{item.path || '-'}</code></td>
                <td data-label="IP"><span className="access-log-ip">{item.ip || '-'}</span></td>
                <td data-label={t('Country')}>{accessLogCountryLabel(item)}</td>
                <td data-label={t('Reason')}>{item.reason || '-'}</td>
                <td data-label={t('Status')}>{item.status || '-'}</td>
              </tr>)}
            </tbody>
          </table>
          {rows.length === 0 && <EmptyState icon={FileText} message={t('No access log entries match these filters.')} />}
        </div>
        {(wafAccessLogs.missing || []).length > 0 && <p className="hint">{t('Missing log files:')} {wafAccessLogs.missing.join(', ')}</p>}
      </div>
    </div>;
  }
  if (page === 'waf-site') return renderWafSite();
  if (page === 'waf-logs' || page === 'access-logs') return renderWafAccessLogs();
  return renderWaf();
}
