const out = {}, check = (name, ok, detail) => { out[name] = ok ? "PASS" : "FAIL " + JSON.stringify(detail); };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const until = async (f, ms) => { const t0 = Date.now(); while (!f() && Date.now() - t0 < ms) await sleep(100); return f(); };
const errors = []; window.addEventListener("error", (e) => errors.push(e.message));
const det = () => document.getElementById("detail");
const rowOf = (id) => [...document.querySelectorAll("#tasks .todo div, #projects .running div")].find((r) => r.querySelector(".id").textContent === id);
await until(() => rowOf("al.1"), 6000);
rowOf("al.1").click(); await until(() => det().querySelector("h2") && det().querySelector("h2").textContent === "First review", 3000);
const shown = (n) => !!n && getComputedStyle(n).display !== "none" && n.offsetParent !== null;
const btn = () => det().querySelector(".sessionbox .goto"), live = () => det().querySelector(".sessionbox .live");
await until(() => btn() && !btn().hidden, 8000);
check("01 the hub's details show Go to session for a running session", shown(btn()) && /running in Ghostty/.test(live().textContent), [btn() && btn().hidden, live() && live().textContent]);
check("03 a running session's button says Go to session", btn().textContent === "Go to session", btn().textContent);
rowOf("al.2").click(); await until(() => det().querySelector("h2") && det().querySelector("h2").textContent === "Hand-off", 3000);
await sleep(2500);   // the hand-off's session is not running, but its transcript is there: Open in VS Code, never clicked here
check("04 a stopped hand-off session shows Open in VS Code, with its prompt", shown(btn()) && btn().textContent === "Open in VS Code" &&
  /with the hand-off's prompt/.test(btn().title) && live().textContent === "", [btn() && btn().hidden, btn() && btn().textContent, btn() && btn().title]);
check("02 no page errors", errors.length === 0, errors);
return JSON.stringify(out, null, 1);
