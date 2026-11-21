import * as THREE from 'three';
import { EffectComposer } from 'three/addons/postprocessing/EffectComposer.js';
import { RenderPass } from 'three/addons/postprocessing/RenderPass.js';
import { BokehPass } from 'three/addons/postprocessing/BokehPass.js';
import { UnrealBloomPass } from 'three/addons/postprocessing/UnrealBloomPass.js';
import { OutputPass } from 'three/addons/postprocessing/OutputPass.js';
import { ShaderPass } from 'three/addons/postprocessing/ShaderPass.js';
import { FXAAShader } from 'three/addons/shaders/FXAAShader.js';
import { FullScreenQuad } from 'three/addons/postprocessing/Pass.js';
import { createMeadow } from './meadow.js';

const capture=new URLSearchParams(location.search).get('capture')==='1';
if(capture)document.body.classList.add('capture');
const W=1920,H=1080,DURATION=91;
const renderer=new THREE.WebGLRenderer({antialias:false,preserveDrawingBuffer:true,powerPreference:'high-performance'});
renderer.setPixelRatio(1);renderer.setSize(W,H,false);renderer.toneMapping=THREE.ACESFilmicToneMapping;renderer.toneMappingExposure=1;
document.body.prepend(renderer.domElement);
const meadow=createMeadow(), scene=meadow.scene;
const camera=new THREE.PerspectiveCamera(42,W/H,.025,150);
const cubeTarget=new THREE.WebGLCubeRenderTarget(128,{type:THREE.HalfFloatType});
const cubeCamera=new THREE.CubeCamera(.1,20,cubeTarget);cubeCamera.update(renderer,meadow.envScene);meadow.waterMat.envMap=cubeTarget.texture;
const composer=new EffectComposer(renderer);composer.renderToScreen=false;
composer.addPass(new RenderPass(scene,camera));
const bokeh=new BokehPass(scene,camera,{focus:4,aperture:.00022,maxblur:.015});composer.addPass(bokeh);
meadow.configureDepth(bokeh.materialDepth);
// Dense disk integration avoids the visible rings of the stock 41-tap kernel.
bokeh.materialBokeh.fragmentShader=bokeh.materialBokeh.fragmentShader.split('void main()')[0]+`
void main(){
  float delta=focus+getViewZ(getDepth(vUv));
  float radius=clamp(delta*aperture,-maxblur,maxblur);
  vec4 sum=vec4(0.);
  for(int i=0;i<128;i++){
    float f=float(i)+.5;float angle=f*2.39996323;
    vec2 disk=vec2(cos(angle),sin(angle))*sqrt(f/128.)*.45;
    sum+=texture2D(tColor,vUv+disk*vec2(1.,aspect)*radius);
  }
  gl_FragColor=vec4((sum/128.).rgb,1.);
}`;
const bloom=new UnrealBloomPass(new THREE.Vector2(W,H),.18,.5,1.05);composer.addPass(bloom);
composer.addPass(new OutputPass());
const fxaa=new ShaderPass(FXAAShader);fxaa.material.uniforms.resolution.value.set(1/W,1/H);composer.addPass(fxaa);
const targetA=new THREE.WebGLRenderTarget(W,H),targetB=new THREE.WebGLRenderTarget(W,H);
const copy=new FullScreenQuad(new THREE.ShaderMaterial({uniforms:{tex:{value:null}},vertexShader:'varying vec2 vUv;void main(){vUv=uv;gl_Position=vec4(position.xy,0.,1.);}',fragmentShader:'varying vec2 vUv;uniform sampler2D tex;void main(){gl_FragColor=texture2D(tex,vUv);}'}));
const mix=new FullScreenQuad(new THREE.ShaderMaterial({uniforms:{a:{value:targetA.texture},b:{value:targetB.texture},blend:{value:0},fade:{value:1}},vertexShader:'varying vec2 vUv;void main(){vUv=uv;gl_Position=vec4(position.xy,0.,1.);}',fragmentShader:`varying vec2 vUv;uniform sampler2D a,b;uniform float blend,fade;void main(){vec3 c=mix(texture2D(a,vUv).rgb,texture2D(b,vUv).rgb,blend);float vignette=1.-.20*pow(length((vUv-.5)*1.25),1.8);gl_FragColor=vec4(c*vignette*fade,1.);}`}));

