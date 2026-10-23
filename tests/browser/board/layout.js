const out = {}, check = (name, ok, detail) => { out[name] = ok ? "PASS" : "FAIL " + JSON.stringify(detail); };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const errors = []; window.addEventListener("error", (e) => errors.push(e.message));
for (let k = 0; k < 40 && !(window.cy && cy.nodes().length); k++) await sleep(100);
const P = (id) => Object.assign({}, cy.getElementById(id).position());
const shownN = () => cy.nodes().filter((n) => !n.hasClass("hidden"));
const pairs = () => { const ns = shownN().toArray(), bad = []; ns.forEach((a, i) => ns.slice(i + 1).forEach((b) => { if (Math.abs(a.position("x") - b.position("x")) < 210 && Math.abs(a.position("y") - b.position("y")) < 72) bad.push(a.id() + "/" + b.id()); })); return bad; };
const snap = (ns) => Object.fromEntries((ns || cy.nodes()).map((n) => [n.id(), P(n.id())]));
const same = (a, b, ids) => ids.every((id) => Math.abs(a[id].x - b[id].x) < 0.01 && Math.abs(a[id].y - b[id].y) < 0.01);
const away = (id) => { const o = shownN().filter((n) => n.id() !== id), bb = o.boundingBox(), p = P(id); return Math.max(0, bb.x1 - p.x, p.x - bb.x2, bb.y1 - p.y, p.y - bb.y2); };
const setDone = (on) => { el("f-done").checked = on; el("f-done").dispatchEvent(new Event("change")); };
const stored = () => { try { return JSON.parse(localStorage.getItem(posKey()) || "{}"); } catch (e) { return {}; } };
check("01 first open: the visible boxes do not overlap, and the board is tidy", pairs().length === 0 && tidy === true && stored()["#tidy"] === true, [pairs(), tidy]);
check("02 the side group and the standalone boxes sit next to the main group (within 2 rows or columns)", ["g.1", "g.2", "s.1", "s.2"].every((id) => away(id) <= 2 * 94 + 1), ["g.1", "g.2", "s.1", "s.2"].map((id) => [id, Math.round(away(id))]));
check("03 arrows run left to right", cy.edges().filter((e) => !e.source().hasClass("hidden") && !e.target().hasClass("hidden")).every((e) => e.source().position("x") < e.target().position("x")), "");
const openLayout = snap(shownN());
setDone(true);
check("04 show done on a tidy board: laid out afresh, no overlaps, done boxes left of what needs them", pairs().length === 0 && ["f.1", "f.2", "f.3", "f.4", "f.5", "f.6"].every((id) => cy.getElementById(id).outgoers("node").every((d) => P(id).x < d.position("x"))), pairs());
check("05 the done standalone box joins the grid, near the rest", away("s.3") <= 2 * 94 + 1, Math.round(away("s.3")));
setDone(false);
check("06 hiding them again gives back the same layout (it is deterministic)", same(openLayout, snap(), Object.keys(openLayout)), "");
const g2 = cy.getElementById("g.2"); g2.position({ x: P("g.2").x + 40, y: P("g.2").y + 30 }); g2.emit("dragfree");
check("07 dragging a box marks the board arranged by hand", tidy === false && !stored()["#tidy"], [tidy, stored()["#tidy"]]);
let before = snap(shownN());
setDone(true);
check("08 show done on a board arranged by hand: what was on show stays put, nothing overlaps", same(before, snap(), Object.keys(before)) && pairs().length === 0, pairs());
check("09 the shown done boxes go left of what needs them", cy.getElementById("f.6").outgoers("node").every((d) => P("f.6").x < d.position("x")), [P("f.6"), P("f.7")]);
setDone(false);
cy.zoom({ level: 1.6, renderedPosition: { x: 200, y: 200 } }); const z = cy.zoom(), pan = Object.assign({}, cy.pan());
const st = JSON.parse(JSON.stringify(state));
const mk = (id, needs, title) => Object.assign(JSON.parse(JSON.stringify(st.items.find((i) => i.id === "f.10"))), { id, needs, title, needed_by: [], done: false, ready: false, line: 99 });
st.items.push(mk("n.1", ["f.10"], "Results review A"), mk("n.2", ["f.10"], "Results review B"), mk("n.3", ["f.9"], "Hand-off 2"), mk("n.4", ["n.3"], "Gated work"), mk("n.5", [], "New standalone"));
st.hash = "x"; before = snap(shownN()); render(st);
check("10 live update: new items with one prerequisite get separate spots right of it; a new chain lines up", P("n.1").x > P("f.10").x && P("n.2").x > P("f.10").x && P("n.4").x > P("n.3").x && P("n.3").x > P("f.9").x, [P("n.1"), P("n.2"), P("n.3"), P("n.4")]);
check("11 a new standalone item lands next to the board, not far away", away("n.5") <= 94 + 1, Math.round(away("n.5")));
check("12 nothing on show overlaps; existing boxes did not move; the view is kept", pairs().length === 0 && same(before, snap(), Object.keys(before)) && cy.zoom() === z && cy.pan().x === pan.x, [pairs(), cy.zoom()]);
el("b-layout").click();
check("13 Re-layout makes the board tidy again, with no overlaps", tidy === true && stored()["#tidy"] === true && pairs().length === 0, [tidy, pairs()]);
// untangle counts only what a swap can change; recounting the whole group with tangle() for every swap must give the same
// arrangement (a swap kept when tangle() drops by more than rounding), on random groups as dagre leaves them
const recount = (pts, es, score) => {
  const cols = new Map();
  Object.keys(pts).forEach((id) => { const x = Math.round(pts[id].x); if (!cols.has(x)) cols.set(x, []); cols.get(x).push(id); });
  const lists = [...cols.values()].filter((c) => c.length > 1).map((c) => c.sort((a, b) => pts[a].y - pts[b].y));
  for (let pass = 0, better = true; better && score > 0 && pass < 4; pass++) {
    better = false;
    for (const col of lists) for (let i = 0; i + 1 < col.length; i++) {
      const a = col[i], b = col[i + 1], ya = pts[a].y, yb = pts[b].y;
      pts[a].y = yb; pts[b].y = ya;
      const t = tangle(pts, es);
      if (t < score - 1e-9) { score = t; col[i] = b; col[i + 1] = a; better = true; } else { pts[a].y = ya; pts[b].y = yb; }
    }
  }
};
let seed = 11, unlike = [], swapped = 0;
const rnd = () => { seed = (seed * 16807) % 2147483647; return seed / 2147483647; };
for (let g = 0; g < 200; g++) {
  const pts = {}, cols = [], es = [], seen = new Set(), ncol = 2 + Math.floor(rnd() * 6);
  for (let c = 0; c < ncol; c++) { cols.push([]); for (let r = 0, n = 1 + Math.floor(rnd() * 7); r < n; r++) { pts[c + "_" + r] = { x: c * COL_STEP, y: r * ROW_STEP }; cols[c].push(c + "_" + r); } }
  for (let c = 0; c + 1 < ncol; c++) for (const a of cols[c]) for (let m = 0, k = 1 + Math.floor(rnd() * 3); m < k; m++) {
    const c2 = Math.min(ncol - 1, c + 1 + Math.floor(rnd() * rnd() * 3)), b = cols[c2][Math.floor(rnd() * cols[c2].length)];
    if (!seen.has(a + ">" + b)) { seen.add(a + ">" + b); es.push([a, b]); }
  }
  const p1 = JSON.parse(JSON.stringify(pts)), p2 = JSON.parse(JSON.stringify(pts)), score = tangle(pts, es);
  untangle(p1, es, score); recount(p2, es, score);
  if (Object.keys(pts).some((id) => p1[id].y !== p2[id].y)) unlike.push(g);
  if (Object.keys(pts).some((id) => p1[id].y !== pts[id].y)) swapped++;
}
check("14 untangling counts a swap as recounting the whole group does (200 random groups, " + swapped + " rearranged)", unlike.length === 0 && swapped > 50, unlike);
check("15 no page errors", errors.length === 0, errors);
return JSON.stringify(out, null, 1);
