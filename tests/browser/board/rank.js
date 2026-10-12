const out = {}, check = (name, ok, detail) => { out[name] = ok ? "PASS" : "FAIL " + JSON.stringify(detail); };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const until = async (f, ms) => { const t0 = Date.now(); while (!f() && Date.now() - t0 < ms) await sleep(100); return f(); };
const expected = "r.9 r.8 r.1 r.2 r.3 r.12 r.4 r.11 r.10".split(" ");
check("1 Next order follows the ranking", JSON.stringify(activeOrder()) === JSON.stringify(expected), activeOrder());
{ // within a priority, what frees Claude to work comes first, but never for a waiting task (the score comes from the server)
  const r3 = byId("r.3"), r11 = byId("r.11"), was = [r3.starts_claude, r11.starts_claude];
  r3.starts_claude = 9; r11.starts_claude = 9;
  const o = activeOrder();
  check("1b a task that frees Claude more ranks first within its priority; a waiting one does not", o[0] === "r.9" && o[1] === "r.3" && o.indexOf("r.11") === 7, o);
  [r3.starts_claude, r11.starts_claude] = was;
}
const rows = [...document.querySelectorAll(".todo div")].map((d) => d.textContent);
check("2 overview lists them in that order with the starts badges", rows.length === 9 && rows[1].includes("r.8") && rows[1].includes("Starts Claude's work") && rows[3].includes("r.2") && rows[3].split("Starts a hand-off").length === 3 && !rows[4].includes("Starts"), rows);
stepActive(1);
check("3 Next starts at the top", selectedId === "r.9" && el("nav-pos").textContent === "1 of 9", [selectedId, el("nav-pos").textContent]);
toggleDone("r.9");
await until(() => byId("r.9") && byId("r.9").done, 4000);
stepActive(1);
check("4 after ticking it, Next goes to the next one", selectedId === "r.8", selectedId);
toggleDone("r.8");
await until(() => byId("r.8") && byId("r.8").done, 4000);
stepActive(1);
check("5 and again", selectedId === "r.1", selectedId);
return JSON.stringify(out, null, 1);
