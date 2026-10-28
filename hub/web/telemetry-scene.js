'use strict';
(function(root){root.EEGTelemetryScene={create(canvas,{dark=false}={}){let trail=[],lastReceiver='',lastSeq=-1,shown={x:0,z:0,yaw:0},lastFrame=performance.now();const palette={"#f6faf7": "#0d1826", "#e9f1eb": "#101e2f", "#edf4ed": "#102437", "#d8e6d9": "#21384b", "#bdd9c8": "#355568", "#bfd3c3": "#3e6272", "#52616e": "#91a9bf", "#84b69e": "#3996a2", "#4caa87": "#5ce2cb"};const tone=c=>dark?(palette[c]||c):c;
function render(peer,now=performance.now(),fresh=false){if(fresh&&peer){const t=peer.telemetry||{};if(peer.receiver_id!==lastReceiver){lastReceiver=peer.receiver_id;trail=[];lastSeq=-1;shown={x:t.x||0,z:t.z||0,yaw:t.yaw||0}}if(t.applied_seq!==lastSeq){lastSeq=t.applied_seq;const point=[t.x||0,t.z||0];if(!trail.length||Math.hypot(point[0]-trail.at(-1)[0],point[1]-trail.at(-1)[1])>.005)trail.push(point);if(trail.length>800)trail.shift()}}const box=canvas.getBoundingClientRect(),dpr=devicePixelRatio||1,w=box.width,h=box.height;if(canvas.width!==Math.round(w*dpr)||canvas.height!==Math.round(h*dpr)){canvas.width=Math.round(w*dpr);canvas.height=Math.round(h*dpr)}const c=canvas.getContext('2d');c.setTransform(dpr,0,0,dpr,0,0);c.clearRect(0,0,w,h);const grad=c.createLinearGradient(0,0,w,h);grad.addColorStop(0,tone('#f6faf7'));grad.addColorStop(1,tone('#e9f1eb'));c.fillStyle=grad;c.fillRect(0,0,w,h);
 const scale=Math.max(1,Math.min(32,(w-40)/29,(h-65)/14)),project=(x,y,z)=>[w*.5+(x-z)*scale*.72,h*.49+(x+z)*scale*.34-y*scale];
 function path(points,color,width=1,close=false){c.beginPath();points.forEach((v,i)=>{const p=project(...v);i?c.lineTo(...p):c.moveTo(...p)});if(close)c.closePath();c.strokeStyle=color;c.lineWidth=width;c.stroke()}
 function polygon(points,fill){c.beginPath();points.forEach((v,i)=>{const p=project(...v);i?c.lineTo(...p):c.moveTo(...p)});c.closePath();c.fillStyle=fill;c.fill()}
 polygon([[-10,0,-10],[-10,0,10],[10,0,10],[10,0,-10]],tone('#edf4ed'));
 for(let i=-10;i<=10;i++){path([[i,0,-10],[i,0,10]],i===0?tone('#bdd9c8'):tone('#d8e6d9'));path([[-10,0,i],[10,0,i]],i===0?tone('#bdd9c8'):tone('#d8e6d9'))}
 path([[-10,0,-10],[-10,0,10],[10,0,10],[10,0,-10]],tone('#bfd3c3'),2,true);
 const ring=Array.from({length:65},(_,i)=>[Math.cos(i*Math.PI/32)*1.3,.01,Math.sin(i*Math.PI/32)*1.3]);path(ring,tone('#84b69e'),2);
 if(trail.length>1)path(trail.map(p=>[p[0],.035,p[1]]),tone('#4caa87'),2);
 const t=peer?.telemetry||{};const dt=Math.min(.1,(now-lastFrame)/1000);lastFrame=now;const alpha=matchMedia('(prefers-reduced-motion: reduce)').matches?1:1-Math.exp(-dt*14);if(fresh){shown.x+=(t.x-shown.x)*alpha;shown.z+=(t.z-shown.z)*alpha;let turn=((t.yaw-shown.yaw+540)%360)-180;shown.yaw=(shown.yaw+turn*alpha+360)%360}
 function pillar(x,z){const p=project(x,0,z),q=project(x,1.3,z);c.strokeStyle='#7f9e88';c.lineWidth=5;c.beginPath();c.moveTo(...p);c.lineTo(...q);c.stroke();c.fillStyle='#62b78c';c.beginPath();c.arc(q[0],q[1],4,0,Math.PI*2);c.fill();c.fillStyle='#c5dacb';c.beginPath();c.ellipse(p[0],p[1]+3,9,4,0,0,Math.PI*2);c.fill()}
 const entities=[{x:-5,z:-5,pillar:true},{x:-5,z:5,pillar:true},{x:5,z:-5,pillar:true},{x:5,z:5,pillar:true},{x:shown.x,z:shown.z,avatar:true}].sort((a,b)=>a.x+a.z-b.x-b.z);
 for(const entity of entities){if(entity.pillar){pillar(entity.x,entity.z);continue}const x=entity.x,z=entity.z,a=shown.yaw*Math.PI/180;const local=(lx,y,lz)=>[x+lx*Math.cos(a)+lz*Math.sin(a),y,z-lx*Math.sin(a)+lz*Math.cos(a)];const foot=project(x,0,z);c.fillStyle='#638a7624';c.beginPath();c.ellipse(foot[0],foot[1]+2,scale*.48,scale*.17,0,0,Math.PI*2);c.fill();const moving=fresh&&!matchMedia('(prefers-reduced-motion: reduce)').matches?Math.min(1,Math.abs(t.actual_speed||0)/.6):0,swing=Math.sin(now*.009)*.17*moving;
  for(const [side,sign] of [[-.15,1],[.15,-1]])path([local(side,.82,0),local(side,.38,swing*sign),local(side,.08,-swing*sign)],fresh?'#456a57':'#a6b9ad',Math.max(3,scale*.17));
  polygon([local(-.26,.77,-.15),local(.26,.77,-.15),local(.25,1.38,-.12),local(-.25,1.38,-.12)],fresh?'#2e9e7a':'#a9bfb0');
  polygon([local(-.25,1.38,-.12),local(.25,1.38,-.12),local(.25,1.38,.15),local(-.25,1.38,.15)],fresh?'#73c2a3':'#b9cbbf');
  polygon([local(.26,.77,-.15),local(.26,.77,.15),local(.25,1.38,.15),local(.25,1.38,-.12)],fresh?'#248768':'#96afa0');
  for(const side of [-1,1])path([local(side*.34,1.32,0),local(side*.38,.87,swing*side)],fresh?'#c0d4c2':'#c7d3ca',Math.max(3,scale*.13));
  const head=project(...local(0,1.67,0));c.fillStyle=fresh?'#d8e5d7':'#dbe3dc';c.beginPath();c.ellipse(head[0],head[1],scale*.22,scale*.25,0,0,Math.PI*2);c.fill();path([local(-.16,1.68,.18),local(.16,1.68,.18)],fresh?'#42775c':'#a4b9ab',Math.max(2,scale*.09));
  const nose=project(...local(0,.04,.95)),back=project(...local(0,.04,.55));c.strokeStyle=fresh?'#72b497':'#b1c4b6';c.lineWidth=2;c.beginPath();c.moveTo(...back);c.lineTo(...nose);c.stroke();c.fillStyle=c.strokeStyle;c.beginPath();c.arc(...nose,2.5,0,Math.PI*2);c.fill();
 }
 c.font='11px Consolas,monospace';c.fillStyle=tone('#52616e');c.fillText('+Z / 前进',18,h-23);c.textAlign='right';c.fillText('20 × 20 m  ·  执行遥测',w-18,h-23);c.textAlign='left'
}
return {render,setTheme(value){dark=!!value}};}};})(globalThis);
