const fs=require("fs");
const s=fs.readFileSync("src/web/app.js","utf8");
const lines=s.split("\n");
const trStart=lines.findIndex(l=>l.indexOf("const TRANSLATIONS")===0);
console.log("TR start line:", trStart+1);
const targets=["正在处理本堂材料","有材料处理失败","可重试","不可重试","处理步骤","错误类型","建议操作","准备","处理材料","提取证据","组装知识","校验","冲突检查","完成"];
for(const t of targets){
  const hits=[];
  lines.forEach((l,i)=>{
    if(l.indexOf(t)!==-1 && !(i>=173&&i<trStart)){
      // exclude TRANSLATIONS es/ca block itself
      hits.push(i+1);
    }
  });
  // filter: exclude lines that are in I18N block (174-467) and TRANSLATIONS es block (490-657), ca block (658-trEnd)
  let trEnd=-1;
  for(let i=trStart;i<lines.length;i++){ if(lines[i].trim()==="};"){trEnd=i;break;} }
  console.log(trEnd? "trEnd:"+trEnd : "");
  const codeHits=hits.filter(h=>!(h>=174&&h<468)&&!(h>=491&&h<=trEnd+1));
  console.log(t, "code lines:", codeHits.join(",")||"NONE");
}
