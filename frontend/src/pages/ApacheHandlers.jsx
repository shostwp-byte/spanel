import React, { useState, useEffect } from 'react';
import {
  Server, Code2, Globe, FolderOpen, Sparkles, Check,
  RefreshCw, Layers, Save, RotateCcw, FileText
} from 'lucide-react';
import { useAppContext } from '../contexts/AppContext.jsx';
import { t } from '../i18n.js';
import { WebsiteSelect } from '../components/WebsiteSelect.jsx';

const REWRITE_PROFILES = [
  {
    id: 'front_controller',
    title: 'WordPress / Front Controller (index.php)',
    category: 'CMS & PHP Router',
    tone: 'blue',
    directive: 'try_files $uri $uri/ /index.php?$args;',
    description: 'Routes all non-existent file and directory requests to /index.php?$args. Ideal for WordPress, Joomla, Drupal, and standard PHP apps.',
  },
  {
    id: 'laravel',
    title: 'Laravel / Symfony Handler',
    category: 'Modern Framework',
    tone: 'indigo',
    directive: 'try_files $uri $uri/ /index.php?$query_string;',
    description: 'Forwards clean URLs and query strings to index.php?$query_string. Pair with Document Root "public" for Laravel & Symfony.',
  },
  {
    id: 'codeigniter',
    title: 'CodeIgniter / MVC Router',
    category: 'MVC Framework',
    tone: 'emerald',
    directive: 'try_files $uri $uri/ /index.php?/$request_uri;',
    description: 'Passes the full request URI segment into /index.php?/$request_uri for CodeIgniter and classic PHP MVC frameworks.',
  },
  {
    id: 'seohburl',
    title: 'Clean SEO Extensionless URLs (.html / .php)',
    category: 'SEO & Static Hybrid',
    tone: 'amber',
    directive: 'try_files $uri $uri/ $uri.html $uri.php?$args /index.php?$args;',
    description: 'Automatically resolves extensionless URLs to matching .html or .php files on disk before falling back to index.php.',
  },
  {
    id: 'none',
    title: 'Direct File & Static MIME Handler',
    category: 'Default Handler',
    tone: 'slate',
    directive: 'try_files $uri $uri/ =404;',
    description: 'Serves exact files and directories directly without framework URL rewriting. Returns 404 if the file does not exist.',
  },
];

