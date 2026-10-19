import React, { useState, useEffect } from 'react';
import { Save, Server, Globe, Plus, HelpCircle, ChevronDown, Trash2 } from 'lucide-react';
import { useAppContext } from '../contexts/AppContext.jsx';
import { t } from '../i18n.js';

export function NameServersPage({ request, isAdmin }) {
  const { setNotice, setError } = useAppContext();
  const [loading, setLoading] = useState(false);
  const [ns1, setNs1] = useState('');
  const [ns2, setNs2] = useState('');
  const [ips, setIps] = useState([]);

  useEffect(() => {
    if (!isAdmin) return;
    loadData();
  }, [isAdmin]);

  async function loadData() {
    setLoading(true);
    try {
      const res = await request('/name-servers');
      if (res) {
        let loadedNs1 = res.ns1 || '';
        let loadedNs2 = res.ns2 || '';
        
        const serverIp = res.server_ip || '127.0.0.1';
        const autoDomain = `server-${serverIp.replace(/\./g, '-')}.spanel.direct`;
        
        if (loadedNs1 === 'ns1.yourdomain.com') loadedNs1 = `ns1.${autoDomain}`;
        if (loadedNs2 === 'ns2.yourdomain.com') loadedNs2 = `ns2.${autoDomain}`;
        
        setNs1(loadedNs1);
        setNs2(loadedNs2);
      }
      
      try {
        const ipRes = await request('/ips');
        let loadedIps = [];
        if (Array.isArray(ipRes)) {
          loadedIps = ipRes.map(ip => ({
            ip: ip.ip_address,
            status: ip.is_shared ? 'shared' : 'server',
            users: ip.assigned_user_id ? 1 : 0
          }));
        } else if (ipRes && ipRes.items) {
          loadedIps = ipRes.items;
        }
        if (loadedIps.length === 0) {
          loadedIps = [{ ip: res?.server_ip || '127.0.0.1', status: 'server', users: 1 }];
        }
        setIps(loadedIps);
      } catch (e) {
        setIps([{ ip: res?.server_ip || '127.0.0.1', status: 'server', users: 1 }]);
      }
      
    } catch (e) {
      setNs1('ns1.example.com');
      setNs2('ns2.example.com');
    }
    setLoading(false);
  }

  async function handleSave(e) {
    e.preventDefault();
    setLoading(true);
    try {
      const res = await request('/name-servers', {
        method: 'POST',
        body: JSON.stringify({ ns1, ns2 })
      });
      if (res) {
        setNotice(t('Name servers updated successfully.'));
      }
    } catch (e) {
      setError(e.message || t('Failed to update name servers.'));
    }
    setLoading(false);
  }

  if (!isAdmin) return null;

  return (
    <div style={{ animation: 'fade-in 0.3s ease-out' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '15px', flexWrap: 'wrap', gap: '10px' }}>
        <h2 style={{ fontSize: '24px', fontWeight: '400', color: 'var(--text)', margin: 0, display: 'flex', alignItems: 'center', gap: '8px' }}>
          {t('Name Servers')}
          <HelpCircle size={16} color="var(--text-muted)" style={{ cursor: 'pointer' }} />
        </h2>
        <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: '10px' }}>
          <button className="primary" style={{ display: 'flex', alignItems: 'center', gap: '6px', background: '#0066cc', color: '#fff', border: 'none', padding: '8px 16px', borderRadius: '4px', fontSize: '13px', fontWeight: '500', cursor: 'pointer', textTransform: 'uppercase' }}>
            <Plus size={16} /> {t('Create Name Servers')}
          </button>
        </div>
      </div>

      <div style={{ display: 'flex', justifyContent: 'flex-end', marginBottom: '10px', gap: '10px' }}>
        <button style={{ display: 'flex', alignItems: 'center', gap: '8px', background: 'var(--surface)', border: '1px solid var(--border)', padding: '6px 12px', borderRadius: '4px', fontSize: '13px', color: 'var(--text)', cursor: 'pointer' }}>
          Columns <ChevronDown size={14} />
        </button>
        <button disabled style={{ display: 'flex', alignItems: 'center', gap: '6px', background: '#93c5fd', color: '#fff', border: 'none', padding: '6px 16px', borderRadius: '4px', fontSize: '13px', cursor: 'not-allowed' }}>
          {t('Delete')}
        </button>
      </div>

      <div style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: '4px', overflow: 'hidden', marginBottom: '40px', boxShadow: '0 1px 3px rgba(0,0,0,0.02)' }}>
        <div style={{ width: '100%', overflowX: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', textAlign: 'left', fontSize: '13px' }}>
            <thead>
              <tr style={{ background: 'var(--surface-alt)', borderBottom: '1px solid var(--border)' }}>
                <th style={{ padding: '12px 15px', width: '40px' }}><input type="checkbox" disabled /></th>
                <th style={{ padding: '12px 15px', fontWeight: '600', color: 'var(--text)' }}>{t('IP')}</th>
                <th style={{ padding: '12px 15px', fontWeight: '600', color: 'var(--text)' }}>{t('Status')}</th>
                <th style={{ padding: '12px 15px', fontWeight: '600', color: 'var(--text)' }}>{t('User(s)')}</th>
                <th style={{ padding: '12px 15px', fontWeight: '600', color: 'var(--text)' }}>{t('Name Server')}</th>
              </tr>
            </thead>
            <tbody>
              {ips.length > 0 ? ips.map((ipObj, idx) => (
                <tr key={idx} style={{ borderBottom: '1px solid var(--border)' }}>
                  <td style={{ padding: '12px 15px' }}><input type="checkbox" /></td>
                  <td style={{ padding: '12px 15px', color: 'var(--text-secondary)' }}>{ipObj.ip}</td>
                  <td style={{ padding: '12px 15px', color: 'var(--text-secondary)' }}>{ipObj.status || 'server'}</td>
                  <td style={{ padding: '12px 15px', color: 'var(--text-secondary)' }}>{ipObj.users || '-'}</td>
                  <td style={{ padding: '12px 15px', color: 'var(--text-secondary)' }}></td>
                </tr>
              )) : (
                <tr>
                  <td colSpan="5" style={{ padding: '30px', textAlign: 'center', color: 'var(--text-muted)' }}>{t('No data available in table')}</td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      <div>
        <h3 style={{ fontSize: '18px', fontWeight: '600', color: 'var(--text)', margin: '0 0 20px 0' }}>
          {t('Set the Name servers that will be assigned to new users')}
        </h3>
        
        <form onSubmit={handleSave} style={{ maxWidth: '100%' }}>
          <div style={{ marginBottom: '20px' }}>
            <label style={{ display: 'block', marginBottom: '8px', fontSize: '14px', color: 'var(--text-secondary)' }}>
              {t('Name Server 1')}
            </label>
            <input 
              type="text" 
              value={ns1} 
              onChange={e => setNs1(e.target.value)}
              style={{ width: 'min(450px, 100%)', padding: '10px 14px', border: '1px solid var(--border)', borderRadius: '4px', fontSize: '14px', background: 'var(--surface)', color: 'var(--text)', outline: 'none' }}
              required
            />
          </div>

          <div style={{ marginBottom: '25px' }}>
            <label style={{ display: 'block', marginBottom: '8px', fontSize: '14px', color: 'var(--text-secondary)' }}>
              {t('Name Server 2')}
            </label>
            <input 
              type="text" 
              value={ns2} 
              onChange={e => setNs2(e.target.value)}
              style={{ width: 'min(450px, 100%)', padding: '10px 14px', border: '1px solid var(--border)', borderRadius: '4px', fontSize: '14px', background: 'var(--surface)', color: 'var(--text)', outline: 'none' }}
              required
            />
          </div>

          <button 
            type="submit" 
            disabled={loading}
            style={{ background: '#10b981', color: '#fff', border: 'none', padding: '10px 24px', borderRadius: '4px', fontSize: '13px', fontWeight: '600', cursor: 'pointer', display: 'inline-flex', alignItems: 'center', gap: '6px', textTransform: 'uppercase' }}
          >
            <Save size={16} /> {loading ? t('Saving...') : t('Save')}
          </button>
        </form>
      </div>
    </div>
  );
}
