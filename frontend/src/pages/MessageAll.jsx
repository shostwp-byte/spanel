import React, { useState } from 'react';
import { Mail, Send, AlertCircle } from 'lucide-react';
import { useAppContext } from '../contexts/AppContext.jsx';
import { t } from '../i18n.js';

export function MessageAllPage({ request, isAdmin }) {
  const { setNotice, setError } = useAppContext();
  const [loading, setLoading] = useState(false);
  const [form, setForm] = useState({
    target: 'all_users',
    priority: 'medium',
    subject: '',
    message: ''
  });

  const handleSend = async (e) => {
    e.preventDefault();
    if (!form.subject.trim() || !form.message.trim()) {
      setError(t('Subject and message cannot be empty.'));
      return;
    }
    
    if (!confirm(t('Are you sure you want to send this message to all selected users?'))) return;

    setLoading(true);
    try {
      const res = await request('/message-all', {
        method: 'POST',
        body: JSON.stringify(form)
      });
      if (res) {
        setNotice(t('Message has been added to the mail queue.'));
        setForm({ ...form, subject: '', message: '' });
      }
    } catch (e) {
      setError(e.message || t('Failed to send message.'));
    }
    setLoading(false);
  };

  if (!isAdmin) return null;

  return (
    <div style={{ animation: 'fade-in 0.3s ease-out' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '25px' }}>
        <h2 style={{ fontSize: '24px', fontWeight: '400', color: 'var(--text)', margin: 0, display: 'flex', alignItems: 'center', gap: '8px' }}>
          {t('Message All Users')}
        </h2>
      </div>

      <div style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: '4px', overflow: 'hidden', boxShadow: '0 1px 3px rgba(0,0,0,0.05)', maxWidth: '800px' }}>
        <div style={{ background: 'var(--surface-alt)', borderBottom: '1px solid var(--border)', padding: '12px 20px', fontSize: '14px', color: 'var(--text-secondary)', fontWeight: '500', display: 'flex', alignItems: 'center', gap: '8px' }}>
          <Mail size={16} /> {t('Compose Message')}
        </div>
        
        <form onSubmit={handleSend} style={{ padding: '25px' }}>
          
          <div style={{ display: 'flex', gap: '20px', marginBottom: '20px', flexWrap: 'wrap' }}>
            <div style={{ flex: '1 1 200px' }}>
              <label style={{ display: 'block', marginBottom: '8px', fontSize: '13px', fontWeight: '500', color: 'var(--text-secondary)' }}>
                {t('Send to')}
              </label>
              <select 
                value={form.target}
                onChange={e => setForm({...form, target: e.target.value})}
                style={{ width: '100%', padding: '8px 12px', border: '1px solid var(--border)', borderRadius: '4px', fontSize: '13px', outline: 'none', background: 'var(--surface)', color: 'var(--text)' }}
              >
                <option value="all_users">{t('All Users')}</option>
                <option value="resellers">{t('All Resellers')}</option>
                <option value="admin">{t('All Admins')}</option>
              </select>
            </div>
            
            <div style={{ flex: '1 1 200px' }}>
              <label style={{ display: 'block', marginBottom: '8px', fontSize: '13px', fontWeight: '500', color: 'var(--text-secondary)' }}>
                {t('Priority')}
              </label>
              <select 
                value={form.priority}
                onChange={e => setForm({...form, priority: e.target.value})}
                style={{ width: '100%', padding: '8px 12px', border: '1px solid var(--border)', borderRadius: '4px', fontSize: '13px', outline: 'none', background: 'var(--surface)', color: 'var(--text)' }}
              >
                <option value="high">{t('High')}</option>
                <option value="medium">{t('Medium')}</option>
                <option value="low">{t('Low')}</option>
              </select>
            </div>
          </div>

          <div style={{ marginBottom: '20px' }}>
            <label style={{ display: 'block', marginBottom: '8px', fontSize: '13px', fontWeight: '500', color: 'var(--text-secondary)' }}>
              {t('Subject')}
            </label>
            <input 
              type="text" 
              value={form.subject}
              onChange={e => setForm({...form, subject: e.target.value})}
              style={{ width: '100%', padding: '8px 12px', border: '1px solid var(--border)', borderRadius: '4px', fontSize: '13px', outline: 'none', background: 'var(--surface)', color: 'var(--text)' }}
              required
            />
          </div>

          <div style={{ marginBottom: '25px' }}>
            <label style={{ display: 'block', marginBottom: '8px', fontSize: '13px', fontWeight: '500', color: 'var(--text-secondary)' }}>
              {t('Message')}
            </label>
            <textarea 
              value={form.message}
              onChange={e => setForm({...form, message: e.target.value})}
              rows={12}
              style={{ width: '100%', padding: '12px', border: '1px solid var(--border)', borderRadius: '4px', fontSize: '13px', outline: 'none', resize: 'vertical', fontFamily: 'inherit', lineHeight: '1.5', background: 'var(--surface)', color: 'var(--text)' }}
              required
            ></textarea>
          </div>
          
          <div style={{ display: 'flex', alignItems: 'center', gap: '10px', background: 'var(--primary-light)', padding: '12px', borderRadius: '4px', border: '1px solid var(--border)', marginBottom: '25px' }}>
            <AlertCircle size={18} color="var(--primary)" style={{ flexShrink: 0 }} />
            <span style={{ fontSize: '13px', color: 'var(--text-secondary)' }}>
              {t("Emails will be queued and sent via the server's mail system to the registered email address of each user.")}
            </span>
          </div>

          <div style={{ display: 'flex', justifyContent: 'flex-end', paddingTop: '15px', borderTop: '1px solid var(--border)' }}>
            <button 
              type="submit" 
              disabled={loading}
              style={{ display: 'flex', alignItems: 'center', gap: '8px', padding: '10px 20px', background: '#0066cc', color: '#fff', border: 'none', borderRadius: '4px', cursor: 'pointer', fontSize: '13px', fontWeight: '500', opacity: loading ? 0.7 : 1 }}
            >
              <Send size={16} />
              {loading ? t('Sending...') : t('Send Message')}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
