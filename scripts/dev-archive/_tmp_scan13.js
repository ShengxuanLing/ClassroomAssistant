const fs=require("fs");
const src=fs.readFileSync("src/web/app.js","utf8");
const lines=src.split("\n");
// The key in TRANSLATIONS is: 还没有处理任务。上传材料后点击\"处理\"。
// i.e. the actual string is: 还没有处理任务。上传材料后点击"处理"。  (with literal double-quote characters)
// The t() call: t('还没有处理任务。上传材料后点击\"处理\"。')
// In JS, the \" inside single quotes is a literal backslash-doublequote, NOT an escaped quote.
// So the actual key string in the t() call is: 还没有处理任务。上传材料后点击\"处理\"。  (with literal backslashes)
// The key in TRANSLATIONS is: '还没有处理任务。上传材料后点击\"处理\"。' which is ALSO the same backslash-doublequote
// So they match! The key IS used. My earlier scan was wrong because I searched for the raw text without backslashes.

// Let's verify by checking if the key in TRANSLATIONS matches the t() call key exactly
// TRANSLATIONS key (from source): '还没有处理任务。上传材料后点击\"处理\"。'
// t() call key (from source): t('还没有处理任务。上传材料后点击\"处理\"。')
// Both have the same literal backslash-doublequote, so they DO match.
// Therefore this is NOT an orphan.

// Now let me check: does the test's regex parse handle this?
// Test regex: r"^\s*'((?:[^'\\]|\\.)*)':"  — this captures (?:[^'\\]|\\.)* which includes \"
// So the captured key would be: 还没有处理任务。上传材料后点击\"处理\"。  (with literal backslash-quotes)
// And _used_keys regex: r"\bt\(\s*'([^']+)'"  — this captures [^']+ which stops at the next unescaped '
// So the used key would be: 还没有处理任务。上传材料后点击\"处理\"。  (same, because [^']+ includes backslashes)
// They match!

console.log("NOT an orphan - the backslash-quotes match between t() call and TRANSLATIONS key");

// Let's re-verify the actual orphan situation with exact string matching
function capture(name){
  const re=new RegExp("const "+name+" = (\\{[\\s\\S]*?\\n\\});");
  return eval("("+src.match(re)[1]+")");
}
const TR=capture("TRANSLATIONS");
const lines2=src.split("\n");
const trStart=lines2.findIndex(l=>l.indexOf("const TRANSLATIONS")===0);
// Parse TRANSLATIONS keys manually (same as test does)
function parseTRKeys(lang){
  const s=src.slice(src.indexOf("const TRANSLATIONS = {"));
  const e=s.indexOf("\n};");
  const block=s.slice(0,e);
  const langStart=block.indexOf(lang+": {");
  const langEnd=block.indexOf("\n  },", langStart);
  const body=block.slice(langStart, langEnd);
  const keys=[];
  const re=/^\s*'((?:[^'\\]|\\.)*)':/gm;
  let m;
  while((m=re.exec(body))!==null) keys.push(m[1]);
  return new Set(keys);
}
const trEsKeys=parseTRKeys("es");
const trCaKeys=parseTRKeys("ca");
console.log("TR.es key count:", trEsKeys.size, "TR.ca key count:", trCaKeys.size);
// Get used t() keys
const used=[...src.matchAll(/\bt\(\s*('([^']*)'|")/g)].map(m=>m[2]).filter(Boolean);
const usedSet=new Set(used);
console.log("used t() keys:", usedSet.size);
const orphanEs=[...trEsKeys].filter(k=>!usedSet.has(k));
const orphanCa=[...trCaKeys].filter(k=>!usedSet.has(k));
console.log("orphans in es:", orphanEs.map(k=>JSON.stringify(k)).join(" | ")||"(none)");
console.log("orphans in ca:", orphanCa.map(k=>JSON.stringify(k)).join(" | ")||"(none)");
