const out = {}, box = document.getElementById("cy"), canvas = box.querySelector("canvas") || box;
const r = box.getBoundingClientRect();
out.ready = !!cy && cy.nodes().length;
out.userZooming = cy.userZoomingEnabled(); out.userPanning = cy.userPanningEnabled();
out.gestureEventType = typeof GestureEvent; out.cyTop = r.top;
const near = (a, b) => Math.abs(a - b) < 1e-6 * Math.max(1, Math.abs(b));
function reset() { cy.zoom(1); cy.pan({ x: 100, y: 100 }); }
function wheel(opts, target) {
  const ev = new WheelEvent("wheel", Object.assign({ bubbles: true, cancelable: true, clientX: r.left + 300, clientY: r.top + 200 }, opts));
  (target || canvas).dispatchEvent(ev); return ev;
}
function gesture(type, scale) {
  const ev = new Event(type, { bubbles: true, cancelable: true }); Object.defineProperty(ev, "scale", { value: scale });
  canvas.dispatchEvent(ev); return ev;
}
const modelAt = (rx, ry) => ({ x: (rx - cy.pan().x) / cy.zoom(), y: (ry - cy.pan().y) / cy.zoom() });
const check = (name, ok, detail) => { out[name] = ok ? "PASS" : "FAIL " + JSON.stringify(detail); };

reset(); let ev = wheel({ deltaY: 100 });
check("1 two-finger/wheel down pans up, no zoom", cy.pan().y === 0 && cy.pan().x === 100 && cy.zoom() === 1 && ev.defaultPrevented, [cy.pan(), cy.zoom(), ev.defaultPrevented]);
reset(); wheel({ deltaX: 50, deltaY: -30 });
check("2 diagonal scroll pans both ways", cy.pan().x === 50 && cy.pan().y === 130 && cy.zoom() === 1, [cy.pan(), cy.zoom()]);
reset(); wheel({ deltaY: 40, shiftKey: true });
check("3 shift-wheel pans sideways", cy.pan().x === 60 && cy.pan().y === 100, cy.pan());
reset(); wheel({ deltaY: 3, deltaMode: 1 });
check("4 line-mode wheel (3 lines) pans 48px", cy.pan().y === 52, cy.pan());
reset(); const ay = Math.trunc(r.top + 200) - r.top; let before = modelAt(300, ay); wheel({ deltaY: -10, ctrlKey: true }); let after = modelAt(300, ay);
check("5 ctrl-wheel (Chrome pinch) zooms in about cursor", near(cy.zoom(), Math.exp(0.1)) && near(before.x, after.x) && near(before.y, after.y), [cy.zoom(), before, after]);
reset(); wheel({ deltaY: 10, metaKey: true });
check("6 cmd-wheel zooms out", near(cy.zoom(), Math.exp(-0.1)), cy.zoom());
reset(); wheel({ deltaY: 500, ctrlKey: true });
check("7 one big ctrl-wheel step is capped", near(cy.zoom(), Math.exp(-0.5)), cy.zoom());
reset(); canvas.dispatchEvent(new MouseEvent("mousemove", { bubbles: true, clientX: r.left + 400, clientY: r.top + 250 }));
const gy = Math.trunc(r.top + 250) - r.top; before = modelAt(400, gy); let gs = gesture("gesturestart", 1); gesture("gesturechange", 1.5); let gc = gesture("gesturechange", 2); after = modelAt(400, gy);
check("8 gesture pinch zooms x2 about pointer", near(cy.zoom(), 2) && near(before.x, after.x) && near(before.y, after.y) && gs.defaultPrevented && gc.defaultPrevented, [cy.zoom(), before, after]);
wheel({ deltaY: -20, ctrlKey: true });
check("9 ctrl-wheel during a gesture pinch is ignored", near(cy.zoom(), 2), cy.zoom());
gesture("gesturechange", 100);
check("10 zoom capped at maxZoom 2.5", cy.zoom() === 2.5, cy.zoom());
gesture("gesturechange", 0.0001);
check("11 zoom floored at minZoom 0.12", cy.zoom() === 0.12, cy.zoom());
gesture("gestureend", 0.0001); reset(); wheel({ deltaY: -10, ctrlKey: true });
check("12 ctrl-wheel zooms again after gestureend", near(cy.zoom(), Math.exp(0.1)), cy.zoom());
reset(); ev = wheel({ deltaY: 100 }, document.getElementById("panel"));
check("13 scrolling over the side panel leaves the board alone", cy.pan().y === 100 && !ev.defaultPrevented, [cy.pan(), ev.defaultPrevented]);
// mouse drag on empty background still pans (Cytoscape's own handling)
reset(); const bg = { x: r.left + 5, y: r.top + r.height - 5 };
out.bgIsEmpty = cy.nodes().every((n) => { const b = n.renderedBoundingBox(); return !(5 >= b.x1 && 5 <= b.x2 && r.height - 5 >= b.y1 && r.height - 5 <= b.y2); });
canvas.dispatchEvent(new MouseEvent("mousedown", { bubbles: true, button: 0, buttons: 1, clientX: bg.x, clientY: bg.y }));
for (let k = 1; k <= 5; k++) window.dispatchEvent(new MouseEvent("mousemove", { bubbles: true, buttons: 1, clientX: bg.x + 20 * k, clientY: bg.y - 10 * k }));
window.dispatchEvent(new MouseEvent("mouseup", { bubbles: true, button: 0, clientX: bg.x + 100, clientY: bg.y - 50 }));
check("14 mouse drag on background still pans", Math.abs(cy.pan().x - 100) > 30 && cy.zoom() === 1, [cy.pan(), cy.zoom()]);

