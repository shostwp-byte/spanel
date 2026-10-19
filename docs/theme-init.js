// Apply the stored theme before first paint so there is no flash.
//
// A file rather than an inline <script>: the panel's CSP is script-src 'self',
// which blocks inline code, so the inline copy never ran and a dark-mode
// user saw the light theme flash on every load.
(function () {
  try {
    var stored = localStorage.getItem('spanel-theme');
    var theme = stored === 'dark' || stored === 'light'
      ? stored
      : (window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
    document.documentElement.setAttribute('data-theme', theme);
    document.documentElement.style.colorScheme = theme;
  } catch (e) {}
})();
