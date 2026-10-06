const test=require('node:test'),assert=require('node:assert/strict');
const G=require('../../src/components/question_crop_editor/crop_geometry.js');
test('creating a reverse-drag rectangle clamps to immutable source pixels',()=>{
  assert.deepEqual(G.bounds({x:100,y:200},{x:-20,y:40},[800,600]),[0,40,100,200]);
});
test('moving never distorts the rectangle or crosses page boundaries',()=>{
  assert.deepEqual(G.move([80,100,180,200],-100,500,[800,600]),[0,500,100,600]);
});
test('all eight resize handles preserve the other edges and a usable minimum',()=>{
  const box=[80,100,180,200];
  for(const handle of Object.keys(G.handles(box))){
    const result=G.resize(box,handle,{x:790,y:590},[800,600]);
    assert.ok(result[2]-result[0]>=4&&result[3]-result[1]>=4);
    if(!handle.includes('w'))assert.equal(result[0],box[0]);
    if(!handle.includes('n'))assert.equal(result[1],box[1]);
    if(!handle.includes('e'))assert.equal(result[2],box[2]);
    if(!handle.includes('s'))assert.equal(result[3],box[3]);
  }
});
test('handle hit testing respects zoom-derived tolerance and otherwise moves',()=>{
  assert.equal(G.hit([80,100,180,200],{x:176,y:196},8),'se');
  assert.equal(G.hit([80,100,180,200],{x:110,y:130},8),'move');
  assert.equal(G.hit([80,100,180,200],{x:40,y:40},8),null);
});
