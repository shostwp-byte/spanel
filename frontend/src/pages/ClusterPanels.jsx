import React, { useEffect, useState } from 'react';
import { ArrowRightLeft, RefreshCw, RotateCcw, Send, Trash2 } from 'lucide-react';
import { EmptyState } from '../components/EmptyState.jsx';
import { useAppContext } from '../contexts/AppContext.jsx';
import { t } from '../i18n.js';

/* The Multi Server page's DNS sync and account transfer tabs. Kept apart
 * from MultiServer.jsx so the node list and the Remote MySQL tab stay as
 * they were. */

const badge = (color) => ({
  padding: '2px 8px',
  borderRadius: '999px',
  fontSize: '0.72rem',
  fontWeight: 700,
  background: `${color}22`,
  color,
});

const STATUS_COLORS = {
  pending: '#3b82f6',
  conflict: '#f59e0b',
  failed: '#ef4444',
  queued: '#3b82f6',
  running: '#3b82f6',
  done: '#10b981',
};

function formatBytes(value) {
  const n = Number(value || 0);
  if (!n) return '—';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let i = 0;
  let size = n;
  while (size >= 1024 && i < units.length - 1) {
    size /= 1024;
    i += 1;
  }
  return `${size.toFixed(i ? 1 : 0)} ${units[i]}`;
}

