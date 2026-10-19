import React from 'react';

export function NoAutofillInput({ onFocus, onPointerDown, ...props }) {
  const unlock = event => { event.currentTarget.readOnly = false; };
  return <input
    autoComplete="off"
    {...props}
    readOnly
    onPointerDown={event => { unlock(event); onPointerDown?.(event); }}
    onFocus={event => { unlock(event); onFocus?.(event); }}
  />;
}
