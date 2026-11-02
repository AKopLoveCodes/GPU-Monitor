// Apply appearance settings before the page paints, and wire the appearance menu.
(() => {
  const themeKey = 'gpu-monitor-theme';
  const opacityKey = theme => `gpu-monitor-wallpaper-opacity-${theme}`;
  const defaults = { light: 36, dark: 42 };
  const validTheme = value => value === 'dark' ? 'dark' : 'light';
  const opacityFor = theme => {
    try {
      const saved = localStorage.getItem(opacityKey(theme));
      if (saved !== null) {
        const value = Number(saved);
        if (Number.isFinite(value)) return Math.max(0, Math.min(100, Math.round(value)));
      }
    } catch { /* Storage may be disabled. */ }
    return defaults[theme];
  };

  let theme = 'light';
  try { theme = validTheme(localStorage.getItem(themeKey)); } catch { /* Storage may be disabled. */ }
  const queryTheme = new URLSearchParams(location.search).get('theme');
  if (queryTheme) theme = validTheme(queryTheme);
  const apply = opacity => {
    document.documentElement.dataset.theme = theme;
    const base = theme === 'dark' ? '13, 13, 13' : '255, 255, 255';
    document.documentElement.style.setProperty('--wallpaper-tint', `rgba(${base}, ${1 - opacity / 100})`);
  };
  apply(opacityFor(theme));

  // Wallpapers are optional user-supplied files; without them the tint sits on a plain background.
  const wallpaper = { light: false, dark: false };
  const syncWallpaper = () => {
    const present = wallpaper[theme];
    document.documentElement.dataset.wallpaper = present ? 'on' : 'off';
    for (const id of ['wallpaper-row', 'wallpaper-opacity', 'wallpaper-scale']) {
      const node = document.getElementById(id);
      if (node) node.hidden = !present;
    }
  };
  for (const name of ['light', 'dark']) {
    const probe = new Image();
    probe.onload = () => { wallpaper[name] = true; syncWallpaper(); };
    probe.src = `/assets/background-${name}.jpg`;
  }

  document.addEventListener('DOMContentLoaded', () => {
    const selector = document.getElementById('theme-select');
    const language = document.getElementById('language-select');
    const slider = document.getElementById('wallpaper-opacity');
    const output = document.getElementById('wallpaper-opacity-value');
    const menu = document.getElementById('appearance-menu');
    const sync = () => {
      const value = opacityFor(theme);
      slider.value = value;
      output.value = `${value}%`;
      slider.setAttribute('aria-valuetext', `${value}%`);
      apply(value);
      syncWallpaper();
    };

    selector.value = theme;
    language.value = I18N.currentLang();
    sync();
    selector.addEventListener('change', () => {
      theme = validTheme(selector.value);
      try { localStorage.setItem(themeKey, theme); } catch { /* Theme still switches. */ }
      sync();
    });
    language.addEventListener('change', () => I18N.setLang(language.value));
    slider.addEventListener('input', () => {
      const value = Math.max(0, Math.min(100, Number(slider.value) || 0));
      output.value = `${value}%`;
      slider.setAttribute('aria-valuetext', `${value}%`);
      apply(value);
      try { localStorage.setItem(opacityKey(theme), String(value)); } catch { /* Slider still works. */ }
    });
    document.addEventListener('click', event => {
      if (menu.open && !menu.contains(event.target)) menu.open = false;
    });
    document.addEventListener('keydown', event => {
      if (event.key === 'Escape') menu.open = false;
    });
  });
})();
