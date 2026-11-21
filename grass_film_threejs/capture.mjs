import puppeteer from 'puppeteer-core';
import { spawn } from 'node:child_process';
import { once } from 'node:events';
import { mkdir, access } from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import { serve, ROOT } from './serve.mjs';

const stills=process.argv.includes('--stills');
const player=process.argv.includes('--check-player');
const root=process.env.FILM_ROOT||path.join(os.homedir(),'grassfilm');
const out=path.join(root,'stills_threejs');await mkdir(out,{recursive:true});
await access(path.join(ROOT,'media/soundtrack.mp3'));
const server=await serve(0),port=server.address().port;
let browser,encoder;
try{
  browser=await puppeteer.launch({executablePath:process.env.CHROME_BIN||'/usr/bin/google-chrome',headless:true,
    args:['--no-sandbox','--disable-dev-shm-usage','--use-gl=angle','--use-angle=swiftshader','--enable-unsafe-swiftshader','--hide-scrollbars','--autoplay-policy=no-user-gesture-required'],
    defaultViewport:{width:1920,height:1080,deviceScaleFactor:1},protocolTimeout:300000});
  const page=await browser.newPage();
  const errors=[];page.on('pageerror',e=>errors.push(String(e)));page.on('console',msg=>{if(msg.type()==='error')console.error(msg.text());});
  await page.goto(`http://127.0.0.1:${port}/${player?'':'?capture=1'}`,{waitUntil:'networkidle0',timeout:120000});
  await page.waitForFunction(()=>window.film?.ready||window.filmError,{timeout:180000});
  const error=await page.evaluate(()=>window.filmError);if(error)throw new Error(error);
  console.log(await page.evaluate(()=>({renderer:window.film.renderer.capabilities.isWebGL2?'WebGL2':'WebGL',duration:window.film.duration,resolution:[innerWidth,innerHeight]})));
  if(player){
    await page.click('#play');
    await page.waitForFunction(()=>document.querySelector('#audio').currentTime>.5,{timeout:30000});
    await page.click('#pause');
    if(!await page.evaluate(()=>document.querySelector('#audio').paused))throw new Error('Pause failed');
    await page.evaluate(()=>{const seek=document.querySelector('#seek');seek.value='25';seek.dispatchEvent(new Event('input'));});
    if(!await page.evaluate(()=>Math.abs(document.querySelector('#audio').currentTime-25)<.01))throw new Error('Seek failed');
    await page.click('#cc');
    if(!await page.evaluate(()=>document.querySelector('#clock').textContent==='0:25 / 1:31'&&document.querySelector('#caption').textContent==='A whole sky rests inside a drop of dew.'))throw new Error('Seek controls or caption out of sync');
    await page.screenshot({path:path.join(out,'player_macro.png')});
    await page.click('#pause');
    await page.waitForFunction(()=>document.querySelector('#audio').currentTime>25.3,{timeout:30000});
    await page.click('#pause');
    console.log('PASS: user-gesture play, audio clock advances, pause, seek to macro, synchronized clock/captions, resume');
  }else if(stills){
    for(let shot=0;shot<9;shot++){
      const frame=Math.round((shot*10+5.5)*24);
      await page.evaluate(n=>window.film.renderFrame(n),frame);
      const filename=path.join(out,`shot_${String(shot+1).padStart(2,'0')}.png`);
      await page.screenshot({path:filename});console.log(`Saved ${filename}`);
    }
    await page.evaluate(()=>{window.film.renderFrame(600);const gl=window.film.renderer.getContext();window.repeatPixels=new Uint8Array(1920*1080*4);gl.readPixels(0,0,1920,1080,gl.RGBA,gl.UNSIGNED_BYTE,window.repeatPixels);});
    await page.evaluate(()=>window.film.renderFrame(1900));
    const delta=await page.evaluate(()=>{
      window.film.renderFrame(600);const gl=window.film.renderer.getContext(),b=new Uint8Array(1920*1080*4);gl.readPixels(0,0,1920,1080,gl.RGBA,gl.UNSIGNED_BYTE,b);
      let total=0,changed=0;for(let i=0;i<b.length;i+=4){let d=0;for(let j=0;j<3;j++)d+=Math.abs(b[i+j]-window.repeatPixels[i+j]);total+=d;if(d)changed++;}
      return {meanChannelDelta:total/(1920*1080*3),changedFraction:changed/(1920*1080)};
    });
    console.log('Repeat frame:',delta);
    if(delta.meanChannelDelta>.001||delta.changedFraction>.0002)throw new Error('Repeat frame exceeds documented pixel tolerance');
    const invalid=await page.evaluate(()=>[-1,2184,undefined].every(n=>{try{window.film.renderFrame(n);return false;}catch(e){return e instanceof RangeError;}})&&[undefined,NaN,Infinity].every(t=>{try{window.film.renderAt(t);return false;}catch(e){return e instanceof TypeError;}}));
    if(!invalid)throw new Error('Invalid frame was accepted');
    console.log('PASS: nine previews; repeat frame within pixel tolerance; empty and invalid times rejected');
  }else{
    for(let i=1;i<=9;i++)await access(path.join(out,`shot_${String(i).padStart(2,'0')}.png`));
    const output=path.join(root,'grass_field_threejs.mp4');
    encoder=spawn('ffmpeg',['-v','warning','-y','-f','image2pipe','-vcodec','png','-framerate','24','-i','pipe:0','-i',path.join(ROOT,'media/soundtrack.mp3'),'-map','0:v','-map','1:a','-t','91','-c:v','libx264','-threads','8','-preset','fast','-crf','18','-pix_fmt','yuv420p','-c:a','aac','-b:a','192k','-movflags','+faststart',output],{stdio:['pipe','inherit','inherit']});
    const finished=once(encoder,'close');let pipeError;encoder.stdin.on('error',e=>{pipeError=e;});
    for(let n=0;n<2184;n++){
      if(pipeError)throw pipeError;
      await page.evaluate(frame=>window.film.renderFrame(frame),n);
      const png=await page.screenshot({type:'png'});
      if(!encoder.stdin.write(png))await once(encoder.stdin,'drain');
      if(n%120===0)console.log(`Frame ${n}/2184 · ${(n/24).toFixed(1)}s`);
    }
    encoder.stdin.end();const [code]=await finished;if(code!==0)throw new Error(`ffmpeg exited ${code}`);
    console.log(`Saved ${output}`);
  }
  if(errors.length)throw new Error(errors.join('\n'));
}finally{
  if(encoder&&encoder.exitCode===null)encoder.kill();
  await browser?.close();server.close();
}
