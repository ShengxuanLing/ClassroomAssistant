const fs=require("fs");
const src=fs.readFileSync("src/web/app.js","utf8");
const lines=src.split("\n");
// Find ALL lines containing 错误
lines.forEach((l,i)=>{
  if(l.indexOf("\u9519\u8bef")!==-1){
    console.log((i+1)+": "+l.trim().slice(0,130));
  }
});
