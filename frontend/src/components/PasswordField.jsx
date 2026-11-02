import React, { useState, useContext } from 'react';
import { Dices, Eye, EyeOff, Copy, Check, X, CheckCircle } from 'lucide-react';
import { AppContext } from '../contexts/AppContext.jsx';
import { t } from '../i18n.js';

export function generateStrongPassword(length = 20) {
  const safeLen = Math.max(8, Math.min(72, Number(length) || 20));
  const lower = 'abcdefghijklmnopqrstuvwxyz';
  const upper = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ';
  const digits = '0123456789';
  const symbols = '!@%^*_+-=';
  const all = lower + upper + digits + symbols;

  const randIndex = (max) => {
    if (typeof crypto !== 'undefined' && crypto.getRandomValues) {
      const buf = new Uint32Array(1);
      const limit = Math.floor(0x100000000 / max) * max;
      do {
        crypto.getRandomValues(buf);
      } while (buf[0] >= limit);
      return buf[0] % max;
    }
    return Math.floor(Math.random() * max);
  };

  const chars = [
    lower[randIndex(lower.length)],
    upper[randIndex(upper.length)],
    digits[randIndex(digits.length)],
    symbols[randIndex(symbols.length)],
  ];
  for (let i = chars.length; i < safeLen; i += 1) {
    chars.push(all[randIndex(all.length)]);
  }
  for (let i = chars.length - 1; i > 0; i -= 1) {
    const j = randIndex(i + 1);
    [chars[i], chars[j]] = [chars[j], chars[i]];
  }
  return chars.join('');
}

export async function copyToClipboardCompat(text) {
  const str = String(text ?? '');
  if (!str) return false;
  if (typeof navigator !== 'undefined' && navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(str);
      return true;
    } catch {
      // Fallback for non-HTTPS contexts (e.g. direct IP access before SSL setup)
    }
  }
  try {
    const holder = document.createElement('textarea');
    holder.value = str;
    holder.setAttribute('readonly', '');
    holder.style.position = 'fixed';
    holder.style.top = '-9999px';
    holder.style.opacity = '0';
    document.body.appendChild(holder);
    holder.select();
    const ok = document.execCommand('copy');
    document.body.removeChild(holder);
    return ok !== false;
  } catch {
    return false;
  }
}

export function CopyPasswordButton({
  value,
  label,
  copiedLabel,
  noticeMessage,
  size = 13,
  className = 'mini secondary-light sp-copy-btn',
  title,
  disabled = false,
}) {
  const ctx = useContext(AppContext) || null;
  const [copied, setCopied] = useState(false);

  async function handleCopy(e) {
    e.preventDefault();
    e.stopPropagation();
    if (!value || disabled) return;
    const ok = await copyToClipboardCompat(value);
    if (ok) {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
      if (noticeMessage !== false && ctx?.setNotice) {
        ctx.setNotice(noticeMessage || t('Password copied to clipboard.'));
      }
    } else if (ctx?.setError) {
      ctx.setError(t('Copy failed. Select the text and press Ctrl+C.'));
    }
  }

  const effectiveTitle = copied
    ? (copiedLabel || t('Copied!'))
    : (title || label || t('Copy password'));

  return (
    <button
      type="button"
      className={`${className}${copied ? ' is-copied' : ''}`}
      onClick={handleCopy}
      disabled={disabled || !value}
      title={effectiveTitle}
      aria-label={effectiveTitle}
    >
      {copied ? (
        <Check size={size} className="sp-copy-check" />
      ) : (
        <Copy size={size} />
      )}
      {label ? <span>{copied ? (copiedLabel || t('Copied!')) : label}</span> : null}
    </button>
  );
}

