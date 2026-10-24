import React, { useState, useEffect } from 'react';
import { Archive, ArchiveRestore, ArrowLeft, Ban, Bot, Boxes, Check, ChevronDown, Clock, Code2, Copy, Cpu, Database, Dices, ExternalLink, Eye, FileText, FolderOpen, Globe, HardDrive, Home, Image, KeyRound, Lock, LogIn, LogOut, MemoryStick, Menu, Moon, MoveRight, Network, Pencil, Save, Search, Server, Settings as SettingsIcon, Shield, Sun, Trash2, TerminalIcon, Users, X, RefreshCw, Plus, Download, Upload, Play, Square, RotateCcw, AlertCircle, Activity, BrickWall, Bug, LockKeyhole, PackageOpen, ScrollText, ShieldAlert, CheckCircle, Zap, Bell, Mail, Send, Cloud, Inbox, Forward, ShieldCheck } from 'lucide-react';
import { EmptyState } from '../components/EmptyState.jsx';
import { useAppContext } from '../contexts/AppContext.jsx';
import { t } from '../i18n.js';

export function MailPage({ request, API, isAdmin, users, isReseller, renderCopyBlock, renderDnsRecordEditor, dnsTtlLabel, setPanelSettingsTab, setPage }) {
  const {
    loading, setLoading, setError, setNotice,
    MAIL_SPF_INCLUDES, generateRandomPassword, splitAddresses,
    copyText, formatFileTime, navigateToPage,
    editRelay, saveRelay, deleteRelay, setDefaultRelay, testRelay, relayTlsLabel, renderRelayForm,
    loadRspamdStat, loadRspamdHistory, loadRspamdLog, loadEximLog, rspamdActionLabel, renderLogViewer,
    catchAllDraft, setCatchAllDraft,
    relayForm, setRelayForm, relayTestTo, setRelayTestTo, relayTestLines, setRelayTestLines,
    rspamdView, setRspamdView, rspamdStat, rspamdHistory, rspamdFilter, setRspamdFilter, rspamdLog, rspamdLogQuery, setRspamdLogQuery,
    eximLog, eximLogQuery, setEximLogQuery, mailDns, setMailDns, mailRelays, setMailRelays,
    domainCatchAllForm, setDomainCatchAllForm, domainRelayPick, setDomainRelayPick,
    eximLogFilter, setEximLogFilter, eximLogLines, setEximLogLines,
    mailDnsFilter, setMailDnsFilter, mailDnsInstalling, mailDnsSubtab, setMailDnsSubtab,
    mailRelayEdit, setMailRelayEdit, mailRelayForm, setMailRelayForm, mailRelayTestResult, setMailRelayTestResult, mailRelayTestTo, setMailRelayTestTo,
    mailServiceTab, setMailServiceTab, mailSpfForm, setMailSpfForm,
    rspamdHistoryFilter, setRspamdHistoryFilter, rspamdLogLines, setRspamdLogLines,
    showCreateMailDomain, setShowCreateMailDomain, showCreateRelay, setShowCreateRelay,
  } = useAppContext();
  
  const [mailInfo, setMailInfo] = useState(null);
  const [mailTab, setMailTab] = useState('mailboxes');
  const [mailFilter, setMailFilter] = useState({ domain_id: '', q: '' });
  const [mailPage, setMailPage] = useState(1);
  const [mailboxList, setMailboxList] = useState(null);
  const [showCreateMailbox, setShowCreateMailbox] = useState(false);
  const [mailboxForm, setMailboxForm] = useState({ domain_id: '', local_part: '', password: '', quota_mb: '' });
  const [mailboxEdit, setMailboxEdit] = useState(null);
  const [mailDomainForm, setMailDomainForm] = useState({ domain: '', owner_id: '' });
  const [mailSettings, setMailSettings] = useState(null);
  const [mailSettingsForm, setMailSettingsForm] = useState(null);
  const [forwarderList, setForwarderList] = useState(null);
  const [showCreateForwarder, setShowCreateForwarder] = useState(false);
  const [forwarderForm, setForwarderForm] = useState({ domain_id: '', local_part: '', destinations: '' });
  const [forwarderEdit, setForwarderEdit] = useState(null);
  const [dmarcForm, setDmarcForm] = useState(null);
  const [hostsDraft, setHostsDraft] = useState([]);
  const [mtaStsForm, setMtaStsForm] = useState({ mode: '', tls_rpt: '' });
  const [queueList, setQueueList] = useState(null);
  const [queueFilter, setQueueFilter] = useState({ q: '', state: '', page: 1 });
  const [queueSelected, setQueueSelected] = useState([]);
  const [queueDetail, setQueueDetail] = useState(null);

  useEffect(() => { loadMailInfo(); loadMailboxes(); }, []);
  useEffect(() => { if (mailTab === 'server' || mailTab === 'settings') { loadMailSettings(); loadEximLog(); } }, [mailTab]);
  useEffect(() => { if (mailTab === 'relay' || mailTab === 'relays') loadMailRelays(); }, [mailTab]);
  useEffect(() => { if (mailTab === 'forwarders') loadForwarders(); }, [mailTab]);
  useEffect(() => { if (mailTab === 'queue') loadQueue(1); }, [mailTab]);
  useEffect(() => { if (mailTab === 'rspamd') { loadRspamdStat(); loadRspamdHistory(1); } }, [mailTab]);
  useEffect(() => { if (mailTab === 'rspamd' && rspamdView === 'log') loadRspamdLog(); }, [mailTab, rspamdView]);

  async function saveCatchAll(domain) {
    const target = (catchAllDraft[domain.id] ?? domain.catch_all ?? '').trim();
    const data = await request(`/mail/domains/${domain.id}/catch-all`, {
      method: 'PUT',
      body: JSON.stringify({ catch_all: target }),
    }, t('Saving catch-all…'));
    if (data?.ok) {
      setNotice(target ? t('Catch-all set to {target}.', { target }) : t('Catch-all turned off.'));
      setCatchAllDraft(prev => { const next = { ...prev }; delete next[domain.id]; return next; });
      await loadMailInfo();
    }
  }

  async function saveDomainRelay(domainId, relayId) {
    const data = await request(`/mail/domains/${domainId}/relay`, {
      method: 'PUT',
      body: JSON.stringify({ relay_id: relayId ? Number(relayId) : null }),
    }, t('Saving relay…'));
    if (data?.ok) {
      setNotice(t('Relay updated.'));
      applyMailDnsView(domainId, data);
    }
  }

  async function loadMailInfo() {
    const data = await request('/mail/overview', { silent: true });
    if (data) setMailInfo(data);
    return data;
  }

  function mailQuery(page) {
    const params = new URLSearchParams({ page: String(page), per_page: '50' });
    if (mailFilter.domain_id) params.set('domain_id', mailFilter.domain_id);
    if (mailFilter.q.trim()) params.set('q', mailFilter.q.trim());
    return params.toString();
  }

  async function loadMailboxes(page = mailPage) {
    const data = await request(`/mail/mailboxes?${mailQuery(page)}`, { silent: true });
    if (data) setMailboxList(data);
  }

  async function loadForwarders(page = mailPage) {
    const data = await request(`/mail/forwarders?${mailQuery(page)}`, { silent: true });
    if (data) setForwarderList(data);
  }

  function refreshMail() {
    loadMailInfo();
    if (mailTab === 'mailboxes') loadMailboxes();
    if (mailTab === 'forwarders') loadForwarders();
  }

  function mailPassword() {
    // The server wants at least one letter and one digit.
    let value = '';
    do { value = generateRandomPassword(16); } while (!/[A-Za-z]/.test(value) || !/\d/.test(value));
    return value;
  }

  async function addMailDomain() {
    const domain = mailDomainForm.domain.trim().toLowerCase();
    if (!domain) return;
    const body = { domain };
    if (isAdmin && mailDomainForm.owner_id) body.owner_id = Number(mailDomainForm.owner_id);
    const data = await request('/mail/domains', { method: 'POST', body: JSON.stringify(body) }, t('Turning on email...'));
    if (data) {
      setMailDomainForm({ domain: '', owner_id: '' });
      setNotice(t('Email is on for {domain}. Publish its DNS records next.', { domain: data.domain }));
      await loadMailInfo();
      openMailDns(data);
    }
  }

  async function deleteMailDomain(domain) {
    const typed = prompt(t('This deletes every mailbox of {domain} with all its mail, and its forwarders. It cannot be undone.\n\nType the domain name to confirm:', { domain: domain.domain }));
    if (typed === null) return;
    if (typed.trim().toLowerCase() !== domain.domain) { setError(t('The name did not match; nothing was deleted.')); return; }
    const data = await request(`/mail/domains/${domain.id}?confirm=${encodeURIComponent(domain.domain)}`, { method: 'DELETE' }, t('Deleting...'));
    if (data) {
      setNotice(t('Email for {domain} deleted.', { domain: domain.domain }));
      if (mailFilter.domain_id === String(domain.id)) setMailFilter(prev => ({ ...prev, domain_id: '' }));
      refreshMail();
    }
  }

  async function rotateMailDkim(domain) {
    if (!confirm(t('Start a DKIM key rotation for {domain}?\n\nA new key is published under a selector of its own while mail is still signed with the current one. Mail switches to the new key only once public DNS shows its record, and the old key stays published for a few days after.', { domain: domain.domain }))) return;
    const data = await request(`/mail/domains/${domain.id}/dkim/rotate`, { method: 'POST' }, t('Creating a new key...'));
    if (data) { setNotice(t('New DKIM key published. Mail switches to it once DNS shows its record.')); applyMailDnsView(domain, data); }
  }

  async function advanceMailDkim(domain) {
    const retiring = mailDns?.dkim?.state === 'retiring';
    if (retiring && !confirm(t('Remove the previous DKIM key of {domain} now?\n\nMail signed with it in the last few days and still on its way can no longer be verified.', { domain: domain.domain }))) return;
    const data = await request(`/mail/domains/${domain.id}/dkim/advance`, { method: 'POST' }, t('Checking DNS...'));
    if (data) {
      setNotice(data.step === 'switch' ? t('Mail is now signed with the new DKIM key.') : data.step === 'retire' ? t('The previous DKIM key is removed.') : t('Nothing to do yet.'));
      applyMailDnsView(domain, data);
    }
  }

  async function cancelMailDkim(domain) {
    if (!confirm(t('Drop the new DKIM key of {domain}? Mail stays signed with the current one.', { domain: domain.domain }))) return;
    const data = await request(`/mail/domains/${domain.id}/dkim/cancel`, { method: 'POST' }, t('Saving...'));
    if (data) { setNotice(t('DKIM key rotation cancelled.')); applyMailDnsView(domain, data); }
  }

  async function saveMailHosts(domain, off = false) {
    const names = hostsDraft.map(label => `${label}.${domain.domain}`).join(', ');
    if (!off && !confirm(t("Serve {names} with a Let's Encrypt certificate of their own?\n\nEach name must already point at this server. Mail apps and other mail servers then see mail.{domain} with a certificate that matches it.", { names, domain: domain.domain }))) return;
    if (off && !confirm(t('Stop serving the mail hosts of {domain}? Mail apps go back to the server name and its certificate.', { domain: domain.domain }))) return;
    const data = await request(`/mail/domains/${domain.id}/mail-hosts`, off ? { method: 'DELETE' } : { method: 'POST', body: JSON.stringify({ hosts: hostsDraft }) },
      off ? t('Removing...') : t('Issuing a certificate for {names}...', { names }));
    if (data) { setNotice(off ? t('The mail hosts of {domain} are no longer served.', { domain: domain.domain }) : t('{names} served with their own certificate.', { names })); applyMailDnsView(domain, data); loadMailInfo(); }
  }

  async function saveMailMtaSts(domain) {
    const mode = mtaStsForm.mode;
    if (mode === 'enforce' && !confirm(t('Enforce MTA-STS for {domain}?\n\nOther mail servers will then deliver only over TLS to mail.{domain} with a valid certificate, and remember that for a week. Run it in testing mode first, with TLS reports on.', { domain: domain.domain }))) return;
    const data = await request(`/mail/domains/${domain.id}/mta-sts`, { method: 'PUT', body: JSON.stringify({ mode, tls_rpt: splitAddresses(mtaStsForm.tls_rpt || '') }) }, t('Saving...'));
    if (data) { setNotice(t('MTA-STS and TLS reporting saved.')); applyMailDnsView(domain, data); }
  }

  async function loadQueue(page = queueFilter.page, state = queueFilter.state) {
    const params = new URLSearchParams({ page: String(page), per_page: '50' });
    if (queueFilter.q.trim()) params.set('q', queueFilter.q.trim());
    if (state) params.set('state', state);
    const data = await request(`/mail/queue?${params.toString()}`, { silent: true });
    if (data) { setQueueList(data); setQueueFilter(prev => ({ ...prev, page, state })); setQueueSelected([]); }
  }

  async function queueAction(action, ids) {
    const labels = { retry: t('Retry'), freeze: t('Hold'), thaw: t('Release'), delete: t('Delete') };
    if (action === 'delete' && !confirm(t('Delete {n} message(s) from the queue? They are not delivered and nobody is told.', { n: ids.length }))) return;
    const data = await request('/mail/queue/action', { method: 'POST', body: JSON.stringify({ action, ids }) }, t('{action}...', { action: labels[action] }));
    if (data) { setNotice(t('{action}: {n} message(s).', { action: labels[action], n: ids.length })); setQueueDetail(null); loadQueue(); }
  }

  async function queueRetryAll() {
    if (!confirm(t('Try every message in the queue again now, held ones included?'))) return;
    const data = await request('/mail/queue/retry-all', { method: 'POST' }, t('Retrying...'));
    if (data) { setNotice(t('The whole queue is being retried.')); setTimeout(() => loadQueue(), 3000); }
  }

  async function openQueueMessage(item) {
    const data = await request(`/mail/queue/${encodeURIComponent(item.id)}`, { silent: true });
    if (data) setQueueDetail({ item, headers: data.headers });
  }

  function renderMailQueue() {
    const list = queueList;
    const items = list?.items || [];
    const pages = Math.max(1, Math.ceil((list?.total || 0) / (list?.per_page || 50)));
    const allChosen = items.length > 0 && items.every(item => queueSelected.includes(item.id));
    return <div className="mail-tab">
      <div className="mail-toolbar">
        <form className="mail-search" onSubmit={e => { e.preventDefault(); loadQueue(1); }}>
          <input value={queueFilter.q} placeholder={t('Id, sender or recipient')} aria-label={t('Search')} onChange={e => setQueueFilter(prev => ({ ...prev, q: e.target.value }))} />
          <button type="submit" className="secondary-light icon-only" aria-label={t('Search')} title={t('Search')}><Search size={14}/></button>
        </form>
        <select value={queueFilter.state} aria-label={t('State')} onChange={e => loadQueue(1, e.target.value)}>
          <option value="">{t('Every message')}</option>
          <option value="frozen">{t('Held (frozen)')}</option>
          <option value="active">{t('Waiting to be sent')}</option>
        </select>
        <button type="button" className="secondary-light" disabled={!!loading} onClick={() => loadQueue()}><RefreshCw size={14}/> {t('Refresh')}</button>
        <button type="button" className="secondary-light" disabled={!!loading || !list?.total} onClick={queueRetryAll}><Play size={14}/> {t('Retry all')}</button>
      </div>
      {queueSelected.length > 0 && <div className="actions">
        <span className="hint">{t('{n} selected', { n: queueSelected.length })}</span>
        <button className="mini secondary-light" disabled={!!loading} onClick={() => queueAction('retry', queueSelected)}><Play size={13}/> {t('Retry')}</button>
        <button className="mini secondary-light" disabled={!!loading} onClick={() => queueAction('freeze', queueSelected)}><Square size={13}/> {t('Hold')}</button>
        <button className="mini secondary-light" disabled={!!loading} onClick={() => queueAction('thaw', queueSelected)}><RotateCcw size={13}/> {t('Release')}</button>
        <button className="mini danger" disabled={!!loading} onClick={() => queueAction('delete', queueSelected)}><Trash2 size={13}/> {t('Delete')}</button>
      </div>}
      {list === null && <p className="hint">{t('Loading…')}</p>}
      {list && items.length === 0 && <EmptyState icon={Send} message={queueFilter.q || queueFilter.state ? t('No queued message matches.') : t('The mail queue is empty.')} />}
      {items.length > 0 && <div className="table">
        <div className="row mail-row">
          <label className="check-line"><input type="checkbox" checked={allChosen} onChange={e => setQueueSelected(e.target.checked ? items.map(item => item.id) : [])} /><span>{t('{n} messages, {frozen} held', { n: list.total, frozen: list.frozen })}</span></label>
        </div>
        {items.map(item => <div className="row mail-row" key={item.id}>
          <span className="mail-row-name">
            <label className="check-line"><input type="checkbox" checked={queueSelected.includes(item.id)} aria-label={t('Select {id}', { id: item.id })}
              onChange={e => setQueueSelected(prev => e.target.checked ? [...prev, item.id] : prev.filter(id => id !== item.id))} />
              <strong><code>{item.id}</code></strong></label>
            <small>{item.sender || t('(bounce)')} → {item.recipients.filter(rcpt => !rcpt.delivered).map(rcpt => rcpt.address).join(', ') || '—'}</small>
            <small>{t('{age} in the queue · {size}', { age: item.age, size: item.size })}{item.frozen && <span className="badge warn">{t('Held')}</span>}</small>
          </span>
          <span className="row-actions">
            <button className="mini secondary-light" disabled={!!loading} onClick={() => openQueueMessage(item)}><Eye size={13}/> {t('Headers')}</button>
            <button className="mini secondary-light" disabled={!!loading} onClick={() => queueAction('retry', [item.id])} title={t('Retry')}><Play size={13}/></button>
            {item.frozen
              ? <button className="mini secondary-light" disabled={!!loading} onClick={() => queueAction('thaw', [item.id])} title={t('Release')}><RotateCcw size={13}/></button>
              : <button className="mini secondary-light" disabled={!!loading} onClick={() => queueAction('freeze', [item.id])} title={t('Hold')}><Square size={13}/></button>}
            <button className="mini danger" disabled={!!loading} onClick={() => queueAction('delete', [item.id])} aria-label={t('Delete {name}', { name: item.id })} title={t('Delete')}><Trash2 size={13}/></button>
          </span>
        </div>)}
      </div>}
      {pages > 1 && <div className="firewall-ip-pager">
        <button className="mini secondary-light" disabled={queueFilter.page <= 1} onClick={() => loadQueue(queueFilter.page - 1)}>{t('Previous')}</button>
        <span className="hint">{t('Page {page} of {pages}', { page: queueFilter.page, pages })}</span>
        <button className="mini secondary-light" disabled={queueFilter.page >= pages} onClick={() => loadQueue(queueFilter.page + 1)}>{t('Next')}</button>
      </div>}
      <p className="hint">{t('Held messages are kept until released or deleted (7 days at most). With auto-hold on, mail from a mailbox or website over its sending limit is held here instead of refused.')}</p>
    </div>;
  }

  async function saveMailDmarc(domain, reset = false) {
    const f = dmarcForm;
    const body = reset ? null : {
      p: f.p, sp: f.sp || '', pct: Number(f.pct ?? 100), adkim: f.adkim || 'r', aspf: f.aspf || 'r', fo: (f.fo || '').trim(),
      rua: splitAddresses(f.rua || ''), ruf: splitAddresses(f.ruf || ''),
    };
    const data = await request(`/mail/domains/${domain.id}/dmarc`, reset ? { method: 'DELETE' } : { method: 'PUT', body: JSON.stringify(body) }, t('Saving...'));
    if (data) { setNotice(reset ? t('The default DMARC policy is back.') : t('DMARC policy saved.')); applyMailDnsView(domain, data); }
  }

  async function toggleWebmailHost(domain, enabled) {
    if (enabled && !confirm(t("Serve webmail at webmail.{domain}?\n\nIts A record must already point at this server: a Let's Encrypt certificate is issued for it now.", { domain: domain.domain }))) return;
    if (!enabled && !confirm(t("Stop serving webmail at webmail.{domain}? Webmail stays available on the server's own address.", { domain: domain.domain }))) return;
    const data = await request(`/mail/domains/${domain.id}/webmail-host`, { method: 'POST', body: JSON.stringify({ enabled }) },
      enabled ? t('Issuing a certificate for webmail.{domain}...', { domain: domain.domain }) : t('Removing...'));
    if (data) {
      setNotice(enabled ? t('Webmail is now at https://webmail.{domain}/', { domain: domain.domain }) : t('webmail.{domain} removed.', { domain: domain.domain }));
      loadMailInfo();
    }
  }

  async function createMailbox() {
    const body = { domain_id: Number(mailboxForm.domain_id), local_part: mailboxForm.local_part.trim().toLowerCase(), password: mailboxForm.password };
    if (String(mailboxForm.quota_mb).trim() !== '') body.quota_mb = Number(mailboxForm.quota_mb);
    const data = await request('/mail/mailboxes', { method: 'POST', body: JSON.stringify(body) }, t('Creating mailbox...'));
    if (data) {
      setNotice(t('{address} created.', { address: data.address }));
      setMailboxForm(prev => ({ ...prev, local_part: '', password: '' }));
      setShowCreateMailbox(false);
      loadMailboxes();
      loadMailInfo();
    }
  }

  async function saveMailboxEdit() {
    const edit = mailboxEdit;
    const body = {};
    if (edit.password) body.password = edit.password;
    if (String(edit.quota_mb) !== String(edit.box.quota_mb)) body.quota_mb = Number(edit.quota_mb);
    if (!Object.keys(body).length) { setMailboxEdit(null); return; }
    const data = await request(`/mail/mailboxes/${edit.box.id}`, { method: 'PUT', body: JSON.stringify(body) }, t('Saving...'));
    if (data) { setNotice(t('{address} saved.', { address: data.address })); setMailboxEdit(null); loadMailboxes(); }
  }

  async function setMailboxEnabled(box, enabled) {
    if (!enabled && !confirm(t('Suspend {address}?\n\nIt keeps receiving mail, but nobody can sign in to it or send from it until it is resumed.', { address: box.address }))) return;
    const data = await request(`/mail/mailboxes/${box.id}`, { method: 'PUT', body: JSON.stringify({ enabled }) }, t('Saving...'));
    if (data) { setNotice(enabled ? t('{address} resumed.', { address: box.address }) : t('{address} suspended.', { address: box.address })); loadMailboxes(); }
  }

  async function deleteMailbox(box) {
    if (!confirm(t('Delete {address} and all of its mail?\n\nThis cannot be undone.', { address: box.address }))) return;
    const data = await request(`/mail/mailboxes/${box.id}`, { method: 'DELETE' }, t('Deleting...'));
    if (data) { setNotice(t('{address} deleted.', { address: box.address })); loadMailboxes(); loadMailInfo(); }
  }

  async function openWebmail(box) {
    // Opened inside the click so no pop-up blocker stops it; the signed link
    // arrives a moment later.
    const win = window.open('about:blank', '_blank');
    const data = await request(`/mail/mailboxes/${box.id}/webmail`, { method: 'POST' }, t('Opening webmail...'));
    if (data?.url) {
      if (win) { win.opener = null; win.location.href = data.url; } else window.location.href = data.url;
    } else if (win) {
      win.close();
    }
  }

  function applyMailDnsView(domain, data) {
    setMailDns(prev => ({ domain, records: data?.records || (prev?.domain?.id === domain.id ? prev.records : []), relay: data?.relay || null, hostedZone: data?.hosted_zone || '', dkim: data?.dkim || null, dmarc: data?.dmarc || null, hosts: data?.hosts || null, mtaSts: data?.mta_sts || null }));
    setHostsDraft(data?.hosts?.served?.length ? data.hosts.served : ['mail', 'mta-sts', 'autoconfig', 'autodiscover']);
    setMtaStsForm({ mode: data?.mta_sts?.mode || '', tls_rpt: (data?.mta_sts?.tls_rpt || []).join(', ') });
    const policy = data?.dmarc?.policy;
    setDmarcForm(policy ? { ...policy, rua: (policy.rua || []).join(', '), ruf: (policy.ruf || []).join(', ') } : null);
  }

  async function openMailDns(domain) {
    setMailDns({ domain, records: null, relay: null });
    const data = await request(`/mail/domains/${domain.id}/dns`, { silent: true });
    applyMailDnsView(domain, data);
  }

  async function loadMailRelays() {
    const data = await request('/mail/relays', { silent: true });
    if (data) setMailRelays(data);
  }

  async function allowMailSender(value) {
    if (!confirm(t('Never block mail from {sender}?', { sender: value }))) return;
    const data = await request('/mail/rspamd/allow', { method: 'POST', body: JSON.stringify({ value }) }, t('Saving...'));
    if (data) {
      setNotice(t('{sender} is on the allowlist. Ask the sender to send the message again.', { sender: value }));
      applyMailSettings({ ...(mailSettings || {}), settings: data.settings });
      loadRspamdHistory(rspamdFilter.page);
    }
  }

  async function blockMailSender(value) {
    if (!confirm(`Chặn vĩnh viễn toàn bộ thư gửi từ "${value}" (Sender / Domain Blacklist)?`)) return;
    const data = await request('/mail/rspamd/block', { method: 'POST', body: JSON.stringify({ value }) }, t('Saving...'));
    if (data) {
      setNotice(`Đã thêm ${value} vào danh sách chặn (Blacklist).`);
      applyMailSettings({ ...(mailSettings || {}), settings: data.settings });
      loadRspamdHistory(rspamdFilter.page);
    }
  }

  function applyMailSettings(data) {
    setMailSettings(data);
    const domainLimitsObj = data.settings?.domain_rate_limits || {};
    const domainLimitsText = Object.entries(domainLimitsObj)
      .map(([dom, limit]) => `${dom}=${limit}`)
      .join('\n');
    setMailSettingsForm({
      ...data.settings,
      allow: (data.settings?.allow || []).join('\n'),
      block: (data.settings?.block || []).join('\n'),
      domain_rate_limits_text: domainLimitsText,
    });
  }

  async function loadMailSettings() {
    const data = await request('/mail/settings', { silent: true });
    if (data) applyMailSettings(data);
  }

  async function saveMailSettings() {
    const f = mailSettingsForm;
    const domainLimits = {};
    for (const rawLine of String(f.domain_rate_limits_text || '').split(/\r?\n/)) {
      const line = rawLine.trim();
      if (!line || line.startsWith('#')) continue;
      const [dom, lim] = line.split(/[=:,\s]+/, 2);
      if (dom && lim !== undefined && !Number.isNaN(Number(lim))) {
        domainLimits[dom.trim().toLowerCase()] = Number(lim);
      }
    }
    const body = {
      auth_rate_per_hour: Number(f.auth_rate_per_hour) || 0,
      local_rate_per_hour: Number(f.local_rate_per_hour) || 0,
      domain_rate_per_hour: Number(f.domain_rate_per_hour ?? 500),
      auto_hold_on_rate_exceed: f.auto_hold_on_rate_exceed !== false,
      domain_rate_limits: domainLimits,
      max_message_mb: Number(f.max_message_mb) || 50,
      spam_enabled: !!f.spam_enabled,
      spam_header_score: Number(f.spam_header_score) || 6,
      spam_rewrite_score: Number(f.spam_rewrite_score) || 8,
      spam_reject_score: Number(f.spam_reject_score) || 15,
      spam_subject_tag: String(f.spam_subject_tag || '[SPAM]').trim() || '[SPAM]',
      greylisting: !!f.greylisting,
      default_quota_mb: Number(f.default_quota_mb) || 1024,
      dkim_rotate_days: Number(f.dkim_rotate_days) || 0,
      dkim_retire_days: Number(f.dkim_retire_days) || 7,
      allow: splitAddresses(f.allow),
      block: splitAddresses(f.block),
    };
    const data = await request('/mail/settings', { method: 'PUT', body: JSON.stringify(body) }, t('Applying mail settings...'));
    if (data) { setNotice(t('Mail settings applied.')); loadMailSettings(); }
  }

  async function createForwarder() {
    const body = { domain_id: Number(forwarderForm.domain_id), local_part: forwarderForm.local_part.trim().toLowerCase(), destinations: splitAddresses(forwarderForm.destinations) };
    const data = await request('/mail/forwarders', { method: 'POST', body: JSON.stringify(body) }, t('Creating forwarder...'));
    if (data) {
      setNotice(t('{address} now forwards to {destinations}.', { address: data.address, destinations: data.destinations.join(', ') }));
      setForwarderForm(prev => ({ ...prev, local_part: '', destinations: '' }));
      setShowCreateForwarder(false);
      loadForwarders();
      loadMailInfo();
    }
  }

  async function saveForwarderEdit() {
    const data = await request(`/mail/forwarders/${forwarderEdit.item.id}`, { method: 'PUT', body: JSON.stringify({ destinations: splitAddresses(forwarderEdit.destinations) }) }, t('Saving...'));
    if (data) { setNotice(t('{address} saved.', { address: data.address })); setForwarderEdit(null); loadForwarders(); }
  }

  async function deleteForwarder(item) {
    if (!confirm(t('Delete the forwarder {address}?', { address: item.address }))) return;
    const data = await request(`/mail/forwarders/${item.id}`, { method: 'DELETE' }, t('Deleting...'));
    if (data) { setNotice(t('{address} deleted.', { address: item.address })); loadForwarders(); loadMailInfo(); }
  }

  function mailDomainOptions(placeholder) {
    return <>
      {placeholder && <option value="">{placeholder}</option>}
      {(mailInfo?.domains || []).map(d => <option key={d.id} value={String(d.id)}>{d.domain}{isAdmin && d.owner ? ` (${d.owner})` : ''}</option>)}
    </>;
  }

  function renderMailFilter(onSearch) {
    return <div className="mail-filter">
      <select value={mailFilter.domain_id} aria-label={t('Domain')} onChange={e => { setMailFilter(prev => ({ ...prev, domain_id: e.target.value })); setMailPage(1); }}>
        {mailDomainOptions(t('All domains'))}
      </select>
      <form className="mail-search" onSubmit={e => { e.preventDefault(); setMailPage(1); onSearch(); }}>
        <input value={mailFilter.q} placeholder={t('Search')} aria-label={t('Search')} onChange={e => setMailFilter(prev => ({ ...prev, q: e.target.value }))} />
        <button type="submit" className="secondary-light icon-only" aria-label={t('Search')} title={t('Search')}><Search size={14}/></button>
      </form>
    </div>;
  }

  function renderMailPager(list) {
    const pages = Math.max(1, Math.ceil((list?.total || 0) / (list?.per_page || 50)));
    if (pages <= 1) return null;
    return <div className="firewall-ip-pager">
      <button className="mini secondary-light" disabled={mailPage <= 1} onClick={() => setMailPage(p => Math.max(1, p - 1))}>{t('Previous')}</button>
      <span className="hint">{t('Page {page} of {pages}', { page: mailPage, pages })}</span>
      <button className="mini secondary-light" disabled={mailPage >= pages} onClick={() => setMailPage(p => p + 1)}>{t('Next')}</button>
    </div>;
  }

  function renderMailboxes() {
    const info = mailInfo;
    const list = mailboxList;
    const items = list?.items || [];
    const limit = info.mailbox_limit;
    const atLimit = !isAdmin && !!info.at_limit;
    const local = mailboxForm.local_part.trim().toLowerCase();
    const canCreate = !!mailboxForm.domain_id && /^[a-z0-9]([a-z0-9._-]{0,62}[a-z0-9])?$/.test(local) && !local.includes('..')
      && mailboxForm.password.length >= 8 && /[A-Za-z]/.test(mailboxForm.password) && /\d/.test(mailboxForm.password);
    const openCreate = () => {
      setShowCreateMailbox(true);
      setMailboxForm(prev => ({
        ...prev,
        domain_id: prev.domain_id || mailFilter.domain_id || String(info.domains[0]?.id || ''),
        password: prev.password || mailPassword(),
        quota_mb: prev.quota_mb === '' ? String(info.default_quota_mb || 1024) : prev.quota_mb,
      }));
    };
    return <div className="mail-tab">
      <div className="mail-toolbar">
        {renderMailFilter(() => loadMailboxes(1))}
        {!showCreateMailbox && <button type="button" disabled={atLimit} title={atLimit ? t('Mailbox limit reached') : ''} onClick={openCreate}><Plus size={15}/> {t('New mailbox')}</button>}
      </div>
      {atLimit && <p className="hint">{limit
        ? t('You have used all {n} of your mailboxes. Delete one, or ask your provider for more.', { n: limit })
        : t('Your hosting package does not include mailboxes. Ask your provider.')}</p>}
      {showCreateMailbox && <div className="create-inline">
        <div className="create-inline-head">
          <strong>{t('New mailbox')}</strong>
          <button type="button" className="secondary-light icon-only mini" onClick={() => setShowCreateMailbox(false)} aria-label={t('Close')} title={t('Close')}><X size={15}/></button>
        </div>
        <div className="mail-create-grid">
          <div className="field mail-address-field"><span className="field-label">{t('Address')}</span>
            <div className="mail-address-input">
              <input value={mailboxForm.local_part} placeholder="info" autoComplete="off" spellCheck={false} aria-label={t('Mailbox name')}
                onChange={e => setMailboxForm(prev => ({ ...prev, local_part: e.target.value.toLowerCase().replace(/[^a-z0-9._-]/g, '') }))} />
              <span>@</span>
              <select value={mailboxForm.domain_id} aria-label={t('Domain')} onChange={e => setMailboxForm(prev => ({ ...prev, domain_id: e.target.value }))}>{mailDomainOptions(t('Choose a domain'))}</select>
            </div>
          </div>
          <div className="field"><span className="field-label">{t('Password')}</span>
            <div className="password-with-generate">
              <input value={mailboxForm.password} autoComplete="new-password" spellCheck={false} placeholder={t('8+ characters, letters and digits')} onChange={e => setMailboxForm(prev => ({ ...prev, password: e.target.value }))} data-lpignore="true" data-1p-ignore="true" />
              <button type="button" className="secondary-light icon-only" title={t('Generate random password')} aria-label={t('Generate random password')} onClick={() => setMailboxForm(prev => ({ ...prev, password: mailPassword() }))}><Dices size={15}/></button>
              <button type="button" className="secondary-light icon-only" title={t('Copy')} aria-label={t('Copy')} onClick={() => copyText(mailboxForm.password, t('Copied.'))}><Copy size={15}/></button>
            </div>
          </div>
          <div className="field mail-quota-field"><span className="field-label">{t('Size (MB)')}{isAdmin && <em> {t('0 = unlimited')}</em>}</span>
            <input type="number" min={isAdmin ? 0 : 1} max={isAdmin ? 1048576 : info.max_user_quota_mb} value={mailboxForm.quota_mb} onChange={e => setMailboxForm(prev => ({ ...prev, quota_mb: e.target.value }))} />
          </div>
          <button disabled={!canCreate || !!loading} onClick={createMailbox}><Plus size={14}/> {t('Create')}</button>
        </div>
        <p className="hint">{t('Copy the password now — it is not shown again. It signs in to webmail and to any mail app, with the full address as the username.')}</p>
      </div>}
      {list === null && <p className="hint">{t('Loading…')}</p>}
      {list && items.length === 0 && <EmptyState icon={Inbox} message={mailFilter.q || mailFilter.domain_id ? t('No mailbox matches.') : t('No mailboxes yet.')} />}
      {items.length > 0 && <div className="table">
        {items.map(box => {
          const percent = box.quota_mb && box.used_mb != null ? Math.min(100, Math.round(box.used_mb / box.quota_mb * 100)) : null;
          return <div className="row mail-row" key={box.id}>
            <span className="mail-row-name">
              <strong>{box.address}</strong>
              <small>
                {!box.enabled && <span className="badge warn">{t('Suspended')}</span>}
                {isAdmin && box.owner ? <span>{t('Account')}: {box.owner}</span> : null}
              </small>
            </span>
            <span className="mail-row-usage">
              <small>{box.used_mb != null ? t('{n} MB', { n: box.used_mb }) : '—'} / {box.quota_mb ? t('{n} MB', { n: box.quota_mb }) : t('unlimited')}</small>
              {percent != null && <span className={`mail-meter ${percent >= 90 ? 'bad' : percent >= 75 ? 'warn' : ''}`}><span style={{ width: `${percent}%` }} /></span>}
            </span>
            <span className="row-actions">
              <button className="mini" disabled={!!loading || !box.enabled} onClick={() => openWebmail(box)} title={t('Open this mailbox in webmail, no password needed')}><Mail size={13}/> {t('Webmail')}</button>
              <button className="mini secondary-light" disabled={!!loading} onClick={() => setMailboxEdit({ box, password: '', quota_mb: String(box.quota_mb) })}><Pencil size={13}/> {t('Edit')}</button>
              <button className="mini secondary-light" disabled={!!loading} onClick={() => setMailboxEnabled(box, !box.enabled)}>{box.enabled ? <><Ban size={13}/> {t('Suspend')}</> : <><Play size={13}/> {t('Resume')}</>}</button>
              <button className="mini danger" disabled={!!loading} onClick={() => deleteMailbox(box)} aria-label={t('Delete {name}', { name: box.address })} title={t('Delete')}><Trash2 size={13}/></button>
            </span>
          </div>;
        })}
      </div>}
      {renderMailPager(list)}
    </div>;
  }

  function renderForwarders() {
    const info = mailInfo;
    const list = forwarderList;
    const items = list?.items || [];
    const local = forwarderForm.local_part.trim().toLowerCase();
    const canCreate = !!forwarderForm.domain_id && /^[a-z0-9]([a-z0-9._-]{0,62}[a-z0-9])?$/.test(local) && splitAddresses(forwarderForm.destinations).length > 0;
    return <div className="mail-tab">
      <div className="mail-toolbar">
        {renderMailFilter(() => loadForwarders(1))}
        {!showCreateForwarder && <button type="button" onClick={() => { setShowCreateForwarder(true); setForwarderForm(prev => ({ ...prev, domain_id: prev.domain_id || mailFilter.domain_id || String(info.domains[0]?.id || '') })); }}><Plus size={15}/> {t('New forwarder')}</button>}
      </div>
      {showCreateForwarder && <div className="create-inline">
        <div className="create-inline-head">
          <strong>{t('New forwarder')}</strong>
          <button type="button" className="secondary-light icon-only mini" onClick={() => setShowCreateForwarder(false)} aria-label={t('Close')} title={t('Close')}><X size={15}/></button>
        </div>
        <div className="mail-create-grid">
          <div className="field mail-address-field"><span className="field-label">{t('Address')}</span>
            <div className="mail-address-input">
              <input value={forwarderForm.local_part} placeholder="sales" autoComplete="off" spellCheck={false} aria-label={t('Forwarder name')}
                onChange={e => setForwarderForm(prev => ({ ...prev, local_part: e.target.value.toLowerCase().replace(/[^a-z0-9._-]/g, '') }))} />
              <span>@</span>
              <select value={forwarderForm.domain_id} aria-label={t('Domain')} onChange={e => setForwarderForm(prev => ({ ...prev, domain_id: e.target.value }))}>{mailDomainOptions(t('Choose a domain'))}</select>
            </div>
          </div>
          <div className="field mail-destinations-field"><span className="field-label">{t('Forward to')}</span>
            <textarea rows={2} value={forwarderForm.destinations} spellCheck={false} placeholder={t('one or more addresses, separated by commas')} onChange={e => setForwarderForm(prev => ({ ...prev, destinations: e.target.value }))} />
          </div>
          <button disabled={!canCreate || !!loading} onClick={createForwarder}><Plus size={14}/> {t('Create')}</button>
        </div>
        <p className="hint">{t('If a mailbox has the same address, it keeps a copy of each message as well.')}</p>
      </div>}
      {list === null && <p className="hint">{t('Loading…')}</p>}
      {list && items.length === 0 && <EmptyState icon={Forward} message={mailFilter.q || mailFilter.domain_id ? t('No forwarder matches.') : t('No forwarders yet.')} />}
      {items.length > 0 && <div className="table">
        {items.map(item => <div className="row mail-row" key={item.id}>
          <span className="mail-row-name"><strong>{item.address}</strong>{item.keeps_copy && <small><span className="badge">{t('Keeps a copy')}</span></small>}</span>
          <span className="mail-row-destinations"><MoveRight size={13}/> <span>{item.destinations.join(', ')}</span></span>
          <span className="row-actions">
            <button className="mini secondary-light" disabled={!!loading} onClick={() => setForwarderEdit({ item, destinations: item.destinations.join(', ') })}><Pencil size={13}/> {t('Edit')}</button>
            <button className="mini danger" disabled={!!loading} onClick={() => deleteForwarder(item)} aria-label={t('Delete {name}', { name: item.address })} title={t('Delete')}><Trash2 size={13}/></button>
          </span>
        </div>)}
      </div>}
      {renderMailPager(list)}
    </div>;
  }

  function renderMailDomains() {
    const info = mailInfo;
    const domains = info.domains || [];
    const candidates = info.candidates || [];
    return <div className="mail-tab">
      <div className="create-inline">
        <div className="create-inline-head"><strong>{t('Turn on email for a domain')}</strong></div>
        <div className="mail-create-grid">
          {isAdmin
            ? <div className="field"><span className="field-label">{t('Domain')}</span>
                <input list="mail-domain-candidates" value={mailDomainForm.domain} placeholder="example.com" spellCheck={false}
                  onChange={e => setMailDomainForm(prev => ({ ...prev, domain: e.target.value.trim().toLowerCase() }))} />
                <datalist id="mail-domain-candidates">{candidates.map(name => <option key={name} value={name} />)}</datalist>
              </div>
            : <div className="field"><span className="field-label">{t('Domain')}</span>
                <select value={mailDomainForm.domain} onChange={e => setMailDomainForm(prev => ({ ...prev, domain: e.target.value }))}>
                  <option value="">{t('Choose one of your websites')}</option>
                  {candidates.map(name => <option key={name} value={name}>{name}</option>)}
                </select>
              </div>}
          {isAdmin && <div className="field"><span className="field-label">{t('Account')}</span>
            <select value={mailDomainForm.owner_id} onChange={e => setMailDomainForm(prev => ({ ...prev, owner_id: e.target.value }))}>
              <option value="">{t("The website's owner")}</option>
              {users.map(user => <option key={user.id} value={user.id}>{user.username}</option>)}
            </select>
          </div>}
          <button disabled={!mailDomainForm.domain.trim() || !!loading} onClick={addMailDomain}><Plus size={14}/> {t('Turn on email')}</button>
        </div>
        {!isAdmin && candidates.length === 0 && <p className="hint">{t('Every domain of your websites already has email. Add a website or a domain alias to use another one.')}</p>}
        <p className="hint">{t('Mail for a domain arrives here once its MX record points at this server; the DNS records to add are shown next.')}</p>
      </div>
      {domains.length === 0 && <EmptyState icon={Mail} message={t('No mail domains yet.')} />}
      {domains.length > 0 && <div className="table">
        {domains.map(d => {
          const draft = String(catchAllDraft[d.id] ?? d.catch_all ?? '');
          return <div className="row mail-domain-row" key={d.id}>
            <span className="mail-row-name">
              <strong>{d.domain}</strong>
              <small>{t('{mailboxes} mailboxes · {forwarders} forwarders', { mailboxes: d.mailboxes, forwarders: d.forwarders })}{isAdmin && d.owner ? ` · ${t('Account')}: ${d.owner}` : ''}</small>
            </span>
            <span className="mail-catchall">
              <span className="field-label">{t('Catch-all')}</span>
              <span className="mail-catchall-input">
                <input value={draft} placeholder={t('Off: unknown addresses are refused')} spellCheck={false} aria-label={t('Catch-all for {domain}', { domain: d.domain })}
                  onChange={e => setCatchAllDraft(prev => ({ ...prev, [d.id]: e.target.value }))} />
                {draft.trim() !== (d.catch_all || '') && <button className="mini" disabled={!!loading} onClick={() => saveCatchAll(d)} aria-label={t('Save')} title={t('Save')}><Save size={13}/></button>}
              </span>
            </span>
            <label className="check-line mail-webmail-host" title={t('Serve webmail at webmail.{domain} with its own certificate', { domain: d.domain })}>
              <input type="checkbox" checked={!!d.webmail_host} disabled={!!loading} onChange={e => toggleWebmailHost(d, e.target.checked)} />
              <span>webmail.{d.domain}</span>
            </label>
            <span className="row-actions">
              <button className="mini secondary-light" disabled={!!loading} onClick={() => openMailDns(d)}><Globe size={13}/> {t('DNS records')}</button>
              <button className="mini danger" disabled={!!loading} onClick={() => deleteMailDomain(d)} aria-label={t('Delete {name}', { name: d.domain })} title={t('Delete')}><Trash2 size={13}/></button>
            </span>
          </div>;
        })}
      </div>}
    </div>;
  }

  function renderMailDns() {
    const { domain, records, relay, hostedZone, dkim, dmarc, hosts, mtaSts } = mailDns;
    const statusLabel = { ok: t('Found'), missing: t('Missing'), different: t('Different'), unknown: t('Not checked') };
    const statusClass = { ok: 'ok', missing: 'bad', different: 'warn', unknown: '' };
    const titles = {
      mx: t('Receiving mail (MX)'),
      mail: t('Mail server address (A)'),
      spf: t('Allowed senders (SPF)'),
      dkim: t('Signature key (DKIM)'),
      'dkim-next': t('Next DKIM key (being published)'),
      'dkim-old': t('Previous DKIM key (retiring)'),
      dmarc: t('Policy (DMARC)'),
      webmail: t('Webmail address (optional)'),
      'mta-sts': t('MTA-STS policy host (optional)'),
      autoconfig: t('Thunderbird set-up host (optional)'),
      autodiscover: t('Outlook set-up host (optional)'),
      'srv-imaps': t('Where mail apps find IMAP (SRV)'),
      'srv-pop3s': t('Where mail apps find POP3 (SRV)'),
      'srv-submissions': t('Where mail apps find SMTP (SRV)'),
      'srv-submission': t('Where mail apps find SMTP with STARTTLS (SRV)'),
      'srv-autodiscover': t('Where Outlook finds its set-up (SRV)'),
      'mta-sts-txt': t('MTA-STS policy id (TXT)'),
      'tls-rpt': t('TLS reports (TLS-RPT)'),
    };
    const titleFor = record => titles[record.key] || t('Asked for by the relay {relay}', { relay: record.relay });
    return <div className="section mail-dns-page">
      <div className="section-title">
        <div className="waf-detail-title">
          <button className="secondary-light" onClick={() => setMailDns(null)}><ArrowLeft size={14}/> {t('Email')}</button>
          <div><h2>{t('DNS records for {domain}', { domain: domain.domain })}</h2>
            <p className="hint">{t('Add these at the DNS provider of {domain}. A change can take a few hours to be seen everywhere.', { domain: domain.domain })}</p></div>
        </div>
        <div className="actions">
          <button className="secondary-light" disabled={!!loading || records === null} onClick={() => openMailDns(domain)}><RefreshCw size={14}/> {t('Check again')}</button>
        </div>
      </div>
      {relay && <div className="mail-relay-card">
        <div className="mail-relay-card-head"><Send size={15}/><strong>{t('Outgoing mail')}</strong></div>
        {isAdmin && <select value={relay.choice} disabled={!!loading} aria-label={t('Outgoing mail')} onChange={e => saveDomainRelay(e.target.value)}>
          <option value="">{t('Server default')}</option>
          <option value="direct">{t('Direct, without a relay')}</option>
          {relay.options.map(option => <option key={option.id} value={option.id}>{t('Relay: {name}', { name: option.name })}</option>)}
        </select>}
        <p className="hint">{relay.effective_name
          ? t('Mail from {domain} leaves through the relay {relay}; the records it asks for are listed below.', { domain: domain.domain, relay: relay.effective_name })
          : t('Mail from {domain} is delivered directly from this server.', { domain: domain.domain })}</p>
      </div>}
      {hosts && <div className="mail-relay-card">
        <div className="mail-relay-card-head"><Lock size={15}/><strong>{t('Mail hosts and certificate')}</strong></div>
        <p className="hint">{t('Mail apps connect to {host}.', { host: hosts.client?.host || '—' })}{' '}
          {hosts.served?.length ? t('These names are served here with their own certificate, renewed on its own.') : t("Serve mail.{domain} with its own certificate so mail apps and other mail servers see a name that matches; mta-sts., autoconfig. and autodiscover. can share it.", { domain: domain.domain })}</p>
        <div className="mail-hosts-picks" style={{ display: 'flex', gap: '12px', flexWrap: 'wrap' }}>
          {(hosts.available || []).map(label => <label className="check-line" key={label}>
            <input type="checkbox" checked={label === 'mail' || hostsDraft.includes(label)} disabled={label === 'mail' || !!loading}
              onChange={e => setHostsDraft(prev => e.target.checked ? [...new Set([...prev, label])] : prev.filter(item => item !== label))} />
            <span>{label}.{domain.domain}{(hosts.served || []).includes(label) && <span className="badge ok">{t('Served')}</span>}</span>
          </label>)}
        </div>
        <div className="actions">
          <button className="secondary-light" disabled={!!loading} onClick={() => saveMailHosts(domain)}><Lock size={14}/> {hosts.served?.length ? t('Update the mail hosts') : t('Issue the certificate and serve')}</button>
          {hosts.served?.length > 0 && <button className="secondary-light" disabled={!!loading} onClick={() => saveMailHosts(domain, true)}><X size={14}/> {t('Stop serving')}</button>}
        </div>
      </div>}
      {dkim && <div className="mail-relay-card">
        <div className="mail-relay-card-head"><KeyRound size={15}/><strong>{t('DKIM key')}</strong></div>
        <p className="hint">{t('Mail from {domain} is signed with the selector {selector}.', { domain: domain.domain, selector: dkim.selector })}
          {' '}{dkim.rotate_days ? t('Keys rotate on their own every {n} days.', { n: dkim.rotate_days }) : ''}</p>
        {dkim.state === 'publishing' && <p className="hint">{t("A new key ({selector}) is published and waits for DNS: mail switches to it on its own once public DNS shows its record. Publish the record marked \"Next DKIM key\" where the domain's DNS is kept.", { selector: dkim.next_selector })}</p>}
        {dkim.state === 'retiring' && <p className="hint">{t('The previous key ({selector}) stays published until {date}, so mail already sent with it can still be verified; then it and its record go.', { selector: dkim.old_selector, date: dkim.retire_at ? new Date(dkim.retire_at + 'Z').toLocaleDateString() : '—' })}</p>}
        <div className="actions">
          {!dkim.state && <button className="secondary-light" disabled={!!loading} onClick={() => rotateMailDkim(domain)}><KeyRound size={14}/> {t('Rotate the DKIM key')}</button>}
          {dkim.state === 'publishing' && <>
            <button className="secondary-light" disabled={!!loading} onClick={() => advanceMailDkim(domain)}><RefreshCw size={14}/> {t('Check DNS and switch')}</button>
            <button className="secondary-light" disabled={!!loading} onClick={() => cancelMailDkim(domain)}><X size={14}/> {t('Cancel')}</button>
          </>}
          {dkim.state === 'retiring' && <button className="secondary-light" disabled={!!loading} onClick={() => advanceMailDkim(domain)}><Trash2 size={14}/> {t('Retire the previous key now')}</button>}
        </div>
      </div>}
      {mtaSts && <div className="mail-relay-card">
        <div className="mail-relay-card-head"><ShieldCheck size={15}/><strong>{t('MTA-STS and TLS reports')}</strong>{mtaSts.mode && <span className="badge ok">{mtaSts.mode}</span>}</div>
        <p className="hint">{t('MTA-STS tells other mail servers to deliver to mail.{domain} only over TLS with a certificate that matches; TLS reports tell you when that fails.', { domain: domain.domain })}</p>
        {!mtaSts.ready && <p className="hint">{t('Serve mail.{domain} and mta-sts.{domain} above first: the policy is published on mta-sts.{domain}.', { domain: domain.domain })}</p>}
        <div className="mail-settings-grid">
          <label className="field"><span className="field-label">{t('MTA-STS mode')}</span>
            <select value={mtaStsForm.mode} disabled={!mtaSts.ready && !mtaSts.mode} onChange={e => setMtaStsForm(prev => ({ ...prev, mode: e.target.value }))}>
              <option value="">{t('Off')}</option>
              <option value="testing">{t('testing: report only')}</option>
              <option value="enforce">{t('enforce: TLS required')}</option>
              <option value="none">{t('none: withdraw the policy')}</option>
            </select></label>
          <label className="field"><span className="field-label">{t('TLS reports to (rua)')}</span>
            <input value={mtaStsForm.tls_rpt} placeholder="tls-reports@example.com" spellCheck={false} onChange={e => setMtaStsForm(prev => ({ ...prev, tls_rpt: e.target.value }))} /></label>
        </div>
        {mtaSts.mode && <p className="hint">{t('Policy:')} <a href={mtaSts.policy_url} target="_blank" rel="noopener noreferrer">{mtaSts.policy_url}</a></p>}
        <div className="actions"><button className="secondary-light" disabled={!!loading} onClick={() => saveMailMtaSts(domain)}><Save size={14}/> {t('Save')}</button></div>
      </div>}
      {dmarc && dmarcForm && <div className="mail-relay-card">
        <div className="mail-relay-card-head"><ShieldCheck size={15}/><strong>{t('DMARC policy')}</strong>{!dmarc.custom && <span className="badge">{t('Default')}</span>}</div>
        <p className="hint">{t('What receiving servers do with mail that claims to be from {domain} but fails SPF and DKIM, and where they send their reports.', { domain: domain.domain })}</p>
        <div className="mail-settings-grid">
          <label className="field"><span className="field-label">{t('Policy (p)')}</span>
            <select value={dmarcForm.p} onChange={e => setDmarcForm(prev => ({ ...prev, p: e.target.value }))}>
              <option value="none">{t('none: only report')}</option>
              <option value="quarantine">{t('quarantine: to the spam folder')}</option>
              <option value="reject">{t('reject: refuse it')}</option>
            </select></label>
          <label className="field"><span className="field-label">{t('Subdomains (sp)')}</span>
            <select value={dmarcForm.sp || ''} onChange={e => setDmarcForm(prev => ({ ...prev, sp: e.target.value }))}>
              <option value="">{t('Same as the domain')}</option>
              <option value="none">none</option>
              <option value="quarantine">quarantine</option>
              <option value="reject">reject</option>
            </select></label>
          <label className="field"><span className="field-label">{t('Applies to (pct, %)')}</span>
            <input type="number" min="0" max="100" value={dmarcForm.pct ?? 100} onChange={e => setDmarcForm(prev => ({ ...prev, pct: e.target.value }))} /></label>
          <label className="field"><span className="field-label">{t('DKIM alignment (adkim)')}</span>
            <select value={dmarcForm.adkim || 'r'} onChange={e => setDmarcForm(prev => ({ ...prev, adkim: e.target.value }))}>
              <option value="r">{t('relaxed')}</option><option value="s">{t('strict')}</option>
            </select></label>
          <label className="field"><span className="field-label">{t('SPF alignment (aspf)')}</span>
            <select value={dmarcForm.aspf || 'r'} onChange={e => setDmarcForm(prev => ({ ...prev, aspf: e.target.value }))}>
              <option value="r">{t('relaxed')}</option><option value="s">{t('strict')}</option>
            </select></label>
          <label className="field"><span className="field-label">{t('Failure report options (fo)')}</span>
            <input value={dmarcForm.fo || ''} placeholder="1:d" spellCheck={false} onChange={e => setDmarcForm(prev => ({ ...prev, fo: e.target.value }))} /></label>
        </div>
        <label className="field"><span className="field-label">{t('Aggregate reports to (rua)')}</span>
          <input value={dmarcForm.rua || ''} placeholder="dmarc@example.com" spellCheck={false} onChange={e => setDmarcForm(prev => ({ ...prev, rua: e.target.value }))} /></label>
        <label className="field"><span className="field-label">{t('Failure reports to (ruf)')}</span>
          <input value={dmarcForm.ruf || ''} placeholder={t('optional')} spellCheck={false} onChange={e => setDmarcForm(prev => ({ ...prev, ruf: e.target.value }))} /></label>
        {renderCopyBlock(t('Record'), dmarc.record)}
        {(dmarc.external || []).map(item => <p className="hint" key={item.domain}>{t('Reports go to another domain, {target}: it has to allow them with this record.', { target: item.domain })} <code>{item.name} TXT "{item.value}"</code></p>)}
        <div className="actions">
          <button className="secondary-light" disabled={!!loading} onClick={() => saveMailDmarc(domain)}><Save size={14}/> {t('Save the DMARC policy')}</button>
          {dmarc.custom && <button className="secondary-light" disabled={!!loading} onClick={() => saveMailDmarc(domain, true)}><RotateCcw size={14}/> {t('Back to the default')}</button>}
        </div>
      </div>}
      {hostedZone && <div className="info-box dns-managed-box">
        <Network size={14}/>
        <span>{t('DNS Manager on this server holds the zone {zone} and keeps these records in it, taking back the ones email no longer needs. "In the zone" is what the zone holds; the other badge is what public DNS answers, which matches once the domain\'s nameservers point here.', { zone: hostedZone })}</span>
        <button type="button" className="mini secondary-light" onClick={() => navigateToPage('dns')}>{t('Open zone')}</button>
      </div>}
      {records === null && <p className="hint">{t('Checking DNS…')}</p>}
      {records && <div className="mail-dns-list">
        {records.map(record => <div className="mail-dns-record" key={record.key}>
          <div className="mail-dns-head">
            <strong>{titleFor(record)}</strong>
            <span className="mail-dns-type"><code>{record.type}</code>{record.priority != null && <small>{t('priority {n}', { n: record.priority })}</small>}</span>
            {hostedZone && record.in_zone != null && <span className={`badge mail-dns-zone ${record.in_zone ? 'ok' : 'warn'}`}
              title={t("DNS Manager's zone on this server")}>{record.in_zone ? t('In the zone') : t('Not in the zone')}</span>}
            <span className={`badge mail-dns-status ${statusClass[record.status] || ''}`} title={t('What public DNS answers now')}>{statusLabel[record.status] || record.status}</span>
          </div>
          {renderCopyBlock(t('Name'), record.name)}
          {renderCopyBlock(t('Value'), record.value, { multiline: record.key.startsWith('dkim') })}
          {record.status === 'different' && (record.found || []).length > 0 && <p className="hint">{t('Found now:')} <code>{record.found.join(' | ')}</code></p>}
          {record.key === 'webmail' && <p className="hint">{t('Only needed for webmail.{domain}; turn that on in the Domains tab once this record is in place.', { domain: domain.domain })}</p>}
        </div>)}
      </div>}
    </div>;
  }

  function renderMailRelays() {
    if (relayForm) return renderRelayForm();
    const data = mailRelays;
    const relays = data?.relays || [];
    return <div className="mail-tab">
      <p className="hint">{t('A relay (smarthost) sends this server\'s outgoing mail for it: needed where the provider blocks port 25, and it can help mail reach the inbox. Each domain uses the default relay unless its DNS page picks another one or direct delivery.')}</p>
      <div className="mail-toolbar">
        <label className="field mail-default-relay"><span className="field-label">{t('Default relay')}</span>
          <select value={data?.default_relay || ''} disabled={!data || !!loading} onChange={e => setDefaultRelay(e.target.value)}>
            <option value="">{t('None: deliver directly')}</option>
            {relays.map(relay => <option key={relay.id} value={relay.id}>{relay.name}</option>)}
          </select></label>
        <button type="button" onClick={() => editRelay(null)}><Plus size={15}/> {t('New relay')}</button>
      </div>
      {data === null && <p className="hint">{t('Loading…')}</p>}
      {data && relays.length === 0 && <EmptyState icon={Send} message={t('No relay yet: mail leaves this server directly.')} />}
      {relays.length > 0 && <div className="table">
        {relays.map(relay => <div className="row mail-relay-row" key={relay.id}>
          <span className="mail-row-name">
            <strong>{relay.name}{relay.default && <span className="badge ok">{t('Default')}</span>}</strong>
            <small>{relay.host}:{relay.port} · {relayTlsLabel(relay.tls)}{relay.username ? ` · ${relay.username}` : ` · ${t('no login')}`}</small>
          </span>
          <span className="mail-relay-meta">
            {relay.spf_include && <small><code>{relay.spf_include}</code></small>}
            {(relay.dns_records || []).length > 0 && <small>{t('{n} DNS records', { n: relay.dns_records.length })}</small>}
            {(relay.domains || []).length > 0 && <small>{t('Chosen by {domains}', { domains: relay.domains.join(', ') })}</small>}
          </span>
          <span className="row-actions">
            <button className="mini secondary-light" disabled={!!loading} onClick={() => editRelay(relay)}><Pencil size={13}/> {t('Edit')}</button>
            <button className="mini danger" disabled={!!loading} onClick={() => deleteRelay(relay)} aria-label={t('Delete {name}', { name: relay.name })} title={t('Delete')}><Trash2 size={13}/></button>
          </span>
        </div>)}
      </div>}
      <h3 className="mail-subhead">{t('Send a test message')}</h3>
      <div className="mail-relay-test">
        <input type="email" value={relayTestTo} onChange={e => setRelayTestTo(e.target.value)} placeholder="you@gmail.com" aria-label={t('Send a test message to')} />
        <button className="secondary-light" disabled={!!loading || !relayTestTo.includes('@')} onClick={testRelay}><Send size={14}/> {t('Send a test message')}</button>
      </div>
      <p className="hint">{t('Sent from postmaster at the server name, through the relay the default route uses. What Exim logged for it is shown, the receiving server\'s answer included.')}</p>
      {relayTestLines && <pre className="mail-log">{relayTestLines.length ? relayTestLines.join('\n') : t('Exim logged nothing more about it.')}</pre>}
    </div>;
  }

  function renderMailRspamd() {
    const stat = rspamdStat;
    const list = rspamdHistory;
    const pages = Math.max(1, Math.ceil((list?.total || 0) / (list?.per_page || 50)));
    const actionClass = { reject: 'bad', 'soft reject': 'warn', greylist: 'warn', 'add header': 'warn', 'rewrite subject': 'warn', 'no action': 'ok' };
    const cards = [['Scanned', stat?.scanned], ['Spam', stat?.spam], ['Ham', stat?.ham], ['Learned', stat?.learned]];
    const addressOf = text => (String(text || '').match(/[^\s<>"]+@[^\s<>"]+/) || [''])[0].toLowerCase();
    const f = mailSettingsForm;
    return <div className="mail-tab">
      <div className="mail-stat-grid">
        {cards.map(([label, value]) => <div className="mail-stat" key={label}><small>{t(label)}</small><strong>{value ?? '—'}</strong></div>)}
        {stat && Object.entries(stat.actions || {}).filter(([, count]) => count > 0).map(([action, count]) =>
          <div className="mail-stat" key={action}><small>{rspamdActionLabel(action)}</small><strong>{count}</strong></div>)}
      </div>
      <div className="segmented-control" role="tablist" aria-label="Rspamd">
        {[['history', 'Scan history'], ['log', 'Log'], ['allow', 'Whitelist & Blacklist']].map(([id, label]) => <button key={id} type="button" role="tab"
          aria-selected={rspamdView === id} className={rspamdView === id ? 'active' : ''} onClick={() => setRspamdView(id)}>{t(label)}</button>)}
      </div>
      {rspamdView === 'history' && <>
        <div className="mail-toolbar">
          <form className="mail-search" onSubmit={e => { e.preventDefault(); loadRspamdHistory(1); }}>
            <input value={rspamdFilter.q} placeholder={t('Sender, recipient, subject or IP')} aria-label={t('Search')} onChange={e => setRspamdFilter(prev => ({ ...prev, q: e.target.value }))} />
            <button type="submit" className="secondary-light icon-only" aria-label={t('Search')} title={t('Search')}><Search size={14}/></button>
          </form>
          <select value={rspamdFilter.action} aria-label={t('Result')} onChange={e => loadRspamdHistory(1, e.target.value)}>
            <option value="">{t('Every result')}</option>
            {['no action', 'add header', 'rewrite subject', 'greylist', 'soft reject', 'reject'].map(action => <option key={action} value={action}>{rspamdActionLabel(action)}</option>)}
          </select>
          <button type="button" className="secondary-light" disabled={!!loading} onClick={() => { loadRspamdStat(); loadRspamdHistory(rspamdFilter.page); }}><RefreshCw size={14}/> {t('Refresh')}</button>
        </div>
        {list === null && <p className="hint">{t('Loading…')}</p>}
        {list && list.items.length === 0 && <EmptyState icon={ShieldCheck} message={rspamdFilter.q || rspamdFilter.action ? t('No scanned message matches.') : t('No message has been scanned yet.')} />}
        {list && list.items.length > 0 && <div className="table">
          {list.items.map((item, index) => {
            const sender = addressOf(item.from) || addressOf(item.envelope_from);
            const senderDomain = sender.split('@')[1] || '';
            return <div className="row rspamd-row" key={`${item.time}-${index}`}>
              <span className="rspamd-when">
                <small>{formatFileTime(item.time)}</small>
                <span className={`badge ${actionClass[item.action] || ''}`}>{rspamdActionLabel(item.action)}</span>
              </span>
              <span className="mail-row-name">
                <strong title={item.subject}>{item.subject || t('(no subject)')}</strong>
                <small>{item.from || '—'} → {item.to.join(', ') || '—'}</small>
                {item.ip && <small>{item.ip}{item.user ? ` · ${t('signed in as {user}', { user: item.user })}` : ''}</small>}
                {item.allowed
                  ? <small className="rspamd-allowed">{t('This sender is on the allowlist.')}</small>
                  : item.blocked
                  ? <small className="badge danger" style={{ width: 'fit-content' }}>Đã chặn (Blacklist)</small>
                  : sender && <span className="rspamd-allow" style={{ display: 'inline-flex', gap: '6px', flexWrap: 'wrap', marginTop: '4px' }}>
                      {item.action !== 'no action' && (
                        <>
                          <button className="mini secondary-light" disabled={!!loading} onClick={() => allowMailSender(sender)}><Check size={12}/> {t('Allow {sender}', { sender })}</button>
                          {senderDomain && <button className="mini secondary-light" disabled={!!loading} onClick={() => allowMailSender(senderDomain)}><Check size={12}/> {t('Allow everyone at {domain}', { domain: senderDomain })}</button>}
                        </>
                      )}
                      <button className="mini danger" disabled={!!loading} onClick={() => blockMailSender(sender)}><Ban size={12}/> Chặn {sender}</button>
                      {senderDomain && <button className="mini danger" disabled={!!loading} onClick={() => blockMailSender(senderDomain)}><Ban size={12}/> Chặn @{senderDomain}</button>}
                    </span>}
              </span>
              <span className="rspamd-score"><strong>{item.score}</strong><small>/ {item.required}</small></span>
              <span className="rspamd-symbols">{item.symbols.slice(0, 10).map(symbol =>
                <code key={symbol.name} className={symbol.score > 0 ? 'pos' : symbol.score < 0 ? 'neg' : ''} title={String(symbol.score)}>{symbol.name}{symbol.score ? ` ${symbol.score > 0 ? '+' : ''}${symbol.score}` : ''}</code>)}</span>
            </div>;
          })}
        </div>}
        {pages > 1 && <div className="firewall-ip-pager">
          <button className="mini secondary-light" disabled={rspamdFilter.page <= 1} onClick={() => loadRspamdHistory(rspamdFilter.page - 1)}>{t('Previous')}</button>
          <span className="hint">{t('Page {page} of {pages}', { page: rspamdFilter.page, pages })}</span>
          <button className="mini secondary-light" disabled={rspamdFilter.page >= pages} onClick={() => loadRspamdHistory(rspamdFilter.page + 1)}>{t('Next')}</button>
        </div>}
        <p className="hint">{t('Rspamd keeps the last 2000 scans. A message sent by a signed-in mailbox is not scanned. A message stopped by a test pattern (GTUBE) is not kept.')}</p>
      </>}
      {rspamdView === 'log' && renderLogViewer(rspamdLog, rspamdLogQuery, setRspamdLogQuery, loadRspamdLog)}
      {rspamdView === 'allow' && (f ? <div className="create-inline mail-settings">
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))', gap: '16px' }}>
          <div>
            <strong style={{ display: 'block', marginBottom: '4px', color: 'var(--text-main)' }}>Danh sách cho phép (Sender / Domain Whitelist)</strong>
            <p className="hint">{t('One email address or domain per line. Mail from them is never blocked or sent to Junk: use it when the scan history shows a mistake.')}</p>
            <textarea className="mail-allow" rows={8} value={f.allow || ''} spellCheck={false} placeholder={'friend@example.com\nexample.org'}
              onChange={e => setMailSettingsForm(prev => ({ ...prev, allow: e.target.value }))} />
          </div>
          <div>
            <strong style={{ display: 'block', marginBottom: '4px', color: 'var(--text-main)' }}>Danh sách chặn vĩnh viễn (Sender / Domain Blacklist)</strong>
            <p className="hint">Mỗi dòng 1 địa chỉ email hoặc tên miền. Thư gửi đến từ danh sách này sẽ bị Rspamd từ chối thẳng (Reject) ở tầng SMTP.</p>
            <textarea className="mail-allow" rows={8} value={f.block || ''} spellCheck={false} placeholder={'spammer@bad-domain.com\nbad-domain.org'}
              onChange={e => setMailSettingsForm(prev => ({ ...prev, block: e.target.value }))} />
          </div>
        </div>
        <div className="actions"><button type="button" disabled={!!loading} onClick={saveMailSettings}><Save size={14}/> Lưu Whitelist & Blacklist</button></div>
      </div> : <p className="hint">{t('Loading…')}</p>)}
    </div>;
  }

  function renderMailServer() {
    const f = mailSettingsForm;
    const status = mailSettings?.status || {};
    const set = patch => setMailSettingsForm(prev => ({ ...prev, ...patch }));
    return <div className="mail-tab">
      {!f ? <p className="hint">{t('Loading…')}</p> : <div className="create-inline mail-settings">
        <div className="create-inline-head"><strong>{t('Mail server')} & Anti-Spam / Outbound Rate-Limiting</strong></div>
        <p className="hint">{t('Server name: {host}. Messages waiting to be sent: {queue}.', { host: mailSettings?.hostname || '—', queue: mailSettings?.queue ?? '—' })}</p>
        <div className="dns-server">
          {[['exim', 'Exim'], ['dovecot', 'Dovecot'], ['rspamd', 'Rspamd'], ['webmail', 'Webmail']].map(([key, label]) =>
            <span key={key} className={`badge ${status[key] ? 'ok' : key === 'rspamd' && !f.spam_enabled ? '' : 'bad'}`}>{status[key] ? t('{name} is running', { name: label }) : t('{name} is not running', { name: label })}</span>)}
          <span className={`badge ${status.outbound_smtp ? 'ok' : 'warn'}`}>{status.outbound_smtp ? t('Can send to other mail servers') : t('Outgoing port 25 is blocked')}</span>
        </div>
        {!status.outbound_smtp && <p className="hint">{t('This server cannot reach other mail servers on port 25, so mail to outside addresses waits in the queue and comes back after a few days. Ask the provider to open it, or send through a relay (Relays tab).')}</p>}
        {f.spam_enabled && status.resolver === 'system' && <p className="hint">{t("This server's provider lets DNS out only to its own resolvers, so Rspamd uses them; Spamhaus and other DNS blocklists refuse such resolvers, so those checks are off. The other checks still run.")}</p>}
        <div className="mail-settings-grid">
          <label className="field"><span className="field-label">{t('Recipients per mailbox per hour')} <em>{t('0 = no limit')}</em></span><input type="number" min="0" value={f.auth_rate_per_hour ?? 300} onChange={e => set({ auth_rate_per_hour: e.target.value })} /></label>
          <label className="field"><span className="field-label">{t('Messages per website account per hour')} <em>{t('0 = no limit')}</em></span><input type="number" min="0" value={f.local_rate_per_hour ?? 300} onChange={e => set({ local_rate_per_hour: e.target.value })} /></label>
          <label className="field"><span className="field-label">Giới hạn gửi ra theo Domain / giờ (Outbound Domain Limit) <em>0 = không giới hạn</em></span><input type="number" min="0" value={f.domain_rate_per_hour ?? 500} onChange={e => set({ domain_rate_per_hour: e.target.value })} /></label>
          <label className="field"><span className="field-label">{t('Largest message (MB)')}</span><input type="number" min="1" max="200" value={f.max_message_mb ?? 50} onChange={e => set({ max_message_mb: e.target.value })} /></label>
          <label className="field"><span className="field-label">{t('New mailbox size (MB)')}</span><input type="number" min="1" value={f.default_quota_mb ?? 1024} onChange={e => set({ default_quota_mb: e.target.value })} /></label>
          <label className="field"><span className="field-label">{t('Rotate DKIM keys every (days)')} <em>{t('0 = only by hand')}</em></span><input type="number" min="0" max="3650" value={f.dkim_rotate_days ?? 0} onChange={e => set({ dkim_rotate_days: e.target.value })} /></label>
          <label className="field"><span className="field-label">{t('Keep a replaced DKIM key published (days)')}</span><input type="number" min="1" max="60" value={f.dkim_retire_days ?? 7} onChange={e => set({ dkim_retire_days: e.target.value })} /></label>
          <label className="field"><span className="field-label">{t('Spam score: move to Junk')}</span><input type="number" min="1" max="100" step="0.5" value={f.spam_header_score ?? 6} disabled={!f.spam_enabled} onChange={e => set({ spam_header_score: e.target.value })} /></label>
          <label className="field"><span className="field-label">Spam score: gắn nhãn tiêu đề (Rewrite Subject)</span><input type="number" min="1" max="100" step="0.5" value={f.spam_rewrite_score ?? 8} disabled={!f.spam_enabled} onChange={e => set({ spam_rewrite_score: e.target.value })} /></label>
          <label className="field"><span className="field-label">{t('Spam score: refuse')}</span><input type="number" min="1" max="100" step="0.5" value={f.spam_reject_score ?? 15} disabled={!f.spam_enabled} onChange={e => set({ spam_reject_score: e.target.value })} /></label>
          <label className="field"><span className="field-label">Nhãn tiêu đề Spam (Subject Tag)</span><input type="text" maxLength={32} value={f.spam_subject_tag ?? '[SPAM]'} disabled={!f.spam_enabled} onChange={e => set({ spam_subject_tag: e.target.value })} /></label>
        </div>
        <label className="field" style={{ marginTop: '8px' }}>
          <span className="field-label">Giới hạn gửi SMTP tùy chỉnh cho từng Domain (Per-Domain Outbound Rate Limit — định dạng <code>domain.com=200</code> mỗi dòng)</span>
          <textarea rows={3} value={f.domain_rate_limits_text || ''} spellCheck={false} placeholder={'example.com=200\nnewsletter.example.org=1000'} onChange={e => set({ domain_rate_limits_text: e.target.value })} />
        </label>
        <label className="check-line"><input type="checkbox" checked={f.auto_hold_on_rate_exceed !== false} onChange={e => set({ auto_hold_on_rate_exceed: e.target.checked })} />
          Tự động tạm giữ hàng đợi (Auto-Hold Queue) và cảnh báo khi tài khoản/domain vượt ngưỡng gửi mail mỗi giờ để bảo vệ uy tín IP máy chủ</label>
        <label className="check-line"><input type="checkbox" checked={!!f.spam_enabled} onChange={e => set({ spam_enabled: e.target.checked })} />
          {t('Spam filter (Rspamd) for mail from outside')}</label>
        <label className="check-line"><input type="checkbox" checked={!!f.greylisting} disabled={!f.spam_enabled} onChange={e => set({ greylisting: e.target.checked })} />
          {t('Greylisting: doubtful senders are asked to retry a few minutes later')}</label>
        <div className="actions"><button type="button" disabled={!!loading} onClick={saveMailSettings}><Save size={14}/> {t('Apply mail settings')}</button></div>
      </div>}
      <h3 className="mail-subhead">{t('Exim log')}</h3>
      {renderLogViewer(eximLog, eximLogQuery, setEximLogQuery, loadEximLog)}
    </div>;
  }

  function renderMailClientHelp() {
    const client = mailInfo?.client || {};
    return <div className="section">
      <div className="section-title">
        <div><h2>{t('Mail app settings')}</h2>
          <p className="hint">{t("For Outlook, Thunderbird, Apple Mail or a phone. The username is the full email address, the password the mailbox's own.")}</p></div>
      </div>
      <div className="sftp-connect-grid">
        {renderCopyBlock(t('Server (IMAP, POP3 and SMTP)'), client.host || '—')}
      </div>
      <ul className="mail-client-ports">
        <li><strong>IMAP</strong> {t('port {port}, SSL/TLS', { port: client.imap_port || 993 })}</li>
        <li><strong>POP3</strong> {t('port {port}, SSL/TLS', { port: client.pop3_port || 995 })}</li>
        <li><strong>SMTP</strong> {t('port {port}, SSL/TLS — or {submission} with STARTTLS', { port: client.smtp_port || 465, submission: client.submission_port || 587 })}</li>
      </ul>
    </div>;
  }

  function renderMailModals() {
    return <>
      {mailboxEdit && <div className="modal-overlay" onClick={() => setMailboxEdit(null)}>
        <div className="modal-card" onClick={e => e.stopPropagation()}>
          <div className="modal-header">
            <h3>{mailboxEdit.box.address}</h3>
            <button className="secondary-light" onClick={() => setMailboxEdit(null)} aria-label={t('Close')}><X size={16}/></button>
          </div>
          <div className="modal-body">
            <label className="field"><span className="field-label">{t('New password')} <small>{t('(leave empty to keep)')}</small></span>
              <div className="password-with-generate">
                <input value={mailboxEdit.password} autoComplete="new-password" spellCheck={false} onChange={e => setMailboxEdit(prev => ({ ...prev, password: e.target.value }))} data-lpignore="true" data-1p-ignore="true" />
                <button type="button" className="secondary-light icon-only" title={t('Generate random password')} aria-label={t('Generate random password')} onClick={() => setMailboxEdit(prev => ({ ...prev, password: mailPassword() }))}><Dices size={15}/></button>
                <button type="button" className="secondary-light icon-only" title={t('Copy')} aria-label={t('Copy')} onClick={() => copyText(mailboxEdit.password, t('Copied.'))}><Copy size={15}/></button>
              </div>
            </label>
            <label className="field"><span className="field-label">{t('Size (MB)')}{isAdmin && <em> {t('0 = unlimited')}</em>}</span>
              <input type="number" min={isAdmin ? 0 : 1} value={mailboxEdit.quota_mb} onChange={e => setMailboxEdit(prev => ({ ...prev, quota_mb: e.target.value }))} />
            </label>
            <p className="hint">{t('A new password takes effect at once: mail apps using the old one must be updated.')}</p>
          </div>
          <div className="modal-actions">
            <button className="secondary-light" onClick={() => setMailboxEdit(null)}>{t('Cancel')}</button>
            <button disabled={!!loading || (mailboxEdit.password !== '' && (mailboxEdit.password.length < 8 || !/[A-Za-z]/.test(mailboxEdit.password) || !/\d/.test(mailboxEdit.password)))} onClick={saveMailboxEdit}><Save size={14}/> {t('Save')}</button>
          </div>
        </div>
      </div>}
      {queueDetail && <div className="modal-overlay" onClick={() => setQueueDetail(null)}>
        <div className="modal-card" onClick={e => e.stopPropagation()}>
          <div className="modal-header">
            <h3><code>{queueDetail.item.id}</code></h3>
            <button className="secondary-light" onClick={() => setQueueDetail(null)} aria-label={t('Close')}><X size={16}/></button>
          </div>
          <div className="modal-body"><pre className="mail-log">{queueDetail.headers || '—'}</pre></div>
          <div className="modal-actions">
            <button className="secondary-light" onClick={() => setQueueDetail(null)}>{t('Close')}</button>
            <button className="danger" disabled={!!loading} onClick={() => queueAction('delete', [queueDetail.item.id])}><Trash2 size={14}/> {t('Delete')}</button>
          </div>
        </div>
      </div>}
      {forwarderEdit && <div className="modal-overlay" onClick={() => setForwarderEdit(null)}>
        <div className="modal-card" onClick={e => e.stopPropagation()}>
          <div className="modal-header">
            <h3>{forwarderEdit.item.address}</h3>
            <button className="secondary-light" onClick={() => setForwarderEdit(null)} aria-label={t('Close')}><X size={16}/></button>
          </div>
          <div className="modal-body">
            <label className="field"><span className="field-label">{t('Forward to')}</span>
              <textarea rows={3} value={forwarderEdit.destinations} spellCheck={false} onChange={e => setForwarderEdit(prev => ({ ...prev, destinations: e.target.value }))} />
            </label>
            <p className="hint">{t('One or more addresses, separated by commas.')}</p>
          </div>
          <div className="modal-actions">
            <button className="secondary-light" onClick={() => setForwarderEdit(null)}>{t('Cancel')}</button>
            <button disabled={!!loading || splitAddresses(forwarderEdit.destinations).length === 0} onClick={saveForwarderEdit}><Save size={14}/> {t('Save')}</button>
          </div>
        </div>
      </div>}
    </>;
  }

  function renderMail() {
    const info = mailInfo;
    if (!info) return <div className="section"><h2>{t('Email')}</h2><p className="hint">{t('Loading…')}</p></div>;
    if (mailDns) return <>{renderMailDns()}{renderMailModals()}</>;
    const domains = info.domains || [];
    const limit = info.mailbox_limit;
    const tabs = [
      ['mailboxes', 'Mailboxes', Inbox], ['domains', 'Domains', Globe],
      // Server-wide: relays, the spam filter and the mail server itself.
      ...(isAdmin ? [['relay', 'Relays', Send], ['rspamd', 'Rspamd', ShieldCheck], ['queue', 'Queue', Inbox], ['server', 'Server', Server]] : []),
    ];
    const serverTabs = ['relay', 'rspamd', 'queue', 'server'];
    const activeTab = domains.length || serverTabs.includes(mailTab) ? mailTab : 'domains';
    return <>
      <div className="section mail-page">
        <div className="section-title">
          <div><h2>{t('Email')}</h2>
            <p className="hint">{isAdmin
              ? t('Mailboxes, forwarders and DNS records of every mail domain on this server.')
              : limit ? t('{used} of {limit} mailboxes used.', { used: info.mailbox_count, limit }) : t('{n} mailboxes.', { n: info.mailbox_count })}</p></div>
          <div className="actions">
            <button type="button" className="secondary-light" onClick={() => window.open(info.webmail_url, '_blank', 'noopener,noreferrer')}><ExternalLink size={14}/> {t('Webmail')}</button>
            <button type="button" className="secondary-light" disabled={!!loading} onClick={refreshMail}><RefreshCw size={14}/> {t('Refresh')}</button>
          </div>
        </div>
        <div className="segmented-control backup-tabs mail-tabs" role="tablist" aria-label={t('Email sections')}>
          {tabs.map(([id, label, Icon]) => <button key={id} type="button" role="tab" aria-selected={activeTab === id}
            className={activeTab === id ? 'active' : ''} disabled={!domains.length && !['domains', ...serverTabs].includes(id)}
            onClick={() => { setMailTab(id); setMailPage(1); }}><Icon size={14}/>{t(label)}</button>)}
        </div>
        {activeTab === 'mailboxes' && renderMailboxes()}
        {activeTab === 'forwarders' && renderForwarders()}
        {activeTab === 'domains' && renderMailDomains()}
        {activeTab === 'relay' && renderMailRelays()}
        {activeTab === 'rspamd' && renderMailRspamd()}
        {activeTab === 'queue' && renderMailQueue()}
        {activeTab === 'server' && renderMailServer()}
      </div>
      {domains.length > 0 && !serverTabs.includes(activeTab) && renderMailClientHelp()}
      {renderMailModals()}
    </>;
  }
  return renderMail();
}
