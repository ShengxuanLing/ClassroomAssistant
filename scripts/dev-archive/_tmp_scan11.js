const fs=require("fs");
const src=fs.readFileSync("src/web/app.js","utf8");
const lines=src.split("\n");
// Find all t() calls and their contexts
const tcalls=[];
lines.forEach((l,i)=>{
  const matches=[...l.matchAll(/\bt\(\s*['"]([^'"]+)['"]/g)];
  matches.forEach(m=>tcalls.push({line:i+1,key:m[1]}));
});
// Keys that are orphan candidates
const orphans=["\u9519\u8bef","\u8fd8\u6ca1\u6709\u5904\u7406\u4efb\u52a1\u3002\u4e0a\u4f20\u6750\u6599\u540e\u70b9\u51fb\u201c\u5904\u7406\u201d\u3002"];
for(const o of orphans){
  const hits=tcalls.filter(c=>c.key===o);
  console.log(JSON.stringify(o), ":", hits.length ? hits.map(h=>"line "+h.line).join(", ") : "NOT USED via t()");
}
// Also check: t(t('...')) pattern
lines.forEach((l,i)=>{
  if(l.indexOf("t(t(")!==-1) console.log("nested t:", (i+1)+"|"+l.trim().slice(0,100));
});
// Check '没有处理任务' usage via string concatenation
const lines2=lines.filter((l,i)=>l.indexOf("\u8fd8\u6ca1\u6709\u5904\u7406\u4efb\u52a1")!==-1);
lines2.forEach(l=>console.log("literal hit:", l.trim().slice(0,120)));
// Check '错误' usage
const lines3=lines.filter(l=>l.indexOf("'错误'")!==-1);
lines3.forEach(l=>console.log("错误 literal:", l.trim().slice(0,120)));
