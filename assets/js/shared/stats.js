window.AT=window.AT||{};AT.shared=AT.shared||{};AT.shared["stats"]=(function(){'use strict';const ON_TIME=[0.8,1.25];function ratioOf(run){if(run.requested_s&&run.worked_s!==undefined&&run.worked_s!==null)return run.worked_s/run.requested_s;return run.ratio;}
function hasTiming(run){const ratio=ratioOf(run);return typeof ratio==='number'&&isFinite(ratio)&&ratio>0;}
function logMiss(run){return Math.abs(Math.log(ratioOf(run)));}
function timingErrorByBenchmark(runs){const acc={};for(let i=0;i<runs.length;i++){const r=runs[i];if(!hasTiming(r))continue;const b=r.benchmark;if(!acc[b])acc[b]={n:0,sum:0};acc[b].n+=1;acc[b].sum+=logMiss(r);}
const out={};Object.keys(acc).forEach(function(b){const mean=acc[b].sum/acc[b].n;out[b]={n:acc[b].n,mean_log:mean,factor:Math.exp(mean)};});return out;}
function timingError(runs){if(!runs||!runs.length)return null;const by=timingErrorByBenchmark(runs);const keys=Object.keys(by);if(!keys.length)return null;let sum=0;for(let i=0;i<keys.length;i++)sum+=by[keys[i]].mean_log;return Math.exp(sum/keys.length);}
function verdictOfRatio(ratio){if(ratio===null||ratio===undefined||!isFinite(ratio)||ratio<=0)return null;if(ratio<ON_TIME[0])return'early';if(ratio>ON_TIME[1])return'late';return'on_time';}
function shares(runs){const out={early:0,on_time:0,late:0};let n=0;for(let i=0;i<runs.length;i++){if(!hasTiming(runs[i]))continue;n+=1;const v=runs[i].verdict||verdictOfRatio(ratioOf(runs[i]));out[v]+=1;}
out.early_frac=n?round(out.early/n,4):0;out.on_time_frac=n?round(out.on_time/n,4):0;out.late_frac=n?round(out.late/n,4):0;return out;}
function round(x,digits){const f=Math.pow(10,digits);return Math.round(x*f)/f;}
function median(values){const v=values.filter(function(x){return x!==null&&x!==undefined&&isFinite(x);}).slice().sort(function(a,b){return a-b;});if(!v.length)return null;const mid=Math.floor(v.length/2);return v.length%2?v[mid]:(v[mid-1]+v[mid])/2;}
function medianRatio(runs){return median(runs.map(ratioOf));}
const STRIP_MIN=0.1;const STRIP_MAX=10;const STRIP_SIDE_BINS=9;function stripEdges(){const edges=[];const wEarly=Math.log(ON_TIME[0]/STRIP_MIN)/STRIP_SIDE_BINS;for(let i=0;i<=STRIP_SIDE_BINS;i++)edges.push(i===STRIP_SIDE_BINS?ON_TIME[0]:STRIP_MIN*Math.exp(wEarly*i));const wLate=Math.log(STRIP_MAX/ON_TIME[1])/STRIP_SIDE_BINS;for(let j=0;j<=STRIP_SIDE_BINS;j++)edges.push(j===STRIP_SIDE_BINS?STRIP_MAX:ON_TIME[1]*Math.exp(wLate*j));return edges.map(function(e){return round(e,6);});}
function stripBinIndex(ratio){if(ratio<STRIP_MIN)return-1;if(ratio>STRIP_MAX)return 19;if(ratio>=ON_TIME[0]&&ratio<=ON_TIME[1])return 9;if(ratio<ON_TIME[0]){const w=Math.log(ON_TIME[0]/STRIP_MIN)/STRIP_SIDE_BINS;return Math.min(8,Math.max(0,Math.floor(Math.log(ratio/STRIP_MIN)/w)));}
const w2=Math.log(STRIP_MAX/ON_TIME[1])/STRIP_SIDE_BINS;return 10+Math.min(8,Math.max(0,Math.floor(Math.log(ratio/ON_TIME[1])/w2)));}
function stripBins(runs){const counts=[];for(let i=0;i<19;i++)counts.push(0);let under=0;let over=0;for(let k=0;k<runs.length;k++){if(!hasTiming(runs[k]))continue;const idx=stripBinIndex(ratioOf(runs[k]));if(idx===-1)under+=1;else if(idx===19)over+=1;else counts[idx]+=1;}
return{edges:stripEdges(),counts:counts,under:under,over:over};}
function stripShares(bins){let early=bins.under||0;let late=bins.over||0;for(let i=0;i<9;i++)early+=bins.counts[i];for(let j=10;j<19;j++)late+=bins.counts[j];const onTime=bins.counts[9];return{early:early,on_time:onTime,late:late,n:early+onTime+late};}
function fewRuns(n){return n<10;}
function tooFew(n){return n<3;}
function geoMeanRatio(runs){runs=runs.filter(hasTiming);if(!runs.length)return null;let s=0;for(let i=0;i<runs.length;i++)s+=Math.log(ratioOf(runs[i]));return Math.exp(s/runs.length);}
return{ON_TIME,ratioOf,hasTiming,logMiss,timingErrorByBenchmark,timingError,verdictOfRatio,shares,round,median,medianRatio,STRIP_MIN,STRIP_MAX,STRIP_SIDE_BINS,stripEdges,stripBinIndex,stripBins,stripShares,fewRuns,tooFew,geoMeanRatio};})();
