const out = {}, check = (name, ok, detail) => { out[name] = ok ? "PASS" : "FAIL " + JSON.stringify(detail); };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const until = async (f, ms) => { const t0 = Date.now(); while (!f() && Date.now() - t0 < ms) await sleep(100); return f(); };
const errors = []; window.addEventListener("error", (e) => errors.push(e.message));
for (let k = 0; k < 40 && !(window.cy && cy.nodes().length); k++) await sleep(100);
await sleep(500);
const opened = []; window.openLink = (u) => { opened.push(decodeURIComponent(u)); };   // nothing is really opened
const tail = (u) => u.split("/").pop();
select("f.7"); await sleep(200);
const grp = () => document.querySelector("#panel .opengroup"), menu = () => grp() && grp().querySelector("details.docmenu");
const rows = () => [...menu().querySelectorAll(".menu button")];
check("01 three documents: the Open button names the first, All 3 beside it", grp() && grp().querySelector("button.opendoc").textContent.includes("report_v2.md") && menu().querySelector("summary").textContent.trim() === "All 3", grp() && grp().textContent);
check("02 the list names every document in link order, the first marked main, the folder marked", rows().map((b) => b.querySelector(".dname").textContent).join("|") === "report_v2.md|results|report_v1.md"
  && rows()[0].textContent.includes("main") && rows()[1].textContent.includes("folder") && !rows()[2].textContent.includes("main"), rows().map((b) => b.textContent));
grp().querySelector("button.opendoc").click();
check("03 the Open button opens the main document", opened.length === 1 && tail(opened[0]) === "report_v2.md", opened);
menu().querySelector("summary").click(); await sleep(100);
check("04 All 3 opens the list", menu().open, menu().open);
renderPanel(); await sleep(100);
check("05 a redraw keeps the list open", menu() && menu().open, menu() && menu().open);
rows()[2].click(); await sleep(100);
check("06 choosing a document opens it and closes the list", tail(opened[1] || "") === "report_v1.md" && !menu().open, [opened, menu().open]);
menu().querySelector("summary").click(); await sleep(100);
document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" })); await sleep(100);
check("07 Escape closes the list", !menu().open, menu().open);
menu().querySelector("summary").click(); await sleep(100);
document.getElementById("panel").querySelector(".dmeta").click(); await sleep(100);   // not the title: it is the tick box's label
check("08 a click elsewhere closes it", !menu().open, menu().open);
select("f.8"); await sleep(200);
check("09 one document: the Open button alone, as before", !grp() && document.querySelector("#panel .actions button.opendoc") && document.querySelector("#panel .actions button.opendoc").textContent.includes("report_v1.md"), document.querySelector("#panel .actions") && document.querySelector("#panel .actions").innerHTML.slice(0, 200));
// files to review: checklist steps that name a linked document; Open and All N offer just those
select("f.7"); await sleep(200);
const addStep = async (t) => { const i = document.getElementById("i-step"); i.value = t; i.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
  await until(() => [...document.querySelectorAll("#panel .checklist .step span")].some((s) => s.textContent === t), 5000); };
await addStep("report_v1.md"); await addStep("results");
const openName = () => document.querySelector("#panel button.opendoc").textContent;
check("10 two steps name linked files: Open names the first, All 2 lists those two in checklist order, the first marked next",
  openName() === "Open report_v1.md" && menu().querySelector("summary").textContent.trim() === "All 2" && rows().map((b) => b.querySelector(".dname").textContent).join("|") === "report_v1.md|results"
  && rows()[0].textContent.includes("next") && !rows()[0].textContent.includes("main"), [openName(), rows().map((b) => b.textContent)]);
check("11 those steps carry a document icon", document.querySelectorAll("#panel .checklist .step span svg").length === 2, document.querySelectorAll("#panel .checklist .step span svg").length);
[...document.querySelectorAll("#panel .checklist .step")].find((l) => l.textContent === "report_v1.md").querySelector("input").click();
await until(() => openName() === "Open results", 5000);
check("12 ticking the first file: Open opens the next one, and the list marks the first reviewed", openName() === "Open results" && rows()[0].textContent.includes("reviewed") && rows()[1].textContent.includes("next"), [openName(), rows().map((b) => b.textContent)]);
document.querySelector("#panel button.opendoc").click();
check("13 Open opens it; the Documents row still lists all three", tail(opened[opened.length - 1]) === "results"
  && [...document.querySelectorAll("#panel .docs .doc a")].map((a) => a.textContent).join("|") === "report_v2.md|results|report_v1.md", opened);
check("14 no page errors", errors.length === 0, errors);
return JSON.stringify(out, null, 1);
