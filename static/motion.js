// xScanner motion + haptics. Pure vanilla. No-ops when the user turns
// effects off, when the OS asks for reduced motion, or when the Vibration
// API is missing (iPhone Safari and most desktops).
window.XS = (() => {
  const LS = 'xs_fx_v1';
  const reduced = typeof matchMedia === 'function' && matchMedia('(prefers-reduced-motion: reduce)').matches;
  const canVibrate = typeof navigator !== 'undefined' && typeof navigator.vibrate === 'function';
  const prefs = { motion: true, haptics: true };
  try { Object.assign(prefs, JSON.parse(localStorage.getItem(LS) || '{}')); } catch (_) {}

  function motionOn() { return !!prefs.motion && !reduced; }
  function hapticsOn() { return !!prefs.haptics; }

  function apply() {
    document.documentElement.classList.toggle('fx-off', !motionOn());
    document.documentElement.classList.toggle('hap-off', !hapticsOn());
    const m = document.getElementById('fxMotion');
    const h = document.getElementById('fxHap');
    if (m) {
      m.classList.toggle('on', motionOn());
      m.setAttribute('aria-pressed', motionOn() ? 'true' : 'false');
      m.disabled = !!reduced;
      m.title = reduced
        ? 'Motion is off because your system asked to reduce motion'
        : (prefs.motion ? 'Smooth motion on — click to turn off' : 'Smooth motion off — click to turn on');
    }
    if (h) {
      h.classList.toggle('on', hapticsOn());
      h.setAttribute('aria-pressed', hapticsOn() ? 'true' : 'false');
      h.classList.toggle('na', !canVibrate);
      h.title = canVibrate
        ? (hapticsOn() ? 'Haptic taps on — click to turn off' : 'Haptic taps off — click to turn on')
        : 'This device does not support vibration (iPhone Safari and most desktops do not)';
    }
  }

  function save() {
    try { localStorage.setItem(LS, JSON.stringify(prefs)); } catch (_) {}
    apply();
  }

  function vibrate(pattern) {
    if (!hapticsOn() || !canVibrate) return;
    try { navigator.vibrate(pattern); } catch (_) {}
  }

  const tap = () => vibrate(12);
  const ok = () => vibrate(14);
  const err = () => vibrate([18, 45, 18]);
  const tab = () => vibrate(8);

  function flash(el) {
    if (!el || !motionOn()) return;
    el.classList.remove('fx-flash');
    requestAnimationFrame(() => { el.classList.add('fx-flash'); });
    el.addEventListener('animationend', () => el.classList.remove('fx-flash'), { once: true });
  }

  function applyEnter(root, sel, row) {
    if (!motionOn() || !root) return;
    const els = [...root.querySelectorAll(sel)].filter(el => !el.classList.contains('skel-card')).slice(0, 24);
    els.forEach((el, i) => {
      el.classList.remove('fx-enter', 'fx-enter-row');
      el.style.setProperty('--d', (Math.min(i, 17) * 26) + 'ms');
    });
    requestAnimationFrame(() => {
      els.forEach(el => el.classList.add(row ? 'fx-enter-row' : 'fx-enter'));
    });
  }

  function tick(el, to, format, duration) {
    if (!el) return;
    const fmt = format || (v => String(v ?? '—'));
    const write = (v) => {
      const out = fmt(v);
      if (typeof out === 'string' && out.indexOf('<') >= 0) el.innerHTML = out;
      else el.textContent = out;
    };
    if (!motionOn() || to == null || !isFinite(+to)) { write(to); return; }
    const dest = +to;
    const from = el.dataset.v != null && isFinite(+el.dataset.v) ? +el.dataset.v : dest;
    el.dataset.v = String(dest);
    if (from === dest) { write(dest); return; }
    const dur = duration || 420;
    const t0 = performance.now();
    const step = (now) => {
      if (!el.isConnected) return;
      const p = Math.min(1, (now - t0) / dur);
      const e = 1 - Math.pow(1 - p, 3);
      write(from + (dest - from) * e);
      if (p < 1) requestAnimationFrame(step);
      else write(dest);
    };
    requestAnimationFrame(step);
  }

  function wire() {
    apply();
    const m = document.getElementById('fxMotion');
    const h = document.getElementById('fxHap');
    if (m) m.addEventListener('click', () => {
      if (reduced) return;
      prefs.motion = !prefs.motion;
      save();
    });
    if (h) h.addEventListener('click', () => {
      prefs.haptics = !prefs.haptics;
      save();
    });
    document.addEventListener('pointerdown', (e) => {
      if (e.pointerType === 'mouse' && e.button !== 0) return;
      const t = e.target.closest('button, .tcard, .ncard, #feed tbody tr[data-m], #heat tbody tr[data-m]');
      if (!t || t.disabled) return;
      tap();
    }, { passive: true });
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', wire);
  else wire();

  return { prefs, save, apply, motionOn, hapticsOn, canVibrate, reduced, tap, ok, err, tab, flash, applyEnter, tick };
})();
