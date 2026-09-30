(function(){'use strict';var AT=window.AT=window.AT||{};var doc=document;function init(){var select=doc.getElementById('family-select');var panels=doc.querySelectorAll('.family-panel');if(!select||!panels.length)return;function show(id){for(var i=0;i<panels.length;i++){var on=panels[i].id==='panel-'+id;if(on)panels[i].removeAttribute('hidden');else panels[i].setAttribute('hidden','');}
var opt=select.options[select.selectedIndex];if(AT.announce&&opt)AT.announce('Showing '+opt.textContent);}
select.addEventListener('change',function(){show(select.value);});}
if(doc.readyState==='loading')doc.addEventListener('DOMContentLoaded',init);else init();})();
