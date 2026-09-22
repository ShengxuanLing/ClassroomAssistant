const fs=require("fs");
const s=fs.readFileSync("src/web/app.js","utf8");
const lines=s.split("\n");
lines.forEach((l,i)=>{ if(l.indexOf("t(\u0027重试\u0027)")!==-1) console.log((i+1)+"|"+l.trim().slice(0,110)); });
console.log("---");
lines.forEach((l,i)=>{ if(l.indexOf("t(\u0027正在处理本堂材料")!==-1) console.log((i+1)+"|"+l.trim().slice(0,110)); });
lines.forEach((l,i)=>{ if(l.indexOf("t(\u0027有材料处理失败")!==-1) console.log((i+1)+"|"+l.trim().slice(0,110)); });
lines.forEach((l,i)=>{ if(l.indexOf("t(\u0027可重试\u0027)")!==-1 || l.indexOf("t(\u0027不可重试\u0027)")!==-1) console.log((i+1)+"|"+l.trim().slice(0,110)); });
