import React from 'react';

export const EmptyState = ({ icon: Icon, message }) => (
  <div className="empty-state">
    <Icon size={24} className="empty-icon"/>
    <span>{message}</span>
  </div>
);
