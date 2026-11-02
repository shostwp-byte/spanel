import React, { useState, useEffect } from 'react';
import { HelpCircle } from 'lucide-react';
import { PasswordField, PasswordResultBox } from '../components/PasswordField.jsx';
import { useAppContext } from '../contexts/AppContext.jsx';
import { t } from '../i18n.js';

export function ChangePasswords({ request }) {
  const { users, loadUsers, currentUser, setNotice, setError } = useAppContext();

  useEffect(() => {
    if (loadUsers) loadUsers();
  }, []);
  
  const manageableUsers = (users || []).filter(u => u.id !== currentUser?.id);
  
  const [selectedUserId, setSelectedUserId] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [updatedCreds, setUpdatedCreds] = useState(null);
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!selectedUserId) {
      return setError(t('Please select a user first.'));
    }
    if (!newPassword || newPassword.length < 12) {
      return setError(t('Password must be at least 12 characters.'));
    }
    if (newPassword.includes(':')) {
      return setError(t("Password cannot contain ':'."));
    }

    const targetUser = manageableUsers.find(u => String(u.id) === String(selectedUserId));
    const submittedPassword = newPassword;

    setLoading(true);
    let successCount = 0;

    try {
      const res1 = await request(`/users/${selectedUserId}/password`, {
        method: 'POST',
        body: JSON.stringify({ password: submittedPassword })
      }, t('Changing panel password...'));
      if (res1) successCount++;
      
      const res2 = await request(`/users/${selectedUserId}/sftp-password`, {
        method: 'POST',
        body: JSON.stringify({ password: submittedPassword })
      }, t('Changing SFTP password...'));
      if (res2) successCount++;

      if (successCount > 0) {
        setNotice(t('Passwords updated successfully.'));
        setUpdatedCreds({
          username: targetUser?.username || String(selectedUserId),
          password: submittedPassword,
          panelOk: !!res1,
          sftpOk: !!res2,
        });
        setNewPassword('');
        setSelectedUserId('');
      }
    } catch (err) {
      setError(err.message || t('Failed to update passwords.'));
    }
    setLoading(false);
  };

  return (
    <div style={{ animation: 'fade-in 0.3s ease-out' }}>
      <div style={{ marginBottom: '20px', paddingBottom: '15px', borderBottom: '1px solid var(--border)' }}>
        <h2 style={{ fontSize: '24px', fontWeight: '400', color: 'var(--text)', margin: 0, display: 'flex', alignItems: 'center', gap: '8px' }}>
          {t('Change Passwords')}
          <HelpCircle size={16} color="var(--text-muted)" style={{ cursor: 'pointer' }} />
        </h2>
      </div>

      <div style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: '4px', padding: '0', boxShadow: '0 1px 3px rgba(0,0,0,0.02)' }}>
        <form onSubmit={handleSubmit}>
          
          <div className="resp-form-row">
            <div className="resp-form-label" style={{ fontSize: '14px', color: 'var(--text-secondary)', fontWeight: '500' }}>
              {t('Username:')}
            </div>
            <div className="resp-form-control">
              <select 
                value={selectedUserId}
                onChange={e => setSelectedUserId(e.target.value)}
                style={{ width: '100%', padding: '10px 15px', border: '1px solid var(--border)', borderRadius: '4px', fontSize: '14px', outline: 'none', color: 'var(--text)', background: 'var(--surface)', appearance: 'auto' }}
              >
                <option value="">{t('Select a user...')}</option>
                {manageableUsers.map(u => (
                  <option key={u.id} value={u.id}>{u.username} ({u.email})</option>
                ))}
              </select>
            </div>
          </div>

          <div className="resp-form-row">
            <div className="resp-form-label" style={{ fontSize: '14px', color: 'var(--text-secondary)', fontWeight: '500' }}>
              {t('Enter Password:')}
            </div>
            <div className="resp-form-control">
              <PasswordField
                value={newPassword}
                onChange={e => setNewPassword(e.target.value)}
                placeholder={t('Min 12 characters')}
              />
            </div>
          </div>

          <div style={{ padding: '20px 25px', display: 'flex', justifyContent: 'flex-end', background: 'var(--surface)' }}>
            <button 
              type="submit" 
              disabled={loading}
              style={{ background: '#10b981', color: '#fff', border: 'none', padding: '10px 24px', borderRadius: '4px', fontSize: '14px', fontWeight: '600', cursor: 'pointer', textTransform: 'uppercase', letterSpacing: '0.5px', opacity: loading ? 0.7 : 1 }}
            >
              {loading ? t('Submitting...') : t('SUBMIT')}
            </button>
          </div>

        </form>
      </div>

      {updatedCreds && (
        <PasswordResultBox
          title={`${t('Password updated')} — ${updatedCreds.username}`}
          items={[
            { label: t('Username'), value: updatedCreds.username, noticeMessage: t('Copied username.') },
            {
              label: updatedCreds.panelOk && updatedCreds.sftpOk
                ? `${t('Panel password')} & SFTP`
                : t('New password'),
              value: updatedCreds.password,
            },
          ]}
          onDismiss={() => setUpdatedCreds(null)}
        />
      )}
    </div>
  );
}
