/* Pixel geometry shared by the native pointer editor and its interaction tests. */
(function(root) {
  const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
  function bounds(a, b, size) {
    return [Math.round(clamp(Math.min(a.x,b.x),0,size[0])),Math.round(clamp(Math.min(a.y,b.y),0,size[1])),
      Math.round(clamp(Math.max(a.x,b.x),0,size[0])),Math.round(clamp(Math.max(a.y,b.y),0,size[1]))];
  }
  function move(box, dx, dy, size) {
    dx=clamp(dx,-box[0],size[0]-box[2]);dy=clamp(dy,-box[1],size[1]-box[3]);
    return box.map((v,i)=>Math.round(v+(i%2?dy:dx)));
  }
  function resize(box, handle, p, size) {
    const b=box.slice();
    if(handle.includes('w'))b[0]=clamp(p.x,0,b[2]-4);
    if(handle.includes('e'))b[2]=clamp(p.x,b[0]+4,size[0]);
    if(handle.includes('n'))b[1]=clamp(p.y,0,b[3]-4);
    if(handle.includes('s'))b[3]=clamp(p.y,b[1]+4,size[1]);
    return b.map(Math.round);
  }
  function handles(b) {const [x,y,r,d]=b,cx=(x+r)/2,cy=(y+d)/2;return {nw:[x,y],n:[cx,y],ne:[r,y],e:[r,cy],se:[r,d],s:[cx,d],sw:[x,d],w:[x,cy]};}
  function hit(box,p,tolerance) {
    for(const [handle,[x,y]] of Object.entries(handles(box)))if(Math.abs(p.x-x)<=tolerance&&Math.abs(p.y-y)<=tolerance)return handle;
    return p.x>=box[0]&&p.x<=box[2]&&p.y>=box[1]&&p.y<=box[3]?'move':null;
  }
  const api={bounds,move,resize,handles,hit};root.CropGeometry=api;
  if(typeof module!=='undefined')module.exports=api;
})(typeof window==='undefined'?globalThis:window);
