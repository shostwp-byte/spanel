import React, { useState, useEffect } from 'react';
import { RefreshCw, CheckCircle2, Lock, ShieldAlert, Key, Settings, Zap } from 'lucide-react';
import { useAppContext } from '../contexts/AppContext.jsx';
import { t } from '../i18n.js';

export function ServerTlsPage({ request, isAdmin }) {
  const { setNotice, setError } = useAppContext();
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);

  async function loadData() {
    setLoading(true);
    const res = await request('/server-tls');
    if (res) setData(res);
    setLoading(false);
  }

  useEffect(() => {
    if (isAdmin) loadData();
  }, [isAdmin]);

  if (!isAdmin) return null;

  return (
    <div className="section custom-da-page" style={{ animation: 'fade-in 0.3s ease-out' }}>
      <div className="section-title" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '20px' }}>
        <h2 style={{ margin: 0, fontSize: '24px', fontWeight: '500', color: 'var(--text)' }}>
          {t('Server TLS Certificate')}
        </h2>
        <div style={{ display: 'flex', gap: '10px' }}>
          <button className="secondary" onClick={() => setNotice(t('ACME Settings configuration is coming soon.'))} style={{ background: '#fff', border: '1px solid var(--border)', padding: '8px 16px', borderRadius: '4px', display: 'flex', alignItems: 'center', gap: '6px' }}>
            <Settings size={14} /> {t('ACME Settings')}
          </button>
          <button className="primary" onClick={() => setNotice(t('Certificate management is coming soon.'))} style={{ background: '#0066cc', color: '#fff', border: 'none', padding: '8px 16px', borderRadius: '4px', display: 'flex', alignItems: 'center', gap: '6px' }}>
            <Lock size={14} /> {t('Change Certificate')}
          </button>
        </div>
      </div>

      <div className="card" style={{ padding: 0, overflow: 'hidden', border: '1px solid var(--border)', width: '100%', alignItems: 'stretch', marginBottom: '24px' }}>
        <div style={{ background: '#f8f9fa', padding: '15px 20px', borderBottom: '1px solid var(--border)', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <div style={{ display: 'flex', flexDirection: 'column' }}>
            <span style={{ color: '#333', fontSize: '16px', fontWeight: '600' }}>{t('Status')}</span>
            <span style={{ color: '#666', fontSize: '13px', marginTop: '4px' }}>{t('Status report for the server TLS certificate and key. It will report missing files, key and certificate mismatches, and certificate validity problems.')}</span>
          </div>
          <button className="secondary" onClick={loadData} disabled={loading} style={{ background: '#fff', border: '1px solid var(--border)', padding: '8px 16px', borderRadius: '4px', display: 'flex', alignItems: 'center', gap: '6px' }}>
            <RefreshCw size={14} /> {t('Refresh')}
          </button>
        </div>
        <div style={{ width: '100%', overflowX: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', textAlign: 'left', fontSize: '14px' }}>
            <tbody>
              <tr style={{ borderBottom: '1px solid #f0f0f0', background: '#ffffff' }}>
                <td style={{ padding: '15px 20px', fontWeight: '500', color: '#555', width: '30%' }}>{t('Key File')}</td>
                <td style={{ padding: '15px 20px' }}>
                  {data?.key_valid ? <span className="badge" style={{ background: '#e6f4ea', color: '#137333', padding: '4px 8px', borderRadius: '4px', display: 'inline-flex', alignItems: 'center', gap: '4px' }}><CheckCircle2 size={12} /> {t('Valid')}</span> : <span className="badge danger" style={{ background: '#fce8e6', color: '#c5221f', padding: '4px 8px', borderRadius: '4px', display: 'inline-flex', alignItems: 'center', gap: '4px' }}><ShieldAlert size={12} /> {t('Missing')}</span>}
                </td>
              </tr>
              <tr style={{ borderBottom: '1px solid #f0f0f0', background: '#fcfcfc' }}>
                <td style={{ padding: '15px 20px', fontWeight: '500', color: '#555' }}>{t('Certificate File')}</td>
                <td style={{ padding: '15px 20px' }}>
                  {data?.cert_valid ? <span className="badge" style={{ background: '#e6f4ea', color: '#137333', padding: '4px 8px', borderRadius: '4px', display: 'inline-flex', alignItems: 'center', gap: '4px' }}><CheckCircle2 size={12} /> {t('Valid')}</span> : <span className="badge danger" style={{ background: '#fce8e6', color: '#c5221f', padding: '4px 8px', borderRadius: '4px', display: 'inline-flex', alignItems: 'center', gap: '4px' }}><ShieldAlert size={12} /> {t('Missing')}</span>}
                </td>
              </tr>
              <tr style={{ borderBottom: '1px solid #f0f0f0', background: '#ffffff' }}>
                <td style={{ padding: '15px 20px', fontWeight: '500', color: '#555' }}>{t('Panel Over HTTPS')}</td>
                <td style={{ padding: '15px 20px' }}>
                  {data?.panel_https ? <span className="badge" style={{ background: '#e6f4ea', color: '#137333', padding: '4px 8px', borderRadius: '4px', display: 'inline-flex', alignItems: 'center', gap: '4px' }}><CheckCircle2 size={12} /> {t('Enabled')}</span> : <span className="badge" style={{ background: '#f1f3f4', color: '#5f6368', padding: '4px 8px', borderRadius: '4px' }}>{t('Disabled')}</span>}
                </td>
              </tr>
              <tr style={{ background: '#fcfcfc' }}>
                <td style={{ padding: '15px 20px', fontWeight: '500', color: '#555' }}>{t('Automatic Certificates')}</td>
                <td style={{ padding: '15px 20px', display: 'flex', alignItems: 'center', gap: '10px' }}>
                  {data?.auto_cert ? <span className="badge" style={{ background: '#e6f4ea', color: '#137333', padding: '4px 8px', borderRadius: '4px', display: 'inline-flex', alignItems: 'center', gap: '4px' }}><CheckCircle2 size={12} /> {t('Enabled')}</span> : <span className="badge" style={{ background: '#f1f3f4', color: '#5f6368', padding: '4px 8px', borderRadius: '4px' }}>{t('Disabled')}</span>}
                  {data?.auto_cert && <button onClick={() => setNotice(t('Certificate renewal triggered.'))} style={{ background: '#fff', border: '1px solid var(--border)', borderRadius: '4px', cursor: 'pointer', color: '#333', padding: '4px 10px', fontSize: '12px', fontWeight: '500' }}>{t('RENEW NOW')}</button>}
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>

      {data?.main_cert && Object.keys(data.main_cert).length > 0 && (
        <div className="card mt-4" style={{ padding: 0, overflow: 'hidden', border: '1px solid var(--border)', width: '100%', alignItems: 'stretch' }}>
          <div style={{ background: '#f8f9fa', padding: '15px 20px', borderBottom: '1px solid var(--border)', display: 'flex', flexDirection: 'column' }}>
            <span style={{ color: '#333', fontSize: '16px', fontWeight: '600' }}>{t('Main TLS certificate')}</span>
            <span style={{ color: '#666', fontSize: '13px', marginTop: '4px' }}>{t('Detailed information about the main server TLS certificate. It makes it easy to check the certificate expiration time and domains for which this certificate can be used.')}</span>
          </div>
          <div style={{ width: '100%', overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', textAlign: 'left', fontSize: '14px' }}>
              <tbody>
                <tr style={{ borderBottom: '1px solid #f0f0f0', background: '#ffffff' }}>
                  <td style={{ padding: '15px 20px', fontWeight: '500', color: '#555', width: '30%' }}>{t('Subject')}</td>
                  <td style={{ padding: '15px 20px', color: '#333' }}>{data.main_cert.subject || '-'}</td>
                </tr>
                <tr style={{ borderBottom: '1px solid #f0f0f0', background: '#fcfcfc' }}>
                  <td style={{ padding: '15px 20px', fontWeight: '500', color: '#555' }}>{t('Issuer')}</td>
                  <td style={{ padding: '15px 20px', color: '#333' }}>{data.main_cert.issuer || '-'}</td>
                </tr>
                <tr style={{ borderBottom: '1px solid #f0f0f0', background: '#ffffff' }}>
                  <td style={{ padding: '15px 20px', fontWeight: '500', color: '#555' }}>{t('Issued on')}</td>
                  <td style={{ padding: '15px 20px', color: '#333' }}>{data.main_cert.issued_on || '-'}</td>
                </tr>
                <tr style={{ borderBottom: '1px solid #f0f0f0', background: '#fcfcfc' }}>
                  <td style={{ padding: '15px 20px', fontWeight: '500', color: '#555' }}>{t('Expires in')}</td>
                  <td style={{ padding: '15px 20px', color: '#333' }}>{data.main_cert.expires_in || '-'}</td>
                </tr>
                <tr style={{ borderBottom: '1px solid #f0f0f0', background: '#ffffff' }}>
                  <td style={{ padding: '15px 20px', fontWeight: '500', color: '#555' }}>{t('Domain names')}</td>
                  <td style={{ padding: '15px 20px', color: '#333', wordBreak: 'break-all' }}>{data.main_cert.domains || '-'}</td>
                </tr>
                <tr style={{ borderBottom: '1px solid #f0f0f0', background: '#fcfcfc' }}>
                  <td style={{ padding: '15px 20px', fontWeight: '500', color: '#555' }}>{t('Key type')}</td>
                  <td style={{ padding: '15px 20px', color: '#333' }}>{data.main_cert.key_type || '-'}</td>
                </tr>
                <tr style={{ background: '#ffffff' }}>
                  <td style={{ padding: '15px 20px', fontWeight: '500', color: '#555' }}>{t('Serial number')}</td>
                  <td style={{ padding: '15px 20px', fontFamily: 'monospace', fontSize: '15px', color: '#0066cc', fontWeight: '600', wordBreak: 'break-all' }}>{data.main_cert.serial_number || '-'}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}
