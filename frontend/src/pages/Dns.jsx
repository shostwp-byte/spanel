import React, { useState, useEffect } from 'react';
import { Archive, ArchiveRestore, ArrowLeft, Ban, Bot, Boxes, Check, ChevronDown, Clock, Code2, Copy, Cpu, Database, Dices, ExternalLink, Eye, FileText, FolderOpen, Globe, HardDrive, Home, Image, KeyRound, Lock, LogIn, LogOut, MemoryStick, Menu, Moon, MoveRight, Network, Pencil, Save, Search, Server, Settings as SettingsIcon, Shield, Sun, Trash2, TerminalIcon, Users, X, RefreshCw, Plus, Download, Upload, Play, Square, RotateCcw, AlertCircle, Activity, BrickWall, Bug, LockKeyhole, PackageOpen, ScrollText, ShieldAlert, CheckCircle, Zap, Bell, Mail, Send, Cloud, Inbox, Forward, ShieldCheck } from 'lucide-react';
import { EmptyState } from '../components/EmptyState.jsx';
import { useAppContext } from '../contexts/AppContext.jsx';
import { t } from '../i18n.js';

export function DnsPage({ request, isAdmin, users, emptyDnsRecord, renderCopyBlock }) {
  const {
    loading, setLoading, setError, setNotice,
    page, navigateToPage, copyText,
    dnsPath, dnsTtlLabel, renderDnsRecordEditor,
    DNS_TYPES, DNS_TTLS, DNS_PLACEHOLDERS,
  } = useAppContext();
  
  const [dnsNewZone, setDnsNewZone] = useState({ name: '', owner_id: '' });
  const [dnsRecordForm, setDnsRecordForm] = useState(() => emptyDnsRecord());
  const [dnsRecordEdit, setDnsRecordEdit] = useState(null);
  const [dnsInfo, setDnsInfo] = useState(null);
  const [dnsTab, setDnsTab] = useState('zones');
  const [dnsZones, setDnsZones] = useState(null);
  const [dnsQuery, setDnsQuery] = useState('');
  const [dnsPage, setDnsPage] = useState(1);
  const [dnsZone, setDnsZone] = useState(null);
  const [dnsDelegation, setDnsDelegation] = useState(null);
  const [dnsDnssec, setDnsDnssec] = useState(null);
  const [dnsRecordFilter, setDnsRecordFilter] = useState('');
  const [dnsSettings, setDnsSettings] = useState(null);

  useEffect(() => { loadDnsInfo(); loadDnsZones(); }, []);
  useEffect(() => { if (dnsTab === 'settings') loadDnsSettings(); }, [dnsTab]);
  useEffect(() => { if (dnsTab === 'zones') loadDnsZones(); }, [dnsQuery, dnsPage]);

  async function loadDnsInfo() {
    const data = await request('/dns/overview', { silent: true });
    if (data) setDnsInfo(data);
    return data;
  }

  async function loadDnsZones(page = dnsPage) {
    const params = new URLSearchParams({ page: String(page), per_page: '50' });
    if (dnsQuery.trim()) params.set('q', dnsQuery.trim());
    const data = await request(`/dns/zones?${params}`, { silent: true });
    if (data) setDnsZones(data);
  }

  async function loadDnsSettings() {
    const data = await request('/dns/settings', { silent: true });
    if (data) setDnsSettings({ ...data, ns1: data.nameservers?.[0] || '', ns2: data.nameservers?.[1] || '' });
  }

  async function openDnsZone(zone) {
    if (page !== 'dns') navigateToPage('dns');
    setDnsTab('zones');
    setDnsZone({ zone, records: null });
    setDnsDelegation(null);
    setDnsDnssec(null);
    setDnsRecordEdit(null);
    setDnsRecordFilter('');
    setDnsRecordForm(emptyDnsRecord());
    const data = await request(`${dnsPath(zone)}/records`);
    if (!data) { setDnsZone(null); return; }
    setDnsZone({ zone, records: data.records || [] });
    const [check, sec] = await Promise.all([
      request(`${dnsPath(zone)}/delegation`, { silent: true }),
      request(`${dnsPath(zone)}/dnssec`, { silent: true }),
    ]);
    setDnsDelegation(check || { status: 'unknown', expected: [], found: [] });
    setDnsDnssec(sec || { enabled: false, ds_records: [], dnskey_records: [] });
  }

  async function toggleZoneDnssec(enable) {
    if (!dnsZone?.zone) return;
    const zName = dnsZone.zone.name || dnsZone.zone;
    if (!enable && !window.confirm(`Bạn có chắc chắn muốn tắt DNSSEC cho tên miền ${zName}? Hãy đảm bảo bạn đã xóa bản ghi DS tại nhà đăng ký tên miền (Registrar) trước khi tắt để tránh lỗi phân giải DNS.`)) {
      return;
    }
    const endpoint = enable ? `${dnsPath(dnsZone.zone)}/dnssec/enable` : `${dnsPath(dnsZone.zone)}/dnssec/disable`;
    const data = await request(endpoint, { method: 'POST' }, enable ? 'Đang khởi tạo khóa CSK & ký số DNSSEC...' : 'Đang tắt DNSSEC...');
    if (data) {
      setDnsDnssec(data);
      setNotice(enable ? `Đã kích hoạt 1-Click DNSSEC (ECDSAP256SHA256) cho ${zName}.` : `Đã tắt DNSSEC cho ${zName}.`);
    }
  }

  function dnsRecordBody(form) {
    const withPriority = ['MX', 'SRV'].includes(form.type);
    return {
      name: String(form.name || '').trim() || '@',
      type: form.type,
      ttl: Number(form.ttl) || Number(dnsInfo?.default_ttl) || 3600,
      content: String(form.value ?? '').trim(),
      priority: withPriority ? (String(form.priority ?? '').trim() === '' ? 10 : Number(form.priority)) : null,
    };
  }

  function dnsRecordKey(record) {
    return { name: record.name, type: record.type, ttl: Number(record.ttl) || 3600, content: record.content, priority: record.priority ?? null };
  }

  function applyDnsRecords(data) {
    setDnsZone(prev => (prev ? { ...prev, records: data.records || [] } : prev));
  }

  async function addDnsRecord() {
    const data = await request(`${dnsPath(dnsZone.zone)}/records`, { method: 'POST', body: JSON.stringify(dnsRecordBody(dnsRecordForm)) }, t('Saving the record...'));
    if (!data) return;
    applyDnsRecords(data);
    setDnsRecordForm(prev => ({ ...prev, name: '', value: '', priority: '' }));
    setNotice(t('Record added.'));
  }

  async function saveDnsRecordEdit() {
    const { old, form } = dnsRecordEdit;
    const body = { original: dnsRecordKey(old), record: dnsRecordBody(form) };
    const data = await request(`${dnsPath(dnsZone.zone)}/records`, { method: 'PUT', body: JSON.stringify(body) }, t('Saving the record...'));
    if (!data) return;
    applyDnsRecords(data);
    setDnsRecordEdit(null);
    setNotice(t('Record saved.'));
  }

  async function deleteDnsRecord(record) {
    const question = record.mail
      ? t('The Email addon keeps this record for {domain} and writes it again when the email settings of {domain} change. Delete the {type} record of {name} anyway?', { domain: record.mail, type: record.type, name: record.name })
      : t('Delete the {type} record of {name}: {value}?', { type: record.type, name: record.name, value: record.value });
    if (!confirm(question)) return;
    const data = await request(`${dnsPath(dnsZone.zone)}/records/delete`, { method: 'POST', body: JSON.stringify(dnsRecordKey(record)) }, t('Deleting the record...'));
    if (!data) return;
    applyDnsRecords(data);
    if (dnsRecordEdit?.old?.content === record.content) setDnsRecordEdit(null);
    setNotice(t('Record deleted.'));
  }

  async function restoreDnsDefaults() {
    if (!confirm(t('Put back the records the panel manages for {zone}: nameservers, websites and email. Records you added are kept.', { zone: dnsZone.zone.name }))) return;
    const data = await request(`${dnsPath(dnsZone.zone)}/defaults`, { method: 'POST' }, t("Restoring the panel's records..."));
    if (!data) return;
    applyDnsRecords(data);
    setNotice(t("The panel's records are back in {zone}.", { zone: dnsZone.zone.name }));
  }

  async function deleteDnsZone(zone) {
    const typed = prompt(t('Every record of {zone} is deleted, and the domain stops resolving once its nameservers point here. Type the domain name to confirm.', { zone: zone.name }));
    if (typed === null) return;
    if (typed.trim().toLowerCase().replace(/\.$/, '') !== zone.name) { setError(t('The name did not match; nothing was deleted.')); return; }
    const data = await request(dnsPath(zone), { method: 'DELETE' }, t('Deleting the zone...'));
    if (!data) return;
    setNotice(t('Zone {zone} deleted.', { zone: zone.name }));
    setDnsZone(null);
    loadDnsZones();
  }

  async function addDnsZone() {
    const body = { name: dnsNewZone.name.trim(), owner_id: dnsNewZone.owner_id ? Number(dnsNewZone.owner_id) : null };
    const data = await request('/dns/zones', { method: 'POST', body: JSON.stringify(body) }, t('Saving...'));
    if (!data) return;
    setDnsNewZone({ name: '', owner_id: '' });
    loadDnsZones();
    openDnsZone({ name: data.zone });
  }

  async function syncDnsZones() {
    const data = await request('/dns/sync', { method: 'POST' }, t('Syncing...'));
    if (!data) return;
    setNotice(t('Every domain on the server has its DNS zone. New zones: {zones}, new records: {records}, owners corrected: {owners}.',
      { zones: data.zones.length, records: data.records.length, owners: data.owners.length }));
    loadDnsZones();
  }

  async function saveDnsSettings() {
    const f = dnsSettings;
    const body = {
      nameservers: [f.ns1, f.ns2].map(name => String(name || '').trim()).filter(Boolean),
      zone_ip: String(f.zone_ip || '').trim(),
      ttl: Number(f.ttl) || 3600,
      auto_zone: !!f.auto_zone,
      template: f.template || '',
    };
    const data = await request('/dns/settings', { method: 'PUT', body: JSON.stringify(body) }, t('Saving DNS settings...'));
    if (!data) return;
    setNotice(t('DNS settings saved.'));
    loadDnsSettings();
    loadDnsInfo();
    loadDnsZones();
  }

  function dnsTypeHint(type) {
    const ip4 = dnsInfo?.addresses?.ipv4?.[0];
    const ip6 = dnsInfo?.addresses?.ipv6?.[0];
    return {
      A: ip4 ? t('The IPv4 address the name points to. This server: {ip}.', { ip: ip4 }) : t('The IPv4 address the name points to.'),
      AAAA: ip6 ? t('The IPv6 address the name points to. This server: {ip}.', { ip: ip6 }) : t('The IPv6 address the name points to.'),
      CNAME: t('Makes the name an alias of another host. A name with a CNAME can have no other record.'),
      MX: t('A mail server for the domain; the lowest priority is tried first.'),
      TXT: t('Text such as SPF, DKIM or a site verification. A long value is split into strings for you.'),
      NS: t('Hands a subdomain to other nameservers.'),
      SRV: t('Where a service runs: weight port target, with the priority in its own field. The name is like _sip._tcp.'),
      CAA: t('Which certificate authorities may issue for the domain, for example 0 issue letsencrypt.org.'),
    }[type] || '';
  }

  function renderDnsRecordFields(form, set, { lockType = false } = {}) {
    const hasPriority = ['MX', 'SRV'].includes(form.type);
    const current = Number(form.ttl) || 0;
    const ttls = !current || DNS_TTLS.includes(current) ? DNS_TTLS : [...DNS_TTLS, current].sort((a, b) => a - b);
    return <div className={"dns-record-fields"}>
      <label className="field"><span className="field-label">{t('Type')}</span>
        <select value={form.type} disabled={lockType} onChange={e => set({ type: e.target.value })}>
          {DNS_TYPES.map(type => <option key={type} value={type}>{type}</option>)}
        </select></label>
      <label className="field"><span className="field-label">{t('Name')}</span>
        <input value={form.name} placeholder="@" spellCheck={false} autoCapitalize="off" onChange={e => set({ name: e.target.value })} /></label>
      {hasPriority && <label className="field"><span className="field-label">{t('Priority')}</span>
        <input type="number" min="0" max="65535" value={form.priority} placeholder="10" onChange={e => set({ priority: e.target.value })} /></label>}
      <label className="field dns-value-field"><span className="field-label">{t('Value')}</span>
        {form.type === 'TXT'
          ? <textarea rows={2} value={form.value} placeholder={DNS_PLACEHOLDERS.TXT} spellCheck={false} onChange={e => set({ value: e.target.value.replace(/[\r\n]+/g, ' ') })} />
          : <input value={form.value} placeholder={DNS_PLACEHOLDERS[form.type]} spellCheck={false} autoCapitalize="off" onChange={e => set({ value: e.target.value })} />}</label>
      <label className="field"><span className="field-label">TTL</span>
        <select value={form.ttl} onChange={e => set({ ttl: e.target.value })}>
          <option value="">{t('Default ({ttl})', { ttl: dnsTtlLabel(dnsInfo?.default_ttl || 3600) })}</option>
          {ttls.map(ttl => <option key={ttl} value={String(ttl)}>{dnsTtlLabel(ttl)}</option>)}
        </select></label>
    </div>;
  }

  function renderDnsNameservers() {
    const names = dnsInfo?.nameservers || [];
    return <div className="dns-ns-line">
      <span className="dns-ns-label"><Network size={14}/> {t('Nameservers')}</span>
      {names.length > 0
        ? <>
          {names.map(name => <code key={name}>{name}</code>)}
          <button type="button" className="mini secondary-light icon-only" aria-label={t('Copy')} title={t('Copy')}
            onClick={() => copyText(names.join('\n'), t('Copied.'))}><Copy size={13}/></button>
        </>
        : <span className="hint">{isAdmin ? t('Not set yet: open Settings.') : t('The administrator has not set the nameservers yet.')}</span>}
    </div>;
  }

  function renderDnsPager() {
    const list = dnsZones;
    const pages = Math.max(1, Math.ceil((list?.total || 0) / (list?.per_page || 50)));
    if (pages <= 1) return null;
    return <div className="firewall-ip-pager">
      <button className="mini secondary-light" disabled={dnsPage <= 1} onClick={() => setDnsPage(p => Math.max(1, p - 1))}>{t('Previous')}</button>
      <span className="hint">{t('Page {page} of {pages}', { page: dnsPage, pages })}</span>
      <button className="mini secondary-light" disabled={dnsPage >= pages} onClick={() => setDnsPage(p => p + 1)}>{t('Next')}</button>
    </div>;
  }

  function renderDnsZones() {
    const list = dnsZones;
    const items = list?.items || [];
    return <div className="mail-tab">
      {renderDnsNameservers()}
      <form className="mail-search dns-search" onSubmit={e => { e.preventDefault(); setDnsPage(1); loadDnsZones(1); }}>
        <input value={dnsQuery} placeholder={t('Search domains')} aria-label={t('Search domains')} onChange={e => setDnsQuery(e.target.value)} />
        <button type="submit" className="secondary-light icon-only" aria-label={t('Search')} title={t('Search')}><Search size={14}/></button>
      </form>
      {list === null && <p className="hint">{t('Loading…')}</p>}
      {list && items.length === 0 && <EmptyState icon={Network} message={dnsQuery.trim() ? 'No domain matches the search.' : 'No domains yet.'} />}
      {items.length > 0 && <div className="table">
        {items.map(zone => <div className="row dns-zone-row" key={zone.name}>
          <span className="mail-row-name">
            <strong>{zone.name}</strong>
            <small>{[isAdmin && zone.owner ? `${t('Account')}: ${zone.owner}` : '',
              zone.created_at ? `${t('Added')} ${new Date(zone.created_at).toLocaleDateString()}` : '',
              zone.on_panel === false ? t('No longer on the panel') : ''].filter(Boolean).join(' · ')}</small>
          </span>
          <span className="row-actions">
            <button className="mini secondary-light" disabled={!!loading} onClick={() => openDnsZone(zone)}><Pencil size={13}/> {t('Records')}</button>
          </span>
        </div>)}
      </div>}
      {renderDnsPager()}
      {isAdmin && <div className="create-inline dns-new-zone">
        <div className="create-inline-head"><strong>{t('A zone for a domain that is not on the panel')}</strong></div>
        <div className="mail-create-grid">
          <label className="field"><span className="field-label">{t('Domain')}</span>
            <input value={dnsNewZone.name} placeholder="example.com" spellCheck={false} onChange={e => setDnsNewZone(prev => ({ ...prev, name: e.target.value.trim().toLowerCase() }))} /></label>
          <label className="field"><span className="field-label">{t('Account')}</span>
            <select value={dnsNewZone.owner_id} onChange={e => setDnsNewZone(prev => ({ ...prev, owner_id: e.target.value }))}>
              <option value="">{t('No owner')}</option>
              {users.map(user => <option key={user.id} value={user.id}>{user.username}</option>)}
            </select></label>
          <button disabled={!dnsNewZone.name.trim() || !!loading} onClick={addDnsZone}><Plus size={14}/> {t('Add zone')}</button>
        </div>
      </div>}
    </div>;
  }

  function renderDnsSettings() {
    const f = dnsSettings;
    const set = patch => setDnsSettings(prev => ({ ...prev, ...patch }));
    const server = f?.server || {};
    const serving = server.running && server.api;
    const address = [dnsInfo?.addresses?.ipv4?.[0], dnsInfo?.addresses?.ipv6?.[0]].filter(Boolean).join(' / ');
    const ttl = Number(f?.ttl) || 3600;
    const ttls = DNS_TTLS.includes(ttl) ? DNS_TTLS : [...DNS_TTLS, ttl].sort((a, b) => a - b);
    return <div className="section dns-page">
      <div className="section-title">
        <div className="waf-detail-title">
          <button className="secondary-light" onClick={() => setDnsTab('zones')}><ArrowLeft size={14}/> {t('DNS Manager')}</button>
          <div><h2>{t('DNS settings')}</h2>
            <p className="hint">{t('The nameservers and default TTL of every zone on this server, and what a new zone holds.')}</p></div>
        </div>
      </div>
      {!f ? <p className="hint">{t('Loading…')}</p> : <div className="create-inline">
        <div className="dns-server">
          <span className={`badge ${serving ? 'ok' : 'bad'}`}>{serving ? t('PowerDNS is running') : t('PowerDNS is not running')}</span>
          <span className={`badge ${server.port_open ? 'ok' : 'warn'}`}>{server.port_open ? t('Port 53 open') : t('Port 53 closed')}</span>
          {server.listen?.length > 0 && <span className="hint">{t('Listening on')} {server.listen.join(', ')}</span>}
        </div>
        <div className="mail-settings-grid">
          <label className="field"><span className="field-label">{t('Nameserver 1')}</span>
            <input value={f.ns1} placeholder="ns1.example.com" spellCheck={false} onChange={e => set({ ns1: e.target.value })} /></label>
          <label className="field"><span className="field-label">{t('Nameserver 2')}</span>
            <input value={f.ns2} placeholder="ns2.example.com" spellCheck={false} onChange={e => set({ ns2: e.target.value })} /></label>
          <label className="field"><span className="field-label">{t('IP address for new zones')}</span>
            <input value={f.zone_ip || ''} list="dns-server-ipv4" spellCheck={false} onChange={e => set({ zone_ip: e.target.value })} />
            <datalist id="dns-server-ipv4">{(f.server_ipv4 || []).map(ip => <option key={ip} value={ip} />)}</datalist></label>
          <label className="field"><span className="field-label">{t('Default TTL')}</span>
            <select value={String(ttl)} onChange={e => set({ ttl: e.target.value })}>
              {ttls.map(value => <option key={value} value={String(value)}>{dnsTtlLabel(value)}</option>)}
            </select></label>
        </div>
        <label className="check-line"><input type="checkbox" checked={!!f.auto_zone} onChange={e => set({ auto_zone: e.target.checked })} /> {t('Give every domain on the server a DNS zone')}</label>
        <label className="field dns-template"><span className="field-label">{t('Records for new zones')}</span>
          <textarea rows={6} value={f.template || ''} spellCheck={false} onChange={e => set({ template: e.target.value })} /></label>
        <p className="hint dns-template-hint">{t('One record per line: name, type, then the value; an MX value starts with its priority. {ip} is the address for new zones, {domain} the zone, and {spf} the SPF record:')} <code>{f.spf}</code>. {t('Zones that already exist are not changed.')}</p>
        <p className="hint">{t('A changed nameserver is written into the NS and SOA records of every zone. Register both names as glue (child nameserver) records at the registrar of their domain, pointing at this server.')} {address && t('This server: {address}.', { address })}</p>
        <div className="actions">
          <button type="button" disabled={!String(f.ns1 || '').trim() || !String(f.ns2 || '').trim() || !!loading} onClick={saveDnsSettings}><Save size={14}/> {t('Save settings')}</button>
          <button type="button" className="secondary-light" disabled={!!loading || !f.auto_zone} onClick={syncDnsZones}><RefreshCw size={14}/> {t('Sync zones now')}</button>
        </div>
      </div>}
    </div>;
  }

  function renderDnsZone() {
    const { zone, records } = dnsZone;
    const delegation = dnsDelegation;
    const statusLabel = { ok: t('Served from here'), missing: t('Not delegated'), different: t('Other nameservers'), unknown: t('Not checked') };
    const statusClass = { ok: 'ok', missing: 'bad', different: 'warn', unknown: '' };
    const term = dnsRecordFilter.trim().toLowerCase();
    const shown = (records || []).filter(r => !term || `${r.name} ${r.type} ${r.value}`.toLowerCase().includes(term));
    const form = dnsRecordForm;
    const edit = dnsRecordEdit;
    const isEditing = record => edit && edit.old.name === record.name && edit.old.type === record.type && edit.old.content === record.content;
    const delegationText = !delegation ? t('Checking the nameservers…')
      : delegation.status === 'ok' ? t('{zone} is answered by this server.', { zone: zone.name })
      : delegation.status === 'different' ? t('{zone} uses other nameservers now ({found}). Set {expected} at its registrar.', { zone: zone.name, found: delegation.found.join(', '), expected: delegation.expected.join(', ') })
      : delegation.status === 'missing' ? t('Set {expected} as the nameservers of {zone} at its registrar. Until then these records are not used.', { expected: delegation.expected.join(', '), zone: zone.name })
      : t('The nameservers of {zone} could not be looked up.', { zone: zone.name });
    return <div className="section dns-zone-page">
      <div className="section-title">
        <div className="waf-detail-title">
          <button className="secondary-light" onClick={() => { setDnsZone(null); loadDnsZones(); }}><ArrowLeft size={14}/> {t('DNS Manager')}</button>
          <div><h2>{zone.name}</h2>
            <p className="hint">{[isAdmin && zone.owner ? `${t('Account')}: ${zone.owner}` : '', records ? t('{n} records', { n: records.length }) : ''].filter(Boolean).join(' · ')}</p></div>
        </div>
        <div className="actions">
          <button className="secondary-light" disabled={!!loading} onClick={() => openDnsZone(zone)}><RefreshCw size={14}/> {t('Refresh')}</button>
          <button className="secondary-light" disabled={!!loading || records === null} onClick={restoreDnsDefaults}><RotateCcw size={14}/> {t('Restore panel records')}</button>
          {isAdmin && zone.on_panel === false && <button className="danger" disabled={!!loading} onClick={() => deleteDnsZone(zone)}><Trash2 size={14}/> {t('Delete zone')}</button>}
        </div>
      </div>
      <div className="dns-delegation">
        <span className={`badge ${statusClass[delegation?.status] || ''}`}>{delegation ? statusLabel[delegation.status] || delegation.status : '…'}</span>
        <span className="hint">{delegationText}</span>
      </div>
      <div className="create-inline" style={{ marginBottom: '14px' }}>
        <div className="create-inline-head" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '10px' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap' }}>
            <ShieldCheck size={16} style={{ color: dnsDnssec?.enabled ? '#10b981' : 'var(--text-muted)' }} />
            <strong>1-Click DNSSEC (Bảo mật chống giả mạo DNS — DS / DNSKEY)</strong>
            <span className={`badge ${dnsDnssec?.enabled ? 'ok' : ''}`}>
              {dnsDnssec?.enabled ? 'Đã kích hoạt (ECDSAP256SHA256)' : 'Chưa bật'}
            </span>
          </div>
          <div>
            {dnsDnssec?.enabled ? (
              <button type="button" className="mini danger" disabled={!!loading} onClick={() => toggleZoneDnssec(false)}>
                Tắt DNSSEC
              </button>
            ) : (
              <button type="button" className="mini" disabled={!!loading} onClick={() => toggleZoneDnssec(true)}>
                <KeyRound size={13} /> Bật 1-Click DNSSEC
              </button>
            )}
          </div>
        </div>
        <p className="hint" style={{ marginTop: '4px' }}>
          {dnsDnssec?.enabled
            ? 'Zone đã được ký số tự động bằng thuật toán chuẩn công nghiệp ECDSAP256SHA256 (Algorithm 13). Sao chép các thông số DS Record bên dưới và dán vào mục DNSSEC tại Nhà đăng ký tên miền (Registrar) của bạn.'
            : 'Tự động sinh khóa Combined Signing Key (CSK) chuẩn ECDSA P-256 SHA-256 và xuất bản ghi DS / DNSKEY chỉ với 1 cú nhấp chuột.'}
        </p>
        {dnsDnssec?.enabled && dnsDnssec.primary_ds && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '8px', marginTop: '8px' }}>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(160px, 1fr))', gap: '8px' }}>
              <div className="info-box" style={{ margin: 0, padding: '8px 12px' }}>
                <small className="hint" style={{ display: 'block' }}>Key Tag</small>
                <strong><code>{dnsDnssec.primary_ds.key_tag}</code></strong>
              </div>
              <div className="info-box" style={{ margin: 0, padding: '8px 12px' }}>
                <small className="hint" style={{ display: 'block' }}>Algorithm</small>
                <strong><code>{dnsDnssec.primary_ds.algorithm_name}</code></strong>
              </div>
              <div className="info-box" style={{ margin: 0, padding: '8px 12px' }}>
                <small className="hint" style={{ display: 'block' }}>Digest Type</small>
                <strong><code>{dnsDnssec.primary_ds.digest_type_name}</code></strong>
              </div>
            </div>
            {renderCopyBlock('DS Digest (SHA-256)', dnsDnssec.primary_ds.digest)}
            {renderCopyBlock('Bản ghi DS đầy đủ (Full DS Record)', dnsDnssec.primary_ds.record)}
            {dnsDnssec.dnskey_records?.[0] && renderCopyBlock('Bản ghi DNSKEY (Public Signing Key)', dnsDnssec.dnskey_records[0])}
          </div>
        )}
      </div>
      <div className="create-inline dns-record-form">
        <div className="create-inline-head"><strong>{t('Add a record')}</strong></div>
        {renderDnsRecordFields(form, patch => setDnsRecordForm(prev => ({ ...prev, ...patch })))}
        <p className="hint">{dnsTypeHint(form.type)} {t('Names are relative to {zone}: @ is the domain itself.', { zone: zone.name })}</p>
        <div className="actions"><button type="button" disabled={!String(form.value).trim() || !!loading || records === null} onClick={addDnsRecord}><Plus size={14}/> {t('Add record')}</button></div>
      </div>
      <div className="mail-toolbar">
        <strong>{t('Records')}</strong>
        <div className="mail-search"><input value={dnsRecordFilter} placeholder={t('Filter records')} aria-label={t('Filter records')} onChange={e => setDnsRecordFilter(e.target.value)} /></div>
      </div>
      {records === null && <p className="hint">{t('Loading…')}</p>}
      {records && <div className="table dns-records">
        <div className="row dns-record-row dns-record-head" aria-hidden="true">
          <span>{t('Name')}</span><span>{t('Type')}</span><span>TTL</span><span>{t('Value')}</span><span/>
        </div>
        {shown.map(record => isEditing(record)
          ? <div className="row dns-record-edit" key={`${record.name}|${record.type}|${record.content}`}>
              {renderDnsRecordFields(edit.form, patch => setDnsRecordEdit(prev => ({ ...prev, form: { ...prev.form, ...patch } })), { lockType: true })}
              <div className="actions">
                <button type="button" className="secondary-light" onClick={() => setDnsRecordEdit(null)}>{t('Cancel')}</button>
                <button type="button" disabled={!String(edit.form.value).trim() || !!loading} onClick={saveDnsRecordEdit}><Save size={14}/> {t('Save')}</button>
              </div>
            </div>
          : <div className="row dns-record-row" key={`${record.name}|${record.type}|${record.content}`}>
              <span className="dns-record-name" title={record.fqdn}>{record.name}</span>
              <span><code className="dns-type">{record.type}</code></span>
              <span className="dns-record-ttl">{dnsTtlLabel(record.ttl)}</span>
              <span className="dns-record-value">{record.priority != null && <small title={t('Priority')}>{record.priority}</small>}<code>{record.value}</code>
                {record.mail && <span className="badge dns-mail-badge" title={t('Kept in step with the email of {domain}', { domain: record.mail })}><Mail size={11}/> {t('Email')}</span>}</span>
              <span className="row-actions">
                {record.locked
                  ? <span className="dns-locked" title={t("Follows DNS Manager's nameserver settings")}><Lock size={13}/></span>
                  : <>
                    <button type="button" className="mini secondary-light icon-only" disabled={!!loading} aria-label={t('Edit')} title={t('Edit')}
                      onClick={() => setDnsRecordEdit({ old: record, form: { type: record.type, name: record.name, value: record.value, priority: record.priority ?? '', ttl: String(record.ttl || '') } })}><Pencil size={13}/></button>
                    <button type="button" className="mini danger icon-only" disabled={!!loading} aria-label={t('Delete')} title={t('Delete')} onClick={() => deleteDnsRecord(record)}><Trash2 size={13}/></button>
                  </>}
              </span>
            </div>)}
        {shown.length === 0 && <p className="hint">{t('No record matches the filter.')}</p>}
      </div>}
    </div>;
  }

  function renderDns() {
    if (dnsZone) return renderDnsZone();
    if (isAdmin && dnsTab === 'settings') return renderDnsSettings();
    return <div className="section dns-page">
      <div className="section-title">
        <div><h2>{t('DNS Manager')}</h2>
          <p className="hint">{isAdmin ? t('Every domain on the panel, answered by this server.') : t('The DNS records of your domains, answered by this server.')}</p></div>
        <div className="actions">
          {isAdmin && <button type="button" className="secondary-light" onClick={() => setDnsTab('settings')}><SettingsIcon size={14}/> {t('Settings')}</button>}
          <button type="button" className="secondary-light" disabled={!!loading} onClick={() => { loadDnsInfo(); loadDnsZones(); }}><RefreshCw size={14}/> {t('Refresh')}</button>
        </div>
      </div>
      {renderDnsZones()}
    </div>;
  }
  return renderDns();
}
