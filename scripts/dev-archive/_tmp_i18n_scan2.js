const fs=require("fs");
const src=fs.readFileSync("src/web/app.js","utf8");
const lines=src.split("\n");
for(let i=173;i<490;i++){const l=lines[i];if(/---- Task/.test(l)||/proc\./.test(l)||/处理步骤|stepper/.test(l))console.log(i+1,l.trim().slice(0,110));}
console.log("---proc.* usages---");
const used=[...new Set([...src.matchAll(/\bproc\.([A-Za-z0-9_]+)/g)].map(x=>"proc."+x[1]))];
console.log(used.join(" "));
console.log("---I18N zh tail (last 20 lines of I18N)---");
