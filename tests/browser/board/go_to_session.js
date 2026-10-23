const out = {}, check = (name, ok, detail) => { out[name] = ok ? "PASS" : "FAIL " + JSON.stringify(detail); };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const until = async (f, ms) => { const t0 = Date.now(); while (!f() && Date.now() - t0 < ms) await sleep(100); return f(); };
const errors = []; window.addEventListener("error", (e) => errors.push(e.message));
for (let k = 0; k < 40 && !(window.cy && cy.nodes().length); k++) await sleep(100);
await sleep(500);
const shown = (n) => !!n && getComputedStyle(n).display !== "none" && n.offsetParent !== null;
const btn = () => document.querySelector("#panel .sessionbox .goto"), live = () => document.querySelector("#panel .sessionbox .live");
select("f.8");
await until(() => btn() && !btn().hidden, 8000);
check("01 a running session shows Go to session, and where it runs", shown(btn()) && /running in Ghostty/.test(live().textContent), [btn() && btn().hidden, live() && live().textContent]);
select("f.7"); await sleep(3000);
check("02 a session that is not running shows no button; Copy resume command stays", !!btn() && !shown(btn()) && live().textContent === "" && [...document.querySelectorAll("#panel .sessionbox button")].some((b) => b.textContent === "Copy resume command"), [btn() && btn().hidden]);
const first = document.querySelector("#panel .sessionbox .row button");
check("03 the button sits first in its row", !!first && first.classList.contains("goto"), first && first.textContent);
select("f.9"); await sleep(3000);   // a hand-off whose session is not running but can be resumed: Open in VS Code, never clicked here
check("05 a stopped session with a transcript shows Open in VS Code, the hand-off's prompt going with it",
  shown(btn()) && btn().textContent === "Open in VS Code" && /Claude Code panel, with the hand-off's prompt/.test(btn().title) && live().textContent === "",
  [btn() && btn().hidden, btn() && btn().textContent, btn() && btn().title]);
select("f.8"); await until(() => btn() && btn().textContent === "Go to session", 8000);
check("06 a running session's button says Go to session", btn().textContent === "Go to session" && /window to the front/.test(btn().title), [btn().textContent, btn().title]);
check("04 no page errors", errors.length === 0, errors);
return JSON.stringify(out, null, 1);
