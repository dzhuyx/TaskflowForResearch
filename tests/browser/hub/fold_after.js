const out = {}, check = (name, ok, detail) => { out[name] = ok ? "PASS" : "FAIL " + JSON.stringify(detail); };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const until = async (f, ms) => { const t0 = Date.now(); while (!f() && Date.now() - t0 < ms) await sleep(100); return f(); };
await until(() => document.querySelectorAll("#tasks h2").length, 4000);
const exp = () => ["me", "handoff", "other"].map((k) => document.querySelector(`#tasks button.fold[data-group="${k}"]`).getAttribute("aria-expanded"));
check("01 a newly opened hub page shows the user's list still folded", JSON.stringify(exp()) === JSON.stringify(["false", "true", "true"]) && document.querySelectorAll("#tasks .todo div").length === 2, [exp(), document.querySelectorAll("#tasks .todo div").length]);
return JSON.stringify(out, null, 1);
