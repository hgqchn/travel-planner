const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const stepEnv={window:{},document:{documentElement:{dataset:{}}}};
vm.runInNewContext(fs.readFileSync('public/usage-tour-demo.js','utf8'),stepEnv);
const steps=stepEnv.window.TripUsageTour.steps;
function harness({locked=false}={}) {
  class Node {
    constructor(){this.events={};this.nodes={};this.style={};this.isConnected=true;this.open=false;this.textContent='';this.clientWidth=1000;this.clientHeight=600;}
    addEventListener(k,f){this.events[k]=f;}
    setAttribute(){} removeAttribute(){} remove(){}
    querySelector(k){return this.nodes[k] ||= new Node();}
    querySelectorAll(k){return [this.querySelector(k)];}
    showModal(){this.open=true;}
    close(){this.open=false;this.events.close?.();}
    click(){if(!this.disabled)this.events.click?.();}
    focus(){this.focused=true;}
    scrollIntoView(){this.scrolled=true;}
    getClientRects(){return [{}];}
    getBoundingClientRect(){return {left:120,top:150,right:500,bottom:250,width:380,height:100};}
  }
  const help=new Node(),opener=new Node(),tour=new Node(),listeners={};
  let observer,loads=0, fail=false;
  const preview=tour.querySelector('iframe');
  Object.defineProperty(preview,'srcdoc',{set(value){this.template=value;loads++;this.contentDocument=new Node();this.contentWindow={
    addEventListener(){},TripUsageTour:{ready:true,actions:[],advance:async i=>{if(fail)throw Error('preset failed');this.contentWindow.TripUsageTour.actions.push(i);}}
  };}});
  const doc={documentElement:{dataset:{},outerHTML:'<html>template</html>'},head:{prepend(){}},createElement:()=>({}),querySelector:()=>new Node()};
  const env={state:{projectUnlocked:!locked,userId:'test',projectId:'main',cityId:'shanghai'},location:{origin:'http://test'},Date,setTimeout,
    fetch:async()=>({ok:true,text:async()=>'<html></html>'}),DOMParser:class {parseFromString(){return doc;}},
    window:{TripUsageTour:{steps},addEventListener:(k,f)=>listeners[k]=f,removeEventListener:k=>delete listeners[k]},
    document:{documentElement:{dataset:{}},getElementById:()=>help,createElement:()=>tour,body:{append(){}},querySelectorAll:()=>[opener],
      querySelector:k=>k.startsWith('#project-dialog')?(locked?new Node():null):new Node()},
    getComputedStyle:()=>({visibility:'visible'}),requestAnimationFrame:f=>{env.pending=f;return 1;},cancelAnimationFrame:()=>{},
    MutationObserver:class {constructor(fn){observer=this;this.fn=fn;}observe(){}disconnect(){this.disconnected=true;}}};
  vm.runInNewContext(fs.readFileSync('public/usage-help.js','utf8'),env);
  const settle=()=>new Promise(setImmediate);
  return {env,help,opener,tour,preview,loads:()=>loads,fail:()=>fail=true,settle,observer:()=>observer,
    start:async()=>{opener.click();help.querySelector('[data-help-tour]').click();await settle();},
    next:async()=>{tour.querySelector('[data-tour-next]').click();await settle();},
    back:async()=>{tour.querySelector('[data-tour-back]').click();await settle();}};
}
test('guide requires project identity',async()=>{
  const h=harness({locked:true});await h.start();assert.equal(h.tour.open,false);assert.equal(h.loads(),0);
});
test('guide follows 21 actual operations and cleans up after completion',async()=>{
  const h=harness();await h.start();assert.equal(h.tour.open,true);assert.equal(h.tour.querySelector('[data-tour-back]').disabled,true);
  for(let i=0;i<20;i++)await h.next();
  assert.equal(h.tour.querySelector('[data-tour-next]').textContent,'完成引导');
  assert.deepEqual(h.preview.contentWindow.TripUsageTour.actions,Array.from({length:20},(_,i)=>i));
  await h.next();assert.equal(h.tour.open,false);assert.equal(h.observer().disconnected,true);assert.equal(h.opener.focused,true);
});
test('back creates a fresh document and replays only earlier actions',async()=>{
  const h=harness();await h.start();for(let i=0;i<8;i++)await h.next();
  const old=h.preview.contentDocument;await h.back();assert.notEqual(h.preview.contentDocument,old);assert.equal(h.loads(),2);
  assert.deepEqual(h.preview.contentWindow.TripUsageTour.actions,[0,1,2,3,4,5,6]);
  assert.match(h.tour.querySelector('.usage-tour-progress').textContent,/8 \/ 21/);
});
test('scope changes close guide and leave the actual project intact',async()=>{
  const h=harness();await h.start();h.env.state.projectId='different';h.observer().fn();h.env.pending();assert.equal(h.tour.open,false);
  assert.equal(h.env.state.cityId,'shanghai');
});
test('failed step does not silently advance',async()=>{
  const h=harness();await h.start();h.fail();await h.next();
  assert.match(h.tour.querySelector('#usage-tour-description').textContent,/preset failed/);
  assert.equal(h.tour.querySelector('[data-tour-next]').disabled,true);
  h.tour.querySelector('[data-tour-exit]').click();assert.equal(h.tour.open,false);
});
test('exit during loading cancels late updates',async()=>{
  const h=harness();h.env.fetch=()=>new Promise(()=>{});
  await h.start();h.tour.querySelector('[data-tour-exit]').click();assert.equal(h.tour.open,false);assert.equal(h.loads(),0);
});
