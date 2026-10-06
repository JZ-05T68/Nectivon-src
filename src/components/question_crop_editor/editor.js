(() => {
  const el=id=>document.getElementById(id),canvas=el('canvas'),ctx=canvas.getContext('2d'),image=new Image(),G=window.CropGeometry;
  let args={},signature='',regions=[],initial=[],selected=null,scale=1,mode='adjust',drag=null,ready=false,dirty=false,autoFit=true;
  const post=value=>window.parent.postMessage({isStreamlitMessage:true,...value},'*');
  const color=index=>args.colors?.[index]||`hsl(${Math.round(index*137.508)%360} 80% 35%)`;
  const current=()=>regions.find(r=>r.id===selected),size=()=>[image.naturalWidth,image.naturalHeight];
  const status=text=>{el('status').textContent=text;};
  const changed=()=>{dirty=true;el('save').disabled=false;status('调整尚未保存。点击“保存截图方框”后同步。');};
  function controls() {
    el('figure').replaceChildren(...regions.map((r,i)=>{const o=document.createElement('option');o.value=r.id;o.textContent=`图 ${i+1}${r.role==='option'?' · 选项 '+r.option_label:''}`;o.style.color=color(i);return o;}));
    el('figure').value=selected||'';const r=current();el('remove').disabled=!r;
    el('role').disabled=el('description').disabled=el('option').disabled=!r;
    el('role').value=r?.role||'stem';el('option').value=r?.option_label||'A';el('description').value=r?.description||'';
    el('optionLabel').hidden=r?.role!=='option';
  }
  function redraw() {
    if(!ready)return;ctx.clearRect(0,0,canvas.width,canvas.height);ctx.drawImage(image,0,0);
    for(const [i,r] of regions.entries()) {
      const [x,y,x2,y2]=r.bounds;ctx.strokeStyle=color(i);ctx.fillStyle=color(i);ctx.lineWidth=(r.id===selected?3:2)/scale;
      ctx.strokeRect(x,y,x2-x,y2-y);ctx.font=`bold ${16/scale}px sans-serif`;
      ctx.fillText(`图 ${i+1}${r.role==='option'?' · '+r.option_label:''}`,x+5/scale,Math.max(19/scale,y-5/scale));
      if(r.id===selected)for(const [hx,hy] of Object.values(G.handles(r.bounds)))ctx.fillRect(hx-4/scale,hy-4/scale,8/scale,8/scale);
    }
    if(drag?.kind==='add') {const b=G.bounds(drag.start,drag.point,size());ctx.strokeStyle=color(regions.length);ctx.lineWidth=2/scale;ctx.strokeRect(b[0],b[1],b[2]-b[0],b[3]-b[1]);}
  }
  function zoom() {canvas.style.width=canvas.width*scale+'px';canvas.style.height=canvas.height*scale+'px';el('zoom').textContent=Math.round(scale*100)+'%';redraw();post({type:'streamlit:setFrameHeight',height:document.body.scrollHeight+5});}
  const point=e=>{const b=canvas.getBoundingClientRect();return {x:(e.clientX-b.left)*canvas.width/b.width,y:(e.clientY-b.top)*canvas.height/b.height};};
  function setMode(value){mode=value;el('adjust').classList.toggle('active',mode==='adjust');el('add').classList.toggle('active',mode==='add');}
  el('adjust').onclick=()=>setMode('adjust');el('add').onclick=()=>setMode('add');
  el('figure').onchange=()=>{selected=el('figure').value;controls();redraw();};
  el('remove').onclick=()=>{regions=regions.filter(r=>r.id!==selected);selected=regions[0]?.id;controls();changed();redraw();};
  el('role').onchange=()=>{const r=current();if(!r)return;r.role=el('role').value;r.option_label=r.role==='option'?el('option').value.toUpperCase()||'A':'';controls();changed();redraw();};
  el('option').oninput=()=>{const r=current();if(r){r.option_label=el('option').value.toUpperCase();changed();redraw();}};
  el('description').oninput=()=>{const r=current();if(r){r.description=el('description').value;changed();}};
  el('plus').onclick=()=>{autoFit=false;scale=Math.min(4,scale*1.25);zoom();};el('minus').onclick=()=>{autoFit=false;scale=Math.max(.1,scale/1.25);zoom();};
  const fit=()=>{scale=Math.max(.1,Math.min(2,(el('view').clientWidth-18)/canvas.width));};
  el('fit').onclick=()=>{autoFit=true;fit();zoom();};
  el('reset').onclick=()=>{regions=structuredClone(initial);selected=regions[0]?.id;dirty=false;el('save').disabled=true;controls();redraw();status('已撤销未保存调整。');};
  canvas.onpointerdown=e=>{
    if(!ready||e.button!==0)return;const p=point(e);canvas.setPointerCapture(e.pointerId);
    if(mode==='add'){if(regions.length>=40){status('一题最多支持 40 个截图方框。');return;}drag={kind:'add',start:p,point:p};}
    else {
      let r=current(),hit=r&&G.hit(r.bounds,p,8/scale);
      if(!hit){r=[...regions].reverse().find(r=>G.hit(r.bounds,p,8/scale));hit=r&&G.hit(r.bounds,p,8/scale);}
      if(r&&hit){selected=r.id;drag={kind:hit,start:p,bounds:r.bounds.slice(),id:r.id};controls();redraw();}
    }
  };
  canvas.onpointermove=e=>{if(!drag)return;const p=point(e);if(drag.kind==='add')drag.point=p;else {const r=regions.find(r=>r.id===drag.id);r.bounds=drag.kind==='move'?G.move(drag.bounds,p.x-drag.start.x,p.y-drag.start.y,size()):G.resize(drag.bounds,drag.kind,p,size());}redraw();};
  canvas.onpointerup=e=>{
    if(!drag)return;canvas.onpointermove(e);
    if(drag.kind==='add'){const b=G.bounds(drag.start,drag.point,size());if(b[2]-b[0]>=4&&b[3]-b[1]>=4){const id=crypto.randomUUID().replaceAll('-','');regions.push({id,bounds:b,role:'stem',option_label:'',description:''});selected=id;changed();setMode('adjust');}}
    else if(JSON.stringify(current().bounds)!==JSON.stringify(drag.bounds))changed();
    drag=null;controls();redraw();
  };
  canvas.onpointercancel=()=>{if(drag?.id){regions.find(r=>r.id===drag.id).bounds=drag.bounds;}drag=null;redraw();};
  el('save').onclick=()=>{if(!ready||!dirty)return;post({type:'streamlit:setComponentValue',dataType:'json',value:{request_id:Date.now()+'-'+Math.random().toString(36).slice(2),source_hash:args.source_hash,scope:args.scope,revision:args.revision,regions}});status('正在保存…');};
  window.addEventListener('message',e=>{
    if(e.data?.type!=='streamlit:render')return;args=e.data.args||{};const next=[args.source_hash,args.scope,args.revision].join(':');if(signature===next){zoom();return;}signature=next;ready=false;dirty=false;el('save').disabled=true;
    image.onload=()=>{regions=structuredClone(args.regions||[]);initial=structuredClone(regions);selected=regions[0]?.id;canvas.width=image.naturalWidth;canvas.height=image.naturalHeight;autoFit=true;fit();ready=true;controls();setMode(regions.length?'adjust':'add');zoom();status('当前只标示本题的截图范围。原始扫描图保持原样。');};
    image.onerror=()=>{signature='';status('原图加载失败，请重新打开工具。');};image.src=args.original;
  });
  window.addEventListener('resize',()=>{if(ready&&autoFit)fit();zoom();});post({type:'streamlit:componentReady',apiVersion:1});
})();
