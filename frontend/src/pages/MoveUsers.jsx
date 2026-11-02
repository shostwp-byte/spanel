import React, { useState, useEffect } from 'react';
import { ArrowRightLeft, Globe, RefreshCw, Search, UserPlus, X } from 'lucide-react';
import { EmptyState } from '../components/EmptyState.jsx';
import { useAppContext } from '../contexts/AppContext.jsx';
import { t } from '../i18n.js';
import { roleLabel } from '../utils/helpers.js';

export function MoveUsersPage({ request, isAdmin }) {
  const { users, websites, loading, loadUsers, setError, setNotice } = useAppContext();
  const [selectedUserIds, setSelectedUserIds] = useState([]);
  const [targetResellerId, setTargetResellerId] = useState('');
  const [searchTerm, setSearchTerm] = useState('');

  useEffect(() => {
    if (isAdmin && loadUsers) loadUsers();
  }, [isAdmin]);

  const endUsers = (users || []).filter(u => u.role === 'user' || u.role === 'end_user');
  const resellers = (users || []).filter(u => u.role === 'reseller' || u.role === 'admin');
  const primaryAdmin = resellers.filter(r => r.role === 'admin').sort((a, b) => Number(a.id) - Number(b.id))[0] || null;

  const getUserDomains = (u) => {
    if (Array.isArray(u.domains) && u.domains.length > 0) return u.domains;
    if (u.primary_domain) return [u.primary_domain];
    return (websites || [])
      .filter(w => Number(w.owner_id) === Number(u.id))
      .map(w => w.domain)
      .filter(Boolean);
  };

  const getCurrentOwner = (u) => {
    if (u.reseller_id) {
      const found = resellers.find(r => Number(r.id) === Number(u.reseller_id));
      if (found) return found;
      if (u.owner_username) return { id: u.reseller_id, username: u.owner_username, role: u.owner_role || 'reseller' };
    }
    if (u.owner_username) {
      return { id: primaryAdmin?.id || null, username: u.owner_username, role: u.owner_role || 'admin' };
    }
    return primaryAdmin;
  };

  const q = searchTerm.trim().toLowerCase();
  const filteredUsers = endUsers.filter(u => {
    if (!q) return true;
    const owner = getCurrentOwner(u);
    const domains = getUserDomains(u);
    return (
      (u.username || '').toLowerCase().includes(q) ||
      (u.email || '').toLowerCase().includes(q) ||
      (owner?.username || '').toLowerCase().includes(q) ||
      domains.some(d => d.toLowerCase().includes(q))
    );
  });

  const toggleSelectUser = (id) => {
    setSelectedUserIds(prev => 
      prev.includes(id) ? prev.filter(userId => userId !== id) : [...prev, id]
    );
  };

  const selectAll = (e) => {
    if (e.target.checked) {
      const allFilteredIds = filteredUsers.map(u => u.id);
      const newIds = new Set([...selectedUserIds, ...allFilteredIds]);
      setSelectedUserIds(Array.from(newIds));
    } else {
      const allFilteredIds = filteredUsers.map(u => u.id);
      setSelectedUserIds(prev => prev.filter(id => !allFilteredIds.includes(id)));
    }
  };

  const handleMove = async () => {
    if (selectedUserIds.length === 0) {
      setError(t('Please select at least one user to move.'));
      return;
    }
    if (!targetResellerId) {
      setError(t('Please select a target reseller.'));
      return;
    }

    const data = await request('/users/move', {
      method: 'POST',
      body: JSON.stringify({
        user_ids: selectedUserIds,
        target_reseller_id: parseInt(targetResellerId, 10)
      })
    }, t('Moving users...'));

    if (data) {
      setNotice(data.message || t('Users successfully moved.'));
      setSelectedUserIds([]);
      setTargetResellerId('');
      if (loadUsers) await loadUsers();
    }
  };

  if (!isAdmin) {
    return <div className="section"><div className="notice warn">{t('You do not have permission to access this page.')}</div></div>;
  }

  const allFilteredSelected = filteredUsers.length > 0 && filteredUsers.every(u => selectedUserIds.includes(u.id));

  return (
    <div className="users-page user-clean-shell">
      <div className="pkg-list-card user-mgmt-card">
        <div className="user-mgmt-toolbar">
          <div className="move-users-toolbar-left">
            <div className="user-search-wrap">
              <Search size={14} className="user-search-icon" />
              <input
                type="text"
                placeholder={t('Search user, email, domain...')}
                value={searchTerm}
                onChange={e => setSearchTerm(e.target.value)}
                aria-label={t('Search user, email, domain...')}
              />
              {searchTerm && (
                <button
                  type="button"
                  className="user-search-clear"
                  onClick={() => setSearchTerm('')}
                  aria-label={t('Clear search')}
                >
                  <X size={13} />
                </button>
              )}
            </div>
            {selectedUserIds.length > 0 && (
              <span className="move-selected-pill">
                {t('{count} selected', { count: selectedUserIds.length })}
              </span>
            )}
          </div>

          <div className="user-mgmt-controls">
            <select
              value={targetResellerId}
              onChange={e => setTargetResellerId(e.target.value)}
              className="user-owner-select move-target-select"
              aria-label={t('Target Reseller / Admin')}
            >
              <option value="">{t('Target Reseller / Admin...')}</option>
              {resellers.map(r => (
                <option key={r.id} value={r.id}>
                  {r.username} ({t(roleLabel(r.role))})
                </option>
              ))}
            </select>
            <button
              type="button"
              className="primary move-submit-btn"
              onClick={handleMove}
              disabled={!!loading || selectedUserIds.length === 0 || !targetResellerId}
            >
              <ArrowRightLeft size={14} />
              <span>
                {selectedUserIds.length > 0
                  ? t('Move {count} Users', { count: selectedUserIds.length })
                  : t('Move Users')}
              </span>
            </button>
            <button
              type="button"
              className="secondary-light user-refresh-btn"
              disabled={!!loading}
              onClick={loadUsers}
            >
              <RefreshCw size={14} />
              <span>{t('Refresh')}</span>
            </button>
          </div>
        </div>

        {filteredUsers.length === 0 ? (
          <div className="user-empty-slot">
            <EmptyState icon={UserPlus} message={t('No users found.')} />
          </div>
        ) : (
          <div className="pkg-table-wrap">
            <table className="pkg-clean-table user-clean-table move-users-clean-table">
              <thead>
                <tr>
                  <th className="move-check-col">
                    <input
                      type="checkbox"
                      className="move-row-checkbox"
                      checked={allFilteredSelected}
                      onChange={selectAll}
                      aria-label={t('Select all users')}
                    />
                  </th>
                  <th>{t('Account')}</th>
                  <th>{t('Domain')}</th>
                  <th>{t('Package')}</th>
                  <th>{t('Current Owner')}</th>
                </tr>
              </thead>
              <tbody>
                {filteredUsers.map(user => {
                  const isSelected = selectedUserIds.includes(user.id);
                  const currentOwner = getCurrentOwner(user);
                  const domains = getUserDomains(user);

                  return (
                    <tr
                      key={user.id}
                      className={`move-user-row${isSelected ? ' is-selected' : ''}`}
                      onClick={e => {
                        if (e.target.tagName !== 'INPUT') {
                          toggleSelectUser(user.id);
                        }
                      }}
                    >
                      <td className="move-check-col">
                        <input
                          type="checkbox"
                          className="move-row-checkbox"
                          checked={isSelected}
                          onChange={() => toggleSelectUser(user.id)}
                          aria-label={user.username}
                        />
                      </td>
                      <td>
                        <div className="user-cell-primary">
                          <strong>{user.username}</strong>
                        </div>
                        <div className="user-cell-sub">{user.email}</div>
                      </td>
                      <td>
                        {domains.length > 0 ? (
                          <div className="user-domain-cell" title={domains.join(', ')}>
                            <Globe size={13} className="user-domain-icon" />
                            <span className="user-domain-name">{domains[0]}</span>
                            {domains.length > 1 && <span className="user-domain-extra">+{domains.length - 1}</span>}
                          </div>
                        ) : (
                          <span className="user-cell-muted">—</span>
                        )}
                      </td>
                      <td>
                        <div className="user-pkg-text">{user.package_name || t('Custom')}</div>
                      </td>
                      <td>
                        {currentOwner ? (
                          <>
                            <div className="user-pkg-text">{currentOwner.username}</div>
                            <div className="user-cell-sub">{t(roleLabel(currentOwner.role))}</div>
                          </>
                        ) : (
                          <span className="user-cell-muted">{t('None')}</span>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