// Nine 11-second shots overlap for one second: total running time 91s.
export const SHOTS=[
  {name:'Before the light',pos:[7,4.2,-14],look:[0,.7,5],fov:43,fog:'#a3afa0',density:.055,top:'#859fa8',bottom:'#c0c5b0',sun:'#ffd4a0',power:2.1,ambient:1.2,exposure:.85,wind:.65,focus:18,aperture:.00008,golden:0},
  {name:'The wind arrives',pos:[3,2.2,-7],look:[0,1,3],fov:39,fog:'#c0c8ae',density:.025,top:'#8eafc1',bottom:'#e5dfb7',sun:'#ffe8b1',power:3.4,ambient:1.3,exposure:.95,wind:1.3,focus:10,aperture:.00016,golden:0},
  {name:'A sky in a drop',pos:[.55,1.17,-1.08],look:[.22,1.04,0],fov:25,fog:'#c6d1a9',density:.045,top:'#9bb6c6',bottom:'#e0e7c5',sun:'#fff0c4',power:3.1,ambient:1.6,exposure:1.0,wind:.35,focus:1.14,aperture:.0011,golden:0},
  {name:'The seed heads',pos:[1.45,1.88,-3.3],look:[.1,1.65,.2],fov:32,fog:'#b9c5a4',density:.04,top:'#91abba',bottom:'#e0daba',sun:'#ffdfa1',power:4.2,ambient:1.1,exposure:.95,wind:.8,focus:3.7,aperture:.00065,golden:.12},
  {name:'Wave follows wave',pos:[-4,1.35,-7],look:[0,.85,3],fov:44,fog:'#afc3b0',density:.025,top:'#8aafc6',bottom:'#d9e3cd',sun:'#fff0cd',power:3.1,ambient:1.4,exposure:1,wind:1.8,focus:8,aperture:.00018,golden:0},
  {name:'A passing shower',pos:[.1,1.3,-3.5],look:[0,1,1],fov:36,fog:'#829e9a',density:.065,top:'#657d89',bottom:'#b3c7c4',sun:'#b9d8e1',power:.9,ambient:1.8,exposure:.8,wind:1.1,focus:4.6,aperture:.00045,golden:0},
  {name:'Each stem is gold',pos:[1.05,1.94,-3.6],look:[.1,1.65,.2],fov:32,fog:'#c39b63',density:.035,top:'#a6a999',bottom:'#f6cb84',sun:'#ffb24c',power:4.2,ambient:1.0,exposure:.95,wind:.65,focus:3.9,aperture:.00065,golden:.85},
  {name:'The day lets go',pos:[7,3.1,-14],look:[0,.8,6],fov:43,fog:'#a47955',density:.038,top:'#727c8d',bottom:'#d49859',sun:'#ff9b3b',power:2.1,ambient:.7,exposure:.72,wind:.55,focus:19,aperture:.00008,golden:1},
  {name:'The growing does not stop',pos:[3,1.7,-7],look:[0,1.2,3],fov:42,fog:'#142733',density:.06,top:'#07131f',bottom:'#203744',sun:'#879ab9',power:.25,ambient:.5,exposure:.72,wind:.4,focus:9,aperture:.00030,golden:.6},
];

