import React, { useState, useEffect, useRef } from 'react';
import { Archive, ArchiveRestore, ArrowLeft, Ban, Bot, Boxes, Check, ChevronDown, Clock, Code2, Copy, Cpu, Database, Dices, ExternalLink, Eye, FileText, FolderOpen, Globe, HardDrive, Home, Image, KeyRound, Lock, LogIn, LogOut, MemoryStick, Menu, Moon, MoveRight, Network, Pencil, Save, Search, Server, Settings as SettingsIcon, Shield, Sun, Trash2, TerminalIcon, Users, X, RefreshCw, Plus, Download, Upload, Play, Square, RotateCcw, AlertCircle, Activity, BrickWall, Bug, LockKeyhole, PackageOpen, ScrollText, ShieldAlert, CheckCircle, Zap, Bell, Mail, Send, Cloud, Inbox, Forward, ShieldCheck } from 'lucide-react';
import { EmptyState } from '../components/EmptyState.jsx';
import { useAppContext } from '../contexts/AppContext.jsx';
import { t, useLanguage } from '../i18n.js';
import { WebsiteSelect } from '../components/WebsiteSelect.jsx';

export function SslPage({ request, loadCfZone, refreshAll, enableSsl }) {
  const {
    loading, setLoading, setError, setNotice, selectedWebsiteId, setSelectedWebsiteId, websites,
    cfZone, setCfZone, wildcardToken, setWildcardToken, sharedSource, setSharedSource,
    cfApplying, cfDnsCreating, cfScanError, cfScanning, cfZones,
  } = useAppContext();
  const [language] = useLanguage();
  const currentSite = websites.find(site => String(site.id) === String(selectedWebsiteId));
  
      const [sslMode, setSslMode] = useState('letsencrypt');
  const [manualSslForm, setManualSslForm] = useState({ certificate: '', private_key: '', ca_bundle: '' });
  const [manualSslFiles, setManualSslFiles] = useState({ certificate: null, private_key: null, ca_bundle: null });
  const [sslSources, setSslSources] = useState([]);
  
  const manualSslCertRef = useRef(null);
  const manualSslKeyRef = useRef(null);
  const manualSslCaRef = useRef(null);

  useEffect(() => { 
    if (selectedWebsiteId) { 
      loadSslSources(selectedWebsiteId); 
    } 
  }, [selectedWebsiteId]);

  
  async function installManualSsl() {
    if (!selectedWebsiteId) return;
    const hasCert = manualSslFiles.certificate || manualSslForm.certificate.trim();
    const hasKey = manualSslFiles.private_key || manualSslForm.private_key.trim();
    if (!hasCert || !hasKey) {
      setError(t('Certificate and private key are required.'));
      return;
    }
    const form = new FormData();
    if (manualSslFiles.certificate) form.append('certificate', manualSslFiles.certificate);
    else form.append('certificate_text', manualSslForm.certificate);
    if (manualSslFiles.private_key) form.append('private_key', manualSslFiles.private_key);
    else form.append('private_key_text', manualSslForm.private_key);
    if (manualSslFiles.ca_bundle) form.append('ca_bundle', manualSslFiles.ca_bundle);
    else if (manualSslForm.ca_bundle.trim()) form.append('ca_bundle_text', manualSslForm.ca_bundle);
    const data = await request(`/websites/${selectedWebsiteId}/ssl/manual`, { method: 'POST', body: form }, t('Installing manual SSL...'));
    if (data) {
      setManualSslForm({ certificate: '', private_key: '', ca_bundle: '' });
      setManualSslFiles({ certificate: null, private_key: null, ca_bundle: null });
      refreshAll();
    }
  }

  async function loadSslSources(id) {
    const data = await request(`/websites/${id}/ssl/sources`, { silent: true });
    setSslSources(Array.isArray(data) ? data : []);
  }

  async function installWildcardSsl() {
    if (!selectedWebsiteId) return;
    const body = {};
    if (wildcardToken.trim()) body.cloudflare_api_token = wildcardToken.trim();
    else if (!cfZone.has_token) { setError(t('Paste a Cloudflare API token (Zone.DNS Edit).')); return; }
    const data = await request(`/websites/${selectedWebsiteId}/ssl/wildcard`,
      { method: 'POST', body: JSON.stringify(body) }, t('Issuing wildcard certificate via Cloudflare...'));
    if (data) {
      setWildcardToken('');
      setNotice(`Wildcard SSL active — *.${data.ssl_source_domain} covers this site.`);
      refreshAll();
    }
  }

  async function installSharedSsl() {
    if (!selectedWebsiteId || !sharedSource) return;
    const data = await request(`/websites/${selectedWebsiteId}/ssl/shared`,
      { method: 'POST', body: JSON.stringify({ source_domain: sharedSource }) },
      `Using ${sharedSource}'s certificate...`);
    if (data) {
      setNotice(`Now serving ${sharedSource}'s certificate.`);
      refreshAll();
    }
  }

  function renderSsl() {
    const sslLabels = {
      manual: 'Manual SSL', cloudflare: 'Wildcard (Cloudflare)', shared: `Using ${currentSite?.ssl_source_domain || ''}`,
    };
    const sslLabel = currentSite?.ssl_enabled
      ? (sslLabels[currentSite?.ssl_mode] || 'SSL Enabled')
      : 'SSL Disabled';
    const locale = language === 'vi' ? 'vi-VN' : 'en-GB';
    const sslUpdated = currentSite?.ssl_updated_at ? new Date(currentSite.ssl_updated_at).toLocaleString(locale) : '';
    // Every site on one list, as OPanel has it: the unsecured first, then the
    // rest by name, each a click from the form above.
    const sslSites = [...websites].sort((a, b) => (a.ssl_enabled === b.ssl_enabled
      ? String(a.domain).localeCompare(String(b.domain)) : a.ssl_enabled ? 1 : -1));
    const securedCount = websites.filter(site => site.ssl_enabled).length;
    const siteSslLabel = site => !site.ssl_enabled ? t('No SSL')
      : site.ssl_mode === 'manual' ? t('Manual SSL')
        : site.ssl_mode === 'shared' ? t('Shared cert')
          : site.ssl_mode === 'cloudflare' ? t('Wildcard') : 'SSL OK';
    return <>
    <div className="section" id="ssl-manage">
      <h2>{t('SSL Certificate')}</h2>
      <WebsiteSelect />
      {currentSite && <div className="info-box" style={{marginTop:8}}>
        <strong>{currentSite.domain}</strong>
        <span className={currentSite.ssl_enabled ? 'badge ok' : 'badge'} style={{justifySelf:'start'}}>{t(sslLabel)}</span>
        {sslUpdated && <span className="hint">{t('Updated')} {sslUpdated}</span>}
        {currentSite.ssl_mode === 'manual' && currentSite.ssl_has_ca && <span className="badge ok" style={{justifySelf:'start'}}>{t('CA Bundle')}</span>}
      </div>}
      <div className="segmented ssl-mode-tabs">
        <button className={sslMode === 'letsencrypt' ? 'active' : ''} onClick={() => setSslMode('letsencrypt')}><Lock size={14}/> Let's Encrypt</button>
        <button className={sslMode === 'manual' ? 'active' : ''} onClick={() => setSslMode('manual')}><KeyRound size={14}/>{t('Manual')}</button>
        <button className={sslMode === 'wildcard' ? 'active' : ''} onClick={() => setSslMode('wildcard')}><Globe size={14}/>{t('Wildcard (Cloudflare)')}</button>
        <button className={sslMode === 'shared' ? 'active' : ''} onClick={() => setSslMode('shared')}><Copy size={14}/>{t('Use existing')}</button>
      </div>
      {sslMode === 'letsencrypt' && <>
        <button className="ssl-install" disabled={!selectedWebsiteId || !!loading} onClick={() => enableSsl(selectedWebsiteId)}><Lock size={15}/>{t('Install / Renew SSL')}</button>
        <p className="hint">{t('The domain must point to the correct VPS IP before issuing SSL.')}</p>
      </>}
      {sslMode === 'wildcard' && <div className="ssl-sub-form">
        <p className="hint">{t('Issues')}{' '}<code>{cfZone.zone ? `${cfZone.zone} + *.${cfZone.zone}` : 'zone + *.zone'}</code> over
          Cloudflare DNS. Needs an API token with <strong>{t('Zone → DNS → Edit')}</strong> for the zone.
        </p>
        {cfZone.has_token
          ? <p className="hint">✓ Token saved for <strong>{cfZone.zone}</strong>. Leave the field blank to reuse it.</p>
          : null}
        <input type="password" autoComplete="off" placeholder={t('Cloudflare API token')}
          value={wildcardToken} onChange={e => setWildcardToken(e.target.value)} />
        <button disabled={!selectedWebsiteId || !!loading} onClick={installWildcardSsl}>
          <Globe size={15}/>{t('Issue wildcard certificate')}</button>
      </div>}
      {sslMode === 'shared' && <div className="ssl-sub-form">
        <p className="hint">{t('Point this site at another spanel website\'s certificate (e.g. a wildcard). No new certificate is issued.')}</p>
        {sslSources.length === 0
          ? <p className="hint">{t('No other website has a certificate that covers')}{' '}<strong>{currentSite?.domain}</strong>.</p>
          : <>
            <select value={sharedSource} onChange={e => setSharedSource(e.target.value)}>
              <option value="">{t('Select a source website…')}</option>
              {sslSources.map(s => <option key={s.domain} value={s.domain}>
                {s.domain}{s.wildcard ? ' (wildcard)' : ''}{s.not_after ? ` — expires ${s.not_after}` : ''}
              </option>)}
            </select>
            <button disabled={!selectedWebsiteId || !sharedSource || !!loading} onClick={installSharedSsl}>
              <Copy size={15}/>{t('Use this certificate')}</button>
          </>}
      </div>}
      {sslMode === 'manual' && <div className="manual-ssl-grid">
        <label>{t('Certificate (.crt/.pem)')}<input type="file" accept=".crt,.pem" onChange={e => setManualSslFiles(prev => ({ ...prev, certificate: e.target.files?.[0] || null }))} />
        </label>
        <label>{t('Private key (.key/.pem)')}<input type="file" accept=".key,.pem" onChange={e => setManualSslFiles(prev => ({ ...prev, private_key: e.target.files?.[0] || null }))} />
        </label>
        <label>{t('CA bundle (.ca/.crt/.pem)')}<input type="file" accept=".ca,.crt,.pem" onChange={e => setManualSslFiles(prev => ({ ...prev, ca_bundle: e.target.files?.[0] || null }))} />
        </label>
        <textarea rows={7} disabled={!!manualSslFiles.certificate} value={manualSslForm.certificate} onChange={e => setManualSslForm(prev => ({ ...prev, certificate: e.target.value }))} placeholder="-----BEGIN CERTIFICATE-----" />
        <textarea rows={7} disabled={!!manualSslFiles.private_key} value={manualSslForm.private_key} onChange={e => setManualSslForm(prev => ({ ...prev, private_key: e.target.value }))} placeholder="-----BEGIN PRIVATE KEY-----" />
        <textarea rows={7} disabled={!!manualSslFiles.ca_bundle} value={manualSslForm.ca_bundle} onChange={e => setManualSslForm(prev => ({ ...prev, ca_bundle: e.target.value }))} placeholder={t('Optional CA bundle')} />
        <button className="manual-ssl-submit" disabled={!selectedWebsiteId || !!loading} onClick={installManualSsl}><Upload size={15}/>{t('Install Manual SSL')}</button>
      </div>}
    </div>
    {websites.length > 0 && <div className="section">
      <div className="section-title">
        <div><h2>{t('All websites')}</h2><p className="hint">{t('{secured} of {total} secured', { secured: securedCount, total: websites.length })}</p></div>
      </div>
      <div className="table ssl-overview">
        {sslSites.map(site => <div className={`row ssl-overview-row${String(site.id) === String(selectedWebsiteId) ? ' selected' : ''}`} key={site.id}>
          <span className="ssl-overview-domain"><strong>{site.domain}</strong>{site.ssl_updated_at && <small>{t('Updated')} {new Date(site.ssl_updated_at).toLocaleDateString(locale)}</small>}</span>
          <span className={`badge ${site.ssl_enabled ? 'ok' : 'warn'}`}>{siteSslLabel(site)}</span>
          <button type="button" className="mini secondary" onClick={() => { setSelectedWebsiteId(String(site.id)); document.getElementById('ssl-manage')?.scrollIntoView({ behavior: 'smooth', block: 'start' }); }}>{site.ssl_enabled ? t('Manage') : t('Set up SSL')}</button>
        </div>)}
      </div>
    </div>}
    </>;
  }
  return renderSsl();
}
