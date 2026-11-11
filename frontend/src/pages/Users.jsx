import React, { useState } from 'react';
import {
  Ban, Globe, LogIn, Pencil, Play, Plus, RefreshCw,
  Save, Search, Trash2, Users, X
} from 'lucide-react';
import { EmptyState } from '../components/EmptyState.jsx';
import { PasswordField, PasswordResultBox } from '../components/PasswordField.jsx';
import { useAppContext } from '../contexts/AppContext.jsx';
import { t } from '../i18n.js';
import { roleLabel } from '../utils/helpers.js';

const DOMAIN_VALID_RE = /^(?!-)([a-z0-9-]{1,63}\.)+[a-z]{2,}$/;

function defaultRoleForMode(currentMode, isResellerRole = false) {
  if (isResellerRole) return 'end_user';
  if (currentMode === 'resellers' || currentMode === 'create_reseller') return 'reseller';
  if (currentMode === 'administrators' || currentMode === 'create_admin') return 'admin';
  return 'end_user';
}

function sanitizeDomainTyping(raw) {
  let val = String(raw || '').replace(/^\s+/, '').toLowerCase();
  val = val.replace(/^https?:\/\//i, '');
  val = val.split('/')[0].split('?')[0].split('#')[0];
  return val;
}

function normalizeDomainFinal(raw) {
  return sanitizeDomainTyping(raw)
    .trim()
    .replace(/:\d+$/, '')
    .replace(/^\.+|\.+$/g, '');
}

function formatPackageSummary(pkg) {
  if (!pkg) return '';
  const storageText = Number(pkg.storage_limit_mb) <= 0
    ? t('Unlimited disk')
    : Number(pkg.storage_limit_mb) >= 1024
      ? `${(Number(pkg.storage_limit_mb) / 1024).toFixed(Number(pkg.storage_limit_mb) % 1024 === 0 ? 0 : 1)} GB`
      : `${pkg.storage_limit_mb} MB`;
  const siteText = Number(pkg.website_limit) < 0
    ? t('Unlimited domains')
    : `${pkg.website_limit} ${t('Domains').toLowerCase()}`;
  return `${pkg.name} (${storageText} · ${siteText})`;
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
  const [emailTouched, setEmailTouched] = useState(false);

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

  function getUserDomains(u) {
    if (Array.isArray(u.domains) && u.domains.length > 0) return u.domains;
    if (u.primary_domain) return [u.primary_domain];
    return (websites || [])
      .filter(w => Number(w.owner_id) === Number(u.id))
      .map(w => w.domain)
      .filter(Boolean);
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
      const userDomains = getUserDomains(u);
      const matchName = (u.username || '').toLowerCase().includes(q);
      const matchEmail = (u.email || '').toLowerCase().includes(q);
      const matchOwner = (creator.username || '').toLowerCase().includes(q);
      const matchDomain = userDomains.some(d => d.toLowerCase().includes(q));
      if (!matchName && !matchEmail && !matchOwner && !matchDomain) return false;
    }
    return true;
  });

  const availablePackages = packages.filter(p =>
    isResellerMode ? p.is_reseller_package : !p.is_reseller_package
  );

  const [userTab, setUserTab] = useState(isCreateMode ? 'add' : 'list');
  const [newUser, setNewUser] = useState({
    username: '',
    domain: '',
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
    if (loadPackages) loadPackages();
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
    setEditingUser(null);
    if (isCreateMode) setUserTab('add');
    else setUserTab('list');
  }, [mode, isCreateMode, isReseller]);

  React.useEffect(() => {
    if (!availablePackages.length) return;
    setNewUser(prev => {
      if (prev.package_id && availablePackages.some(p => String(p.id) === String(prev.package_id))) {
        return prev;
      }
      const firstPkg = availablePackages[0];
      return {
        ...prev,
        package_id: String(firstPkg.id),
        website_limit: firstPkg.website_limit,
        storage_limit_mb: firstPkg.storage_limit_mb,
        sftp_accounts_limit: firstPkg.sftp_accounts_limit ?? 3,
        mail_accounts_limit: firstPkg.mail_accounts_limit ?? 10,
      };
    });
  }, [packages.length, isResellerMode]);

  const [editingUser, setEditingUser] = useState(null);
  const [passwordBanner, setPasswordBanner] = useState(null);

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
    if (!Number.isInteger(websiteLimit) || websiteLimit < -1 || websiteLimit > 1000) {
      setError(t('Website limit must be between 0 and 1000.'));
      return;
    }
    if (!Number.isInteger(storageLimitMb) || storageLimitMb < -1 || storageLimitMb > 1024 * 1024) {
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

  function handleDomainChange(rawValue) {
    const typed = sanitizeDomainTyping(rawValue);
    const cleaned = normalizeDomainFinal(typed);
    setNewUser(prev => {
      const next = { ...prev, domain: typed };
      if (!emailTouched) {
        next.email = DOMAIN_VALID_RE.test(cleaned) ? `admin@${cleaned}` : '';
      }
      return next;
    });
  }

  async function createUser() {
    const assignedRole = defaultRoleForMode(mode, isReseller);
    const cleanUsername = String(newUser.username || '').trim().toLowerCase();
    const cleanDomain = assignedRole === 'end_user' ? normalizeDomainFinal(newUser.domain) : '';
    const cleanEmail = String(newUser.email || '').trim().toLowerCase()
      || (cleanDomain && DOMAIN_VALID_RE.test(cleanDomain) ? `admin@${cleanDomain}` : '');

    if (!cleanUsername) {
      setError(t('Username is required.'));
      return;
    }
    if (assignedRole === 'end_user') {
      if (!cleanDomain) {
        setError(t('Primary domain is required when creating a user.'));
        return;
      }
      if (!DOMAIN_VALID_RE.test(cleanDomain)) {
        setError(t('Please enter a valid domain name (e.g. example.com).'));
        return;
      }
    }
    if (!cleanEmail) {
      setError(t('Email is required.'));
      return;
    }
    if (!newUser.password || newUser.password.length < 12) {
      setError(t('Password must be at least 12 characters.'));
      return;
    }
    if (newUser.password.includes(':')) {
      setError(t("Password cannot contain ':'."));
      return;
    }
    if (!newUser.package_id && assignedRole !== 'admin') {
      setError(t('A package must be selected.'));
      return;
    }
    const payload = {
      username: cleanUsername,
      email: cleanEmail,
      password: newUser.password,
      role: assignedRole,
      ...(cleanDomain ? { domain: cleanDomain } : {}),
      package_id: assignedRole === 'admin' ? null : (newUser.package_id ? Number(newUser.package_id) : null),
      website_limit: Number(newUser.website_limit),
      storage_limit_mb: Number(newUser.storage_limit_mb),
      sftp_accounts_limit: Number(newUser.sftp_accounts_limit || 0),
      mail_accounts_limit: Number(newUser.mail_accounts_limit || 0),
    };
    const createdPassword = newUser.password;
    const data = await request('/users', { method: 'POST', body: JSON.stringify(payload) }, t('Creating user...'));
    if (data) {
      const assignedDomain = data.primary_domain || cleanDomain;
      setNotice(
        assignedDomain
          ? t('Created user {name} with domain {domain}.', { name: data.username, domain: assignedDomain })
          : `Created user ${data.username}`
      );
      setPasswordBanner({
        userId: null,
        title: `${t('Credentials ready')} — ${data.username}`,
        items: [
          { label: t('Username'), value: data.username, noticeMessage: t('Copied username.') },
          ...(assignedDomain
            ? [{ label: t('Primary domain'), value: assignedDomain, noticeMessage: t('Copied.') }]
            : []),
          { label: t('Password (Panel & SFTP)'), value: createdPassword },
        ],
      });
      const firstPkg = availablePackages[0];
      setEmailTouched(false);
      setNewUser({
        username: '',
        domain: '',
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
      if (refreshAll) await refreshAll();
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
    const siteCount = getUserDomains(user).length;
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
    const userTabButton = (key, Icon, label, extra = null) => (
      <button
        type="button"
        className={`user-mgmt-tab${key === 'add' ? ' add-tab' : ''}${activeUserTab === key ? ' active' : ''}`}
        role="tab"
        aria-selected={activeUserTab === key}
        aria-controls={`users-tab-${key}`}
        id={`users-tab-button-${key}`}
        onClick={() => setUserTab(key)}
      >
        <Icon size={14} />
        {t(label)}
        {extra}
      </button>
    );

    const addTabLabel = isResellerMode ? 'Add Reseller' : isAdminMode ? 'Add Administrator' : 'Add User';
    const listTabLabel = isResellerMode ? 'List Resellers' : isAdminMode ? 'List Administrators' : 'List user';
    const assignedRole = defaultRoleForMode(mode, isReseller);
    const showDomainCol = !isResellerMode && !isAdminMode;
    const showPackageAndDiskCols = !isAdminMode;
    const showCreatorSubtext = isAdmin && mode === 'users' && !isResellerMode && !isAdminMode;
    const tableColCount = 3 + (showDomainCol ? 1 : 0) + (showPackageAndDiskCols ? 2 : 0);
    const canSubmitCreate =
      !loading &&
      !!newUser.username.trim() &&
      !!newUser.password &&
      newUser.password.length >= 12 &&
      (assignedRole !== 'end_user' || !!normalizeDomainFinal(newUser.domain)) &&
      (assignedRole === 'admin' || !!newUser.package_id);

    return (
      <div className="users-page user-clean-shell">
        <div className="pkg-list-card user-mgmt-card">
          <div className="user-mgmt-toolbar">
            <div className="user-mgmt-tabs" role="tablist" aria-label={t('Panel user sections')}>
              {userTabButton('list', Users, listTabLabel, <span className="user-mgmt-count">{displayUsers.length}</span>)}
              {userTabButton('add', Plus, addTabLabel)}
            </div>

            {activeUserTab === 'list' && (
              <div className="user-mgmt-controls">
                <div className="user-search-wrap">
                  <Search size={14} className="user-search-icon" />
                  <input
                    type="text"
                    placeholder={showDomainCol ? t('Search user, email, domain...') : t('Search users...')}
                    value={searchQuery}
                    onChange={e => setSearchQuery(e.target.value)}
                    aria-label={t('Search users...')}
                  />
                  {searchQuery && (
                    <button
                      type="button"
                      className="user-search-clear"
                      onClick={() => setSearchQuery('')}
                      aria-label={t('Clear search')}
                    >
                      <X size={13} />
                    </button>
                  )}
                </div>
                {showCreatorSubtext && (
                  <select
                    value={ownerFilter}
                    onChange={e => setOwnerFilter(e.target.value)}
                    className="user-owner-select"
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
                <button type="button" className="secondary-light user-refresh-btn" disabled={!!loading} onClick={loadUsers}>
                  <RefreshCw size={14} />
                  <span>{t('Refresh')}</span>
                </button>
              </div>
            )}
          </div>

          {passwordBanner && (!editingUser || editingUser.id !== passwordBanner.userId) && (
            <div className="user-banner-slot">
              <PasswordResultBox
                title={passwordBanner.title}
                items={passwordBanner.items}
                onDismiss={() => setPasswordBanner(null)}
              />
            </div>
          )}

          {activeUserTab === 'list' && (
            <div id="users-tab-list" role="tabpanel" aria-labelledby="users-tab-button-list">
              {displayUsers.length === 0 ? (
                <div className="user-empty-slot">
                  <EmptyState icon={Users} message={t('No users found.')} />
                </div>
              ) : (
                <div className="pkg-table-wrap">
                  <table className="pkg-clean-table user-clean-table">
                    <thead>
                      <tr>
                        <th>{t('Account')}</th>
                        {showDomainCol && <th>{t('Domain')}</th>}
                        {showPackageAndDiskCols && <th>{t('Package')}</th>}
                        {showPackageAndDiskCols && <th>{t('Disk Usage')}</th>}
                        <th>{t('Status')}</th>
                        <th style={{ textAlign: 'right' }}>{t('Actions')}</th>
                      </tr>
                    </thead>
                    <tbody>
                      {displayUsers.map(user => {
                        const domains = getUserDomains(user);
                        const creator = getUserCreatorInfo(user);
                        const isEditingThis = editingUser?.id === user.id;
                        const pct = Math.min(100, Math.max(0, Number(user.storage_percent || 0)));
                        return (
                          <React.Fragment key={user.id}>
                            <tr className={isEditingThis ? 'is-editing' : ''}>
                              <td>
                                <div className="user-cell-primary">
                                  <strong>{user.username}</strong>
                                  {user.totp_enabled && <span className="user-2fa-tag">2FA</span>}
                                </div>
                                <div className="user-cell-sub">{user.email}</div>
                              </td>
                              {showDomainCol && (
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
                              )}
                              {showPackageAndDiskCols && (
                                <td>
                                  <div className="user-pkg-text">{user.package_name || t('Custom')}</div>
                                  {showCreatorSubtext && (
                                    <div className="user-cell-sub">
                                      {t('Created by')}: {creator.username}
                                    </div>
                                  )}
                                </td>
                              )}
                              {showPackageAndDiskCols && (
                                <td>
                                  <div className="user-disk-cell">
                                    <span className="user-disk-text">{storageUsageText(user)}</span>
                                    {Number(user.storage_limit_bytes) > 0 && Number(user.storage_used_bytes) >= 0 && (
                                      <div className="user-disk-bar" aria-hidden="true">
                                        <div
                                          className={`user-disk-fill${pct >= 90 ? ' danger' : pct >= 75 ? ' warn' : ''}`}
                                          style={{ width: `${pct}%` }}
                                        />
                                      </div>
                                    )}
                                  </div>
                                </td>
                              )}
                              <td>
                                <span className={`user-status-label ${user.is_active ? 'is-active' : 'is-suspended'}`}>
                                  <span className="user-status-dot" />
                                  {user.is_active ? t('Active') : t('Suspended')}
                                </span>
                              </td>
                              <td>
                                <div className="user-clean-actions">
                    {user.id !== currentUser?.id && (
                      <button className="mini secondary-light user-login-as-btn" disabled={!!loading} onClick={() => quickLoginUser(user)}>
                        <LogIn size={14} />
                        {t('Login as')}
                      </button>
                    )}
                    <button
                      type="button"
                      className={`user-icon-btn${isEditingThis ? ' active' : ''}`}
                      disabled={!!loading}
                      onClick={() => (isEditingThis ? cancelEditingUser() : startEditingUser(user))}
                      title={t('Edit')}
                      aria-label={t('Edit')}
                    >
                      <Pencil size={14} />
                    </button>
                    {user.totp_enabled && user.id !== currentUser?.id && (
                      <button className="mini secondary-light" disabled={!!loading} onClick={() => resetUserTwoFactor(user)}>
                        {t('Reset 2FA')}
                      </button>
                    )}
                    {user.id !== currentUser?.id &&
                      (user.is_active ? (
                        <button type="button" className="user-icon-btn warn-hover" disabled={!!loading} onClick={() => suspendUser(user)} title={t('Suspend')} aria-label={t('Suspend')}>
                          <Ban size={14} />
                        </button>
                      ) : (
                        <button type="button" className="user-icon-btn ok-hover" disabled={!!loading} onClick={() => unsuspendUser(user)} title={t('Unsuspend')} aria-label={t('Unsuspend')}>
                          <Play size={14} />
                        </button>
                      ))}
                    {user.id !== currentUser?.id && (
                      <button type="button" className="user-icon-btn danger-hover" disabled={!!loading} onClick={() => deletePanelUser(user)} title={t('Delete')} aria-label={t('Delete')}>
                        <Trash2 size={14} />
                      </button>
                    )}
                                </div>
                              </td>
                            </tr>
                            {isEditingThis && (
                              <tr className="user-edit-expand-tr">
                                <td colSpan={tableColCount}>
                    <div className="user-edit-inline">
                      <div className="user-edit-heading">
                        <div>
                          <strong>{t('Edit')} {user.username}</strong>
                          <small>
                            {user.id === currentUser?.id
                              ? t('Role is locked for the active admin session.')
                              : t('Package or role updates apply immediately.')}
                          </small>
                        </div>
                        <button
                          type="button"
                          className="user-icon-btn"
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
                        <label style={!isAdmin ? { display: 'none' } : undefined}>
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
                                      {formatPackageSummary(item)}
                                    </option>
                                  ))}
                              </select>
                            </label>
                            {!editingUserForm.package_id && <>
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
                            </>}
                          </>
                        )}
                      </div>
                      <div className="user-edit-pw-block">
                        <div className="user-edit-heading">
                          <div>
                            <strong>{t('Change password')} (Panel &amp; SFTP)</strong>
                            <small>
                              {t('Updates both SPanel login and SFTP password at once (minimum 12 characters).')}
                            </small>
                          </div>
                        </div>
                        <div className="user-edit-grid">
                          <label>
                            <span>{t('New password')}</span>
                            <PasswordField
                              placeholder={t('Min 12 characters')}
                              value={editingUserForm.new_password}
                              onChange={e => setEditingUserForm(prev => ({ ...prev, new_password: e.target.value }))}
                              onGenerate={gen => setEditingUserForm(prev => ({ ...prev, new_password: gen, confirm_password: gen }))}
                            />
                          </label>
                          <label>
                            <span>{t('Confirm password')}</span>
                            <PasswordField
                              showGenerator={false}
                              showCopy={false}
                              placeholder={t('Repeat password')}
                              value={editingUserForm.confirm_password}
                              onChange={e => setEditingUserForm(prev => ({ ...prev, confirm_password: e.target.value }))}
                            />
                          </label>
                        </div>
                        <div className="user-edit-actions">
                          <button
                            type="button"
                            disabled={!!loading || !editingUserForm.new_password || editingUserForm.new_password.length < 12}
                            onClick={async () => {
                              const res = await submitPasswordChange(user);
                              if (res?.ok) {
                                setPasswordBanner({
                                  userId: user.id,
                                  title: `${t('Password updated')} — ${res.username}`,
                                  items: [
                                    { label: t('Username'), value: res.username, noticeMessage: t('Copied username.') },
                                    { label: t('Password (Panel & SFTP)'), value: res.password },
                                  ],
                                });
                              }
                            }}
                          >
                            {t('Set password')}
                          </button>
                        </div>
                        {passwordBanner && passwordBanner.userId === user.id && (
                          <PasswordResultBox
                            title={passwordBanner.title}
                            items={passwordBanner.items}
                            onDismiss={() => setPasswordBanner(null)}
                          />
                        )}
                      </div>
                      <div className="user-edit-actions">
                        <button type="button" className="secondary-light" onClick={cancelEditingUser}>
                          {t('Cancel')}
                        </button>
                        <button type="button" disabled={!!loading || !editingUserForm.email.trim()} onClick={updatePanelUser}>
                          <Save size={14} />
                          {t('Save changes')}
                        </button>
                      </div>
                    </div>
                                </td>
                              </tr>
                            )}
                          </React.Fragment>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              )}

            {isAdmin && !isResellerMode && !isAdminMode && websites.length > 0 && <div className="user-assign-footer">
              <div>
                <strong>{t('Assign domain to user')}</strong>
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
                <button type="button" disabled={!assignWebsiteId || !assignUserId || !!loading} onClick={assignDomainToUser}>
                  {t('Assign')}
                </button>
              </div>
            </div>}
            </div>
          )}

        {activeUserTab === 'add' && (
          <div className="user-add-body" id="users-tab-add" role="tabpanel" aria-labelledby="users-tab-button-add">
            <div className="user-add-intro">
              <h3>
                {t(isResellerMode ? 'Create Reseller' : isAdminMode ? 'Create Administrator' : 'Add User')}
              </h3>
              <p className="hint">
                {assignedRole === 'end_user'
                  ? t('Enter the username, primary domain, and unified password (used for both SPanel login and SFTP). The domain will be ready under Domains as soon as the user signs in.')
                  : t('Use one unified password for both SPanel login and SFTP access.')}
              </p>
            </div>
            <div className="user-add-grid">
              <label>
                <span>{t('Username')}</span>
                <input
                  value={newUser.username}
                  onChange={e => setNewUser(prev => ({ ...prev, username: e.target.value.toLowerCase().replace(/\s+/g, '') }))}
                  placeholder="johndoe"
                />
              </label>
              {assignedRole === 'end_user' && (
                <label>
                  <span>{t('Primary domain')}</span>
                  <input
                    value={newUser.domain}
                    onChange={e => handleDomainChange(e.target.value)}
                    onBlur={e => setNewUser(prev => ({ ...prev, domain: normalizeDomainFinal(e.target.value) }))}
                    placeholder="domain.com"
                  />
                </label>
              )}
              <label>
                <span>{t('Email')}</span>
                <input
                  type="email"
                  value={newUser.email}
                  onChange={e => {
                    const val = e.target.value;
                    setEmailTouched(val.trim().length > 0);
                    setNewUser(prev => ({ ...prev, email: val }));
                  }}
                  placeholder={
                    assignedRole === 'end_user' && normalizeDomainFinal(newUser.domain)
                      ? `admin@${normalizeDomainFinal(newUser.domain)}`
                      : 'admin@domain.com'
                  }
                />
              </label>
              <label>
                <span>{t('Password (Panel & SFTP)')}</span>
                <PasswordField
                  value={newUser.password}
                  onChange={e => setNewUser(prev => ({ ...prev, password: e.target.value }))}
                  placeholder={t('Min 12 characters')}
                />
              </label>
              {assignedRole !== 'admin' && (
                <>
                  <label className={assignedRole === 'end_user' ? 'user-add-span-full' : ''}>
                    <span>{t('Package')}</span>
                    <select value={newUser.package_id} onChange={e => applyPackageToNewUser(e.target.value)}>
                      <option value="" disabled>
                        -- {t('Select a Package')} --
                      </option>
                      {availablePackages.map(item => (
                        <option key={item.id} value={item.id}>
                          {formatPackageSummary(item)}
                        </option>
                      ))}
                    </select>
                  </label>
                  {!newUser.package_id && <>
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
                  </>}
                </>
              )}
            </div>
            <div className="user-add-footer">
              <button type="button" className="secondary-light" onClick={() => setUserTab('list')}>
                {t('Cancel')}
              </button>
              <button type="button" disabled={!canSubmitCreate} onClick={createUser}>
                <Plus size={14} />
                {t(isResellerMode ? 'Create reseller' : isAdminMode ? 'Create administrator' : 'Create user')}
              </button>
            </div>
          </div>
        )}
        </div>
      </div>
    );
  }
  return renderUsers();
}
