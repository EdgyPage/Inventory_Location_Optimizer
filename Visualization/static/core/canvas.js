// canvas.js — DPR-correct sizing, and the primitives every view draws with.
//
// One rule: a view never touches canvas.width/height itself. `fit()` owns the device-pixel
// scaling so a 150x40 bin grid stays crisp on a retina display without every view repeating
// the arithmetic (and getting the hit-testing subtly wrong).

export function fit(canvas) {
  const dpr = window.devicePixelRatio || 1;
  const rect = canvas.getBoundingClientRect();
  const w = Math.max(1, Math.floor(rect.width));
  const h = Math.max(1, Math.floor(rect.height));
  if (canvas.width !== w * dpr || canvas.height !== h * dpr) {
    canvas.width = w * dpr;
    canvas.height = h * dpr;
  }
  const ctx = canvas.getContext('2d');
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, w, h);
  return { ctx, w, h };
}

/** CSS-pixel position of a pointer event, matching what `fit` draws in. */
export function pointer(canvas, ev) {
  const r = canvas.getBoundingClientRect();
  return { x: ev.clientX - r.left, y: ev.clientY - r.top };
}

export function rect(ctx, x, y, w, h, fill) {
  ctx.fillStyle = fill;
  ctx.fillRect(x, y, w, h);
}

export function stroke(ctx, x, y, w, h, color, width = 1) {
  ctx.strokeStyle = color;
  ctx.lineWidth = width;
  ctx.strokeRect(x + width / 2, y + width / 2, w - width, h - width);
}

// Canvas fillStyle does NOT resolve CSS custom properties — `var(--fg)` silently becomes
// transparent black, i.e. invisible on a dark ground. Every colour here must be a literal.
export const FG = '#e6e9ef';

export function text(ctx, s, x, y, {
  color = FG, size = 11, align = 'left', baseline = 'top', weight = 400,
} = {}) {
  ctx.fillStyle = color;
  ctx.font = `${weight} ${size}px ui-monospace, SFMono-Regular, Menlo, monospace`;
  ctx.textAlign = align;
  ctx.textBaseline = baseline;
  ctx.fillText(s, x, y);
}

export function dot(ctx, x, y, r, fill) {
  ctx.beginPath();
  ctx.arc(x, y, r, 0, Math.PI * 2);
  ctx.fillStyle = fill;
  ctx.fill();
}

/** Manhattan path between two bins — pickers move along x, then y. */
export function manhattan(ctx, a, b, color, alpha = 0.7) {
  ctx.save();
  ctx.globalAlpha = alpha;
  ctx.strokeStyle = color;
  ctx.lineWidth = 1.5;
  ctx.beginPath();
  ctx.moveTo(a.x, a.y);
  ctx.lineTo(b.x, a.y);
  ctx.lineTo(b.x, b.y);
  ctx.stroke();
  ctx.restore();
}

export function centred(ctx, w, h, msg) {
  text(ctx, msg, w / 2, h / 2, { color: 'rgba(255,255,255,0.45)', size: 13, align: 'center',
    baseline: 'middle' });
}

/**
 * Bin geometry for one aisle drawn into a rect.
 *
 * bayY counts UP from the floor, so it is flipped on screen: level 1 draws at the bottom, which
 * is what makes the dark-to-light lightness ramp read as "low shelf to high shelf".
 */
export function binGrid(box, bayX, bayY) {
  const cw = box.w / Math.max(1, bayX);
  const ch = box.h / Math.max(1, bayY);
  return {
    cw,
    ch,
    at(x, y) {
      return { x: box.x + (x - 1) * cw, y: box.y + (bayY - y) * ch, w: cw, h: ch };
    },
    hit(px, py) {
      const cx = Math.floor((px - box.x) / cw) + 1;
      const cy = bayY - Math.floor((py - box.y) / ch);
      if (cx < 1 || cx > bayX || cy < 1 || cy > bayY) return null;
      return { bayX: cx, bayY: cy };
    },
  };
}
