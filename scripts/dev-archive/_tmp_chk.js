const s=require("fs").readFileSync("src/web/app.js","utf8").split("\n");
const i18nS=s.findIndex(l=>l.indexOf("const I18N")===0);
let i18nE=-1; for(let i=i18nS;i<s.length;i++){if(s[i]==="};"){i18nE=i;break;}}
const keys=["\u51c6\u5907","\u5904\u7406\u6750\u6599","\u63d0\u53d6\u8bc1\u636e","\u7ec4\u88c5\u77e5\u8bc6","\u6821\u9a8c","\u51b2\u7a81\u68c0\u67e5","\u5b8c\u6210","\u91cd\u8bd5","\u9519\u8bef\u7c7b\u578b","\u5efa\u8bae\u64cd\u4f5c","\u53ef\u91cd\u8bd5","\u4e0d\u53ef\u91cd\u8bd5","\u5904\u7406\u6b65\u9aa4","\u6b63\u5728\u5904\u7406\u672c\u5802\u6750\u6599\uff0c\u53ef\u5173\u95ed\u9875\u9762\u7a7a\u540e\u56de\u6765\u67e5\u770b\u8fdb\u5ea6\u3002","\u6709\u6750\u6599\u5904\u7406\u5931\u8d25\uff0c\u5c55\u5f00\u67e5\u770b\u539f\u56e0\u4e0e\u5efa\u8bae\u64cd\u4f5c\u3002","\u6ca1\u6709\u5904\u7406\u6b65\u9aa4\u53ef\u663e\u793a\u3002"];
for(const k of keys){
  let n=0, hitLines=[];
  s.forEach((l,i)=>{ if(i>=i18nS&&i<=i18nE&&l.includes("\u0027"+k+"\u0027:")){n++;hitLines.push(i+1);} });
  if(n!==2) console.log(n+"x  "+k+"  lines:"+hitLines.join(","));
}
// print exact raw lines for the 2 long keys
[273,376].forEach(ln=>{});