export function ApacheHandlersPage({ request, phpVersions: propPhpVersions }) {
  const {
    websites = [], selectedWebsiteId, setSelectedWebsiteId,
    phpVersions: ctxPhpVersions, loadPhpVersions, loading, setLoading, setNotice, setError,
    refreshAll, navigateToPage
  } = useAppContext();

  const phpVersions = propPhpVersions || ctxPhpVersions || { installed: ['8.4'], supported: ['7.4', '8.0', '8.1', '8.2', '8.3', '8.4'] };
  const installedPhps = (phpVersions.installed && phpVersions.installed.length ? phpVersions.installed : ['8.4']);

  const [activeTab, setActiveTab] = useState('rewrite');
  const [customBlock, setCustomBlock] = useState('');
  const [docRootDraft, setDocRootDraft] = useState('public_html');
  const [phpDraft, setPhpDraft] = useState('8.4');

  const selectedSite = websites.find(w => String(w.id) === String(selectedWebsiteId)) || websites[0] || null;

  useEffect(() => {
    if (loadPhpVersions) loadPhpVersions({ silent: true });
  }, []);

  useEffect(() => {
    if (!selectedWebsiteId && websites.length > 0) {
      setSelectedWebsiteId(String(websites[0].id));
    }
  }, [websites, selectedWebsiteId]);

  useEffect(() => {
    if (selectedSite) {
      setDocRootDraft(selectedSite.document_root || 'public_html');
      setPhpDraft(selectedSite.php_version || '8.4');
      loadCustomNginx(selectedSite.id);
    }
  }, [selectedSite?.id, selectedSite?.document_root, selectedSite?.php_version]);

  async function loadCustomNginx(siteId = selectedSite?.id) {
    if (!siteId) return;
    const res = await request(`/websites/${siteId}/nginx-custom`, { silent: true });
    if (res) {
      setCustomBlock(res.nginx_custom || '');
    }
  }

  async function handleSelectRewriteMode(modeId) {
    if (!selectedSite) return;
    const res = await request(
      `/websites/${selectedSite.id}`,
      {
        method: 'PATCH',
        body: JSON.stringify({ nginx_rewrite_mode: modeId }),
      },
      t('Applying URL rewrite handler...')
    );
    if (res) {
      setNotice(t('Activated rewrite handler "{m}" for {d}.', { m: modeId, d: selectedSite.domain }));
      if (refreshAll) await refreshAll();
    }
  }

  async function handleSaveRuntimeAndDocRoot(e) {
    e.preventDefault();
    if (!selectedSite) return;
    const payload = {};
    if (phpDraft && phpDraft !== selectedSite.php_version) {
      payload.php_version = phpDraft;
    }
    if (docRootDraft && docRootDraft.trim() !== (selectedSite.document_root || 'public_html')) {
      payload.document_root = docRootDraft.trim();
    }
    if (Object.keys(payload).length === 0) {
      setNotice(t('Runtime and document root settings are already up to date.'));
      return;
    }
    const res = await request(
      `/websites/${selectedSite.id}`,
      {
        method: 'PATCH',
        body: JSON.stringify(payload),
      },
      t('Updating PHP runtime & document root handler...')
    );
    if (res) {
      setNotice(t('Updated runtime & document root handler for {d}.', { d: selectedSite.domain }));
      if (refreshAll) await refreshAll();
    }
  }

  async function handleSaveCustomBlock(e) {
    if (e) e.preventDefault();
    if (!selectedSite) return;
    const res = await request(
      `/websites/${selectedSite.id}/nginx-custom`,
      {
        method: 'PUT',
        body: JSON.stringify({ nginx_custom: customBlock }),
      },
      t('Validating & applying custom Nginx directives...')
    );
    if (res) {
      setNotice(t('Saved custom handler directives for {d}.', { d: selectedSite.domain }));
      if (refreshAll) await refreshAll();
    }
  }

  function insertSnippet(snippet) {
    setCustomBlock(prev => {
      const clean = (prev || '').trim();
      return clean ? `${clean}\n\n${snippet}` : snippet;
    });
  }

  const currentRewriteMode = selectedSite
    ? (selectedSite.app_type === 'wordpress' ? 'front_controller' : (selectedSite.nginx_rewrite_mode || 'none'))
    : 'none';

  return (
    <div className="section">
      <div className="section-title" style={{ marginBottom: '14px' }}>
        <div>
          <h2 style={{ display: 'flex', alignItems: 'center', gap: '10px', margin: 0 }}>
            <Server size={19} />
            <span>{t('Request, Rewrite & Runtime Handlers')}</span>
          </h2>
          <p className="hint" style={{ marginTop: '4px' }}>
            {t('Configure URL rewrite profiles, PHP-FPM runtime handlers, document roots, and custom cache directives per domain.')}
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
            onClick={() => loadCustomNginx(selectedSite?.id)}
          >
            <RefreshCw size={14} /> {t('Refresh')}
          </button>
        </div>
      </div>

      {/* KPI Strip */}
      <div className="cb-stats-strip">
        <div className="cb-stat-box">
          <div className="cb-stat-icon blue"><Layers size={18} /></div>
          <div className="cb-stat-info">
            <span className="cb-stat-label">{t('Active Rewrite Profile')}</span>
            <strong>
              {currentRewriteMode}
              <small className="cb-stat-sub">{selectedSite?.app_type || 'php'}</small>
            </strong>
          </div>
        </div>
        <div className="cb-stat-box">
          <div className="cb-stat-icon indigo"><Code2 size={18} /></div>
          <div className="cb-stat-info">
            <span className="cb-stat-label">{t('PHP-FPM Handler')}</span>
            <strong>
              PHP {selectedSite?.php_version || '8.4'}
              <small className="cb-stat-sub">FastCGI Socket</small>
            </strong>
          </div>
        </div>
        <div className="cb-stat-box">
          <div className="cb-stat-icon emerald"><FolderOpen size={18} /></div>
          <div className="cb-stat-info">
            <span className="cb-stat-label">{t('Document Root')}</span>
            <strong>
              {selectedSite?.document_root || 'public_html'}
              <small className="cb-stat-sub">{selectedSite?.domain || '-'}</small>
            </strong>
          </div>
        </div>
        <div className="cb-stat-box">
          <div className="cb-stat-icon amber"><FileText size={18} /></div>
          <div className="cb-stat-info">
            <span className="cb-stat-label">{t('Custom Directives')}</span>
            <strong>
              {customBlock.trim() ? t('Configured') : t('Default')}
              <small className="cb-stat-sub">nginx_custom.conf</small>
            </strong>
          </div>
        </div>
      </div>

      {/* Tabs */}
      <div className="segmented-control backup-tabs" role="tablist" style={{ marginBottom: '16px' }}>
        <button
          type="button"
          role="tab"
          aria-selected={activeTab === 'rewrite'}
          className={activeTab === 'rewrite' ? 'active' : ''}
          onClick={() => setActiveTab('rewrite')}
        >
          <Layers size={14} /> {t('URL Rewrite & PHP Runtime Handlers')}
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={activeTab === 'custom'}
          className={activeTab === 'custom' ? 'active' : ''}
          onClick={() => setActiveTab('custom')}
        >
          <Code2 size={14} /> {t('Static Cache & Custom Directives')}
        </button>
      </div>

      {!selectedSite ? (
        <div style={{ padding: '40px 20px', textAlign: 'center', border: '1px solid var(--border)', borderRadius: '12px', background: 'var(--surface)' }}>
          <Globe size={36} style={{ opacity: 0.35, marginBottom: '10px' }} />
          <h3 style={{ margin: '0 0 6px', fontSize: '15px' }}>{t('No website selected')}</h3>
          <p className="hint" style={{ margin: '0 0 14px' }}>{t('Create a website in Domain Setup first to configure handlers.')}</p>
          <button type="button" className="cb-btn primary" onClick={() => navigateToPage('websites')}>
            {t('Go to Domain Setup')}
          </button>
        </div>
      ) : activeTab === 'rewrite' ? (
        <div style={{ display: 'grid', gap: '18px' }}>
          {/* PHP Runtime & Document Root Bar */}
          <form
            onSubmit={handleSaveRuntimeAndDocRoot}
            style={{
              padding: '16px 18px',
              background: 'var(--surface)',
              border: '1px solid var(--border)',
              borderRadius: '12px',
              display: 'flex',
              flexWrap: 'wrap',
              alignItems: 'flex-end',
              justifyContent: 'space-between',
              gap: '14px',
            }}
          >
            <div style={{ flex: '1 1 260px' }}>
              <strong style={{ fontSize: '14.5px', display: 'block' }}>{t('PHP-FPM Runtime & Document Root Handler')}</strong>
              <span className="hint" style={{ fontSize: '12.5px' }}>
                {t('Assign which PHP-FPM branch executes .php scripts and which subfolder serves as the web root for {d}.', { d: selectedSite.domain })}
              </span>
            </div>

            <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'flex-end', gap: '12px' }}>
              <label style={{ margin: 0, minWidth: '160px' }}>
                <span style={{ display: 'block', fontSize: '12.5px', marginBottom: '4px' }}>{t('PHP Handler Version')}</span>
                <select value={phpDraft} onChange={e => setPhpDraft(e.target.value)}>
                  {installedPhps.map(v => (
                    <option key={v} value={v}>PHP {v} (FPM)</option>
                  ))}
                </select>
              </label>

              <label style={{ margin: 0, minWidth: '200px' }}>
                <span style={{ display: 'block', fontSize: '12.5px', marginBottom: '4px' }}>{t('Document Root Subfolder')}</span>
                <input
                  type="text"
                  value={docRootDraft}
                  onChange={e => setDocRootDraft(e.target.value)}
                  placeholder="public_html"
                />
              </label>

              <button type="submit" className="cb-btn primary" disabled={!!loading} style={{ height: '38px' }}>
                <Save size={14} /> {t('Apply Runtime')}
              </button>
            </div>
          </form>

          {/* URL Rewrite Handler Cards */}
          <div>
            <h3 style={{ margin: '0 0 4px', fontSize: '15px' }}>{t('URL Rewrite Engine Profiles')}</h3>
            <p className="hint" style={{ margin: '0 0 12px' }}>
              {t('Select how Nginx routes clean URLs and non-existent file paths to your application front controller.')}
            </p>
            <div className="cb-grid">
              {REWRITE_PROFILES.map(profile => {
                const isActive = currentRewriteMode === profile.id;
                const isLockedByWp = selectedSite.app_type === 'wordpress' && profile.id !== 'front_controller';
                return (
                  <div className={`cb-card ${isActive ? '' : 'is-uninstalled'}`} key={profile.id}>
                    <div className="cb-card-top">
                      <div className="cb-card-header">
                        <div className={`cb-card-icon ${profile.tone}`}>
                          <Layers size={18} />
                        </div>
                        <div className="cb-card-heading">
                          <div className="cb-card-title-row">
                            <h4 className="cb-card-title" title={profile.title}>{t(profile.title)}</h4>
                          </div>
                          <div className="cb-card-meta">
                            <span>{t(profile.category)}</span>
                            <span className="cb-dot">•</span>
                            <code className="cb-ver-tag">{profile.id}</code>
                          </div>
                        </div>
                        <span className={`cb-status-pill ${isActive ? 'running' : 'uninstalled'}`}>
                          <span className="cb-status-dot" />
                          {isActive ? t('Active') : t('Available')}
                        </span>
                      </div>
                      <p className="cb-card-desc">{t(profile.description)}</p>
                    </div>

                    <div className="cb-card-footer">
                      <div className="cb-card-unit">
                        <code title={profile.directive}>{profile.directive}</code>
                      </div>
                      <div className="cb-card-actions">
                        {isActive ? (
                          <span className="cb-kernel-tag"><Check size={13} style={{ verticalAlign: 'middle' }} /> {t('Selected')}</span>
                        ) : (
                          <button
                            type="button"
                            className="cb-btn primary"
                            disabled={!!loading || isLockedByWp}
                            title={isLockedByWp ? t('WordPress mode automatically uses front_controller') : ''}
                            onClick={() => handleSelectRewriteMode(profile.id)}
                          >
                            <Sparkles size={13} /> {t('Activate')}
                          </button>
                        )}
                      </div>
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        </div>
      ) : (
        <div style={{ display: 'grid', gap: '14px' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '10px' }}>
            <div>
              <strong style={{ fontSize: '14.5px', display: 'block' }}>{t('Custom Cache & Location Directives')}</strong>
              <span className="hint" style={{ fontSize: '12.5px' }}>
                {t('Included inside the server {} block of {d}. Supports rewrite, try_files, expires, and cache headers.', { d: selectedSite.domain })}
              </span>
            </div>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: '8px' }}>
              <button
                type="button"
                className="cb-btn secondary"
                onClick={() => insertSnippet('location /assets/ {\n    expires 30d;\n    add_header Cache-Control "public, immutable";\n}')}
              >
                + {t('Static Asset Cache (30d)')}
              </button>
              <button
                type="button"
                className="cb-btn secondary"
                onClick={() => insertSnippet('location /app/ {\n    try_files $uri $uri/ /app/index.html;\n}')}
              >
                + {t('SPA Subfolder Fallback')}
              </button>
              <button
                type="button"
                className="cb-btn secondary"
                onClick={() => insertSnippet('rewrite ^/legacy-page$ /index.php last;')}
              >
                + {t('Rewrite Rule')}
              </button>
            </div>
          </div>

          <textarea
            rows={12}
            value={customBlock}
            onChange={e => setCustomBlock(e.target.value)}
            placeholder={'# Custom Nginx handler directives for ' + selectedSite.domain + '\n# Example:\n# rewrite ^/old-path$ /new-path last;'}
            style={{
              width: '100%',
              fontFamily: 'monospace',
              fontSize: '13px',
              padding: '14px',
              borderRadius: '12px',
              border: '1px solid var(--border)',
              background: '#0f172a',
              color: '#e2e8f0',
            }}
          />

          <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '10px' }}>
            <button
              type="button"
              className="cb-btn secondary"
              disabled={!!loading}
              onClick={() => setCustomBlock('')}
            >
              <RotateCcw size={13} /> {t('Clear Editor')}
            </button>
            <button
              type="button"
              className="cb-btn primary"
              disabled={!!loading}
              onClick={handleSaveCustomBlock}
            >
              <Save size={14} /> {t('Save & Reload Nginx')}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
