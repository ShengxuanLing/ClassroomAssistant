const fs=require("fs");
const src=fs.readFileSync("src/web/app.js","utf8");
function capture(name){
  const re=new RegExp("const "+name+" = (\\{[\\s\\S]*?\\});");
  const mm=src.match(re);
  if(!mm) return null;
  return eval("("+mm[1]+")");
}
const I18N=capture("I18N"), TR=capture("TRANSLATIONS");
const ks=o=>o?Object.keys(o).sort():[];
for(const o of ["zh","es","ca"]) console.log("I18N."+o+":", ks(I18N[o]).length, "TR."+o+":", TR[o]?ks(TR[o]).length:"(missing)");
const zi=ks(I18N.zh);
for(const o of ["es","ca"]){
  const ao=ks(I18N[o]);
  console.log("I18N "+o+" missing vs zh:", zi.filter(x=>!ao.includes(x)).join(",")||"none", "| extra:", ao.filter(x=>!zi.includes(x)).join(",")||"none");
}
function isChinese(v){return typeof v==="string"&&/[一-鿿]/.test(v);}
for(const o of ["es","ca","zh"]){
  const off=Object.entries(I18N[o]).filter(([k,v])=>isChinese(v));
  console.log("I18N."+o+" keys w/ Chinese values:", off.map(x=>x[0]+"="+x[1]).join(" ; ")||"(none)");
}
if(TR.ca){
  const zt=ks(TR.zh), et=ks(TR.es), ct=ks(TR.ca);
  console.log("TR keys zh/es/ca:", zt.length, et.length, ct.length);
  console.log("TR es missing in zh:", et.filter(x=>!zt.includes(x)).join(",")||"none");
  console.log("TR ca missing in zh:", ct.filter(x=>!zt.includes(x)).join(",")||"none");
  console.log("TR zh missing in es:", zt.filter(x=>!et.includes(x)).join(",")||"none");
  console.log("TR zh missing in ca:", zt.filter(x=>!ct.includes(x)).join(",")||"none");
}
const tuses=[...src.matchAll(/\bt\(\s*['"]([^'"]+)['"]/g)].map(m=>m[1]);
const uniq=[...new Set(tuses)];
const i18nAll=new Set([...ks(I18N.zh),...ks(I18N.es),...ks(I18N.ca)]);
const allTr=new Set(); for(const o of ["zh","es","ca"]) if(TR[o]) Object.keys(TR[o]).forEach(x=>allTr.add(x));
console.log("t() unique keys:", uniq.length);
console.log("t() keys undefined everywhere:", uniq.filter(k2=>!i18nAll.has(k2)&&!allTr.has(k2)).join(",")||"none");
// keys defined in TRANSLATIONS but never used
console.log("TR keys unused in app.js:", [...allTr].filter(k=>!uniq.includes(k)).join(",")||"none");
