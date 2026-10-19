import React from 'react';
import { useAppContext } from '../contexts/AppContext.jsx';
import { t } from '../i18n.js';

export function WebsiteSelect() {
  const { websites, selectedWebsiteId, setSelectedWebsiteId } = useAppContext();
  
  return (
    <select value={selectedWebsiteId} onChange={e => setSelectedWebsiteId(e.target.value)}>
      <option value="">{t('-- Select website --')}</option>
      {websites.map(site => <option key={site.id} value={site.id}>{site.domain}</option>)}
    </select>
  );
}
