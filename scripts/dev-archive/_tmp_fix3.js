const fs=require("fs");
const raw=fs.readFileSync("src/web/app.js","utf8");
const crlf=raw.includes("\r\n");
const lines=raw.split("\n").map(l=>l.replace(/\r$/,""));

const i18nS=lines.findIndex(l=>l.indexOf("const I18N")===0);
let i18nE=-1; for(let i=i18nS;i<lines.length;i++){if(lines[i]==="};"){i18nE=i;break;}}
const trS=lines.findIndex(l=>l.indexOf("const TRANSLATIONS")===0);
let trE=-1; for(let i=trS;i<lines.length;i++){if(lines[i]==="};"){trE=i;break;}}
console.log("I18N",i18nS+1,i18nE+1,"TR",trS+1,trE+1);

// 16 misattributed keys
const keysToRemove=["\u51c6\u5907","\u5904\u7406\u6750\u6599","\u63d0\u53d6\u8bc1\u636e","\u7ec4\u88c5\u77e5\u8bc6","\u6821\u9a8c","\u51b2\u7a81\u68c0\u67e5","\u5b8c\u6210","\u91cd\u8bd5","\u9519\u8bef\u7c7b\u578b","\u5efa\u8bae\u64cd\u4f5c","\u53ef\u91cd\u8bd5","\u4e0d\u53ef\u91cd\u8bd5","\u5904\u7406\u6b65\u9aa4","\u6b63\u5728\u5904\u7406\u672c\u5802\u6750\u6599\uff0c\u53ef\u5173\u95ed\u9875\u9762\u7a7a\u540e\u56de\u6765\u67e5\u770b\u8fdb\u5ea6\u3002","\u6709\u6750\u6599\u5904\u7406\u5931\u8d25\uff0c\u5c55\u5f00\u67e5\u770b\u539f\u56e0\u4e0e\u5efa\u8bae\u64cd\u4f5c\u3002","\u6ca1\u6709\u5904\u7406\u6b65\u9aa4\u53ef\u663e\u793a\u3002"];

const removeSet=new Set();
// verify each of the 16 is a standalone line in I18N region
for(const k of keysToRemove){
  let n=0;
  lines.forEach((l,i)=>{ if(i>=i18nS&&i<=i18nE&&l.trim()==="'"+k+"': "+l.trim().slice("'"+k+"': ".length-1)){n++;} });
  // simpler: exact line match via prefix check
  let n2=0;
  lines.forEach((l,i)=>{ if(i>=i18nS&&i<=i18nE){ const t=l.trim(); if(t.startsWith("'"+k+"': ")){n2++; removeSet.add(i);} } });
  if(n2!==2) console.log("!! ",JSON.stringify(k),"found",n2,"times in I18N");
}
// orphan 错误 lines in TR region
lines.forEach((l,i)=>{ if(i>=trS&&i<=trE&&l.trim().startsWith("'\u9519\u8bef': ")){removeSet.add(i); console.log("removing orphan \u9519\u8bef at",i+1);} });

console.log("total removing:",removeSet.size,"(expect 32+2=34)");
if(removeSet.size!==34){ console.log("ABORT: unexpected removal count"); process.exit(1); }

const kept=lines.filter((_,i)=>!removeSet.has(i));

// find TR.es / TR.ca closing braces in kept
function findTRClose(arr,lang){
  const trI=arr.findIndex(l=>l.indexOf("const TRANSLATIONS")===0);
  for(let i=trI;i<arr.length;i++){
    if(arr[i].trim()===lang+": {"){
      for(let j=i+1;j<arr.length;j++){ if(arr[j].trim()==="  },"){ return j; } }
    }
  }
  return -1;
}
const esEnd=findTRClose(kept,"es");
const caEnd=findTRClose(kept,"ca");
console.log("TR.es close at",esEnd+1,"TR.ca close at",caEnd+1);
if(esEnd<0||caEnd<0){console.log("ABORT");process.exit(1);}

// 15 entries each (重试 already exists in both TR tables)
const esNew=[
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
["\u6ca1\u6709\u5904\u7406\u6b65\u9aa4\u53ef\u663e\u793a\u3002","No hay pasos de procesamiento que mostrar."]
];
const caNew=[
["\u51c6\u5907","Preparant"],
["\u5904\u7406\u6750\u6599","Processant materials"],
["\u63d0\u53d6\u8bc1\u636e","Extraient evid\u00e8ncies"],
["\u7ec4\u88c5\u77e5\u8bc6","Muntant coneixement"],
["\u6821\u9a8c","Validant"],
["\u51b2\u7a81\u68c0\u67e5","Comprovant conflictes"],
["\u5b8c\u6210","Finalitzat"],
["\u9519\u8bef\u7c7b\u578b","Categoria d\\'error"],
["\u5efa\u8bae\u64cd\u4f5c","Acci\u00f3 recomanada"],
["\u53ef\u91cd\u8bd5","Reintentable"],
["\u4e0d\u53ef\u91cd\u8bd5","No reintentable"],
["\u5904\u7406\u6b65\u9aa4","Passos de processament"],
["\u6b63\u5728\u5904\u7406\u672c\u5802\u6750\u6599\uff0c\u53ef\u5173\u95ed\u9875\u9762\u7a7a\u540e\u56de\u6765\u67e5\u770b\u8fdb\u5ea6\u3002","S\\'est\u00e0 processant els materials de la sessi\u00f3; pot tancar la p\u00e0gina i tornar m\u00e9s tard."],
["\u6709\u6750\u6599\u5904\u7406\u5931\u8d25\uff0c\u5c55\u5f00\u67e5\u770b\u539f\u56e0\u4e0e\u5efa\u8bae\u64cd\u4f5c\u3002","Alguns materials han fallat; desplegui per veure la causa i l\\'acci\u00f3 recomanada."],
["\u6ca1\u6709\u5904\u7406\u6b65\u9aa4\u53ef\u663e\u793a\u3002","No hi ha passos de processament per mostrar."]
];

const out=[...kept];
const caL=caNew.map(([k,v])=>"    \u0027"+k+"\u0027: \u0027"+v+"\u0027,");
const esL=esNew.map(([k,v])=>"    \u0027"+k+"\u0027: \u0027"+v+"\u0027,");
for(let i=caL.length-1;i>=0;i--) out.splice(caEnd,0,caL[i]);
for(let i=esL.length-1;i>=0;i--) out.splice(esEnd,0,esL[i]);

fs.writeFileSync("src/web/app.js",out.join(crlf?"\r\n":"\n"),"utf8");
console.log("OK written",out.length,"lines");
