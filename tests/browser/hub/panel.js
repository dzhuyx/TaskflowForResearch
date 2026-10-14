const out = {}, check = (name, ok, detail) => { out[name] = ok ? "PASS" : "FAIL " + JSON.stringify(detail); };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const until = async (f, ms) => { const t0 = Date.now(); while (!f() && Date.now() - t0 < ms) await sleep(100); return f(); };
const errors = []; window.addEventListener("error", (e) => errors.push(e.message));
const det = () => document.getElementById("detail");
const rowOf = (id) => [...document.querySelectorAll("#tasks .todo div, #projects .running div")].find((r) => r.querySelector(".id") && r.querySelector(".id").textContent === id);
const enter = (n) => n.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
// the details' sections, top to bottom, named as the board's panel (renderItem in static/index.html) has them
const KEY = (n) => n.matches("div.dmeta") ? "meta" : n.matches("label.donebox") ? "tick" : n.matches("textarea.outcome") ? "outcome" : n.matches("div.badges") ? "badges"
  : n.matches("div.seg") ? "status" : n.matches("div.actions") ? "actions" : n.matches("div.checklist") ? "checklist" : n.matches("h3") ? "h3 " + n.textContent
  : n.matches("#t-note") ? "note" : n.matches("div.row") ? "row " + n.textContent : n.matches("div.msg") ? "msg" : n.matches("div.runbox") ? "run"
  : n.matches("div.sessionbox") ? "session" : n.matches("dl.facts") ? "facts " + [...n.querySelectorAll("dt")].map((d) => d.textContent).join("/")
  : n.matches("details.line") ? "line" : "? " + n.tagName;
const order = () => [...det().children].map(KEY).join(", ");
const pressed = () => { const b = det().querySelector(".seg button[aria-pressed='true']"); return b ? b.textContent : ""; };
const dd = (name) => { const t = [...det().querySelectorAll("dl.facts dt")].find((d) => d.textContent === name); return t ? t.nextElementSibling : null; };
const notes = () => (dd("Notes") ? dd("Notes").textContent : "");
const steps = () => [...det().querySelectorAll(".checklist .step span")].map((s) => s.textContent).join("|");
const needs = () => [...dd("Needs").querySelectorAll(".chip")].map((c) => (c.textContent.match(/[a-z]{2}\.\d+/) || [""])[0]).join(",");
const msg = () => (det().querySelector(".msg") ? det().querySelector(".msg").textContent : "");

await until(() => rowOf("al.1"), 6000);
rowOf("al.1").click(); await until(() => det().querySelector("h2") && det().querySelector("h2").textContent === "First review", 3000);
const want = "meta, tick, outcome, badges, status, actions, checklist, h3 Add a note, note, row Add note, msg, facts Needs/Needed by/Documents/Notes, line";
check("01 the details have the board's sections, in its order", order() === want, order());
check("02 the status switch shows Open", pressed() === "Open", pressed());

[...det().querySelectorAll(".seg button")].find((b) => b.textContent === "Waiting").click();
await until(() => pressed() === "Waiting", 5000);
check("03 Waiting set from the hub is saved", pressed() === "Waiting" && msg().startsWith("Saved to alpha_tracker"), [pressed(), msg()]);

det().querySelector("#t-note").value = "Asked about section 3";
[...det().querySelectorAll("div.row button")].find((b) => b.textContent === "Add note").click();
await until(() => notes().includes("Asked about section 3"), 5000);
check("04 a note from the hub is saved as a dated line, and its box emptied", /\d{4}-\d{2}-\d{2}: Asked about section 3/.test(notes()) && det().querySelector("#t-note").value === "", notes());

det().querySelector("#i-step").value = "Check the figures"; enter(det().querySelector("#i-step"));
await until(() => steps().includes("Check the figures"), 5000);
check("05 a step added from the hub", steps() === "Outline the comments|Send them|Check the figures" && det().querySelector(".checklist .chead").textContent.includes(" of 3 done"), steps());

[...det().querySelectorAll(".checklist .steprow")].find((r) => r.textContent.includes("Check the figures")).querySelector("button.x").click();
await until(() => !steps().includes("Check the figures"), 5000);
check("06 and removed with its ×", steps() === "Outline the comments|Send them", steps());

det().querySelector("#i-needs").value = "al.5"; enter(det().querySelector("#i-needs"));
await until(() => needs() === "al.5", 5000);
check("07 a prerequisite added from the hub", needs() === "al.5", needs());

dd("Needs").querySelector(".chip button.x").click();
await until(() => needs() === "", 5000);
check("08 and removed with its ×", needs() === "", needs());

det().querySelector("#t-note").value = "half-typed note"; det().querySelector("#t-note").focus();
lastFull = 0; await poll(); await sleep(400);
check("09 a minute's refresh keeps a half-typed note", det().querySelector("#t-note").value === "half-typed note" && document.activeElement === det().querySelector("#t-note"), det().querySelector("#t-note").value);
check("10 no page errors", errors.length === 0, errors);
return JSON.stringify(out, null, 1);
