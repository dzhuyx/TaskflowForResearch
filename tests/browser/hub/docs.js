const out = {}, check = (name, ok, detail) => { out[name] = ok ? "PASS" : "FAIL " + JSON.stringify(detail); };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const until = async (f, ms) => { const t0 = Date.now(); while (!f() && Date.now() - t0 < ms) await sleep(100); return f(); };
const errors = []; window.addEventListener("error", (e) => errors.push(e.message));
const det = () => document.getElementById("detail");
const rowOf = (id) => [...document.querySelectorAll("#tasks .todo div, #projects .running div")].find((r) => r.querySelector(".id").textContent === id);
await until(() => rowOf("al.1"), 6000);
const opened = []; window.openLink = (u) => { opened.push(decodeURIComponent(u)); };   // nothing is really opened
const tail = (u) => u.split("/").pop();
rowOf("al.1").click(); await until(() => det().querySelector(".opengroup"), 3000);
const grp = () => det().querySelector(".opengroup"), menu = () => grp() && grp().querySelector("details.docmenu");
const rows = () => [...menu().querySelectorAll(".menu button")];
check("01 three documents: the Open button names the first, All 3 beside it, the board link after", grp() && grp().querySelector("button.opendoc").textContent.includes("report_v2.md") && menu().querySelector("summary").textContent.trim() === "All 3" && grp().nextElementSibling && grp().nextElementSibling.matches("a.button.board"), grp() && grp().parentNode.textContent);
check("02 the list names every document in link order, the first marked main, the folder marked", rows().map((b) => b.querySelector(".dname").textContent).join("|") === "report_v2.md|results|report_v1.md"
  && rows()[0].textContent.includes("main") && rows()[1].textContent.includes("folder"), rows().map((b) => b.textContent));
grp().querySelector("button.opendoc").click();
check("03 the Open button opens the main document", opened.length === 1 && tail(opened[0]) === "report_v2.md", opened);
menu().querySelector("summary").click(); await sleep(100);
check("04 All 3 opens the list, drawn over what follows", menu().open && getComputedStyle(menu().querySelector(".menu")).position === "absolute", menu().open);
renderDetail(); await sleep(100);
check("05 a redraw keeps the list open", menu() && menu().open, menu() && menu().open);
rows()[1].click(); await sleep(100);
check("06 choosing a document opens it and closes the list", tail(opened[1] || "") === "results" && !menu().open, [opened, menu().open]);
menu().querySelector("summary").click(); await sleep(100);
document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" })); await sleep(100);
check("07 Escape closes the list", !menu().open, menu().open);
check("08 the Documents row still lists all three", [...det().querySelectorAll(".docs .doc a")].map((a) => a.textContent).join("|") === "report_v2.md|results|report_v1.md", [...det().querySelectorAll(".docs .doc a")].map((a) => a.textContent));
check("09 no page errors", errors.length === 0, errors);
return JSON.stringify(out, null, 1);
