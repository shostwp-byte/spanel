import React, { useState, useEffect, useRef } from 'react';
import { PackageOpen, ExternalLink, Box, Code2, Search, Download, RefreshCw } from 'lucide-react';
import { useAppContext, WordPressIcon } from '../contexts/AppContext.jsx';
import { t } from '../i18n.js';

export function AppInstallerPage({ request, websites }) {
  const { loading, setLoading, setError, setNotice, phpVersions, loadPhpVersions, appsFeatureEnabled } = useAppContext();
  const installedPhpList = phpVersions?.installed?.length ? phpVersions.installed : ['8.4'];
  useEffect(() => { if (loadPhpVersions) loadPhpVersions({ silent: true }); }, []);
  const [search, setSearch] = useState('');
  const [selectedApp, setSelectedApp] = useState(null);
  const [installForm, setInstallForm] = useState({ domain: '', php_version: installedPhpList[0] || '8.4' });
  const [showInstallModal, setShowInstallModal] = useState(false);

  const busyRef = useRef(false);
  const [installing, setInstalling] = useState(false);
  const isBusy = Boolean(installing || loading);

  const apps = [
    {
      id: 'laravel',
      name: 'Laravel',
      category: 'Framework',
      description: 'The PHP Framework for Web Artisans. A web application framework with expressive, elegant syntax.',
      icon: <Code2 size={40} color="#ff2d20" />,
      color: 'linear-gradient(135deg, rgba(255,45,32,0.1), rgba(255,45,32,0.05))',
      requires_php: true
    },
    ...(appsFeatureEnabled ? [{
      id: 'nodejs',
      name: 'Node.js',
      category: 'Runtime',
      description: 'A JavaScript runtime built on Chrome\'s V8 JavaScript engine. Ideal for scalable network applications.',
      icon: <Box size={40} color="#68a063" />,
      color: 'linear-gradient(135deg, rgba(104,160,99,0.1), rgba(104,160,99,0.05))',
      requires_php: false
    }] : []),
    {
      id: 'wordpress',
      name: 'WordPress',
      category: 'CMS',
      description: 'Create a beautiful website, blog, or app. WordPress is open source software you can use to create anything.',
      icon: <WordPressIcon size={40} color="#21759b" />,
      color: 'linear-gradient(135deg, rgba(33,117,155,0.1), rgba(33,117,155,0.05))',
      requires_php: true
    }
  ];

  const filteredApps = apps.filter(app => app.name.toLowerCase().includes(search.toLowerCase()) || app.category.toLowerCase().includes(search.toLowerCase()));

  const handleInstallClick = (app) => {
    if (isBusy) return;
    setSelectedApp(app);
    setInstallForm({ domain: websites?.[0]?.domain || '', php_version: installedPhpList[0] || '8.4' });
    setShowInstallModal(true);
  };

  const handleInstallSubmit = async (e) => {
    e.preventDefault();
    if (busyRef.current || isBusy) return;
    if (!installForm.domain) {
      setError(t('Please select a domain'));
      return;
    }

    busyRef.current = true;
    setInstalling(true);
    try {
      const res = await request(
        '/app-installer/install',
        {
          method: 'POST',
          body: JSON.stringify({
            app_type: selectedApp.id,
            domain: installForm.domain,
            php_version: selectedApp.requires_php ? installForm.php_version : undefined
          })
        }
      );
      if (res) {
        setNotice(t(`Installation of ${selectedApp.name} started in the background. It will be ready in a few minutes.`));
        setShowInstallModal(false);
      }
    } catch (err) {
      setError(err.message);
    } finally {
      busyRef.current = false;
      setInstalling(false);
    }
  };

  return (
    <div className="section" style={{ animation: 'fade-in 0.3s ease-out' }}>
      <div className="section-title" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <div>
          <h2><PackageOpen style={{ marginRight: '8px', color: 'var(--primary)' }} /> {t('App Installer')}</h2>
          <p className="hint">{t('1-Click installer for popular frameworks and CMS.')}</p>
        </div>
        <div style={{ position: 'relative', width: '250px' }}>
          <Search size={18} style={{ position: 'absolute', left: '10px', top: '50%', transform: 'translateY(-50%)', color: 'var(--text-muted)' }} />
          <input
            type="text"
            placeholder={t('Search applications...')}
            value={search}
            onChange={e => setSearch(e.target.value)}
            style={{ paddingLeft: '36px', width: '100%' }}
          />
        </div>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(300px, 1fr))', gap: '1.5rem', marginTop: '2rem' }}>
        {filteredApps.map(app => (
          <div key={app.id} style={{
            background: app.color,
            borderRadius: '16px',
            border: '1px solid var(--border)',
            padding: '1.5rem',
            display: 'flex',
            flexDirection: 'column',
            transition: 'transform 0.2s, box-shadow 0.2s',
            cursor: isBusy ? 'not-allowed' : 'pointer',
            opacity: isBusy ? 0.7 : 1
          }}
          onMouseEnter={e => { if (!isBusy) { e.currentTarget.style.transform = 'translateY(-4px)'; e.currentTarget.style.boxShadow = '0 10px 15px -3px rgba(0,0,0,0.1)'; } }}
          onMouseLeave={e => { e.currentTarget.style.transform = 'translateY(0)'; e.currentTarget.style.boxShadow = 'none'; }}
          onClick={() => handleInstallClick(app)}
          >
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: '1rem' }}>
              <div style={{ background: '#fff', padding: '12px', borderRadius: '12px', boxShadow: '0 2px 4px rgba(0,0,0,0.05)' }}>
                {app.icon}
              </div>
              <span className="badge" style={{ background: 'var(--surface)' }}>{app.category}</span>
            </div>
            <h3 style={{ fontSize: '1.25rem', marginBottom: '0.5rem', color: 'var(--text)' }}>{app.name}</h3>
            <p className="hint" style={{ flexGrow: 1, lineHeight: '1.5' }}>{app.description}</p>
            <button
              type="button"
              className="button button-primary"
              disabled={isBusy}
              style={{ marginTop: '1.5rem', width: '100%', justifyContent: 'center' }}
              onClick={(e) => { e.stopPropagation(); handleInstallClick(app); }}
            >
              <Download size={18} /> {t('Install')}
            </button>
          </div>
        ))}
      </div>

      {showInstallModal && selectedApp && (
        <div className="modal-overlay">
          <div className="modal">
            <div className="modal-header">
              <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                {selectedApp.icon}
                <h3 style={{ margin: 0 }}>{t('Install')} {selectedApp.name}</h3>
              </div>
              <button type="button" className="icon-button" disabled={isBusy} onClick={() => setShowInstallModal(false)}><ExternalLink size={18} /></button>
            </div>
            <form onSubmit={handleInstallSubmit}>
              <div className="modal-body">
                <p className="hint" style={{ marginBottom: '1.5rem' }}>
                  {t('This will overwrite the contents of the selected website\'s document root.')}
                </p>
                <label>
                  <span>{t('Target Website (Domain)')}</span>
                  <select disabled={isBusy} value={installForm.domain} onChange={e => setInstallForm({...installForm, domain: e.target.value})} required>
                    <option value="">-- {t('Select a website')} --</option>
                    {websites?.map(w => (
                      <option key={w.domain} value={w.domain}>{w.domain}</option>
                    ))}
                  </select>
                </label>

                {selectedApp.requires_php && (
                  <label style={{ marginTop: '1rem' }}>
                    <span>{t('PHP Version')}</span>
                    <select disabled={isBusy} value={installForm.php_version} onChange={e => setInstallForm({...installForm, php_version: e.target.value})}>
                      {installedPhpList.map(v => (
                        <option key={v} value={v}>PHP {v}</option>
                      ))}
                    </select>
                  </label>
                )}
              </div>
              <div className="modal-footer">
                <button type="button" className="button" disabled={isBusy} onClick={() => setShowInstallModal(false)}>{t('Cancel')}</button>
                <button type="submit" className="button button-primary" disabled={isBusy}>
                  {installing ? <><RefreshCw size={16} className="spin" /> {t('Installing...')}</> : t('Start Installation')}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}
