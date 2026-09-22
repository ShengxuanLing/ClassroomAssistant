const fs=require("fs");
const src=fs.readFileSync("src/web/app.js","utf8");
const lines=src.split("\n");
// Check what the test's _used_keys regex would find
const re=/\bt\(\s*'([^']+)'/g;
const used=[];
let m;
while((m=re.exec(src))!==null) used.push(m[1]);
const usedSet=new Set(used);
console.log("Test regex found keys:", usedSet.size);
console.log("\u9519\u8bef (2-char) in used:", usedSet.has("\u9519\u8bef"));
console.log("\u9519\u8bef\u7c7b\u578b (4-char) in used:", usedSet.has("\u9519\u8bef\u7c7b\u578b"));
// The test's _translations regex
function parseTRKeys(lang){
  const s=src.slice(src.indexOf("const TRANSLATIONS = {"));
  const e=s.indexOf("\n};");
  const block=s.slice(0,e);
  const langStart=block.indexOf(lang+": {");
  const langEnd=block.indexOf("\n  },", langStart);
  const body=block.slice(langStart, langEnd);
  const keys=[];
  const tre=/^\s*'((?:[^'\\]|\\.)*)':/gm;
  let m2;
  while((m2=tre.exec(body))!==null) keys.push(m2[1]);
  return new Set(keys);
}
const trEs=parseTRKeys("es");
const trCa=parseTRKeys("ca");
// orphan = in TR but not in used
const orphansEs=[...trEs].filter(k=>!usedSet.has(k));
const orphansCa=[...trCa].filter(k=>!usedSet.has(k));
console.log("orphans in TR.es:", orphansEs.map(k=>JSON.stringify(k)));
console.log("orphans in TR.ca:", orphansCa.map(k=>JSON.stringify(k)));
