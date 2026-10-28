// Iter49: unit checks for pickedOrder / withPlaceholders (EvidenceGallery.tsx) - extracted and transpiled with typescript.
const fs = require("fs");
const ts = require("/app/frontend/node_modules/typescript");
const src = fs.readFileSync("/app/frontend/src/components/EvidenceGallery.tsx", "utf8");
const start = src.indexOf("const BATCH");
const end = src.indexOf("export function EvidenceGallery");
const body = "type EvidenceFile = any; type PendingUpload = any;\n" + src.slice(start, end).replace(/export /g, "") + "\nmodule.exports = { pickedOrder, withPlaceholders };";
const js = ts.transpileModule(body, { compilerOptions: { target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.CommonJS } }).outputText;
const m = { exports: {} };
new Function("module", "exports", js)(m, m.exports);
const { pickedOrder, withPlaceholders } = m.exports;
let pass = 0, fail = 0;
const eq = (name, a, b) => { const ok = JSON.stringify(a) === JSON.stringify(b); ok ? pass++ : fail++; console.log(`${ok ? "PASS" : "FAIL"} ${name}: got ${JSON.stringify(a)}${ok ? "" : " expected " + JSON.stringify(b)}`); };
const f = (id, cid) => ({ id, client_id: cid });
const ids = (l) => l.map((x) => x.id);
const keys = (t) => t.map((x) => ("real" in x ? "R:" + x.real.id : "U:" + x.up.key));

// 1. completion order 3,1,4,0,2 -> picked order
eq("pickedOrder basic", ids(pickedOrder([f("c", "up-1-3"), f("a", "up-1-1"), f("d", "up-1-4"), f("z", "up-1-0"), f("b", "up-1-2")])), ["z", "a", "b", "c", "d"]);
// 2. other photos keep slots
eq("pickedOrder mixed", ids(pickedOrder([f("x", null), f("b", "up-1-1"), f("y", "cam-9"), f("a", "up-1-0"), f("w", undefined)])), ["x", "a", "y", "b", "w"]);
// 3. two batches interleaved
eq("pickedOrder two batches", ids(pickedOrder([f("B1", "up-2-1"), f("A1", "up-1-1"), f("B0", "up-2-0"), f("A0", "up-1-0")])), ["B0", "A0", "B1", "A1"]);
// 4. index numeric not lexical (10 after 2)
eq("pickedOrder numeric", ids(pickedOrder([f("i10", "up-1-10"), f("i2", "up-1-2"), f("i1", "up-1-1")])), ["i1", "i2", "i10"]);
// 5. placeholders in picked position: 1 and 3 done, 0,2,4 uploading
const ups = [0, 1, 2, 3, 4].map((i) => ({ key: `up-5-${i}`, status: "uploading" }));
eq("withPlaceholders picked", keys(withPlaceholders(pickedOrder([f("p", null), f("r3", "up-5-3"), f("r1", "up-5-1")]), ups)),
  ["R:p", "U:up-5-0", "R:r1", "U:up-5-2", "R:r3", "U:up-5-4"]);
// 6. no duplicate when real exists and upload not yet 'shown'
const t6 = withPlaceholders([f("r0", "up-6-0")], [{ key: "up-6-0", status: "done" }, { key: "up-6-1", status: "failed" }]);
eq("withPlaceholders no dup", keys(t6), ["R:r0", "U:up-6-1"]);
// 7. shown upload whose photo is missing (refetch failed) -> tile disappears (documented behaviour)
eq("withPlaceholders shown but missing", keys(withPlaceholders([], [{ key: "up-7-0", status: "done", shown: true }])), []);
// 8. no reals of batch -> placeholders appended at end in order
eq("withPlaceholders all pending", keys(withPlaceholders([f("p", null)], [{ key: "up-8-1" }, { key: "up-8-0" }])), ["R:p", "U:up-8-0", "U:up-8-1"]);
console.log(`unit: ${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
