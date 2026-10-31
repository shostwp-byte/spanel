import React, { useState } from 'react';
import {
  Ban, HardDrive, LogIn, Pencil, Play, Plus, RefreshCw,
  Save, Trash2, Users, X
} from 'lucide-react';
import { EmptyState } from '../components/EmptyState.jsx';
import { useAppContext } from '../contexts/AppContext.jsx';
import { t } from '../i18n.js';
import { roleLabel } from '../utils/helpers.js';

function defaultRoleForMode(currentMode, isResellerRole = false) {
  if (isResellerRole) return 'end_user';
  if (currentMode === 'resellers' || currentMode === 'create_reseller') return 'reseller';
  if (currentMode === 'administrators' || currentMode === 'create_admin') return 'admin';
  return 'end_user';
}

export function UsersPage({
  request, isAdmin, isReseller, mailAddonInstalled, loadUsers, loadPackages, websites, refreshAll,
  assignWebsiteId, setAssignWebsiteId, assignUserId, setAssignUserId, assignDomainToUser, mode = 'users'
}) {
  const {
    loading, setError, setNotice, currentUser, setCurrentUser, packages, users,
    storageUsageText, loadCurrentUser, navigateToPage, submitPasswordChange, editingUserForm, setEditingUserForm, setViewLevel,
  } = useAppContext();

  const isResellerMode = mode === 'resellers' || mode === 'create_reseller';
  const isAdminMode = mode === 'administrators' || mode === 'create_admin';
  const isCreateMode = mode.startsWith('create_');
  const [ownerFilter, setOwnerFilter] = useState('all');
  const [searchQuery, setSearchQuery] = useState('');

  const managersById = React.useMemo(() => {
    const map = new Map();
    (users || []).forEach(u => {
      if (u.role === 'admin' || u.role === 'reseller') {
        map.set(Number(u.id), u);
      }
    });
    return map;
  }, [users]);

  const primaryAdmin = React.useMemo(() => {
    const admins = (users || [])
      .filter(u => u.role === 'admin')
      .sort((a, b) => Number(a.id) - Number(b.id));
    return admins[0] || (isAdmin && currentUser ? currentUser : null);
  }, [users, isAdmin, currentUser]);

  function getUserCreatorInfo(u) {
    if (u.reseller_id) {
      const mgr = managersById.get(Number(u.reseller_id));
      if (mgr) {
        return { id: Number(mgr.id), username: mgr.username, role: mgr.role };
      }
      if (currentUser && Number(u.reseller_id) === Number(currentUser.id)) {
        return { id: Number(currentUser.id), username: currentUser.username, role: currentUser.role };
      }
      if (u.owner_username) {
        return { id: Number(u.reseller_id), username: u.owner_username, role: u.owner_role || 'reseller' };
      }
    }
    if (u.owner_username) {
      return {
        id: u.reseller_id ? Number(u.reseller_id) : (primaryAdmin ? Number(primaryAdmin.id) : null),
        username: u.owner_username,
        role: u.owner_role || 'admin',
      };
    }
    if (primaryAdmin) {
      return { id: Number(primaryAdmin.id), username: primaryAdmin.username, role: 'admin' };
    }
    return { id: null, username: 'admin', role: 'admin' };
  }

  const displayUsers = (users || []).filter(u => {
    if (isResellerMode) return u.role === 'reseller';
    if (isAdminMode) return u.role === 'admin';
    const isEndUser = u.role === 'user' || u.role === 'end_user';
    if (!isEndUser) return false;

    const creator = getUserCreatorInfo(u);

    // My Users (or Reseller view): only users initialized/managed by the currently logged-in account
    if (mode === 'my_users' || (isReseller && !isAdmin)) {
      if (Number(creator.id) !== Number(currentUser?.id)) return false;
    } else {
      // Show All Users (Admin): all users created by Admin role or Reseller role
      if (creator.role !== 'admin' && creator.role !== 'reseller') return false;
      if (ownerFilter === 'admin' && creator.role !== 'admin') return false;
      if (ownerFilter === 'reseller' && creator.role !== 'reseller') return false;
      if (ownerFilter.startsWith('mgr:') && Number(creator.id) !== Number(ownerFilter.slice(4))) return false;
    }

    if (searchQuery.trim()) {
      const q = searchQuery.trim().toLowerCase();
      const matchName = (u.username || '').toLowerCase().includes(q);
      const matchEmail = (u.email || '').toLowerCase().includes(q);
      const matchOwner = (creator.username || '').toLowerCase().includes(q);
      if (!matchName && !matchEmail && !matchOwner) return false;
    }
    return true;
  });

  const availablePackages = packages.filter(p =>
    isResellerMode ? p.is_reseller_package : !p.is_reseller_package
  );

  const [userTab, setUserTab] = useState(isCreateMode ? 'add' : 'list');
  const [newUser, setNewUser] = useState({
    username: '',
    password: '',
    email: '',
    role: defaultRoleForMode(mode, isReseller),
    package_id: '',
    website_limit: 5,
    storage_limit_mb: 1024,
    sftp_accounts_limit: 3,
    mail_accounts_limit: 10,
  });

  React.useEffect(() => {
    if (!isAdmin && !isReseller) return;
    if (loadUsers) loadUsers();
    if (loadPackages && packages.length === 0) loadPackages();
  }, []);

  React.useEffect(() => {
    const defaultRole = defaultRoleForMode(mode, isReseller);
    const firstPkg = availablePackages[0];
    setNewUser(prev => ({
      ...prev,
      role: defaultRole,
      package_id: firstPkg ? String(firstPkg.id) : '',
      website_limit: firstPkg ? firstPkg.website_limit : 5,
      storage_limit_mb: firstPkg ? firstPkg.storage_limit_mb : 1024,
      sftp_accounts_limit: firstPkg ? (firstPkg.sftp_accounts_limit ?? 3) : 3,
      mail_accounts_limit: firstPkg ? (firstPkg.mail_accounts_limit ?? 10) : 10,
    }));
    setOwnerFilter('all');
    if (isCreateMode) setUserTab('add');
    else setUserTab('list');
  }, [mode, isCreateMode, isReseller, packages.length]);

  const [editingUser, setEditingUser] = useState(null);

  async function updatePanelUser() {
    if (!editingUser) return;
    const websiteLimit = Number(editingUserForm.website_limit);
    const storageLimitMb = Number(editingUserForm.storage_limit_mb);
    const targetRole = editingUserForm.role === 'user' ? 'end_user' : (editingUserForm.role || 'end_user');
    if (!editingUserForm.email.trim()) {
      setError(t('Email is required.'));
      return;
    }
    if (targetRole !== 'admin' && !editingUserForm.package_id) {
      setError(t('A package must be selected.'));
      return;
    }
    if (!Number.isInteger(websiteLimit) || websiteLimit < 0 || websiteLimit > 1000) {
      setError(t('Website limit must be between 0 and 1000.'));
      return;
    }
    if (!Number.isInteger(storageLimitMb) || storageLimitMb < 0 || storageLimitMb > 1024 * 1024) {
      setError(t('Storage limit must be between 0 and 1048576 MB.'));
      return;
    }
    const payload = {
      email: editingUserForm.email.trim(),
      package_id: targetRole === 'admin' ? null : (editingUserForm.package_id ? Number(editingUserForm.package_id) : null),
      website_limit: websiteLimit,
      storage_limit_mb: storageLimitMb,
      sftp_accounts_limit: Number(editingUserForm.sftp_accounts_limit || 0),
      mail_accounts_limit: Number(editingUserForm.mail_accounts_limit || 0),
    };
    if (editingUser.id !== currentUser?.id) {
      payload.role = targetRole;
    }
    const data = await request(
      `/users/${editingUser.id}`,
      {
        method: 'PATCH',
        body: JSON.stringify(payload),
      },
      `Updating ${editingUser.username}...`
    );
    if (data) {
      setNotice(`Updated user ${data.username}.`);
      if (data.id === currentUser?.id) setCurrentUser(prev => ({ ...prev, ...data }));
      cancelEditingUser();
      await loadUsers();
    }
  }

  function applyPackageToNewUser(packageId) {
    const selected = packages.find(item => String(item.id) === String(packageId));
    setNewUser(prev => ({
      ...prev,
      package_id: packageId,
      website_limit: selected ? selected.website_limit : 5,
      storage_limit_mb: selected ? selected.storage_limit_mb : 1024,
      sftp_accounts_limit: selected ? (selected.sftp_accounts_limit ?? 3) : 3,
      mail_accounts_limit: selected ? (selected.mail_accounts_limit ?? 10) : 10,
    }));
  }

  function applyPackageToEditingUser(packageId) {
    const selected = packages.find(item => String(item.id) === String(packageId));
    setEditingUserForm(prev => ({
      ...prev,
      package_id: packageId,
      website_limit: selected ? selected.website_limit : 5,
      storage_limit_mb: selected ? selected.storage_limit_mb : 1024,
      sftp_accounts_limit: selected ? (selected.sftp_accounts_limit ?? 3) : 3,
      mail_accounts_limit: selected ? (selected.mail_accounts_limit ?? 10) : 10,
    }));
  }

  async function createUser() {
    const assignedRole = defaultRoleForMode(mode, isReseller);
    if (!newUser.package_id && assignedRole !== 'admin') {
      setError(t('A package must be selected.'));
      return;
    }
    const payload = {
      ...newUser,
      role: assignedRole,
      package_id: assignedRole === 'admin' ? null : (newUser.package_id ? Number(newUser.package_id) : null),
      website_limit: Number(newUser.website_limit),
      storage_limit_mb: Number(newUser.storage_limit_mb),
      sftp_accounts_limit: Number(newUser.sftp_accounts_limit || 0),
      mail_accounts_limit: Number(newUser.mail_accounts_limit || 0),
    };
    const data = await request('/users', { method: 'POST', body: JSON.stringify(payload) }, t('Creating user...'));
    if (data) {
      setNotice(`Created user ${data.username}`);
      const firstPkg = availablePackages[0];
      setNewUser({
        username: '',
        email: '',
        password: '',
        role: assignedRole,
        package_id: firstPkg ? String(firstPkg.id) : '',
        website_limit: firstPkg ? firstPkg.website_limit : 5,
        storage_limit_mb: firstPkg ? firstPkg.storage_limit_mb : 1024,
        sftp_accounts_limit: firstPkg ? (firstPkg.sftp_accounts_limit ?? 3) : 3,
        mail_accounts_limit: firstPkg ? (firstPkg.mail_accounts_limit ?? 10) : 10,
      });
      await loadUsers();
      setUserTab('list');
    }
  }

  function startEditingUser(user) {
    setEditingUser(user);
    setEditingUserForm({
      email: user.email || '',
      role: (!user.role || user.role === 'user') ? 'end_user' : user.role,
      package_id: user.package_id ? String(user.package_id) : '',
      website_limit: user.website_limit ?? 5,
      storage_limit_mb: user.storage_limit_mb ?? 1024,
      sftp_accounts_limit: user.sftp_accounts_limit ?? 0,
      mail_accounts_limit: user.mail_accounts_limit ?? 10,
      new_password: '',
      confirm_password: '',
    });
  }

  function cancelEditingUser() {
    setEditingUser(null);
    setEditingUserForm({
      email: '',
      role: 'end_user',
      package_id: '',
      website_limit: 5,
      storage_limit_mb: 1024,
      sftp_accounts_limit: 0,
      mail_accounts_limit: 10,
      new_password: '',
      confirm_password: '',
    });
  }

  async function deletePanelUser(user) {
    if (!user || user.id === currentUser?.id) return;
    if (!confirm(`Delete panel user ${user.username} and permanently delete all owned websites, files, databases, SSL certificates, and Linux user data?`)) return;
    const data = await request(`/users/${user.id}`, { method: 'DELETE' }, `Deleting user ${user.username}...`);
    if (data) {
      const count = data.deleted_websites?.length || 0;
      setNotice(
        count
          ? t('Deleted user {name} and {n} website(s)', { name: user.username, n: count })
          : t('Deleted user {name}', { name: user.username })
      );
      await loadUsers();
      await refreshAll();
    }
  }

  async function suspendUser(user) {
    if (!user || user.id === currentUser?.id) return;
    const siteCount = websites.filter(w => w.owner_id === user.id).length;
    if (!confirm(t('Suspend user {name}? This will block login, disable all {n} website(s), lock SFTP, and kill active sessions.', { name: user.username, n: siteCount }))) return;
    const data = await request(`/users/${user.id}/suspend`, { method: 'POST' }, `Suspending user ${user.username}...`);
    if (data) {
      await loadUsers();
      await refreshAll();
    }
  }

  async function unsuspendUser(user) {
    if (!user || user.id === currentUser?.id) return;
    if (!confirm(`Unsuspend user ${user.username}? This will restore login, websites, and SFTP access.`)) return;
    const data = await request(`/users/${user.id}/unsuspend`, { method: 'POST' }, `Unsuspending user ${user.username}...`);
    if (data) {
      await loadUsers();
      await refreshAll();
    }
  }

  async function quickLoginUser(user) {
    if (!user) return;
    const suspendedNote = user.is_active ? '' : ' This user is SUSPENDED — websites and SFTP are disabled.';
    if (!confirm(`Login as ${user.username}?${suspendedNote}`)) return;
    let body;
    if (currentUser?.totp_enabled) {
      const code = prompt(`Enter the 6-digit code from your authenticator to confirm impersonation of ${user.username}:`);
      if (!code) return;
      body = new URLSearchParams({ otp: code.trim() });
    }
    const data = await request(
      `/auth/impersonate/${user.id}`,
      body
        ? { method: 'POST', body, headers: { 'Content-Type': 'application/x-www-form-urlencoded' } }
        : { method: 'POST' },
      `Logging in as ${user.username}...`
    );
    if (data?.requires_2fa) {
      const code = prompt(`Enter the 6-digit code from your authenticator to confirm impersonation of ${user.username}:`);
      if (!code) return;
      const retryBody = new URLSearchParams({ otp: code.trim() });
      const retryData = await request(
        `/auth/impersonate/${user.id}`,
        { method: 'POST', body: retryBody, headers: { 'Content-Type': 'application/x-www-form-urlencoded' } },
        `Logging in as ${user.username}...`
      );
      if (retryData?.access_token) {
        setNotice(`Logged in as ${user.username}.`);
        setViewLevel?.(null);
        await loadCurrentUser();
        navigateToPage('dashboard');
      }
      return;
    }
    if (data?.access_token) {
      setNotice(`Logged in as ${user.username}.`);
      setViewLevel?.(null);
      await loadCurrentUser();
      navigateToPage('dashboard');
    }
  }

  async function resetUserTwoFactor(user) {
    if (!confirm(`Reset 2FA for ${user.username}?`)) return;
    const data = await request(`/users/${user.id}/2fa/reset`, { method: 'POST' }, `Resetting 2FA for ${user.username}...`);
    if (data?.message) {
      setNotice(data.message);
      await loadUsers();
    }
  }

  function renderUsers() {
    if (!isAdmin && !isReseller) {
      return (
        <div className="section">
          <h2>{t('Users')}</h2>
          <p className="hint">{t('No permission.')}</p>
        </div>
      );
    }
    const activeUserTab = userTab || 'list';
    const userTabButton = (key, Icon, label) => (
      <button
        type="button"
        className={activeUserTab === key ? 'active' : ''}
        role="tab"
        aria-selected={activeUserTab === key}
        aria-controls={`users-tab-${key}`}
        id={`users-tab-button-${key}`}
        onClick={() => setUserTab(key)}
      >
        <Icon size={14} />
        {t(label)}
      </button>
    );

    const addTabLabel = isResellerMode ? 'Add Reseller' : isAdminMode ? 'Add Administrator' : 'Add User';
    const listTabLabel = isResellerMode ? 'List Resellers' : isAdminMode ? 'List Administrators' : 'List user';
    const assignedRole = defaultRoleForMode(mode, isReseller);

    return (
      <div className="users-page">
        <div className="section-title">
          <div>
            <h2>{t(isResellerMode ? 'Resellers' : isAdminMode ? 'Administrators' : 'Panel users')}</h2>
            <p className="hint">{t('Manage users, packages, and domain ownership.')}</p>
          </div>
        </div>

        <div className="segmented-control backup-tabs user-tabs" role="tablist" aria-label={t('Panel user sections')}>
          {userTabButton('list', Users, listTabLabel)}
          {userTabButton('add', Plus, addTabLabel)}
        </div>

        {activeUserTab === 'list' && (
          <div className="user-tab-panel" id="users-tab-list" role="tabpanel" aria-labelledby="users-tab-button-list">
            <div className="section-title user-panel-title" style={{ flexWrap: 'wrap', gap: '12px' }}>
              <div>
                <h2>{t(isResellerMode ? 'Reseller list' : isAdminMode ? 'Administrator list' : mode === 'my_users' ? 'My Users' : (isAdmin ? 'Show All Users' : 'User list'))}</h2>
                <p className="hint">
                  {isResellerMode || isAdminMode
                    ? t('Current panel users and service limits.')
                    : mode === 'my_users'
                      ? t('Users initialized by your account ({name}).', { name: currentUser?.username || '' })
                      : isAdmin
                        ? t('All user accounts created by Admin and Reseller accounts.')
                        : t('Current panel users and service limits.')}
                </p>
              </div>
              <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap' }}>
                {!isResellerMode && !isAdminMode && (
                  <input
                    type="text"
                    placeholder={t('Search users...')}
                    value={searchQuery}
                    onChange={e => setSearchQuery(e.target.value)}
                    style={{ width: '190px', padding: '6px 10px', fontSize: '13px' }}
                  />
                )}
                {isAdmin && mode === 'users' && !isResellerMode && !isAdminMode && (
                  <select
                    value={ownerFilter}
                    onChange={e => setOwnerFilter(e.target.value)}
                    style={{ width: 'auto', minWidth: '210px', padding: '6px 10px', fontSize: '13px' }}
                  >
                    <option value="all">{t('All Creators (Admin & Reseller)')}</option>
                    <option value="admin">{t('Created by Admin')}</option>
                    <option value="reseller">{t('Created by Reseller')}</option>
                    {Array.from(managersById.values()).map(mgr => (
                      <option key={mgr.id} value={`mgr:${mgr.id}`}>
                        {mgr.username} ({t(roleLabel(mgr.role))})
                      </option>
                    ))}
                  </select>
                )}
                <button className="secondary-light" disabled={!!loading} onClick={loadUsers}>
                  <RefreshCw size={14} />
                  {t('Refresh')}
                </button>
              </div>
            </div>
            {displayUsers.length === 0 && <EmptyState icon={Users} message={t('No users found.')} />}
            <div className="table">
              {displayUsers.map(user => (
                <div className="row user-row" key={user.id}>
                  <div className="user-main">
                    <strong>{user.username}</strong>
                    <small>{user.email}</small>
                  </div>
                  <div className="user-badges">
                    <span className={user.is_active ? 'badge ok' : 'badge danger'}>
                      {user.is_active ? t('Active') : t('Suspended')}
                    </span>
                    <span className="badge">{t(roleLabel(user.role))}</span>
                    {(user.role === 'user' || user.role === 'end_user') && (() => {
                      const creator = getUserCreatorInfo(user);
                      return (
                        <span
                          className="badge"
                          title={`${t('Created by')}: ${creator.username} (${t(roleLabel(creator.role))})`}
                        >
                          {t('Created by')}: {creator.username} ({t(roleLabel(creator.role))})
                        </span>
                      );
                    })()}
                    <span className="badge">{user.package_name || t('Custom')}</span>
                    {user.totp_enabled && <span className="badge ok">2FA</span>}
                  </div>
                  <span className="user-metric">
                    <HardDrive size={13} />
                    {storageUsageText(user)}
                  </span>
                  <div className="row-actions">
                    <button className="mini secondary-light" disabled={!!loading} onClick={() => startEditingUser(user)}>
                      <Pencil size={14} />
                      {t('Edit')}
                    </button>
                    {user.id !== currentUser?.id && (
                      <button className="mini secondary-light" disabled={!!loading} onClick={() => quickLoginUser(user)}>
                        <LogIn size={14} />
                        {t('Login as')}
                      </button>
                    )}
                    {user.totp_enabled && user.id !== currentUser?.id && (
                      <button className="mini secondary-light" disabled={!!loading} onClick={() => resetUserTwoFactor(user)}>
                        {t('Reset 2FA')}
                      </button>
                    )}
                    {user.id !== currentUser?.id &&
                      (user.is_active ? (
                        <button className="mini secondary-light" disabled={!!loading} onClick={() => suspendUser(user)}>
                          <Ban size={14} />
                          {t('Suspend')}
                        </button>
                      ) : (
                        <button className="mini secondary-light" disabled={!!loading} onClick={() => unsuspendUser(user)}>
                          <Play size={14} />
                          {t('Unsuspend')}
                        </button>
                      ))}
                    {user.id !== currentUser?.id && (
                      <button className="mini danger" disabled={!!loading} onClick={() => deletePanelUser(user)}>
                        <Trash2 size={14} />
                      </button>
                    )}
                  </div>
                  {editingUser?.id === user.id && (
                    <div className="user-edit-panel">
                      <div className="user-edit-heading">
                        <div>
                          <strong>Edit {user.username}</strong>
                          <small>
                            {user.id === currentUser?.id
                              ? 'Role is locked for the active admin session.'
                              : 'Role changes sign the user out of existing sessions.'}
                            {editingUserForm.role === 'admin' ? ' Admin accounts bypass website and storage limits.' : ''}
                          </small>
                        </div>
                        <button
                          className="user-edit-close secondary-light"
                          onClick={cancelEditingUser}
                          aria-label={t('Close user editor')}
                          title={t('Close user editor')}
                        >
                          <X size={16} />
                        </button>
                      </div>
                      <div className="user-edit-grid">
                        <label>
                          <span>{t('Email')}</span>
                          <input
                            type="email"
                            value={editingUserForm.email}
                            onChange={e => setEditingUserForm(prev => ({ ...prev, email: e.target.value }))}
                          />
                        </label>
                        <label>
                          <span>{t('Role')}</span>
                          <select
                            value={editingUserForm.role === 'user' ? 'end_user' : editingUserForm.role}
                            disabled={user.id === currentUser?.id || isReseller}
                            onChange={e => setEditingUserForm(prev => ({ ...prev, role: e.target.value }))}
                          >
                            <option value="end_user">{t('End user')}</option>
                            {isAdmin && <option value="reseller">{t('Reseller')}</option>}
                            {isAdmin && <option value="admin">{t('Admin')}</option>}
                          </select>
                        </label>
                        {editingUserForm.role !== 'admin' && (
                          <>
                            <label>
                              <span>{t('Package')}</span>
                              <select
                                value={editingUserForm.package_id}
                                onChange={e => applyPackageToEditingUser(e.target.value)}
                              >
                                <option value="" disabled>
                                  -- {t('Select a Package')} --
                                </option>
                                {packages
                                  .filter(p => (editingUserForm.role === 'reseller' ? p.is_reseller_package : !p.is_reseller_package))
                                  .map(item => (
                                    <option key={item.id} value={item.id}>
                                      {item.name}
                                    </option>
                                  ))}
                              </select>
                            </label>
                            <label>
                              <span>{t('Website limit')}</span>
                              <input
                                type="number"
                                min="0"
                                max="1000"
                                disabled={!!editingUserForm.package_id}
                                value={editingUserForm.website_limit}
                                onChange={e => setEditingUserForm(prev => ({ ...prev, website_limit: e.target.value }))}
                              />
                            </label>
                            <label>
                              <span>{t('Storage limit (MB)')}</span>
                              <input
                                type="number"
                                min="0"
                                max="1048576"
                                disabled={!!editingUserForm.package_id}
                                value={editingUserForm.storage_limit_mb}
                                onChange={e => setEditingUserForm(prev => ({ ...prev, storage_limit_mb: e.target.value }))}
                              />
                            </label>
                            <label>
                              <span>{t('SFTP accounts')}</span>
                              <input
                                type="number"
                                min="0"
                                max="100"
                                disabled={!!editingUserForm.package_id}
                                value={editingUserForm.sftp_accounts_limit}
                                onChange={e => setEditingUserForm(prev => ({ ...prev, sftp_accounts_limit: e.target.value }))}
                              />
                            </label>
                            {mailAddonInstalled && (
                              <label>
                                <span>{t('Mailboxes')}</span>
                                <input
                                  type="number"
                                  min="0"
                                  max="1000"
                                  disabled={!!editingUserForm.package_id}
                                  value={editingUserForm.mail_accounts_limit}
                                  onChange={e => setEditingUserForm(prev => ({ ...prev, mail_accounts_limit: e.target.value }))}
                                />
                              </label>
                            )}
                          </>
                        )}
                      </div>
                      <div className="user-edit-section">
                        <div className="user-edit-heading">
                          <div>
                            <strong>{t('Change password')}</strong>
                            <small>
                              {t('Minimum 12 characters.')}{' '}
                              {t(user.id === currentUser?.id ? 'Requires current password + 2FA.' : 'Admin can set directly.')}
                            </small>
                          </div>
                        </div>
                        <div className="user-edit-grid">
                          <label>
                            <span>{t('New password')}</span>
                            <input
                              type="password"
                              placeholder={t('Min 12 characters')}
                              value={editingUserForm.new_password}
                              onChange={e => setEditingUserForm(prev => ({ ...prev, new_password: e.target.value }))}
                            />
                          </label>
                          <label>
                            <span>{t('Confirm password')}</span>
                            <input
                              type="password"
                              placeholder={t('Repeat password')}
                              value={editingUserForm.confirm_password}
                              onChange={e => setEditingUserForm(prev => ({ ...prev, confirm_password: e.target.value }))}
                            />
                          </label>
                        </div>
                        <div className="user-edit-actions">
                          <button
                            disabled={!!loading || !editingUserForm.new_password || editingUserForm.new_password.length < 12}
                            onClick={() => submitPasswordChange(user)}
                          >
                            {t('Set password')}
                          </button>
                        </div>
                      </div>
                      <div className="user-edit-actions">
                        <button className="secondary-light" onClick={cancelEditingUser}>
                          {t('Cancel')}
                        </button>
                        <button disabled={!!loading || !editingUserForm.email.trim()} onClick={updatePanelUser}>
                          <Save size={14} />
                          {t('Save changes')}
                        </button>
                      </div>
                    </div>
                  )}
                </div>
              ))}
            </div>
            {websites.length > 0 && <div className="user-action-panel">
              <div>
                <h3>{t('Assign domain to user')}</h3>
                <p className="hint">{t('Move an existing domain under a selected panel user.')}</p>
              </div>
              <div className="assign-row">
                <select value={assignWebsiteId} onChange={e => setAssignWebsiteId(e.target.value)}>
                  <option value="">{t('Select domain')}</option>
                  {websites.map(site => (
                    <option key={site.id} value={site.id}>
                      {site.domain}
                    </option>
                  ))}
                </select>
                <select value={assignUserId} onChange={e => setAssignUserId(e.target.value)}>
                  <option value="">{t('Select user')}</option>
                  {users.filter(user => user.role !== 'admin' && user.role !== 'reseller').map(user => (
                    <option key={user.id} value={user.id}>
                      {user.username} ({roleLabel(user.role)})
                    </option>
                  ))}
                </select>
                <button disabled={!assignWebsiteId || !assignUserId || !!loading} onClick={assignDomainToUser}>
                  {t('Assign')}
                </button>
              </div>
            </div>}
          </div>
        )}

        {activeUserTab === 'add' && (
          <div className="user-tab-panel" id="users-tab-add" role="tabpanel" aria-labelledby="users-tab-button-add">
            <div className="section-title user-panel-title">
              <div>
                <h2>
                  {t(isResellerMode ? 'Create Reseller' : isAdminMode ? 'Create Administrator' : 'Add User')}
                </h2>
                <p className="hint">
                  {t('Panel username is also the Linux user. Login as a user before creating websites for that account.')}
                </p>
              </div>
            </div>
            <div className="user-create-card">
              <label>
                <span>{t('Username')}</span>
                <input
                  value={newUser.username}
                  onChange={e => setNewUser(prev => ({ ...prev, username: e.target.value.toLowerCase() }))}
                  placeholder="johndoe"
                />
              </label>
              <label>
                <span>{t('Email')}</span>
                <input
                  value={newUser.email}
                  onChange={e => setNewUser(prev => ({ ...prev, email: e.target.value }))}
                  placeholder="user@domain.com"
                />
              </label>
              <label>
                <span>{t('Password')}</span>
                <input
                  value={newUser.password}
                  onChange={e => setNewUser(prev => ({ ...prev, password: e.target.value }))}
                  placeholder={t('Min 12 characters')}
                  type="password"
                />
              </label>
              {assignedRole !== 'admin' && (
                <>
                  <label>
                    <span>{t('Package')}</span>
                    <select value={newUser.package_id} onChange={e => applyPackageToNewUser(e.target.value)}>
                      <option value="" disabled>
                        -- {t('Select a Package')} --
                      </option>
                      {availablePackages.map(item => (
                        <option key={item.id} value={item.id}>
                          {item.name}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label>
                    <span>{t('Site limit')}</span>
                    <input
                      type="number"
                      disabled={!!newUser.package_id}
                      value={newUser.website_limit}
                      onChange={e => setNewUser(prev => ({ ...prev, website_limit: e.target.value }))}
                    />
                  </label>
                  <label>
                    <span>{t('Storage MB')}</span>
                    <input
                      type="number"
                      disabled={!!newUser.package_id}
                      value={newUser.storage_limit_mb}
                      onChange={e => setNewUser(prev => ({ ...prev, storage_limit_mb: e.target.value }))}
                    />
                  </label>
                  <label>
                    <span>{t('SFTP accounts')}</span>
                    <input
                      type="number"
                      min="0"
                      max="100"
                      disabled={!!newUser.package_id}
                      value={newUser.sftp_accounts_limit}
                      onChange={e => setNewUser(prev => ({ ...prev, sftp_accounts_limit: e.target.value }))}
                    />
                  </label>
                  {mailAddonInstalled && (
                    <label>
                      <span>{t('Mailboxes')}</span>
                      <input
                        type="number"
                        min="0"
                        max="1000"
                        disabled={!!newUser.package_id}
                        value={newUser.mail_accounts_limit}
                        onChange={e => setNewUser(prev => ({ ...prev, mail_accounts_limit: e.target.value }))}
                      />
                    </label>
                  )}
                </>
              )}
              <button disabled={!!loading || !newUser.username || !newUser.password} onClick={createUser}>
                <Plus size={14} />
                {t(isResellerMode ? 'Create reseller' : isAdminMode ? 'Create administrator' : 'Create user')}
              </button>
            </div>
          </div>
        )}
      </div>
    );
  }
  return renderUsers();
}
