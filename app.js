/* 公共前端 helper：cssVar / crossPointer
 * mood.html 与 indicators.html 共用，消除双份维护。
 * 必须在 echarts.min.js 之后、各页面内联脚本之前加载。
 */
function cssVar(name){
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

// 十字准星：竖线定位某一天，横线给出该点在纵轴上的历史水位，便于对照
function crossPointer(){
  var c = cssVar("--chart-mark") || "#d4d4d4";
  return {
    type:"cross", snap:true,
    lineStyle:{color:c, type:"dashed", width:1},
    crossStyle:{color:c, type:"dashed", width:1},
    label:{
      backgroundColor:cssVar("--chart-tip-bg") || "#fff",
      borderColor:cssVar("--chart-tip-bd") || "#e8e8e8",
      borderWidth:1,
      color:cssVar("--chart-tip-fg") || "#111",
      fontSize:10
    }
  };
}
