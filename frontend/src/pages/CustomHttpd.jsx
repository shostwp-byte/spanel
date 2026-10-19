import React, { useState } from 'react';
import { HelpCircle, Settings, ChevronDown, RefreshCw, X, Save } from 'lucide-react';
import { useAppContext } from '../contexts/AppContext.jsx';
import { t } from '../i18n.js';

export function CustomHttpdPage({ request, websites = [] }) {
  const { loading, setLoading, setError, setNotice } = useAppContext();
  const [searchTerm, setSearchTerm] = useState('');
  const [selectedSite, setSelectedSite] = useState(null);
  const [customConfig, setCustomConfig] = useState('');

  const filteredSites = (websites || []).filter(site => 
    site.domain.toLowerCase().includes(searchTerm.toLowerCase())
  );

  const loadCustomConfig = async (site) => {
    setSelectedSite(site);
    setCustomConfig('');
    setLoading(t('Loading custom config...'));
    try {
      const data = await request(`/websites/${site.id}/nginx-custom`);
      if (data) setCustomConfig(data.nginx_custom || '');
      else setSelectedSite(null);
    } catch (err) {
      setError(err.detail || t('Failed to load custom config'));
      setSelectedSite(null);
    } finally {
      setLoading('');
    }
  };

  const handleSave = async (e) => {
    e.preventDefault();
    if (!selectedSite) return;
    
    setLoading(t('Saving custom configuration...'));
    try {
      const res = await request(`/websites/${selectedSite.id}/nginx-custom`, {
        method: 'PUT',
        body: JSON.stringify({ nginx_custom: customConfig })
      }, t('Saving custom configuration...'));
      if (res) {
        setNotice(t('Custom HTTPD configuration saved successfully.'));
        setSelectedSite(null);
      }
    } catch (err) {
      setError(err.detail || t('Failed to save configuration'));
    } finally {
      setLoading('');
    }
  };

  if (selectedSite) {
    return (
      <div style={{ animation: 'fade-in 0.3s ease-out' }}>
        <div style={{ marginBottom: '20px', paddingBottom: '15px', borderBottom: '1px solid var(--border)', display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '10px' }}>
          <h2 style={{ fontSize: '24px', fontWeight: '400', color: 'var(--text)', margin: 0, display: 'flex', alignItems: 'center', gap: '8px' }}>
            {t('Custom HTTPD Configuration for')} {selectedSite.domain}
            <HelpCircle size={16} color="var(--text-muted)" style={{ cursor: 'pointer' }} />
          </h2>
          <button onClick={() => setSelectedSite(null)} style={{ background: 'var(--surface-alt)', border: '1px solid var(--border)', padding: '6px 12px', borderRadius: '4px', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: '6px', color: 'var(--text)', fontSize: '13px' }}>
            <X size={14} /> {t('Back')}
          </button>
        </div>

        <div style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: '4px', padding: '20px', boxShadow: '0 1px 3px rgba(0,0,0,0.02)' }}>
          <form onSubmit={handleSave}>
            <label style={{ display: 'block', marginBottom: '8px', fontSize: '14px', fontWeight: '500', color: 'var(--text-secondary)' }}>
              {t('Custom Nginx Configuration')}
            </label>
            <textarea 
              rows={20}
              value={customConfig}
              onChange={e => setCustomConfig(e.target.value)}
              disabled={loading}
              style={{ width: '100%', fontFamily: 'monospace', fontSize: '13px', padding: '12px', border: '1px solid var(--border)', borderRadius: '4px', outline: 'none', background: 'var(--surface-alt)', color: 'var(--text)', resize: 'vertical' }}
              placeholder={t('Enter custom nginx directives here...')}
            />
            <p style={{ fontSize: '13px', color: 'var(--text-muted)', marginTop: '10px' }}>
              {t('Example: add_header X-Frame-Options "SAMEORIGIN";')}
            </p>
            
            <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '10px', marginTop: '20px', paddingTop: '15px', borderTop: '1px solid var(--border)', flexWrap: 'wrap' }}>
              <button type="button" onClick={() => setSelectedSite(null)} disabled={loading} style={{ background: 'var(--surface)', border: '1px solid var(--border)', padding: '8px 20px', borderRadius: '4px', cursor: 'pointer', color: 'var(--text)', fontSize: '13px', fontWeight: '500' }}>
                {t('Cancel')}
              </button>
              <button type="submit" disabled={loading} style={{ background: '#10b981', color: '#fff', border: 'none', padding: '8px 20px', borderRadius: '4px', fontSize: '13px', fontWeight: '600', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: '6px', textTransform: 'uppercase' }}>
                <Save size={16} /> {loading ? t('Saving...') : t('Save')}
              </button>
            </div>
          </form>
        </div>
      </div>
    );
  }

  return (
    <div style={{ animation: 'fade-in 0.3s ease-out' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '25px', flexWrap: 'wrap', gap: '10px' }}>
        <h2 style={{ fontSize: '24px', fontWeight: '400', color: 'var(--text)', margin: 0, display: 'flex', alignItems: 'center', gap: '8px' }}>
          {t('Custom HTTPD Configurations')}
          <HelpCircle size={16} color="var(--text-muted)" style={{ cursor: 'pointer' }} />
        </h2>
        <button style={{ display: 'flex', alignItems: 'center', gap: '6px', background: '#0066cc', color: '#fff', border: 'none', padding: '8px 16px', borderRadius: '4px', fontSize: '13px', fontWeight: '500', cursor: 'pointer', textTransform: 'uppercase' }}>
          <Settings size={16} /> {t('DA BUILD REWRITE_CONFS')}
        </button>
      </div>

      <div style={{ display: 'flex', alignItems: 'center', marginBottom: '20px', gap: '15px', flexWrap: 'wrap' }}>
        <span style={{ fontSize: '14px', color: 'var(--text-secondary)', fontWeight: '500' }}>{t('Show Domains that')}</span>
        <div style={{ display: 'flex', alignItems: 'center', border: '1px solid var(--border)', borderRadius: '4px', background: 'var(--surface)', overflow: 'hidden', height: '36px', flex: '1 1 280px', maxWidth: '560px' }}>
          <select style={{ padding: '0 12px', border: 'none', borderRight: '1px solid var(--border)', background: 'var(--surface)', fontSize: '13px', color: 'var(--text)', outline: 'none', appearance: 'auto', height: '100%', width: 'auto' }}>
            <option>{t('Starts With')}</option>
            <option>{t('Contains')}</option>
            <option>{t('Ends With')}</option>
          </select>
          <input 
            type="text" 
            value={searchTerm}
            onChange={e => setSearchTerm(e.target.value)}
            style={{ flex: 1, minWidth: '100px', padding: '0 12px', border: 'none', fontSize: '13px', outline: 'none', height: '100%', background: 'var(--surface)', color: 'var(--text)' }}
          />
          <button onClick={() => setSearchTerm('')} style={{ padding: '0 12px', border: 'none', borderLeft: '1px solid var(--border)', background: 'var(--surface)', color: 'var(--text-muted)', cursor: 'pointer', display: 'flex', alignItems: 'center', height: '100%' }}>
            <X size={16} />
          </button>
        </div>
      </div>

      <div style={{ display: 'flex', justifyContent: 'flex-end', marginBottom: '10px', gap: '10px' }}>
        <button style={{ display: 'flex', alignItems: 'center', gap: '8px', background: 'var(--surface)', border: '1px solid var(--border)', padding: '6px 12px', borderRadius: '4px', fontSize: '13px', color: 'var(--text)', cursor: 'pointer' }}>
          Columns <ChevronDown size={14} />
        </button>
        <button style={{ background: 'none', border: 'none', cursor: 'pointer', color: '#3b82f6', display: 'flex', alignItems: 'center', padding: '4px' }}>
          <RefreshCw size={16} />
        </button>
      </div>

      <div style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: '4px', overflow: 'hidden', boxShadow: '0 1px 3px rgba(0,0,0,0.02)' }}>
        <div style={{ width: '100%', overflowX: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', textAlign: 'left', fontSize: '13px' }}>
            <thead>
              <tr style={{ background: 'var(--surface-alt)', borderBottom: '1px solid var(--border)' }}>
                <th style={{ padding: '15px 20px', fontWeight: '600', color: 'var(--text)', width: '30%' }}>{t('Domain')}</th>
                <th style={{ padding: '15px 20px', fontWeight: '600', color: 'var(--text)', width: '20%' }}>{t('User')}</th>
                <th style={{ padding: '15px 20px', fontWeight: '600', color: 'var(--text)', width: '50%' }}>{t('Conf. Files')}</th>
              </tr>
            </thead>
            <tbody>
              {filteredSites.map((site, idx) => (
                <tr key={site.id} style={{ borderBottom: '1px solid var(--border)', background: idx % 2 === 0 ? 'var(--surface)' : 'var(--surface-alt)' }}>
                  <td style={{ padding: '15px 20px', color: 'var(--text-secondary)' }}>{site.domain}</td>
                  <td style={{ padding: '15px 20px', color: 'var(--text-secondary)' }}>{site.owner_username}</td>
                  <td style={{ padding: '15px 20px' }}>
                    <div style={{ display: 'flex', gap: '20px', flexWrap: 'wrap' }}>
                      <span 
                        onClick={() => loadCustomConfig(site)}
                        style={{ color: 'var(--primary)', cursor: 'pointer', fontWeight: '500' }}
                      >
                        nginx.conf
                      </span>
                      <span 
                        onClick={() => loadCustomConfig(site)}
                        style={{ color: 'var(--primary)', cursor: 'pointer', fontWeight: '500' }}
                      >
                        php-fpm.conf {site.php_version || '8.1'}
                      </span>
                    </div>
                  </td>
                </tr>
              ))}
              {filteredSites.length === 0 && (
                <tr>
                  <td colSpan="3" style={{ padding: '40px', textAlign: 'center', color: 'var(--text-muted)' }}>
                    {t('No data available in table')}
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
