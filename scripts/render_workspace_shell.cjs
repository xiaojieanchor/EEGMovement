'use strict';
// Materialize the shared navigation before first paint; no product API is called.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const {parseHTML}=require('linkedom');
for(const [page,route] of [['index','/'],['lab','/lab'],['training','/training'],['downloads','/downloads']]){
 const path='hub/web/'+page+'.html',html=fs.readFileSync(path,'utf8'),{window,document}=parseHTML(html);
 const ids=[...document.querySelectorAll('[id]')].map(el=>el.id);
 const context=vm.createContext({window,document,location:new URL(route,'http://127.0.0.1:8765'),URLSearchParams,MutationObserver:class{observe(){}},ResizeObserver:class{observe(){}},matchMedia:query=>({matches:query.includes('prefers-reduced-motion')}),requestAnimationFrame(){},setTimeout(){},setInterval(){},Date,Math,Number,JSON});
 vm.runInContext(fs.readFileSync('hub/web/ui-state.js','utf8'),context);
 window.EEGStudio.mount();
 for(const id of ids)assert.ok(document.getElementById(id),'preserve #'+id);
 assert.equal(document.querySelectorAll('.studio-rail').length,1);
 assert.equal(document.querySelectorAll('.hero-art').length,1);
 fs.writeFileSync(path,document.toString());console.log('Rendered stable shell:',route);
}
