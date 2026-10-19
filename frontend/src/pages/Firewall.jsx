import React, { useState, useEffect } from 'react';
import { Archive, ArchiveRestore, ArrowLeft, Ban, Bot, Boxes, Check, ChevronDown, Clock, Code2, Copy, Cpu, Database, Dices, ExternalLink, Eye, FileText, FolderOpen, Globe, HardDrive, Home, Image, KeyRound, Lock, LogIn, LogOut, MemoryStick, Menu, Moon, MoveRight, Network, Pencil, Save, Search, Server, Settings as SettingsIcon, Shield, Sun, Trash2, TerminalIcon, Users, X, RefreshCw, Plus, Download, Upload, Play, Square, RotateCcw, AlertCircle, Activity, BrickWall, Bug, LockKeyhole, PackageOpen, ScrollText, ShieldAlert, CheckCircle, Zap, Bell, Mail, Send, Cloud, Inbox, Forward, ShieldCheck } from 'lucide-react';
import { EmptyState } from '../components/EmptyState.jsx';
import { useAppContext } from '../contexts/AppContext.jsx';
import { t } from '../i18n.js';

export function FirewallPage({ request, isAdmin, addons }) {
  const { loading, setLoading, setError, setNotice } = useAppContext();
  const [f2bBanned, setF2bBanned] = useState({ items: [], total: 0, offset: 0, limit: 50 });
  const [fwDetail, setFwDetail] = useState(null);
  const [f2b, setF2b] = useState(null);
  const [fwFilter, setFwFilter] = useState('');
  const [fwAction, setFwAction] = useState('block');
  const [firewallStatus, setFirewallStatus] = useState(null);
  const [firewallPort, setFirewallPort] = useState('80');
  const [firewallProtocol, setFirewallProtocol] = useState('tcp');
  const [firewallAllowIp, setFirewallAllowIp] = useState('');
  const [firewallAllowPort, setFirewallAllowPort] = useState('');
  const [firewallAllowProtocol, setFirewallAllowProtocol] = useState('tcp');
  const [firewallBlockIp, setFirewallBlockIp] = useState('');
  const [firewallBlockPort, setFirewallBlockPort] = useState('');
  const [firewallBlockProtocol, setFirewallBlockProtocol] = useState('tcp');
  const [firewallDeleteNumber, setFirewallDeleteNumber] = useState('');
  const [firewallBlocklists, setFirewallBlocklists] = useState(null);
  const [firewallBlocklistUrl, setFirewallBlocklistUrl] = useState('');

  useEffect(() => {
    loadFirewall();
    loadFirewallBlocklists();
    loadFail2ban();
  }, []);

  async function loadFail2ban() {
    // 409 when the addon is not installed, which is an answer, not an error.
    const data = await request('/fail2ban/status', { silent: true });
    setF2b(data || null);
  }

  async function unbanAddress(ip) {
    const data = await request('/fail2ban/unban', { method: 'POST', body: JSON.stringify({ ip }) },
      `Unbanning ${ip}...`);
    if (data) {
      setF2bBanned({ items: data.items, total: data.total, offset: data.offset, limit: data.limit });
      setF2b(prev => (prev ? { ...prev, banned: data.total } : prev));
      setNotice(`${ip} unbanned.`);
    }
  }

  async function loadFirewall() {
    const data = await request('/firewall/status', {}, t('Loading firewall...'));
    if (data) setFirewallStatus(data);
  }

  async function submitFirewallRule() {
    if (fwAction === 'port') return openFirewallPort();
    if (fwAction === 'allow') return allowFirewallIp();
    return blockFirewallIp();
  }

  async function runFirewallAction(path, options = {}, label = 'Updating firewall...') {
    const data = await request(path, options, label);
    if (data) { setNotice((data.stdout || data.stderr || 'Firewall updated.').trim()); await loadFirewall(); }
  }

  async function enableFirewall() {
    if (!confirm(t('Enable the firewall now? SSH, the panel port and 80/443/465/587 stay open automatically.'))) return;
    await runFirewallAction('/firewall/enable', { method: 'POST' }, t('Enabling firewall...'));
  }

  async function disableFirewall() {
    if (!confirm(t('Disable the firewall? Every port will be reachable again.'))) return;
    await runFirewallAction('/firewall/disable', { method: 'POST' }, t('Disabling firewall...'));
  }

  async function reloadFirewall() { await runFirewallAction('/firewall/reload', { method: 'POST' }, t('Reloading firewall...')); }

  async function repairFirewall() { await runFirewallAction('/firewall/repair', { method: 'POST' }, t('Repairing the firewall...')); }

  async function openFirewallPort() { await runFirewallAction('/firewall/allow-port', { method: 'POST', body: JSON.stringify({ port: firewallPort, protocol: firewallProtocol }) }, t('Opening port...')); }

  async function allowFirewallIp() { await runFirewallAction('/firewall/allow-ip', { method: 'POST', body: JSON.stringify({ ip: firewallAllowIp, port: firewallAllowPort || null, protocol: firewallAllowProtocol }) }, t('Allowing IP...')); }

  async function blockFirewallIp() {
    if (!confirm(`Block ${firewallBlockIp || 'this IP'}?`)) return;
    await runFirewallAction('/firewall/block-ip', { method: 'POST', body: JSON.stringify({ ip: firewallBlockIp, port: firewallBlockPort || null, protocol: firewallBlockProtocol }) }, t('Blocking IP...'));
  }

  async function deleteFirewallRule(numberOverride = firewallDeleteNumber) {
    const ruleNumber = String(numberOverride || '').trim();
    if (!ruleNumber) return;
    if (!confirm(`Delete firewall rule #${ruleNumber}?`)) return;
    await runFirewallAction(`/firewall/rules/${encodeURIComponent(ruleNumber)}`, { method: 'DELETE' }, t('Deleting rule...'));
    setFirewallDeleteNumber('');
  }

  function parseFirewallBlocklistUrls(text) {
    const lines = String(text || '').split('\n');
    const urls = [];
    let inUrls = false;
    for (const raw of lines) {
      const line = raw.trim();
      if (line === 'URLs:') { inUrls = true; continue; }
      if (line === 'Networks:' || line === 'Timer:') break;
      if (inUrls && /^https?:\/\//i.test(line)) urls.push(line);
    }
    return urls;
  }

  async function loadFirewallBlocklists() {
    const data = await request('/firewall/blocklists', {}, t('Loading IP blocklists...'));
    if (data) setFirewallBlocklists(data);
  }

  
  function openFwDetail(which) {
    const next = fwDetail === which ? null : which;
    setFwDetail(next);
    setFwFilter('');
    if (next === 'banned') loadBannedPage(0);
  }

  async function loadBannedPage(offset = 0) {
    const data = await request(`/fail2ban/banned?limit=50&offset=${offset}`, { silent: true });
    if (data) setF2bBanned(data);
  }

  async function addFirewallBlocklistUrl() {
    const url = firewallBlocklistUrl.trim();
    if (!url) return;
    const data = await request('/firewall/blocklists', { method: 'POST', body: JSON.stringify({ url }) }, t('Adding IP blocklist URL...'));
    if (data) {
      setNotice((data.stdout || data.stderr || 'IP blocklist URL added.').trim());
      setFirewallBlocklistUrl('');
      await loadFirewallBlocklists();
    }
  }

  async function deleteFirewallBlocklistUrl(url) {
    if (!confirm(`Delete blocklist URL?\n${url}`)) return;
    const data = await request('/firewall/blocklists/delete', { method: 'POST', body: JSON.stringify({ url }) }, t('Deleting IP blocklist URL...'));
    if (data) {
      setNotice((data.stdout || data.stderr || 'IP blocklist URL removed.').trim());
      await loadFirewallBlocklists();
    }
  }

  async function updateFirewallBlocklistsNow() {
    const data = await request('/firewall/blocklists/update', { method: 'POST' }, t('Refreshing IP blocklists...'));
    if (data) {
      setNotice((data.stdout || data.stderr || 'IP blocklists refreshed.').trim());
      await loadFirewall();
      await loadFirewallBlocklists();
    }
  }

  function renderFirewall() {
    if (!isAdmin) return <div className="section"><h2>{t('Firewall')}</h2><p className="hint">{t('No permission.')}</p></div>;
    const firewallText = firewallStatus?.stdout || firewallStatus?.stderr || 'Press Refresh to load the status.';
    const blocklistText = firewallBlocklists?.stdout || firewallBlocklists?.stderr || 'Blocklist status not loaded yet.';
    const blocklistUrls = parseFirewallBlocklistUrls(blocklistText);
    const allRules = firewallStatus?.rules || [];
    const userRules = allRules.filter(rule => !rule.protected);
    const panelRules = allRules.filter(rule => rule.protected);
    const f2bInstalled = addons.items.find(item => item.slug === 'fail2ban')?.installed;
    // The helper prints `Status: enabled|disabled` as its first line. Read that
    // rather than pattern-matching the whole dump, which carries the word
    // "disabled" in other contexts too.
    const statusLine = (firewallText.split('\n').find(line => line.startsWith('Status:')) || '').toLowerCase();
    const enabled = statusLine.includes('enabled');
    const stateKnown = statusLine !== '';
    // "Chain active: no" is a firewall turned on whose rules are not in force
    // (UFW still in charge, or ipset missing): the dashboard and the alert
    // call that off, so the page must not call it on.
    const chainLine = (firewallText.split('\n').find(line => line.startsWith('Chain active:')) || '').toLowerCase();
    const notApplied = enabled && chainLine.includes('no');
    const keep = value => !fwFilter.trim() || String(value).toLowerCase().includes(fwFilter.trim().toLowerCase());
    const shownRules = userRules.filter(rule => keep(`${rule.id} ${rule.action} ${rule.to} ${rule.from}`));
    const shownUrls = blocklistUrls.filter(keep);
    const shownBanned = (f2bBanned.items || []).map(item => typeof item === 'string' ? item : (item?.ip || item?.address || '')).filter(Boolean).filter(keep);

    return <>
      <div className="section">
        <div className="section-title">
          <div>
            <h2>{t('Firewall')}</h2>
            <p className="hint">iptables + ipset. SSH, the panel port and 80/443/465/587 are always kept open.</p>
          </div>
          <div className="actions">
            <button className="secondary-light" disabled={!!loading} onClick={loadFirewall}><RefreshCw size={14}/>{t('Refresh')}</button>
            {/* One of these, never both: the other is not an action available now. */}
            {stateKnown && (enabled
              ? <button className="danger" disabled={!!loading} onClick={disableFirewall}>{t('Turn off')}</button>
              : <button disabled={!!loading} onClick={enableFirewall}><Shield size={14}/>{t('Turn on')}</button>)}
            {enabled && <button className="secondary" disabled={!!loading} onClick={reloadFirewall}>{t('Reload')}</button>}
          </div>
        </div>

        <div className="chip-row">
          <span className={notApplied ? 'badge bad' : enabled ? 'badge ok' : 'badge warn'}>{notApplied ? t('On, not applied') : enabled ? 'On' : 'Off'}</span>
          {panelRules.length > 0 && <span className="hint">protected ports: {panelRules.map(rule => rule.to).join(', ')}</span>}
        </div>

        {notApplied && <div className="firewall-not-applied">
          <p>{t('The firewall is turned on, but its rules are not in force: this server still filters with UFW, or iptables and ipset are missing. An update from an older release can leave it so.')}</p>
          <button disabled={!!loading} onClick={repairFirewall}><Shield size={14}/>{t('Repair the firewall')}</button>
        </div>}

        <div className="detail-tabs">
          <button className={fwDetail === 'rules' ? 'chip on' : 'chip'} disabled={!!loading}
            onClick={() => openFwDetail('rules')}>{t('Your rules')}{' '}<b>{userRules.length}</b></button>
          <button className={fwDetail === 'urls' ? 'chip on' : 'chip'} disabled={!!loading}
            onClick={() => openFwDetail('urls')}>{t('Blocklist URL')}{' '}<b>{blocklistUrls.length}</b></button>
          <button className={fwDetail === 'raw' ? 'chip on' : 'chip'} disabled={!!loading}
            onClick={() => openFwDetail('raw')}>{t('Raw status')}</button>
        </div>

        {fwDetail && <div className="detail-panel">
          {fwDetail !== 'raw' && <div className="detail-head">
            <input id="fw-filter" value={fwFilter} onChange={e => setFwFilter(e.target.value)}
              placeholder={t('Filter this list...')} aria-label={t('Filter list')} />
            <button className="secondary-light" onClick={() => setFwDetail(null)}><X size={14}/>{t('Close')}</button>
          </div>}

          {fwDetail === 'rules' && <div className="detail-body">
            {shownRules.length === 0 && <p className="hint">{userRules.length === 0
              ? 'No rules yet. Only the protected ports are open.'
              : 'No rules match that filter.'}</p>}
            {shownRules.map(rule => <div className="firewall-rule" key={rule.id}>
              <span>
                <strong>#{rule.id}</strong>{' '}
                <span className={rule.action === 'DENY' ? 'badge danger' : 'badge ok'}>{rule.action}</span>{' '}
                {rule.to} from {rule.from}
              </span>
              <div className="firewall-rule-actions">
                <button className="danger" disabled={!!loading} onClick={() => deleteFirewallRule(rule.id)}><Trash2 size={14}/>{t('Delete')}</button>
              </div>
            </div>)}
          </div>}

          {fwDetail === 'urls' && <div className="detail-body">
            <div className="firewall-form firewall-blocklist-form">
              <label><span>TXT URL</span><input id="fw-blocklist-url" value={firewallBlocklistUrl}
                onChange={e => setFirewallBlocklistUrl(e.target.value)} placeholder="https://example.com/blocklist.txt" /></label>
              <button disabled={!!loading || !firewallBlocklistUrl.trim()} onClick={addFirewallBlocklistUrl}><Plus size={14}/>{t('Add')}</button>
              <button className="secondary-light" disabled={!!loading} onClick={updateFirewallBlocklistsNow}><RefreshCw size={14}/>{t('Update now')}</button>
            </div>
            <p className="hint">{t('Fetched daily at 01:00 into an ipset, so even a million-entry list costs one kernel lookup per packet.')}</p>
            {shownUrls.length === 0 && <p className="hint">{t('No URLs yet.')}</p>}
            {shownUrls.map(url => <div className="firewall-rule" key={url}>
              <span className="wrap-any">{url}</span>
              <div className="firewall-rule-actions"><button className="danger" disabled={!!loading} onClick={() => deleteFirewallBlocklistUrl(url)}><Trash2 size={14}/>{t('Delete')}</button></div>
            </div>)}
          </div>}

          {fwDetail === 'raw' && <div className="detail-body">
            <div className="detail-head">
              <strong>{t('Firewall status')}</strong>
              <button className="secondary-light" onClick={() => setFwDetail(null)}><X size={14}/>{t('Close')}</button>
            </div>
            <pre>{firewallText}</pre>
            <strong>{t('Blocklist status')}</strong>
            <pre>{blocklistText}</pre>
            <div className="firewall-delete-inline">
              <label><span>{t('Delete rule #')}</span><input id="fw-delete-number" value={firewallDeleteNumber}
                onChange={e => setFirewallDeleteNumber(e.target.value)} placeholder="12" inputMode="numeric" /></label>
              <button className="danger" disabled={!!loading || !firewallDeleteNumber} onClick={() => deleteFirewallRule()}>{t('Delete')}</button>
            </div>
          </div>}
        </div>}

        <div className="firewall-form rule-form">
          <label><span>{t('Action')}</span>
            <select id="fw-action" value={fwAction} onChange={e => setFwAction(e.target.value)}>
              <option value="block">{t('Block IP')}</option>
              <option value="allow">{t('Allow IP')}</option>
              <option value="port">{t('Open port')}</option>
            </select>
          </label>
          {fwAction === 'block' && <label><span>{t('IP / CIDR')}</span><input id="fw-block-ip" value={firewallBlockIp}
            onChange={e => setFirewallBlockIp(e.target.value)} placeholder="5.6.7.8" /></label>}
          {fwAction === 'allow' && <label><span>{t('IP / CIDR')}</span><input id="fw-allow-ip" value={firewallAllowIp}
            onChange={e => setFirewallAllowIp(e.target.value)} placeholder="1.2.3.4" /></label>}
          <label><span>Port{fwAction === 'port' ? '' : ' (optional)'}</span>
            {fwAction === 'block' && <input id="fw-block-port" value={firewallBlockPort} onChange={e => setFirewallBlockPort(e.target.value)} placeholder={t('All ports')} inputMode="numeric" />}
            {fwAction === 'allow' && <input id="fw-allow-port" value={firewallAllowPort} onChange={e => setFirewallAllowPort(e.target.value)} placeholder="22" inputMode="numeric" />}
            {fwAction === 'port' && <input id="fw-port" value={firewallPort} onChange={e => setFirewallPort(e.target.value)} placeholder="80" inputMode="numeric" />}
          </label>
          <label><span>{t('Protocol')}</span>
            {fwAction === 'block' && <select id="fw-block-proto" value={firewallBlockProtocol} onChange={e => setFirewallBlockProtocol(e.target.value)}><option value="tcp">TCP</option><option value="udp">UDP</option></select>}
            {fwAction === 'allow' && <select id="fw-allow-proto" value={firewallAllowProtocol} onChange={e => setFirewallAllowProtocol(e.target.value)}><option value="tcp">TCP</option><option value="udp">UDP</option></select>}
            {fwAction === 'port' && <select id="fw-proto" value={firewallProtocol} onChange={e => setFirewallProtocol(e.target.value)}><option value="tcp">TCP</option><option value="udp">UDP</option></select>}
          </label>
          <button className={fwAction === 'block' ? 'danger' : ''} disabled={!!loading
            || (fwAction === 'block' && !firewallBlockIp)
            || (fwAction === 'allow' && !firewallAllowIp)
            || (fwAction === 'port' && !firewallPort)} onClick={submitFirewallRule}>
            {fwAction === 'block' ? 'Block' : fwAction === 'allow' ? 'Allow' : 'Open port'}
          </button>
        </div>
      </div>

      {f2bInstalled && <div className="section">
        <div className="section-title">
          <div>
            <h2>Fail2ban</h2>
            <p className="hint">{t('Five failed attempts within an hour bans an address for an hour, and longer each time it comes back, up to a week. The server never bans its own addresses.')}</p>
          </div>
          <button className="secondary-light" disabled={!!loading} onClick={loadFail2ban}><RefreshCw size={14}/>{t('Refresh')}</button>
        </div>
        {!f2b && <p className="hint">{t('Status not loaded. Press Refresh.')}</p>}
        {f2b && <>
          {f2b.warning && <p className="hint alarm">{f2b.warning}</p>}
          <div className="chip-row">
            <span className={f2b.running ? 'badge ok' : 'badge warn'}>{f2b.running ? 'Running' : 'Not running'}</span>
            <span className={f2b.bans_reach_kernel ? 'badge ok' : 'badge warn'}>{f2b.bans_reach_kernel ? 'Bans take effect' : 'Bans NOT reaching iptables'}</span>
            <span className={f2b.filter_sees_journal ? 'badge ok' : 'badge warn'}>{f2b.filter_sees_journal ? 'Reading the log' : 'Seeing NO log'}</span>
            <span className="hint">{f2b.ssh_unit || '—'} · {f2b.banaction || '—'} · {t('{n} failures seen', { n: f2b.total_failed ?? 0 })}</span>
          </div>
          <div className="detail-tabs">
            <button className={fwDetail === 'banned' ? 'chip on' : 'chip'} disabled={!!loading}
              onClick={() => openFwDetail('banned')}>{t('Banned addresses')}{' '}<b>{f2b.banned ?? 0}</b></button>
          </div>
          {fwDetail === 'banned' && <div className="detail-panel">
            <div className="detail-head">
              <input id="f2b-filter" value={fwFilter} onChange={e => setFwFilter(e.target.value)}
                placeholder={t('Filter by address...')} aria-label={t('Filter banned addresses')} />
              <button className="secondary-light" onClick={() => setFwDetail(null)}><X size={14}/>{t('Close')}</button>
            </div>
            <div className="detail-body">
              {shownBanned.length === 0 && <p className="hint">{f2bBanned.total === 0
                ? 'No addresses are banned right now.' : 'No addresses match that filter.'}</p>}
              {shownBanned.map(ip => <div className="firewall-rule" key={ip}>
                <span><code>{ip}</code></span>
                <div className="firewall-rule-actions">
                  <button className="danger" disabled={!!loading} onClick={() => unbanAddress(ip)}>{t('Unban')}</button>
                </div>
              </div>)}
            </div>
            {f2bBanned.total > f2bBanned.limit && <div className="detail-foot">
              <button className="secondary-light" disabled={!!loading || f2bBanned.offset === 0}
                onClick={() => loadBannedPage(Math.max(0, f2bBanned.offset - f2bBanned.limit))}>{t('Previous')}</button>
              <span className="hint">{f2bBanned.offset + 1}–{Math.min(f2bBanned.offset + f2bBanned.limit, f2bBanned.total)} trong {f2bBanned.total}</span>
              <button className="secondary-light" disabled={!!loading || f2bBanned.offset + f2bBanned.limit >= f2bBanned.total}
                onClick={() => loadBannedPage(f2bBanned.offset + f2bBanned.limit)}>{t('Next')}</button>
            </div>}
          </div>}
        </>}
      </div>}
    </>;
  }
  return renderFirewall();
}