export function ClusterDnsSyncPanel({ request }) {
  const { loading } = useAppContext();
  const [data, setData] = useState(null);

  async function load(silent = true) {
    const res = await request('/multi-server/dns-sync/status', silent ? { silent: true } : {});
    if (res) setData(res);
  }

  useEffect(() => { load(); }, []);

  async function reconcile() {
    const res = await request('/multi-server/dns-sync/reconcile', { method: 'POST' }, t('Comparing zones with every peer...'));
    if (res) await load();
  }

  async function retry(row) {
    const res = await request(`/multi-server/dns-sync/outbox/${row.id}/retry`, { method: 'POST' }, t('Queuing again...'));
    if (res) await load();
  }

  async function discard(row) {
    if (!confirm(t('Drop the queued change of {zone} for {node}?').replace('{zone}', row.zone).replace('{node}', row.node_name))) return;
    const res = await request(`/multi-server/dns-sync/outbox/${row.id}`, { method: 'DELETE' }, t('Removing...'));
    if (res) await load();
  }

  const outbox = data?.outbox || [];
  const replicas = data?.replicas || [];
  const counts = data?.counts || {};

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '20px' }}>
      <div className="pkg-card" style={{ padding: '20px 22px' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '12px', marginBottom: '14px' }}>
          <div>
            <h3 style={{ margin: 0, fontSize: '1.02rem', fontWeight: 700, color: 'var(--text-main)' }}>{t('DNS sync queue')}</h3>
            <p style={{ margin: '3px 0 0', fontSize: '0.81rem', color: 'var(--text-muted)' }}>
              {t('Zone changes waiting for a peer. Unreachable peers are retried automatically; a refused zone (owned by the peer) stays here until you retry or drop it.')}
            </p>
          </div>
          <div style={{ display: 'flex', gap: '8px' }}>
            <button type="button" className="secondary-light" disabled={!!loading} onClick={() => load(false)}>
              <RefreshCw size={14} /> {t('Refresh')}
            </button>
            <button type="button" disabled={!!loading} onClick={reconcile}>
              <RotateCcw size={14} /> {t('Reconcile now')}
            </button>
          </div>
        </div>
        <div style={{ display: 'flex', gap: '10px', flexWrap: 'wrap', marginBottom: '12px', fontSize: '0.8rem' }}>
          <span style={badge(STATUS_COLORS.pending)}>{t('Pending')}: {counts.pending || 0}</span>
          <span style={badge(STATUS_COLORS.conflict)}>{t('Conflict')}: {counts.conflict || 0}</span>
          <span style={badge(STATUS_COLORS.failed)}>{t('Gave up')}: {counts.failed || 0}</span>
        </div>
        {data?.dnssec_policy && (
          <p className="hint" style={{ fontSize: '0.78rem', margin: '0 0 12px' }}>{t(data.dnssec_policy)}</p>
        )}
        <div className="pkg-table-wrap">
          <table className="pkg-clean-table">
            <thead>
              <tr>
                <th>{t('Zone')}</th>
                <th>{t('Peer')}</th>
                <th>{t('Status')}</th>
                <th>{t('Attempts')}</th>
                <th>{t('Last error')}</th>
                <th style={{ textAlign: 'right' }}>{t('Actions')}</th>
              </tr>
            </thead>
            <tbody>
              {outbox.map((row) => (
                <tr key={row.id}>
                  <td><strong>{row.zone}</strong> <code style={{ fontSize: '0.72rem' }}>{row.action}</code></td>
                  <td>{row.node_name}</td>
                  <td><span style={badge(STATUS_COLORS[row.status] || '#64748b')}>{row.status}</span></td>
                  <td style={{ fontSize: '0.8rem' }}>
                    {row.attempts}
                    {row.status === 'pending' && row.next_attempt_at && (
                      <div style={{ fontSize: '0.72rem', color: 'var(--text-muted)' }}>
                        {t('Next')}: {new Date(row.next_attempt_at).toLocaleTimeString()}
                      </div>
                    )}
                  </td>
                  <td style={{ fontSize: '0.76rem', color: 'var(--text-muted)', maxWidth: '320px' }}>{row.last_error || '—'}</td>
                  <td style={{ textAlign: 'right' }}>
                    <div style={{ display: 'inline-flex', gap: '6px' }}>
                      <button type="button" className="secondary-light" style={{ padding: '6px 10px' }} disabled={!!loading} onClick={() => retry(row)} title={t('Retry now')}>
                        <RotateCcw size={13} />
                      </button>
                      <button type="button" className="danger" style={{ padding: '6px 10px' }} disabled={!!loading} onClick={() => discard(row)} title={t('Drop')}>
                        <Trash2 size={13} />
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
              {outbox.length === 0 && (
                <tr>
                  <td colSpan="6" style={{ textAlign: 'center', padding: '20px', color: 'var(--text-muted)' }}>
                    {t('Nothing waiting: every peer has the latest zones.')}
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      <div className="pkg-card" style={{ padding: '20px 22px' }}>
        <h3 style={{ margin: '0 0 4px', fontSize: '1.02rem', fontWeight: 700, color: 'var(--text-main)' }}>{t('Zones replicated from peers')}</h3>
        <p style={{ margin: '0 0 12px', fontSize: '0.81rem', color: 'var(--text-muted)' }}>
          {t('Copies of other nodes\' zones served here. Change them on their origin node: local edits are overwritten by its next sync.')}
        </p>
        <div className="pkg-table-wrap">
          <table className="pkg-clean-table">
            <thead>
              <tr>
                <th>{t('Zone')}</th>
                <th>{t('Origin node')}</th>
                <th>{t('Owner on origin')}</th>
                <th>{t('Origin serial')}</th>
                <th>{t('Updated')}</th>
              </tr>
            </thead>
            <tbody>
              {replicas.map((row) => (
                <tr key={row.zone}>
                  <td><strong>{row.zone}</strong></td>
                  <td>{row.origin_name || row.origin_uid}</td>
                  <td>{row.origin_owner || '—'}</td>
                  <td><code>{row.source_serial}</code></td>
                  <td style={{ fontSize: '0.78rem', color: 'var(--text-muted)' }}>{row.updated_at ? new Date(row.updated_at).toLocaleString() : '—'}</td>
                </tr>
              ))}
              {replicas.length === 0 && (
                <tr>
                  <td colSpan="5" style={{ textAlign: 'center', padding: '20px', color: 'var(--text-muted)' }}>
                    {t('This node holds no zones of other nodes.')}
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

export function ClusterTransfersPanel({ request, nodes }) {
  const { loading, setNotice } = useAppContext();
  const [users, setUsers] = useState([]);
  const [jobs, setJobs] = useState([]);
  const [placement, setPlacement] = useState(null);
  const [form, setForm] = useState({ user_id: '', node_id: '', switch_dns: false, suspend_source: false });

  const hostNodes = (nodes || []).filter((n) => n.enabled !== false && !['dns_only', 'db_node'].includes(n.role));

  async function loadJobs() {
    const res = await request('/multi-server/transfers', { silent: true });
    if (res) setJobs(res.jobs || []);
  }

  useEffect(() => {
    (async () => {
      const list = await request('/users', { silent: true });
      if (Array.isArray(list)) setUsers(list.filter((u) => u.role !== 'admin'));
      await loadJobs();
    })();
  }, []);

  const running = jobs.some((j) => ['queued', 'running'].includes(j.status));
  useEffect(() => {
    if (!running) return undefined;
    const timer = setInterval(loadJobs, 5000);
    return () => clearInterval(timer);
  }, [running]);

  useEffect(() => {
    if (!form.user_id) {
      setPlacement(null);
      return;
    }
    (async () => {
      const res = await request(`/multi-server/placement?purpose=transfer&user_id=${encodeURIComponent(form.user_id)}`, { silent: true });
      if (res) {
        setPlacement(res);
        if (res.suggested && !form.node_id) setForm((prev) => ({ ...prev, node_id: res.suggested.id }));
      }
    })();
  }, [form.user_id]);

  async function submit(e) {
    e.preventDefault();
    const user = users.find((u) => String(u.id) === String(form.user_id));
    const node = hostNodes.find((n) => n.id === form.node_id);
    if (!user || !node) return;
    if (!confirm(t('Move {user} to {node}?').replace('{user}', user.username).replace('{node}', node.name))) return;
    const res = await request('/multi-server/transfers', {
      method: 'POST',
      body: JSON.stringify({ ...form, user_id: Number(form.user_id) }),
    }, t('Starting the transfer...'));
    if (res) {
      if (res.message) setNotice(res.message);
      await loadJobs();
    }
  }

  const candidateById = Object.fromEntries((placement?.candidates || []).map((c) => [c.id, c]));

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '20px' }}>
      <form className="pkg-card" style={{ padding: '20px 22px', display: 'flex', flexDirection: 'column', gap: '14px' }} onSubmit={submit}>
        <div>
          <h3 style={{ margin: 0, fontSize: '1.02rem', fontWeight: 700, color: 'var(--text-main)' }}>{t('Move an account to another node')}</h3>
          <p style={{ margin: '3px 0 0', fontSize: '0.81rem', color: 'var(--text-muted)' }}>
            {t('A full backup, encrypted with a one-time passphrase, is uploaded to the target node and restored there. The account keeps its panel password. The archive is deleted afterwards on both nodes.')}
          </p>
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(230px, 1fr))', gap: '12px' }}>
          <label style={{ margin: 0 }}>
            <span style={{ display: 'block', fontSize: '0.78rem', fontWeight: 600, color: 'var(--text-muted)', marginBottom: '5px' }}>{t('Account')}</span>
            <select value={form.user_id} onChange={(e) => setForm({ ...form, user_id: e.target.value, node_id: '' })} required>
              <option value="">{t('-- Select account --')}</option>
              {users.map((u) => <option key={u.id} value={u.id}>{u.username}</option>)}
            </select>
          </label>
          <label style={{ margin: 0 }}>
            <span style={{ display: 'block', fontSize: '0.78rem', fontWeight: 600, color: 'var(--text-muted)', marginBottom: '5px' }}>{t('Target node')}</span>
            <select value={form.node_id} onChange={(e) => setForm({ ...form, node_id: e.target.value })} required>
              <option value="">{t('-- Select node --')}</option>
              {hostNodes.map((n) => {
                const c = candidateById[n.id];
                const suffix = c ? (c.eligible ? ` • ${t('score')} ${c.score}` : ` • ${c.reasons?.[0] || ''}`) : '';
                const star = placement?.suggested?.id === n.id ? ` ★ ${t('suggested')}` : '';
                return <option key={n.id} value={n.id}>{n.name}{suffix}{star}</option>;
              })}
            </select>
          </label>
        </div>
        {placement && (
          <div style={{ fontSize: '0.78rem', color: 'var(--text-muted)' }}>
            {t('Space the account needs')}: <strong>{formatBytes(placement.required_bytes)}</strong>
            {placement.suggested
              ? <> • {t('Suggested')}: <strong>{placement.suggested.name}</strong> ({placement.suggested.reasons?.slice(-1)[0]})</>
              : <> • {t('No node has room for this account right now.')}</>}
          </div>
        )}
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: '18px' }}>
          <label className="check-line" style={{ margin: 0 }}>
            <input type="checkbox" checked={form.switch_dns} onChange={(e) => setForm({ ...form, switch_dns: e.target.checked })} />
            <span>{t('Move DNS to the target (it becomes the zones\' master and its IP replaces this server\'s)')}</span>
          </label>
          <label className="check-line" style={{ margin: 0 }}>
            <input type="checkbox" checked={form.suspend_source} onChange={(e) => setForm({ ...form, suspend_source: e.target.checked })} />
            <span>{t('Suspend the account here afterwards')}</span>
          </label>
        </div>
        <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
          <button type="submit" disabled={!!loading || !form.user_id || !form.node_id}>
            <Send size={14} /> {t('Start transfer')}
          </button>
        </div>
      </form>

      <div className="pkg-card" style={{ padding: '20px 22px' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '12px' }}>
          <h3 style={{ margin: 0, fontSize: '1.02rem', fontWeight: 700, color: 'var(--text-main)' }}>{t('Transfers')}</h3>
          <button type="button" className="secondary-light" onClick={loadJobs}><RefreshCw size={14} /> {t('Refresh')}</button>
        </div>
        <div className="pkg-table-wrap">
          <table className="pkg-clean-table">
            <thead>
              <tr>
                <th>{t('Account')}</th>
                <th>{t('Direction')}</th>
                <th>{t('Status')}</th>
                <th>{t('Step')}</th>
                <th>{t('Size')}</th>
                <th>{t('Message')}</th>
                <th>{t('Started')}</th>
              </tr>
            </thead>
            <tbody>
              {jobs.map((job) => (
                <tr key={job.job_uid}>
                  <td><strong>{job.username}</strong></td>
                  <td style={{ fontSize: '0.8rem' }}>{job.direction === 'in' ? `← ${job.node_name}` : `→ ${job.node_name}`}</td>
                  <td><span style={badge(STATUS_COLORS[job.status] || '#64748b')}>{job.status}</span></td>
                  <td><code style={{ fontSize: '0.75rem' }}>{job.step}</code></td>
                  <td style={{ fontSize: '0.78rem' }}>{formatBytes(job.archive_size)}</td>
                  <td style={{ fontSize: '0.78rem', color: 'var(--text-muted)', maxWidth: '360px' }}>{job.message}</td>
                  <td style={{ fontSize: '0.78rem', color: 'var(--text-muted)', whiteSpace: 'nowrap' }}>{job.created_at ? new Date(job.created_at).toLocaleString() : '—'}</td>
                </tr>
              ))}
              {jobs.length === 0 && (
                <tr>
                  <td colSpan="7">
                    <EmptyState icon={ArrowRightLeft} title={t('No transfers yet')} subtitle={t('Accounts moved to or from this node appear here.')} />
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
