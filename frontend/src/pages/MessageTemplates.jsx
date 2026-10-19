import React, { useState, useEffect } from 'react';
import { Save, RefreshCw } from 'lucide-react';
import { t } from '../i18n.js';
import { useAppContext } from '../contexts/AppContext.jsx';

export default function MessageTemplates({ request }) {
  const { loading, setLoading, setError, setNotice } = useAppContext();
  const [activeTab, setActiveTab] = useState('user_welcome');
  
  const [templates, setTemplates] = useState({
    user_welcome: { subject: '', body: '' },
    reseller_welcome: { subject: '', body: '' },
    user_suspension: { subject: '', body: '' },
  });

  const loadTemplates = () => {
    setLoading(t('Loading...'));
    request('/message-templates')
      .then(res => {
        const loaded = {
          user_welcome: { subject: 'Welcome to your new account', body: 'Hello {username},\n\nYour account has been created!\n\nDomain: {domain}\nUsername: {username}\nPassword: {password}\n\nLogin at: {login_url}' },
          reseller_welcome: { subject: 'Welcome to your reseller account', body: 'Hello {username},\n\nYour reseller account has been created!\n\nUsername: {username}\nPassword: {password}\n\nLogin at: {login_url}' },
          user_suspension: { subject: 'Account suspended', body: 'Hello {username},\n\nYour account has been suspended.\n\nReason: {reason}' },
        };
        if (Array.isArray(res)) {
          res.forEach(t => {
            if (loaded[t.template_type]) {
              loaded[t.template_type] = { subject: t.subject, body: t.body };
            }
          });
        }
        setTemplates(loaded);
      })
      .catch(err => {
        setError(t('Failed to load templates'));
        console.error(err);
      })
      .finally(() => setLoading(''));
  };

  useEffect(() => {
    loadTemplates();
  }, []);

  const handleSave = () => {
    setLoading(t('Saving...'));
    const payload = templates[activeTab];
    request(`/message-templates/${activeTab}`, { method: 'PUT', body: JSON.stringify(payload) })
      .then(res => {
        if (res) setNotice(t('Template saved successfully'));
      })
      .catch(err => {
        setError(err.detail || t('Failed to save template'));
      })
      .finally(() => setLoading(''));
  };

  const currentTemplate = templates[activeTab] || { subject: '', body: '' };

  return (
    <div className="section message-templates-page">
      <div className="section-title">
        <div>
          <h2>{t('Message templates')}</h2>
          <p className="hint">{t('Customize the automated emails sent to users.')}</p>
        </div>
        <button className="secondary-light" onClick={loadTemplates} disabled={loading}>
          <RefreshCw size={14} />{t('Refresh')}
        </button>
      </div>

      <div className="tabs" style={{ marginBottom: '20px', display: 'flex', gap: '10px' }}>
        <button className={activeTab === 'user_welcome' ? 'active primary' : 'secondary-light'} onClick={() => setActiveTab('user_welcome')}>User Welcome</button>
        <button className={activeTab === 'reseller_welcome' ? 'active primary' : 'secondary-light'} onClick={() => setActiveTab('reseller_welcome')}>Reseller Welcome</button>
        <button className={activeTab === 'user_suspension' ? 'active primary' : 'secondary-light'} onClick={() => setActiveTab('user_suspension')}>User Suspension</button>
      </div>

      <div className="card form-card">
        <div className="form-group">
          <label>{t('Email Subject')}</label>
          <input 
            type="text" 
            value={currentTemplate.subject} 
            onChange={(e) => setTemplates({...templates, [activeTab]: {...currentTemplate, subject: e.target.value}})} 
            disabled={loading}
          />
        </div>
        <div className="form-group">
          <label>{t('Email Body')}</label>
          <textarea 
            rows={15}
            value={currentTemplate.body} 
            onChange={(e) => setTemplates({...templates, [activeTab]: {...currentTemplate, body: e.target.value}})} 
            disabled={loading}
            style={{ width: '100%', padding: '10px', borderRadius: '4px', border: '1px solid var(--border)', background: 'var(--input-bg)', color: 'var(--text)' }}
          />
          <p className="hint" style={{ marginTop: '10px' }}>
            {t('Available variables')}: {'{username}'}, {'{password}'}, {'{domain}'}, {'{login_url}'}, {'{reason}'}
          </p>
        </div>
        <div className="form-actions">
          <button className="primary" onClick={handleSave} disabled={loading}>
            <Save size={16}/> {t('Save Template')}
          </button>
        </div>
      </div>
    </div>
  );
}
