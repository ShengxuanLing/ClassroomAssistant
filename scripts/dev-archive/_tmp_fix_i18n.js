const fs=require("fs");
const src=fs.readFileSync("src/web/app.js","utf8");
const lines=src.split("\n");

const keysToRemove=[
  "\u51c6\u5907","\u5904\u7406\u6750\u6599","\u63d0\u53d6\u8bc1\u636e","\u7ec4\u88c5\u77e5\u8bc6",
  "\u6821\u9a8c","\u51b2\u7a81\u68c0\u67e5","\u5b8c\u6210","\u91cd\u8bd5",
  "\u9519\u8bef\u7c7b\u578b","\u5efa\u8bae\u64cd\u4f5c","\u53ef\u91cd\u8bd5","\u4e0d\u53ef\u91cd\u8bd5",
  "\u5904\u7406\u6b65\u9aa4","\u6b63\u5728\u5904\u7406\u672c\u5802\u6750\u6599\uff0c\u53ef\u5173\u95ed\u9875\u9762\u7a7a\u540e\u56de\u6765\u67e5\u770b\u8fdb\u5ea6\u3002",
  "\u6709\u6750\u6599\u5904\u7406\u5931\u8d25\uff0c\u5c55\u5f00\u67e5\u770b\u539f\u56e0\u4e0e\u5efa\u8bae\u64cd\u4f5c\u3002",
  "\u6ca1\u6709\u5904\u7406\u6b65\u9aa4\u53ef\u663e\u793a\u3002"
];

// Find block boundaries
const i18nStart=lines.findIndex(l=>l.indexOf("const I18N")===0);
let i18nEnd=-1;
for(let i=i18nStart;i<lines.length;i++){ if(lines[i]==="};"){i18nEnd=i;break;} }

const trStart=lines.findIndex(l=>l.indexOf("const TRANSLATIONS")===0);
let trEnd=-1;
for(let i=trStart;i<lines.length;i++){ if(lines[i]==="};"){trEnd=i;break;} }

// Mark lines to remove
const removeSet=new Set();

// Remove 16 misattributed key lines from I18N.es and I18N.ca
for(const k of keysToRemove){
  const escaped=k.replace(/[.*+?^${}()|[\]\\]/g,"\\$&");
  const re=new RegExp("^\\s*\\'"+escaped+"\\':");
  lines.forEach((l,i)=>{
    if(i>=i18nStart&&i<=i18nEnd&&re.test(l)) removeSet.add(i);
  });
}
console.log("Removing "+removeSet.size+" misattributed key lines from I18N");

// Remove orphan 错误 lines from TRANSLATIONS
lines.forEach((l,i)=>{
  if(i>=trStart&&i<=trEnd){
    if(/^\s*'\u9519\u8bef'\s*:/.test(l)) removeSet.add(i);
  }
});
const totalRemove=removeSet.size;
console.log("Total lines to remove (incl orphan): "+totalRemove);

// Build filtered array
const filtered=lines.filter((_,i)=>!removeSet.has(i));
console.log("Filtered: "+filtered.length+" lines");

// Find new positions of TR.es and TR.ca block ends in filtered array
function findBlockEnd(array, langMarker){
  const markerIdx=array.findIndex(l=>l.trim()===langMarker);
  if(markerIdx<0) return -1;
  for(let i=markerIdx+1;i<array.length;i++){
    if(array[i].trim()==="  },") return i;
  }
  return -1;
}
const newEsEnd=findBlockEnd(filtered,"es: {");
const newCaEnd=findBlockEnd(filtered,"ca: {");
console.log("New TR.es end: "+(newEsEnd+1), "New TR.ca end: "+(newCaEnd+1));

