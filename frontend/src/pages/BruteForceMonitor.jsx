import React, { useState, useEffect } from 'react';
import { Shield, ShieldAlert, ShieldCheck, RefreshCw, Trash2, AlertCircle, Lock, Activity, Play, RotateCcw, Download } from 'lucide-react';
import { useAppContext } from '../contexts/AppContext.jsx';
import { t } from '../i18n.js';

export function BruteForceMonitorPage({ request, isAdmin }) {
  const { setNotice, loadAddons } = useAppContext();
  const [status, setStatus] = useState(null);
  const [banned, setBanned] = useState({ items: [], total: 0 });
  const [loading, setLoading] = useState(false);
  const [selectedJail, setSelectedJail] = useState('sshd');
  const [page, setPage] = useState(0);
  const [blockForm, setBlockForm] = useState({ ip: '' });

  const limit = 50;

  async function loadData() {
    setLoading(true);
    const statusData = await request('/fail2ban/status');
    if (statusData) {
      setStatus(statusData);
      const activeJails = statusData.jails?.length ? statusData.jails : ['sshd'];
      if (!activeJails.includes(selectedJail)) {
        setSelectedJail(activeJails[0]);
      }
    }
    setLoading(false);
  }

  async function loadBanned() {
    const jailToQuery = selectedJail || 'sshd';
    setLoading(true);
    const data = await request(`/fail2ban/banned?jail=${encodeURIComponent(jailToQuery)}&limit=${limit}&offset=${page * limit}`, { silent: true });
    if (data) setBanned({ items: data.items || [], total: data.total || (data.items || []).length });
    setLoading(false);
  }

  useEffect(() => {
    if (isAdmin) loadData();
  }, [isAdmin]);

  useEffect(() => {
    if (isAdmin && status?.running) loadBanned();
  }, [isAdmin, selectedJail, page, status?.running]);

  async function handleServiceAction(action) {
    setLoading(true);
    const res = await request('/fail2ban/service', {
      method: 'POST',
      body: JSON.stringify({ action }),
    });
    if (res) {
      setStatus(res);
      if (loadAddons) await loadAddons();
      setNotice(
        action === 'restart'
          ? t('Fail2Ban service restarted.')
          : t('Fail2Ban service started.')
      );
      if (res.running) {
        await loadBanned();
      }
    }
    setLoading(false);
  }

  async function handleBlockIp(e) {
    if (e) e.preventDefault();
    const ipToBlock = blockForm.ip.trim();
    if (!ipToBlock) return;
    setLoading(true);
    const banRes = await request('/fail2ban/ban', {
      method: 'POST',
      body: JSON.stringify({ ip: ipToBlock, jail: selectedJail || 'sshd' }),
    }, { silent: true });
    const fwRes = await request('/firewall/block-ip', {
      method: 'POST',
      body: JSON.stringify({ ip: ipToBlock }),
    });
    setLoading(false);
    if (banRes || fwRes) {
      setNotice(t('IP {ip} has been blocked.', { ip: ipToBlock }));
      setBlockForm({ ip: '' });
      await loadData();
      await loadBanned();
    }
  }

  async function handleUnban(ip) {
    if (!window.confirm(t('Are you sure you want to unban IP {ip}?', { ip }))) return;
    const res = await request('/fail2ban/unban', {
      method: 'POST',
      body: JSON.stringify({ ip, jail: selectedJail || 'sshd' }),
    });
    if (res) {
      setNotice(t('IP {ip} has been unbanned successfully.', { ip }));
      await loadBanned();
      await loadData();
    }
  }

  if (!isAdmin) return null;

  const jailOptions = status?.jails?.length ? status.jails : ['sshd'];

  return (
    <div className="section">
      <div className="section-title" style={{ marginBottom: '14px' }}>
        <div>
          <h2 style={{ display: 'flex', alignItems: 'center', gap: '10px', margin: 0 }}>
            <ShieldAlert size={19} />
            <span>{t('Brute Force Monitor (Fail2Ban)')}</span>
          </h2>
          <p className="hint" style={{ marginTop: '4px' }}>
            {t('Monitor SSH, Nginx, Postfix, and Dovecot intrusion attempts, unban legitimate IPs, or block attackers manually.')}
          </p>
        </div>
        <div className="cb-toolbar-actions" style={{ display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
          {status && !status.installed && (
            <button type="button" className="cb-btn primary" onClick={() => handleServiceAction('install')} disabled={loading}>
              <Download size={14} /> {t('Install & Enable Fail2Ban')}
            </button>
          )}
          {status && status.installed && !status.running && (
            <button type="button" className="cb-btn primary" onClick={() => handleServiceAction('start')} disabled={loading}>
              <Play size={14} /> {t('Start Fail2Ban')}
            </button>
          )}
          {status && status.running && (
            <button type="button" className="cb-btn secondary" onClick={() => handleServiceAction('restart')} disabled={loading}>
              <RotateCcw size={14} /> {t('Restart Fail2Ban')}
            </button>
          )}
          <button type="button" className="cb-btn secondary" onClick={() => { loadData(); if (status?.running) loadBanned(); }} disabled={loading}>
            <RefreshCw size={14} /> {t('Refresh')}
          </button>
        </div>
      </div>

      {/* KPI Strip */}
      <div className="cb-stats-strip">
        <div className="cb-stat-box">
          <div className={`cb-stat-icon ${status?.running ? 'emerald' : 'amber'}`}><Shield size={18} /></div>
          <div className="cb-stat-info">
            <span className="cb-stat-label">{t('Fail2Ban Engine')}</span>
            <strong>
              {status ? (status.running ? t('Running') : t('Stopped')) : t('Loading...')}
              <small className="cb-stat-sub">{status?.installed ? t('Installed') : t('Not Installed')}</small>
            </strong>
          </div>
        </div>
        <div className="cb-stat-box">
          <div className="cb-stat-icon rose"><Lock size={18} /></div>
          <div className="cb-stat-info">
            <span className="cb-stat-label">{t('Total Banned IPs')}</span>
            <strong>{status?.banned ?? 0} <small className="cb-stat-sub">{t('Currently blocked')}</small></strong>
          </div>
        </div>
        <div className="cb-stat-box">
          <div className="cb-stat-icon amber"><AlertCircle size={18} /></div>
          <div className="cb-stat-info">
            <span className="cb-stat-label">{t('Failed Login Attempts')}</span>
            <strong>{status?.total_failed ?? 0} <small className="cb-stat-sub">{t('Detected across jails')}</small></strong>
          </div>
        </div>
        <div className="cb-stat-box">
          <div className="cb-stat-icon blue"><Activity size={18} /></div>
          <div className="cb-stat-info">
            <span className="cb-stat-label">{t('Active Jails')}</span>
            <strong>
              {(status?.jails || []).length} {t('Jails')}
              <small className="cb-stat-sub">{(status?.jails || []).join(', ') || '-'}</small>
            </strong>
          </div>
        </div>
      </div>

      {status?.warning && (
        <div style={{ background: 'var(--warning-bg, rgba(245,158,11,0.12))', color: 'var(--warning-text, #d97706)', padding: '12px 16px', borderRadius: '10px', border: '1px solid rgba(245,158,11,0.3)', display: 'flex', alignItems: 'center', gap: '10px', marginBottom: '16px' }}>
          <AlertCircle size={16} />
          <span style={{ fontSize: '13px' }}>{status.warning}</span>
        </div>
      )}

      {/* Manual Block IP Bar */}
      <form
        onSubmit={handleBlockIp}
        style={{
          display: 'flex',
          flexWrap: 'wrap',
          alignItems: 'center',
          justifyContent: 'space-between',
          gap: '12px',
          padding: '14px 18px',
          background: 'var(--surface)',
          border: '1px solid var(--border)',
          borderRadius: '12px',
          marginBottom: '16px',
        }}
      >
        <div>
          <strong style={{ fontSize: '14px', display: 'block' }}>{t('Manual IP Block (Firewall Drop)')}</strong>
          <span className="hint" style={{ fontSize: '12.5px' }}>{t('Immediately block an offending IPv4/IPv6 address or CIDR at the host firewall.')}</span>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap' }}>
          <input
            type="text"
            value={blockForm.ip}
            onChange={e => setBlockForm({ ip: e.target.value })}
            placeholder={t('Enter IP or CIDR (e.g. 203.0.113.50)...')}
            style={{ width: '250px', height: '34px' }}
          />
          <button type="submit" className="cb-btn danger" disabled={loading || !blockForm.ip.trim()}>
            <Lock size={13} /> {t('Block IP')}
          </button>
        </div>
      </form>

      {/* Banned IPs Table */}
      <div style={{ border: '1px solid var(--border)', borderRadius: '12px', overflow: 'hidden', background: 'var(--surface)' }}>
        <div style={{ background: 'var(--surface-alt)', padding: '14px 18px', borderBottom: '1px solid var(--border)', display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '10px' }}>
          <div>
            <strong style={{ fontSize: '15px', display: 'block' }}>{t('Banned IPs by Jail')}</strong>
            <span className="hint" style={{ fontSize: '12.5px' }}>{t('IP addresses currently blocked by Fail2Ban in the selected jail')}</span>
          </div>
          <select
            value={selectedJail}
            onChange={e => { setSelectedJail(e.target.value); setPage(0); }}
            style={{ width: 'auto', minWidth: '170px', height: '34px' }}
          >
            {jailOptions.map(j => <option key={j} value={j}>{t('Jail')}: {j}</option>)}
          </select>
        </div>

        {banned.items.length === 0 ? (
          <div style={{ padding: '44px 20px', textAlign: 'center' }}>
            <ShieldCheck size={42} style={{ opacity: 0.35, marginBottom: '10px' }} />
            <h3 style={{ margin: '0 0 6px', fontSize: '15px' }}>{t('No banned IPs in this jail')}</h3>
            <p className="hint" style={{ margin: 0 }}>{t('There are no IP addresses currently banned in the selected jail.')}</p>
          </div>
        ) : (
          <div style={{ width: '100%', overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', textAlign: 'left', fontSize: '13.5px' }}>
              <thead>
                <tr style={{ background: 'var(--surface-alt)', borderBottom: '1px solid var(--border)' }}>
                  <th style={{ padding: '12px 18px' }}>{t('IP Address')}</th>
                  <th style={{ padding: '12px 18px' }}>{t('Jail')}</th>
                  <th style={{ padding: '12px 18px', textAlign: 'right', width: '140px' }}>{t('Actions')}</th>
                </tr>
              </thead>
              <tbody>
                {banned.items.map((item, idx) => {
                  const ipAddr = typeof item === 'string' ? item : (item?.ip || item?.address || '');
                  return (
                    <tr key={ipAddr || idx} style={{ borderBottom: '1px solid var(--border)' }}>
                      <td style={{ padding: '12px 18px' }}><code>{ipAddr}</code></td>
                      <td style={{ padding: '12px 18px' }}><span className="badge">{selectedJail}</span></td>
                      <td style={{ padding: '12px 18px', textAlign: 'right' }}>
                        <button type="button" className="cb-btn danger" onClick={() => handleUnban(ipAddr)}>
                          <Trash2 size={13} /> {t('Unban')}
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}

        {banned.total > limit && (
          <div style={{ padding: '12px 18px', background: 'var(--surface-alt)', borderTop: '1px solid var(--border)', display: 'flex', justifyContent: 'center', alignItems: 'center', gap: '12px' }}>
            <button type="button" className="cb-btn secondary" disabled={page === 0} onClick={() => setPage(p => p - 1)}>{t('Previous')}</button>
            <span className="hint">{t('Page')} {page + 1} {t('of')} {Math.ceil(banned.total / limit)}</span>
            <button type="button" className="cb-btn secondary" disabled={(page + 1) * limit >= banned.total} onClick={() => setPage(p => p + 1)}>{t('Next')}</button>
          </div>
        )}
      </div>
    </div>
  );
}
