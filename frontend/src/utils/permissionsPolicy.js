export const CORE_PERMISSIONS_FEATURES = [
  'camera',
  'microphone',
  'geolocation',
  'payment',
  'usb',
  'bluetooth',
  'magnetometer',
  'gyroscope',
  'accelerometer',
];

export const PERMISSIONS_UI_ITEMS = [
  { key: 'microphone', label: 'Microphone', hint: 'WebRTC / Voice / Ghi âm' },
  { key: 'camera', label: 'Camera', hint: 'Video Call / Quét QR' },
  { key: 'geolocation', label: 'Geolocation', hint: 'Định vị vị trí trình duyệt' },
  { key: 'display-capture', label: 'Display Capture', hint: 'Chia sẻ màn hình (Screen Share)' },
  { key: 'autoplay', label: 'Autoplay', hint: 'Tự động phát Media' },
  { key: 'fullscreen', label: 'Fullscreen', hint: 'Toàn màn hình' },
];

export function parsePermissionsPolicy(policyStr = '') {
  const map = {};
  for (const feat of CORE_PERMISSIONS_FEATURES) {
    map[feat] = '()';
  }
  const text = String(policyStr || '').trim();
  if (!text) return map;
  for (const segment of text.split(',')) {
    const idx = segment.indexOf('=');
    if (idx === -1) continue;
    const key = segment.slice(0, idx).trim().toLowerCase();
    const val = segment.slice(idx + 1).trim();
    if (key) {
      map[key] = val || '()';
    }
  }
  return map;
}

export function buildPermissionsPolicy(directivesMap = {}) {
  const merged = {};
  for (const feat of CORE_PERMISSIONS_FEATURES) {
    merged[feat] = directivesMap[feat] || '()';
  }
  for (const [rawKey, rawVal] of Object.entries(directivesMap)) {
    const key = String(rawKey || '').trim().toLowerCase();
    if (!key || CORE_PERMISSIONS_FEATURES.includes(key)) continue;
    const val = String(rawVal || '').trim();
    if (val && val !== '()') {
      merged[key] = val;
    }
  }
  const extras = Object.keys(merged)
    .filter(k => !CORE_PERMISSIONS_FEATURES.includes(k))
    .sort();
  return [...CORE_PERMISSIONS_FEATURES, ...extras]
    .map(k => `${k}=${merged[k]}`)
    .join(', ');
}
