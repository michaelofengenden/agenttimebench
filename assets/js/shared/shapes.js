window.AT=window.AT||{};AT.shared=AT.shared||{};AT.shared["shapes"]=(function(){'use strict';const SHAPES=['triangle','diamond','circle','square'];function n2(x){return Math.round(x*100)/100;}
function triangleGeom(cx,cy,s){const a=1.2*s;const h=a*Math.sqrt(3)/2;return{top:cy-h*0.58,base:cy+h*0.42,half:a/2};}
function shapePath(shape,cx,cy,s){const size=s||7;if(shape==='square'){const h=0.44*size;return'M'+n2(cx-h)+' '+n2(cy-h)+'H'+n2(cx+h)+'V'+n2(cy+h)+'H'+n2(cx-h)+'Z';}
if(shape==='diamond'){const d=0.6*size;return'M'+n2(cx)+' '+n2(cy-d)+'L'+n2(cx+d)+' '+n2(cy)+'L'+n2(cx)+' '+n2(cy+d)+'L'+n2(cx-d)+' '+n2(cy)+'Z';}
if(shape==='triangle'){const t=triangleGeom(cx,cy,size);return'M'+n2(cx)+' '+n2(t.top)+'L'+n2(cx+t.half)+' '+n2(t.base)+'L'+n2(cx-t.half)+' '+n2(t.base)+'Z';}
const r=size/2;return'M'+n2(cx-r)+' '+n2(cy)+'a'+n2(r)+' '+n2(r)+' 0 1 0 '+n2(2*r)+' 0a'+n2(r)+' '+n2(r)+' 0 1 0 '+n2(-2*r)+' 0Z';}
function drawShape(ctx,shape,x,y,s){const size=s||7;ctx.beginPath();if(shape==='square'){const h=0.44*size;ctx.rect(x-h,y-h,2*h,2*h);}else if(shape==='diamond'){const d=0.6*size;ctx.moveTo(x,y-d);ctx.lineTo(x+d,y);ctx.lineTo(x,y+d);ctx.lineTo(x-d,y);ctx.closePath();}else if(shape==='triangle'){const t=triangleGeom(x,y,size);ctx.moveTo(x,t.top);ctx.lineTo(x+t.half,t.base);ctx.lineTo(x-t.half,t.base);ctx.closePath();}else{ctx.arc(x,y,size/2,0,Math.PI*2);}}
function shapeSVG(shape,size,opts){const o=opts||{};const box=size||12;const s=box/1.3;const c=box/2;const color=o.color||'currentColor';const paint=o.hollow
?'fill:none;stroke:'+color+';stroke-width:1.5'
:'fill:'+color;return'<svg class="shape shape-'+shape+'" width="'+box+'" height="'+box+'" viewBox="0 0 '+box+' '+box+'" aria-hidden="true" focusable="false"><path d="'+shapePath(shape,c,c+(shape==='triangle'?0.4:0),o.hollow?s*0.9:s)+'" style="'+paint+'"/></svg>';}
const AGENT_KEYS=['astra','sol','fable','muse'];const AGENT_SHAPES={astra:'triangle',sol:'diamond',fable:'circle',muse:'square'};function agentKey(agent){const a=String(agent||'').toLowerCase();for(let i=0;i<AGENT_KEYS.length;i++)if(a===AGENT_KEYS[i]||a.indexOf(AGENT_KEYS[i])>=0)return AGENT_KEYS[i];return null;}
function agentColor(agent){const k=agentKey(agent);return k?'var(--'+k+')':'var(--ink)';}
return{SHAPES,shapePath,drawShape,shapeSVG,AGENT_KEYS,AGENT_SHAPES,agentKey,agentColor};})();
