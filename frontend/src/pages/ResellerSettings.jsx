import React, { useState, useEffect } from 'react';
import { Save, ExternalLink, Image, Upload, Plus, Trash2, Edit2, Code2, Copy, Check } from 'lucide-react';
import { useAppContext } from '../contexts/AppContext.jsx';
import { t } from '../i18n.js';
import '../premium-branding.css';

export function ResellerSettingsPage({ request, API, panelSettings, setPanelSettings }) {
    const { loading, setLoading, setError, setNotice, currentUser } = useAppContext();
  
  const [panelLogoFile, setPanelLogoFile] = useState(null);
  const [panelFaviconFile, setPanelFaviconFile] = useState(null);
  const [panelLoginBgFile, setPanelLoginBgFile] = useState(null);

  async function uploadPanelAsset(kind) {
    const file = kind === 'logo' ? panelLogoFile : kind === 'favicon' ? panelFaviconFile : panelLoginBgFile;
    if (!file) return;
    const body = new FormData();
    body.append('file', file);
    const data = await request(`/panel-settings/${kind}`, { method: 'POST', body }, `Uploading ${kind}...`);
    if (data) {
      setPanelSettings(data);
      if (kind === 'logo') setPanelLogoFile(null);
      if (kind === 'favicon') setPanelFaviconFile(null);
      if (kind === 'login_background') setPanelLoginBgFile(null);
      setNotice(t(`${kind} uploaded successfully!`));
    }
  }

  
  const [stats, setStats] = useState(null);
  const [whitelabel, setWhitelabel] = useState({ company_name: '', panel_domain: '', logo_url: '', primary_color: '#4f46e5', support_url: '' });
  const [webhooks, setWebhooks] = useState([]);
  const [webhookForm, setWebhookForm] = useState({ id: null, url: '', secret: '', events: '*', is_active: true });
  const [showWebhookForm, setShowWebhookForm] = useState(false);
  const [copiedKey, setCopiedKey] = useState(null);

  useEffect(() => {
    loadData();
  }, []);

  
  const formatBytes = (bytes) => {
    if (!bytes || bytes === 0) return '0 B';
    const k = 1024, sizes = ['B', 'KB', 'MB', 'GB', 'TB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
  };

  const loadData = async () => {
    setLoading(t('Loading...'));
    try {
      const [statsRes, wlRes, whRes] = await Promise.all([
        request('/resellers/stats'),
        request('/resellers/whitelabel'),
        request('/resellers/webhooks')
      ]);
      setStats(statsRes);
      setWhitelabel(wlRes || { company_name: 'Control Panel', panel_domain: '', logo_url: '', primary_color: '#4f46e5', support_url: '' });
      setWebhooks(whRes || []);
    } catch (e) {
      setError(e.message);
    }
    setLoading('');
  };

  const saveWhitelabel = async (e) => {
    e.preventDefault();
    setLoading(t('Saving...'));
    try {
      const res = await request('/resellers/whitelabel', { method: 'PUT', body: JSON.stringify(whitelabel) });
      if (res) {
        setNotice(t('White-label settings saved. Refresh the page to see color changes.'));
      }
    } catch (e) {
      setError(e.message);
    }
    setLoading('');
  };

  const saveWebhook = async (e) => {
    e.preventDefault();
    setLoading(t('Saving...'));
    try {
      let res;
      if (webhookForm.id) {
        res = await request(`/resellers/webhooks/${webhookForm.id}`, { method: 'PUT', body: JSON.stringify(webhookForm) });
        if (res) setNotice(t('Webhook updated.'));
      } else {
        res = await request('/resellers/webhooks', { method: 'POST', body: JSON.stringify(webhookForm) });
        if (res) setNotice(t('Webhook created.'));
      }
      if (res) {
        setShowWebhookForm(false);
        loadData();
      }
    } catch (e) {
      setError(e.message);
    }
    setLoading('');
  };

  const deleteWebhook = async (id) => {
    if (!confirm(t('Delete this webhook?'))) return;
    setLoading(t('Deleting...'));
    try {
      const res = await request(`/resellers/webhooks/${id}`, { method: 'DELETE' });
      if (res) {
        setNotice(t('Webhook deleted.'));
        loadData();
      }
    } catch (e) {
      setError(e.message);
    }
    setLoading('');
  };

  const copyToClipboard = (text, key) => {
    navigator.clipboard.writeText(text);
    setCopiedKey(key);
    setTimeout(() => setCopiedKey(null), 2000);
  };

  return (
    <div className="reseller-settings-page">
      <div className="section-title">
        <h2>{t('Reseller Settings & Branding')}</h2>
        <p className="hint">{t('Configure your white-label branding and billing integrations.')}</p>
      </div>

      {stats && (
        <div style={{ marginBottom: '2rem' }}>
          <div className="stats-grid" style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: '1rem', marginBottom: '1.5rem' }}>
            <div className="stat-card" style={{ background: 'var(--surface)', padding: '1.5rem', borderRadius: '12px', border: '1px solid var(--border)', boxShadow: '0 4px 6px -1px rgba(0, 0, 0, 0.1)' }}>
              <div style={{ fontSize: '2rem', fontWeight: 'bold', color: 'var(--primary)' }}>{stats.total_users}</div>
              <p className="hint" style={{ marginTop: '0.5rem', fontWeight: '500' }}>{t('Total Users')}</p>
            </div>
            <div className="stat-card" style={{ background: 'var(--surface)', padding: '1.5rem', borderRadius: '12px', border: '1px solid var(--border)', boxShadow: '0 4px 6px -1px rgba(0, 0, 0, 0.1)' }}>
              <div style={{ fontSize: '2rem', fontWeight: 'bold', color: 'var(--primary)' }}>{stats.total_websites}</div>
              <p className="hint" style={{ marginTop: '0.5rem', fontWeight: '500' }}>{t('Hosted Websites')}</p>
            </div>
            <div className="stat-card" style={{ background: 'var(--surface)', padding: '1.5rem', borderRadius: '12px', border: '1px solid var(--border)', boxShadow: '0 4px 6px -1px rgba(0, 0, 0, 0.1)' }}>
              <div style={{ fontSize: '2rem', fontWeight: 'bold', color: 'var(--primary)' }}>{stats.total_databases}</div>
              <p className="hint" style={{ marginTop: '0.5rem', fontWeight: '500' }}>{t('Databases')}</p>
            </div>
            <div className="stat-card" style={{ background: 'var(--surface)', padding: '1.5rem', borderRadius: '12px', border: '1px solid var(--border)', boxShadow: '0 4px 6px -1px rgba(0, 0, 0, 0.1)' }}>
              <div style={{ fontSize: '2rem', fontWeight: 'bold', color: 'var(--primary)' }}>{stats.total_packages}</div>
              <p className="hint" style={{ marginTop: '0.5rem', fontWeight: '500' }}>{t('Custom Packages')}</p>
            </div>
          </div>
          
          <div className="resource-usage" style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(min(280px, 100%), 1fr))', gap: '1.5rem' }}>
            {/* Storage Usage Widget */}
            <div className="resource-card" style={{ background: 'linear-gradient(145deg, var(--surface), rgba(var(--primary-color-rgb), 0.05))', padding: '1.5rem', borderRadius: '16px', border: '1px solid var(--border)' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '1rem' }}>
                <h3 style={{ margin: 0, fontSize: '1.1rem' }}>{t('Storage Allocation')}</h3>
                <span className="badge" style={{ background: 'rgba(var(--primary-color-rgb), 0.1)', color: 'var(--primary)', padding: '4px 8px', borderRadius: '20px', fontSize: '0.8rem', fontWeight: 'bold' }}>
                  {formatBytes(stats.total_storage_used_bytes)} / {stats.total_storage_limit_bytes > 0 ? formatBytes(stats.total_storage_limit_bytes) : t('Unlimited')}
                </span>
              </div>
              <div className="progress-bar-container" style={{ width: '100%', height: '12px', background: 'var(--border)', borderRadius: '10px', overflow: 'hidden' }}>
                <div className="progress-fill" style={{ 
                  height: '100%', 
                  background: 'linear-gradient(90deg, var(--primary), #818cf8)', 
                  width: `${stats.total_storage_limit_bytes > 0 ? Math.min(100, (stats.total_storage_used_bytes / stats.total_storage_limit_bytes) * 100) : 0}%`,
                  transition: 'width 0.5s ease'
                }}></div>
              </div>
              <p className="hint" style={{ marginTop: '0.75rem', fontSize: '0.85rem' }}>{t('Total disk space consumed by all your users.')}</p>
            </div>

            {/* Bandwidth Usage Widget */}
            <div className="resource-card" style={{ background: 'linear-gradient(145deg, var(--surface), rgba(16, 185, 129, 0.05))', padding: '1.5rem', borderRadius: '16px', border: '1px solid var(--border)' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '1rem' }}>
                <h3 style={{ margin: 0, fontSize: '1.1rem' }}>{t('Bandwidth Traffic')}</h3>
                <span className="badge" style={{ background: 'rgba(16, 185, 129, 0.1)', color: '#10b981', padding: '4px 8px', borderRadius: '20px', fontSize: '0.8rem', fontWeight: 'bold' }}>
                  {formatBytes(stats.total_bandwidth_used_bytes)} / {stats.total_bandwidth_limit_bytes > 0 ? formatBytes(stats.total_bandwidth_limit_bytes) : t('Unlimited')}
                </span>
              </div>
              <div className="progress-bar-container" style={{ width: '100%', height: '12px', background: 'var(--border)', borderRadius: '10px', overflow: 'hidden' }}>
                <div className="progress-fill" style={{ 
                  height: '100%', 
                  background: 'linear-gradient(90deg, #10b981, #34d399)', 
                  width: `${stats.total_bandwidth_limit_bytes > 0 ? Math.min(100, (stats.total_bandwidth_used_bytes / stats.total_bandwidth_limit_bytes) * 100) : 0}%`,
                  transition: 'width 0.5s ease'
                }}></div>
              </div>
              <p className="hint" style={{ marginTop: '0.75rem', fontSize: '0.85rem' }}>{t('Total data transfer for the current billing cycle.')}</p>
            </div>
          </div>
        </div>
      )}

      <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 1fr)', gap: '2rem', alignItems: 'start' }}>
        <section className="premium-section" style={{ gridColumn: '1 / -1' }}>
        <div className="premium-section-header">
          <h3><Edit2 size={18} /> {t('White-Label Branding')}</h3>
          <p>{t('Customize the panel appearance for your customers.')}</p>
        </div>
        <div className="premium-section-body">
          <form className="premium-form-grid" onSubmit={saveWhitelabel}>
            <div className="premium-form-group">
              <label>{t('Company Name')}</label>
              <input type="text" className="premium-input" placeholder="Your Company LLC" value={whitelabel.company_name || ''} onChange={e => setWhitelabel({...whitelabel, company_name: e.target.value})} required />
            </div>
            
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(min(280px, 100%), 1fr))', gap: '20px' }}>
              <div className="premium-form-group">
                <label>{t('Panel Domain')} <span style={{fontWeight:'normal',color:'var(--text-muted)'}}>(Optional)</span></label>
                <input type="text" className="premium-input" placeholder="cp.yourdomain.com" value={whitelabel.panel_domain || ''} onChange={e => setWhitelabel({...whitelabel, panel_domain: e.target.value})} />
              </div>
              <div className="premium-form-group">
                <label>{t('Logo URL')} <span style={{fontWeight:'normal',color:'var(--text-muted)'}}>(Optional)</span></label>
                <input type="url" className="premium-input" placeholder="https://..." value={whitelabel.logo_url || ''} onChange={e => setWhitelabel({...whitelabel, logo_url: e.target.value})} />
              </div>
            </div>

            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(min(280px, 100%), 1fr))', gap: '20px' }}>
              <div className="premium-form-group">
                <label>{t('Primary Color')}</label>
                <div className="premium-color-picker-wrap">
                  <input type="color" className="premium-color-input" value={whitelabel.primary_color || '#4f46e5'} onChange={e => setWhitelabel({...whitelabel, primary_color: e.target.value})} />
                  <input type="text" className="premium-input" value={whitelabel.primary_color || '#4f46e5'} onChange={e => setWhitelabel({...whitelabel, primary_color: e.target.value})} style={{ flex: 1 }} />
                </div>
              </div>
              <div className="premium-form-group">
                <label>{t('Support URL')} <span style={{fontWeight:'normal',color:'var(--text-muted)'}}>(Optional)</span></label>
                <input type="url" className="premium-input" placeholder="https://..." value={whitelabel.support_url || ''} onChange={e => setWhitelabel({...whitelabel, support_url: e.target.value})} />
              </div>
            </div>

            <div style={{ marginTop: '10px' }}>
              <button type="submit" className="button primary"><Save size={16} /> {t('Save Branding')}</button>
            </div>
          </form>
        </div>
      </section>

      {currentUser?.role === 'admin' && (
        <section className="premium-section" style={{ gridColumn: '1 / -1' }}>
          <div className="premium-section-header">
            <h3><Image size={18} /> {t('Global Brand Assets')}</h3>
            <p>{t('These assets apply globally when no reseller branding matches. Maximum file size: 1MB.')}</p>
          </div>
          <div className="premium-section-body">
            <div className="premium-assets-grid">
              
              <div className="premium-asset-card">
                <div className="premium-asset-preview">
                  {panelSettings?.logo_url ? <img src={panelSettings.logo_url} alt="Logo" /> : <Image size={40} className="empty-icon" />}
                </div>
                <div className="premium-asset-info">
                  <h4 className="premium-asset-title">{t('Panel Logo')}</h4>
                  <div className="premium-asset-actions">
                    <label className="premium-file-btn">
                      <input type="file" accept="image/png,image/jpeg,image/webp,image/x-icon" onChange={e => setPanelLogoFile(e.target.files?.[0] || null)} />
                      {panelLogoFile ? <span style={{maxWidth:'100px',overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap'}}>{panelLogoFile.name}</span> : <><Image size={14} /> {t('Choose file')}</>}
                    </label>
                    <button type="button" className="premium-upload-btn" disabled={!!loading || !panelLogoFile} onClick={() => uploadPanelAsset('logo')}>
                      <Upload size={14} /> {t('Upload')}
                    </button>
                  </div>
                </div>
              </div>

              <div className="premium-asset-card">
                <div className="premium-asset-preview">
                  {panelSettings?.favicon_url ? <img src={panelSettings.favicon_url} alt="Favicon" style={{ maxHeight: '48px' }} /> : <Image size={40} className="empty-icon" />}
                </div>
                <div className="premium-asset-info">
                  <h4 className="premium-asset-title">{t('Favicon')}</h4>
                  <div className="premium-asset-actions">
                    <label className="premium-file-btn">
                      <input type="file" accept="image/png,image/jpeg,image/webp,image/x-icon" onChange={e => setPanelFaviconFile(e.target.files?.[0] || null)} />
                      {panelFaviconFile ? <span style={{maxWidth:'100px',overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap'}}>{panelFaviconFile.name}</span> : <><Image size={14} /> {t('Choose file')}</>}
                    </label>
                    <button type="button" className="premium-upload-btn" disabled={!!loading || !panelFaviconFile} onClick={() => uploadPanelAsset('favicon')}>
                      <Upload size={14} /> {t('Upload')}
                    </button>
                  </div>
                </div>
              </div>

              <div className="premium-asset-card">
                <div className="premium-asset-preview">
                  {panelSettings?.login_background_url ? <img src={panelSettings.login_background_url} alt="Login Bg" style={{ objectFit: 'cover', width: '100%', height: '100%' }} /> : <Image size={40} className="empty-icon" />}
                </div>
                <div className="premium-asset-info">
                  <h4 className="premium-asset-title">{t('Login Background')}</h4>
                  <div className="premium-asset-actions">
                    <label className="premium-file-btn">
                      <input type="file" accept="image/png,image/jpeg,image/webp" onChange={e => setPanelLoginBgFile(e.target.files?.[0] || null)} />
                      {panelLoginBgFile ? <span style={{maxWidth:'100px',overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap'}}>{panelLoginBgFile.name}</span> : <><Image size={14} /> {t('Choose file')}</>}
                    </label>
                    <button type="button" className="premium-upload-btn" disabled={!!loading || !panelLoginBgFile} onClick={() => uploadPanelAsset('login_background')}>
                      <Upload size={14} /> {t('Upload')}
                    </button>
                  </div>
                </div>
              </div>

            </div>
          </div>
        </section>
      )}

        <div className="section">
<div className="section-title" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <h3>{t('Billing & API Webhooks')}</h3>
            <button className="button small" onClick={() => { setWebhookForm({id: null, url: '', secret: '', events: '*', is_active: true}); setShowWebhookForm(true); }}>
              <Plus size={14} /> {t('Add Webhook')}
            </button>
          </div>
          
          <p className="hint" style={{ marginBottom: '1rem' }}>
            {t('Webhooks allow external billing systems (like WHMCS, Blesta) to be notified of panel events.')}
          </p>

          {showWebhookForm && (
            <form className="form-grid" onSubmit={saveWebhook} style={{ background: 'var(--surface)', padding: '1rem', borderRadius: '8px', marginBottom: '1rem', border: '1px solid var(--border)' }}>
              <label>
                <span>{t('Endpoint URL')}</span>
                <input type="url" value={webhookForm.url} onChange={e => setWebhookForm({...webhookForm, url: e.target.value})} required />
              </label>
              <label>
                <span>{t('HMAC Secret')} (Optional)</span>
                <input type="text" value={webhookForm.secret} onChange={e => setWebhookForm({...webhookForm, secret: e.target.value})} placeholder="Shared secret for X-SPanel-Signature" />
              </label>
              <label>
                <span>{t('Events')}</span>
                <input type="text" value={webhookForm.events} onChange={e => setWebhookForm({...webhookForm, events: e.target.value})} placeholder="*, provisioning_create, etc" required />
              </label>
              <label className="check-line">
                <input type="checkbox" checked={webhookForm.is_active} onChange={e => setWebhookForm({...webhookForm, is_active: e.target.checked})} />
                {t('Active')}
              </label>
              <div className="form-actions">
                <button type="button" className="button" onClick={() => setShowWebhookForm(false)}>{t('Cancel')}</button>
                <button type="submit" className="button primary">{t('Save')}</button>
              </div>
            </form>

          

          )}

          <div className="data-table">
            <table>
              <thead>
                <tr>
                  <th>URL</th>
                  <th>Events</th>
                  <th>Status</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {webhooks.length === 0 && (
                  <tr><td colSpan="4" className="empty-state">{t('No webhooks configured.')}</td></tr>
                )}
                {webhooks.map(wh => (
                  <tr key={wh.id}>
                    <td>
                      <div style={{ wordBreak: 'break-all', fontSize: '0.9em' }}>{wh.url}</div>
                    </td>
                    <td><code>{wh.events}</code></td>
                    <td>{wh.is_active ? <span style={{ color: 'var(--success)' }}>Active</span> : <span style={{ color: 'var(--text-muted)' }}>Inactive</span>}</td>
                    <td className="actions">
                      <button className="button icon-only" title="Edit" onClick={() => { setWebhookForm(wh); setShowWebhookForm(true); }}><Edit2 size={16} /></button>
                      <button className="button icon-only danger" title="Delete" onClick={() => deleteWebhook(wh.id)}><Trash2 size={16} /></button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </div>
  );
}


