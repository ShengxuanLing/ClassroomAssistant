const fs=require("fs");
const src=fs.readFileSync("src/web/app.js","utf8");
const lines=src.split("\n");
const keys=["准备","处理材料","提取证据","组装知识","校验","冲突检查","完成","重试","错误类型","建议操作","可重试","不可重试","处理步骤","正在处理本堂材料，可关闭页面稍后回来查看进度。","有材料处理失败，展开查看原因与建议操作。","没有处理步骤可显示。"];
for(const k of keys){
  const hits=[];
  lines.forEach((l,li)=>{ if(l.indexOf("\u0027"+k+"\u0027")!==-1) hits.push(li+1); });
  console.log(k+" -> lines: "+hits.join(","));
}