// Build new entry lines (15 entries each for es and ca, excluding 重试 which is already in TR)
// 重试 is already in both TR.es and TR.ca, so we skip it
const esEntries=[
  ["\u51c6\u5907","Preparando"],
  ["\u5904\u7406\u6750\u6599","Procesando materiales"],
  ["\u63d0\u53d6\u8bc1\u636e","Extrayendo evidencias"],
  ["\u7ec4\u88c5\u77e5\u8bc6","Ensamblando conocimiento"],
  ["\u6821\u9a8c","Validando"],
  ["\u51b2\u7a81\u68c0\u67e5","Comprobando conflictos"],
  ["\u5b8c\u6210","Finalizado"],
  ["\u9519\u8bef\u7c7b\u578b","Categor\u00eda de error"],
  ["\u5efa\u8bae\u64cd\u4f5c","Acci\u00f3n recomendada"],
  ["\u53ef\u91cd\u8bd5","Reintentable"],
  ["\u4e0d\u53ef\u91cd\u8bd5","No reintentable"],
  ["\u5904\u7406\u6b65\u9aa4","Pasos de procesamiento"],
  ["\u6b63\u5728\u5904\u7406\u672c\u5802\u6750\u6599\uff0c\u53ef\u5173\u95ed\u9875\u9762\u7a7a\u540e\u56de\u6765\u67e5\u770b\u8fdb\u5ea6\u3002","Procesando los materiales de la sesi\u00f3n; puede cerrar la p\u00e1gina y volver m\u00e1s tarde."],
  ["\u6709\u6750\u6599\u5904\u7406\u5931\u8d25\uff0c\u5c55\u5f00\u67e5\u770b\u539f\u56e0\u4e0e\u5efa\u8bae\u64cd\u4f5c\u3002","Algunos materiales fallaron; expanda para ver la causa y la acci\u00f3n recomendada."],
  ["\u6ca1\u6709\u5904\u7406\u6b65\u9aa4\u53ef\u663e\u793a\u3002","No hay pasos de procesamiento que mostrar."],
];
const caEntries=[
  ["\u51c6\u5907","Preparant"],
  ["\u5904\u7406\u6750\u6599","Processant materials"],
  ["\u63d0\u53d6\u8bc1\u636e","Extraient evid\u00e8ncies"],
  ["\u7ec4\u88c5\u77e5\u8bc6","Muntant coneixement"],
  ["\u6821\u9a8c","Validant"],
  ["\u51b2\u7a81\u68c0\u67e5","Comprovant conflictes"],
  ["\u5b8c\u6210","Finalitzat"],
  ["\u9519\u8bef\u7c7b\u578b","Categoria d\u0027error"],
  ["\u5efa\u8bae\u64cd\u4f5c","Acci\u00f3 recomanada"],
  ["\u53ef\u91cd\u8bd5","Reintentable"],
  ["\u4e0d\u53ef\u91cd\u8bd5","No reintentable"],
  ["\u5904\u7406\u6b65\u9aa4","Passos de processament"],
  ["\u6b63\u5728\u5904\u7406\u672c\u5802\u6750\u6599\uff0c\u53ef\u5173\u95ed\u9875\u9762\u7a7a\u540e\u56de\u6765\u67e5\u770b\u8fdb\u5ea6\u3002","S\u0027est\u00e0 processant els materials de la sessi\u00f3; pot tancar la p\u00e0gina i tornar m\u00e9s tard."],
  ["\u6709\u6750\u6599\u5904\u7406\u5931\u8d25\uff0c\u5c55\u5f00\u67e5\u770b\u539f\u56e0\u4e0e\u5efa\u8bae\u64cd\u4f5c\u3002","Alguns materials han fallat; desplegui per veure la causa i l\u0027acci\u00f3 recomanada."],
  ["\u6ca1\u6709\u5904\u7406\u6b65\u9aa4\u53ef\u663e\u793a\u3002","No hi ha passos de processament per mostrar."],
];

function makeEntryLine(key,val){
  return "    '"+key+"': '"+val+"',";
}

const esLines=esEntries.map(([k,v])=>makeEntryLine(k,v));
const caLines=caEntries.map(([k,v])=>makeEntryLine(k,v));

// Insert ca first (higher position), then es
const finalLines=[...filtered];
for(let i=0;i<caLines.length;i++) finalLines.splice(newCaEnd+i,0,caLines[i]);
// After inserting ca lines, newCaEnd shifts, but newEsEnd is before newCaEnd so it stays valid
for(let i=0;i<esLines.length;i++) finalLines.splice(newEsEnd+i,0,esLines[i]);

const result=finalLines.join("\n");
fs.writeFileSync("src/web/app.js",result,"utf8");
console.log("Done. Final line count: "+finalLines.length);
console.log("Removed: "+totalRemove+" lines, added: "+(esLines.length+caLines.length)+" lines");
