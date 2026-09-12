const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
function harness({locked=false,empty=false}={}) {
  class Node {
    constructor(){this.events={};this.nodes={};this.style={};this.isConnected=true;this.open=false;this.textContent='';}
    addEventListener(k,f){this.events[k]=f;}
    setAttribute(){}
    querySelector(k){
      if(k.startsWith('[data-demo=') && !this.innerHTML?.includes(k.slice(1,-1))) return null;
      return this.nodes[k] ||= new Node();
    }
    querySelectorAll(k){return [this.querySelector(k)];}
    showModal(){this.open=true;}
    close(){this.open=false;this.events.close?.();}
    click(){if(!this.disabled)this.events.click?.();}
    focus(){this.focused=true;}
    scrollIntoView(){this.scrolled=true;}
    getClientRects(){return [{}];}
    getBoundingClientRect(){return {left:120,top:150,right:500,bottom:250,width:380,height:100};}
  }
  const help=new Node(),opener=new Node(),tour=new Node(),targets={},listeners={};
  let observer,navClicks=0;
  const env={state:{projectUnlocked:!locked,userId:'test',projectId:'main',cityId:'shanghai'},
    window:{innerWidth:1000,innerHeight:700,addEventListener:(k,f)=>listeners[k]=f,removeEventListener:k=>delete listeners[k]},
    document:{getElementById:()=>help,createElement:()=>tour,body:{append(){}},
      querySelectorAll:()=>[opener],querySelector:k=>{
        if(k.startsWith('#project-dialog')) return locked?new Node():null;
        if(empty && ['.daily-day-picker','.daily-slot-links','[data-tour-target="daily-confirm"]','[data-tour-target="daily-route"]','#export-open'].includes(k))return null;
        const n=targets[k] ||= new Node();
        if(k==='[data-tab="itinerary"]')n.events.click=()=>navClicks++;
        return n;
      }},getComputedStyle:()=>({visibility:'visible'}),requestAnimationFrame:f=>{env.pending=f;return 1;},cancelAnimationFrame:()=>{},
    MutationObserver:class {constructor(fn){observer=this;this.fn=fn;}observe(){}disconnect(){this.disconnected=true;}}};
  vm.runInNewContext(fs.readFileSync('public/usage-help.js','utf8'),env);
  const start=()=>{opener.click();help.querySelector('[data-help-tour]').click();};
  return {env,help,opener,tour,start,observer:()=>observer,navClicks:()=>navClicks,next:()=>tour.querySelector('[data-tour-next]').click()};
}
test('guide requires project identity and explains how to unlock it',()=>{
  const h=harness({locked:true});h.start();assert.equal(h.tour.open,false);
  assert.match(h.help.querySelector('[data-help-tour-hint]').textContent,/先进入项目/);
});
test('guide traverses eight steps, supports back and completion, and cleans up',()=>{
  const h=harness();h.start();assert.equal(h.tour.open,true);assert.equal(h.navClicks(),0);
  assert.equal(h.tour.querySelector('[data-tour-back]').disabled,true);
  assert.equal(h.tour.querySelector('#usage-tour-title').textContent,'选择旅行城市');
  h.next();h.tour.querySelector('[data-tour-back]').click();
  assert.match(h.tour.querySelector('.usage-tour-progress').textContent,/1 \/ 8/);
  for(let i=0;i<7;i++)h.next();
  assert.equal(h.tour.querySelector('[data-tour-next]').textContent,'完成引导');h.next();
  assert.equal(h.tour.open,false);assert.equal(h.observer().disconnected,true);assert.equal(h.opener.focused,true);
});
test('empty plans use template targets and scope changes end the guide',()=>{
  const h=harness({empty:true});h.start();for(let i=0;i<3;i++)h.next();
  assert.match(h.tour.querySelector('#usage-tour-description').textContent,/每次显示一天/);
  assert.match(h.tour.querySelector('.usage-tour-preview').innerHTML,/已导入 AI 草稿/);
  h.env.state.projectId='different';h.observer().fn();h.env.pending();
  assert.equal(h.tour.open,false);
});
test('exit closes the guide without creating or modifying itinerary data',()=>{
  const h=harness();h.start();h.tour.querySelector('[data-tour-exit]').click();assert.equal(h.tour.open,false);
  assert.equal(h.env.state.projectId,'main');
});

test('template advances through draft, import, confirmation and saved route, and restores on back',()=>{
  const h=harness({empty:true});const before=JSON.stringify(h.env.state);h.start();
  const preview=h.tour.querySelector('.usage-tour-preview');
  const steps=['city','ai','add','day','confirm','slots','route','export'];
  for(let i=0;i<8;i++) {
    assert.ok(preview.querySelector('[data-demo="'+steps[i]+'"]'), 'target must exist at step '+i);
    assert.equal(h.tour.open,true);
    if(i===0)assert.match(preview.innerHTML,/选择城市/);
    if(i===2)assert.match(preview.innerHTML,/待确认导入/);
    if(i===3)assert.match(preview.innerHTML,/已导入 AI 草稿/);
    if(i===5)assert.match(preview.innerHTML,/已确认当天行程/);
    if(i===7)assert.match(preview.innerHTML,/示例路线已保存/);
    if(i<7)h.next();
  }
  h.tour.querySelector('[data-tour-back]').click();
  assert.doesNotMatch(preview.innerHTML,/示例路线已保存/);
  h.tour.querySelector('[data-tour-exit]').click();
  assert.equal(preview.innerHTML,'');assert.equal(JSON.stringify(h.env.state),before);
  h.start();assert.match(preview.innerHTML,/选择城市/);assert.doesNotMatch(preview.innerHTML,/已导入 AI 草稿/);
});
