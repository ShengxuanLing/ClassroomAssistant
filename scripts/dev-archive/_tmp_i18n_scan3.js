const fs=require("fs");
const src=fs.readFileSync("src/web/app.js","utf8");
function capture(name){
  const re=new RegExp("const "+name+" = (\\{[\\s\\S]*?\\});");
  const mm=src.match(re);
  return eval("("+mm[1]+")");
}
const I18N=capture("I18N"), TR=capture("TRANSLATIONS");
const lines=src.split("\n");
// locate table line ranges by scanning
function findRange(startMarker){
  const s=lines.findIndex(l=>l.indexOf(startMarker)!==-1);
  return s;
}
const i18nStart=lines.findIndex(l=>l.indexOf("const I18N")===0)+1; // 0-based line where I18N begins
// Instead: find line numbers of each key literal occurrence across all of app.js
const i18nBlockEnd=lines.findIndex((l,i)=>i>i18nStart&&l==="};"); // rough
for(const k of Object.keys(TR.es)){
  const hits=[];
  lines.forEach((l,li)=>{ if(l.indexOf("\u0027"+k+"\u0027")!==-1) hits.push(li+1); });
  // classify
  if(hits.length===1) console.log("SINGLE-HIT:"+k+" @ "+hits[0]);
}
console.log("--- I18N block line range:", i18nStart+1, "to", i18nBlockEnd+1);
