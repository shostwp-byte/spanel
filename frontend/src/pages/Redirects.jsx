import React, { useState, useEffect } from 'react';
import {
  MoveRight, Globe, Plus, Trash2, RefreshCw, Lock,
  Layers, CheckCircle, AlertCircle, Code2, Sparkles
} from 'lucide-react';
import { useAppContext } from '../contexts/AppContext.jsx';
import { t } from '../i18n.js';
import { WebsiteSelect } from '../components/WebsiteSelect.jsx';

const REWRITE_LINE_RE = /^\s*rewrite\s+(\S+)\s+(\S+)(?:\s+(last|break))?\s*;\s*$/i;

function parseCustomNginxRules(rawText = '') {
  const lines = String(rawText || '').replace(/\r\n/g, '\n').split('\n');
  const rules = [];
  const otherLines = [];
  lines.forEach((line, idx) => {
    const trimmed = line.trim();
    if (trimmed === '# spanel URI REDIRECT') return;
    const match = line.match(REWRITE_LINE_RE);
    if (match) {
      rules.push({
        id: `rule-${idx}`,
        pattern: match[1],
        target: match[2],
        flag: (match[3] || 'last').toLowerCase(),
        raw: line.trim(),
      });
    } else {
      otherLines.push(line);
    }
  });
  return {
    rules,
    otherContent: otherLines.join('\n').trim(),
  };
}

function serializeCustomNginxRules(rules, otherContent) {
  const ruleLines = rules.map(r => `rewrite ${r.pattern} ${r.target} ${r.flag || 'last'};`);
  const parts = [];
  if (otherContent && otherContent.trim()) {
    parts.push(otherContent.trim());
  }
  if (ruleLines.length > 0) {
    parts.push(ruleLines.join('\n'));
  }
  return parts.join('\n\n');
}

