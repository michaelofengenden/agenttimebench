(function(){'use strict';var AT=window.AT=window.AT||{};AT.search=AT.search||{};var doc=document;var MAX_RESULTS=12;var RUN_RE=/^[a-z2-7]{10}$/;var GROUP_LABEL={run:'Run',page:'Pages',agent:'Agents',benchmark:'Benchmarks',task:'Tasks'};var dialogBuilt=false;var overlay,panel,input,listbox,emptyEl,closeBtn,countEl;var opener=null;var active=-1;var results=[];var index=null;function stripMarks(s){var out='';for(var i=0;i<s.length;i++){var c=s.charCodeAt(i);if(c<0x300||c>0x36f)out+=s.charAt(i);}
return out;}
function tokens(s){var n=stripMarks(String(s||'').toLowerCase().normalize('NFD'))
.replace(/[^a-z0-9]+/g,' ')
.trim();return n?n.split(' '):[];}
function matchScore(queryTokens,entryTokens){var score=0;for(var i=0;i<queryTokens.length;i++){var q=queryTokens[i],best=-1;for(var j=0;j<entryTokens.length;j++){if(entryTokens[j]===q){best=2;break;}
if(best<1&&entryTokens[j].indexOf(q)===0)best=1;}
if(best<0)return-1;score+=best;}
return score;}
function splitRows(blob){return blob?blob.split('\n').map(function(r){return r.split('\t');}):[];}
function buildIndex(raw){var pages=splitRows(raw.p).map(function(r){return{kind:'page',type:'Page',title:r[0],href:r[1],sub:r[2],tok:tokens(r[0])};});var agents=splitRows(raw.a).map(function(r){return{kind:'agent',type:'Agent',title:r[0],href:'agents/'+r[1]+'/',sub:r[2],tok:tokens(r[0])};});var benchRows=splitRows(raw.b);var benchmarks=benchRows.map(function(r){return{kind:'benchmark',type:'Benchmark',title:r[0],slug:r[1],href:'benchmarks/'+r[1]+'/',tok:tokens(r[0])};});var taskGroups=raw.t?raw.t.split('\n\n'):[];var tasks=[];for(var g=0;g<benchRows.length;g++){var bench=benchmarks[g];var rows=splitRows(taskGroups[g]||'');for(var i=0;i<rows.length;i++){var r=rows[i];if(!r[0])continue;tasks.push({kind:'task',type:bench.title,title:r[0],sub:r[1],href:'benchmarks/'+bench.slug+'/'+r[2]+'/',tok:tokens(r[0]),});}
if(bench)bench.sub=rows.length+(rows.length===1?' task':' tasks');}
var runSet=Object.create(null);var rid=raw.r||'';for(var p=0;p+10<=rid.length;p+=10)runSet[rid.slice(p,p+10)]=true;return{pages:pages,agents:agents,benchmarks:benchmarks,tasks:tasks,runs:runSet};}
function search(query){var q=tokens(query);var out=[];if(index&&q.length){var runId=query.trim().toLowerCase();if(RUN_RE.test(runId)&&index.runs[runId]){out.push({kind:'run',type:'Run',title:'Run '+runId,sub:'Asked, worked and the verdict for this run.',href:'runs/'+runId+'/'});}
['pages','agents','benchmarks','tasks'].forEach(function(cat){if(out.length>=MAX_RESULTS)return;var list=index[cat];var scored=[];for(var i=0;i<list.length;i++){var s=matchScore(q,list[i].tok);if(s>=0)scored.push({s:s,i:i,r:list[i]});}
scored.sort(function(a,b){return b.s-a.s;});for(var k=0;k<scored.length&&out.length<MAX_RESULTS;k++)out.push(scored[k].r);});}
return out.slice(0,MAX_RESULTS);}
function escHtml(s){return String(s==null?'':s).replace(/[&<>"']/g,function(c){return c==='&'?'&amp;':c==='<'?'&lt;':c==='>'?'&gt;':c==='"'?'&quot;':'&#39;';});}
function setCount(n){if(!countEl)return;countEl.textContent=n?(n+(n===1?' result':' results')):'';}
function renderResults(query,list){results=list;active=list.length?0:-1;setCount(list.length);if(!list.length){listbox.innerHTML='';listbox.hidden=true;emptyEl.hidden=false;if(!query.trim()){emptyEl.querySelector('.search-empty-text').textContent='Search for a task, a benchmark, an agent or a run.';}else{emptyEl.querySelector('.search-empty-text').textContent='Nothing matches "'+query+'". Try a benchmark name or a few words of a task title.';}
input.setAttribute('aria-expanded','false');input.removeAttribute('aria-activedescendant');return;}
emptyEl.hidden=true;listbox.hidden=false;input.setAttribute('aria-expanded','true');var html='';var lastKind=null;for(var i=0;i<list.length;i++){var r=list[i];if(r.kind!==lastKind){html+='<li class="search-group-label" role="presentation">'+escHtml(GROUP_LABEL[r.kind]||r.kind)+'</li>';lastKind=r.kind;}
var subClass=r.kind==='task'||r.kind==='run'?' mono':'';var typeHtml=r.kind==='task'&&r.type?'<span class="search-type">'+escHtml(r.type)+'</span>':'';html+='<li role="option" id="search-opt-'+i+'" class="search-option" aria-selected="'+(i===active?'true':'false')+'" data-i="'+i+'">'
+typeHtml
+'<span class="search-title">'+escHtml(r.title)+'</span>'
+(r.sub?'<span class="search-sub'+subClass+'">'+escHtml(r.sub)+'</span>':'')
+'</li>';}
listbox.innerHTML=html;input.setAttribute('aria-activedescendant',active>=0?'search-opt-'+active:'');}
function setActive(i){if(!results.length)return;active=(i+results.length)%results.length;var opts=listbox.querySelectorAll('.search-option');for(var j=0;j<opts.length;j++)opts[j].setAttribute('aria-selected',j===active?'true':'false');input.setAttribute('aria-activedescendant','search-opt-'+active);var el=opts[active];if(el&&el.scrollIntoView)el.scrollIntoView({block:'nearest'});}
function go(i){var r=results[i];if(!r)return;location.href=AT.href(r.href);}
var pendingCbs=null;function ensureData(cb){if(index){cb();return;}
if(pendingCbs){pendingCbs.push(cb);return;}
pendingCbs=[cb];AT.loadScript('assets/data/search.js',function(){var raw=(window.AT_DATA&&window.AT_DATA.search)||{p:'',a:'',b:'',t:'',r:''};index=buildIndex(raw);var q=pendingCbs||[];pendingCbs=null;q.forEach(function(f){f();});});}
function build(){if(dialogBuilt)return;dialogBuilt=true;overlay=doc.createElement('div');overlay.className='search-overlay';overlay.hidden=true;overlay.innerHTML=
'<div class="search-dialog" role="dialog" aria-modal="true" aria-label="Find a task or benchmark">'
+'<div class="search-field">'
+'<svg class="icon" width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="8.5" cy="8.5" r="5.25"/><path d="M12.4 12.4 17 17"/></svg>'
+'<input id="search-input" type="text" role="combobox" aria-expanded="false" aria-controls="search-listbox" aria-autocomplete="list" aria-activedescendant="" aria-label="Find a task or benchmark" autocomplete="off" spellcheck="false" placeholder="Find a task or benchmark">'
+'<button type="button" class="icon-btn" data-search-close aria-label="Close search"><svg class="icon" width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4.5 4.5l11 11M15.5 4.5l-11 11"/></svg></button>'
+'</div>'
+'<p id="search-count" class="search-count" aria-live="polite"></p>'
+'<ul id="search-listbox" class="search-listbox" role="listbox" aria-label="Search results"></ul>'
+'<div id="search-empty" class="search-empty" hidden><p class="search-empty-text"></p><p class="search-empty-links"><a href="'+AT.href('tasks/')+'">All tasks</a><a href="'+AT.href('benchmarks/')+'">All benchmarks</a></p></div>'
+'</div>';doc.body.appendChild(overlay);panel=overlay.querySelector('.search-dialog');input=overlay.querySelector('#search-input');listbox=overlay.querySelector('#search-listbox');emptyEl=overlay.querySelector('#search-empty');closeBtn=overlay.querySelector('[data-search-close]');countEl=overlay.querySelector('#search-count');overlay.addEventListener('mousedown',function(e){if(e.target===overlay)close();});closeBtn.addEventListener('click',close);input.addEventListener('input',function(){ensureData(function(){renderResults(input.value,search(input.value));});});listbox.addEventListener('click',function(e){var li=e.target.closest?e.target.closest('[data-i]'):null;if(li)go(parseInt(li.getAttribute('data-i'),10));});overlay.addEventListener('keydown',function(e){if(e.key==='Escape'){e.preventDefault();close();return;}
if(e.key==='ArrowDown'){e.preventDefault();setActive(active<0?0:active+1);return;}
if(e.key==='ArrowUp'){e.preventDefault();setActive(active<0?results.length-1:active-1);return;}
if(e.key==='Enter'){e.preventDefault();go(active);return;}
if(e.key==='Tab'){AT.trapFocus(panel,e);}});}
function open(fromEl){build();opener=fromEl||doc.activeElement;overlay.hidden=false;doc.body.classList.add('search-open');input.value='';renderResults('',[]);ensureData(function(){renderResults(input.value,search(input.value));});input.focus();}
function close(){if(!overlay||overlay.hidden)return;overlay.hidden=true;doc.body.classList.remove('search-open');if(opener&&opener.focus)opener.focus();}
AT.search.open=open;})();
