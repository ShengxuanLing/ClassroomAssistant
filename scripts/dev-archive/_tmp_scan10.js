const fs=require("fs");
const src=fs.readFileSync("src/web/app.js","utf8");
function capture(name){
  const re=new RegExp("const "+name+" = (\\{[\\s\\S]*?\\n\\});");
  const mm=src.match(re);
  return eval("("+mm[1]+")");
}
const I18N=capture("I18N"), TR=capture("TRANSLATIONS");
// t() keys: from t('...') and t("...")
const tkeys=[...src.matchAll(/\bt\(\s*(['"])([^'"]+)\1/g)].map(m=>m[2]);
const uniq=[...new Set(tkeys)];
const i18nKeys=new Set();
for(const o of ["zh","es","ca"]) i18nKeys.addAll? 0: Object.keys(I18N[o]).forEach(k=>i18nKeys.add(k));
const trEs=new Set(Object.keys(TR.es)), trCa=new Set(Object.keys(TR.ca));
const undefined=uniq.filter(k=>!i18nKeys.has(k)&&!trEs.has(k)&&!trCa.has(k));
console.log("undefined t() keys:", undefined.length?undefined.join(" | "):"(none)");
// orphan: keys in TR.es not in TR.ca or vice versa, or not used
const used=uniq.filter(k=>/[一-鿿]/.test(k));
console.log("used sentence keys:", used.length);
console.log("orphan in TR.es:", Object.keys(TR.es).filter(k=>!used.includes(k)).join(",")||"(none)");
console.log("orphan in TR.ca:", Object.keys(TR.ca).filter(k=>!used.includes(k)).join(",")||"(none)");
console.log("missing in TR.es:", used.filter(k=>!trEs.has(k)).join(",")||"(none)");
console.log("missing in TR.ca:", used.filter(k=>!trCa.has(k)).join(",")||"(none)");
// check t('...) that are undefined but in zh I18N
console.log("in I18N.zh only:", uniq.filter(k=>i18nKeys.has(k)&&!trEs.has(k)&&!trCa.has(k)).length);
