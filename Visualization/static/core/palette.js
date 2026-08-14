// palette.js — colour as an answer to "is this item where it ends up?"
//
// 384 distinguishable hues is perceptually impossible; categorical colour tops out near 8-12.
// So an aisle's IDENTITY is not what the palette encodes. Ranked by the questions actually
// being asked of this warehouse:
//
//   1. Is this item home?          BINARY   -> chroma  (saturated = home, muted = misplaced)
//   2. Which region is it from?    ~13      -> hue     (the aisle FAMILY)
//   3. How high on the rack?       ordinal  -> lightness (dark at the floor, light at the top)
//   4. Exactly which aisle?        a LABEL, on hover. Never a colour.
//
// Chroma carries (1) because a chroma difference survives every colour-vision deficiency, and
// (1) is the read the convergence animation exists for.
//
// Colours are OKLCH: its lightness axis is perceptually uniform, so the shelf ramp reads the
// same under every hue. HSL would make a yellow aisle's ramp look nothing like a blue one's.
//
// KNOWN AND DELIBERATE: the largest family holds 88 of 384 aisles, so its members sit ~0.2
// degrees apart in hue — indistinguishable. That is not a bug. Widening the band would destroy
// the family read, which is the one that matters; at that density the distinguishing signal is
// stable screen position plus the label.

// 13 anchors, evenly spread and offset off pure red. Measured on the production warehouse:
// (handling_type, unit_type, storage_size) yields exactly 13 families across 384 aisles.
const N_ANCHORS = 13;
const ANCHOR_OFFSET = 25;
const BAND = 18;          // degrees a family's aisles fan across
const CHROMA = 0.16;
const L_LO = 0.32;
const L_HI = 0.86;

export const GREY = 'oklch(0.50 0 0)';          // no final home — never emitted by the formula
export const EMPTY = 'rgba(255,255,255,0.06)';  // an empty bin is never coloured

function hueFor(familyIndex, familyOrd, familySize) {
  const anchor = ANCHOR_OFFSET + (familyIndex % N_ANCHORS) * (360 / N_ANCHORS);
  const spread = familySize > 1 ? (familyOrd / (familySize - 1) - 0.5) : 0;
  return (anchor + BAND * spread + 360) % 360;
}

// Lightness divides by the aisle's OWN bay_y. That normalisation is the point: bay_y ranges
// 4..40 here, so an absolute Y/40 would render every 4-high aisle uniformly near-black and
// "top shelf" would mean something different in every aisle.
function lightFor(bayY, bayCount, home) {
  const frac = bayCount > 0 ? (bayY - 0.5) / bayCount : 0.5;
  const l = L_LO + (L_HI - L_LO) * Math.max(0, Math.min(1, frac));
  // A bottom-shelf MISPLACED item would otherwise be both near-black and near-grey, i.e.
  // indistinguishable from an empty bin. Floor it higher so it stays visible.
  return Math.max(home ? L_LO : 0.38, Math.min(L_HI, l));
}

/**
 * Build the colour function for one comparison ("authority") run.
 *
 * @param geometry  the authority run's aisles, each with family_index/family_ord/family_size
 * @param homes     final_home().homes — sku -> {aisle_id, bayY, home_aisles: [...]}
 */
export function makePalette(geometry, homes) {
  const byAisle = new Map(geometry.map((a) => [a.aisle_id, a]));

  /** Colour a bin currently holding `sku`, sitting in `aisleId`. */
  function forSku(sku, aisleId) {
    const home = homes ? homes[String(sku)] : null;
    if (!home) return GREY;
    const dest = byAisle.get(home.aisle_id);
    if (!dest) return GREY;
    // "Home" is membership of the aisle SET, not equality with one bin: 64% of SKUs hold
    // several bins and only 10% of those keep every replica in one aisle, so scoring against
    // the primary bin alone would paint most correctly-placed replicas as misplaced.
    const isHome = home.home_aisles.includes(aisleId) ? 1 : 0;
    const h = hueFor(dest.family_index, dest.family_ord, dest.family_size);
    const c = CHROMA * (0.55 + 0.45 * isHome);
    const l = lightFor(home.bayY, dest.bay_y || 1, isHome);
    return `oklch(${l.toFixed(3)} ${c.toFixed(3)} ${h.toFixed(1)})`;
  }

  /** Is this bin's occupant home? Drives the convergence percentage, not just the colour. */
  function isHome(sku, aisleId) {
    const home = homes ? homes[String(sku)] : null;
    return !!home && home.home_aisles.includes(aisleId);
  }

  /** Colour an AISLE by its own identity — used by the mini-map and the aisle headers. */
  function forAisle(aisleId) {
    const a = byAisle.get(aisleId);
    if (!a) return GREY;
    const h = hueFor(a.family_index, a.family_ord, a.family_size);
    return `oklch(0.62 ${CHROMA.toFixed(3)} ${h.toFixed(1)})`;
  }

  return { forSku, forAisle, isHome, hasHomes: !!homes && Object.keys(homes).length > 0 };
}

/** Sequential ramp for the non-home colour modes (fill, layout score, pick activity). */
export function ramp(v) {
  const x = Math.max(0, Math.min(1, v));
  return `oklch(${(0.30 + 0.55 * x).toFixed(3)} ${(0.04 + 0.12 * x).toFixed(3)} 250)`;
}

/** Diverging ramp for the compare view: -1 (base better) .. 0 .. +1 (comparison better). */
export function diverge(v) {
  const x = Math.max(-1, Math.min(1, v));
  const hue = x < 0 ? 25 : 155;
  return `oklch(${(0.85 - 0.35 * Math.abs(x)).toFixed(3)} ${(0.02 + 0.14 * Math.abs(x)).toFixed(3)} ${hue})`;
}
