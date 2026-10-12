const out = {}, check = (name, ok, detail) => { out[name] = ok ? "PASS" : "FAIL " + JSON.stringify(detail); };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const until = async (f, ms) => { const t0 = Date.now(); while (!f() && Date.now() - t0 < ms) await sleep(100); return f(); };
const errors = []; window.addEventListener("error", (e) => errors.push(e.message));
for (let k = 0; k < 40 && !(window.cy && cy.nodes().length); k++) await sleep(100);
await sleep(800);
const face = (id) => decodeURIComponent(cy.getElementById(id).data("cb"));
check("01 steps are not boxes on the board", cy.nodes().length === state.items.length && !cy.nodes().some((n) => /Read section|Lemma|constants/.test(n.data("label") || "")), cy.nodes().length);
check("02 the card shows 1/3 at its top right; a task without steps shows none", face("f.7").includes(">1/3<") && !/>\d+\/\d+</.test(face("f.8")), face("f.7").match(/>\d+\/\d+</));
select("f.7"); await sleep(200);
const box = () => document.querySelector("#panel .checklist"), steps = () => [...box().querySelectorAll(".step")];
check("03 the panel lists the steps, one ticked, the deeper one indented", !!box() && box().textContent.includes("1 of 3 done")
  && steps().map((s) => s.textContent + ":" + s.querySelector("input").checked).join("|") === "Read section 2:false|Check Lemma 1:true|Its constants:false"
  && steps()[2].style.paddingLeft === "22px", box() && steps().map((s) => s.textContent));
steps()[0].querySelector("input").click();
await until(() => byId("f.7").checklist[0].done, 4000);
check("04 ticking a step writes @done on its own line; the card and the panel follow", byId("f.7").checklist[0].done && /@done\(\d{4}-\d\d-\d\d \d\d:\d\d\)/.test(byId("f.7").checklist[0].raw) && face("f.7").includes(">2/3<") && box().textContent.includes("2 of 3 done"), byId("f.7").checklist[0].raw);
steps()[2].querySelector("input").click();
await until(() => byId("f.7").checklist[2].done, 4000);
check("05 all ticked: the mark turns green", face("f.7").includes(">3/3<") && face("f.7").includes("stroke='#13663b'"), "");
steps()[0].querySelector("input").click();
await until(() => !byId("f.7").checklist[0].done, 4000);
check("06 unticking removes @done", !byId("f.7").checklist[0].done && !/@done/.test(byId("f.7").checklist[0].raw) && face("f.7").includes(">2/3<"), byId("f.7").checklist[0].raw);
check("07 the task itself is untouched", !byId("f.7").done && byId("f.7").raw === state.items.find((i) => i.id === "f.7").raw, "");
select(null); await sleep(150);
check("08 the panel's legend explains the mark", document.querySelector("#panel .legend").textContent.includes("steps of its checklist done"), "");
select("f.8"); await sleep(200);
const before = (a, b) => !!a && !!b && !!(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING);
const facts = document.querySelector("#panel dl.facts");
check("10 the panel puts the Claude session and Add a note above Needs", before(document.querySelector("#panel .sessionbox"), facts) && before(el("t-note"), facts) && facts.querySelector("dt").textContent === "Needs",
  [!!document.querySelector("#panel .sessionbox"), !!el("t-note")]);
const afterList = document.querySelector("#panel .checklist").nextElementSibling;
check("10b Add a note sits right below the checklist", afterList && afterList.tagName === "H3" && afterList.textContent === "Add a note" && afterList.nextElementSibling === el("t-note"),
  afterList && afterList.outerHTML.slice(0, 80));
// adding steps from the board: every open task shows its checklist, with a field to add a step
select("f.8"); await sleep(200);
check("11 a task with no steps still shows its checklist, with a field to add one", !!box() && box().textContent.includes("none yet") && !!el("i-step"), box() && box().textContent);
el("i-step").value = "Draft the reply"; el("i-step").dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
await until(() => byId("f.8").checklist.length === 1, 4000);
check("12 Enter adds it under the task; the card shows 0/1", byId("f.8").checklist.length === 1 && byId("f.8").checklist[0].text === "Draft the reply" && face("f.8").includes(">0/1<") && steps().length === 1, byId("f.8").checklist);
steps()[0].querySelector("input").click();
await until(() => byId("f.8").checklist[0].done, 4000);
check("13 and it ticks like any step", byId("f.8").checklist[0].done && face("f.8").includes(">1/1<"), "");
document.querySelector("#panel .checklist .steprow .x").click();
await until(() => byId("f.8").checklist.length === 0, 4000);
check("14 × removes it again", byId("f.8").checklist.length === 0 && !/>\d+\/\d+</.test(face("f.8")) && !!el("i-step"), byId("f.8").checklist);
check("09 no page errors", errors.length === 0, errors);
return JSON.stringify(out, null, 1);