// ---- keys, cursor, hint ----
const key = (k, opts, target) => { const ev = new KeyboardEvent("keydown", Object.assign({ key: k, bubbles: true, cancelable: true }, opts)); (target || document.body).dispatchEvent(ev); return ev; };
const cx = cy.width() / 2, cyy = cy.height() / 2;
reset(); before = modelAt(cx, cyy); key("+"); after = modelAt(cx, cyy);
check("15 + key zooms in x1.25 about the centre", near(cy.zoom(), 1.25) && near(before.x, after.x) && near(before.y, after.y), [cy.zoom(), before, after]);
reset(); key("="); check("16 = key (unshifted +) zooms in", near(cy.zoom(), 1.25), cy.zoom());
reset(); key("-"); check("17 - key zooms out", near(cy.zoom(), 0.8), cy.zoom());
reset(); key("_"); check("18 _ key (shifted -) zooms out", near(cy.zoom(), 0.8), cy.zoom());
reset(); key("+", { metaKey: true }); check("19 cmd-+ leaves the board zoom alone", cy.zoom() === 1, cy.zoom());
reset(); const sb = document.getElementById("f-search"); sb.focus(); key("+", {}, sb); sb.blur();
check("20 + typed in the search box does not zoom", cy.zoom() === 1, cy.zoom());
reset(); for (let k = 0; k < 30; k++) key("+"); check("21 + key stops at maxZoom", cy.zoom() === 2.5, cy.zoom());
reset();
out.cssCursor = getComputedStyle(box).cursor;
check("22 hand cursor on the background", getComputedStyle(box).cursor === "grab" && box.style.cursor === "", [getComputedStyle(box).cursor, box.style.cursor]);
canvas.dispatchEvent(new MouseEvent("mousedown", { bubbles: true, button: 0, buttons: 1, clientX: bg.x, clientY: bg.y }));
const during = getComputedStyle(box).cursor;
window.dispatchEvent(new MouseEvent("mouseup", { bubbles: true, button: 0, clientX: bg.x, clientY: bg.y }));
check("23 closed hand while pressing the background, open hand after", during === "grabbing" && getComputedStyle(box).cursor === "grab", [during, getComputedStyle(box).cursor]);
const n1 = cy.nodes()[0].renderedPosition();
canvas.dispatchEvent(new MouseEvent("mousedown", { bubbles: true, button: 0, buttons: 1, clientX: bg.x, clientY: bg.y }));
window.dispatchEvent(new MouseEvent("mouseup", { bubbles: true, button: 0, clientX: r.left + n1.x, clientY: r.top + n1.y }));
check("24 releasing over a box shows the pointing hand", getComputedStyle(box).cursor === "pointer", getComputedStyle(box).cursor);
box.style.cursor = "";
const n2 = cy.nodes()[1].renderedPosition();
canvas.dispatchEvent(new MouseEvent("mousedown", { bubbles: true, button: 0, buttons: 1, clientX: r.left + n2.x, clientY: r.top + n2.y }));
check("25 pressing a box does not show the closed hand", getComputedStyle(box).cursor !== "grabbing", getComputedStyle(box).cursor);
window.dispatchEvent(new MouseEvent("mouseup", { bubbles: true, button: 0, clientX: r.left + n2.x, clientY: r.top + n2.y }));
select(null);
check("26 overview explains moving and zooming", document.getElementById("panel").textContent.includes("Zoom with a pinch, ⌘-scroll, or the + and − keys"), "");

out.consoleNote = "done";
return JSON.stringify(out, null, 1);
