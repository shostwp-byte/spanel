import React, { useState, useEffect, useRef, useCallback } from 'react';
import ace from 'ace-builds/src-noconflict/ace';
import {
  Archive,
  ArchiveRestore,
  ArrowLeft,
  Check,
  ChevronDown,
  ChevronRight,
  Clock,
  Code2,
  Copy,
  Download,
  FilePlus,
  Files,
  FileText,
  FolderOpen,
  FolderPlus,
  Home,
  Link2,
  Lock,
  MoreVertical,
  MoveRight,
  Pencil,
  Plus,
  RefreshCw,
  RotateCcw,
  Save,
  Settings as SettingsIcon,
  Trash2,
  Upload,
  X,
  AlertCircle,
} from 'lucide-react';
import { useAppContext } from '../contexts/AppContext.jsx';
import { t } from '../i18n.js';
import { formatBytes } from '../utils/helpers.js';

const EDITOR_FONT_FAMILY = "Consolas, 'SFMono-Regular', 'Liberation Mono', Menlo, monospace";
const ACE_THEMES = { light: 'ace/theme/textmate', dark: 'ace/theme/tomorrow_night' };

function detectTheme() {
  const attr = document.documentElement.getAttribute('data-theme');
  if (attr === 'dark' || attr === 'light') return attr;
  return 'light';
}

function editorLanguageForPath(path = '') {
  const name = (path.split('/').pop() || '').toLowerCase();
  if (name.endsWith('.php') || name.endsWith('.phtml')) return 'PHP';
  if (/\.(js|jsx|mjs|cjs|ts|tsx)$/.test(name)) return 'JavaScript';
  if (/\.(css|scss|less)$/.test(name)) return 'CSS';
  if (/\.(html|htm|xml|svg)$/.test(name)) return 'HTML';
  if (/\.(json| lock)$/.test(name) || name.endsWith('.json')) return 'JSON';
  if (/\.(yml|yaml)$/.test(name)) return 'YAML';
  if (
    name === '.env' ||
    name.startsWith('.env.') ||
    name === '.htaccess' ||
    name === '.user.ini' ||
    /\.(ini|conf|cfg|cnf|env)$/.test(name)
  ) {
    return 'Config';
  }
  return 'Text';
}

function aceModeName(mode) {
  if (mode === 'PHP') return 'php';
  if (mode === 'JavaScript') return 'javascript';
  if (mode === 'CSS') return 'css';
  if (mode === 'HTML') return 'html';
  if (mode === 'JSON') return 'json';
  if (mode === 'YAML') return 'yaml';
  if (mode === 'Config') return 'ini';
  return 'text';
}

function InlineCodeEditor({ value, mode, disabled, wrapEnabled = false, onChange, onCursorChange, onSaveShortcut }) {
  const hostRef = useRef(null);
  const editorRef = useRef(null);
  const suppressChangeRef = useRef(false);
  const onChangeRef = useRef(onChange);
  const onCursorChangeRef = useRef(onCursorChange);
  const onSaveShortcutRef = useRef(onSaveShortcut);
  const [themeName, setThemeName] = useState(detectTheme);

  useEffect(() => { onChangeRef.current = onChange; }, [onChange]);
  useEffect(() => { onCursorChangeRef.current = onCursorChange; }, [onCursorChange]);
  useEffect(() => { onSaveShortcutRef.current = onSaveShortcut; }, [onSaveShortcut]);

  useEffect(() => {
    const handler = e => setThemeName(e.detail || detectTheme());
    document.addEventListener('spanel-theme-change', handler);
    return () => document.removeEventListener('spanel-theme-change', handler);
  }, []);

  useEffect(() => {
    editorRef.current?.setTheme(ACE_THEMES[themeName] || ACE_THEMES.light);
  }, [themeName]);

  useEffect(() => {
    if (!hostRef.current) return undefined;
    const editor = ace.edit(hostRef.current, {
      mode: `ace/mode/${aceModeName(mode)}`,
      theme: ACE_THEMES[themeName] || ACE_THEMES.light,
      value: value || '',
      readOnly: !!disabled,
      showPrintMargin: false,
      highlightActiveLine: true,
      fontSize: 13,
      tabSize: 2,
      useSoftTabs: true,
      wrap: !!wrapEnabled,
      selectionStyle: 'text',
    });

    editor.setOptions({
      enableBasicAutocompletion: true,
      enableLiveAutocompletion: true,
      enableMatchBrackets: true,
      enableSnippets: false,
      fontFamily: EDITOR_FONT_FAMILY,
    });
    editor.session.setUseWorker(false);
    editor.session.setNewLineMode('unix');

    editor.commands.addCommand({
      name: 'saveFileInline',
      bindKey: { win: 'Ctrl-S', mac: 'Command-S' },
      exec: () => {
        if (onSaveShortcutRef.current) onSaveShortcutRef.current();
      },
    });

    let destroyed = false;
    const reportCursor = () => {
      if (destroyed || !editorRef.current || !onCursorChangeRef.current) return;
      const pos = editorRef.current.getCursorPosition();
      onCursorChangeRef.current({ line: pos.row + 1, column: pos.column + 1 });
    };
    const handleChange = () => {
      if (destroyed || !editorRef.current) return;
      if (!suppressChangeRef.current && onChangeRef.current) {
        onChangeRef.current(editorRef.current.getValue());
      }
    };

    editor.session.on('change', handleChange);
    editor.selection.on('changeCursor', reportCursor);
    editorRef.current = editor;
    reportCursor();

    return () => {
      destroyed = true;
      editor.session.off('change', handleChange);
      editor.selection.off('changeCursor', reportCursor);
      editor.destroy();
      editorRef.current = null;
      if (hostRef.current) hostRef.current.textContent = '';
    };
  }, []);

  useEffect(() => {
    const editor = editorRef.current;
    if (!editor) return;
    const nextValue = value ?? '';
    if (nextValue === editor.getValue()) return;
    const cursor = editor.getCursorPosition();
    suppressChangeRef.current = true;
    editor.setValue(nextValue, -1);
    const newRow = Math.max(0, Math.min(cursor.row, editor.session.getLength() - 1));
    editor.moveCursorTo(newRow, cursor.column);
    suppressChangeRef.current = false;
  }, [value]);

  useEffect(() => {
    const editor = editorRef.current;
    if (!editor) return;
    editor.session.setMode(`ace/mode/${aceModeName(mode)}`);
  }, [mode]);

  useEffect(() => {
    const editor = editorRef.current;
    if (!editor) return;
    editor.session.setUseWrapMode(!!wrapEnabled);
  }, [wrapEnabled]);

  useEffect(() => {
    const editor = editorRef.current;
    if (!editor) return;
    editor.setReadOnly(!!disabled);
  }, [disabled]);

  return <div className="code-editor-host" ref={hostRef}></div>;
}

function TreeFolderIcon({ open = false }) {
  return (
    <svg className="da-folder-svg" viewBox="0 0 24 24" width="16" height="16" aria-hidden="true">
      {open ? (
        <path
          fill="#2b89e6"
          d="M20 6h-8l-2-2H4a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2z"
        />
      ) : (
        <path
          fill="#3592eb"
          d="M10 4H4a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-8l-2-2z"
        />
      )}
    </svg>
  );
}

function TableEntryIcon({ item }) {
  if (item?.is_dir) {
    return (
      <svg className="da-table-folder-svg" viewBox="0 0 24 24" width="22" height="22" aria-hidden="true">
        <path
          fill="#55a8f7"
          d="M10 4H4a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-8l-2-2z"
        />
        <path
          fill="#3b93ea"
          d="M2 10h20v8a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2v-8z"
        />
      </svg>
    );
  }
  const name = (item?.name || '').toLowerCase();
  let badge = 'FILE';
  let badgeColor = '#8c8c8c';
  if (name.endsWith('.php') || name.endsWith('.phtml')) {
    badge = 'PHP';
    badgeColor = '#6f74b5';
  } else if (name.endsWith('.html') || name.endsWith('.htm')) {
    badge = 'HTML';
    badgeColor = '#d97724';
  } else if (name.endsWith('.css') || name.endsWith('.scss')) {
    badge = 'CSS';
    badgeColor = '#2b89e6';
  } else if (name.endsWith('.js') || name.endsWith('.jsx') || name.endsWith('.ts') || name.endsWith('.tsx')) {
    badge = 'JS';
    badgeColor = '#d4a017';
  } else if (name.endsWith('.json')) {
    badge = 'JSON';
    badgeColor = '#5a8f5c';
  } else if (name.endsWith('.txt') || name.endsWith('.md') || name.endsWith('.log')) {
    badge = 'TXT';
    badgeColor = '#8a857b';
  } else if (name.endsWith('.zip') || name.endsWith('.tar.gz') || name.endsWith('.tgz') || name.endsWith('.gz')) {
    badge = 'ZIP';
    badgeColor = '#b0652b';
  } else if (name.endsWith('.sql')) {
    badge = 'SQL';
    badgeColor = '#0284c7';
  } else if (name.startsWith('.')) {
    badge = 'CFG';
    badgeColor = '#7d7a74';
  }
  return (
    <span className="da-table-file-icon" aria-hidden="true">
      <svg viewBox="0 0 24 28" width="20" height="24">
        <path fill="#dedad2" d="M3 1h12l6 6v19a1 1 0 0 1-1 1H3a1 1 0 0 1-1-1V2a1 1 0 0 1 1-1z" />
        <path fill="#c2bdb3" d="M15 1v6h6z" />
        <rect x="3" y="18" width="18" height="7" rx="1" fill={badgeColor} />
        <text x="12" y="23.3" textAnchor="middle" fill="#ffffff" fontSize="5.4" fontWeight="700" fontFamily="sans-serif">
          {badge}
        </text>
      </svg>
    </span>
  );
}

function suggestDuplicateName(name = '') {
  if (!name) return 'copy';
  const dotIdx = name.lastIndexOf('.');
  if (dotIdx > 0) {
    return `${name.slice(0, dotIdx)}-copy${name.slice(dotIdx)}`;
  }
  return `${name}-copy`;
}

