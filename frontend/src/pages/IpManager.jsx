import React, { useState, useEffect, useMemo } from 'react';
import { createPortal } from 'react-dom';
import {
  Plus, X, RefreshCw, Network, Globe, Trash2, Users,
  Check, Edit3, Share2, Unlock
} from 'lucide-react';
import { useAppContext } from '../contexts/AppContext.jsx';
import { t } from '../i18n.js';

const LOCAL_META_KEY = 'spanel_ip_meta_cache';

function readLocalMeta() {
  try {
    const raw = localStorage.getItem(LOCAL_META_KEY);
    return raw ? JSON.parse(raw) : {};
  } catch {
    return {};
  }
}

function writeLocalMeta(meta) {
  try {
    localStorage.setItem(LOCAL_META_KEY, JSON.stringify(meta));
  } catch {
    // ignore storage errors
  }
}

export function IpManager({ request, isAdmin, isReseller, users = [] }) {
  const { loading, setLoading, setNotice, websites = [], loadUsers } = useAppContext();
  const [ips, setIps] = useState([]);
  const [localMeta, setLocalMeta] = useState(readLocalMeta);
  const [selectedIps, setSelectedIps] = useState([]);
  const [searchQuery, setSearchQuery] = useState('');
  const [statusFilter, setStatusFilter] = useState('all');

  const [showAddModal, setShowAddModal] = useState(false);
  const [showAssignModal, setShowAssignModal] = useState(false);
  const [editingIp, setEditingIp] = useState(null);

  const [ipForm, setIpForm] = useState({
    ip_address: '',
    netmask: '/24',
    device: 'eth0',
    is_shared: false,
    assigned_user_id: '',
    nameserver: '',
    is_global: false,
    notes: '',
  });
  const [assignUserId, setAssignUserId] = useState('');

  useEffect(() => {
    loadIps();
    if ((!users || users.length === 0) && loadUsers) {
      loadUsers();
    }
  }, []);

  async function loadIps() {
    setLoading(t('Loading IP pool...'));
    try {
      const data = await request('/ips', { silent: true });
      if (data) {
        const list = Array.isArray(data) ? data : (data.items || []);
        setIps(list);
        setSelectedIps(prev => prev.filter(id => list.some(item => item.id === id)));
      }
    } finally {
      setLoading('');
    }
  }

  function updateLocalMetaForIp(addr, patch) {
    setLocalMeta(prev => {
      const next = {
        ...prev,
        [addr]: { ...(prev[addr] || {}), ...patch },
      };
      writeLocalMeta(next);
      return next;
    });
  }

  const enrichedIps = useMemo(() => {
    const usersById = {};
    (users || []).forEach(u => { usersById[u.id] = u; });

    const domainCountsByOwner = {};
    (websites || []).forEach(w => {
      if (w.owner_id) {
        domainCountsByOwner[w.owner_id] = (domainCountsByOwner[w.owner_id] || 0) + 1;
      }
    });

    return ips.map((raw, idx) => {
      const addr = String(raw.ip_address || '').trim();
      const cached = localMeta[addr] || {};
      const isV6 = addr.includes(':');
      const version = raw.version || (isV6 ? 6 : 4);
      const isShared = cached.is_shared !== undefined ? cached.is_shared : !!raw.is_shared;
      const assignedId = cached.assigned_user_id !== undefined ? cached.assigned_user_id : raw.assigned_user_id;
      const assignedUser = assignedId ? usersById[assignedId] : null;
      const isMain = raw.is_main_ip !== undefined ? raw.is_main_ip : (idx === 0 && isShared);

      let status = raw.status;
      if (!status || cached.is_shared !== undefined || cached.assigned_user_id !== undefined) {
        if (isMain && isShared) status = 'server';
        else if (isShared) status = 'shared';
        else if (assignedId) status = 'owned';
        else status = 'free';
      }

      let resellerName = raw.reseller_username || '';
      if (!resellerName && assignedUser) {
        if (assignedUser.role === 'reseller') resellerName = assignedUser.username;
        else if (assignedUser.reseller_id && usersById[assignedUser.reseller_id]) {
          resellerName = usersById[assignedUser.reseller_id].username;
        }
      }

      const assignedName = raw.assigned_username || (assignedUser ? assignedUser.username : '');
      const uCount = raw.users_count !== undefined
        ? raw.users_count
        : (isShared ? (users?.length || 1) : (assignedId ? 1 : 0));
      const dCount = raw.domains_count !== undefined
        ? raw.domains_count
        : (isShared ? (websites?.length || 0) : (assignedId ? (domainCountsByOwner[assignedId] || 0) : 0));

      return {
        ...raw,
        is_shared: isShared,
        assigned_user_id: assignedId,
        version,
        status,
        is_main_ip: isMain,
        device: cached.device || raw.device || 'eth0',
        netmask: cached.netmask || raw.netmask || (isV6 ? '/64' : '/24'),
        nameserver: cached.nameserver !== undefined ? cached.nameserver : (raw.nameserver || ''),
        is_global: cached.is_global !== undefined ? cached.is_global : !!raw.is_global,
        notes: cached.notes !== undefined ? cached.notes : (raw.notes || ''),
        reseller_username: resellerName,
        assigned_username: assignedName,
        users_count: uCount,
        domains_count: dCount,
      };
    });
  }, [ips, localMeta, users, websites]);

  const filteredIps = useMemo(() => {
    return enrichedIps.filter(ip => {
      if (statusFilter !== 'all' && ip.status !== statusFilter) return false;
      if (searchQuery.trim()) {
        const q = searchQuery.toLowerCase();
        const hay = `${ip.ip_address} ${ip.device} ${ip.reseller_username} ${ip.assigned_username} ${ip.nameserver} ${ip.notes}`.toLowerCase();
        if (!hay.includes(q)) return false;
      }
      return true;
    });
  }, [enrichedIps, statusFilter, searchQuery]);

  async function handleCreateIp(e) {
    e.preventDefault();
    const addr = ipForm.ip_address.trim();
    if (!addr) return;
    const payload = {
      ip_address: addr,
      is_shared: !!ipForm.is_shared,
      netmask: ipForm.netmask || (addr.includes(':') ? '/64' : '/24'),
      device: ipForm.device || 'eth0',
      assigned_user_id: ipForm.assigned_user_id ? Number(ipForm.assigned_user_id) : null,
      nameserver: ipForm.nameserver.trim(),
      is_global: !!ipForm.is_global,
      notes: ipForm.notes.trim(),
    };
    const res = await request('/ips', { method: 'POST', body: JSON.stringify(payload) }, t('Adding IP address...'));
    if (res) {
      updateLocalMetaForIp(addr, {
        netmask: payload.netmask,
        device: payload.device,
        nameserver: payload.nameserver,
        is_global: payload.is_global,
        notes: payload.notes,
      });
      if (!ipForm.is_shared && payload.assigned_user_id && res.id) {
        await request(`/ips/${res.id}/assign`, {
          method: 'PUT',
          body: JSON.stringify({ assigned_user_id: payload.assigned_user_id }),
          silent: true,
        });
      }
      setNotice(t('IP address {ip} added to pool.', { ip: addr }));
      setShowAddModal(false);
      setIpForm({
        ip_address: '',
        netmask: '/24',
        device: 'eth0',
        is_shared: false,
        assigned_user_id: '',
        nameserver: '',
        is_global: false,
        notes: '',
      });
      await loadIps();
    }
  }

  async function handleSaveEdit(e) {
    e.preventDefault();
    if (!editingIp) return;
    const addr = editingIp.ip_address;
    updateLocalMetaForIp(addr, {
      device: editingIp.device,
      netmask: editingIp.netmask,
      nameserver: editingIp.nameserver,
      is_global: !!editingIp.is_global,
      notes: editingIp.notes,
      is_shared: !!editingIp.is_shared,
    });
    if (!editingIp.is_shared) {
      await request(`/ips/${editingIp.id}/assign`, {
        method: 'PUT',
        body: JSON.stringify({
          assigned_user_id: editingIp.assigned_user_id ? Number(editingIp.assigned_user_id) : null,
        }),
        silent: true,
      });
    }
    setNotice(t('Updated IP {ip}.', { ip: addr }));
    setEditingIp(null);
    await loadIps();
  }

  async function handleAssignSubmit(e) {
    e.preventDefault();
    if (!assignUserId || selectedIps.length === 0) return;
    const uid = Number(assignUserId);
    setLoading(t('Assigning selected IP(s)...'));
    try {
      for (const id of selectedIps) {
        const ipObj = enrichedIps.find(x => x.id === id);
        if (!ipObj) continue;
        updateLocalMetaForIp(ipObj.ip_address, { is_shared: false, assigned_user_id: uid });
        await request(`/ips/${id}/assign`, {
          method: 'PUT',
          body: JSON.stringify({ assigned_user_id: uid }),
          silent: true,
        });
      }
      setNotice(t('Assigned {n} IP address(es).', { n: selectedIps.length }));
      setShowAssignModal(false);
      setAssignUserId('');
      setSelectedIps([]);
      await loadIps();
    } finally {
      setLoading('');
    }
  }

  async function handleBatchAction(action) {
    if (selectedIps.length === 0) return;
    if (action === 'delete') {
      if (!confirm(t('Delete {n} selected IP address(es) from the pool?', { n: selectedIps.length }))) return;
      setLoading(t('Deleting IP(s)...'));
      try {
        for (const id of selectedIps) {
          await request(`/ips/${id}`, { method: 'DELETE' });
        }
        setNotice(t('Selected IP address(es) deleted.'));
        setSelectedIps([]);
        await loadIps();
      } finally {
        setLoading('');
      }
      return;
    }

    for (const id of selectedIps) {
      const ipObj = enrichedIps.find(x => x.id === id);
      if (!ipObj) continue;
      if (action === 'share') {
        updateLocalMetaForIp(ipObj.ip_address, { is_shared: true, assigned_user_id: null });
      } else if (action === 'free' || action === 'unassign') {
        updateLocalMetaForIp(ipObj.ip_address, { is_shared: false, assigned_user_id: null });
        await request(`/ips/${id}/assign`, {
          method: 'PUT',
          body: JSON.stringify({ assigned_user_id: null }),
          silent: true,
        });
      } else if (action === 'clear_ns') {
        updateLocalMetaForIp(ipObj.ip_address, { nameserver: '' });
      } else if (action === 'toggle_global') {
        updateLocalMetaForIp(ipObj.ip_address, { is_global: !ipObj.is_global });
      }
    }
    setNotice(t('Updated {n} IP address(es).', { n: selectedIps.length }));
    setSelectedIps([]);
    await loadIps();
  }

  const toggleSelectAll = e => {
    if (e.target.checked) setSelectedIps(filteredIps.map(ip => ip.id));
    else setSelectedIps([]);
  };

  const toggleSelectIp = id => {
    setSelectedIps(prev => (prev.includes(id) ? prev.filter(x => x !== id) : [...prev, id]));
  };

  const statusBadge = status => {
    if (status === 'server') return <span className="badge ok">{t('Server Main')}</span>;
    if (status === 'shared') return <span className="badge ok">{t('Shared')}</span>;
    if (status === 'owned') return <span className="badge warn">{t('Dedicated')}</span>;
    return <span className="badge">{t('Free')}</span>;
  };

  const counts = {
    total: enrichedIps.length,
    shared: enrichedIps.filter(i => i.status === 'shared' || i.status === 'server').length,
    owned: enrichedIps.filter(i => i.status === 'owned').length,
    free: enrichedIps.filter(i => i.status === 'free').length,
  };

  return (
    <div className="section">
      <div className="section-title">
        <div>
          <h2 style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
            <Network size={18} />
            {t('IP Management')}
          </h2>
          <p className="hint">
            {t('Manage server IPv4 & IPv6 addresses, shared pools, reseller/user assignments, and nameserver bindings.')}
          </p>
        </div>
        <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: '8px' }}>
          <button type="button" className="secondary-light" disabled={!!loading} onClick={loadIps}>
            <RefreshCw size={14} /> {t('Refresh')}
          </button>
          {isAdmin && (
            <button type="button" disabled={!!loading} onClick={() => setShowAddModal(true)}>
              <Plus size={14} /> {t('Add IP')}
            </button>
          )}
        </div>
      </div>

      <div className="status-grid" style={{ '--status-cols': 4 }}>
        <div className="status-card tone-ok" onClick={() => setStatusFilter('all')}>
          <div className="status-card-head"><Network size={15} /><span>{t('Total IPs in Pool')}</span></div>
          <strong>{counts.total}</strong>
          <small>{t('IPv4 & IPv6 addresses')}</small>
        </div>
        <div className="status-card tone-ok" onClick={() => setStatusFilter('shared')}>
          <div className="status-card-head"><Share2 size={15} /><span>{t('Server / Shared')}</span></div>
          <strong>{counts.shared}</strong>
          <small>{t('Hosting shared accounts')}</small>
        </div>
        <div className="status-card tone-warn" onClick={() => setStatusFilter('owned')}>
          <div className="status-card-head"><Users size={15} /><span>{t('Dedicated / Assigned')}</span></div>
          <strong>{counts.owned}</strong>
          <small>{t('Assigned to specific user/reseller')}</small>
        </div>
        <div className="status-card" onClick={() => setStatusFilter('free')}>
          <div className="status-card-head"><Unlock size={15} /><span>{t('Free / Available')}</span></div>
          <strong>{counts.free}</strong>
          <small>{t('Ready for assignment')}</small>
        </div>
      </div>

      <div style={{ display: 'flex', flexWrap: 'wrap', justifyContent: 'space-between', alignItems: 'center', gap: '12px', padding: '12px 14px', background: 'var(--surface-alt)', border: '1px solid var(--border)', borderRadius: 'var(--radius-md)' }}>
        <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: '8px', flex: '1 1 280px' }}>
          <input
            value={searchQuery}
            onChange={e => setSearchQuery(e.target.value)}
            placeholder={t('Filter by IP, device, user, or nameserver...')}
            style={{ maxWidth: '280px', height: '34px', minHeight: '34px' }}
          />
          <div className="segmented" style={{ display: 'inline-flex', flexWrap: 'wrap', gap: '6px', background: 'transparent', border: 'none', padding: 0 }}>
            {['all', 'server', 'shared', 'owned', 'free'].map(st => (
              <button
                key={st}
                type="button"
                className={`mini ${statusFilter === st ? 'active' : 'secondary'}`}
                onClick={() => setStatusFilter(st)}
              >
                {st === 'all' ? t('All') : t(st.charAt(0).toUpperCase() + st.slice(1))}
              </button>
            ))}
          </div>
        </div>

        <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: '8px' }}>
          <button
            type="button"
            className="mini secondary"
            disabled={selectedIps.length === 0 || !!loading}
            onClick={() => setShowAssignModal(true)}
          >
            <Users size={13} /> {t('Assign')}
          </button>
          <button
            type="button"
            className="mini secondary"
            disabled={selectedIps.length === 0 || !!loading}
            onClick={() => handleBatchAction('unassign')}
          >
            <Unlock size={13} /> {t('Remove from Reseller')}
          </button>
          {isAdmin && (
            <>
              <button
                type="button"
                className="mini secondary"
                disabled={selectedIps.length === 0 || !!loading}
                onClick={() => handleBatchAction('share')}
              >
                <Share2 size={13} /> {t('Share')}
              </button>
              <button
                type="button"
                className="mini secondary"
                disabled={selectedIps.length === 0 || !!loading}
                onClick={() => handleBatchAction('free')}
              >
                {t('Free')}
              </button>
              <button
                type="button"
                className="mini secondary"
                disabled={selectedIps.length === 0 || !!loading}
                onClick={() => handleBatchAction('clear_ns')}
              >
                {t('Clear NS')}
              </button>
              <button
                type="button"
                className="mini secondary"
                disabled={selectedIps.length === 0 || !!loading}
                onClick={() => handleBatchAction('toggle_global')}
              >
                <Globe size={13} /> {t('Toggle Global')}
              </button>
              <button
                type="button"
                className="mini danger"
                disabled={selectedIps.length === 0 || !!loading}
                onClick={() => handleBatchAction('delete')}
              >
                <Trash2 size={13} /> {t('Delete')}
              </button>
            </>
          )}
        </div>
      </div>

      <div style={{ width: '100%', overflowX: 'auto', border: '1px solid var(--border)', borderRadius: 'var(--radius-md)', background: 'var(--surface)' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', textAlign: 'left', fontSize: '13.5px' }}>
          <thead>
            <tr style={{ borderBottom: '1px solid var(--border)', background: 'var(--surface-alt)', color: 'var(--text-secondary)', fontSize: '12px', textTransform: 'uppercase' }}>
              <th style={{ padding: '12px 14px', width: '36px' }}>
                <input
                  type="checkbox"
                  onChange={toggleSelectAll}
                  checked={filteredIps.length > 0 && selectedIps.length === filteredIps.length}
                />
              </th>
              <th style={{ padding: '12px 10px' }}>{t('IP Address')}</th>
              <th style={{ padding: '12px 10px' }}>{t('Status')}</th>
              <th style={{ padding: '12px 10px' }}>{t('Device / Netmask')}</th>
              <th style={{ padding: '12px 10px' }}>{t('Reseller / User')}</th>
              <th style={{ padding: '12px 10px' }}>{t('Users / Domains')}</th>
              <th style={{ padding: '12px 10px' }}>{t('Name Server')}</th>
              <th style={{ padding: '12px 14px', textAlign: 'right' }}>{t('Actions')}</th>
            </tr>
          </thead>
          <tbody>
            {filteredIps.map(ip => (
              <tr key={ip.id} style={{ borderBottom: '1px solid var(--border)' }}>
                <td style={{ padding: '12px 14px' }}>
                  <input
                    type="checkbox"
                    checked={selectedIps.includes(ip.id)}
                    onChange={() => toggleSelectIp(ip.id)}
                  />
                </td>
                <td style={{ padding: '12px 10px' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap' }}>
                    <strong style={{ color: 'var(--primary-ink)', fontFamily: 'monospace', fontSize: '14px' }}>
                      {ip.ip_address}
                    </strong>
                    <span className="badge">IPv{ip.version}</span>
                    {ip.is_global && <span className="badge ok">Global</span>}
                  </div>
                  {ip.notes && <small className="hint" style={{ display: 'block', marginTop: '2px' }}>{ip.notes}</small>}
                </td>
                <td style={{ padding: '12px 10px' }}>{statusBadge(ip.status)}</td>
                <td style={{ padding: '12px 10px' }}>
                  <code>{ip.device}</code> <span className="hint">{ip.netmask}</span>
                </td>
                <td style={{ padding: '12px 10px' }}>
                  {ip.assigned_username ? (
                    <span>
                      <strong>{ip.assigned_username}</strong>
                      {ip.reseller_username && ip.reseller_username !== ip.assigned_username && (
                        <small className="hint" style={{ display: 'block' }}>Reseller: {ip.reseller_username}</small>
                      )}
                    </span>
                  ) : (
                    <span className="hint">{ip.is_shared ? t('All Users (Shared)') : '—'}</span>
                  )}
                </td>
                <td style={{ padding: '12px 10px' }}>
                  <span>{ip.users_count} {t('users')} · {ip.domains_count} {t('domains')}</span>
                </td>
                <td style={{ padding: '12px 10px' }}>
                  {ip.nameserver ? <code>{ip.nameserver}</code> : <span className="hint">—</span>}
                </td>
                <td style={{ padding: '12px 14px', textAlign: 'right' }}>
                  <div style={{ display: 'inline-flex', gap: '6px', justifyContent: 'flex-end' }}>
                    <button
                      type="button"
                      className="mini secondary"
                      onClick={() => setEditingIp({ ...ip })}
                    >
                      <Edit3 size={13} /> {t('Edit')}
                    </button>
                    {isAdmin && !ip.is_main_ip && (
                      <button
                        type="button"
                        className="mini danger"
                        onClick={async () => {
                          if (!confirm(t('Delete IP {ip}?', { ip: ip.ip_address }))) return;
                          await request(`/ips/${ip.id}`, { method: 'DELETE' }, t('Deleting IP...'));
                          await loadIps();
                        }}
                      >
                        <Trash2 size={13} />
                      </button>
                    )}
                  </div>
                </td>
              </tr>
            ))}
            {filteredIps.length === 0 && (
              <tr>
                <td colSpan="8" style={{ padding: '32px', textAlign: 'center' }} className="hint">
                  {t('No IP addresses match the current filter.')}
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {showAddModal && createPortal(
        <div className="modal-backdrop" onClick={() => setShowAddModal(false)}>
          <div className="modal" style={{ maxWidth: '500px', width: '92%' }} onClick={e => e.stopPropagation()}>
            <div className="modal-header">
              <h3>{t('Add IP Address to Pool')}</h3>
              <button type="button" className="icon-only secondary" onClick={() => setShowAddModal(false)}><X size={16} /></button>
            </div>
            <form onSubmit={handleCreateIp}>
              <div className="modal-body" style={{ display: 'grid', gap: '12px' }}>
                <label>
                  <span>{t('IPv4 or IPv6 Address')}</span>
                  <input
                    required
                    value={ipForm.ip_address}
                    onChange={e => setIpForm(prev => ({
                      ...prev,
                      ip_address: e.target.value,
                      netmask: e.target.value.includes(':') ? '/64' : prev.netmask,
                    }))}
                    placeholder="e.g. 103.1.2.3 or 2001:db8::1"
                  />
                </label>
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '12px' }}>
                  <label>
                    <span>{t('Netmask / Prefix')}</span>
                    <input
                      value={ipForm.netmask}
                      onChange={e => setIpForm(prev => ({ ...prev, netmask: e.target.value }))}
                      placeholder="/24 or 255.255.255.0"
                    />
                  </label>
                  <label>
                    <span>{t('Network Interface')}</span>
                    <input
                      value={ipForm.device}
                      onChange={e => setIpForm(prev => ({ ...prev, device: e.target.value }))}
                      placeholder="eth0 / ens18"
                    />
                  </label>
                </div>
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '12px' }}>
                  <label>
                    <span>{t('Assign to User / Reseller (Optional)')}</span>
                    <select
                      value={ipForm.assigned_user_id}
                      disabled={ipForm.is_shared}
                      onChange={e => setIpForm(prev => ({ ...prev, assigned_user_id: e.target.value }))}
                    >
                      <option value="">{t('-- Unassigned (Free) --')}</option>
                      {(users || []).map(u => (
                        <option key={u.id} value={u.id}>{u.username} ({u.role})</option>
                      ))}
                    </select>
                  </label>
                  <label>
                    <span>{t('Name Server Hostname')}</span>
                    <input
                      value={ipForm.nameserver}
                      onChange={e => setIpForm(prev => ({ ...prev, nameserver: e.target.value }))}
                      placeholder="ns1.example.com"
                    />
                  </label>
                </div>
                <label>
                  <span>{t('Notes')}</span>
                  <input
                    value={ipForm.notes}
                    onChange={e => setIpForm(prev => ({ ...prev, notes: e.target.value }))}
                    placeholder={t('Optional description...')}
                  />
                </label>
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: '16px', paddingTop: '4px' }}>
                  <label className="check-line">
                    <input
                      type="checkbox"
                      checked={ipForm.is_shared}
                      onChange={e => setIpForm(prev => ({ ...prev, is_shared: e.target.checked, assigned_user_id: '' }))}
                    />
                    {t('Shared IP (can host multiple users)')}
                  </label>
                  <label className="check-line">
                    <input
                      type="checkbox"
                      checked={ipForm.is_global}
                      onChange={e => setIpForm(prev => ({ ...prev, is_global: e.target.checked }))}
                    />
                    {t('Global IP')}
                  </label>
                </div>
              </div>
              <div className="modal-footer" style={{ display: 'flex', justifyContent: 'flex-end', gap: '8px', marginTop: '16px' }}>
                <button type="button" className="secondary" onClick={() => setShowAddModal(false)}>{t('Cancel')}</button>
                <button type="submit" disabled={!!loading}><Plus size={14} /> {t('Add IP')}</button>
              </div>
            </form>
          </div>
        </div>,
        document.body
      )}

      {showAssignModal && createPortal(
        <div className="modal-backdrop" onClick={() => setShowAssignModal(false)}>
          <div className="modal" style={{ maxWidth: '420px', width: '92%' }} onClick={e => e.stopPropagation()}>
            <div className="modal-header">
              <h3>{t('Assign Selected IP(s)')}</h3>
              <button type="button" className="icon-only secondary" onClick={() => setShowAssignModal(false)}><X size={16} /></button>
            </div>
            <form onSubmit={handleAssignSubmit}>
              <div className="modal-body" style={{ display: 'grid', gap: '12px' }}>
                <p className="hint" style={{ margin: 0 }}>
                  {t('Assign {n} selected IP address(es) to a reseller or user account:', { n: selectedIps.length })}
                </p>
                <label>
                  <span>{t('Target Account')}</span>
                  <select required value={assignUserId} onChange={e => setAssignUserId(e.target.value)}>
                    <option value="">{t('-- Select account --')}</option>
                    {(users || []).map(u => (
                      <option key={u.id} value={u.id}>{u.username} ({u.role})</option>
                    ))}
                  </select>
                </label>
              </div>
              <div className="modal-footer" style={{ display: 'flex', justifyContent: 'flex-end', gap: '8px', marginTop: '16px' }}>
                <button type="button" className="secondary" onClick={() => setShowAssignModal(false)}>{t('Cancel')}</button>
                <button type="submit" disabled={!assignUserId || !!loading}><Check size={14} /> {t('Assign')}</button>
              </div>
            </form>
          </div>
        </div>,
        document.body
      )}

      {editingIp && createPortal(
        <div className="modal-backdrop" onClick={() => setEditingIp(null)}>
          <div className="modal" style={{ maxWidth: '480px', width: '92%' }} onClick={e => e.stopPropagation()}>
            <div className="modal-header">
              <h3>{t('Edit IP')} — {editingIp.ip_address}</h3>
              <button type="button" className="icon-only secondary" onClick={() => setEditingIp(null)}><X size={16} /></button>
            </div>
            <form onSubmit={handleSaveEdit}>
              <div className="modal-body" style={{ display: 'grid', gap: '12px' }}>
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '12px' }}>
                  <label>
                    <span>{t('Network Interface')}</span>
                    <input
                      value={editingIp.device || ''}
                      onChange={e => setEditingIp(prev => ({ ...prev, device: e.target.value }))}
                    />
                  </label>
                  <label>
                    <span>{t('Netmask / Prefix')}</span>
                    <input
                      value={editingIp.netmask || ''}
                      onChange={e => setEditingIp(prev => ({ ...prev, netmask: e.target.value }))}
                    />
                  </label>
                </div>
                <label>
                  <span>{t('Assigned Account')}</span>
                  <select
                    value={editingIp.assigned_user_id || ''}
                    disabled={editingIp.is_shared}
                    onChange={e => setEditingIp(prev => ({ ...prev, assigned_user_id: e.target.value ? Number(e.target.value) : null }))}
                  >
                    <option value="">{t('-- Unassigned (Free) --')}</option>
                    {(users || []).map(u => (
                      <option key={u.id} value={u.id}>{u.username} ({u.role})</option>
                    ))}
                  </select>
                </label>
                <label>
                  <span>{t('Name Server Hostname')}</span>
                  <input
                    value={editingIp.nameserver || ''}
                    onChange={e => setEditingIp(prev => ({ ...prev, nameserver: e.target.value }))}
                    placeholder="ns1.example.com"
                  />
                </label>
                <label>
                  <span>{t('Notes')}</span>
                  <input
                    value={editingIp.notes || ''}
                    onChange={e => setEditingIp(prev => ({ ...prev, notes: e.target.value }))}
                  />
                </label>
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: '16px' }}>
                  <label className="check-line">
                    <input
                      type="checkbox"
                      checked={!!editingIp.is_shared}
                      onChange={e => setEditingIp(prev => ({ ...prev, is_shared: e.target.checked, assigned_user_id: null }))}
                    />
                    {t('Shared IP')}
                  </label>
                  <label className="check-line">
                    <input
                      type="checkbox"
                      checked={!!editingIp.is_global}
                      onChange={e => setEditingIp(prev => ({ ...prev, is_global: e.target.checked }))}
                    />
                    {t('Global IP')}
                  </label>
                </div>
              </div>
              <div className="modal-footer" style={{ display: 'flex', justifyContent: 'flex-end', gap: '8px', marginTop: '16px' }}>
                <button type="button" className="secondary" onClick={() => setEditingIp(null)}>{t('Cancel')}</button>
                <button type="submit"><Check size={14} /> {t('Save Changes')}</button>
              </div>
            </form>
          </div>
        </div>,
        document.body
      )}
    </div>
  );
}
