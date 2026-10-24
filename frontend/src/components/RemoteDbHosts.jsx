import React, { useState } from 'react';
import { Check, Database, Edit2, Plus, ShieldCheck, Trash2, Wifi, X } from 'lucide-react';
import { EmptyState } from './EmptyState.jsx';
import { t } from '../i18n.js';

// Remote MySQL / MariaDB hosts: the Multi-Server page's "Remote MySQL" tab.
// Customers pick one of these on the Databases page; the admin adds, edits
// and tests them here. The admin password is never sent back - leaving the
// field empty when editing keeps the stored one.

const EMPTY_FORM = {
  id: null,
  name: '',
  host: '',
  port: 3306,
  admin_user: 'root',
  admin_password: '',
  use_tls: false,
  ca_cert: '',
  allowed_client_hosts: '',
  enabled: true,
};

const labelStyle = { display: 'block', fontSize: '0.78rem', fontWeight: 600, color: 'var(--text-muted)', marginBottom: '5px' };

export function RemoteDbHostsPanel({ request, hosts, loading, onChanged }) {
  const [form, setForm] = useState(null);

  function startAdd() {
    setForm({ ...EMPTY_FORM });
  }

  function startEdit(host) {
    setForm({
      id: host.id,
      name: host.name || '',
      host: host.host || '',
      port: host.port || 3306,
      admin_user: host.admin_user || 'root',
      admin_password: '',
      use_tls: !!host.use_tls,
      ca_cert: host.ca_cert || '',
      allowed_client_hosts: host.allowed_client_hosts || '',
      enabled: host.enabled !== false,
      has_password: !!host.has_password,
      database_count: host.database_count || 0,
    });
  }

  async function submit(e) {
    e.preventDefault();
    const editing = form.id != null;
    const body = {
      name: form.name.trim(),
      host: form.host.trim(),
      port: Number(form.port || 3306),
      admin_user: form.admin_user.trim() || 'root',
      use_tls: !!form.use_tls,
      ca_cert: form.use_tls ? form.ca_cert : '',
      allowed_client_hosts: form.allowed_client_hosts.trim(),
      enabled: !!form.enabled,
    };
    if (form.admin_password) body.admin_password = form.admin_password;
    const res = await request(
      editing ? `/multi-server/remote-db/${form.id}` : '/multi-server/remote-db',
      { method: editing ? 'PUT' : 'POST', body: JSON.stringify(body) },
      t('Saving and testing the database host...'),
    );
    if (res) {
      setForm(null);
      await onChanged();
    }
  }

  async function testHost(host) {
    await request(`/multi-server/remote-db/${host.id}/test`, { method: 'POST' }, t('Testing the connection...'));
    await onChanged();
  }

  async function removeHost(host) {
    if (!confirm(t('Remove database host {name} ({address})?', { name: host.name, address: `${host.host}:${host.port}` }))) return;
    const res = await request(`/multi-server/remote-db/${host.id}`, { method: 'DELETE' }, t('Removing...'));
    if (res) await onChanged();
  }

  return (
    <div className="pkg-card" style={{ padding: '20px 22px' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '12px', marginBottom: '16px' }}>
        <div>
          <h3 style={{ margin: 0, fontSize: '1.02rem', fontWeight: 700, color: 'var(--text-main)' }}>
            {t('Remote MySQL / MariaDB hosts')}
          </h3>
          <p style={{ margin: '3px 0 0', fontSize: '0.81rem', color: 'var(--text-muted)' }}>
            {t('Customers can create databases on these servers from the Databases page. Backups, suspension, quotas and phpMyAdmin follow each database to its host.')}
          </p>
        </div>
        {!form && <button type="button" onClick={startAdd}><Plus size={15} /> {t('Add database host')}</button>}
      </div>

      {form && (
        <form
          onSubmit={submit}
          style={{
            padding: '16px', borderRadius: '10px', background: 'var(--surface-2)', border: '1px solid var(--border-subtle)',
            marginBottom: '18px', display: 'flex', flexDirection: 'column', gap: '14px',
          }}
        >
          <div style={{ fontWeight: 700, fontSize: '0.9rem', color: 'var(--text-main)' }}>
            {form.id != null ? t('Edit database host') : t('Add database host')}
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: '12px' }}>
            <label style={{ margin: 0 }}>
              <span style={labelStyle}>{t('Name')}</span>
              <input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="MariaDB 01" required />
            </label>
            <label style={{ margin: 0 }}>
              <span style={labelStyle}>{t('IP address or hostname')}</span>
              <input
                value={form.host}
                onChange={(e) => setForm({ ...form, host: e.target.value })}
                placeholder="10.0.0.25 / db1.example.com"
                disabled={form.id != null && form.database_count > 0}
                title={form.id != null && form.database_count > 0 ? t('A host with databases on it cannot change address.') : ''}
                required
              />
            </label>
            <label style={{ margin: 0 }}>
              <span style={labelStyle}>{t('Port')}</span>
              <input
                type="number" min="1" max="65535" value={form.port}
                onChange={(e) => setForm({ ...form, port: e.target.value })}
                disabled={form.id != null && form.database_count > 0}
              />
            </label>
            <label style={{ margin: 0 }}>
              <span style={labelStyle}>{t('Admin user')}</span>
              <input value={form.admin_user} onChange={(e) => setForm({ ...form, admin_user: e.target.value })} placeholder="root" />
            </label>
            <label style={{ margin: 0 }}>
              <span style={labelStyle}>{t('Admin password')}</span>
              <input
                type="password"
                autoComplete="new-password"
                value={form.admin_password}
                onChange={(e) => setForm({ ...form, admin_password: e.target.value })}
                placeholder={form.has_password ? t('(leave empty to keep)') : '••••••••••••'}
              />
            </label>
            <label style={{ margin: 0 }}>
              <span style={labelStyle}>{t('Allowed client hosts')}</span>
              <input
                value={form.allowed_client_hosts}
                onChange={(e) => setForm({ ...form, allowed_client_hosts: e.target.value })}
                placeholder={t('auto: this server\'s address')}
              />
            </label>
          </div>
          <p className="hint" style={{ margin: 0 }}>
            {t('Customer accounts are created only for these client hosts (IP, 10.0.0.%, or address/netmask), never for %. Leave empty to use the address this server reaches the host from; set it when NAT or several web servers are involved.')}
          </p>
          <div style={{ display: 'flex', gap: '18px', flexWrap: 'wrap' }}>
            <label className="check-line" style={{ margin: 0 }}>
              <input type="checkbox" checked={!!form.use_tls} onChange={(e) => setForm({ ...form, use_tls: e.target.checked })} />
              <span>{t('Use TLS (verify the server certificate)')}</span>
            </label>
            <label className="check-line" style={{ margin: 0 }}>
              <input type="checkbox" checked={!!form.enabled} onChange={(e) => setForm({ ...form, enabled: e.target.checked })} />
              <span>{t('Offer to customers for new databases')}</span>
            </label>
          </div>
          {form.use_tls && (
            <label style={{ margin: 0 }}>
              <span style={labelStyle}>{t('CA certificate (PEM, optional)')}</span>
              <textarea
                rows={5}
                value={form.ca_cert}
                onChange={(e) => setForm({ ...form, ca_cert: e.target.value })}
                placeholder="-----BEGIN CERTIFICATE-----"
                style={{ fontFamily: 'monospace', fontSize: '0.78rem', width: '100%' }}
              />
              <span className="hint">{t('Without one, the system trust store is used. The certificate must name the address entered above.')}</span>
            </label>
          )}
          <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '8px' }}>
            <button type="button" className="secondary-light" onClick={() => setForm(null)}><X size={15} /> {t('Cancel')}</button>
            <button type="submit" disabled={!!loading}><Check size={15} /> {t('Save and test connection')}</button>
          </div>
        </form>
      )}

      <div className="pkg-table-wrap">
        <table className="pkg-clean-table">
          <thead>
            <tr>
              <th>{t('Name')}</th>
              <th>{t('Address')}</th>
              <th>{t('Version')}</th>
              <th>{t('Status')}</th>
              <th>{t('Databases')}</th>
              <th style={{ textAlign: 'right' }}>{t('Actions')}</th>
            </tr>
          </thead>
          <tbody>
            {hosts.map((host) => {
              const isOnline = host.status === 'online';
              return (
                <tr key={host.id}>
                  <td>
                    <strong style={{ color: 'var(--text-main)' }}>{host.name}</strong>
                    {!host.enabled && <span className="badge" style={{ marginLeft: '6px' }}>{t('Disabled')}</span>}
                    <div style={{ fontSize: '0.74rem', color: 'var(--text-muted)' }}>
                      {t('User')}: <code>{host.admin_user}</code>
                      {host.use_tls && <> • <ShieldCheck size={11} /> TLS{host.has_ca_cert ? ' + CA' : ''}</>}
                    </div>
                  </td>
                  <td>
                    <code>{host.host}:{host.port}</code>
                    <div style={{ fontSize: '0.74rem', color: 'var(--text-muted)' }}>
                      {t('Clients')}: {host.allowed_client_hosts || t('auto')}
                    </div>
                  </td>
                  <td><code style={{ fontSize: '0.78rem' }}>{host.server_version || '—'}</code></td>
                  <td>
                    <span
                      title={host.last_error || ''}
                      style={{
                        padding: '3px 9px', borderRadius: '999px', fontSize: '0.74rem', fontWeight: 700,
                        background: isOnline ? 'rgba(16, 185, 129, 0.14)' : 'rgba(239, 68, 68, 0.14)',
                        color: isOnline ? '#10b981' : '#ef4444',
                      }}
                    >
                      {isOnline ? `${t('Online')} (${host.latency_ms ?? 1}ms)` : (host.status === 'unknown' ? t('Not tested') : t('Offline'))}
                    </span>
                    {!isOnline && host.last_error && (
                      <div style={{ fontSize: '0.72rem', color: 'var(--text-muted)', maxWidth: '320px', marginTop: '4px' }}>{host.last_error}</div>
                    )}
                  </td>
                  <td>{host.database_count || 0}</td>
                  <td style={{ textAlign: 'right' }}>
                    <div style={{ display: 'inline-flex', gap: '6px' }}>
                      <button type="button" className="secondary-light" style={{ padding: '6px 10px', fontSize: '0.77rem' }} disabled={!!loading} onClick={() => testHost(host)}>
                        <Wifi size={13} /> {t('Test')}
                      </button>
                      <button type="button" className="secondary-light" style={{ padding: '6px 10px' }} disabled={!!loading} onClick={() => startEdit(host)} title={t('Edit')} aria-label={`${t('Edit')} ${host.name}`}>
                        <Edit2 size={14} />
                      </button>
                      <button
                        type="button" className="danger" style={{ padding: '6px 10px' }}
                        disabled={!!loading || host.database_count > 0}
                        title={host.database_count > 0 ? t('Delete or move its databases first') : t('Delete')}
                        aria-label={`${t('Delete')} ${host.name}`}
                        onClick={() => removeHost(host)}
                      >
                        <Trash2 size={14} />
                      </button>
                    </div>
                  </td>
                </tr>
              );
            })}
            {hosts.length === 0 && (
              <tr>
                <td colSpan="6">
                  <EmptyState
                    icon={Database}
                    message={t('No remote database hosts yet. Databases are created on the local MariaDB until one is added here.')}
                  />
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
