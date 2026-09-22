const fs=require("fs");
const s=fs.readFileSync("src/web/app.js","utf8");
const lines=s.split("\n");
const trStart=lines.findIndex(l=>l.indexOf("const TRANSLATIONS")===0);
let trEnd=-1;
for(let i=trStart;i<lines.length;i++){ if(lines[i]==="};"){trEnd=i;break;} }
console.log("TR block lines:", trStart+1, "to", trEnd+1);
const targets=["正在处理本堂材料","有材料处理失败","可重试","不可重试","处理步骤","错误类型","建议操作","准备","处理材料","提取证据","组装知识","校验","冲突检查","完成"];
for(const t of targets){
  const hits=[];
  lines.forEach((l,i)=>{ if(l.indexOf(t)!==-1 && i<trStart) hits.push(i+1); });
  console.log(t, ":", hits.length ? hits.join(",") : "NONE outside TR block");
}