export function FilesPage({ request, API }) {
  const {
    loading,
    setLoading,
    setError,
    setNotice,
    fileAppId,
    setFileAppId,
    filePath,
    setFilePath,
    fileListPath,
    setFileListPath,
    websites,
    selectedWebsiteId,
    setSelectedWebsiteId,
    siteApps,
    currentUser,
    isAdmin,
    storageUsageText,
    loadCurrentUser,
    readCookie,
    upsertFileJob,
    dismissFileJob,
    formatFileTime,
    fileJobs,
    setFileJobs,
  } = useAppContext();

  const currentSite = websites.find(site => String(site.id) === String(selectedWebsiteId));
  const currentFileApp = () => siteApps?.items?.find(app => String(app.id) === String(fileAppId)) || null;

  const [fileUploadDir, setFileUploadDir] = useState('public_html');
  const [files, setFiles] = useState([]);
  const [archiveFormat, setArchiveFormat] = useState('zip');
  const [selectedFilePaths, setSelectedFilePaths] = useState([]);
  const [virtualView, setVirtualView] = useState(() => (selectedWebsiteId || fileAppId ? 'target' : 'domains'));
  const [userNavigatedVirtual, setUserNavigatedVirtual] = useState(false);

  // In-page File Editor state (replaces opening a new browser tab)
  const [editingFilePath, setEditingFilePath] = useState('');
  const [fileContent, setFileContent] = useState('');
  const [originalFileContent, setOriginalFileContent] = useState('');
  const [editorCursor, setEditorCursor] = useState({ line: 1, column: 1 });
  const [editorWrap, setEditorWrap] = useState(false);

  // Left Directory Tree state
  const [treeExpanded, setTreeExpanded] = useState({
    'root:domains': true,
    'root:applications': true,
  });
  const [treeDirs, setTreeDirs] = useState({});
  const [treeLoading, setTreeLoading] = useState({});

  // Context Menu Dialog (floating right-click / row menu)
  const [contextMenu, setContextMenu] = useState(null);

  // Clean Action Modal Dialogs (mkdir, create, rename, duplicate, copy, move, archive, extract, delete)
  const [actionDialog, setActionDialog] = useState(null);

  // Permissions (chmod) modal state
  const [chmodTarget, setChmodTarget] = useState(null);
  const [chmodMode, setChmodMode] = useState('644');

  const uploadInputRef = useRef(null);

  function formatApiError(detail, fallback = 'Request failed.') {
    if (!detail) return fallback;
    if (typeof detail === 'string') return detail;
    if (typeof detail === 'object') {
      if (detail.detail !== undefined) return typeof detail.detail === 'string' ? detail.detail : fallback;
      if (detail.message !== undefined) return typeof detail.message === 'string' ? detail.message : fallback;
    }
    return fallback;
  }

  function handleAuthExpired(status) {
    if (status === 401) {
      window.location.reload();
      return true;
    }
    return false;
  }

  function hasFileTarget() {
    return virtualView === 'target' && !!(currentFileApp() || currentSite);
  }

  function fileTargetBody() {
    return fileAppId ? { app_id: Number(fileAppId) } : { website_id: Number(selectedWebsiteId) };
  }

  function fileTargetBase(overrideSiteId = null, overrideAppId = null) {
    const effAppId = overrideAppId !== null ? overrideAppId : fileAppId;
    const effSiteId = overrideSiteId !== null ? overrideSiteId : selectedWebsiteId;
    return effAppId ? `/maintenance/app-files/${effAppId}` : `/maintenance/files/${effSiteId}`;
  }

  function fileTargetKey(overrideSiteId = null, overrideAppId = null) {
    const effAppId = overrideAppId !== null ? overrideAppId : fileAppId;
    const effSiteId = overrideSiteId !== null ? overrideSiteId : selectedWebsiteId;
    return effAppId ? `app:${effAppId}` : effSiteId ? `site:${effSiteId}` : '';
  }

  function parentFilePath(path) {
    const parts = String(path || '').split('/').filter(Boolean);
    parts.pop();
    return parts.join('/');
  }

  function fileBreadcrumbs(path) {
    const parts = String(path || '').split('/').filter(Boolean);
    let current = '';
    return parts.map(part => {
      current = current ? `${current}/${part}` : part;
      return { label: part, path: current };
    });
  }

  function isTextEditable(item) {
    if (!item || item.is_dir) return false;
    const name = (item.name || '').toLowerCase();
    const editableDotfiles = new Set([
      '.env',
      '.env.example',
      '.env.local',
      '.env.production',
      '.htaccess',
      '.htpasswd',
      '.user.ini',
      '.gitignore',
      '.gitattributes',
      '.editorconfig',
    ]);
    return (
      editableDotfiles.has(name) ||
      /\.(txt|md|json|css|scss|less|js|jsx|mjs|cjs|ts|tsx|html|htm|xml|svg|yml|yaml|ini|conf|cfg|cnf|log|php|phtml|env|htaccess|sql|sh|py|rb|toml)$/.test(name) ||
      !name.includes('.')
    );
  }

  function isArchiveFile(item) {
    if (!item || item.is_dir) return false;
    const name = (item.name || '').toLowerCase();
    return name.endsWith('.zip') || name.endsWith('.tar.gz') || name.endsWith('.tgz');
  }

  // Silently fetch subdirectories for the left folder tree
  const fetchTreeSubdirs = useCallback(
    async (targetPrefix, baseApi, dirPath = '') => {
      const nodeKey = `${targetPrefix}:${dirPath}`;
      setTreeLoading(prev => ({ ...prev, [nodeKey]: true }));
      try {
        const res = await fetch(`${API}${baseApi}?path=${encodeURIComponent(dirPath)}`, {
          credentials: 'include',
        });
        if (!res.ok) return;
        const data = await res.json().catch(() => ({}));
        if (Array.isArray(data?.items)) {
          const dirs = data.items
            .filter(item => item.is_dir)
            .sort((a, b) => a.name.localeCompare(b.name));
          setTreeDirs(prev => ({ ...prev, [nodeKey]: dirs }));
        }
      } catch {
        // Ignore silent tree fetch errors
      } finally {
        setTreeLoading(prev => ({ ...prev, [nodeKey]: false }));
      }
    },
    [API]
  );

  // Expand tree path ancestors when navigating
  const expandTreeAncestors = useCallback((targetPrefix, baseApi, path = '') => {
    if (!targetPrefix) return;
    const isApp = targetPrefix.startsWith('app:');
    const nextExpanded = {
      [isApp ? 'root:applications' : 'root:domains']: true,
      [`${targetPrefix}:`]: true,
    };
    const parts = String(path || '').split('/').filter(Boolean);
    let current = '';
    for (const part of parts) {
      current = current ? `${current}/${part}` : part;
      nextExpanded[`${targetPrefix}:${current}`] = true;
    }
    setTreeExpanded(prev => ({ ...prev, ...nextExpanded }));
  }, []);

  function closeInlineEditorIfAllowed() {
    if (editingFilePath && fileContent !== originalFileContent) {
      if (!window.confirm(t('You have unsaved changes. Discard them and leave the editor?'))) {
        return false;
      }
    }
    setEditingFilePath('');
    return true;
  }

  function openVirtualHome() {
    if (!closeInlineEditorIfAllowed()) return;
    setUserNavigatedVirtual(true);
    setVirtualView('home');
    setSelectedFilePaths([]);
  }

  function openVirtualDomains() {
    if (!closeInlineEditorIfAllowed()) return;
    setUserNavigatedVirtual(true);
    setVirtualView('domains');
    setSelectedFilePaths([]);
    setTreeExpanded(prev => ({ ...prev, 'root:domains': true }));
  }

  function openVirtualApplications() {
    if (!closeInlineEditorIfAllowed()) return;
    setUserNavigatedVirtual(true);
    setVirtualView('applications');
    setSelectedFilePaths([]);
    setTreeExpanded(prev => ({ ...prev, 'root:applications': true }));
  }

  function openSiteFolder(site, initialPath = 'public_html') {
    if (!site) return;
    if (!closeInlineEditorIfAllowed()) return;
    const sameTarget = !fileAppId && String(selectedWebsiteId) === String(site.id) && virtualView === 'target';
    setUserNavigatedVirtual(false);
    setFileAppId('');
    setSelectedWebsiteId(String(site.id));
    setFileListPath(initialPath);
    setFileUploadDir(initialPath || '');
    setSelectedFilePaths([]);
    setVirtualView('target');
    const prefix = `site:${site.id}`;
    const base = `/maintenance/files/${site.id}`;
    expandTreeAncestors(prefix, base, initialPath);
    if (!treeDirs[`${prefix}:`]) {
      fetchTreeSubdirs(prefix, base, '');
    }
    if (sameTarget) {
      listFiles(initialPath, site.id, '');
    }
  }

  function openAppFolder(app, initialPath = '') {
    if (!app) return;
    if (!closeInlineEditorIfAllowed()) return;
    const sameTarget = String(fileAppId) === String(app.id) && virtualView === 'target';
    setUserNavigatedVirtual(false);
    setFileAppId(String(app.id));
    setFileListPath(initialPath);
    setFileUploadDir(initialPath || '');
    setSelectedFilePaths([]);
    setVirtualView('target');
    const prefix = `app:${app.id}`;
    const base = `/maintenance/app-files/${app.id}`;
    expandTreeAncestors(prefix, base, initialPath);
    if (!treeDirs[`${prefix}:`]) {
      fetchTreeSubdirs(prefix, base, '');
    }
    if (sameTarget) {
      listFiles(initialPath, '', app.id);
    }
  }

  function toggleTreeExpand(nodeKey, prefix = null, baseApi = null, dirPath = '') {
    const nextOpen = !treeExpanded[nodeKey];
    setTreeExpanded(prev => ({ ...prev, [nodeKey]: nextOpen }));
    if (nextOpen && prefix && baseApi && !treeDirs[nodeKey]) {
      fetchTreeSubdirs(prefix, baseApi, dirPath);
    }
  }

  // Close floating context menu on outside click, Escape, or scroll
  useEffect(() => {
    if (!contextMenu) return undefined;
    const handleOutside = () => setContextMenu(null);
    const handleKeyDown = e => {
      if (e.key === 'Escape') setContextMenu(null);
    };
    window.addEventListener('click', handleOutside);
    window.addEventListener('keydown', handleKeyDown);
    window.addEventListener('scroll', handleOutside, true);
    return () => {
      window.removeEventListener('click', handleOutside);
      window.removeEventListener('keydown', handleKeyDown);
      window.removeEventListener('scroll', handleOutside, true);
    };
  }, [contextMenu]);

  function openRowContextMenu(e, item) {
    e.preventDefault();
    e.stopPropagation();
    if (!item) return;
    if (!selectedFilePaths.includes(item.path)) {
      setSelectedFilePaths([item.path]);
    }
    const menuWidth = 205;
    const menuHeight = item.is_dir ? 310 : 350;
    const x = Math.max(12, Math.min(e.clientX, window.innerWidth - menuWidth - 12));
    const y = Math.max(12, Math.min(e.clientY, window.innerHeight - menuHeight - 12));
    setContextMenu({ x, y, item });
  }

  function toggleFileSelection(path) {
    setSelectedFilePaths(prev => (prev.includes(path) ? prev.filter(item => item !== path) : [...prev, path]));
  }

  function toggleAllFiles() {
    setSelectedFilePaths(prev => (prev.length === files.length ? [] : files.map(item => item.path)));
  }

  // In-page File Editor functions (replaces opening a new browser tab)
  async function openInlineEditor(path) {
    if (!hasFileTarget() || !path) return;
    const data = await request(
      `${fileTargetBase()}/read?path=${encodeURIComponent(path)}`,
      {},
      t('Reading file...')
    );
    if (data?.content !== undefined) {
      setFilePath(path);
      setEditingFilePath(path);
      setFileContent(data.content);
      setOriginalFileContent(data.content);
      setEditorCursor({ line: 1, column: 1 });
    }
  }

  // Keep alias for any legacy caller, opening in-page instead of a new tab
  function openFileEditorTab(path) {
    openInlineEditor(path);
  }

  async function reloadInlineFile() {
    if (!editingFilePath) return;
    const data = await request(
      `${fileTargetBase()}/read?path=${encodeURIComponent(editingFilePath)}`,
      {},
      t('Reloading file...')
    );
    if (data?.content !== undefined) {
      setFileContent(data.content);
      setOriginalFileContent(data.content);
      setNotice(t('Reloaded {name}.', { name: editingFilePath.split('/').pop() }));
    }
  }

  async function saveInlineFile() {
    if (!editingFilePath || !hasFileTarget()) return;
    const data = await request(
      '/maintenance/files/write',
      {
        method: 'POST',
        body: JSON.stringify({ ...fileTargetBody(), path: editingFilePath, content: fileContent }),
      },
      t('Saving file...')
    );
    if (data) {
      setOriginalFileContent(fileContent);
      setNotice(t('Saved {name}.', { name: editingFilePath.split('/').pop() }));
      await listFiles(fileListPath);
      await loadCurrentUser();
    }
  }

  // Permissions (chmod) helpers
  const PERMISSION_BITS = [
    { key: 'read', label: 'Read', value: 4 },
    { key: 'write', label: 'Write', value: 2 },
    { key: 'execute', label: 'Execute', value: 1 },
  ];

  const PERMISSION_PRESETS = {
    file: [
      ['644', 'Default'],
      ['755', 'Executable'],
      ['600', 'Private'],
      ['444', 'Read-only'],
    ],
    dir: [
      ['755', 'Default'],
      ['750', 'Group read'],
      ['775', 'Group write'],
      ['700', 'Private'],
    ],
  };

  function normalizeOctalMode(mode) {
    const value = String(mode ?? '').trim();
    return /^[0-7]{3,4}$/.test(value) ? value : '';
  }

  function octalToPermissionBits(mode) {
    const padded = (normalizeOctalMode(mode) || '0644').padStart(4, '0');
    return {
      special: Number(padded[0]),
      owner: Number(padded[1]),
      group: Number(padded[2]),
      other: Number(padded[3]),
    };
  }

  function permissionBitsToOctal({ special, owner, group, other }) {
    const body = `${owner}${group}${other}`;
    return special ? `${special}${body}` : body;
  }

  function permissionSymbols(mode, isDir = false) {
    const fallback = isDir ? '0755' : '0644';
    const padded = (normalizeOctalMode(mode) || fallback).padStart(4, '0');
    const bits = octalToPermissionBits(padded);
    const renderClass = (classBits, isOwner, isGroup) => {
      const r = classBits & 4 ? 'r' : '-';
      const w = classBits & 2 ? 'w' : '-';
      let x = classBits & 1 ? 'x' : '-';
      if (isOwner && (bits.special & 4)) x = classBits & 1 ? 's' : 'S';
      if (isGroup && (bits.special & 2)) x = classBits & 1 ? 's' : 'S';
      if (!isOwner && !isGroup && (bits.special & 1)) x = classBits & 1 ? 't' : 'T';
      return `${r}${w}${x}`;
    };
    return `${isDir ? 'd' : '-'}${renderClass(bits.owner, true, false)}${renderClass(bits.group, false, true)}${renderClass(bits.other, false, false)}`;
  }

  function openChmodDialog(items) {
    const targets = (Array.isArray(items) ? items : [items]).filter(Boolean);
    if (targets.length === 0) return;
    setChmodTarget(targets);
    if (targets.length === 1 && normalizeOctalMode(targets[0].mode)) {
      setChmodMode(normalizeOctalMode(targets[0].mode));
      return;
    }
    const specials = targets.map(item => octalToPermissionBits(item.mode).special);
    const special = specials.every(value => value === specials[0]) ? specials[0] : 0;
    const base = targets.every(item => item.is_dir)
      ? { owner: 7, group: 5, other: 5 }
      : { special: 0, owner: 6, group: 4, other: 4 };
    setChmodMode(permissionBitsToOctal({ special, ...base }));
  }

  async function applyChmod() {
    const targets = chmodTarget || [];
    const mode = chmodMode.trim();
    if (targets.length === 0) return;
    if (!/^[0-7]{3,4}$/.test(mode)) {
      setError(t('Mode must be octal, for example 644 or 755.'));
      return;
    }
    for (const item of targets) {
      const data = await request(
        '/maintenance/files/chmod',
        {
          method: 'POST',
          body: JSON.stringify({ ...fileTargetBody(), path: item.path, mode }),
        },
        `Setting permissions on ${item.name}...`
      );
      if (!data) return;
    }
    setChmodTarget(null);
    setNotice(t('Permissions set to {mode} on {n} item(s).', { mode, n: targets.length }));
    await listFiles(fileListPath);
  }

  function renderChmodDialog() {
    const targets = chmodTarget || [];
    if (targets.length === 0) return null;
    const bits = octalToPermissionBits(chmodMode);
    const onlyDirs = targets.every(item => item.is_dir);
    const hasFiles = targets.some(item => !item.is_dir);
    const worldWritable = !!(bits.other & 2);
    const setBit = (classKey, bitValue) =>
      setChmodMode(
        permissionBitsToOctal({
          ...bits,
          [classKey]: bits[classKey] ^ bitValue,
        })
      );
    const title = targets.length === 1 ? targets[0].name : `${targets.length} selected items`;
    return (
      <div className="chmod-backdrop" role="presentation" onClick={() => setChmodTarget(null)}>
        <div
          className="chmod-dialog"
          role="dialog"
          aria-modal="true"
          aria-label={t('Set Permissions')}
          onClick={e => e.stopPropagation()}
        >
          <div className="chmod-head">
            <div>
              <h3>
                <Lock size={15} />
                {t('Set Permissions')}
              </h3>
              <p>{title}</p>
            </div>
            <button className="mini secondary-light" onClick={() => setChmodTarget(null)} aria-label={t('Close')}>
              <X size={14} />
            </button>
          </div>
          <table className="chmod-grid">
            <thead>
              <tr>
                <th scope="col"></th>
                {PERMISSION_BITS.map(bit => (
                  <th scope="col" key={bit.key}>
                    {bit.label}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {[
                { key: 'owner', label: 'Owner' },
                { key: 'group', label: 'Group' },
                { key: 'other', label: 'Public' },
              ].map(cls => (
                <tr key={cls.key}>
                  <th scope="row">{cls.label}</th>
                  {PERMISSION_BITS.map(bit => (
                    <td key={bit.key}>
                      <input
                        type="checkbox"
                        checked={!!(bits[cls.key] & bit.value)}
                        onChange={() => setBit(cls.key, bit.value)}
                        aria-label={`${cls.label} ${bit.label}`}
                      />
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
          <div className="chmod-value">
            <label>
              <span>{t('Octal')}</span>
              <input
                value={chmodMode}
                inputMode="numeric"
                maxLength={4}
                onChange={e => setChmodMode(e.target.value.replace(/[^0-7]/g, '').slice(0, 4))}
              />
            </label>
            <code>{permissionSymbols(chmodMode, onlyDirs)}</code>
          </div>
          <div className="chmod-presets">
            {(onlyDirs ? PERMISSION_PRESETS.dir : PERMISSION_PRESETS.file).map(([preset, label]) => (
              <button
                key={preset}
                type="button"
                className={`mini ${chmodMode === preset ? '' : 'secondary-light'}`}
                onClick={() => setChmodMode(preset)}
              >
                {preset} <small>{t(label)}</small>
              </button>
            ))}
          </div>
          {onlyDirs && (
            <label className="chmod-setgid">
              <input
                type="checkbox"
                checked={bits.special === 2}
                onChange={() => setChmodMode(permissionBitsToOctal({ ...bits, special: bits.special === 2 ? 0 : 2 }))}
              />
              <span>
                {t(
                  "Setgid — new files inside keep the folder's group. spanel sets this on site folders; leave it on unless you know otherwise."
                )}
              </span>
            </label>
          )}
          {worldWritable && (
            <p className="chmod-note warn">
              <AlertCircle size={13} />{' '}
              {hasFiles
                ? t(
                    'World-writable: anyone with an account on the server can change these files. Use 755 unless something really needs it.'
                  )
                : t(
                    'World-writable: anyone with an account on the server can change what is inside these folders. Use 755 unless something really needs it.'
                  )}
            </p>
          )}
          <div className="chmod-actions">
            <button className="secondary-light" disabled={!!loading} onClick={() => setChmodTarget(null)}>
              {t('Cancel')}
            </button>
            <button disabled={!!loading} onClick={applyChmod}>
              <Check size={14} /> {t('Save')} ({chmodMode})
            </button>
          </div>
        </div>
      </div>
    );
  }

  useEffect(() => {
    if (hasFileTarget()) loadFileJobs();
  }, []);

  useEffect(() => {
    if (fileJobs.length > 0 && !loading) {
      const running = fileJobs.filter(j => j.status === 'running');
      if (running.length > 0) {
        const timer = setTimeout(() => {
          loadFileJobs();
        }, 2000);
        return () => clearTimeout(timer);
      }
    }
  }, [fileJobs, loading]);

  useEffect(() => {
    const activeApp = currentFileApp();
    if (activeApp || currentSite) {
      if (virtualView === 'target') {
        listFiles(fileListPath);
        const prefix = activeApp ? `app:${activeApp.id}` : `site:${currentSite.id}`;
        const base = activeApp ? `/maintenance/app-files/${activeApp.id}` : `/maintenance/files/${currentSite.id}`;
        expandTreeAncestors(prefix, base, fileListPath);
        if (!treeDirs[`${prefix}:`]) {
          fetchTreeSubdirs(prefix, base, '');
        }
      }
    } else if (!userNavigatedVirtual && websites.length >= 1 && !(siteApps?.items?.length > 0)) {
      setSelectedWebsiteId(String(websites[0].id));
      setFileListPath('public_html');
      setVirtualView('target');
    } else if (virtualView === 'target') {
      setVirtualView('domains');
    }
  }, [selectedWebsiteId, fileAppId, websites.length, Boolean(currentSite)]);

  async function listFiles(path = fileListPath, overrideSiteId = null, overrideAppId = null) {
    const effAppId = overrideAppId !== null ? overrideAppId : fileAppId;
    const effSiteId = overrideSiteId !== null ? overrideSiteId : selectedWebsiteId;
    if (!effAppId && !effSiteId) return;
    const base = effAppId ? `/maintenance/app-files/${effAppId}` : `/maintenance/files/${effSiteId}`;
    const prefix = effAppId ? `app:${effAppId}` : `site:${effSiteId}`;
    const data = await request(`${base}?path=${encodeURIComponent(path)}`, {}, t('Loading file list...'));
    if (data?.items) {
      setFiles(data.items);
      setFileListPath(path);
      setFileUploadDir(path || '');
      setSelectedFilePaths([]);
      // Sync directories into the left tree cache
      const subDirs = data.items
        .filter(item => item.is_dir)
        .sort((a, b) => a.name.localeCompare(b.name));
      setTreeDirs(prev => ({ ...prev, [`${prefix}:${path || ''}`]: subDirs }));
      expandTreeAncestors(prefix, base, path || '');
    }
  }

  async function downloadFile(path) {
    if (!hasFileTarget() || !path) return;
    try {
      setError('');
      setLoading('Downloading file...');
      const res = await fetch(`${API}${fileTargetBase()}/download?path=${encodeURIComponent(path)}`, {
        credentials: 'include',
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        if (handleAuthExpired(res.status, data.detail)) return;
        setError(formatApiError(data.detail, 'Download failed.'));
        return;
      }
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = path.split('/').pop() || 'download';
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
    } catch {
      setError(t('File download failed.'));
    } finally {
      setLoading('');
    }
  }

  // Modal Dialog openers for all file/folder operations
  function openNewFolderModal() {
    if (!hasFileTarget()) return;
    setActionDialog({
      type: 'mkdir',
      title: t('Create New Folder'),
      value: '',
      placeholder: 'new-folder',
    });
  }

  function openNewFileModal() {
    if (!hasFileTarget()) return;
    setActionDialog({
      type: 'create',
      title: t('Create New File'),
      value: 'index.php',
      placeholder: 'filename.php',
      openAfterCreate: true,
    });
  }

  function openRenameModal(item) {
    if (!item || !hasFileTarget()) return;
    setActionDialog({
      type: 'rename',
      title: item.is_dir ? t('Rename Folder') : t('Rename File'),
      item,
      value: item.name,
      placeholder: item.name,
    });
  }

  function openDuplicateModal(item) {
    if (!item || !hasFileTarget()) return;
    setActionDialog({
      type: 'duplicate',
      title: item.is_dir ? t('Duplicate Folder') : t('Duplicate File'),
      item,
      value: suggestDuplicateName(item.name),
      placeholder: suggestDuplicateName(item.name),
    });
  }

  function openTransferModal(mode, paths) {
    if (!hasFileTarget() || !paths?.length) return;
    setActionDialog({
      type: mode, // 'copy' | 'move'
      title: mode === 'copy' ? t('Copy Items') : t('Move Items'),
      paths,
      value: fileListPath || 'public_html',
      placeholder: 'public_html',
    });
  }

  function openArchiveModal(paths) {
    if (!hasFileTarget() || !paths?.length) return;
    const ext = archiveFormat === 'tar.gz' ? 'tar.gz' : 'zip';
    const baseName =
      paths.length === 1
        ? (paths[0].split('/').pop() || 'archive').replace(/\.(zip|tar\.gz|tgz)$/i, '')
        : `archive-${new Date().toISOString().slice(0, 10)}`;
    setActionDialog({
      type: 'archive',
      title: t('Archive Selected Items'),
      paths,
      format: archiveFormat,
      value: `${baseName}.${ext}`,
      placeholder: `archive.${ext}`,
    });
  }

  function openExtractModal(item) {
    if (!item || !hasFileTarget()) return;
    setActionDialog({
      type: 'extract',
      title: t('Extract Archive'),
      item,
      value: fileListPath || '.',
      placeholder: fileListPath || 'public_html',
    });
  }

  function openDeleteModal(paths) {
    if (!hasFileTarget() || !paths?.length) return;
    setActionDialog({
      type: 'delete',
      title: t('Remove Selected Items'),
      paths,
    });
  }

  async function submitActionDialog(e) {
    if (e) e.preventDefault();
    if (!actionDialog) return;
    const { type } = actionDialog;
    const val = String(actionDialog.value || '').trim();

    if (type === 'mkdir') {
      if (!val) return;
      const data = await request(
        '/maintenance/files/mkdir',
        { method: 'POST', body: JSON.stringify({ ...fileTargetBody(), path: fileListPath || '', name: val }) },
        t('Creating folder...')
      );
      if (data) {
        setActionDialog(null);
        setNotice(t('Created folder {name}.', { name: val }));
        await listFiles(fileListPath);
      }
      return;
    }

    if (type === 'create') {
      if (!val) return;
      const openAfter = !!actionDialog.openAfterCreate;
      const data = await request(
        '/maintenance/files/create',
        { method: 'POST', body: JSON.stringify({ ...fileTargetBody(), path: fileListPath || '', name: val }) },
        t('Creating file...')
      );
      if (data) {
        setActionDialog(null);
        await listFiles(fileListPath);
        const newPath = [fileListPath, val].filter(Boolean).join('/');
        if (openAfter) {
          await openInlineEditor(newPath);
        }
      }
      return;
    }

    if (type === 'rename') {
      const { item } = actionDialog;
      if (!item || !val || val === item.name) {
        setActionDialog(null);
        return;
      }
      const data = await request(
        '/maintenance/files/rename',
        { method: 'POST', body: JSON.stringify({ ...fileTargetBody(), path: item.path, new_name: val }) },
        t('Renaming...')
      );
      if (data) {
        setActionDialog(null);
        setNotice(t('Renamed {old} to {new}.', { old: item.name, new: val }));
        await listFiles(fileListPath);
      }
      return;
    }

    if (type === 'duplicate') {
      const { item } = actionDialog;
      if (!item || !val || val === item.name) return;
      const data = await request(
        '/maintenance/files/duplicate',
        { method: 'POST', body: JSON.stringify({ ...fileTargetBody(), path: item.path, new_name: val }) },
        t('Duplicating...')
      );
      if (data) {
        setActionDialog(null);
        setNotice(t('Duplicated {old} as {new}.', { old: item.name, new: val }));
        await listFiles(fileListPath);
        await loadCurrentUser();
      }
      return;
    }

    if (type === 'copy' || type === 'move') {
      const targetPath = val === '.' || val === '/' ? '' : val;
      const verb = type === 'copy' ? 'Copying' : 'Moving';
      const data = await request(
        `/maintenance/files/${type}`,
        {
          method: 'POST',
          body: JSON.stringify({
            ...fileTargetBody(),
            paths: actionDialog.paths,
            destination_path: targetPath,
          }),
        },
        `${verb} items...`
      );
      if (data) {
        setActionDialog(null);
        setNotice(
          type === 'copy'
            ? t('Copied {n} item(s) to /{dest}.', { n: actionDialog.paths.length, dest: targetPath })
            : t('Moved {n} item(s) to /{dest}.', { n: actionDialog.paths.length, dest: targetPath })
        );
        await listFiles(fileListPath);
        await loadCurrentUser();
      }
      return;
    }

    if (type === 'archive') {
      if (!val) return;
      const fmt = actionDialog.format || 'zip';
      setArchiveFormat(fmt);
      const data = await request(
        '/maintenance/files/archive',
        {
          method: 'POST',
          body: JSON.stringify({
            ...fileTargetBody(),
            base_path: fileListPath || '',
            paths: actionDialog.paths,
            output_name: val,
            format: fmt,
          }),
        },
        t('Creating archive...')
      );
      if (data) {
        setActionDialog(null);
        setNotice(t('Created archive {name}.', { name: val }));
        await listFiles(fileListPath);
        await loadCurrentUser();
      }
      return;
    }

    if (type === 'extract') {
      const { item } = actionDialog;
      if (!item) return;
      const targetPath = val || fileListPath || '.';
      const data = await request(
        '/maintenance/files/extract',
        {
          method: 'POST',
          body: JSON.stringify({
            ...fileTargetBody(),
            archive_path: item.path,
            destination_path: targetPath,
          }),
        },
        t('Starting extraction...')
      );
      setActionDialog(null);
      if (data?.job_id) {
        upsertFileJob(data);
      } else if (data) {
        setNotice(t('Extracted {name}.', { name: item.name }));
        await listFiles(targetPath === '.' ? '' : targetPath);
        await loadCurrentUser();
      }
      return;
    }

    if (type === 'delete') {
      const data = await request(
        '/maintenance/files/delete',
        {
          method: 'POST',
          body: JSON.stringify({ ...fileTargetBody(), paths: actionDialog.paths }),
        },
        t('Removing selected items...')
      );
      if (data) {
        setActionDialog(null);
        setNotice(t('Removed {n} item(s).', { n: actionDialog.paths.length }));
        await listFiles(fileListPath);
        await loadCurrentUser();
      }
    }
  }

  async function loadFileJobs() {
    const data = await request('/maintenance/files/jobs');
    if (data?.jobs) setFileJobs(data.jobs.filter(job => job.status !== 'done').slice(0, 6));
  }

  async function uploadSiteFile(file) {
    if (!file) return;
    if (!hasFileTarget()) {
      setError(t('Please select a website or application first.'));
      return;
    }
    const uploadDir = (fileListPath ?? fileUploadDir).trim();
    const form = new FormData();
    form.append('file', file);
    try {
      setError('');
      setLoading('Uploading file...');
      const csrfToken = readCookie('spanel_csrf');
      const headers = csrfToken ? { 'X-CSRF-Token': csrfToken } : {};
      const res = await fetch(`${API}${fileTargetBase()}/upload?path=${encodeURIComponent(uploadDir)}`, {
        method: 'POST',
        credentials: 'include',
        headers,
        body: form,
      });
      const responseText = await res.text();
      let data;
      try {
        data = responseText ? JSON.parse(responseText) : {};
      } catch {
        data = { detail: responseText || `HTTP ${res.status}` };
      }
      if (!res.ok) {
        if (handleAuthExpired(res.status, data.detail)) return;
        setError(formatApiError(data.detail, 'Upload failed.'));
        return;
      }
      setNotice(`Uploaded ${file.name} to ${uploadDir || 'site root'}.`);
      await listFiles(uploadDir);
      await loadCurrentUser();
    } catch {
      setError(t('File upload failed.'));
    } finally {
      setLoading('');
    }
  }

  // Render recursive directory nodes in the left sidebar tree
  function renderSubdirTreeNodes(prefix, baseApi, parentPath, depth, ownerObj, isSite = true) {
    const nodeKey = `${prefix}:${parentPath}`;
    const subdirs = treeDirs[nodeKey] || [];
    const isLoadingNode = !!treeLoading[nodeKey];

    if (isLoadingNode && subdirs.length === 0) {
      return (
        <div className="da-tree-loading" style={{ paddingLeft: `${depth * 14 + 28}px` }}>
          {t('Loading...')}
        </div>
      );
    }

    return subdirs.map(dir => {
      const childKey = `${prefix}:${dir.path}`;
      const isExpanded = !!treeExpanded[childKey];
      const isActive =
        virtualView === 'target' &&
        (isSite
          ? !fileAppId && String(selectedWebsiteId) === String(ownerObj.id)
          : String(fileAppId) === String(ownerObj.id)) &&
        String(fileListPath || '') === String(dir.path);
      const isShortcutLike = dir.name === 'private_html';

      return (
        <div className="da-tree-branch" key={childKey}>
          <div
            className={`da-tree-row ${isActive ? 'active' : ''}`}
            style={{ paddingLeft: `${depth * 14 + 8}px` }}
          >
            <button
              type="button"
              className="da-tree-Caret"
              aria-label={isExpanded ? t('Collapse') : t('Expand')}
              onClick={e => {
                e.stopPropagation();
                toggleTreeExpand(childKey, prefix, baseApi, dir.path);
              }}
            >
              {isExpanded ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
            </button>
            <button
              type="button"
              className="da-tree-label"
              onClick={() => {
                setTreeExpanded(prev => ({ ...prev, [childKey]: true }));
                if (!treeDirs[childKey]) {
                  fetchTreeSubdirs(prefix, baseApi, dir.path);
                }
                if (isSite) openSiteFolder(ownerObj, dir.path);
                else openAppFolder(ownerObj, dir.path);
              }}
              title={dir.name}
            >
              <TreeFolderIcon open={isExpanded || isActive} />
              <span>{dir.name}</span>
              {isShortcutLike && <Link2 size={12} className="da-tree-link-icon" />}
            </button>
          </div>
          {isExpanded && renderSubdirTreeNodes(prefix, baseApi, dir.path, depth + 1, ownerObj, isSite)}
        </div>
      );
    });
  }

  function renderLeftDirectoryTree() {
    const domainsExpanded = !!treeExpanded['root:domains'];
    const appsExpanded = !!treeExpanded['root:applications'];
    const defaultShortcutSite = currentSite || websites[0] || null;
    const isPublicHtmlShortcutActive =
      virtualView === 'target' &&
      !fileAppId &&
      defaultShortcutSite &&
      String(selectedWebsiteId) === String(defaultShortcutSite.id) &&
      fileListPath === 'public_html';

    return (
      <aside className="da-fm-sidebar" aria-label={t('My Files')}>
        <div className="da-fm-sidebar-header">
          <button
            type="button"
            className="da-fm-myfiles-btn"
            onClick={openVirtualHome}
            title={t('My Files')}
          >
            <FolderOpen size={15} />
            <span>{t('My Files')}</span>
          </button>
        </div>

        <div className="da-fm-tree-scroll">
          {/* domains root node */}
          <div className="da-tree-branch">
            <div
              className={`da-tree-row ${virtualView === 'domains' ? 'active' : ''}`}
              style={{ paddingLeft: '8px' }}
            >
              <button
                type="button"
                className="da-tree-Caret"
                onClick={e => {
                  e.stopPropagation();
                  toggleTreeExpand('root:domains');
                }}
                aria-label={domainsExpanded ? t('Collapse') : t('Expand')}
              >
                {domainsExpanded ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
              </button>
              <button type="button" className="da-tree-label" onClick={openVirtualDomains}>
                <TreeFolderIcon open={domainsExpanded} />
                <span>domains</span>
              </button>
            </div>

            {domainsExpanded &&
              websites.map(site => {
                const prefix = `site:${site.id}`;
                const baseApi = `/maintenance/files/${site.id}`;
                const siteRootKey = `${prefix}:`;
                const isSiteExpanded = !!treeExpanded[siteRootKey];
                const isSiteRootActive =
                  virtualView === 'target' &&
                  !fileAppId &&
                  String(selectedWebsiteId) === String(site.id) &&
                  (fileListPath === '' || !fileListPath);

                return (
                  <div className="da-tree-branch" key={siteRootKey}>
                    <div
                      className={`da-tree-row ${isSiteRootActive ? 'active' : ''}`}
                      style={{ paddingLeft: '22px' }}
                    >
                      <button
                        type="button"
                        className="da-tree-Caret"
                        onClick={e => {
                          e.stopPropagation();
                          toggleTreeExpand(siteRootKey, prefix, baseApi, '');
                        }}
                        aria-label={isSiteExpanded ? t('Collapse') : t('Expand')}
                      >
                        {isSiteExpanded ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
                      </button>
                      <button
                        type="button"
                        className="da-tree-label"
                        onClick={() => {
                          setTreeExpanded(prev => ({ ...prev, [siteRootKey]: true }));
                          if (!treeDirs[siteRootKey]) {
                            fetchTreeSubdirs(prefix, baseApi, '');
                          }
                          openSiteFolder(site, '');
                        }}
                        title={site.domain}
                      >
                        <TreeFolderIcon open={isSiteExpanded || isSiteRootActive} />
                        <span>{site.domain}</span>
                      </button>
                    </div>
                    {isSiteExpanded && renderSubdirTreeNodes(prefix, baseApi, '', 2, site, true)}
                  </div>
                );
              })}
          </div>

          {/* applications root node (if user has apps) */}
          {siteApps?.items?.length > 0 && (
            <div className="da-tree-branch">
              <div
                className={`da-tree-row ${virtualView === 'applications' ? 'active' : ''}`}
                style={{ paddingLeft: '8px' }}
              >
                <button
                  type="button"
                  className="da-tree-Caret"
                  onClick={e => {
                    e.stopPropagation();
                    toggleTreeExpand('root:applications');
                  }}
                >
                  {appsExpanded ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
                </button>
                <button type="button" className="da-tree-label" onClick={openVirtualApplications}>
                  <TreeFolderIcon open={appsExpanded} />
                  <span>applications</span>
                </button>
              </div>

              {appsExpanded &&
                siteApps.items.map(app => {
                  const prefix = `app:${app.id}`;
                  const baseApi = `/maintenance/app-files/${app.id}`;
                  const appRootKey = `${prefix}:`;
                  const isAppExpanded = !!treeExpanded[appRootKey];
                  const isAppRootActive =
                    virtualView === 'target' &&
                    String(fileAppId) === String(app.id) &&
                    (fileListPath === '' || !fileListPath);

                  return (
                    <div className="da-tree-branch" key={appRootKey}>
                      <div
                        className={`da-tree-row ${isAppRootActive ? 'active' : ''}`}
                        style={{ paddingLeft: '22px' }}
                      >
                        <button
                          type="button"
                          className="da-tree-Caret"
                          onClick={e => {
                            e.stopPropagation();
                            toggleTreeExpand(appRootKey, prefix, baseApi, '');
                          }}
                        >
                          {isAppExpanded ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
                        </button>
                        <button
                          type="button"
                          className="da-tree-label"
                          onClick={() => {
                            setTreeExpanded(prev => ({ ...prev, [appRootKey]: true }));
                            if (!treeDirs[appRootKey]) {
                              fetchTreeSubdirs(prefix, baseApi, '');
                            }
                            openAppFolder(app, '');
                          }}
                          title={app.name}
                        >
                          <TreeFolderIcon open={isAppExpanded || isAppRootActive} />
                          <span>{app.name}</span>
                        </button>
                      </div>
                      {isAppExpanded && renderSubdirTreeNodes(prefix, baseApi, '', 2, app, false)}
                    </div>
                  );
                })}
            </div>
          )}

          {/* public_html quick symlink node at bottom of tree */}
          {defaultShortcutSite && (
            <div className="da-tree-branch">
              <div
                className={`da-tree-row ${isPublicHtmlShortcutActive ? 'active' : ''}`}
                style={{ paddingLeft: '24px' }}
              >
                <button
                  type="button"
                  className="da-tree-label"
                  onClick={() => openSiteFolder(defaultShortcutSite, 'public_html')}
                  title={`domains/${defaultShortcutSite.domain}/public_html`}
                >
                  <TreeFolderIcon open={false} />
                  <span>public_html</span>
                  <Link2 size={12} className="da-tree-link-icon" />
                </button>
              </div>
            </div>
          )}
        </div>
      </aside>
    );
  }

  // Floating Context Menu Dialog (different items for Folder vs File)
  function renderContextMenuDialog() {
    if (!contextMenu || !contextMenu.item) return null;
    const { x, y, item } = contextMenu;
    const isDir = !!item.is_dir;
    const canEdit = !isDir && isTextEditable(item);
    const canExtract = !isDir && isArchiveFile(item);

    return (
      <div
        className="da-context-menu"
        role="menu"
        style={{ left: `${x}px`, top: `${y}px` }}
        onClick={e => e.stopPropagation()}
      >
        {isDir ? (
          <button
            type="button"
            role="menuitem"
            className="da-context-item"
            onClick={() => {
              setContextMenu(null);
              listFiles(item.path);
            }}
          >
            <FolderOpen size={15} />
            <span>{t('Open')}</span>
          </button>
        ) : (
          canEdit && (
            <button
              type="button"
              role="menuitem"
              className="da-context-item"
              onClick={() => {
                setContextMenu(null);
                openInlineEditor(item.path);
              }}
            >
              <Pencil size={15} />
              <span>{t('Edit')}</span>
            </button>
          )
        )}

        {canExtract && (
          <button
            type="button"
            role="menuitem"
            className="da-context-item"
            onClick={() => {
              setContextMenu(null);
              openExtractModal(item);
            }}
          >
            <ArchiveRestore size={15} />
            <span>{t('Extract')}</span>
          </button>
        )}

        <button
          type="button"
          role="menuitem"
          className="da-context-item"
          onClick={() => {
            setContextMenu(null);
            openTransferModal('copy', [item.path]);
          }}
        >
          <Copy size={15} />
          <span>{t('Copy')}</span>
        </button>

        <button
          type="button"
          role="menuitem"
          className="da-context-item"
          onClick={() => {
            setContextMenu(null);
            openDuplicateModal(item);
          }}
        >
          <Files size={15} />
          <span>{t('Duplicate')}</span>
        </button>

        <button
          type="button"
          role="menuitem"
          className="da-context-item"
          onClick={() => {
            setContextMenu(null);
            openTransferModal('move', [item.path]);
          }}
        >
          <MoveRight size={15} />
          <span>{t('Move')}</span>
        </button>

        {!isDir && (
          <button
            type="button"
            role="menuitem"
            className="da-context-item"
            onClick={() => {
              setContextMenu(null);
              downloadFile(item.path);
            }}
          >
            <Download size={15} />
            <span>{t('Download')}</span>
          </button>
        )}

        <button
          type="button"
          role="menuitem"
          className="da-context-item"
          onClick={() => {
            setContextMenu(null);
            openArchiveModal([item.path]);
          }}
        >
          <Archive size={15} />
          <span>{t('Archive')}</span>
        </button>

        <button
          type="button"
          role="menuitem"
          className="da-context-item"
          onClick={() => {
            setContextMenu(null);
            openRenameModal(item);
          }}
        >
          <Pencil size={15} />
          <span>{t('Rename')}</span>
        </button>

        <button
          type="button"
          role="menuitem"
          className="da-context-item"
          onClick={() => {
            setContextMenu(null);
            openChmodDialog(item);
          }}
        >
          <SettingsIcon size={15} />
          <span>{t('Set Permissions')}</span>
        </button>

        <div className="da-context-divider" />

        <button
          type="button"
          role="menuitem"
          className="da-context-item danger"
          onClick={() => {
            setContextMenu(null);
            openDeleteModal([item.path]);
          }}
        >
          <Trash2 size={15} />
          <span>{t('Remove')}</span>
        </button>
      </div>
    );
  }

  // Clean Modal Dialogs for Folder/File Actions
  function renderActionModalDialog() {
    if (!actionDialog) return null;
    const { type, title } = actionDialog;
    const currentFolderSubdirs = files.filter(f => f.is_dir).slice(0, 6);

    return (
      <div className="chmod-backdrop" role="presentation" onClick={() => setActionDialog(null)}>
        <form
          className="chmod-dialog da-action-dialog"
          role="dialog"
          aria-modal="true"
          aria-label={title}
          onClick={e => e.stopPropagation()}
          onSubmit={submitActionDialog}
        >
          <div className="chmod-head">
            <div>
              <h3>{title}</h3>
              {actionDialog.item && <p>{actionDialog.item.path}</p>}
              {actionDialog.paths && actionDialog.paths.length > 0 && (
                <p>
                  {actionDialog.paths.length === 1
                    ? actionDialog.paths[0]
                    : t('{n} selected items', { n: actionDialog.paths.length })}
                </p>
              )}
            </div>
            <button
              type="button"
              className="mini secondary-light"
              onClick={() => setActionDialog(null)}
              aria-label={t('Close')}
            >
              <X size={14} />
            </button>
          </div>

          {type === 'delete' ? (
            <div className="da-dialog-body">
              <p className="chmod-note warn">
                <AlertCircle size={15} />
                <span>
                  {t('Are you sure you want to permanently remove {n} item(s)? This action cannot be undone.', {
                    n: actionDialog.paths?.length || 0,
                  })}
                </span>
              </p>
              <ul className="da-delete-preview-list">
                {(actionDialog.paths || []).slice(0, 8).map(p => (
                  <li key={p}>
                    <code>{p}</code>
                  </li>
                ))}
                {(actionDialog.paths?.length || 0) > 8 && (
                  <li>+{actionDialog.paths.length - 8} more...</li>
                )}
              </ul>
            </div>
          ) : (
            <div className="da-dialog-body">
              <label className="da-dialog-field">
                <span>
                  {type === 'mkdir' && t('Folder name')}
                  {type === 'create' && t('File name')}
                  {(type === 'rename' || type === 'duplicate') && t('New name')}
                  {(type === 'copy' || type === 'move' || type === 'extract') && t('Destination folder path')}
                  {type === 'archive' && t('Archive file name')}
                </span>
                <input
                  type="text"
                  autoFocus
                  value={actionDialog.value || ''}
                  placeholder={actionDialog.placeholder || ''}
                  onChange={e => setActionDialog(prev => ({ ...prev, value: e.target.value }))}
                />
              </label>

              {type === 'create' && (
                <label className="chmod-setgid">
                  <input
                    type="checkbox"
                    checked={!!actionDialog.openAfterCreate}
                    onChange={e =>
                      setActionDialog(prev => ({ ...prev, openAfterCreate: e.target.checked }))
                    }
                  />
                  <span>{t('Open in editor immediately after creating')}</span>
                </label>
              )}

              {type === 'archive' && (
                <label className="da-dialog-field">
                  <span>{t('Archive format')}</span>
                  <select
                    value={actionDialog.format || 'zip'}
                    onChange={e => {
                      const nextFmt = e.target.value;
                      const stripped = String(actionDialog.value || 'archive').replace(
                        /\.(zip|tar\.gz|tgz)$/i,
                        ''
                      );
                      setActionDialog(prev => ({
                        ...prev,
                        format: nextFmt,
                        value: `${stripped}.${nextFmt}`,
                      }));
                    }}
                  >
                    <option value="zip">.zip (ZIP Archive)</option>
                    <option value="tar.gz">.tar.gz (Gzipped Tarball)</option>
                  </select>
                </label>
              )}

              {(type === 'copy' || type === 'move' || type === 'extract') && (
                <div className="da-quick-dest">
                  <small>{t('Quick select:')}</small>
                  <div className="chmod-presets">
                    <button
                      type="button"
                      className="mini secondary-light"
                      onClick={() => setActionDialog(prev => ({ ...prev, value: '.' }))}
                    >
                      / (Root)
                    </button>
                    <button
                      type="button"
                      className="mini secondary-light"
                      onClick={() => setActionDialog(prev => ({ ...prev, value: 'public_html' }))}
                    >
                      public_html
                    </button>
                    {fileListPath && parentFilePath(fileListPath) && (
                      <button
                        type="button"
                        className="mini secondary-light"
                        onClick={() =>
                          setActionDialog(prev => ({ ...prev, value: parentFilePath(fileListPath) }))
                        }
                      >
                        .. ({parentFilePath(fileListPath)})
                      </button>
                    )}
                    {currentFolderSubdirs.map(sub => (
                      <button
                        key={sub.path}
                        type="button"
                        className="mini secondary-light"
                        onClick={() => setActionDialog(prev => ({ ...prev, value: sub.path }))}
                      >
                        {sub.name}
                      </button>
                    ))}
                  </div>
                </div>
              )}
            </div>
          )}

          <div className="chmod-actions">
            <button
              type="button"
              className="secondary-light"
              disabled={!!loading}
              onClick={() => setActionDialog(null)}
            >
              {t('Cancel')}
            </button>
            <button
              type="submit"
              className={type === 'delete' ? 'danger' : ''}
              disabled={!!loading}
            >
              {type === 'delete' ? (
                <>
                  <Trash2 size={14} /> {t('Remove')}
                </>
              ) : (
                <>
                  <Check size={14} /> {t('Confirm')}
                </>
              )}
            </button>
          </div>
        </form>
      </div>
    );
  }

  // Render In-Page File Content Editor (inside the File Manager right pane)
  function renderInPageEditor() {
    const fileName = editingFilePath.split('/').pop() || editingFilePath;
    const langMode = editorLanguageForPath(editingFilePath);
    const lineCount = (fileContent || '').split('\n').length;
    const isDirty = fileContent !== originalFileContent;
    const activeApp = currentFileApp();
    const targetLabel = activeApp ? activeApp.name : currentSite?.domain || '';

    return (
      <div className="da-inline-editor">
        <div className="da-inline-editor-header">
          <div className="da-inline-editor-title">
            <button
              type="button"
              className="secondary-light da-editor-back-btn"
              onClick={closeInlineEditorIfAllowed}
              title={t('Back to folder')}
            >
              <ArrowLeft size={15} />
              <span>{t('Back')}</span>
            </button>
            <div className="da-inline-editor-meta">
              <div className="da-inline-editor-filename">
                <Code2 size={16} />
                <strong>{fileName}</strong>
                {isDirty && <span className="da-editor-dirty-pill">{t('Modified')}</span>}
              </div>
              <span className="da-inline-editor-path">
                {targetLabel ? `${targetLabel} / ` : ''}
                {editingFilePath}
              </span>
            </div>
          </div>

          <div className="da-inline-editor-actions">
            <span className="editor-chip">{langMode}</span>
            <span className="editor-chip">{lineCount} lines</span>
            <span className="editor-chip">
              Ln {editorCursor.line}, Col {editorCursor.column}
            </span>
            <button
              type="button"
              className={`secondary-light ${editorWrap ? 'active' : ''}`}
              onClick={() => setEditorWrap(w => !w)}
              title={t('Toggle word wrap')}
            >
              Wrap
            </button>
            <button
              type="button"
              className="secondary-light"
              disabled={!!loading}
              onClick={reloadInlineFile}
              title={t('Reload from disk')}
            >
              <RotateCcw size={14} />
              <span>{t('Reload')}</span>
            </button>
            <button
              type="button"
              className="secondary-light"
              disabled={!!loading}
              onClick={() => downloadFile(editingFilePath)}
              title={t('Download file')}
            >
              <Download size={14} />
            </button>
            <button
              type="button"
              disabled={!!loading}
              onClick={saveInlineFile}
              title="Ctrl+S"
            >
              <Save size={14} />
              <span>{t('Save')}</span>
            </button>
            <button
              type="button"
              className="secondary-light"
              onClick={closeInlineEditorIfAllowed}
              title={t('Close editor')}
            >
              <X size={14} />
              <span>{t('Close')}</span>
            </button>
          </div>
        </div>

        <div className="da-inline-editor-body">
          <InlineCodeEditor
            value={fileContent}
            mode={langMode}
            disabled={!!loading}
            wrapEnabled={editorWrap}
            onChange={setFileContent}
            onCursorChange={setEditorCursor}
            onSaveShortcut={saveInlineFile}
          />
        </div>
      </div>
    );
  }

  function renderFiles() {
    const allSelected = files.length > 0 && selectedFilePaths.length === files.length;
    const selectedItems = files.filter(item => selectedFilePaths.includes(item.path));
    const singleSelected = selectedItems.length === 1 ? selectedItems[0] : null;
    const singleIsFolder = !!(singleSelected && singleSelected.is_dir);
    const singleIsEditableFile = !!(singleSelected && !singleSelected.is_dir && isTextEditable(singleSelected));
    const singleIsArchive = !!(singleSelected && !singleSelected.is_dir && isArchiveFile(singleSelected));
    const singleIsDownloadable = !!(singleSelected && !singleSelected.is_dir);

    const activeFileApp = currentFileApp();
    const isTargetActive = hasFileTarget();
    const targetKey = fileTargetKey();
    const visibleFileJobs = fileJobs
      .filter(job => (job.target_key || `site:${job.website_id}`) === targetKey && job.status !== 'done')
      .slice(0, 4);
    const crumbs = isTargetActive ? fileBreadcrumbs(fileListPath) : [];
    const defaultShortcutSite = currentSite || websites[0] || null;
    const ownerName =
      currentSite?.linux_user ||
      activeFileApp?.linux_user ||
      currentUser?.username ||
      'user';
    const uidGidLabel = `${ownerName}/${ownerName}`;

    return (
      <div className="section da-fm-section">
        {renderChmodDialog()}
        {renderContextMenuDialog()}
        {renderActionModalDialog()}

        <div className="da-fm-layout">
          {/* 1. Left Directory Tree Sidebar ("My Files") */}
          {renderLeftDirectoryTree()}

          {/* 2. Right Main File Workspace */}
          <div className="da-fm-main">
            {/* Top Breadcrumb + Quick Create/Upload Bar */}
            <div className="da-fm-topbar">
              <nav className="da-file-breadcrumb" aria-label={t('File path')}>
                <button
                  type="button"
                  className={`da-crumb-home ${virtualView === 'home' && !editingFilePath ? 'da-crumb-current' : ''}`}
                  onClick={openVirtualHome}
                  title={t('Home')}
                >
                  <Home size={16} />
                </button>
                {virtualView !== 'home' && (
                  <>
                    <ChevronRight size={14} className="da-crumb-sep" />
                    {isTargetActive && activeFileApp ? (
                      <button type="button" className="da-crumb-link" onClick={openVirtualApplications}>
                        applications
                      </button>
                    ) : virtualView === 'applications' ? (
                      <button
                        type="button"
                        className="da-crumb-link da-crumb-current"
                        onClick={openVirtualApplications}
                      >
                        applications
                      </button>
                    ) : (
                      <button
                        type="button"
                        className={`da-crumb-link ${!isTargetActive && virtualView === 'domains' ? 'da-crumb-current' : ''}`}
                        onClick={openVirtualDomains}
                      >
                        domains
                      </button>
                    )}
                  </>
                )}
                {isTargetActive && (activeFileApp || currentSite) && (
                  <>
                    <ChevronRight size={14} className="da-crumb-sep" />
                    <button
                      type="button"
                      className={`da-crumb-link ${crumbs.length === 0 && !editingFilePath ? 'da-crumb-current' : ''}`}
                      onClick={() => {
                        if (!closeInlineEditorIfAllowed()) return;
                        listFiles('');
                      }}
                    >
                      {activeFileApp ? activeFileApp.name : currentSite.domain}
                    </button>
                  </>
                )}
                {crumbs.map((crumb, idx) => (
                  <React.Fragment key={crumb.path}>
                    <ChevronRight size={14} className="da-crumb-sep" />
                    <button
                      type="button"
                      className={`da-crumb-link ${idx === crumbs.length - 1 && !editingFilePath ? 'da-crumb-current' : ''}`}
                      onClick={() => {
                        if (!closeInlineEditorIfAllowed()) return;
                        listFiles(crumb.path);
                      }}
                    >
                      {crumb.label}
                    </button>
                  </React.Fragment>
                ))}
                {editingFilePath && (
                  <>
                    <ChevronRight size={14} className="da-crumb-sep" />
                    <span className="da-crumb-link da-crumb-current">
                      {editingFilePath.split('/').pop()}
                    </span>
                  </>
                )}
              </nav>

              <div className="da-fm-quick-actions">
                {currentUser && !isAdmin && (
                  <span className="da-fm-quota-pill" title={t('Storage usage')}>
                    {storageUsageText(currentUser)}
                  </span>
                )}
                <button
                  type="button"
                  className="secondary-light da-quick-btn"
                  disabled={!isTargetActive || !!loading || !!editingFilePath}
                  onClick={openNewFolderModal}
                  title={t('New Folder')}
                >
                  <FolderPlus size={14} />
                  <span>{t('New Folder')}</span>
                </button>
                <button
                  type="button"
                  className="secondary-light da-quick-btn"
                  disabled={!isTargetActive || !!loading || !!editingFilePath}
                  onClick={openNewFileModal}
                  title={t('New File')}
                >
                  <FilePlus size={14} />
                  <span>{t('New File')}</span>
                </button>
                <button
                  type="button"
                  className="secondary-light da-quick-btn"
                  disabled={!isTargetActive || !!loading || !!editingFilePath}
                  onClick={() => uploadInputRef.current?.click()}
                  title={t('Upload File')}
                >
                  <Upload size={14} />
                  <span>{t('Upload')}</span>
                </button>
                <input
                  ref={uploadInputRef}
                  type="file"
                  style={{ display: 'none' }}
                  disabled={!isTargetActive || !!loading}
                  onChange={e => {
                    uploadSiteFile(e.target.files?.[0]);
                    e.target.value = '';
                  }}
                />
                <button
                  type="button"
                  className="secondary-light da-quick-btn"
                  disabled={!isTargetActive || !!loading}
                  onClick={() => (editingFilePath ? reloadInlineFile() : listFiles(fileListPath))}
                  title={t('Refresh')}
                >
                  <RefreshCw size={14} />
                </button>
              </div>
            </div>

            {/* 3. If editing a file, show the In-Page File Content Editor right here */}
            {editingFilePath ? (
              renderInPageEditor()
            ) : (
              <>
                {/* Contextual Action Bar (matches Screenshot 1 & 2) */}
                <div className="da-fm-actionbar">
                  <span className="da-fm-selected-count">
                    {t('Selected:')} {isTargetActive ? `${selectedFilePaths.length} / ${files.length}` : '0 / 0'}
                  </span>

                  {singleIsFolder ? (
                    <button
                      type="button"
                      className="da-actionbar-btn"
                      disabled={!!loading}
                      onClick={() => listFiles(singleSelected.path)}
                    >
                      <FolderOpen size={14} />
                      <span>{t('Open')}</span>
                    </button>
                  ) : (
                    <button
                      type="button"
                      className="da-actionbar-btn"
                      disabled={!isTargetActive || !singleIsEditableFile || !!loading}
                      onClick={() => singleSelected && openInlineEditor(singleSelected.path)}
                    >
                      <Pencil size={14} />
                      <span>{t('Edit')}</span>
                    </button>
                  )}

                  {singleIsArchive && (
                    <button
                      type="button"
                      className="da-actionbar-btn"
                      disabled={!!loading}
                      onClick={() => openExtractModal(singleSelected)}
                    >
                      <ArchiveRestore size={14} />
                      <span>{t('Extract')}</span>
                    </button>
                  )}

                  <button
                    type="button"
                    className="da-actionbar-btn"
                    disabled={!isTargetActive || selectedFilePaths.length === 0 || !!loading}
                    onClick={() => openTransferModal('copy', selectedFilePaths)}
                  >
                    <Copy size={14} />
                    <span>{t('Copy')}</span>
                  </button>

                  <button
                    type="button"
                    className="da-actionbar-btn"
                    disabled={!isTargetActive || !singleSelected || !!loading}
                    onClick={() => singleSelected && openDuplicateModal(singleSelected)}
                  >
                    <Files size={14} />
                    <span>{t('Duplicate')}</span>
                  </button>

                  <button
                    type="button"
                    className="da-actionbar-btn"
                    disabled={!isTargetActive || selectedFilePaths.length === 0 || !!loading}
                    onClick={() => openTransferModal('move', selectedFilePaths)}
                  >
                    <MoveRight size={14} />
                    <span>{t('Move')}</span>
                  </button>

                  <button
                    type="button"
                    className="da-actionbar-btn"
                    disabled={!isTargetActive || !singleIsDownloadable || !!loading}
                    onClick={() => singleSelected && downloadFile(singleSelected.path)}
                  >
                    <Download size={14} />
                    <span>{t('Download')}</span>
                  </button>

                  <button
                    type="button"
                    className="da-actionbar-btn"
                    disabled={!isTargetActive || selectedFilePaths.length === 0 || !!loading}
                    onClick={() => openArchiveModal(selectedFilePaths)}
                  >
                    <Archive size={14} />
                    <span>{t('Archive')}</span>
                  </button>

                  <button
                    type="button"
                    className="da-actionbar-btn"
                    disabled={!isTargetActive || !singleSelected || !!loading}
                    onClick={() => singleSelected && openRenameModal(singleSelected)}
                  >
                    <Pencil size={14} />
                    <span>{t('Rename')}</span>
                  </button>

                  <button
                    type="button"
                    className="da-actionbar-btn"
                    disabled={!isTargetActive || selectedItems.length === 0 || !!loading}
                    onClick={() => openChmodDialog(selectedItems)}
                  >
                    <SettingsIcon size={14} />
                    <span>{t('Set Permissions')}</span>
                  </button>

                  <button
                    type="button"
                    className="da-actionbar-btn danger"
                    disabled={!isTargetActive || selectedFilePaths.length === 0 || !!loading}
                    onClick={() => openDeleteModal(selectedFilePaths)}
                  >
                    <Trash2 size={14} />
                    <span>{t('Remove')}</span>
                  </button>
                </div>

                {visibleFileJobs.length > 0 && (
                  <div className="file-job-list da-fm-jobs">
                    {visibleFileJobs.map(job => (
                      <div className={`file-job ${job.status}`} key={job.job_id}>
                        <Clock size={14} />
                        <span>
                          <strong>{job.archive_path?.split('/').pop() || 'Archive'}</strong>{' '}
                          {job.status === 'error' ? 'failed' : job.status}
                        </span>
                        {job.error && <small>{job.error}</small>}
                        <button
                          className="file-job-dismiss"
                          onClick={() => dismissFileJob(job.job_id)}
                          aria-label={t('Dismiss')}
                        >
                          <X size={13} />
                        </button>
                      </div>
                    ))}
                  </div>
                )}

                {/* Table Header */}
                <div className="da-fm-table-header">
                  <label className="da-fm-check-cell">
                    <input
                      type="checkbox"
                      checked={isTargetActive && allSelected}
                      onChange={toggleAllFiles}
                      disabled={!isTargetActive || files.length === 0}
                    />
                    <span className="sr-only">{t('Select all')}</span>
                  </label>
                  <span>{t('Name')}</span>
                  <span>{t('Size')}</span>
                  <span>{t('Permissions')}</span>
                  <span>{t('Last modified')}</span>
                  <span>{t('UID/GID')}</span>
                </div>

                {/* Table Body */}
                <div className="da-fm-table-body">
                  {!isTargetActive && virtualView === 'home' && (
                    <>
                      <div className="da-fm-row" onClick={openVirtualDomains}>
                        <span className="da-fm-check-cell">
                          <input type="checkbox" disabled />
                        </span>
                        <button type="button" className="da-fm-name-btn" onClick={openVirtualDomains}>
                          <TableEntryIcon item={{ is_dir: true, name: 'domains' }} />
                          <span>domains</span>
                        </button>
                        <span className="da-fm-size"></span>
                        <span className="da-fm-perms">drwxr-xr-x</span>
                        <span className="da-fm-modified">{t('{n} domain(s)', { n: websites.length })}</span>
                        <span className="da-fm-uid">{uidGidLabel}</span>
                      </div>
                      {siteApps?.items?.length > 0 && (
                        <div className="da-fm-row" onClick={openVirtualApplications}>
                          <span className="da-fm-check-cell">
                            <input type="checkbox" disabled />
                          </span>
                          <button type="button" className="da-fm-name-btn" onClick={openVirtualApplications}>
                            <TableEntryIcon item={{ is_dir: true, name: 'applications' }} />
                            <span>applications</span>
                          </button>
                          <span className="da-fm-size"></span>
                          <span className="da-fm-perms">drwxr-xr-x</span>
                          <span className="da-fm-modified">
                            {t('{n} item(s)', { n: siteApps.items.length })}
                          </span>
                          <span className="da-fm-uid">{uidGidLabel}</span>
                        </div>
                      )}
                      {defaultShortcutSite && (
                        <div
                          className="da-fm-row"
                          onClick={() => openSiteFolder(defaultShortcutSite, 'public_html')}
                        >
                          <span className="da-fm-check-cell">
                            <input type="checkbox" disabled />
                          </span>
                          <button
                            type="button"
                            className="da-fm-name-btn"
                            onClick={() => openSiteFolder(defaultShortcutSite, 'public_html')}
                          >
                            <TableEntryIcon item={{ is_dir: true, name: 'public_html' }} />
                            <span>public_html</span>
                            <small className="da-symlink-badge">
                              → domains/{defaultShortcutSite.domain}/public_html
                            </small>
                          </button>
                          <span className="da-fm-size"></span>
                          <span className="da-fm-perms">lrwxrwxrwx</span>
                          <span className="da-fm-modified">{defaultShortcutSite.domain}</span>
                          <span className="da-fm-uid">{uidGidLabel}</span>
                        </div>
                      )}
                    </>
                  )}

                  {!isTargetActive && virtualView === 'domains' && (
                    <>
                      {websites.length === 0 && (
                        <div className="empty-box">{t('No websites found.')}</div>
                      )}
                      {websites.map(site => (
                        <div
                          className="da-fm-row"
                          key={`domain-folder-${site.id}`}
                          onClick={() => openSiteFolder(site, 'public_html')}
                        >
                          <span className="da-fm-check-cell">
                            <input type="checkbox" disabled />
                          </span>
                          <button
                            type="button"
                            className="da-fm-name-btn"
                            onClick={e => {
                              e.stopPropagation();
                              openSiteFolder(site, 'public_html');
                            }}
                          >
                            <TableEntryIcon item={{ is_dir: true, name: site.domain }} />
                            <span>{site.domain}</span>
                          </button>
                          <span className="da-fm-size"></span>
                          <span className="da-fm-perms">drwxr-xr-x</span>
                          <span className="da-fm-modified">PHP {site.php_version || '8.4'}</span>
                          <span className="da-fm-uid">
                            {(site.linux_user || ownerName)}/{(site.linux_user || ownerName)}
                          </span>
                        </div>
                      ))}
                    </>
                  )}

                  {!isTargetActive && virtualView === 'applications' && (
                    <>
                      {(!siteApps?.items || siteApps.items.length === 0) && (
                        <div className="empty-box">{t('No files in this folder.')}</div>
                      )}
                      {(siteApps?.items || []).map(app => (
                        <div
                          className="da-fm-row"
                          key={`app-folder-${app.id}`}
                          onClick={() => openAppFolder(app, '')}
                        >
                          <span className="da-fm-check-cell">
                            <input type="checkbox" disabled />
                          </span>
                          <button
                            type="button"
                            className="da-fm-name-btn"
                            onClick={e => {
                              e.stopPropagation();
                              openAppFolder(app, '');
                            }}
                          >
                            <TableEntryIcon item={{ is_dir: true, name: app.name }} />
                            <span>{app.name}</span>
                          </button>
                          <span className="da-fm-size"></span>
                          <span className="da-fm-perms">drwxr-xr-x</span>
                          <span className="da-fm-modified">{app.runtime || 'app'}</span>
                          <span className="da-fm-uid">{uidGidLabel}</span>
                        </div>
                      ))}
                    </>
                  )}

                  {isTargetActive && files.length === 0 && (
                    <div className="empty-box">{t('No files in this folder.')}</div>
                  )}

                  {isTargetActive &&
                    files.map(item => {
                      const isSelected = selectedFilePaths.includes(item.path);
                      const symPerms = permissionSymbols(item.mode, item.is_dir);

                      return (
                        <div
                          key={item.path}
                          className={`da-fm-row ${isSelected ? 'selected' : ''}`}
                          onClick={e => {
                            if (e.ctrlKey || e.metaKey) {
                              toggleFileSelection(item.path);
                            } else {
                              setSelectedFilePaths([item.path]);
                            }
                          }}
                          onDoubleClick={() => {
                            if (item.is_dir) listFiles(item.path);
                            else if (isTextEditable(item)) openInlineEditor(item.path);
                            else downloadFile(item.path);
                          }}
                          onContextMenu={e => openRowContextMenu(e, item)}
                        >
                          <span
                            className="da-fm-check-cell"
                            onClick={e => e.stopPropagation()}
                          >
                            <input
                              type="checkbox"
                              checked={isSelected}
                              onChange={() => toggleFileSelection(item.path)}
                            />
                          </span>

                          <button
                            type="button"
                            className="da-fm-name-btn"
                            onClick={e => {
                              e.stopPropagation();
                              if (item.is_dir) listFiles(item.path);
                              else if (isTextEditable(item)) openInlineEditor(item.path);
                              else downloadFile(item.path);
                            }}
                          >
                            <TableEntryIcon item={item} />
                            <span>{item.name}</span>
                          </button>

                          <span className="da-fm-size">
                            {item.is_dir ? '' : formatBytes(item.size)}
                          </span>

                          <button
                            type="button"
                            className="da-fm-perms-btn"
                            disabled={!!loading}
                            title={`${item.mode || '---'} (${symPerms}) - ${t('Click to change permissions')}`}
                            onClick={e => {
                              e.stopPropagation();
                              openChmodDialog(item);
                            }}
                          >
                            {symPerms}
                          </button>

                          <span
                            className="da-fm-modified"
                            title={formatFileTime(item.modified, true)}
                          >
                            {formatFileTime(item.modified)}
                          </span>

                          <div className="da-fm-uid-cell">
                            <span className="da-fm-uid">{uidGidLabel}</span>
                            <button
                              type="button"
                              className="da-fm-row-menu-btn"
                              aria-label={t('Actions')}
                              title={t('Actions')}
                              onClick={e => openRowContextMenu(e, item)}
                            >
                              <MoreVertical size={15} />
                            </button>
                          </div>
                        </div>
                      );
                    })}
                </div>
              </>
            )}
          </div>
        </div>
      </div>
    );
  }

  return renderFiles();
}