export function PasswordField({
  value = '',
  onChange,
  onValueChange,
  onGenerate,
  generator,
  length = 20,
  showGenerator = true,
  showToggle = true,
  showCopy = true,
  defaultRevealed = false,
  compact = false,
  placeholder,
  disabled = false,
  required = false,
  name,
  id,
  ariaLabel,
  className = '',
  style,
  inputStyle,
  onKeyDown,
  minLength,
  maxLength,
  ...rest
}) {
  const ctx = useContext(AppContext) || null;
  const [revealed, setRevealed] = useState(Boolean(defaultRevealed));
  const [copied, setCopied] = useState(false);

  const emitValue = (nextVal) => {
    if (onValueChange) {
      onValueChange(nextVal);
    }
    if (onChange) {
      onChange({ target: { value: nextVal, name: name || '' } });
    }
  };

  const handleInputChange = (e) => {
    if (onValueChange) {
      onValueChange(e.target.value);
    }
    if (onChange) {
      onChange(e);
    }
  };

  const handleRollPassword = (e) => {
    e.preventDefault();
    e.stopPropagation();
    if (disabled) return;
    const nextPassword =
      typeof generator === 'function' ? generator() : generateStrongPassword(length);
    setRevealed(true);
    emitValue(nextPassword);
    if (onGenerate) {
      onGenerate(nextPassword);
    }
  };

  const handleToggleReveal = (e) => {
    e.preventDefault();
    e.stopPropagation();
    setRevealed((prev) => !prev);
  };

  const handleCopy = async (e) => {
    e.preventDefault();
    e.stopPropagation();
    if (!value) return;
    const ok = await copyToClipboardCompat(value);
    if (ok) {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
      if (ctx?.setNotice) {
        ctx.setNotice(t('Password copied to clipboard.'));
      }
    } else if (ctx?.setError) {
      ctx.setError(t('Copy failed. Select the text and press Ctrl+C.'));
    }
  };

  return (
    <div className={`sp-password-field ${compact ? 'compact ' : ''}${className}`.trim()} style={style}>
      <input
        id={id}
        name={name}
        type={revealed ? 'text' : 'password'}
        className={revealed ? 'sp-pw-visible' : ''}
        value={value}
        onChange={handleInputChange}
        onKeyDown={onKeyDown}
        placeholder={placeholder}
        disabled={disabled}
        required={required}
        minLength={minLength}
        maxLength={maxLength}
        aria-label={ariaLabel || rest['aria-label']}
        autoComplete="new-password"
        spellCheck={false}
        data-lpignore="true"
        data-1p-ignore="true"
        style={inputStyle}
      />
      {showGenerator && (
        <button
          type="button"
          className="secondary-light sp-pw-btn"
          onClick={handleRollPassword}
          disabled={disabled}
          title={t('Generate random password')}
          aria-label={t('Generate random password')}
        >
          <Dices size={15} />
        </button>
      )}
      {showToggle && (
        <button
          type="button"
          className="secondary-light sp-pw-btn"
          onClick={handleToggleReveal}
          title={revealed ? t('Hide password') : t('Show password')}
          aria-label={revealed ? t('Hide password') : t('Show password')}
        >
          {revealed ? <EyeOff size={15} /> : <Eye size={15} />}
        </button>
      )}
      {showCopy && (
        <button
          type="button"
          className={`secondary-light sp-pw-btn${copied ? ' is-copied' : ''}`}
          onClick={handleCopy}
          disabled={!value}
          title={copied ? t('Copied!') : t('Copy password')}
          aria-label={copied ? t('Copied!') : t('Copy password')}
        >
          {copied ? <Check size={15} className="sp-copy-check" /> : <Copy size={15} />}
        </button>
      )}
    </div>
  );
}

export function PasswordResultBox({
  title,
  items = [],
  hint,
  onDismiss,
  className = '',
  style,
}) {
  const visibleItems = (items || []).filter(
    (item) => item && item.value !== undefined && item.value !== null && item.value !== ''
  );
  if (visibleItems.length === 0) return null;

  return (
    <div className={`info-box db-created-box sp-password-result ${className}`.trim()} style={style}>
      <div className="db-created-head">
        <strong className="sp-password-result-title">
          <CheckCircle size={15} className="sp-copy-check" />
          <span>{title || t('Credentials ready')}</span>
        </strong>
        {onDismiss && (
          <button
            type="button"
            className="mini secondary-light sp-result-close"
            onClick={onDismiss}
            title={t('Close')}
            aria-label={t('Close')}
          >
            <X size={13} />
          </button>
        )}
      </div>
      <div className="db-created-grid">
        {visibleItems.map((item, idx) => (
          <React.Fragment key={`${item.label}-${idx}`}>
            <label>{item.label}</label>
            <span className="sp-password-result-val">
              {item.code !== false ? <code>{item.value}</code> : <span>{item.value}</span>}
              {item.copyable !== false && (
                <CopyPasswordButton
                  value={item.value}
                  label={item.copyLabel || t('Copy')}
                  copiedLabel={t('Copied!')}
                  noticeMessage={item.noticeMessage}
                  size={12}
                />
              )}
            </span>
          </React.Fragment>
        ))}
      </div>
      {hint !== false && (
        <p className="hint sp-password-result-hint">
          {hint || t('Copy and save this password now — it will be hidden once you close this notice.')}
        </p>
      )}
    </div>
  );
}
