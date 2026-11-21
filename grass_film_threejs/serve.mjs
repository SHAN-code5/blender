import { createServer } from 'node:http';
import { readFile } from 'node:fs/promises';
import { fileURLToPath, pathToFileURL } from 'node:url';
import path from 'node:path';

export const ROOT=fileURLToPath(new URL('.',import.meta.url));
export function serve(port=8030){
  const types={'.html':'text/html','.js':'text/javascript','.mjs':'text/javascript','.json':'application/json','.mp3':'audio/mpeg'};
  const server=createServer(async(req,res)=>{
    try{
      const name=decodeURIComponent(new URL(req.url,'http://localhost').pathname);
      const filename=path.resolve(ROOT,`.${name==='/'?'/index.html':name}`);
      if(!filename.startsWith(ROOT)){res.writeHead(403).end();return;}
      const data=await readFile(filename);
      const headers={'Content-Type':types[path.extname(filename)]||'application/octet-stream','Cache-Control':'no-cache','Accept-Ranges':'bytes'};
      let start=0,end=data.length-1,status=200;
      if(req.headers.range){
        const range=/^bytes=(\d*)-(\d*)$/.exec(req.headers.range);
        if(range){
          start=range[1]?Number(range[1]):Math.max(0,data.length-Number(range[2]));
          end=range[1]&&range[2]?Math.min(Number(range[2]),data.length-1):data.length-1;
        }
        if(!range||(!range[1]&&!range[2])||start>end||start>=data.length){res.writeHead(416,{'Content-Range':`bytes */${data.length}`}).end();return;}
        status=206;headers['Content-Range']=`bytes ${start}-${end}/${data.length}`;
      }
      headers['Content-Length']=Math.max(0,end-start+1);
      res.writeHead(status,headers);res.end(req.method==='HEAD'?undefined:data.subarray(start,end+1));
    }catch{res.writeHead(404).end('Not found. Run npm ci && npm run prepare-film.');}
  });
  return new Promise((resolve,reject)=>{server.once('error',reject);server.listen(port,'127.0.0.1',()=>resolve(server));});
}
if(process.argv[1]&&pathToFileURL(path.resolve(process.argv[1])).href===import.meta.url){
  const port=Number(process.env.PORT||8030);await serve(port);console.log(`Grass film: http://127.0.0.1:${port}`);
}
