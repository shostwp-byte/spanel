import React, { useState, useEffect } from 'react';
import { RefreshCw, Trash2, ChevronLeft, ChevronRight } from 'lucide-react';
import { useAppContext } from '../contexts/AppContext.jsx';
import { t } from '../i18n.js';

export function ProcessMonitorPage({ request, isAdmin }) {
  const { setNotice, setError } = useAppContext();
  const [data, setData] = useState({ summary: '', items: [] });
  const [loading, setLoading] = useState(false);
  const [searchTerm, setSearchTerm] = useState('');
  const [autoReload, setAutoReload] = useState(15);
  const [currentPage, setCurrentPage] = useState(1);
  const [pageSize, setPageSize] = useState(50);

  async function loadData() {
    setLoading(true);
    try {
      const res = await request('/processes');
      if (res && res.items) setData(res);
    } catch (e) {
      setError(t('Failed to load processes.'));
    }
    setLoading(false);
  }

  useEffect(() => {
    if (isAdmin) loadData();
  }, [isAdmin]);

  useEffect(() => {
    if (autoReload === 0 || !isAdmin || searchTerm) return;
    const interval = setInterval(() => {
      loadData();
    }, autoReload * 1000);
    return () => clearInterval(interval);
  }, [isAdmin, searchTerm, autoReload]);

  async function handleKill(pid) {
    if (!window.confirm(t(`Send SIGKILL to process ${pid}?`))) return;
    try {
      const res = await request(`/processes/${pid}/kill`, { method: 'POST' });
      if (res && res.success) {
        setNotice(t(`Process ${pid} killed.`));
        loadData();
      }
    } catch (e) {
      setError(e.message || t('Failed to kill process.'));
    }
  }

  const filteredProcesses = (data.items || []).filter(p => 
    (p.cmd || '').toLowerCase().includes(searchTerm.toLowerCase()) || 
    (p.user || '').toLowerCase().includes(searchTerm.toLowerCase()) ||
    String(p.pid || '').includes(searchTerm)
  );

  const totalProcesses = filteredProcesses.length;
  const totalPages = Math.max(1, Math.ceil(totalProcesses / pageSize));
  
  useEffect(() => {
    if (currentPage > totalPages) {
      setCurrentPage(totalPages);
    }
  }, [totalPages, currentPage]);

  const startIndex = (currentPage - 1) * pageSize;
  const endIndex = Math.min(startIndex + pageSize, totalProcesses);
  const currentProcesses = filteredProcesses.slice(startIndex, endIndex);

  function renderSummary(summary) {
    if (!summary) return t('Waiting for top command data...');
    const rawLines = summary.replace(/\x1b\[.*?m/g, '').split('\n');
    return rawLines.map((rawLine, idx) => {
      const line = rawLine.trim();
      if (!line) return null;
      
      let title = '';
      let content = '';
      
      const lowerLine = line.toLowerCase();
      if (lowerLine.startsWith('top -')) {
        title = 'Top:';
        content = line.substring(line.indexOf('-') + 1).trim();
      } else if (lowerLine.startsWith('%cpu')) {
        title = 'CPU(s):';
        const colonIdx = line.indexOf(':');
        content = colonIdx > -1 ? line.substring(colonIdx + 1).trim() : line;
      } else if (lowerLine.startsWith('mib mem') || lowerLine.startsWith('kib mem')) {
        title = 'KiB Mem:';
        const colonIdx = line.indexOf(':');
        content = colonIdx > -1 ? line.substring(colonIdx + 1).trim() : line;
      } else if (lowerLine.startsWith('mib swap') || lowerLine.startsWith('kib swap')) {
        title = 'KiB Swap:';
        const colonIdx = line.indexOf(':');
        content = colonIdx > -1 ? line.substring(colonIdx + 1).trim() : line;
      } else {
        const colonIdx = line.indexOf(':');
        if (colonIdx > -1) {
          title = line.substring(0, colonIdx + 1).trim();
          content = line.substring(colonIdx + 1).trim();
        } else {
          return <div key={idx} style={{ paddingLeft: '95px', color: 'var(--text-secondary)' }}>{line}</div>;
        }
      }
      
      title = title.charAt(0).toUpperCase() + title.slice(1);
      
      return (
        <div key={idx} style={{ display: 'flex', whiteSpace: 'pre' }}>
          <span style={{ fontWeight: '500', width: '90px', display: 'inline-block', textAlign: 'left', color: 'var(--text-muted)', flexShrink: 0 }}>{title}</span>
          <span style={{ color: 'var(--text)', marginLeft: '5px' }}>{content}</span>
        </div>
      );
    });
  }

  if (!isAdmin) return null;

  return (
    <div style={{ animation: 'fade-in 0.3s ease-out' }}>
      <div style={{ display: 'flex', justifyContent: 'flex-end', marginBottom: '20px' }}>
        <input 
          type="text" 
          placeholder={t('Enter your search criteria...')} 
          value={searchTerm}
          onChange={e => { setSearchTerm(e.target.value); setCurrentPage(1); }}
          className="input"
          style={{ width: 'min(300px, 100%)', padding: '8px 12px', border: '1px solid var(--border)', borderRadius: '4px', fontSize: '13px', background: 'var(--surface)', color: 'var(--text)' }}
        />
      </div>

      <div style={{ background: 'var(--surface)', padding: '15px 20px', fontFamily: 'monospace', fontSize: '13px', border: '1px solid var(--border)', borderRadius: '4px', marginBottom: '20px', overflowX: 'auto' }}>
        {renderSummary(data.summary)}
      </div>

      <div style={{ display: 'flex', justifyContent: 'flex-end', marginBottom: '15px', gap: '10px', flexWrap: 'wrap' }}>
        <button className="secondary" onClick={loadData} disabled={loading} style={{ background: 'var(--surface)', color: 'var(--text)', border: '1px solid var(--border)', padding: '6px 12px', borderRadius: '4px', display: 'flex', alignItems: 'center', gap: '6px', fontSize: '13px' }}>
          <RefreshCw size={14} className={loading ? 'spin' : ''} /> {t('Reload')}
        </button>
        <select 
          value={autoReload} 
          onChange={e => setAutoReload(Number(e.target.value))}
          style={{ width: 'auto', minWidth: '80px', padding: '6px 12px', border: '1px solid var(--primary)', background: 'var(--primary-light)', color: 'var(--primary)', borderRadius: '4px', fontSize: '13px', cursor: 'pointer', fontWeight: '500' }}
        >
          <option value={0}>{t('Off')}</option>
          <option value={5}>5s</option>
          <option value={15}>15s</option>
          <option value={30}>30s</option>
          <option value={60}>60s</option>
        </select>
      </div>

      <div style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: '4px', overflow: 'hidden' }}>
        <div style={{ width: '100%', overflowX: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', textAlign: 'left', fontSize: '13px' }}>
            <thead>
              <tr style={{ borderBottom: '1px solid var(--border)', background: 'var(--surface-alt)' }}>
                <th style={{ padding: '12px 15px', fontWeight: '700', color: 'var(--text)' }}>PID</th>
                <th style={{ padding: '12px 15px', fontWeight: '700', color: 'var(--text)' }}>USER</th>
                <th style={{ padding: '12px 15px', fontWeight: '700', color: 'var(--text)' }}>PR</th>
                <th style={{ padding: '12px 15px', fontWeight: '700', color: 'var(--text)' }}>NI</th>
                <th style={{ padding: '12px 15px', fontWeight: '700', color: 'var(--text)' }}>VIRT</th>
                <th style={{ padding: '12px 15px', fontWeight: '700', color: 'var(--text)' }}>RES</th>
                <th style={{ padding: '12px 15px', fontWeight: '700', color: 'var(--text)' }}>SHR</th>
                <th style={{ padding: '12px 15px', fontWeight: '700', color: 'var(--text)' }}>S</th>
                <th style={{ padding: '12px 15px', fontWeight: '700', color: 'var(--text)' }}>%CPU</th>
                <th style={{ padding: '12px 15px', fontWeight: '700', color: 'var(--text)' }}>%MEM</th>
                <th style={{ padding: '12px 15px', fontWeight: '700', color: 'var(--text)' }}>TIME+</th>
                <th style={{ padding: '12px 15px', fontWeight: '700', color: 'var(--text)' }}>COMMAND</th>
                <th style={{ padding: '12px 15px', width: '40px' }}></th>
              </tr>
            </thead>
            <tbody>
              {currentProcesses.map((p, idx) => (
                <tr key={p.pid} style={{ borderBottom: '1px solid var(--border)', background: idx % 2 === 0 ? 'var(--surface-alt)' : 'var(--surface)' }}>
                  <td style={{ padding: '12px 15px', color: 'var(--text-secondary)' }}>{p.pid}</td>
                  <td style={{ padding: '12px 15px', color: 'var(--text-secondary)' }}>{p.user}</td>
                  <td style={{ padding: '12px 15px', color: 'var(--text-secondary)' }}>{p.pr || '-'}</td>
                  <td style={{ padding: '12px 15px', color: 'var(--text-secondary)' }}>{p.ni || '-'}</td>
                  <td style={{ padding: '12px 15px', color: 'var(--text-secondary)' }}>{p.virt || '-'}</td>
                  <td style={{ padding: '12px 15px', color: 'var(--text-secondary)' }}>{p.res || '-'}</td>
                  <td style={{ padding: '12px 15px', color: 'var(--text-secondary)' }}>{p.shr || '-'}</td>
                  <td style={{ padding: '12px 15px', color: 'var(--text-secondary)' }}>{p.s || (p.stat ? p.stat : '-')}</td>
                  <td style={{ padding: '12px 15px', color: 'var(--text-secondary)' }}>{p.cpu}</td>
                  <td style={{ padding: '12px 15px', color: 'var(--text-secondary)' }}>{p.mem}</td>
                  <td style={{ padding: '12px 15px', color: 'var(--text-secondary)' }}>{p.time}</td>
                  <td style={{ padding: '12px 15px', color: 'var(--text-secondary)', maxWidth: '350px', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }} title={p.cmd}>
                    {p.cmd}
                  </td>
                  <td style={{ padding: '12px 15px', textAlign: 'right' }}>
                    <Trash2 size={14} color="#dc2626" style={{ cursor: 'pointer', opacity: 0.7 }} onClick={() => handleKill(p.pid)} title={t('Kill')} />
                  </td>
                </tr>
              ))}
              {(data.items || []).length > 0 && currentProcesses.length === 0 && (
                <tr>
                  <td colSpan="13" style={{ padding: '40px', textAlign: 'center', color: 'var(--text-muted)' }}>
                    {t('No processes match your search.')}
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
        
        {totalProcesses > 0 && (
          <div style={{ padding: '15px 20px', display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '10px', background: 'var(--surface)', fontSize: '13px', color: 'var(--text-secondary)', borderTop: '1px solid var(--border)' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
              <span style={{ whiteSpace: 'nowrap' }}>{startIndex + 1}-{endIndex} of {totalProcesses} processes</span>
              <select 
                value={pageSize} 
                onChange={e => { setPageSize(Number(e.target.value)); setCurrentPage(1); }} 
                style={{ padding: '2px 6px', border: '1px solid var(--border)', background: 'var(--surface)', color: 'var(--text)', cursor: 'pointer', outline: 'none', fontSize: '13px', borderRadius: '4px', minWidth: '60px' }}
              >
                <option value={10}>10</option>
                <option value={20}>20</option>
                <option value={50}>50</option>
                <option value={100}>100</option>
                <option value={500}>500</option>
              </select>
            </div>
            
            {totalPages > 1 && (
              <div style={{ display: 'flex', gap: '5px', flexWrap: 'wrap' }}>
                <button 
                  onClick={() => setCurrentPage(p => Math.max(1, p - 1))} 
                  disabled={currentPage === 1}
                  style={{ padding: '5px', minWidth: '32px', height: '32px', display: 'flex', alignItems: 'center', justifyContent: 'center', background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: '4px', cursor: currentPage === 1 ? 'not-allowed' : 'pointer', color: currentPage === 1 ? 'var(--text-muted)' : 'var(--text-secondary)' }}
                >
                  <ChevronLeft size={16} />
                </button>
                
                {Array.from({ length: totalPages }, (_, i) => i + 1)
                  .filter(p => p === 1 || p === totalPages || Math.abs(p - currentPage) <= 2)
                  .map((p, idx, arr) => {
                    if (idx > 0 && arr[idx - 1] !== p - 1) {
                      return <span key={`ellipsis-${p}`} style={{ padding: '5px', minWidth: '32px', height: '32px', display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--text-muted)' }}>...</span>;
                    }
                    return (
                      <button 
                        key={p} 
                        onClick={() => setCurrentPage(p)}
                        style={{ padding: '5px 12px', minWidth: '32px', height: '32px', background: p === currentPage ? 'var(--primary)' : 'var(--surface)', color: p === currentPage ? '#fff' : 'var(--text-secondary)', border: '1px solid', borderColor: p === currentPage ? 'var(--primary)' : 'var(--border)', borderRadius: '4px', cursor: 'pointer', fontWeight: p === currentPage ? '600' : '400' }}
                      >
                        {p}
                      </button>
                    );
                  })}

                <button 
                  onClick={() => setCurrentPage(p => Math.min(totalPages, p + 1))} 
                  disabled={currentPage === totalPages}
                  style={{ padding: '5px', minWidth: '32px', height: '32px', display: 'flex', alignItems: 'center', justifyContent: 'center', background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: '4px', cursor: currentPage === totalPages ? 'not-allowed' : 'pointer', color: currentPage === totalPages ? 'var(--text-muted)' : 'var(--text-secondary)' }}
                >
                  <ChevronRight size={16} />
                </button>
              </div>
            )}
          </div>
        )}
      </div>
      <style dangerouslySetInnerHTML={{__html:`
        @keyframes spin { 100% { transform: rotate(360deg); } }
        .spin { animation: spin 1s linear infinite; }
      `}} />
    </div>
  );
}
