import React, { useState, useEffect } from 'react';
import {
  Activity, AlertCircle, Check, CheckCircle, Clock, Copy, Cpu, Database,
  Edit2, Globe, HardDrive, KeyRound, Network, Plus, RefreshCw, Save,
  Server, ShieldCheck, Trash2, Wifi, Zap
} from 'lucide-react';
import { EmptyState } from '../components/EmptyState.jsx';
import { RemoteDbHostsPanel } from '../components/RemoteDbHosts.jsx';
import { useAppContext } from '../contexts/AppContext.jsx';
import { t } from '../i18n.js';

export function MultiServerPage({ request, isAdmin }) {
  const { loading, setError, setNotice } = useAppContext();

  const [activeTab, setActiveTab] = useState('nodes');
  const [clusterData, setClusterData] = useState(null);
  const [settingsForm, setSettingsForm] = useState({
    enabled: true,
    node_name: '',
    auto_dns_sync: true,
    check_domain_conflict: true,
    check_username_conflict: false,
  });

  // Peer Node form state
  const [showNodeForm, setShowNodeForm] = useState(false);
  const [nodeForm, setNodeForm] = useState({
    id: null,
    name: '',
    url: '',
    api_token: '',
    role: 'full_peer',
    verify_tls: false,
    sync_dns: true,
    check_domain: true,
    enabled: true,
  });

  // Local token generator state
  const [generatedClusterToken, setGeneratedClusterToken] = useState('');

  useEffect(() => {
    if (isAdmin) {
      loadClusterStatus();
    }
  }, [isAdmin]);

  async function loadClusterStatus(silent = false) {
    const data = await request(
      '/multi-server/status',
      silent ? { silent: true } : {},
      silent ? '' : 'Đang tải thông tin cụm Multi-Server...'
    );
    if (data) {
      setClusterData(data);
      if (data.settings) {
        setSettingsForm({
          enabled: data.settings.enabled !== false,
          node_name: data.settings.node_name || data.local_node?.hostname || '',
          auto_dns_sync: data.settings.auto_dns_sync !== false,
          check_domain_conflict: data.settings.check_domain_conflict !== false,
          check_username_conflict: !!data.settings.check_username_conflict,
        });
      }
    }
  }

  async function saveClusterSettings() {
    const res = await request(
      '/multi-server/settings',
      {
        method: 'PUT',
        body: JSON.stringify(settingsForm),
      },
      'Đang lưu cấu hình Multi-Server...'
    );
    if (res) {
      await loadClusterStatus(true);
    }
  }

  async function generateLocalClusterToken() {
    const hostLabel = clusterData?.local_node?.hostname || 'Node';
    const res = await request(
      '/multi-server/local-token',
      {
        method: 'POST',
        body: JSON.stringify({
          name: `Cluster Peer (${hostLabel})`,
          allowed_ips: '',
        }),
      },
      'Đang tạo khóa Cluster API Token...'
    );
    if (res?.token) {
      setGeneratedClusterToken(res.token);
    }
  }

  async function copyValue(val, msg = 'Đã sao chép vào bộ nhớ tạm.') {
    if (!val) return;
    try {
      await navigator.clipboard.writeText(val);
      setNotice(msg);
    } catch {
      setError('Không thể tự động sao chép, vui lòng bôi đen và nhấn Ctrl+C.');
    }
  }

  async function submitNodeForm(e) {
    e.preventDefault();
    if (!nodeForm.name.trim() || !nodeForm.url.trim()) {
      setError('Vui lòng nhập Tên máy chủ và Địa chỉ URL.');
      return;
    }
    if (!nodeForm.id && !nodeForm.api_token.trim()) {
      setError('Vui lòng nhập API Token (bp_...) của máy chủ Peer.');
      return;
    }

    if (nodeForm.id) {
      const res = await request(
        `/multi-server/nodes/${nodeForm.id}`,
        {
          method: 'PUT',
          body: JSON.stringify(nodeForm),
        },
        'Đang cập nhật máy chủ Peer...'
      );
      if (res) {
        setShowNodeForm(false);
        await loadClusterStatus(true);
      }
    } else {
      const res = await request(
        '/multi-server/nodes',
        {
          method: 'POST',
          body: JSON.stringify(nodeForm),
        },
        'Đang kết nối và kiểm tra máy chủ Peer...'
      );
      if (res) {
        setShowNodeForm(false);
        await loadClusterStatus(true);
      }
    }
  }

  async function testNode(node) {
    const res = await request(
      `/multi-server/nodes/${node.id}/test`,
      { method: 'POST' },
      `Đang kiểm tra kết nối tới ${node.name}...`
    );
    await loadClusterStatus(true);
    if (res?.message) setNotice(res.message);
  }

  async function removeNode(node) {
    if (!confirm(`Gỡ bỏ máy chủ "${node.name}" (${node.url}) khỏi cụm Multi-Server?`)) return;
    const res = await request(
      `/multi-server/nodes/${node.id}`,
      { method: 'DELETE' },
      `Đang gỡ bỏ ${node.name}...`
    );
    if (res) {
      await loadClusterStatus(true);
    }
  }

  async function pingAllNodes() {
    const res = await request(
      '/multi-server/ping-all',
      { method: 'POST' },
      'Đang kiểm tra trạng thái toàn bộ các máy chủ trong cụm...'
    );
    if (res) {
      await loadClusterStatus(true);
    }
  }

  async function syncAllDns() {
    const res = await request(
      '/multi-server/sync-dns',
      {
        method: 'POST',
        body: JSON.stringify({ zone: null }),
      },
      'Đang đồng bộ toàn bộ DNS Zones sang các máy chủ Peer...'
    );
    if (res) {
      await loadClusterStatus(true);
    }
  }

  if (!isAdmin) {
    return (
      <section className="section">
        <h2>{t('Multi Server Setup')}</h2>
        <p className="hint">{t('Administrators only')}</p>
      </section>
    );
  }

  const localNode = clusterData?.local_node || {};
  const nodes = clusterData?.nodes || [];
  const remoteDbs = clusterData?.remote_db_hosts || [];
  const syncLogs = clusterData?.sync_logs || [];
  const summary = clusterData?.summary || { total_nodes: 0, online_nodes: 0, dns_sync_nodes: 0, remote_db_count: 0 };

  return (
    <div className="section multi-server-page" style={{ display: 'flex', flexDirection: 'column', gap: '20px' }}>
      {/* TOP HEADER */}
      <div className="pkg-card" style={{ padding: '20px 22px' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', flexWrap: 'wrap', gap: '14px', marginBottom: '18px' }}>
          <div>
            <h2 style={{ margin: 0, fontSize: '1.2rem', fontWeight: 800, color: 'var(--text-main)', display: 'flex', alignItems: 'center', gap: '10px' }}>
              <Server size={22} style={{ color: 'var(--accent-primary)' }} />
              Multi-Server Cluster & DNS Synchronization
            </h2>
            <p style={{ margin: '5px 0 0', fontSize: '0.85rem', color: 'var(--text-muted)' }}>
              Liên kết nhiều máy chủ SPanel thành một cụm thống nhất: Tự động đồng bộ DNS đa máy chủ (`ns1` / `ns2`), kiểm tra chống trùng lặp tên miền toàn cụm và giám sát tài nguyên tập trung.
            </p>
          </div>
          <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
            <button
              type="button"
              className="secondary-light"
              disabled={!!loading}
              onClick={generateLocalClusterToken}
              title="Tạo nhanh 1 khóa API Token của máy chủ này để dán sang máy chủ SPanel khác"
            >
              <KeyRound size={14} /> Tạo Token Liên Kết Máy Chủ Này
            </button>
            {nodes.length > 0 && (
              <>
                <button
                  type="button"
                  className="secondary-light"
                  disabled={!!loading}
                  onClick={pingAllNodes}
                >
                  <Wifi size={14} /> Ping Toàn Cụm
                </button>
                <button
                  type="button"
                  disabled={!!loading}
                  onClick={syncAllDns}
                >
                  <Zap size={14} /> Đồng Bộ DNS Ngay
                </button>
              </>
            )}
            <button
              type="button"
              className="secondary-light"
              disabled={!!loading}
              onClick={() => loadClusterStatus()}
            >
              <RefreshCw size={14} /> Làm mới
            </button>
          </div>
        </div>

        {/* GENERATED LOCAL TOKEN BANNER */}
        {generatedClusterToken && (
          <div style={{
            padding: '14px 16px',
            borderRadius: '10px',
            marginBottom: '16px',
            background: 'rgba(16, 185, 129, 0.1)',
            border: '1px solid rgba(16, 185, 129, 0.35)',
            display: 'flex',
            flexDirection: 'column',
            gap: '8px',
          }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '8px' }}>
              <strong style={{ fontSize: '0.86rem', color: '#10b981', display: 'flex', alignItems: 'center', gap: '6px' }}>
                <CheckCircle size={16} /> Khóa Cluster API Token của máy chủ này ({localNode.hostname || 'Local Node'}):
              </strong>
              <div style={{ display: 'flex', gap: '8px' }}>
                <button
                  type="button"
                  onClick={() => copyValue(generatedClusterToken, 'Đã sao chép Cluster API Token.')}
                >
                  <Copy size={13} /> Sao chép Token
                </button>
                <button
                  type="button"
                  className="secondary-light"
                  onClick={() => setGeneratedClusterToken('')}
                >
                  Đóng
                </button>
              </div>
            </div>
            <input
              readOnly
              value={generatedClusterToken}
              onFocus={(e) => e.target.select()}
              style={{ fontFamily: 'monospace', fontWeight: 700, fontSize: '0.85rem' }}
            />
            <span style={{ fontSize: '0.77rem', color: 'var(--text-muted)' }}>
              Dán mã Token này cùng địa chỉ <code>{window.location.origin}</code> vào mục <strong>Multi Server Setup</strong> trên máy chủ SPanel đối tác để liên kết 2 chiều.
            </span>
          </div>
        )}

        {/* KPI OVERVIEW CARDS */}
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(215px, 1fr))', gap: '12px' }}>
          <div style={{ padding: '14px 16px', borderRadius: '10px', background: 'var(--surface-2)', border: '1px solid var(--border-subtle)' }}>
            <div style={{ fontSize: '0.76rem', fontWeight: 700, color: 'var(--text-muted)', textTransform: 'uppercase' }}>
              Máy chủ hiện tại (Primary / Local)
            </div>
            <div style={{ fontSize: '1.05rem', fontWeight: 800, color: 'var(--text-main)', marginTop: '4px', display: 'flex', alignItems: 'center', gap: '6px' }}>
              <span style={{ width: '8px', height: '8px', borderRadius: '99px', background: '#10b981', display: 'inline-block' }} />
              {localNode.node_name || localNode.hostname || 'SPanel Node'}
            </div>
            <div style={{ fontSize: '0.77rem', color: 'var(--text-muted)', marginTop: '4px' }}>
              v{localNode.version || '1.0.7'} • {localNode.websites_count ?? 0} Websites • {localNode.dns_zones_count ?? 0} DNS Zones
            </div>
          </div>

          <div style={{ padding: '14px 16px', borderRadius: '10px', background: 'var(--surface-2)', border: '1px solid var(--border-subtle)' }}>
            <div style={{ fontSize: '0.76rem', fontWeight: 700, color: 'var(--text-muted)', textTransform: 'uppercase' }}>
              Máy chủ Peer trong cụm
            </div>
            <div style={{ fontSize: '1.15rem', fontWeight: 800, color: summary.online_nodes > 0 ? '#10b981' : 'var(--text-main)', marginTop: '4px' }}>
              {summary.online_nodes} / {summary.total_nodes} Online
            </div>
            <div style={{ fontSize: '0.77rem', color: 'var(--text-muted)', marginTop: '4px' }}>
              {summary.dns_sync_nodes} máy chủ đang nhận đồng bộ DNS
            </div>
          </div>

          <div style={{ padding: '14px 16px', borderRadius: '10px', background: 'var(--surface-2)', border: '1px solid var(--border-subtle)' }}>
            <div style={{ fontSize: '0.76rem', fontWeight: 700, color: 'var(--text-muted)', textTransform: 'uppercase' }}>
              Đồng bộ DNS Tự động (Zone Push)
            </div>
            <div style={{ fontSize: '1.05rem', fontWeight: 800, color: settingsForm.auto_dns_sync ? '#10b981' : '#f59e0b', marginTop: '4px' }}>
              {settingsForm.auto_dns_sync ? 'Đang bật (Real-Time)' : 'Chỉ đồng bộ thủ công'}
            </div>
            <div style={{ fontSize: '0.77rem', color: 'var(--text-muted)', marginTop: '4px' }}>
              Tự động đẩy bản ghi sang NS2/NS3 khi sửa DNS
            </div>
          </div>

          <div style={{ padding: '14px 16px', borderRadius: '10px', background: 'var(--surface-2)', border: '1px solid var(--border-subtle)' }}>
            <div style={{ fontSize: '0.76rem', fontWeight: 700, color: 'var(--text-muted)', textTransform: 'uppercase' }}>
              Kiểm tra Trùng Tên Miền Liên Máy Chủ
            </div>
            <div style={{ fontSize: '1.05rem', fontWeight: 800, color: settingsForm.check_domain_conflict ? 'var(--accent-primary)' : 'var(--text-muted)', marginTop: '4px' }}>
              {settingsForm.check_domain_conflict ? 'Đang bảo vệ (Active)' : 'Đang tắt'}
            </div>
            <div style={{ fontSize: '0.77rem', color: 'var(--text-muted)', marginTop: '4px' }}>
              Ngăn tạo trùng Domain đã tồn tại trên Node khác
            </div>
          </div>
        </div>
      </div>

      {/* TABS NAVIGATION */}
      <div className="segmented-control backup-tabs" role="tablist">
        <button
          type="button"
          role="tab"
          className={activeTab === 'nodes' ? 'active' : ''}
          onClick={() => setActiveTab('nodes')}
        >
          <Server size={14} /> Cụm Máy Chủ SPanel ({nodes.length})
        </button>
        <button
          type="button"
          role="tab"
          className={activeTab === 'remote_db' ? 'active' : ''}
          onClick={() => setActiveTab('remote_db')}
        >
          <Database size={14} /> Remote MySQL / MariaDB Nodes ({remoteDbs.length})
        </button>
        <button
          type="button"
          role="tab"
          className={activeTab === 'logs' ? 'active' : ''}
          onClick={() => setActiveTab('logs')}
        >
          <Clock size={14} /> Nhật Ký Đồng Bộ Cụm ({syncLogs.length})
        </button>
      </div>

      {/* TAB 1: PEER NODES & CLUSTER SETTINGS */}
      {activeTab === 'nodes' && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '20px' }}>
          {/* CLUSTER GLOBAL SETTINGS */}
          <div className="pkg-card" style={{ padding: '18px 22px' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '12px', marginBottom: '14px' }}>
              <div>
                <h3 style={{ margin: 0, fontSize: '1rem', fontWeight: 700, color: 'var(--text-main)' }}>
                  Quy tắc Vận hành Cụm Đa Máy Chủ (Cluster Rules)
                </h3>
                <p style={{ margin: '3px 0 0', fontSize: '0.8rem', color: 'var(--text-muted)' }}>
                  Thiết lập hành vi tự động đồng bộ DNS và kiểm tra xung đột tên miền/tài khoản giữa các máy chủ trong cụm.
                </p>
              </div>
              <button type="button" disabled={!!loading} onClick={saveClusterSettings}>
                <Save size={14} /> Lưu cấu hình cụm
              </button>
            </div>

            <div style={{ display: 'grid', gridTemplateColumns: 'minmax(220px, 1fr) repeat(auto-fit, minmax(220px, 1fr))', gap: '14px', alignItems: 'center' }}>
              <label style={{ margin: 0 }}>
                <span style={{ display: 'block', fontSize: '0.78rem', fontWeight: 600, color: 'var(--text-muted)', marginBottom: '5px' }}>
                  Tên định danh máy chủ hiện tại
                </span>
                <input
                  value={settingsForm.node_name}
                  onChange={(e) => setSettingsForm((prev) => ({ ...prev, node_name: e.target.value }))}
                  placeholder="VD: Primary Node HN-01"
                />
              </label>

              <label className="check-line" style={{ margin: 0 }}>
                <input
                  type="checkbox"
                  checked={!!settingsForm.enabled}
                  onChange={(e) => setSettingsForm((prev) => ({ ...prev, enabled: e.target.checked }))}
                />
                <span>Kích hoạt Multi-Server Cluster</span>
              </label>

              <label className="check-line" style={{ margin: 0 }}>
                <input
                  type="checkbox"
                  checked={!!settingsForm.auto_dns_sync}
                  onChange={(e) => setSettingsForm((prev) => ({ ...prev, auto_dns_sync: e.target.checked }))}
                />
                <span>Tự động đẩy DNS Zone khi thay đổi</span>
              </label>

              <label className="check-line" style={{ margin: 0 }}>
                <input
                  type="checkbox"
                  checked={!!settingsForm.check_domain_conflict}
                  onChange={(e) => setSettingsForm((prev) => ({ ...prev, check_domain_conflict: e.target.checked }))}
                />
                <span>Chặn tạo trùng Tên miền trên toàn cụm</span>
              </label>

              <label className="check-line" style={{ margin: 0 }}>
                <input
                  type="checkbox"
                  checked={!!settingsForm.check_username_conflict}
                  onChange={(e) => setSettingsForm((prev) => ({ ...prev, check_username_conflict: e.target.checked }))}
                />
                <span>Chặn tạo trùng Username trên toàn cụm</span>
              </label>
            </div>
          </div>

          {/* PEER NODES LIST & ADD FORM */}
          <div className="pkg-card" style={{ padding: '20px 22px' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '12px', marginBottom: '16px' }}>
              <div>
                <h3 style={{ margin: 0, fontSize: '1.02rem', fontWeight: 700, color: 'var(--text-main)' }}>
                  Danh sách Máy chủ SPanel Liên kết (Peer Fleet Nodes)
                </h3>
                <p style={{ margin: '3px 0 0', fontSize: '0.81rem', color: 'var(--text-muted)' }}>
                  Mỗi máy chủ Peer có thể đóng vai trò là máy chủ DNS dự phòng (`ns2`, `ns3`) hoặc máy chủ Hosting song song trong cụm.
                </p>
              </div>
              <button
                type="button"
                onClick={() => {
                  setNodeForm({
                    id: null,
                    name: '',
                    url: '',
                    api_token: '',
                    role: 'full_peer',
                    verify_tls: false,
                    sync_dns: true,
                    check_domain: true,
                    enabled: true,
                  });
                  setShowNodeForm(true);
                }}
              >
                <Plus size={15} /> Thêm Máy Chủ Peer
              </button>
            </div>

            {showNodeForm && (
              <form
                onSubmit={submitNodeForm}
                style={{
                  padding: '16px',
                  borderRadius: '10px',
                  background: 'var(--surface-2)',
                  border: '1px solid var(--border-subtle)',
                  marginBottom: '18px',
                  display: 'flex',
                  flexDirection: 'column',
                  gap: '14px',
                }}
              >
                <div style={{ fontWeight: 700, fontSize: '0.92rem', color: 'var(--text-main)' }}>
                  {nodeForm.id ? `Chỉnh sửa máy chủ Peer: ${nodeForm.name}` : 'Kết nối máy chủ SPanel Peer mới vào cụm'}
                </div>

                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(230px, 1fr))', gap: '12px' }}>
                  <label style={{ margin: 0 }}>
                    <span style={{ display: 'block', fontSize: '0.78rem', fontWeight: 600, color: 'var(--text-muted)', marginBottom: '5px' }}>
                      Tên gợi nhớ của Máy chủ Peer
                    </span>
                    <input
                      value={nodeForm.name}
                      onChange={(e) => setNodeForm({ ...nodeForm, name: e.target.value })}
                      placeholder="VD: NS2 Secondary Singapore / Node 02"
                      required
                    />
                  </label>

                  <label style={{ margin: 0 }}>
                    <span style={{ display: 'block', fontSize: '0.78rem', fontWeight: 600, color: 'var(--text-muted)', marginBottom: '5px' }}>
                      Địa chỉ Panel URL (Cổng 2222)
                    </span>
                    <input
                      value={nodeForm.url}
                      onChange={(e) => setNodeForm({ ...nodeForm, url: e.target.value })}
                      placeholder="https://node2.yourdomain.com:2222"
                      required
                    />
                  </label>

                  <label style={{ margin: 0 }}>
                    <span style={{ display: 'block', fontSize: '0.78rem', fontWeight: 600, color: 'var(--text-muted)', marginBottom: '5px' }}>
                      Khóa API Token trên máy chủ Peer (bp_...)
                    </span>
                    <input
                      type="password"
                      value={nodeForm.api_token}
                      onChange={(e) => setNodeForm({ ...nodeForm, api_token: e.target.value })}
                      placeholder={nodeForm.id ? 'Để trống nếu giữ nguyên API Token cũ' : 'Dán mã bp_... tạo từ máy chủ Peer'}
                    />
                  </label>

                  <label style={{ margin: 0 }}>
                    <span style={{ display: 'block', fontSize: '0.78rem', fontWeight: 600, color: 'var(--text-muted)', marginBottom: '5px' }}>
                      Vai trò trong cụm (Cluster Role)
                    </span>
                    <select
                      value={nodeForm.role}
                      onChange={(e) => setNodeForm({ ...nodeForm, role: e.target.value })}
                    >
                      <option value="full_peer">Full Cluster Peer (DNS + Kiểm tra Domain + Giám sát)</option>
                      <option value="dns_only">DNS Cluster Node (Chuyên đồng bộ Nameserver NS2/NS3)</option>
                    </select>
                  </label>
                </div>

                <div style={{ display: 'flex', flexWrap: 'wrap', gap: '18px', alignItems: 'center' }}>
                  <label className="check-line" style={{ margin: 0 }}>
                    <input
                      type="checkbox"
                      checked={!!nodeForm.sync_dns}
                      onChange={(e) => setNodeForm({ ...nodeForm, sync_dns: e.target.checked })}
                    />
                    <span>Đồng bộ các DNS Zone sang máy chủ này</span>
                  </label>

                  <label className="check-line" style={{ margin: 0 }}>
                    <input
                      type="checkbox"
                      checked={!!nodeForm.check_domain}
                      onChange={(e) => setNodeForm({ ...nodeForm, check_domain: e.target.checked })}
                    />
                    <span>Kiểm tra chống trùng Domain trên máy chủ này</span>
                  </label>

                  <label className="check-line" style={{ margin: 0 }}>
                    <input
                      type="checkbox"
                      checked={!!nodeForm.verify_tls}
                      onChange={(e) => setNodeForm({ ...nodeForm, verify_tls: e.target.checked })}
                    />
                    <span>Bắt buộc chứng chỉ SSL hợp lệ (Tắt nếu dùng SSL tự ký trên IP)</span>
                  </label>

                  <label className="check-line" style={{ margin: 0 }}>
                    <input
                      type="checkbox"
                      checked={!!nodeForm.enabled}
                      onChange={(e) => setNodeForm({ ...nodeForm, enabled: e.target.checked })}
                    />
                    <span>Kích hoạt Node</span>
                  </label>
                </div>

                <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '8px' }}>
                  <button type="button" className="secondary-light" onClick={() => setShowNodeForm(false)}>
                    Hủy
                  </button>
                  <button type="submit" disabled={!!loading}>
                    <Check size={15} /> {nodeForm.id ? 'Lưu cập nhật' : 'Kết nối & Kiểm tra Node'}
                  </button>
                </div>
              </form>
            )}

            <div className="pkg-table-wrap">
              <table className="pkg-clean-table">
                <thead>
                  <tr>
                    <th>Máy chủ Peer</th>
                    <th>Vai trò & Chức năng</th>
                    <th>Trạng thái & Độ trễ</th>
                    <th>Tài nguyên từ xa (CPU / RAM / Disk)</th>
                    <th>Quy mô trên Node</th>
                    <th style={{ textAlign: 'right' }}>Thao tác</th>
                  </tr>
                </thead>
                <tbody>
                  {nodes.map((node) => {
                    const isOnline = node.status === 'online';
                    const rInfo = node.remote_info || {};
                    return (
                      <tr key={node.id}>
                        <td>
                          <div style={{ fontWeight: 700, color: 'var(--text-main)' }}>{node.name}</div>
                          <code style={{ fontSize: '0.76rem', color: 'var(--accent-primary)' }}>{node.url}</code>
                          {rInfo.hostname && (
                            <div style={{ fontSize: '0.73rem', color: 'var(--text-muted)', marginTop: '2px' }}>
                              Host: {rInfo.hostname} {rInfo.version ? `• SPanel v${rInfo.version}` : ''}
                            </div>
                          )}
                        </td>
                        <td>
                          <div style={{ display: 'flex', flexDirection: 'column', gap: '4px', alignItems: 'flex-start' }}>
                            <span style={{
                              padding: '2px 8px',
                              borderRadius: '999px',
                              fontSize: '0.72rem',
                              fontWeight: 700,
                              background: 'rgba(59, 130, 246, 0.14)',
                              color: '#3b82f6',
                            }}>
                              {node.role === 'dns_only' ? 'DNS Cluster Node' : 'Full Cluster Peer'}
                            </span>
                            <div style={{ fontSize: '0.74rem', color: 'var(--text-muted)' }}>
                              DNS Sync: <strong>{node.sync_dns ? 'Bật' : 'Tắt'}</strong> • Conflict Guard: <strong>{node.check_domain ? 'Bật' : 'Tắt'}</strong>
                            </div>
                          </div>
                        </td>
                        <td>
                          <div style={{ display: 'flex', flexDirection: 'column', gap: '3px', alignItems: 'flex-start' }}>
                            <span style={{
                              padding: '3px 9px',
                              borderRadius: '999px',
                              fontSize: '0.74rem',
                              fontWeight: 700,
                              background: isOnline ? 'rgba(16, 185, 129, 0.14)' : 'rgba(239, 68, 68, 0.14)',
                              color: isOnline ? '#10b981' : '#ef4444',
                            }}>
                              {isOnline ? `Online (${node.latency_ms ?? 1}ms)` : (node.status === 'offline' ? 'Offline / Lỗi kết nối' : 'Chưa kiểm tra')}
                            </span>
                            {node.last_error && !isOnline && (
                              <span style={{ fontSize: '0.72rem', color: '#ef4444', maxWidth: '210px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={node.last_error}>
                                {node.last_error}
                              </span>
                            )}
                            {node.last_sync_at && (
                              <span style={{ fontSize: '0.71rem', color: 'var(--text-muted)' }}>
                                Sync: {new Date(node.last_sync_at).toLocaleTimeString()}
                              </span>
                            )}
                          </div>
                        </td>
                        <td>
                          {isOnline && rInfo.cpu_percent !== undefined ? (
                            <div style={{ fontSize: '0.79rem', display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
                              <span className="badge">CPU: {rInfo.cpu_percent}%</span>
                              <span className="badge">RAM: {rInfo.ram_percent}%</span>
                              <span className="badge">Disk: {rInfo.disk_percent}%</span>
                            </div>
                          ) : (
                            <span style={{ fontSize: '0.78rem', color: 'var(--text-muted)' }}>—</span>
                          )}
                        </td>
                        <td>
                          {isOnline && rInfo.websites_count !== undefined ? (
                            <div style={{ fontSize: '0.78rem', color: 'var(--text-main)' }}>
                              <strong>{rInfo.websites_count}</strong> Websites • <strong>{rInfo.dns_zones_count ?? 0}</strong> DNS Zones • <strong>{rInfo.users_count ?? 0}</strong> Users
                            </div>
                          ) : (
                            <span style={{ fontSize: '0.78rem', color: 'var(--text-muted)' }}>—</span>
                          )}
                        </td>
                        <td style={{ textAlign: 'right' }}>
                          <div style={{ display: 'inline-flex', gap: '6px', justifyContent: 'flex-end' }}>
                            <button
                              type="button"
                              className="secondary-light"
                              style={{ padding: '6px 10px', fontSize: '0.77rem' }}
                              disabled={!!loading}
                              onClick={() => testNode(node)}
                              title="Kiểm tra kết nối & cập nhật thông số Node"
                            >
                              <Wifi size={13} /> Ping
                            </button>
                            <button
                              type="button"
                              className="secondary-light"
                              style={{ padding: '6px 10px' }}
                              disabled={!!loading}
                              onClick={() => {
                                setNodeForm({
                                  id: node.id,
                                  name: node.name,
                                  url: node.url,
                                  api_token: '',
                                  role: node.role || 'full_peer',
                                  verify_tls: !!node.verify_tls,
                                  sync_dns: node.sync_dns !== false,
                                  check_domain: node.check_domain !== false,
                                  enabled: node.enabled !== false,
                                });
                                setShowNodeForm(true);
                              }}
                              title="Chỉnh sửa cấu hình Node"
                            >
                              <Edit2 size={14} />
                            </button>
                            <button
                              type="button"
                              className="danger"
                              style={{ padding: '6px 10px' }}
                              disabled={!!loading}
                              onClick={() => removeNode(node)}
                              title="Xóa Node khỏi cụm"
                            >
                              <Trash2 size={14} />
                            </button>
                          </div>
                        </td>
                      </tr>
                    );
                  })}
                  {nodes.length === 0 && (
                    <tr>
                      <td colSpan="6">
                        <EmptyState
                          icon={Server}
                          title="Chưa có máy chủ Peer nào trong cụm Multi-Server"
                          subtitle="Nhấn 'Thêm Máy Chủ Peer' ở góc phải để liên kết máy chủ SPanel thứ 2 làm Nameserver dự phòng (NS2) hoặc quản lý cụm tập trung."
                        />
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      )}

      {/* TAB 2: REMOTE DATABASE NODES */}
      {activeTab === 'remote_db' && (
        <RemoteDbHostsPanel request={request} hosts={remoteDbs} loading={loading} onChanged={() => loadClusterStatus(true)} />
      )}

      {/* TAB 3: SYNC LOGS */}
      {activeTab === 'logs' && (
        <div className="pkg-card" style={{ padding: '20px 22px' }}>
          <div style={{ marginBottom: '14px' }}>
            <h3 style={{ margin: 0, fontSize: '1.02rem', fontWeight: 700, color: 'var(--text-main)' }}>
              Nhật ký Hoạt động & Đồng bộ Cụm (Cluster Sync Audit Log)
            </h3>
            <p style={{ margin: '3px 0 0', fontSize: '0.81rem', color: 'var(--text-muted)' }}>
              Ghi nhận lịch sử đẩy DNS Zone, nhận đồng bộ từ máy chủ Peer và thay đổi cấu trúc cụm.
            </p>
          </div>
          <div className="pkg-table-wrap">
            <table className="pkg-clean-table">
              <thead>
                <tr>
                  <th>Thời gian</th>
                  <th>Hành động</th>
                  <th>Đối tượng</th>
                  <th>Trạng thái</th>
                  <th>Chi tiết</th>
                </tr>
              </thead>
              <tbody>
                {syncLogs.map((log) => (
                  <tr key={log.id}>
                    <td style={{ fontSize: '0.78rem', color: 'var(--text-muted)', whiteSpace: 'nowrap' }}>
                      {log.created_at ? new Date(log.created_at).toLocaleString() : '—'}
                    </td>
                    <td>
                      <code style={{ fontSize: '0.76rem', fontWeight: 700, color: 'var(--accent-primary)' }}>
                        {log.action}
                      </code>
                    </td>
                    <td>
                      <strong style={{ fontSize: '0.81rem' }}>{log.target}</strong>
                    </td>
                    <td>
                      <span style={{
                        padding: '2px 8px',
                        borderRadius: '999px',
                        fontSize: '0.72rem',
                        fontWeight: 700,
                        background: log.status === 'success' ? 'rgba(16, 185, 129, 0.14)' : log.status === 'warn' ? 'rgba(245, 158, 11, 0.14)' : 'rgba(59, 130, 246, 0.14)',
                        color: log.status === 'success' ? '#10b981' : log.status === 'warn' ? '#f59e0b' : '#3b82f6',
                      }}>
                        {log.status.toUpperCase()}
                      </span>
                    </td>
                    <td style={{ fontSize: '0.8rem', color: 'var(--text-muted)' }}>{log.detail}</td>
                  </tr>
                ))}
                {syncLogs.length === 0 && (
                  <tr>
                    <td colSpan="5" style={{ textAlign: 'center', padding: '24px', color: 'var(--text-muted)' }}>
                      Chưa có sự kiện đồng bộ cụm nào được ghi nhận.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}
