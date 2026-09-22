const fs=require("fs");
const src=fs.readFileSync("src/web/app.js","utf8");
// Find all t() calls, including t(t(...)) patterns
const tcalls=[...src.matchAll(/\bt\(\s*['"]([^'"]+)['"]/g)].map(m=>m[1]);
console.log("total t() calls:", tcalls.length);
// unique
const uniq=new Set(tcalls);
console.log("unique t() keys:", uniq.size);
// Check if 错误 is in unique t() keys
console.log("\u9519\u8bef in t() keys:", uniq.has("\u9519\u8bef"));
// Check all keys containing 错误
[...uniq].filter(k=>k.includes("\u9519\u8bef")).forEach(k=>console.log("contains \u9519\u8bef:", JSON.stringify(k)));
// Now: is t("\u9519\u8bef") called anywhere with double quotes?
console.log('---');
const doubleQuote=[...src.matchAll(/\bt\(\s*"([^"]+)"\s*\)/g)].map(m=>m[1]);
console.log("t() with double quotes:", doubleQuote.length ? doubleQuote.join(" | ") : "(none)");
