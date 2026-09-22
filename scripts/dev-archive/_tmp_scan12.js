const fs=require("fs");
const src=fs.readFileSync("src/web/app.js","utf8");
const lines=src.split("\n");
// Find the exact t() call with 还没有处理任务
lines.forEach((l,i)=>{
  if(l.indexOf("\u8fd8\u6ca1\u6709\u5904\u7406\u4efb\u52a1")!==-1){
    // print the exact substring around t(
    const idx=l.indexOf("t(");
    console.log((i+1)+":", JSON.stringify(l.slice(idx, idx+80)));
  }
});
console.log("---");
// Find the TRANSLATIONS key
lines.forEach((l,i)=>{
  if(l.indexOf("\u8fd8\u6ca1\u6709\u5904\u7406\u4efb\u52a1")!==-1 && i>489){
    console.log((i+1)+" (TR):", JSON.stringify(l.slice(0,100)));
  }
});