export function RedirectsPage({ request }) {
  const {
    websites = [], selectedWebsiteId, setSelectedWebsiteId,
    loading, setLoading, setNotice, setError, navigateToPage
  } = useAppContext();

  const [activeTab, setActiveTab] = useState('domain');
  const [aliases, setAliases] = useState([]);
  const [domainForm, setDomainForm] = useState({ domain: '', mode: 'redirect' });

  const [uriRules, setUriRules] = useState([]);
  const [otherNginxCustom, setOtherNginxCustom] = useState('');
  const [uriForm, setUriForm] = useState({ sourcePath: '/old-page', targetUri: '/new-page', flag: 'last', exact: true });

  const selectedSite = websites.find(w => String(w.id) === String(selectedWebsiteId)) || websites[0] || null;

  useEffect(() => {
    if (!selectedWebsiteId && websites.length > 0) {
      setSelectedWebsiteId(String(websites[0].id));
    }
  }, [websites, selectedWebsiteId]);

  useEffect(() => {
    if (selectedSite?.id) {
      loadRedirectData(selectedSite.id);
    } else {
      setAliases([]);
      setUriRules([]);
      setOtherNginxCustom('');
    }
  }, [selectedSite?.id]);

  async function loadRedirectData(siteId = selectedSite?.id) {
    if (!siteId) return;
    setLoading(t('Loading site redirection rules...'));
    try {
      const [aliasRes, customRes] = await Promise.all([
        request(`/websites/${siteId}/aliases`, { silent: true }),
        request(`/websites/${siteId}/nginx-custom`, { silent: true }),
      ]);
      if (Array.isArray(aliasRes)) {
        setAliases(aliasRes);
      }
      const parsed = parseCustomNginxRules(customRes?.nginx_custom || '');
      setUriRules(parsed.rules);
      setOtherNginxCustom(parsed.otherContent);
    } finally {
      setLoading('');
    }
  }

  async function handleAddDomainRedirect(e) {
    e.preventDefault();
    if (!selectedSite) return;
    const cleanDomain = domainForm.domain.trim().toLowerCase().replace(/^https?:\/\//, '').replace(/\/.*$/, '');
    if (!cleanDomain) {
      setError(t('Please enter a valid domain or subdomain name.'));
      return;
    }
    const res = await request(
      `/websites/${selectedSite.id}/aliases`,
      {
        method: 'POST',
        body: JSON.stringify({ domain: cleanDomain, mode: domainForm.mode }),
      },
      t('Configuring domain redirection in Nginx...')
    );
    if (res) {
      setNotice(
        domainForm.mode === 'redirect'
          ? t('Added 301 permanent redirect from {src} to {dst}.', { src: cleanDomain, dst: selectedSite.domain })
          : t('Added domain alias {src} pointing to {dst}.', { src: cleanDomain, dst: selectedSite.domain })
      );
      setDomainForm({ domain: '', mode: 'redirect' });
      loadRedirectData(selectedSite.id);
    }
  }

  async function handleDeleteDomainRedirect(alias) {
    if (!selectedSite) return;
    if (!window.confirm(t('Remove domain rule for {d}?', { d: alias.domain }))) return;
    const res = await request(
      `/websites/${selectedSite.id}/aliases/${alias.id}`,
      { method: 'DELETE' },
      t('Removing domain rule...')
    );
    if (res) {
      setNotice(t('Removed domain rule for {d}.', { d: alias.domain }));
      loadRedirectData(selectedSite.id);
    }
  }

  async function handleAddUriRule(e) {
    e.preventDefault();
    if (!selectedSite) return;
    let src = uriForm.sourcePath.trim();
    let dst = uriForm.targetUri.trim();
    if (!src.startsWith('^') && !src.startsWith('/')) {
      src = '/' + src;
    }
    if (!dst.startsWith('/') || dst.startsWith('//') || dst.includes('://') || dst.includes('..')) {
      setError(t('Destination URI must be a local path starting with "/" (e.g. /new-page or /index.php).'));
      return;
    }
    const pattern = uriForm.exact && !src.startsWith('^')
      ? `^${src.replace(/\$$/, '')}$`
      : src;

    if ( /\s/.test(pattern) || /\s/.test(dst) ) {
      setError(t('Source pattern and destination URI cannot contain spaces.'));
      return;
    }

    const nextRules = [
      ...uriRules,
      {
        id: `rule-${Date.now()}`,
        pattern,
        target: dst,
        flag: uriForm.flag === 'break' ? 'break' : 'last',
      },
    ];
    const nextNginxCustom = serializeCustomNginxRules(nextRules, otherNginxCustom);
    const res = await request(
      `/websites/${selectedSite.id}/nginx-custom`,
      {
        method: 'PUT',
        body: JSON.stringify({ nginx_custom: nextNginxCustom }),
      },
      t('Applying URI rewrite rule and reloading Nginx...')
    );
    if (res) {
      setNotice(t('URI rewrite rule added and Nginx reloaded.'));
      setUriForm({ sourcePath: '/old-page', targetUri: '/new-page', flag: 'last', exact: true });
      loadRedirectData(selectedSite.id);
    }
  }

  async function handleDeleteUriRule(ruleId) {
    if (!selectedSite) return;
    const nextRules = uriRules.filter(r => r.id !== ruleId);
    const nextNginxCustom = serializeCustomNginxRules(nextRules, otherNginxCustom);
    const res = await request(
      `/websites/${selectedSite.id}/nginx-custom`,
      {
        method: 'PUT',
        body: JSON.stringify({ nginx_custom: nextNginxCustom }),
      },
      t('Removing URI rewrite rule...')
    );
    if (res) {
      setNotice(t('URI rewrite rule removed.'));
      loadRedirectData(selectedSite.id);
    }
  }

  const redirectAliases = aliases.filter(a => a.mode === 'redirect');
  const mirrorAliases = aliases.filter(a => a.mode !== 'redirect');

  return (
    <div className="section">
      <div className="section-title" style={{ marginBottom: '14px' }}>
        <div>
          <h2 style={{ display: 'flex', alignItems: 'center', gap: '10px', margin: 0 }}>
            <MoveRight size={19} />
            <span>{t('Site Redirection & URL Forwarding')}</span>
          </h2>
          <p className="hint" style={{ marginTop: '4px' }}>
            {t('Configure 301 permanent domain redirects, domain pointers, and internal URI path rewrite rules.')}
          </p>
        </div>
        <div className="cb-toolbar-actions">
          <WebsiteSelect
            value={selectedSite ? String(selectedSite.id) : ''}
            websites={websites}
            onChange={val => setSelectedWebsiteId(val)}
          />
          <button
            type="button"
            className="cb-btn secondary"
            disabled={!selectedSite || !!loading}
            onClick={() => loadRedirectData(selectedSite?.id)}
          >
            <RefreshCw size={14} /> {t('Refresh')}
          </button>
        </div>
      </div>

      {/* KPI Strip */}
      <div className="cb-stats-strip">
        <div className="cb-stat-box">
          <div className="cb-stat-icon blue"><Globe size={18} /></div>
          <div className="cb-stat-info">
            <span className="cb-stat-label">{t('Primary Destination')}</span>
            <strong>
              {selectedSite ? selectedSite.domain : t('No Website')}
              <small className="cb-stat-sub">{selectedSite?.ssl_enabled ? 'HTTPS (SSL)' : 'HTTP'}</small>
            </strong>
          </div>
        </div>
        <div className="cb-stat-box">
          <div className="cb-stat-icon emerald"><MoveRight size={18} /></div>
          <div className="cb-stat-info">
            <span className="cb-stat-label">{t('301 Domain Redirects')}</span>
            <strong>
              {redirectAliases.length} {t('Domains')}
              <small className="cb-stat-sub">return 301 https://...</small>
            </strong>
          </div>
        </div>
        <div className="cb-stat-box">
          <div className="cb-stat-icon indigo"><Layers size={18} /></div>
          <div className="cb-stat-info">
            <span className="cb-stat-label">{t('Mirrored Aliases')}</span>
            <strong>
              {mirrorAliases.length} {t('Aliases')}
              <small className="cb-stat-sub">Shared Document Root</small>
            </strong>
          </div>
        </div>
        <div className="cb-stat-box">
          <div className="cb-stat-icon amber"><Code2 size={18} /></div>
          <div className="cb-stat-info">
            <span className="cb-stat-label">{t('URI Rewrite Rules')}</span>
            <strong>
              {uriRules.length} {t('Rules')}
              <small className="cb-stat-sub">Nginx PCRE Rewrite</small>
            </strong>
          </div>
        </div>
      </div>

      {/* Tabs */}
      <div className="segmented-control backup-tabs" role="tablist" style={{ marginBottom: '16px' }}>
        <button
          type="button"
          role="tab"
          aria-selected={activeTab === 'domain'}
          className={activeTab === 'domain' ? 'active' : ''}
          onClick={() => setActiveTab('domain')}
        >
          <Globe size={14} /> {t('Domain 301 Redirects & Pointers')} ({aliases.length})
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={activeTab === 'uri'}
          className={activeTab === 'uri' ? 'active' : ''}
          onClick={() => setActiveTab('uri')}
        >
          <Code2 size={14} /> {t('URI Path Rewrite Rules')} ({uriRules.length})
        </button>
      </div>

      {!selectedSite ? (
        <div style={{ padding: '40px 20px', textAlign: 'center', border: '1px solid var(--border)', borderRadius: '12px', background: 'var(--surface)' }}>
          <Globe size={36} style={{ opacity: 0.35, marginBottom: '10px' }} />
          <h3 style={{ margin: '0 0 6px', fontSize: '15px' }}>{t('No website available')}</h3>
          <p className="hint" style={{ margin: '0 0 14px' }}>{t('Create a website in Domain Setup first to configure redirections.')}</p>
          <button type="button" className="cb-btn primary" onClick={() => navigateToPage('websites')}>
            <Plus size={13} /> {t('Go to Domain Setup')}
          </button>
        </div>
      ) : activeTab === 'domain' ? (
        <div style={{ display: 'grid', gap: '16px' }}>
          {/* Add Domain Redirect Form */}
          <form
            onSubmit={handleAddDomainRedirect}
            style={{
              padding: '16px 18px',
              background: 'var(--surface)',
              border: '1px solid var(--border)',
              borderRadius: '12px',
              display: 'grid',
              gap: '12px',
            }}
          >
            <div>
              <strong style={{ fontSize: '14.5px', display: 'block' }}>{t('Create Domain Redirect or Pointer')}</strong>
              <span className="hint" style={{ fontSize: '12.5px' }}>
                {t('Forward an additional domain or subdomain to {d} via HTTP 301 Permanent Redirect or serve the same site content.', { d: selectedSite.domain })}
              </span>
            </div>

            <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'flex-end', gap: '12px' }}>
              <label style={{ flex: '1 1 240px', margin: 0 }}>
                <span style={{ display: 'block', fontSize: '12.5px', marginBottom: '4px' }}>{t('Source Domain / Subdomain')}</span>
                <input
                  type="text"
                  value={domainForm.domain}
                  onChange={e => setDomainForm(prev => ({ ...prev, domain: e.target.value }))}
                  placeholder={t('e.g. old-brand.com or promo.example.com')}
                  required
                />
              </label>

              <label style={{ flex: '0 1 260px', margin: 0 }}>
                <span style={{ display: 'block', fontSize: '12.5px', marginBottom: '4px' }}>{t('Redirection Mode')}</span>
                <select
                  value={domainForm.mode}
                  onChange={e => setDomainForm(prev => ({ ...prev, mode: e.target.value }))}
                >
                  <option value="redirect">{t('301 Permanent Redirect (→ https://' + selectedSite.domain + ')')}</option>
                  <option value="alias">{t('Domain Alias / Mirror (Same Document Root)')}</option>
                </select>
              </label>

              <button type="submit" className="cb-btn primary" disabled={!!loading} style={{ height: '38px' }}>
                <Plus size={14} /> {t('Add Redirection')}
              </button>
            </div>
          </form>

          {/* Domain Redirects Table */}
          <div style={{ border: '1px solid var(--border)', borderRadius: '12px', overflow: 'hidden', background: 'var(--surface)' }}>
            {aliases.length === 0 ? (
              <div style={{ padding: '40px 20px', textAlign: 'center' }}>
                <MoveRight size={34} style={{ opacity: 0.35, marginBottom: '8px' }} />
                <h3 style={{ margin: '0 0 4px', fontSize: '15px' }}>{t('No domain redirects configured yet')}</h3>
                <p className="hint" style={{ margin: 0 }}>{t('Add a domain above to automatically generate a 301 redirect vhost in Nginx.')}</p>
              </div>
            ) : (
              <div style={{ width: '100%', overflowX: 'auto' }}>
                <table style={{ width: '100%', borderCollapse: 'collapse', textAlign: 'left', fontSize: '13.5px' }}>
                  <thead>
                    <tr style={{ background: 'var(--surface-alt)', borderBottom: '1px solid var(--border)' }}>
                      <th style={{ padding: '12px 16px' }}>{t('Source Domain')}</th>
                      <th style={{ padding: '12px 16px' }}>{t('Type')}</th>
                      <th style={{ padding: '12px 16px' }}>{t('Destination / Behavior')}</th>
                      <th style={{ padding: '12px 16px' }}>{t('SSL Status')}</th>
                      <th style={{ padding: '12px 16px', textAlign: 'right' }}>{t('Action')}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {aliases.map(item => (
                      <tr key={item.id} style={{ borderBottom: '1px solid var(--border)' }}>
                        <td style={{ padding: '12px 16px', fontWeight: 600 }}>{item.domain}</td>
                        <td style={{ padding: '12px 16px' }}>
                          <span className={`badge ${item.mode === 'redirect' ? 'ok' : ''}`}>
                            {item.mode === 'redirect' ? t('301 Redirect') : t('Domain Alias')}
                          </span>
                        </td>
                        <td style={{ padding: '12px 16px' }}>
                          {item.mode === 'redirect' ? (
                            <code>https://{selectedSite.domain}$request_uri</code>
                          ) : (
                            <span className="hint">{t('Serves {root}', { root: selectedSite.document_root || 'public_html' })}</span>
                          )}
                        </td>
                        <td style={{ padding: '12px 16px' }}>
                          <span className={`badge ${item.ssl_enabled ? 'ok' : 'warn'}`}>
                            {item.ssl_enabled ? t('SSL Covered') : t('HTTP / Re-issue SSL')}
                          </span>
                        </td>
                        <td style={{ padding: '12px 16px', textAlign: 'right' }}>
                          <button
                            type="button"
                            className="cb-btn danger"
                            disabled={!!loading}
                            onClick={() => handleDeleteDomainRedirect(item)}
                          >
                            <Trash2 size={13} /> {t('Delete')}
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </div>
      ) : (
        <div style={{ display: 'grid', gap: '16px' }}>
          {/* Add URI Path Rewrite Rule Form */}
          <form
            onSubmit={handleAddUriRule}
            style={{
              padding: '16px 18px',
              background: 'var(--surface)',
              border: '1px solid var(--border)',
              borderRadius: '12px',
              display: 'grid',
              gap: '12px',
            }}
          >
            <div>
              <strong style={{ fontSize: '14.5px', display: 'block' }}>{t('Add Internal URI Path Rewrite Rule')}</strong>
              <span className="hint" style={{ fontSize: '12.5px' }}>
                {t('Rewrite incoming URL paths on {d} to another local path or script inside Nginx.', { d: selectedSite.domain })}
              </span>
            </div>

            <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'flex-end', gap: '12px' }}>
              <label style={{ flex: '1 1 200px', margin: 0 }}>
                <span style={{ display: 'block', fontSize: '12.5px', marginBottom: '4px' }}>{t('Source Path / Regex')}</span>
                <input
                  type="text"
                  value={uriForm.sourcePath}
                  onChange={e => setUriForm(prev => ({ ...prev, sourcePath: e.target.value }))}
                  placeholder="/old-path"
                  required
                />
              </label>

              <label style={{ flex: '1 1 200px', margin: 0 }}>
                <span style={{ display: 'block', fontSize: '12.5px', marginBottom: '4px' }}>{t('Destination Local URI')}</span>
                <input
                  type="text"
                  value={uriForm.targetUri}
                  onChange={e => setUriForm(prev => ({ ...prev, targetUri: e.target.value }))}
                  placeholder="/new-path"
                  required
                />
              </label>

              <label style={{ flex: '0 1 180px', margin: 0 }}>
                <span style={{ display: 'block', fontSize: '12.5px', marginBottom: '4px' }}>{t('Match Type')}</span>
                <select
                  value={uriForm.exact ? 'exact' : 'regex'}
                  onChange={e => setUriForm(prev => ({ ...prev, exact: e.target.value === 'exact' }))}
                >
                  <option value="exact">{t('Exact Path (^/path$)')}</option>
                  <option value="regex">{t('Prefix / Custom Regex')}</option>
                </select>
              </label>

              <label style={{ flex: '0 1 160px', margin: 0 }}>
                <span style={{ display: 'block', fontSize: '12.5px', marginBottom: '4px' }}>{t('Nginx Flag')}</span>
                <select
                  value={uriForm.flag}
                  onChange={e => setUriForm(prev => ({ ...prev, flag: e.target.value }))}
                >
                  <option value="last">last (re-evaluate location)</option>
                  <option value="break">break (stop in current)</option>
                </select>
              </label>

              <button type="submit" className="cb-btn primary" disabled={!!loading} style={{ height: '38px' }}>
                <Plus size={14} /> {t('Add Rewrite Rule')}
              </button>
            </div>
          </form>

          {/* URI Rewrite Rules Table */}
          <div style={{ border: '1px solid var(--border)', borderRadius: '12px', overflow: 'hidden', background: 'var(--surface)' }}>
            {uriRules.length === 0 ? (
              <div style={{ padding: '40px 20px', textAlign: 'center' }}>
                <Code2 size={34} style={{ opacity: 0.35, marginBottom: '8px' }} />
                <h3 style={{ margin: '0 0 4px', fontSize: '15px' }}>{t('No custom URI rewrite rules')}</h3>
                <p className="hint" style={{ margin: 0 }}>{t('Add a source path and destination URI above to create an Nginx rewrite rule.')}</p>
              </div>
            ) : (
              <div style={{ width: '100%', overflowX: 'auto' }}>
                <table style={{ width: '100%', borderCollapse: 'collapse', textAlign: 'left', fontSize: '13.5px' }}>
                  <thead>
                    <tr style={{ background: 'var(--surface-alt)', borderBottom: '1px solid var(--border)' }}>
                      <th style={{ padding: '12px 16px' }}>{t('Source Pattern')}</th>
                      <th style={{ padding: '12px 16px' }}>{t('Target Local URI')}</th>
                      <th style={{ padding: '12px 16px' }}>{t('Flag')}</th>
                      <th style={{ padding: '12px 16px' }}>{t('Generated Directive')}</th>
                      <th style={{ padding: '12px 16px', textAlign: 'right' }}>{t('Action')}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {uriRules.map(rule => (
                      <tr key={rule.id} style={{ borderBottom: '1px solid var(--border)' }}>
                        <td style={{ padding: '12px 16px' }}><code>{rule.pattern}</code></td>
                        <td style={{ padding: '12px 16px' }}><code>{rule.target}</code></td>
                        <td style={{ padding: '12px 16px' }}><span className="badge">{rule.flag}</span></td>
                        <td style={{ padding: '12px 16px' }}>
                          <code style={{ fontSize: '12px' }}>rewrite {rule.pattern} {rule.target} {rule.flag};</code>
                        </td>
                        <td style={{ padding: '12px 16px', textAlign: 'right' }}>
                          <button
                            type="button"
                            className="cb-btn danger"
                            disabled={!!loading}
                            onClick={() => handleDeleteUriRule(rule.id)}
                          >
                            <Trash2 size={13} /> {t('Delete')}
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
