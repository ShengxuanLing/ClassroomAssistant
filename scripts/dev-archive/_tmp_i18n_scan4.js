const fs=require("fs");
const src=fs.readFileSync("src/web/app.js","utf8");
function capture(name){
  const re=new RegExp("const "+name+" = (\\{[\\s\\S]*?\\n\\});");
  const mm=src.match(re);
  return eval("("+mm[1]+")");
}
const I18N=capture("I18N"), TR=capture("TRANSLATIONS");
const zhKeys=Object.keys(I18N.zh);
// 16 misattributed keys: keys present in es/ca blocks that are NOT in zh block = sentence keys
const esOnly=Object.keys(I18N.es).filter(k=>!zhKeys.includes(k));
const caOnly=Object.keys(I18N.ca).filter(k=>!zhKeys.includes(k));
console.log("es extra:", esOnly.length); console.log("ca extra:", caOnly.length);
console.log("es extras:"); esOnly.forEach(k=>{
  const inTR=Object.keys(TR.es).includes(k)?Object.keys(TR.ca).includes(k):0;
  const trEs=TR.es[k]!==undefined, trCa=TR.ca[k]!==undefined;
  console.log("  "+JSON.stringify(k)+" | inTR.es="+trEs+" inTR.ca="+trCa+" | I18N.es="+JSON.stringify(I18N.es[k]));
});
// check which of the 16 keys in TR are used in code
const lines=src.split("\n");
for(const k of esOnly){
  const hits=[];
  lines.forEach((l,li)=>{ if(l.indexOf("\u0027"+k+"\u0027")!==-1) hits.push(li+1); });
  console.log(k, "::", hits.length, "literal hits:", hits.slice(0,6).join(","));
}
