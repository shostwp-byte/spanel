export function formatBytes(value) {
  const amount = Number(value);
  if (!Number.isFinite(amount) || amount < 0) return '--';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let size = amount;
  let unit = 0;
  while (size >= 1024 && unit < units.length - 1) {
    size /= 1024;
    unit++;
  }
  return `${size < 10 && unit > 0 ? size.toFixed(1) : Math.round(size)} ${units[unit]}`;
}

export function roleLabel(role) {
  if (role === 'admin') return 'Admin';
  if (role === 'reseller') return 'Reseller';
  return 'End user';
}

export const CRON_SCHEDULE_PRESETS = [
  ['* * * * *', 'Every minute'],
  ['*/5 * * * *', 'Every 5 minutes'],
  ['*/15 * * * *', 'Every 15 minutes'],
  ['*/30 * * * *', 'Every 30 minutes'],
  ['0 * * * *', 'Every hour'],
  ['0 */6 * * *', 'Every 6 hours'],
  ['0 0 * * *', 'Every day at 00:00'],
  ['0 2 * * *', 'Every day at 02:00'],
  ['0 0 * * 0', 'Every Sunday at 00:00'],
  ['0 0 1 * *', 'On the 1st of every month'],
];

export const BACKUP_SCHEDULE_PRESETS = [
  ['0 2 * * *', 'Every day at 02:00'],
  ['0 3 * * *', 'Every day at 03:00'],
  ['0 */12 * * *', 'Every 12 hours'],
  ['0 2 * * 0', 'Every Sunday at 02:00'],
  ['0 2 1 * *', 'On the 1st of every month at 02:00'],
];

export const isPresetSchedule = (presetsOrValue, maybeValue) => {
  const list = Array.isArray(presetsOrValue) ? presetsOrValue : BACKUP_SCHEDULE_PRESETS;
  const target = Array.isArray(presetsOrValue) ? maybeValue : presetsOrValue;
  return list.some((entry) => (Array.isArray(entry) ? entry[0] : entry?.value) === target);
};

export const MALWARE_SCHEDULE_LABELS = {
  websites: 'Websites',
  server: 'Whole server',
};

export const WEEKDAY_LABELS = [
  'Monday',
  'Tuesday',
  'Wednesday',
  'Thursday',
  'Friday',
  'Saturday',
  'Sunday',
];

export function utcScheduleToVn(weekday = 6, hour = 3) {
  const totalHours = ((Number(weekday) * 24) + Number(hour) + 7 + 168) % 168;
  return { weekday: Math.floor(totalHours / 24), hour: totalHours % 24 };
}

export function vnScheduleToUtc(weekday = 6, hour = 10) {
  const totalHours = ((Number(weekday) * 24) + Number(hour) - 7 + 168) % 168;
  return { weekday: Math.floor(totalHours / 24), hour: totalHours % 24 };
}
