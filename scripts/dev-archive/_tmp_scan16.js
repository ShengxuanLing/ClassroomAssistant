const fs=require("fs");
const src=fs.readFileSync("src/web/app.js","utf8");
const lines=src.split("\n");
// Search for all occurrences of 错误
lines.forEach((l,i)=>{
  if(l.includes("\u9519\u8bef")){
    console.log((i+1)+": "+l.trim().slice(0,150));
  }
});
