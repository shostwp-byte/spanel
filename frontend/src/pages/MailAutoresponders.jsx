import React, { useState, useEffect } from 'react';
import { HelpCircle, Plus, Search, ChevronDown, RefreshCw, Trash2, Pencil, X, Save } from 'lucide-react';
import { useAppContext } from '../contexts/AppContext.jsx';
import { t } from '../i18n.js';
import { EmptyState } from '../components/EmptyState.jsx';

export function MailAutorespondersPage({ request, isAdmin }) {
  const { loading, setLoading, setError, setNotice } = useAppContext();
  
  const [mailInfo, setMailInfo] = useState(null);
  const [autoresponders, setAutoresponders] = useState([]);
  const [searchTerm, setSearchTerm] = useState('');
  
  const [showCreate, setShowCreate] = useState(false);
  const [editItem, setEditItem] = useState(null);
  const emptyForm = { domain_id: '', local_part: '', subject: '', body: '', enabled: true, start_date: '', end_date: '', interval_days: 1 };
  const [form, setForm] = useState(emptyForm);

  useEffect(() => {
    loadMailInfo();
    loadAutoresponders();
  }, []);

  const loadMailInfo = async () => {
    try {
      const data = await request('/mail/overview', { silent: true });
      if (data) setMailInfo(data);
    } catch (e) {
      console.error(e);
    }
  };

  const loadAutoresponders = async () => {
    setLoading(t('Loading autoresponders...'));
    try {
      const data = await request('/mail/autoresponders?per_page=1000');
      if (data && data.items) setAutoresponders(data.items);
    } catch (e) {
      setError(e.message || t('Failed to load autoresponders'));
    }
    setLoading('');
  };

  const handleDelete = async (item) => {
    if (!confirm(t('Delete the autoresponder {address}?', { address: item.address }))) return;
    try {
      const res = await request(`/mail/autoresponders/${item.id}`, { method: 'DELETE' }, t('Deleting...'));
      if (res) {
        setNotice(t('Autoresponder deleted.'));
        loadAutoresponders();
      }
    } catch (e) {
      setError(e.message);
    }
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    try {
      let res;
      if (editItem) {
        res = await request(`/mail/autoresponders/${editItem.id}`, {
          method: 'PUT',
          body: JSON.stringify({
            subject: form.subject,
            body: form.body,
            enabled: !!form.enabled,
            start_date: form.start_date || '',
            end_date: form.end_date || '',
            interval_days: Number(form.interval_days) || 1
          })
        }, t('Saving...'));
        if (res) setNotice(t('Autoresponder updated.'));
      } else {
        res = await request('/mail/autoresponders', {
          method: 'POST',
          body: JSON.stringify({
            domain_id: parseInt(form.domain_id),
            local_part: form.local_part,
            subject: form.subject,
            body: form.body,
            enabled: !!form.enabled,
            start_date: form.start_date || '',
            end_date: form.end_date || '',
            interval_days: Number(form.interval_days) || 1
          })
        }, t('Creating...'));
        if (res) setNotice(t('Autoresponder created.'));
      }
      if (res) {
        setShowCreate(false);
        setEditItem(null);
        setForm(emptyForm);
        loadAutoresponders();
      }
    } catch (e) {
      setError(e.message);
    }
  };

  const handleEdit = (item) => {
    setEditItem(item);
    setForm({
      domain_id: '',
      local_part: item.local_part,
      subject: item.subject,
      body: item.body,
      enabled: item.enabled !== false,
      start_date: item.start_date || '',
      end_date: item.end_date || '',
      interval_days: item.interval_days || 1
    });
    setShowCreate(true);
  };

  const filteredItems = autoresponders.filter(f => 
    f.address.toLowerCase().includes(searchTerm.toLowerCase()) ||
    f.subject.toLowerCase().includes(searchTerm.toLowerCase())
  );

  const domains = mailInfo?.domains || [];

  if (showCreate) {
    return (
      <div style={{ animation: 'fade-in 0.3s ease-out' }}>
        <div style={{ marginBottom: '20px', paddingBottom: '15px', borderBottom: '1px solid var(--border)', display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '10px' }}>
          <h2 style={{ fontSize: '24px', fontWeight: '400', color: 'var(--text)', margin: 0, display: 'flex', alignItems: 'center', gap: '8px' }}>
            {editItem ? t('Edit Autoresponder') : t('Create Autoresponder')}
          </h2>
          <button onClick={() => { setShowCreate(false); setEditItem(null); }} style={{ background: 'var(--surface-alt)', border: '1px solid var(--border)', padding: '6px 12px', borderRadius: '4px', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: '6px', color: 'var(--text)', fontSize: '13px' }}>
            <X size={14} /> {t('Back')}
          </button>
        </div>

        <div style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: '4px', padding: '20px', boxShadow: '0 1px 3px rgba(0,0,0,0.02)' }}>
          <form onSubmit={handleSubmit}>
            
            <div style={{ marginBottom: '20px' }}>
              <label style={{ display: 'block', marginBottom: '8px', fontSize: '14px', fontWeight: '500', color: 'var(--text-secondary)' }}>
                {t('Responder Address')}
              </label>
              <div style={{ display: 'flex', gap: '10px', alignItems: 'center', flexWrap: 'wrap' }}>
                <input 
                  type="text" 
                  value={editItem ? editItem.local_part : form.local_part}
                  onChange={e => !editItem && setForm({...form, local_part: e.target.value.toLowerCase().replace(/[^a-z0-9._-]/g, '')})}
                  style={{ width: 'min(200px, 100%)', flex: '1 1 140px', maxWidth: '240px', padding: '10px 15px', border: '1px solid var(--border)', borderRadius: '4px', outline: 'none', background: editItem ? 'var(--surface-alt)' : 'var(--surface)', color: 'var(--text)' }}
                  placeholder="e.g. sales"
                  disabled={!!editItem}
                  required
                />
                <span style={{ fontSize: '18px', color: 'var(--text-muted)' }}>@</span>
                {editItem ? (
                  <input type="text" value={editItem.domain} disabled style={{ width: 'min(250px, 100%)', flex: '1 1 180px', maxWidth: '300px', padding: '10px 15px', border: '1px solid var(--border)', borderRadius: '4px', outline: 'none', background: 'var(--surface-alt)', color: 'var(--text)' }} />
                ) : (
                  <select 
                    value={form.domain_id}
                    onChange={e => setForm({...form, domain_id: e.target.value})}
                    style={{ width: 'min(250px, 100%)', flex: '1 1 180px', maxWidth: '300px', padding: '10px 15px', border: '1px solid var(--border)', borderRadius: '4px', outline: 'none', background: 'var(--surface)', color: 'var(--text)' }}
                    required
                  >
                    <option value="">{t('Select Domain')}</option>
                    {domains.map(d => <option key={d.id} value={d.id}>{d.domain}</option>)}
                  </select>
                )}
              </div>
            </div>

            <div style={{ marginBottom: '20px' }}>
              <label style={{ display: 'block', marginBottom: '8px', fontSize: '14px', fontWeight: '500', color: 'var(--text-secondary)' }}>
                {t('Subject')}
              </label>
              <input 
                type="text"
                value={form.subject}
                onChange={e => setForm({...form, subject: e.target.value})}
                style={{ width: '100%', padding: '10px 15px', border: '1px solid var(--border)', borderRadius: '4px', outline: 'none', background: 'var(--surface)', color: 'var(--text)' }}
                placeholder={t('Out of office')}
                required
              />
            </div>

            <div style={{ marginBottom: '20px' }}>
              <label style={{ display: 'block', marginBottom: '8px', fontSize: '14px', fontWeight: '500', color: 'var(--text-secondary)' }}>
                {t('Responder Message')}
              </label>
              <textarea 
                value={form.body}
                onChange={e => setForm({...form, body: e.target.value})}
                style={{ width: '100%', height: '150px', padding: '10px 15px', border: '1px solid var(--border)', borderRadius: '4px', outline: 'none', resize: 'vertical', fontFamily: 'monospace', background: 'var(--surface)', color: 'var(--text)' }}
                placeholder={t('Thank you for your email. I am currently out of the office.')}
                required
              />
            </div>

            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))', gap: '16px', marginBottom: '20px' }}>
              <label style={{ display: 'block', fontSize: '14px', fontWeight: '500', color: 'var(--text-secondary)' }}>
                {t('From (optional)')}
                <input type="date" value={form.start_date} onChange={e => setForm({ ...form, start_date: e.target.value })}
                  style={{ display: 'block', width: '100%', marginTop: '8px', padding: '8px 12px', border: '1px solid var(--border)', borderRadius: '4px', background: 'var(--surface)', color: 'var(--text)' }} />
              </label>
              <label style={{ display: 'block', fontSize: '14px', fontWeight: '500', color: 'var(--text-secondary)' }}>
                {t('Until (optional)')}
                <input type="date" value={form.end_date} min={form.start_date || undefined} onChange={e => setForm({ ...form, end_date: e.target.value })}
                  style={{ display: 'block', width: '100%', marginTop: '8px', padding: '8px 12px', border: '1px solid var(--border)', borderRadius: '4px', background: 'var(--surface)', color: 'var(--text)' }} />
              </label>
              <label style={{ display: 'block', fontSize: '14px', fontWeight: '500', color: 'var(--text-secondary)' }}>
                {t('Answer the same sender once every (days)')}
                <input type="number" min="1" max="30" value={form.interval_days} onChange={e => setForm({ ...form, interval_days: e.target.value })}
                  style={{ display: 'block', width: '100%', marginTop: '8px', padding: '8px 12px', border: '1px solid var(--border)', borderRadius: '4px', background: 'var(--surface)', color: 'var(--text)' }} />
              </label>
            </div>

            <label className="check-line" style={{ marginBottom: '8px' }}>
              <input type="checkbox" checked={!!form.enabled} onChange={e => setForm({ ...form, enabled: e.target.checked })} />
              <span>{t('Autoresponder on')}</span>
            </label>
            <p className="hint">{t('Replies go out only between the two dates (when set), never to mailing lists, automatic mail or mail marked as spam. The address must be a mailbox.')}</p>

            <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '10px', marginTop: '20px', paddingTop: '15px', borderTop: '1px solid var(--border)', flexWrap: 'wrap' }}>
              <button type="button" onClick={() => { setShowCreate(false); setEditItem(null); }} disabled={loading} style={{ background: 'var(--surface)', border: '1px solid var(--border)', padding: '8px 20px', borderRadius: '4px', cursor: 'pointer', color: 'var(--text)', fontSize: '13px', fontWeight: '500' }}>
                {t('Cancel')}
              </button>
              <button type="submit" disabled={loading} style={{ background: '#10b981', color: '#fff', border: 'none', padding: '8px 20px', borderRadius: '4px', fontSize: '13px', fontWeight: '600', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: '6px', textTransform: 'uppercase' }}>
                <Save size={16} /> {loading ? t('Saving...') : (editItem ? t('Save') : t('Create'))}
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
          {t('Autoresponders')}
          <HelpCircle size={16} color="var(--text-muted)" style={{ cursor: 'pointer' }} />
        </h2>
        <button 
          onClick={() => {
            setEditItem(null);
            setForm({ ...emptyForm, domain_id: domains[0]?.id || '' });
            setShowCreate(true);
          }}
          style={{ display: 'flex', alignItems: 'center', gap: '6px', background: '#0066cc', color: '#fff', border: 'none', padding: '8px 16px', borderRadius: '4px', fontSize: '13px', fontWeight: '500', cursor: 'pointer', textTransform: 'uppercase' }}
        >
          <Plus size={16} /> {t('CREATE AUTORESPONDER')}
        </button>
      </div>

      <div style={{ display: 'flex', alignItems: 'center', marginBottom: '20px', gap: '15px', flexWrap: 'wrap' }}>
        <span style={{ fontSize: '14px', color: 'var(--text-secondary)', fontWeight: '500' }}>{t('Search')}</span>
        <div style={{ display: 'flex', alignItems: 'center', border: '1px solid var(--border)', borderRadius: '4px', background: 'var(--surface)', overflow: 'hidden', height: '36px', flex: '1 1 240px', maxWidth: '460px' }}>
          <Search size={16} color="var(--text-muted)" style={{ marginLeft: '12px', flexShrink: 0 }} />
          <input 
            type="text" 
            value={searchTerm}
            onChange={e => setSearchTerm(e.target.value)}
            placeholder={t('Search autoresponders...')}
            style={{ flex: 1, minWidth: '100px', padding: '0 12px', border: 'none', fontSize: '13px', outline: 'none', height: '100%', background: 'var(--surface)', color: 'var(--text)' }}
          />
          <button onClick={() => setSearchTerm('')} style={{ padding: '0 15px', border: 'none', borderLeft: '1px solid var(--border)', background: 'var(--surface)', color: 'var(--text-muted)', cursor: 'pointer', display: 'flex', alignItems: 'center', height: '100%' }}>
            <X size={16} />
          </button>
        </div>
      </div>

      <div style={{ display: 'flex', justifyContent: 'flex-end', marginBottom: '10px', gap: '10px' }}>
        <button onClick={loadAutoresponders} style={{ background: 'none', border: 'none', cursor: 'pointer', color: '#3b82f6', display: 'flex', alignItems: 'center', padding: '4px' }}>
          <RefreshCw size={16} />
        </button>
      </div>

      <div style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: '4px', overflow: 'hidden', boxShadow: '0 1px 3px rgba(0,0,0,0.02)' }}>
        <div style={{ width: '100%', overflowX: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', textAlign: 'left', fontSize: '13px' }}>
            <thead>
              <tr style={{ background: 'var(--surface-alt)', borderBottom: '1px solid var(--border)' }}>
                <th style={{ padding: '15px 20px', fontWeight: '600', color: 'var(--text)', width: '35%' }}>{t('Responder Address')}</th>
                <th style={{ padding: '15px 20px', fontWeight: '600', color: 'var(--text)', width: '35%' }}>{t('Subject')}</th>
                <th style={{ padding: '15px 20px', fontWeight: '600', color: 'var(--text)', width: '15%' }}>{t('Status')}</th>
                <th style={{ padding: '15px 20px', fontWeight: '600', color: 'var(--text)', width: '15%', textAlign: 'right' }}>{t('Actions')}</th>
              </tr>
            </thead>
            <tbody>
              {filteredItems.map((item, idx) => (
                <tr key={item.id} style={{ borderBottom: '1px solid var(--border)', background: idx % 2 === 0 ? 'var(--surface)' : 'var(--surface-alt)' }}>
                  <td style={{ padding: '15px 20px', color: 'var(--primary)', fontWeight: '500' }}>{item.address}</td>
                  <td style={{ padding: '15px 20px', color: 'var(--text-secondary)' }}>{item.subject}</td>
                  <td style={{ padding: '15px 20px' }}>
                    <span className={`badge ${!item.has_mailbox ? 'bad' : item.active_now ? 'ok' : 'warn'}`}>
                      {!item.has_mailbox ? t('No mailbox') : !item.enabled ? t('Off') : item.active_now ? t('Answering') : t('Scheduled')}
                    </span>
                    {(item.start_date || item.end_date) && <small style={{ display: 'block', color: 'var(--text-muted)' }}>{item.start_date || '…'} → {item.end_date || '…'}</small>}
                  </td>
                  <td style={{ padding: '15px 20px', textAlign: 'right' }}>
                    <button onClick={() => handleEdit(item)} style={{ background: 'none', border: 'none', color: '#3b82f6', cursor: 'pointer', padding: '4px', marginRight: '8px' }} title={t('Edit')}>
                      <Pencil size={16} />
                    </button>
                    <button onClick={() => handleDelete(item)} style={{ background: 'none', border: 'none', color: '#ef4444', cursor: 'pointer', padding: '4px' }} title={t('Delete')}>
                      <Trash2 size={16} />
                    </button>
                  </td>
                </tr>
              ))}
              {filteredItems.length === 0 && (
                <tr>
                  <td colSpan="4" style={{ padding: '40px', textAlign: 'center', color: 'var(--text-muted)' }}>
                    {t('No autoresponders found')}
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
