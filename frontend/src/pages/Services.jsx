import React, { useState, useEffect } from 'react';
import { Archive, ArchiveRestore, ArrowLeft, Ban, Bot, Boxes, Check, ChevronDown, Clock, Code2, Copy, Cpu, Database, Dices, ExternalLink, Eye, FileText, FolderOpen, Globe, HardDrive, Home, Image, KeyRound, Lock, LogIn, LogOut, MemoryStick, Menu, Moon, MoveRight, Network, Pencil, Save, Search, Server, Settings as SettingsIcon, Shield, Sun, Trash2, TerminalIcon, Users, X, RefreshCw, Plus, Download, Upload, Play, Square, RotateCcw, AlertCircle, Activity, BrickWall, Bug, LockKeyhole, PackageOpen, ScrollText, ShieldAlert, CheckCircle, Zap, Bell, Mail, Send, Cloud, Inbox, Forward, ShieldCheck } from 'lucide-react';
import { EmptyState } from '../components/EmptyState.jsx';
import { useAppContext } from '../contexts/AppContext.jsx';
import { t } from '../i18n.js';

const DEFAULT_SERVICE_NAMES = ['spanel-api', 'nginx', 'php8.3-fpm', 'php8.4-fpm', 'mariadb', 'redis-server'];

export function ServicesPage({ request, isAdmin }) {
  const { loading, setLoading, error, setError, setNotice } = useAppContext();
  const [serviceStates, setServiceStates] = useState({});
  const [serviceNames, setServiceNames] = useState(DEFAULT_SERVICE_NAMES);

  useEffect(() => {
    loadServiceNames().then(checkAllServices);
  }, []);

  useEffect(() => {
    if (!loading) {
      const timer = setInterval(checkAllServices, 10000);
      return () => clearInterval(timer);
    }
  }, [loading]);

  async function checkService(name) {
    const data = await request('/services/action', { method: 'POST', body: JSON.stringify({ name, action: 'status' }) });
    setServiceStates(prev => ({ ...prev, [name]: data || { stdout: '', stderr: error || 'Cannot check', returncode: 1 } }));
    return data;
  }

  async function loadServiceNames() {
    const data = await request('/services/list');
    const names = data?.services?.length ? data.services : serviceNames;
    setServiceNames(names);
    return names;
  }

  async function checkAllServices() {
    setLoading('Checking services...');
    const names = await loadServiceNames();
    for (const name of names) { await checkService(name); }
    setLoading('');
  }

  async function runServiceAction(name, action) {
    await request('/services/action', { method: 'POST', body: JSON.stringify({ name, action }) }, `${action} ${name}...`);
    await checkService(name);
  }

  function renderServices() {
    return <div className="section">
      <div className="section-title">
        <div>
          <h2>{t('Services')}</h2>
          <p className="hint">{t('Auto-refreshes every 10s')}</p>
        </div>
        <button className="secondary" disabled={!!loading} onClick={checkAllServices}><RefreshCw size={15}/>{t('Refresh')}</button>
      </div>
      {/* A running service's Start and a stopped one's Stop are disabled, so
          the enabled button is the obvious next step. */}
      <div className="service-grid">
        {serviceNames.map(name => {
          const state = serviceStates[name];
          const text = `${state?.stdout || ''} ${state?.stderr || ''}`;
          const active = text.includes('active (running)');
          const inactive = text.includes('inactive') || text.includes('failed');
          const canStop = !['spanel-api', 'redis-server'].includes(name);
          return <div className="service-card" key={name}>
            <div><strong>{name}</strong><span className={active ? 'badge ok' : inactive ? 'badge bad' : 'badge'}>{active ? t('Running') : inactive ? t('Stopped') : '...'}</span></div>
            {isAdmin && <div className="service-actions">
              <button className="secondary" disabled={active} onClick={() => runServiceAction(name, 'start')}><Play size={13}/>{t('Start')}</button>
              {canStop && <button className="danger-light" disabled={inactive} onClick={() => runServiceAction(name, 'stop')}><Square size={13}/>{t('Stop')}</button>}
              <button className="secondary" onClick={() => runServiceAction(name, 'restart')}><RotateCcw size={13}/>{t('Restart')}</button>
            </div>}
          </div>;
        })}
      </div>
    </div>;
  }
  return renderServices();
}
