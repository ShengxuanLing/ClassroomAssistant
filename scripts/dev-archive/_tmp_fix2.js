const fs=require("fs");
let raw=fs.readFileSync("src/web/app.js","utf8");
const crlf=raw.includes("\r\n");
const lines=raw.split("\n");
function norm(l){return l.replace(/\r$/,"");}
const nlines=lines.map(norm);

const i18nS=nlines.findIndex(l=>l.indexOf("const I18N")===0);
let i18nE=-1; for(let i=i18nS;i<nlines.length;i++){if(nlines[i]==="};"){i18nE=i;break;}}
const trS=nlines.findIndex(l=>l.indexOf("const TRANSLATIONS")===0);
let trE=-1; for(let i=trS;i<nlines.length;i++){if(nlines[i]==="};"){trE=i;break;}}
console.log("I18N", i18nS+1, i18nE+1, "TR", trS+1, trE+1);

const keysToRemove=[
  "\u51c6\u5907","\u5904\u7406\u6750\u6599","\u63d0\u53d6\u8bc1\u636e","\u7ec4\u88c5\u77e5\u8bc6",
  "\u6821\u9a8c","\u51b2\u7a81\u68c0\u67e5","\u5b8c\u6210","\u91cd\u8bd5",
  "\u9519\u8bef\u7c7b\u578b","\u5efa\u8bae\u64cd\u4f5c","\u53ef\u91cd\u8bd5","\u4e0d\u53ef\u91cd\u8bd5",
  "\u5904\u7406\u6b65\u9aa4","\u6b63\u5728\u5904\u7406\u672c\u5802\u6750\u6599\uff0c\u53ef\u5173\u95ed\u9875\u9762\u7a7a\u540e\u56de\u6765\u67e5\u770b\u8fdb\u5ea6\u3002",
  "\u6709\u6750\u6599\u5904\u7406\u5931\u8d25\uff0c\u5c55\u5f00\u67e5\u770b\u539f\u56e0\u4e0e\u5efa\u8bae\u64cd\u4f5c\u3002",
  "\u6ca1\u6709\u5904\u7406\u6b65\u9aa4\u53ef\u663e\u793a\u3002"
];

// verify each key line exists exactly twice in I18N region (es + ca)
const esc=k=>k.replace(/[.*+?^${}()|[\]\\]/g,"\\$&");
for(const k of keysToRemove){
  const re=new RegExp("^\\s*\\'"+esc(k)+"\\':");
  let n=0; nlines.forEach((l,i)=>{ if(i>=i18nS&&i<=i18nE&&re.test(l)) n++; });
  if(n!==2) console.log("!! key "+JSON.stringify(k)+" found "+n+" times in I18N (expected 2)");
}

// remove lines in I18N region
const removeSet=new Set();
for(const k of keysToRemove){
  const re=new RegExp("^\\s*\\'"+esc(k)+"\\':");
  nlines.forEach((l,i)=>{ if(i>=i18nS&&i<=i18nE&&re.test(l)) removeSet.add(i); });
}
// remove orphan 错误 lines in TR region
nlines.forEach((l,i)=>{ if(i>=trS&&i<=trE&&/^\s*'\u9519\u8bef'\s*:/.test(l)) removeSet.add(i); });
console.log("lines to remove:",removeSet.size);

const kept=nlines.filter((_,i)=>!removeSet.has(i));
console.log("kept:",kept.length);

// find TR.es / TR.ca closing braces in kept array
function findTRBlockEnd(arr,lang){
  const trIdx=arr.findIndex(l=>l.indexOf("const TRANSLATIONS")===0);
  for(let i=trIdx;i<arr.length;i++){
    if(arr[i].trim()===lang+": {"){
      for(let j=i+1;j<arr.length;j++){
        if(arr[j].trim()==="  },") return j;
      }
    }
  }
  return -1;
}
const esEnd=findTRBlockEnd(kept,"es");
const caEnd=findTRBlockEnd(kept,"ca");
console.log("kept TR.es end:",esEnd+1,"TR.ca end:",caEnd+1);
if(esEnd<0||caEnd<0){console.log("ERROR: block end not found");process.exit(1);}

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
// check duplicates against existing TR keys
function capBlock(){
  const trStartRaw=raw.indexOf("const TRANSLATIONS = {");
  const endRaw=raw.indexOf("\n};",trStartRaw);
  const block=raw.slice(trStartRaw,endRaw);
  return block;
}
const dupCheck=(k,lang)=>{
  const trIdx=kept.findIndex(l=>l.indexOf("const TRANSLATIONS")===0);
  const b=kept.findIndex((l,i)=>i>trIdx&&l.trim()===lang+": {");
  for(let i=b+1;i<kept.length;i++){
    if(/^\s*'/.test(kept[i])&&kept[i].includes("\u0027"+k+"\u0027")) return true;
  }
  return false;
};
for(const [k,v] of esNew.concat(caNew)){
  if(kept.some(l=>l.includes("\u0027"+k+"\u0027: ")&&l.trim().startsWith("    \u0027"))) {
    // in TR region only
  }
}
// safer: verify none of the new es keys already in kept TR region lines with 'key': pattern in TR region
const trRegion=new Set();
for(let i=0;i<kept.length;i++){ /* compute after we know tr index */ }
const trIdxK=kept.findIndex(l=>l.indexOf("const TRANSLATIONS")===0);
for(const list of [esNew,caNew]){
  for(const k of list.map(x=>x[0])){
    const found=kept.slice(trIdxK).some(l=>/^\s*'\u0027/.test(l)&&l.includes("\u0027"+k+"\u0027"));
    // just check simple substring in TR region
    let hit=false;
    for(let i=trIdxK;i<kept.length;i++){ if(kept[i].includes("\u0027"+k+"\u0027:")){hit=true;break;} }
    if(hit) console.log("DUP WARNING: "+k+" already in TR region");
  }
}

// insert: ca first (index caEnd), then es (index esEnd); es comes before ca so do ca first
const esLines=esNew.map(([k,v])=>"    \u0027"+k+"\u0027: \u0027"+v+"\u0027,");
const caLines=caNew.map(([k,v])=>"    \u0027"+k+"\u0027: \u0027"+v+"\u0027,");
const out=[...kept];
for(let i=caLines.length-1;i>=0;i--) out.splice(caEnd,0,caLines[i]);
for(let i=esLines.length-1;i>=0;i--) out.splice(esEnd,0,esLines[i]);

const joiner=crlf?"\r\n":"\n";
fs.writeFileSync("src/web/app.js",out.join(joiner),"utf8");
console.log("written:",out.length,"lines, crlf:",crlf);
