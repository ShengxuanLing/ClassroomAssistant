const fs=require("fs");
const src=fs.readFileSync("src/web/app.js","utf8");
function capture(name){
  const re=new RegExp("const "+name+" = (\\{[\\s\\S]*?\\n\\});");
  return eval("("+src.match(re)[1]+")");
}
const I18N=capture("I18N"), TR=capture("TRANSLATIONS");
const used=set => new Set(0); // placeholder
const lines=src.split("\n");
// all t() calls
const tkeys=[...src.matchAll(/\bt\(\s*['"]([^'"]+)['"]/g)].map(m=>m[1]);
// find keys present in I18N.es but not in I18N.zh (the 16 misattributed ones)
const zhKeys=Object.keys(I18N.zh);
const misEs=Object.keys(I18N.es).filter(k=>!zhKeys.includes(k));
const misCa=Object.keys(I18N.ca).filter(k=>!zhKeys.includes(k));
console.log("misEs:", JSON.stringify(misEs,null,2));
console.log("misCa:", JSON.stringify(misCa,null,2));
console.log("Same set:", JSON.stringify(misEs.sort())===JSON.stringify(misCa.sort()));
// For each miskey, check usage in code
for(const k of misEs){
  const inTRes=TR.es[k]!==undefined, inTRca=TR.ca[k]!==undefined;
  const uses=[...src.matchAll(new RegExp("\\bt\\(\\s*['\""+k.replace(/[.*+?^${}()|[\\]\\\\]/g,"\\\\$&")+"['\"]","g"))];
  console.log(k, "| TR.es:", inTRes, "TR.ca:", inTRca, "t()uses:", uses.length);
}
