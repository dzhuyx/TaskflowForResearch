const out = {}, check = (name, ok, detail) => { out[name] = ok ? "PASS" : "FAIL " + JSON.stringify(detail); };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const until = async (f, ms) => { const t0 = Date.now(); while (!f() && Date.now() - t0 < ms) await sleep(100); return f(); };
const errors = []; window.addEventListener("error", (e) => errors.push(e.message));
const pr = () => document.getElementById("progress");
if (!document.getElementById("main").classList.contains("pv")) document.getElementById("b-progress").click();
await until(() => pr().querySelector("figure.heat"), 4000);
if (pf.table) { [...pr().querySelectorAll(".pfilters button")][0].click(); }
const figs = () => [...pr().querySelectorAll("figure.heat")];
const cellOf = (f, d) => f.querySelector(`rect.cell[data-day="${d}"]`);
check("01 three calendars: You, Claude, Combined", JSON.stringify(figs().map((f) => f.querySelector("b").textContent)) === JSON.stringify(["You", "Claude", "Combined"]), figs().length);
const cells = [...figs()[0].querySelectorAll("rect.cell")], first = cells[0].dataset.day, last = cells[cells.length - 1].dataset.day;
check("02 whole weeks from a Sunday, ending today", dayOf(first).getDay() === 0 && last === prog.today && cells.length === hdaysLen(), [first, last, cells.length]);
function hdaysLen() { return dayRange(first, prog.today).length; }
check("03 at most 53 weeks", cells.length <= 53 * 7, cells.length);
const n = (f, d) => cellOf(f, d) && [cellOf(f, d).dataset.n, cellOf(f, d).getAttribute("fill")];
check("04 You: Oct 1 one task (lightest), Oct 2 three (second step)", JSON.stringify([n(figs()[0], "2026-10-01"), n(figs()[0], "2026-10-02")]) === JSON.stringify([["1", "#86b6ef"], ["3", "#3987e5"]]), [n(figs()[0], "2026-10-01"), n(figs()[0], "2026-10-02")]);
check("05 Claude: Oct 1 none (empty), Oct 2 one", JSON.stringify([n(figs()[1], "2026-10-01"), n(figs()[1], "2026-10-02")]) === JSON.stringify([["0", "#f0efec"], ["1", "#e7a28b"]]), [n(figs()[1], "2026-10-01"), n(figs()[1], "2026-10-02")]);
check("06 Combined: Oct 1 one, Oct 2 four", JSON.stringify([n(figs()[2], "2026-10-01"), n(figs()[2], "2026-10-02")]) === JSON.stringify([["1", "#b3b2af"], ["4", "#888783"]]), [n(figs()[2], "2026-10-01"), n(figs()[2], "2026-10-02")]);
const caps = figs().map((f) => [...f.querySelectorAll("figcaption > span")].map((s) => s.textContent));
check("07 captions: totals, active days and streaks", JSON.stringify(caps) === JSON.stringify([
  ["your tasks and hand-offs", "4 finished", "2 days active · current streak 0 days · longest 2 days"],
  ["its own tasks", "1 finished", "1 day active · current streak 0 days · longest 1 day"],
  ["every finished task", "5 finished", "2 days active · current streak 0 days · longest 2 days"]]), caps);
const legend = [...figs()[0].querySelectorAll(".lg > span")].map((s) => s.textContent);
check("08 legend shows the bins", JSON.stringify(legend) === JSON.stringify(["0", "1–2", "3–5", "6–9", "10+"]), legend);
cellOf(figs()[2], "2026-10-02").dispatchEvent(new PointerEvent("pointermove", { bubbles: true, clientX: 200, clientY: 200 }));
const t = document.querySelector(".tip").textContent;
check("09 tooltip: the day, the count, who, and the tasks", document.querySelector(".tip").style.display === "block" && t.includes("Fri, Oct 2, 2026") && t.includes("4 tasks finished") && t.includes("3 You") && t.includes("1 Claude") && t.includes("p.2 B · P") && t.includes("p.10 J"), t);
figs()[2].querySelector("svg").dispatchEvent(new PointerEvent("pointerleave"));
check("10 leaving the calendar hides the tooltip", document.querySelector(".tip").style.display === "none", document.querySelector(".tip").style.display);
check("11 bins: 0,1,2,3,5,6,9,10,40 -> 0,1,1,2,2,3,3,4,4", JSON.stringify([0, 1, 2, 3, 5, 6, 9, 10, 40].map(heatLevel)) === JSON.stringify([0, 1, 1, 2, 2, 3, 3, 4, 4]), [0, 1, 2, 3, 5, 6, 9, 10, 40].map(heatLevel));
const D = ["a", "b", "c", "d"];
check("12 streaks: a run through today counts today", JSON.stringify(streaks(D, { c: [1], d: [1] })) === JSON.stringify({ longest: 2, current: 2 }), streaks(D, { c: [1], d: [1] }));
check("13 streaks: today without one yet keeps yesterday's run", JSON.stringify(streaks(D, { a: [1], b: [1], c: [1] })) === JSON.stringify({ longest: 3, current: 3 }), streaks(D, { a: [1], b: [1], c: [1] }));
check("14 streaks: a gap before yesterday ends it", JSON.stringify(streaks(D, { a: [1], b: [1] })) === JSON.stringify({ longest: 2, current: 0 }), streaks(D, { a: [1], b: [1] }));
const months = [...figs()[0].querySelectorAll("svg > text")].map((x) => x.textContent).filter((x) => !["Mon", "Wed", "Fri"].includes(x));
const M = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
check("15 month labels run month by month", months.length >= 1 && months.every((m, i) => !i || M.indexOf(m) === (M.indexOf(months[i - 1]) + 1) % 12), months);
[...pr().querySelectorAll(".pfilters button")][0].click();
const tb = pr().querySelector("table.ptable"), rows = [...tb.querySelectorAll("tr")].map((r) => [...r.children].map((c) => c.textContent));
check("16 table view: days with any, newest first", JSON.stringify(rows) === JSON.stringify([["Day", "You", "Claude", "Combined"], ["Fri, Oct 2, 2026", "3", "1", "4"], ["Thu, Oct 1, 2026", "1", "0", "1"]]), rows);
[...pr().querySelectorAll(".pfilters button")][0].click();
const range = pr().querySelectorAll(".pfilters select")[1]; range.value = "7"; range.dispatchEvent(new Event("change"));
check("17 the range menu does not change the calendars", figs()[2].querySelector('rect.cell[data-day="2026-10-02"]').dataset.n === "4", figs().length);
check("18 no page errors", errors.length === 0, errors);
return JSON.stringify(out, null, 1);
