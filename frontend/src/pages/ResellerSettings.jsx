import React, { useState, useEffect } from 'react';
import { Save, Image, Upload, Edit2, Palette, Globe, LifeBuoy, Sparkles } from 'lucide-react';
import { useAppContext } from '../contexts/AppContext.jsx';
import { t } from '../i18n.js';
import '../premium-branding.css';

const BRAND_COLOR_PRESETS = [
  { label: 'Emerald Green', color: '#16a34a' },
  { label: 'Teal Cyan', color: '#0d9488' },
  { label: 'Royal Indigo', color: '#4f46e5' },
  { label: 'Ocean Blue', color: '#2563eb' },
  { label: 'Violet Purple', color: '#7c3aed' },
  { label: 'Rose Crimson', color: '#e11d48' },
  { label: 'Amber Gold', color: '#d97706' },
];

export function ResellerSettingsPage({ request, API, panelSettings, setPanelSettings, loadPanelSettings }) {
  const { loading, setLoading, setError, setNotice, currentUser } = useAppContext();

  const [panelLogoFile, setPanelLogoFile] = useState(null);
  const [panelFaviconFile, setPanelFaviconFile] = useState(null);
  const [panelLoginBgFile, setPanelLoginBgFile] = useState(null);

  const [whitelabel, setWhitelabel] = useState({
    company_name: '',
    panel_domain: '',
    logo_url: '',
    primary_color: '#16a34a',
    support_url: '',
  });

  useEffect(() => {
    loadWhitelabel();
  }, []);

  async function loadWhitelabel() {
    setLoading(t('Loading...'));
    try {
      const wlRes = await request('/resellers/whitelabel');
      setWhitelabel(
        wlRes || {
          company_name: 'SPanel',
          panel_domain: '',
          logo_url: '',
          primary_color: '#16a34a',
          support_url: '',
        }
      );
    } catch (e) {
      setError(e.message);
    }
    setLoading('');
  }

  async function saveWhitelabel(e) {
    e.preventDefault();
    setLoading(t('Saving...'));
    try {
      const res = await request('/resellers/whitelabel', {
        method: 'PUT',
        body: JSON.stringify(whitelabel),
      });
      if (res) {
        setWhitelabel(res);
        if (loadPanelSettings) await loadPanelSettings();
        setNotice(t('White-label settings saved.'));
      }
    } catch (e) {
      setError(e.message);
    }
    setLoading('');
  }

  async function uploadPanelAsset(kind) {
    const file =
      kind === 'logo'
        ? panelLogoFile
        : kind === 'favicon'
        ? panelFaviconFile
        : panelLoginBgFile;
    if (!file) return;
    const body = new FormData();
    body.append('file', file);
    const data = await request(
      `/panel-settings/${kind}`,
      { method: 'POST', body },
      `Uploading ${kind}...`
    );
    if (data) {
      setPanelSettings(data);
      if (kind === 'logo') setPanelLogoFile(null);
      if (kind === 'favicon') setPanelFaviconFile(null);
      if (kind === 'login_background') setPanelLoginBgFile(null);
      setNotice(t(`${kind} uploaded successfully!`));
    }
  }

  return (
    <div className="reseller-settings-page" style={{ display: 'flex', flexDirection: 'column', gap: '20px' }}>
      {/* Header Banner */}
      <div className="pkg-card" style={{ padding: '20px 24px' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '16px' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '14px' }}>
            <div style={{
              width: '44px', height: '44px', borderRadius: '12px',
              background: 'rgba(13, 148, 136, 0.12)', color: 'var(--accent-primary)',
              display: 'flex', alignItems: 'center', justifyContent: 'center',
            }}>
              <Palette size={22} />
            </div>
            <div>
              <h2 style={{ margin: 0, fontSize: '1.2rem', fontWeight: 700, color: 'var(--text-main)' }}>
                {t('Customize Skin & White-Label Branding')}
              </h2>
              <p style={{ margin: '4px 0 0', fontSize: '0.85rem', color: 'var(--text-muted)' }}>
                {t('Customize the panel appearance, brand identity, logo, favicon, and login screen for your customers.')}
              </p>
            </div>
          </div>

          {/* Live Brand Badge Preview */}
          <div style={{
            display: 'inline-flex', alignItems: 'center', gap: '10px',
            padding: '8px 14px', borderRadius: '10px',
            background: 'var(--surface-2)', border: '1px solid var(--border-subtle)',
          }}>
            <span style={{
              width: '18px', height: '18px', borderRadius: '50%',
              background: whitelabel.primary_color || '#16a34a',
              boxShadow: '0 0 0 3px rgba(0,0,0,0.08)',
            }} />
            <span style={{ fontSize: '0.83rem', fontWeight: 700, color: 'var(--text-main)' }}>
              {whitelabel.company_name || 'SPanel'}
            </span>
            {whitelabel.panel_domain && (
              <code style={{ fontSize: '0.76rem', color: 'var(--text-muted)' }}>
                {whitelabel.panel_domain}
              </code>
            )}
          </div>
        </div>
      </div>

      {/* Section 1: White-Label Branding */}
      <section className="premium-section">
        <div className="premium-section-header">
          <h3><Edit2 size={18} /> {t('White-Label Branding')}</h3>
          <p>{t('Customize the panel appearance for your customers.')}</p>
        </div>
        <div className="premium-section-body">
          <form className="premium-form-grid" onSubmit={saveWhitelabel}>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(min(280px, 100%), 1fr))', gap: '20px' }}>
              <div className="premium-form-group">
                <label>{t('Company Name')}</label>
                <input
                  type="text"
                  className="premium-input"
                  placeholder="Your Company LLC"
                  value={whitelabel.company_name || ''}
                  onChange={(e) => setWhitelabel({ ...whitelabel, company_name: e.target.value })}
                  required
                />
              </div>

              <div className="premium-form-group">
                <label>
                  <Globe size={13} style={{ marginRight: '4px', verticalAlign: 'middle' }} />
                  {t('Panel Domain')} <span style={{ fontWeight: 'normal', color: 'var(--text-muted)' }}>({t('Optional')})</span>
                </label>
                <input
                  type="text"
                  className="premium-input"
                  placeholder="cp.yourdomain.com"
                  value={whitelabel.panel_domain || ''}
                  onChange={(e) => setWhitelabel({ ...whitelabel, panel_domain: e.target.value })}
                />
              </div>
            </div>

            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(min(280px, 100%), 1fr))', gap: '20px' }}>
              <div className="premium-form-group">
                <label>
                  {t('Logo URL')} <span style={{ fontWeight: 'normal', color: 'var(--text-muted)' }}>({t('Optional')})</span>
                </label>
                <input
                  type="url"
                  className="premium-input"
                  placeholder="https://yourdomain.com/logo.png"
                  value={whitelabel.logo_url || ''}
                  onChange={(e) => setWhitelabel({ ...whitelabel, logo_url: e.target.value })}
                />
              </div>

              <div className="premium-form-group">
                <label>
                  <LifeBuoy size={13} style={{ marginRight: '4px', verticalAlign: 'middle' }} />
                  {t('Support Ticket URL')} <span style={{ fontWeight: 'normal', color: 'var(--text-muted)' }}>({t('Optional')})</span>
                </label>
                <input
                  type="url"
                  className="premium-input"
                  placeholder="https://my.yourdomain.com/submitticket.php"
                  value={whitelabel.support_url || ''}
                  onChange={(e) => setWhitelabel({ ...whitelabel, support_url: e.target.value })}
                />
              </div>
            </div>

            <div className="premium-form-group">
              <label>{t('Primary Color')}</label>
              <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: '12px' }}>
                <div className="premium-color-picker-wrap" style={{ minWidth: '220px' }}>
                  <input
                    type="color"
                    className="premium-color-input"
                    value={whitelabel.primary_color || '#16a34a'}
                    onChange={(e) => setWhitelabel({ ...whitelabel, primary_color: e.target.value })}
                  />
                  <input
                    type="text"
                    className="premium-input"
                    value={whitelabel.primary_color || '#16a34a'}
                    onChange={(e) => setWhitelabel({ ...whitelabel, primary_color: e.target.value })}
                    style={{ flex: 1 }}
                  />
                </div>
                <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap' }}>
                  <span style={{ fontSize: '0.78rem', color: 'var(--text-muted)', display: 'inline-flex', alignItems: 'center', gap: '4px' }}>
                    <Sparkles size={13} /> {t('Presets')}:
                  </span>
                  {BRAND_COLOR_PRESETS.map((preset) => {
                    const active = (whitelabel.primary_color || '').toLowerCase() === preset.color.toLowerCase();
                    return (
                      <button
                        key={preset.color}
                        type="button"
                        title={preset.label}
                        onClick={() => setWhitelabel({ ...whitelabel, primary_color: preset.color })}
                        style={{
                          width: '26px',
                          height: '26px',
                          borderRadius: '50%',
                          padding: 0,
                          background: preset.color,
                          border: active ? '2px solid var(--text-main)' : '2px solid transparent',
                          boxShadow: active ? '0 0 0 2px var(--surface-1)' : 'none',
                          cursor: 'pointer',
                        }}
                      />
                    );
                  })}
                </div>
              </div>
            </div>

            <div style={{ marginTop: '6px', display: 'flex', justifyContent: 'flex-end' }}>
              <button type="submit" className="button primary" disabled={!!loading}>
                <Save size={16} /> {t('Save Branding')}
              </button>
            </div>
          </form>
        </div>
      </section>

      {/* Section 2: Global Brand Assets (Admin Only) */}
      {currentUser?.role === 'admin' && (
        <section className="premium-section">
          <div className="premium-section-header">
            <h3><Image size={18} /> {t('Global Brand Assets')}</h3>
            <p>{t('These assets apply globally when no reseller branding matches. Maximum file size: 1MB.')}</p>
          </div>
          <div className="premium-section-body">
            <div className="premium-assets-grid">
              <div className="premium-asset-card">
                <div className="premium-asset-preview">
                  {panelSettings?.logo_url ? (
                    <img src={panelSettings.logo_url} alt="Logo" />
                  ) : (
                    <Image size={40} className="empty-icon" />
                  )}
                </div>
                <div className="premium-asset-info">
                  <h4 className="premium-asset-title">{t('Panel Logo')}</h4>
                  <div className="premium-asset-actions">
                    <label className="premium-file-btn">
                      <input
                        type="file"
                        accept="image/png,image/jpeg,image/webp,image/x-icon"
                        onChange={(e) => setPanelLogoFile(e.target.files?.[0] || null)}
                      />
                      {panelLogoFile ? (
                        <span style={{ maxWidth: '100px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                          {panelLogoFile.name}
                        </span>
                      ) : (
                        <>
                          <Image size={14} /> {t('Choose file')}
                        </>
                      )}
                    </label>
                    <button
                      type="button"
                      className="premium-upload-btn"
                      disabled={!!loading || !panelLogoFile}
                      onClick={() => uploadPanelAsset('logo')}
                    >
                      <Upload size={14} /> {t('Upload')}
                    </button>
                  </div>
                </div>
              </div>

              <div className="premium-asset-card">
                <div className="premium-asset-preview">
                  {panelSettings?.favicon_url ? (
                    <img src={panelSettings.favicon_url} alt="Favicon" style={{ maxHeight: '48px' }} />
                  ) : (
                    <Image size={40} className="empty-icon" />
                  )}
                </div>
                <div className="premium-asset-info">
                  <h4 className="premium-asset-title">{t('Favicon')}</h4>
                  <div className="premium-asset-actions">
                    <label className="premium-file-btn">
                      <input
                        type="file"
                        accept="image/png,image/jpeg,image/webp,image/x-icon"
                        onChange={(e) => setPanelFaviconFile(e.target.files?.[0] || null)}
                      />
                      {panelFaviconFile ? (
                        <span style={{ maxWidth: '100px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                          {panelFaviconFile.name}
                        </span>
                      ) : (
                        <>
                          <Image size={14} /> {t('Choose file')}
                        </>
                      )}
                    </label>
                    <button
                      type="button"
                      className="premium-upload-btn"
                      disabled={!!loading || !panelFaviconFile}
                      onClick={() => uploadPanelAsset('favicon')}
                    >
                      <Upload size={14} /> {t('Upload')}
                    </button>
                  </div>
                </div>
              </div>

              <div className="premium-asset-card">
                <div className="premium-asset-preview">
                  {panelSettings?.login_background_url ? (
                    <img
                      src={panelSettings.login_background_url}
                      alt="Login Bg"
                      style={{ objectFit: 'cover', width: '100%', height: '100%' }}
                    />
                  ) : (
                    <Image size={40} className="empty-icon" />
                  )}
                </div>
                <div className="premium-asset-info">
                  <h4 className="premium-asset-title">{t('Login Background')}</h4>
                  <div className="premium-asset-actions">
                    <label className="premium-file-btn">
                      <input
                        type="file"
                        accept="image/png,image/jpeg,image/webp"
                        onChange={(e) => setPanelLoginBgFile(e.target.files?.[0] || null)}
                      />
                      {panelLoginBgFile ? (
                        <span style={{ maxWidth: '100px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                          {panelLoginBgFile.name}
                        </span>
                      ) : (
                        <>
                          <Image size={14} /> {t('Choose file')}
                        </>
                      )}
                    </label>
                    <button
                      type="button"
                      className="premium-upload-btn"
                      disabled={!!loading || !panelLoginBgFile}
                      onClick={() => uploadPanelAsset('login_background')}
                    >
                      <Upload size={14} /> {t('Upload')}
                    </button>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </section>
      )}
    </div>
  );
}
