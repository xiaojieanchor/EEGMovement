(function(){
 'use strict';
 const root=document.getElementById('questPreview');if(!root)return;
 const image=document.getElementById('questImage'),label=document.getElementById('questState'),detail=document.getElementById('questDetail');
 let seq=0,last=-Infinity,pending=false,frame=null,sourceFresh=false;
 function expire(){if(!sourceFresh||performance.now()-last>=2000)image.hidden=true;}
 image.onload=()=>{if(!frame)return;last=frame.at-frame.age;seq=frame.seq;frame=null;image.hidden=!sourceFresh||performance.now()-last>=2000;};
 image.onerror=()=>{frame=null;last=-Infinity;image.hidden=true;label.textContent='画面读取失败';detail.textContent='正在重试；旧画面已隐藏。';};
 async function poll(){if(pending)return;pending=true;const start=performance.now();try{
  const response=await fetch('/api/quest/status',{cache:'no-store',signal:AbortSignal.timeout(1500)});if(!response.ok)throw Error();
  const state=await response.json(),age=state.frame_age_ms+performance.now()-start;
  sourceFresh=!!state.enabled&&!!state.mirror_fresh&&Number.isFinite(age)&&age<2000;
  if(!sourceFresh){image.hidden=true;label.textContent='等待Quest新鲜画面';detail.textContent=state.enabled?'请确认头显应用正在运行；过期画面不继续显示。':'Quest局域网服务未启动';return;}
  if(frame&&performance.now()-frame.at>3000){frame=null;image.removeAttribute('src');}
  if(!frame&&(seq!==state.frame_seq||image.hidden)){frame={seq:state.frame_seq,at:performance.now(),age};image.src='/api/quest/frame.jpg?seq='+state.frame_seq+'&t='+Math.round(start);}
  expire();label.textContent=image.hidden?'Quest已连接 · 画面接收中':'Quest应用实时预览';
  detail.textContent=image.hidden?'等待画面实际读取完成。':'显示画面年龄 '+Math.round(performance.now()-last)+'ms · 帧 '+seq+' · 单眼应用视图';
 }catch(e){sourceFresh=false;image.hidden=true;label.textContent='画面连接未确认';detail.textContent='请检查中枢与局域网连接。'}finally{pending=false;}}
 const full=document.getElementById('questFullscreen');
 if(full)full.onclick=async()=>{try{if(document.fullscreenElement)await document.exitFullscreen();else await root.requestFullscreen();}catch(e){detail.textContent='浏览器未允许全屏，可使用独立窗口查看。';}};
 setInterval(expire,200);setInterval(poll,200);poll();
})();
