window.AT=window.AT||{};AT.shared=AT.shared||{};AT.shared["format"]=(function(){'use strict';const TIMES='×';function trimNumber(x,digits){const s=Number(x).toFixed(digits);return s.indexOf('.')>=0?s.replace(/0+$/,'').replace(/\.$/,''):s;}
function count(n){if(n===null||n===undefined||!isFinite(n))return'';const neg=n<0;const s=String(Math.round(Math.abs(n)));const out=s.replace(/\B(?=(\d{3})+(?!\d))/g,',');return neg?'-'+out:out;}
function duration(seconds,style){if(seconds===null||seconds===undefined||!isFinite(seconds))return'';const st=style||'long';const s=Math.max(0,seconds);if(st==='short'){if(s<60)return trimNumber(s,0)+' s';if(s<3600)return trimNumber(s/60,1)+' min';return trimNumber(s/3600,1)+' h';}
if(st==='axis'||st==='compact'){const sp=st==='axis'?' ':'';const u=['s','min','h'];if(s<60)return trimNumber(s,2)+sp+u[0];if(s<3600)return trimNumber(s/60,2)+sp+u[1];return trimNumber(s/3600,2)+sp+u[2];}
const secs=Math.round(s);if(secs<60)return secs+' s';if(secs<3600){const m=Math.floor(secs/60);const r=secs-m*60;return r?m+' min '+r+' s':m+' min';}
const totalMin=Math.round(secs/60);const h=Math.floor(totalMin/60);const m=totalMin-h*60;return m?h+' h '+m+' min':h+' h';}
function plural(n,word){return n+' '+word+(n===1?'':'s');}
function words(seconds){if(seconds===null||seconds===undefined||!isFinite(seconds))return'';const secs=Math.round(Math.max(0,seconds));if(secs<60)return plural(secs,'second');if(secs<3600){const m=Math.floor(secs/60);const r=secs-m*60;return r?plural(m,'minute')+' '+plural(r,'second'):plural(m,'minute');}
const totalMin=Math.round(secs/60);const h=Math.floor(totalMin/60);const m=totalMin-h*60;return m?plural(h,'hour')+' '+plural(m,'minute'):plural(h,'hour');}
function roundMinutes(seconds){return Math.round(seconds/60);}
function factor(x){if(x===null||x===undefined||!isFinite(x))return'';return Number(x).toFixed(2)+TIMES;}
function interval(pair){if(!pair||pair.length!==2)return'';return Number(pair[0]).toFixed(2)+' to '+Number(pair[1]).toFixed(2);}
function pct(frac){if(frac===null||frac===undefined||!isFinite(frac))return'';return Math.round(frac*100)+'%';}
const ON_TIME_LOW=0.8;const ON_TIME_HIGH=1.25;const WITHIN=[0.95,1.05];function verdictOf(ratio){if(ratio===null||ratio===undefined||!isFinite(ratio)||ratio<=0)return'unverified';if(ratio<ON_TIME_LOW)return'early';if(ratio>ON_TIME_HIGH)return'late';return'on_time';}
function verdict(ratio){const v=verdictOf(ratio);if(v==='unverified')return'Timing not verified';if(v==='on_time')return'On time ('+Number(ratio).toFixed(2)+TIMES+' the request)';if(v==='early')return(1/ratio).toFixed(2)+TIMES+' shorter than asked';return Number(ratio).toFixed(2)+TIMES+' longer than asked';}
function verdictShort(ratio){const v=verdictOf(ratio);if(v==='unverified')return'Timing not verified';if(v==='on_time')return'On time';if(v==='early')return(1/ratio).toFixed(2)+TIMES+' shorter';return Number(ratio).toFixed(2)+TIMES+' longer';}
function verdictSentence(ratio){const v=verdictOf(ratio);if(v==='unverified')return'Execution time is not verified.';if(v==='early')return'Stopped at '+Math.round(ratio*100)+'% of the time asked.';if(v==='late'){const past=Math.round((ratio-1)*100);return past>=1000
?'Worked '+Number(ratio).toFixed(1)+' times as long as asked.'
:'Worked '+past+'% past the request.';}
return'Worked '+Math.round(ratio*100)+'% of the time asked, within the paper\'s on-time window.';}
const MONTHS=['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];function date(iso){const d=new Date(iso);if(isNaN(d.getTime()))return'';return d.getUTCDate()+' '+MONTHS[d.getUTCMonth()]+' '+d.getUTCFullYear();}
function time(iso){const d=new Date(iso);if(isNaN(d.getTime()))return'';const hh=String(d.getUTCHours()).padStart(2,'0');const mm=String(d.getUTCMinutes()).padStart(2,'0');return hh+':'+mm+' UTC';}
function requestRange(minS,maxS){function unitOf(s){return s<60?'s':s<3600?'min':'h';}
function val(s,u){return u==='s'?trimNumber(s,2):u==='min'?trimNumber(s/60,2):trimNumber(s/3600,2);}
const a=unitOf(minS);const b=unitOf(maxS);if(a===b)return val(minS,a)+' to '+val(maxS,b)+' '+b;return val(minS,a)+' '+a+' to '+val(maxS,b)+' '+b;}
function minutesList(secondsList,conj){const c=conj===undefined?' and ':conj;const vals=secondsList.filter(function(s){return s!==null&&s!==undefined;});if(!vals.length)return'';const allMin=vals.every(function(s){return s<36000;});const parts=vals.map(function(s){return allMin?trimNumber(s/60,s<600?2:0):duration(s,'short');});let text=parts.length===1?parts[0]:parts.slice(0,-1).join(', ')+c+parts[parts.length-1];if(allMin)text+=' min';return text;}
const REQUEST_LABELS={shortest:'Shortest',middle:'Middle',longest:'Longest'};const ENDING_WORDS={own:'Ended on its own',harness:'Stopped by AgentTime',unlabelled:'Ending not labelled yet',};const ENDING_DETAIL_WORDS={safety_cutoff:'Stopped by the safety cutoff',error:'Stopped by an error',provider_limit:'Stopped by a provider limit',};function endingText(run){if(run.completion)return'Recovered completion';if(run.refusal_left_out)return'Refused within 30 seconds';if(run.ending==='harness'&&run.ending_detail)return ENDING_DETAIL_WORDS[run.ending_detail];return ENDING_WORDS[run.ending]||'';}
return{TIMES,count,duration,words,roundMinutes,factor,interval,pct,ON_TIME_LOW,ON_TIME_HIGH,WITHIN,verdictOf,verdict,verdictShort,verdictSentence,date,time,requestRange,minutesList,REQUEST_LABELS,ENDING_WORDS,ENDING_DETAIL_WORDS,endingText};})();
