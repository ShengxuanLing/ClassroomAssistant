const fs=require("fs");
const raw=fs.readFileSync("src/web/app.js","utf8");
const crlf=raw.includes("\r\n");
const lines=raw.split("\n").map(l=>l.replace(/\r$/,""));
const i18nS=lines.findIndex(l=>l.indexOf("const I18N")===0);
let i18nE=-1; for(let i=i18nS;i<lines.length;i++){if(lines[i]==="};"){i18nE=i;break;}}
const trS=lines.findIndex(l=>l.indexOf("const TRANSLATIONS")===0);
let trE=-1; for(let i=trS;i<lines.length;i++){if(lines[i]==="};"){trE=i;break;}}
const removeSet=new Set();
const exactKeys=["\u51c6\u5907","\u5904\u7406\u6750\u6599","\u63d0\u53d6\u8bc1\u636e","\u7ec4\u88c5\u77e5\u8bc6","\u6821\u9a8c","\u51b2\u7a81\u68c0\u67e5","\u5b8c\u6210","\u91cd\u8bd5","\u9519\u8bef\u7c7b\u578b","\u5efa\u8bae\u64cd\u4f5c","\u53ef\u91cd\u8bd5","\u4e0d\u53ef\u91cd\u8bd5","\u5904\u7406\u6b65\u9aa4"];
for(const k of exactKeys){
  lines.forEach((l,i)=>{ if(i>=i18nS&&i<=i18nE){ if(l.trim().startsWith("\u0027"+k+"\u0027: ")) removeSet.add(i); } });
}
const longPrefixes=["\u6b63\u5728\u5904\u7406\u672c\u5802\u6750\u6599","\u6709\u6750\u6599\u5904\u7406\u5931\u8d25","\u6ca1\u6709\u5904\u7406\u6b65\u9aa4\u53ef\u663e\u793a\u3002"];
for(const p of longPrefixes){
  lines.forEach((l,i)=>{ if(i>=i18nS&&i<=i18nE){ if(l.trim().startsWith("\u0027"+p)&&l.indexOf("\u0027: ")!==-1) removeSet.add(i); } });
}
lines.forEach((l,i)=>{ if(i>=trS&&i<=trE&&l.trim().startsWith("\u0027\u9519\u8bef\u0027: ")){removeSet.add(i);} });
console.log("removing total:",removeSet.size);
if(removeSet.size!==34){process.exit(1);}
const kept=lines.filter((_,i)=>!removeSet.has(i));
function findTRClose(arr,lang,fromIdx){
  for(let i=fromIdx;i<arr.length;i++){
    if(arr[i].trim()===lang+": {"){
      for(let j=i+1;j<arr.length;j++){ if(arr[j].trim()==="  },"){ return j; } }
    }
  }
  return -1;
}
const trInKept=kept.findIndex(l=>l.indexOf("const TRANSLATIONS")===0);
const esEnd=findTRClose(kept,"es",trInKept);
const caEnd=findTRClose(kept,"ca",esEnd);
console.log("TR.es close:",esEnd+1,"TR.ca close:",caEnd+1);
if(esEnd<0||caEnd<0){process.exit(1);}
const escQ=v=>v.replace(/\u0027/g,"\\u0027");
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
["\u9519\u8bef\u7c7b\u578b","Categoria d\u0027error"],
["\u5efa\u8bae\u64cd\u4f5c","Acci\u00f3 recomanada"],
["\u53ef\u91cd\u8bd5","Reintentable"],
["\u4e0d\u53ef\u91cd\u8bd5","No reintentable"],
["\u5904\u7406\u6b65\u9aa4","Passos de processament"],
["\u6b63\u5728\u5904\u7406\u672c\u5802\u6750\u6599\uff0c\u53ef\u5173\u95ed\u9875\u9762\u7a7a\u540e\u56de\u6765\u67e5\u770b\u8fdb\u5ea6\u3002","S\u0027est\u00e0 processant els materials de la sessi\u00f3; pot tancar la p\u00e0gina i tornar m\u00e9s tard."],
["\u6709\u6750\u6599\u5904\u7406\u5931\u8d25\uff0c\u5c55\u5f00\u67e5\u770b\u539f\u56e0\u4e0e\u5efa\u8bae\u64cd\u4f5c\u3002","Alguns materials han fallat; desplegui per veure la causa i l\u0027acci\u00f3 recomanada."],
["\u6ca1\u6709\u5904\u7406\u6b65\u9aa4\u53ef\u663e\u793a\u3002","No hi ha passos de processament per mostrar."]
];
const out=[...kept];
const caL=caNew.map(([k,v])=>"    \u0027"+k+"\u0027: \u0027"+escQ(v)+"\u0027,");
const esL=esNew.map(([k,v])=>"    \u0027"+k+"\u0027: \u0027"+escQ(v)+"\u0027,");
for(let i=caL.length-1;i>=0;i--) out.splice(caEnd,0,caL[i]);
for(let i=esL.length-1;i>=0;i--) out.splice(esEnd,0,esL[i]);
fs.writeFileSync("src/web/app.js",out.join(crlf?"\r\n":"\n"),"utf8");
console.log("OK written",out.length,"lines, crlf:",crlf);