function renderShot(index,t,target){
  const s=SHOTS[index],local=Math.max(0,Math.min(11,t-index*10)),drift=(local/11-.5)*.24;
  camera.position.fromArray(s.pos).add(new THREE.Vector3(drift,drift*.08,drift*.15));
  camera.lookAt(...s.look);camera.fov=s.fov;camera.updateProjectionMatrix();
  meadow.uniforms.wind.value=s.wind;meadow.uniforms.golden.value=s.golden;
  scene.fog.color.set(s.fog);scene.fog.density=s.density;
  meadow.skyMat.uniforms.top.value.set(s.top);meadow.skyMat.uniforms.bottom.value.set(s.bottom);meadow.skyMat.uniforms.sunColor.value.set(s.sun);meadow.skyMat.uniforms.sunPower.value=index===8?0:.65;
  meadow.sun.color.set(s.sun);meadow.sun.intensity=s.power;meadow.hemi.intensity=s.ambient;meadow.fill.intensity=index===8?.06:1.8;
  meadow.seedMat.color.set(index>=6?0xb49a57:0xa99d67);meadow.stemMat.color.set(index>=6?0x8e813b:0x63772c);
  renderer.toneMappingExposure=s.exposure;
  bokeh.enabled=index!==8;
  bokeh.uniforms.focus.value=s.focus;bokeh.uniforms.aperture.value=index===2?.025:s.aperture*4;bokeh.uniforms.maxblur.value=index===2?.08:.035;
  bloom.strength=index===8?.65:.16;bloom.threshold=index===8?.5:1.05;
  meadow.update(t,index);composer.render(0);
  renderer.setRenderTarget(target);copy.material.uniforms.tex.value=composer.readBuffer.texture;copy.render(renderer);
}

export function renderAt(t){
  if(!Number.isFinite(t))throw new TypeError('Film time must be finite');
  t=Math.min(DURATION,Math.max(0,t));
  const incoming=Math.min(8,Math.floor(t/10));
  const transitioning=incoming>0&&t<incoming*10+1;
  if(transitioning){renderShot(incoming-1,t,targetA);renderShot(incoming,t,targetB);}else renderShot(incoming,t,targetA);
  mix.material.uniforms.blend.value=transitioning?THREE.MathUtils.smoothstep(t-incoming*10,0,1):0;
  mix.material.uniforms.fade.value=Math.min(THREE.MathUtils.smoothstep(t,0,1.5),1-THREE.MathUtils.smoothstep(t,88.5,91));
  renderer.setRenderTarget(null);mix.render(renderer);
}

const audio=document.querySelector('#audio'),play=document.querySelector('#play'),pause=document.querySelector('#pause'),seek=document.querySelector('#seek');
let showCaptions=false,lines=[];
async function init(){
  const response=await fetch('./narration.json');if(!response.ok)throw new Error('Missing narration.json');lines=await response.json();
  window.film={duration:DURATION,fps:24,shots:SHOTS.map(s=>s.name),renderAt,renderFrame(n){if(!Number.isInteger(n)||n<0||n>=2184)throw new RangeError('Frame must be an integer in 0..2183');renderAt(n/24);},renderer};
  renderAt(capture?0:5);window.film.ready=true;play.disabled=false;play.textContent='Play film ↗';
  if(!capture)requestAnimationFrame(tick);
}
function tick(){
  if(!audio.paused){renderAt(audio.currentTime);updateControls();}
  requestAnimationFrame(tick);
}
function updateControls(){
  const t=audio.currentTime;seek.value=t;document.querySelector('#clock').textContent=`${Math.floor(t/60)}:${String(Math.floor(t%60)).padStart(2,'0')} / 1:31`;
  const line=lines.findLast(([start])=>t>=start);document.querySelector('#caption').textContent=showCaptions&&line&&t<line[0]+4.5?line[1]:'';
}
play.addEventListener('click',async()=>{try{if(audio.ended)audio.currentTime=0;await audio.play();document.querySelector('#intro').hidden=true;document.querySelector('#intro').style.display='none';pause.textContent='Pause';}catch(e){document.querySelector('#error').textContent=`Audio unavailable. Run npm run prepare-film. ${e.message}`;}});
pause.addEventListener('click',async()=>{if(audio.paused){await audio.play();pause.textContent='Pause';}else{audio.pause();pause.textContent='Resume';}});
seek.addEventListener('input',()=>{audio.currentTime=Number(seek.value);renderAt(audio.currentTime);updateControls();});
document.querySelector('#cc').addEventListener('click',e=>{showCaptions=!showCaptions;e.target.setAttribute('aria-pressed',String(showCaptions));updateControls();});
audio.addEventListener('ended',()=>{renderAt(91);play.textContent='Watch again ↗';document.querySelector('#intro').style.display='grid';});
audio.addEventListener('error',()=>{document.querySelector('#error').textContent='Soundtrack missing. Run npm run prepare-film before playing.';});
init().catch(e=>{window.filmError=e.stack;document.querySelector('#error').textContent=e.message;console.error(e);});
