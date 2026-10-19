import React, { useState, useEffect } from 'react';
import { ArrowRightLeft, Search, UserPlus } from 'lucide-react';
import { EmptyState } from '../components/EmptyState.jsx';
import { useAppContext } from '../contexts/AppContext.jsx';
import { t } from '../i18n.js';
import { roleLabel } from '../utils/helpers.js';

export function MoveUsersPage({ request, isAdmin }) {
  const { users, loadUsers, setError, setNotice } = useAppContext();
  const [selectedUserIds, setSelectedUserIds] = useState([]);
  const [targetResellerId, setTargetResellerId] = useState('');
  const [searchTerm, setSearchTerm] = useState('');

  useEffect(() => {
    if (isAdmin && loadUsers) loadUsers();
  }, [isAdmin]);

  const endUsers = (users || []).filter(u => u.role === 'user' || u.role === 'end_user');
  const resellers = (users || []).filter(u => u.role === 'reseller' || u.role === 'admin');

  const filteredUsers = endUsers.filter(u => 
    u.username.toLowerCase().includes(searchTerm.toLowerCase()) ||
    (u.email || '').toLowerCase().includes(searchTerm.toLowerCase())
  );

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
      loadUsers();
    }
  };

  if (!isAdmin) {
    return <div className="section"><div className="notice warn">{t('You do not have permission to access this page.')}</div></div>;
  }

  const allFilteredSelected = filteredUsers.length > 0 && filteredUsers.every(u => selectedUserIds.includes(u.id));

  return (
    <div className="users-page">
      <p className="hint" style={{ marginBottom: "25px", fontSize: "15px" }}>{t('Select users and a target reseller to move them.')}</p>

      {/* Target Reseller Selection */}
      <div className="card" style={{ marginBottom: '20px', padding: '20px', display: 'flex', flexDirection: 'row', gap: '15px', alignItems: 'flex-end', flexWrap: 'wrap' }}>
        <div className="form-group" style={{ margin: 0, flex: '1 1 220px', maxWidth: '400px' }}>
          <label>{t('Target Reseller / Admin')}</label>
          <select 
            value={targetResellerId} 
            onChange={e => setTargetResellerId(e.target.value)}
          >
            <option value="">{t('-- Select Target --')}</option>
            {resellers.map(r => (
              <option key={r.id} value={r.id}>
                {r.username} ({t(roleLabel(r.role))})
              </option>
            ))}
          </select>
        </div>
        <button 
          type="button" 
          className="primary" 
          onClick={handleMove}
          disabled={selectedUserIds.length === 0 || !targetResellerId}
        >
          <ArrowRightLeft size={16} />
          {t('Move {count} Users', { count: selectedUserIds.length })}
        </button>
      </div>

      {/* Search Bar */}
      <div className="website-search-bar" style={{ marginBottom: '15px' }}>
        <Search size={16} />
        <input 
          type="text" 
          placeholder={t('Search users...')} 
          value={searchTerm} 
          onChange={e => setSearchTerm(e.target.value)} 
        />
      </div>

      {/* Table-like Header */}
      {filteredUsers.length > 0 && (
        <div className="row move-users-grid-row" style={{ background: 'transparent', border: 'none', paddingBottom: '0', color: 'var(--text-muted)', fontSize: '13px', fontWeight: 'bold' }}>
          <div style={{ display: 'flex', justifyContent: 'center' }}>
            <input type="checkbox" checked={allFilteredSelected} onChange={selectAll} style={{ cursor: 'pointer' }} />
          </div>
          <div>{t('Username')}</div>
          <div className="move-col-role">{t('Email')}</div>
          <div>{t('Current Owner')}</div>
        </div>
      )}

      {/* Table Rows */}
      {filteredUsers.length === 0 ? (
        <EmptyState icon={UserPlus} message={t('No users found.')} />
      ) : (
        <div className="table">
          {filteredUsers.map(user => {
            const isSelected = selectedUserIds.includes(user.id);
            const currentOwner = resellers.find(r => Number(r.id) === Number(user.reseller_id)) || (!user.reseller_id ? resellers.find(r => r.role === 'admin') : null);
            
            return (
              <div 
                className="row move-users-grid-row" 
                key={user.id} 
                onClick={(e) => {
                  if (e.target.tagName !== 'INPUT') {
                    toggleSelectUser(user.id);
                  }
                }}
                style={{ 
                  cursor: 'pointer',
                  borderColor: isSelected ? 'var(--primary)' : 'var(--border)',
                  background: isSelected ? 'var(--primary-light)' : undefined
                }}
              >
                <div style={{ display: 'flex', justifyContent: 'center' }}>
                  <input 
                    type="checkbox" 
                    checked={isSelected} 
                    onChange={() => toggleSelectUser(user.id)} 
                    style={{ cursor: 'pointer' }}
                  />
                </div>
                <div className="user-main">
                  <strong>{user.username}</strong>
                </div>
                <div className="move-col-role">
                  <small style={{ color: 'var(--text-secondary)' }}>{user.email}</small>
                </div>
                <div>
                  {currentOwner ? (
                    <span className="badge" style={{ background: 'var(--surface-alt)' }}>
                      {currentOwner.username}
                    </span>
                  ) : (
                    <span className="badge">{t('None')}</span>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
