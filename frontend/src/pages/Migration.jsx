import React, { useState, useEffect, useRef } from 'react';
import { Cloud } from 'lucide-react';
import { t } from '../i18n.js';

export function Migration({ request, setError, setNotice, loading }) {
  const [migrateJob, setMigrateJob] = useState(null);
  const timerRef = useRef(null);
  const [migrateForm, setMigrateForm] = useState({
    panel_type: 'directadmin',
    method: 'url',
    source_url: '',
    ftp_host: '',
    ftp_port: 21,
    ftp_user: '',
    ftp_pass: '',
    ftp_path: '',
    import_websites: true,
    import_databases: true,
    import_emails: true
  });

  useEffect(() => {
    return () => {
      if (timerRef.current) clearInterval(timerRef.current);
    };
  }, []);

  async function startUserMigration() {
    if (!migrateForm.source_url && migrateForm.method === 'url') return setError(t('Please enter the backup download URL.'));
    if (!migrateForm.ftp_host && migrateForm.method === 'ftp') return setError(t('Please enter the FTP host.'));
    
    const data = await request('/migration/start', { method: 'POST', body: JSON.stringify(migrateForm) }, t('Starting migration...'));
    if (data?.job_id) {
      setNotice(t('Migration started! You can close the browser and check back later.'));
      setMigrateJob(data);
      pollMigrationJob(data.job_id);
    }
  }

  async function pollMigrationJob(jobId) {
    if (timerRef.current) clearInterval(timerRef.current);
    timerRef.current = setInterval(async () => {
      const data = await request(`/migration/status/${jobId}`, { silent: true });
      if (data) {
        setMigrateJob(data);
        if (data.status === 'completed' || data.status === 'failed') {
          clearInterval(timerRef.current);
          timerRef.current = null;
          if (data.status === 'completed') setNotice(t('Migration completed!'));
          if (data.status === 'failed') setError(t('Migration failed: ') + (data.error || 'Unknown error'));
        }
      }
    }, 3000);
  }

  return (<div className="section packages-page">
      <div className="section-title">
        <div>
          <h2>{t('Data Migration')}</h2>
          <p className="hint">{t('Automatically migrate websites, databases, and emails from your previous hosting server to SPanel.')}</p>
        </div>
      </div>
      
      {migrateJob && migrateJob.status !== 'failed' ? (
        <div className="user-create-card">
          <h3>{t('Migration Status:')} {migrateJob.status === 'completed' ? t('Completed') : t('In progress...')}</h3>
          <p style={{ marginTop: 10, color: '#3b82f6', fontWeight: 600 }}>{migrateJob.progress}</p>
          {migrateJob.status === 'completed' && <button onClick={() => setMigrateJob(null)} style={{marginTop: 15}}>{t('Migrate another backup')}</button>}
        </div>
      ) : (
        <div className="user-create-card package-create-card" style={{ maxWidth: 1000 }}>
          <label><span>{t('Source Control Panel')}</span><select value={migrateForm.panel_type} onChange={e => setMigrateForm(p => ({...p, panel_type: e.target.value}))}>
              <option value="directadmin">DirectAdmin</option>
              <option value="cpanel">cPanel</option>
              <option value="plesk">Plesk</option>
          </select></label>
          
          <label><span>{t('Transfer Method')}</span><select value={migrateForm.method} onChange={e => setMigrateForm(p => ({...p, method: e.target.value}))}>
              <option value="url">{t('Direct Download URL (wget)')}</option>
              <option value="ftp">{t('Remote FTP')}</option>
          </select></label>

          {migrateForm.method === 'url' ? (
            <label><span>{t('Backup Archive URL (.tar.gz)')}</span><input type="text" value={migrateForm.source_url} onChange={e => setMigrateForm(p => ({...p, source_url: e.target.value}))} placeholder="https://old-host.com/backup.tar.gz" /></label>
          ) : (
            <>
              <label><span>{t('FTP Host / IP')}</span><input type="text" value={migrateForm.ftp_host} onChange={e => setMigrateForm(p => ({...p, ftp_host: e.target.value}))} placeholder="123.123.123.123" /></label>
              <label><span>{t('FTP Port')}</span><input type="number" value={migrateForm.ftp_port} onChange={e => setMigrateForm(p => ({...p, ftp_port: parseInt(e.target.value)}))} /></label>
              <label><span>{t('FTP Username')}</span><input type="text" value={migrateForm.ftp_user} onChange={e => setMigrateForm(p => ({...p, ftp_user: e.target.value}))} placeholder="user" /></label>
              <label><span>{t('FTP Password')}</span><input type="password" value={migrateForm.ftp_pass} onChange={e => setMigrateForm(p => ({...p, ftp_pass: e.target.value}))} placeholder="***" /></label>
              <label><span>{t('Remote File Path (FTP Path)')}</span><input type="text" value={migrateForm.ftp_path} onChange={e => setMigrateForm(p => ({...p, ftp_path: e.target.value}))} placeholder="/backups/backup-1.tar.gz" /></label>
            </>
          )}

          <div style={{ marginTop: 20, marginBottom: 10 }}><strong>{t('Data to restore:')}</strong></div>
          <label className="check-line"><input type="checkbox" checked={migrateForm.import_websites} onChange={e => setMigrateForm(p => ({...p, import_websites: e.target.checked}))} />{t('Restore Website Files & Source Code')}</label>
          <label className="check-line"><input type="checkbox" checked={migrateForm.import_databases} onChange={e => setMigrateForm(p => ({...p, import_databases: e.target.checked}))} />{t('Restore Databases')}</label>
          <label className="check-line"><input type="checkbox" checked={migrateForm.import_emails} onChange={e => setMigrateForm(p => ({...p, import_emails: e.target.checked}))} />{t('Restore Email Accounts (Mailboxes)')}</label>
          
          <button style={{ marginTop: 20 }} disabled={!!loading} onClick={startUserMigration}><Cloud size={14}/> {t('Start Migration')}</button>
        </div>
      )}
    </div>
  );
}
